"""Adw.Application subclass owning actions, accelerators and the session."""

import contextlib
import logging
import signal
import sys
from collections.abc import Callable
from pathlib import Path

import gi

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")

from gi.repository import Adw, Gio, GLib, Gtk

from . import config, lock, theme
from .storage import session
from .ui import preferences, shortcuts
from .window import NotemadWindow

logger = logging.getLogger(__name__)

APP_ID = "io.gitlab.efferone.Notemad"

# How long a copy that refused to start waits for its message to be read before
# leaving anyway. Long enough to read it, short enough not to sit there for good.
REFUSAL_TIMEOUT_S = 120


class NotemadApplication(Adw.Application):
    def __init__(self) -> None:
        # Without this the window is classed after whatever started it, which is
        # how a desktop entry loses track of its own window, and its icon along
        # with it.
        GLib.set_prgname(APP_ID)
        GLib.set_application_name("notemad")
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_OPEN)
        self.set_option_context_parameter_string("[FILE...]")
        self.set_option_context_summary(
            "Files given here open as tabs alongside the restored session."
        )
        self.window: NotemadWindow | None = None
        self.config = config.Config()
        # Held for the life of the process, so a second copy on another session
        # bus cannot write over this one's notes and settings.
        self._lock = lock.DirectoryLock()
        self._may_run = True
        # Whether the hold taken while a refusal is on screen has been given up.
        self._released = False

    def do_startup(self) -> None:
        Adw.Application.do_startup(self)
        # Before anything is read or written, since the whole point is to keep
        # two copies from touching the same files.
        self._may_run = self._lock.take()
        if not self._may_run:
            return

        self.config = config.load()
        theme.register_style_schemes()
        theme.register_icons()
        theme.apply_font(self.config.font_family, self.config.font_size)
        Gtk.Window.set_default_icon_name(APP_ID)
        self._add_action("new-tab", self._on_new_tab, ["<Control>n"])
        self._add_action("open", self._on_open, ["<Control>o"])
        # The same binding browsers use for reopening a tab you have just shut,
        # which is exactly what this does.
        self._add_action("recover", self._on_recover, ["<Control><Shift>t"])
        self._add_action("save", self._on_save, ["<Control>s"])
        self._add_action("save-as", self._on_save_as, ["<Control><Shift>s"])
        self._add_action("close-tab", self._on_close_tab, ["<Control>w"])
        self._add_action("rename", self._on_rename, ["F2"])
        # A letter rather than punctuation on purpose: on layouts where
        # backslash sits behind a third level shift, adding shift to it produces
        # something else entirely and the accelerator never matches.
        self._add_action("move-to-pane", self._on_move_to_pane, ["<Control><Shift>m"])
        self._add_action("find", self._on_find, ["<Control>f"])
        self._add_action("replace", self._on_replace, ["<Control>h"])
        self._add_action("search-notes", self._on_search_notes, ["<Control><Shift>f"])
        self._add_action("next-tab", self._on_next_tab, ["<Control>Tab"])
        self._add_action("previous-tab", self._on_previous_tab, ["<Control><Shift>Tab"])
        self._add_action("preferences", self._on_preferences, ["<Control>comma"])
        # No accelerator: Ctrl+? never reached this on the keyboards it was tried
        # on, and the menu entry is the only way in that works everywhere.
        self._add_action("shortcuts", self._on_shortcuts)
        self._add_action("quit", self._on_quit, ["<Control>q"])
        self._add_action("toggle-theme", self._on_toggle_theme, ["<Control>d"])

        # The preferences dialog names the themes and the header button flips
        # between them, and both end up changing the state of this one action.
        self.theme_action = Gio.SimpleAction.new_stateful(
            "theme",
            GLib.VariantType.new("s"),
            GLib.Variant.new_string(self.config.theme),
        )
        self.theme_action.connect("change-state", self._on_theme_state)
        self.add_action(self.theme_action)

        # The header button and the menu entry are two faces of one state.
        self.split_action = Gio.SimpleAction.new_stateful(
            "split", None, GLib.Variant.new_boolean(False)
        )
        self.split_action.connect("change-state", self._on_split_state)
        self.add_action(self.split_action)
        self.set_accels_for_action("app.split", ["<Control>backslash"])

        # One action taking a position rather than nine near-identical ones.
        self._add_action("jump-tab", self._on_jump_tab, parameter_type="i")
        for number in range(1, 10):
            self.set_accels_for_action(f"app.jump-tab({number})", [f"<Alt>{number}"])

        # Only matters while the preference is to follow the system, but the
        # desktop can change its mind at any point, including mid-session.
        Adw.StyleManager.get_default().connect("notify::dark", self._on_system_dark)

        # Being killed from a terminal or by the session manager should cost the
        # user nothing, so both signals go through the same flush and quit.
        for signum in (signal.SIGINT, signal.SIGTERM):
            GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signum, self._on_unix_signal)

    def do_activate(self) -> None:
        if not self._may_run:
            self._refuse_to_start()
            return

        window = self._ensure_window()
        window.present()
        GLib.idle_add(window.focus_active_view)

    def do_open(self, files: list[Gio.File], n_files: int, hint: str) -> None:
        """Open the paths given on the command line alongside the session."""
        if not self._may_run:
            self._refuse_to_start()
            return

        window = self._ensure_window()
        for gfile in files:
            path = gfile.get_path()
            if path is None:
                # A remote URI. Notemad only ever edits local files.
                window.toast(f"Cannot open {gfile.get_uri()}")
                continue
            window.open_path(Path(path))
        window.present()
        GLib.idle_add(window.focus_active_view)

    def _refuse_to_start(self) -> None:
        """Say why there will be no window, then stop.

        Said on screen as well as on the terminal, because the usual way to
        start Notemad twice is from a desktop menu with nowhere to print to.
        """
        holder = self._lock.holder
        named = f" as process {holder}" if holder is not None else ""
        detail = (
            f"Another copy is already running{named} and using the same notes. "
            "Two copies writing the same files can corrupt them, so this one "
            "will not start."
        )
        print(f"notemad: {detail}", file=sys.stderr)

        # Nothing is holding the application up once this returns, so it is kept
        # alive by hand until the dialog has been answered.
        self.hold()
        dialog = Gtk.AlertDialog(
            message="Notemad is already running", detail=detail, modal=True
        )
        dialog.choose(None, None, self._refusal_seen)

        # A backstop, not a feature. A dialog taken away by the window manager
        # rather than by its button never finishes, and a copy that refused to
        # start has no business outliving its own message.
        GLib.timeout_add_seconds(REFUSAL_TIMEOUT_S, self._give_up_waiting)

    def _refusal_seen(self, dialog: Gtk.AlertDialog, result, _data=None) -> None:
        with contextlib.suppress(GLib.Error):
            dialog.choose_finish(result)
        self._let_go()

    def _give_up_waiting(self) -> bool:
        self._let_go()
        return GLib.SOURCE_REMOVE

    def _let_go(self) -> None:
        """Release the hold from the refusal, once and once only."""
        if self._released:
            return
        self._released = True
        self.release()

    def _ensure_window(self) -> NotemadWindow:
        if self.window is None:
            self.window = NotemadWindow(application=self)
            # Before the tabs exist, so they are built with the right colours
            # rather than repainted a moment after the user is looking at them.
            self.window.apply_theme(
                self.config.theme, self.config.last_dark, self.config.last_light
            )
            self.window.apply_archive_warning(self.config.archive_warn_bytes)
            self.window.restore(session.load())
        return self.window

    def set_theme(self, theme_id: str) -> None:
        self.config.theme = theme_id
        self._remember_theme(theme_id)
        if self.window is not None:
            self.window.apply_theme(
                theme_id, self.config.last_dark, self.config.last_light
            )
        config.save(self.config)

    def _remember_theme(self, theme_id: str) -> None:
        """Note which dark and which light theme were last chosen by name.

        Following the system is not a choice of theme, so it does not change
        which two the header button moves between.
        """
        if theme_id == theme.FOLLOW_SYSTEM:
            return
        chosen = theme.get(theme_id)
        if chosen.dark:
            self.config.last_dark = chosen.id
        else:
            self.config.last_light = chosen.id

    def set_font(self, family: str, size: int) -> None:
        self.config.font_family = family
        self.config.font_size = config.clamp_font_size(size)
        theme.apply_font(self.config.font_family, self.config.font_size)
        config.save(self.config)

    def set_archive_warning(self, warn_at: int) -> None:
        self.config.archive_warn_bytes = warn_at
        if self.window is not None:
            self.window.apply_archive_warning(warn_at)
        config.save(self.config)

    def do_shutdown(self) -> None:
        self.flush()
        if self.window is not None:
            # Tabs are about to be torn down, and a window on its way out has no
            # business opening a fresh note to replace them.
            self.window.mark_closing()
        Adw.Application.do_shutdown(self)

    def flush(self) -> None:
        """Write out anything unsaved across the whole application."""
        if self.window is not None:
            self.window.flush_all()

    def _add_action(
        self,
        name: str,
        callback: Callable,
        accels: list[str] | None = None,
        parameter_type: str | None = None,
    ) -> None:
        variant_type = GLib.VariantType.new(parameter_type) if parameter_type else None
        action = Gio.SimpleAction.new(name, variant_type)
        action.connect("activate", callback)
        self.add_action(action)
        if accels:
            self.set_accels_for_action(f"app.{name}", accels)

    def _on_new_tab(self, action: Gio.SimpleAction, parameter) -> None:
        if self.window is not None:
            self.window.new_scratch_tab()

    def _on_open(self, action: Gio.SimpleAction, parameter) -> None:
        if self.window is not None:
            self.window.open_file_dialog()

    def _on_recover(self, action: Gio.SimpleAction, parameter) -> None:
        if self.window is not None:
            self.window.open_archive_dialog()

    def _on_save(self, action: Gio.SimpleAction, parameter) -> None:
        if self.window is not None:
            self.window.save_active_tab()

    def _on_save_as(self, action: Gio.SimpleAction, parameter) -> None:
        if self.window is not None:
            self.window.save_as_dialog()

    def _on_close_tab(self, action: Gio.SimpleAction, parameter) -> None:
        if self.window is not None:
            self.window.close_active_tab()

    def _on_next_tab(self, action: Gio.SimpleAction, parameter) -> None:
        if self.window is not None:
            self.window.select_next_tab()

    def _on_previous_tab(self, action: Gio.SimpleAction, parameter) -> None:
        if self.window is not None:
            self.window.select_previous_tab()

    def _on_jump_tab(self, action: Gio.SimpleAction, parameter: GLib.Variant) -> None:
        if self.window is not None:
            self.window.select_tab_number(parameter.get_int32())

    def _on_rename(self, action: Gio.SimpleAction, parameter) -> None:
        if self.window is not None:
            self.window.rename_active_tab()

    def _on_move_to_pane(self, action: Gio.SimpleAction, parameter) -> None:
        if self.window is not None:
            self.window.move_active_tab_to_other_pane()
            self.split_action.set_state(
                GLib.Variant.new_boolean(self.window.is_split())
            )

    def _on_split_state(self, action: Gio.SimpleAction, value: GLib.Variant) -> None:
        action.set_state(value)
        if self.window is not None:
            self.window.set_split(value.get_boolean())
            # The window refuses to leave a pane empty, so it has the last word
            # on whether the split actually happened.
            action.set_state(GLib.Variant.new_boolean(self.window.is_split()))

    def _on_find(self, action: Gio.SimpleAction, parameter) -> None:
        if self.window is not None:
            self.window.toggle_find()

    def _on_replace(self, action: Gio.SimpleAction, parameter) -> None:
        if self.window is not None:
            self.window.open_find(replacing=True)

    def _on_search_notes(self, action: Gio.SimpleAction, parameter) -> None:
        if self.window is not None:
            self.window.open_search_dialog()

    def _on_toggle_theme(self, action: Gio.SimpleAction, parameter) -> None:
        """Move between the dark theme and the light theme last chosen.

        With more than two themes the button cannot simply name its
        destination. Resolved first, so that following the system flips away
        from whichever of the two is actually on screen rather than a fixed one.
        """
        showing = theme.resolve(
            self.theme_action.get_state().get_string(),
            self.config.last_dark,
            self.config.last_light,
        )
        target = self.config.last_light if showing.dark else self.config.last_dark
        self.theme_action.change_state(GLib.Variant.new_string(target))

    def _on_system_dark(self, manager: Adw.StyleManager, param) -> None:
        if self.config.theme != theme.FOLLOW_SYSTEM or self.window is None:
            return
        self.window.apply_theme(
            theme.FOLLOW_SYSTEM, self.config.last_dark, self.config.last_light
        )

    def _on_preferences(self, action: Gio.SimpleAction, parameter) -> None:
        if self.window is not None:
            preferences.present(self.window, self)

    def _on_theme_state(self, action: Gio.SimpleAction, value: GLib.Variant) -> None:
        action.set_state(value)
        self.set_theme(value.get_string())

    def _on_shortcuts(self, action: Gio.SimpleAction, parameter) -> None:
        if self.window is not None:
            shortcuts.present(self.window)

    def _on_quit(self, action: Gio.SimpleAction, parameter) -> None:
        self.quit()

    def _on_unix_signal(self) -> bool:
        logger.info("caught termination signal, saving before quitting")
        self.flush()
        self.quit()
        return GLib.SOURCE_REMOVE
