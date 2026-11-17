"""Main application window and tab view wiring."""

import logging
import re
from collections.abc import Callable
from pathlib import Path

import gi

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")

from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from . import config, theme
from .sizes import human_size
from .storage import archive, notes, search, session
from .storage.archive import ArchiveEntry
from .storage.search import Result
from .storage.session import (
    KIND_FILE,
    KIND_SCRATCH,
    SessionState,
    TabState,
    WindowState,
)
from .tab import Tab
from .ui import archive as archive_dialog
from .ui import search as search_dialog
from .ui.find import FindBar
from .ui.status import StatusBar

logger = logging.getLogger(__name__)

# Session writes are debounced like buffer writes, for the same reason.
SESSION_SAVE_DELAY_MS = 500

# How many idle turns to keep trying to restore a scroll position before giving
# up on it. The view has to be allocated first, which does not happen until
# after the window has been presented.
SCROLL_RESTORE_ATTEMPTS = 20

# Long enough for the window manager to have restored an unmaximized window to
# its old size, which it does in its own time rather than on the call.
UNMAXIMIZE_SETTLE_MS = 250

NOTE_TITLE = re.compile(r"^Note (\d+)$")

# The window title, spaced out and bracketed by tildes.
TITLE_TEXT = "n o t e m a d"

# Long enough for a sentence of a title, short enough to stay a tab label.
TITLE_MAX_LENGTH = 60

# Past this, opening a file is worth asking about first. Nothing is refused:
# logs get read, and this only stops an accidental one costing a frozen window.
LARGE_FILE_BYTES = 100 * 1024 * 1024


def _resolved(path: Path) -> Path:
    """A path in its canonical form, or as given if that cannot be worked out."""
    try:
        return path.resolve()
    except OSError:
        return path


def _main_menu() -> Gio.Menu:
    """Everything the keyboard can do, for the times you would rather click."""
    notes_section = Gio.Menu()
    notes_section.append("New Note", "app.new-tab")
    notes_section.append("Open File", "app.open")
    notes_section.append("Recover Note", "app.recover")

    save_section = Gio.Menu()
    save_section.append("Save", "app.save")
    save_section.append("Save As", "app.save-as")
    save_section.append("Rename Note", "app.rename")
    save_section.append("Close Tab", "app.close-tab")

    edit_section = Gio.Menu()
    edit_section.append("Find", "app.find")
    edit_section.append("Find and Replace", "app.replace")
    edit_section.append("Search All Notes", "app.search-notes")

    view_section = Gio.Menu()
    view_section.append("Split View", "app.split")
    view_section.append("Move to Other Pane", "app.move-to-pane")

    app_section = Gio.Menu()
    app_section.append("Preferences", "app.preferences")
    app_section.append("Keyboard Shortcuts", "app.shortcuts")
    app_section.append("Quit", "app.quit")

    menu = Gio.Menu()
    menu.append_section(None, notes_section)
    menu.append_section(None, save_section)
    menu.append_section(None, edit_section)
    menu.append_section(None, view_section)
    menu.append_section(None, app_section)
    return menu


def _tab_menu() -> Gio.Menu:
    """The menu on a right click, acting on the tab that was clicked."""
    menu = Gio.Menu()
    menu.append("Rename Note", "app.rename")
    menu.append("Move to Other Pane", "app.move-to-pane")
    menu.append("Close Tab", "app.close-tab")
    return menu


def _finish_dialog(finish: Callable, result) -> Gio.File | None:
    """Complete a file dialog, treating the user cancelling it as a non-event."""
    try:
        return finish(result)
    except GLib.Error as error:
        if not error.matches(Gtk.dialog_error_quark(), Gtk.DialogError.DISMISSED):
            logger.warning("file dialog failed: %s", error.message)
        return None


class Pane:
    """One side of the window: a strip of tabs and the note in front of it."""

    def __init__(self, index: int) -> None:
        self.index = index

        self.tab_view = Adw.TabView(vexpand=True)
        self.tab_view.set_menu_model(_tab_menu())

        # Without autohide the strip stays put, so opening a second note does not
        # shift the text down by the height of the tab bar.
        self.tab_bar = Adw.TabBar(view=self.tab_view, autohide=False)

        self.widget = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.widget.append(self.tab_bar)
        self.widget.append(self.tab_view)


class NotemadWindow(Adw.ApplicationWindow):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.set_title(f"~  {TITLE_TEXT}  ~")
        self.set_default_size(session.DEFAULT_WIDTH, session.DEFAULT_HEIGHT)

        self.tabs: dict[Adw.TabPage, Tab] = {}
        self.theme = theme.get(theme.DEFAULT_THEME)
        self._next_note_number = 1
        self._session_timer = 0
        self._restoring = False
        self._closing = False
        self._moving = False
        # Pages on their way out. A close says so before the page is detached;
        # a drag to the other pane detaches it without a word, and that is the
        # only thing telling the two apart.
        self._closing_pages: set[Adw.TabPage] = set()
        self._focused_pane = 0
        # Where the divider sits, kept while the split is closed so that
        # reopening it lands where the user last left it.
        self._divider = session.SplitState().position
        # A restored position waiting for the window to have a width.
        self._pending_divider: float | None = None
        # The tab a context menu was opened on, so that Rename and the rest act
        # on the tab that was right clicked rather than the one in front.
        self._context_page: Adw.TabPage | None = None
        # Tabs currently showing a conflict dialog, so that switching back and
        # forth does not stack up another copy of it.
        self._conflicts: set[str] = set()
        # Likewise, so the status bar can be repainted without reaching back
        # into the application for the figure.
        self.archive_warn_bytes = config.DEFAULT_ARCHIVE_WARN

        self.panes = [Pane(index) for index in range(session.PANE_COUNT)]
        for pane in self.panes:
            self._wire_pane(pane)

        self.connect("notify::is-active", self._on_active_changed)
        self.connect("close-request", self._on_close_request)

        # Resizing the window is a change to the session like any other. Without
        # this the new size is only noticed if something else happens to ask for
        # a write before the window closes.
        self.connect("notify::default-width", self._on_geometry_changed)
        self.connect("notify::default-height", self._on_geometry_changed)
        self.connect("notify::maximized", self._on_geometry_changed)

        new_tab_button = Gtk.Button(icon_name="notemad-new-note-symbolic")
        new_tab_button.set_tooltip_text("New note")
        new_tab_button.set_action_name("app.new-tab")

        self.theme_button = Gtk.Button(icon_name=self.theme.icon)
        self.theme_button.set_action_name("app.toggle-theme")

        self.split_button = Gtk.ToggleButton(icon_name="notemad-split-view-symbolic")
        self.split_button.set_tooltip_text("Split the window in two")
        self.split_button.set_action_name("app.split")

        menu_button = Gtk.MenuButton(icon_name="open-menu-symbolic")
        menu_button.set_tooltip_text("Main menu")
        menu_button.set_menu_model(_main_menu())

        self.title_label = Gtk.Label()
        self.title_label.add_css_class("app-title")

        self.theme_button.add_css_class("theme-switch")

        separator = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
        separator.add_css_class("header-separator")

        header = Adw.HeaderBar(title_widget=self.title_label)
        # Set here rather than taken from the desktop, so the window controls sit
        # on the right and nothing else does, whatever the desktop prefers.
        header.set_decoration_layout("icon:minimize,maximize,close")
        header.pack_start(menu_button)
        header.pack_start(self.split_button)
        header.pack_start(self.theme_button)
        header.pack_start(separator)
        header.pack_start(new_tab_button)

        self.find_bar = FindBar(announce=self.toast)

        self.paned = Gtk.Paned(
            orientation=Gtk.Orientation.HORIZONTAL,
            start_child=self.panes[0].widget,
            end_child=self.panes[1].widget,
            resize_start_child=True,
            resize_end_child=True,
            shrink_start_child=False,
            shrink_end_child=False,
            vexpand=True,
        )
        self.paned.add_css_class("split")
        self.paned.connect("notify::position", self._on_divider_moved)
        self.panes[1].widget.set_visible(False)

        self.status_bar = StatusBar(on_archive_clicked=self.open_archive_dialog)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        content.append(header)
        content.append(self.find_bar)
        content.append(self.paned)
        content.append(self.status_bar)

        self.toasts = Adw.ToastOverlay(child=content)
        self.set_content(self.toasts)

    def _wire_pane(self, pane: Pane) -> None:
        view = pane.tab_view
        view.connect("page-detached", self._on_page_detached, pane)
        view.connect("page-attached", self._on_page_attached, pane)
        view.connect("page-reordered", self._on_page_reordered)
        view.connect("close-page", self._on_close_page)
        view.connect("notify::selected-page", self._on_selected_page_changed, pane)
        view.connect("setup-menu", self._on_setup_menu)

        # Renaming is a double click on a tab. A single click on the note
        # already in front used to do it too, which took the press that
        # dragging the tab to the other pane needs.
        clicks = Gtk.GestureClick(button=1)
        clicks.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        clicks.connect("pressed", self._on_tab_clicked, pane)
        pane.tab_bar.add_controller(clicks)

        # Whichever side last had the pointer or the keyboard is the side the
        # actions in the menu apply to.
        focus = Gtk.EventControllerFocus()
        focus.connect("enter", self._on_pane_focused, pane)
        pane.widget.add_controller(focus)

    @property
    def pane(self) -> Pane:
        """The side of the window the actions apply to."""
        return self.panes[self._focused_pane]

    @property
    def tab_view(self) -> Adw.TabView:
        return self.pane.tab_view

    def new_scratch_tab(self) -> Tab:
        tab = Tab.new_scratch(self._next_title())
        self._add_tab(tab, self.pane)
        return tab

    def active_tab(self) -> Tab | None:
        page = self.tab_view.get_selected_page()
        return self.tabs.get(page) if page is not None else None

    def close_active_tab(self) -> None:
        page = self._target_page()
        if page is not None:
            self._pane_of(page).tab_view.close_page(page)

    def select_next_tab(self) -> None:
        if not self.tab_view.select_next_page():
            self._select_page_at(0)

    def select_previous_tab(self) -> None:
        if not self.tab_view.select_previous_page():
            self._select_page_at(self.tab_view.get_n_pages() - 1)

    def select_tab_number(self, number: int) -> None:
        """Jump to a tab by its position in the strip, counting from one."""
        if 1 <= number <= self.tab_view.get_n_pages():
            self._select_page_at(number - 1)

    def rename_active_tab(self) -> None:
        page = self._target_page()
        if page is not None:
            self._start_rename(page)

    def toggle_find(self) -> None:
        """Find on an open find bar closes it, so the same key puts it away."""
        if self.find_bar.get_reveal_child():
            self.find_bar.close()
            return
        self.open_find()

    def open_find(self, replacing: bool = False) -> None:
        tab = self.active_tab()
        if tab is None:
            return
        self.find_bar.attach(tab.view)
        self.find_bar.open(replacing)

    def update_status(self) -> None:
        """Point the status bar at whichever note is in front."""
        tab = self.active_tab()
        if tab is None:
            return

        cursor = tab.buffer.get_iter_at_mark(tab.buffer.get_insert())
        self.status_bar.update(cursor.get_line() + 1, cursor.get_line_offset() + 1)

    def is_split(self) -> bool:
        return self.panes[1].widget.get_visible()

    def set_split(self, enabled: bool, page: Adw.TabPage | None = None) -> None:
        """Show or hide the second pane, moving notes so neither side is empty.

        A page can be named to say which note should end up on the right.
        """
        if enabled == self.is_split():
            return

        if enabled:
            self._open_split(page)
        else:
            self._close_split()

        self._publish_split_state()
        self.save_session()

    def _publish_split_state(self) -> None:
        """Tell the action what actually happened, since the window has the say.

        The button in the header follows the action, and the window collapses
        the split on its own when a side is emptied, so the two would drift
        apart otherwise.
        """
        application = self.get_application()
        if application is None:
            return

        action = application.lookup_action("split")
        if action is not None:
            action.set_state(GLib.Variant.new_boolean(self.is_split()))

    def move_active_tab_to_other_pane(self) -> None:
        page = self._target_page()
        if page is None:
            return

        source = self._pane_of(page)

        if not self.is_split():
            # Opening the split is itself a move, so this note is the one that
            # goes across rather than whatever happened to be in front.
            self.set_split(True, page)
            return

        destination = self.panes[1 - source.index]
        if source.tab_view.get_n_pages() == 1:
            # Sending the last note out of the second pane is another way of
            # saying stop splitting, and that is what closing the split does.
            if source.index == 1:
                self.set_split(False)
            else:
                self.toast("That is the only note on this side")
            return

        self._reparent(page, source, destination)
        self.save_session()

    def mark_closing(self) -> None:
        """Stop replacing closed tabs, because the whole window is going away."""
        self._closing = True

    def _open_split(self, page: Adw.TabPage | None = None) -> None:
        self.panes[1].widget.set_visible(True)
        self._place_divider(self._divider)

        # The second pane has to start with something in it, so a note moves
        # across, or a new one is made if that would empty the first pane
        # entirely.
        source = self.panes[0]
        page = page or source.tab_view.get_selected_page()
        if source.tab_view.get_n_pages() > 1 and page is not None:
            self._reparent(page, source, self.panes[1])
        else:
            self._add_tab(Tab.new_scratch(self._next_title()), self.panes[1])

    def _close_split(self) -> None:
        source = self.panes[1]
        while source.tab_view.get_n_pages():
            self._reparent(source.tab_view.get_nth_page(0), source, self.panes[0])

        self.panes[1].widget.set_visible(False)
        self._focused_pane = 0

    def _reparent(self, page: Adw.TabPage, source: Pane, destination: Pane) -> None:
        """Move one note from one side to the other, keeping it selected.

        This is a transfer rather than a close and reopen: the page survives, so
        the note is not archived on the way past and nothing needs remapping.
        """
        tab = self.tabs.get(page)
        if tab is None:
            return

        # A transfer detaches the page before attaching it again, and a detached
        # page normally means a tab has gone.
        self._moving = True
        try:
            source.tab_view.transfer_page(
                page, destination.tab_view, destination.tab_view.get_n_pages()
            )
        finally:
            self._moving = False

        destination.tab_view.set_selected_page(page)
        self._focused_pane = destination.index
        tab.view.grab_focus()

    def _place_divider(self, fraction: float, attempt: int = 0) -> bool:
        """Put the divider at a fraction of the width, once there is a width.

        Called before the window has been allocated there is nothing to measure
        against, so it waits for one rather than dividing nothing in half.
        """
        width = self.paned.get_width()
        if width > 0:
            self.paned.set_position(int(width * fraction))
            self._pending_divider = None
        elif attempt < SCROLL_RESTORE_ATTEMPTS:
            GLib.idle_add(
                self._place_divider, fraction, attempt + 1, priority=GLib.PRIORITY_LOW
            )
        return GLib.SOURCE_REMOVE

    def _divider_fraction(self) -> float:
        # A restored position that has not been applied yet is still the truth
        # about where the divider belongs, and saving over it would lose it.
        if self._pending_divider is not None:
            return self._pending_divider

        width = self.paned.get_width()
        # With the split closed the paned reports something meaningless, so the
        # last width the user chose is remembered for the next time they split.
        if not self.is_split() or width <= 0:
            return self._divider

        fraction = self.paned.get_position() / width
        self._divider = min(max(fraction, session.MIN_SPLIT), session.MAX_SPLIT)
        return self._divider

    def toast(self, message: str) -> None:
        self.toasts.add_toast(Adw.Toast(title=message))

    def open_file_dialog(self) -> None:
        dialog = Gtk.FileDialog(title="Open File")
        dialog.open(self, None, self._on_open_finished)

    def open_path(self, path: Path) -> None:
        already_open = self._tab_for_path(path)
        if already_open is not None:
            self._select(already_open)
            return

        # Reading happens on the same thread that draws the window, so a big
        # enough file freezes it for as long as the read takes. Nothing is
        # refused, but a mis-click on something enormous is worth catching.
        size = notes.file_size(path)
        if size is not None and size > LARGE_FILE_BYTES:
            self._confirm_large_open(path, size)
            return

        self._open_now(path)

    def _open_now(self, path: Path) -> None:
        text = notes.read_file(path)
        if text is None:
            self.toast(f"Could not read {path.name}")
            return

        self._add_tab(Tab.new_file(path, text), self.pane)

    def _confirm_large_open(self, path: Path, size: int) -> None:
        dialog = Adw.AlertDialog(
            heading="This is a large file",
            body=f"{path.name} is {human_size(size)}. Notemad will read all of "
            "it before the window responds again, and will hold it in memory "
            "while it is open.",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("open", "Open Anyway")
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", self._on_large_open_response, path)
        dialog.present(self)

    def _on_large_open_response(
        self, dialog: Adw.AlertDialog, response: str, path: Path
    ) -> None:
        if response == "open":
            self._open_now(path)

    def open_archive_dialog(self) -> None:
        archive_dialog.present(
            self,
            self.recover_note,
            warn_at=self.archive_warn_bytes,
            on_changed=self.update_archive_usage,
        )

    def update_archive_usage(self) -> None:
        """Put the current archive size in the status bar.

        Called when something has changed the archive rather than on a timer.
        It is a stat for each file, which is cheap, but not free enough to run
        for no reason.
        """
        self.status_bar.set_archive(archive.usage(), self.archive_warn_bytes)

    def open_search_dialog(self) -> None:
        """Search every note, having first made sure the disk knows about them.

        The search reads files rather than buffers, so anything typed in the
        last half second has to be written out, and the session with it since
        that is where the names live.
        """
        self.flush_all()
        self.save_session()
        search_dialog.present(
            self, theme.source_scheme(self.theme), self.open_search_result
        )

    def open_search_result(self, result: Result, offset: int, query: str) -> None:
        """Bring up whatever a search result points at, sitting on the match."""
        tab = self._open_tab_for(result)
        if tab is None:
            return

        self._reveal_match(tab, offset, query)

    def _open_tab_for(self, result: Result) -> Tab | None:
        """The tab a result belongs in, opening one if it is not already there."""
        if result.archived:
            text = search.read(result)
            if text is None:
                self.toast("That note is not there any more")
                return None
            tab = Tab.new_recovered(result.title or self._next_title(), text)
            self._add_tab(tab, self.pane)
            return tab

        # A result from a file opened from disk belongs to that file, which is
        # already open in a tab, since that is the only way it was searched.
        if result.path is not None:
            tab = self._tab_for_path(Path(result.path))
            if tab is None:
                self.open_path(Path(result.path))
                return self._tab_for_path(Path(result.path))
            self._select(tab)
            return tab

        tab = self._tab_for_id(result.id)
        if tab is not None:
            self._select(tab)
            return tab

        text = search.read(result)
        if text is None:
            self.toast("That note is not there any more")
            return None

        tab = Tab.from_state(
            TabState(
                id=result.id,
                kind=KIND_SCRATCH,
                title=result.title or self._next_title(),
            )
        )
        tab.text = text
        self._add_tab(tab, self.pane)
        return tab

    def _reveal_match(self, tab: Tab, offset: int, query: str) -> None:
        """Select the match, so the eye lands on it and Ctrl+F picks it up."""
        length = tab.buffer.get_char_count()
        start = tab.buffer.get_iter_at_offset(min(offset, length))
        end = tab.buffer.get_iter_at_offset(min(offset + len(query), length))
        tab.buffer.select_range(start, end)
        GLib.idle_add(self._scroll_to_selection, tab, 0)

    def _scroll_to_selection(self, tab: Tab, attempt: int) -> bool:
        """Scroll the match into view, once the view has been given a size."""
        if tab.view.get_height() <= 0 and attempt < SCROLL_RESTORE_ATTEMPTS:
            GLib.idle_add(self._scroll_to_selection, tab, attempt + 1)
        else:
            tab.view.scroll_to_mark(tab.buffer.get_insert(), 0.15, True, 0.0, 0.3)
        return GLib.SOURCE_REMOVE

    def recover_note(self, entry: ArchiveEntry, text: str) -> None:
        """Open an archived note as a new note, leaving the archived copy alone.

        A new identity rather than the old one, because the id an archived note
        was stored under can still belong to an open tab: saving a scratch note
        to a file archives the note while its tab carries on as the file.
        """
        tab = Tab.new_recovered(entry.title or self._next_title(), text)
        self._add_tab(tab, self.pane)
        self.toast(f"Recovered {tab.title}")

    def save_active_tab(self) -> None:
        tab = self.active_tab()
        if tab is None:
            return

        if tab.kind == KIND_SCRATCH:
            self.toast("Scratch notes save themselves")
            return

        if tab.has_changed_on_disk():
            self._show_conflict(tab)
            return

        self._write(tab, Path(tab.path))

    def save_as_dialog(self) -> None:
        tab = self.active_tab()
        if tab is None:
            return

        dialog = Gtk.FileDialog(title="Save As")
        if tab.path is not None:
            dialog.set_initial_file(Gio.File.new_for_path(tab.path))
        else:
            dialog.set_initial_name(f"{tab.title}.txt")
        dialog.save(self, None, self._on_save_as_finished, tab)

    def restore(self, state: SessionState) -> None:
        """Rebuild the window from a stored session.

        Anything that cannot be brought back is dropped rather than allowed to
        stop the rest of the session from opening.
        """
        self.set_default_size(state.window.width, state.window.height)
        if state.window.maximized:
            self.maximize()

        # Attaching and selecting pages emits the same signals that normally ask
        # for a session write. Left alone, restoring the session would overwrite
        # it halfway through with a partly rebuilt version of itself.
        self._restoring = True
        try:
            self.panes[1].widget.set_visible(state.split.enabled)
            pending_scroll = self._restore_tabs(state)

            # An empty second pane is not a state the window can be left in, so
            # the split goes with it.
            if state.split.enabled and not self.panes[1].tab_view.get_n_pages():
                self.panes[1].widget.set_visible(False)
        finally:
            self._restoring = False

        self._publish_split_state()
        if self.is_split():
            # The paned has no width until it has been allocated, so a position
            # set now would be measured against nothing.
            self._pending_divider = state.split.position
            GLib.idle_add(self._place_divider, state.split.position)

        if not self.tabs:
            self.new_scratch_tab()
            return

        self.update_status()

        # A file may well have been edited by something else while Notemad was
        # closed, but asking about it before the window is even up would be
        # rude, so it waits until everything has settled.
        GLib.idle_add(self._check_for_conflict)

        if pending_scroll:
            GLib.idle_add(
                self._restore_scroll,
                pending_scroll,
                0,
                priority=GLib.PRIORITY_LOW,
            )

    def focus_active_view(self) -> bool:
        """Put the keyboard into the note in front.

        Tabs are built before the window is presented, so the focus each one
        grabs on the way in is taken back when the window is mapped. This runs
        from an idle callback once the window is actually up, which is the only
        point the focus will stay where it is put.
        """
        tab = self.active_tab()
        if tab is not None:
            tab.view.grab_focus()
        return GLib.SOURCE_REMOVE

    @property
    def app(self):
        return self.get_application()

    def apply_archive_warning(self, warn_at: int) -> None:
        """Set the size the archive figure starts warning at, and repaint it."""
        self.archive_warn_bytes = warn_at
        self.update_archive_usage()

    def apply_theme(
        self,
        theme_id: str,
        dark_id: str = theme.DEFAULT_THEME,
        light_id: str = theme.DEFAULT_LIGHT_THEME,
    ) -> None:
        self.theme = theme.apply(theme_id, dark_id, light_id)
        scheme = theme.source_scheme(self.theme)
        for tab in self.tabs.values():
            tab.set_style_scheme(scheme)

        self.title_label.set_markup(
            f'<span foreground="{self.theme.accent}">~</span>'
            f"  {TITLE_TEXT}  "
            f'<span foreground="{self.theme.accent}">~</span>'
        )
        self.theme_button.set_icon_name(self.theme.icon)
        target = light_id if self.theme.dark else dark_id
        self.theme_button.set_tooltip_text(f"Switch to {theme.get(target).name}")

    def flush_all(self) -> None:
        """Write out every tab that has unsaved changes, and the session."""
        for tab in self.tabs.values():
            tab.flush()
        self.save_session()

    def _on_save_failed(self, title: str) -> None:
        """Say so when a note could not be written out.

        Notemad never asks the user to save, which means it has to tell them
        when saving is not happening. Carrying on quietly would be worse than
        any error message.
        """
        self.toast(f"Could not save {title}. Check the disk has room")

    def save_session(self) -> None:
        self._cancel_pending_session_save()
        if self._restoring:
            return
        try:
            session.save(self.session_state())
        except OSError as exc:
            # Losing the session costs the arrangement of the tabs, not their
            # contents, so this is worth a log line rather than interrupting.
            logger.warning("could not write the session: %s", exc)

    def session_state(self) -> SessionState:
        tabs = []
        selected: list[str | None] = []

        for pane in self.panes:
            for position in range(pane.tab_view.get_n_pages()):
                tab = self.tabs.get(pane.tab_view.get_nth_page(position))
                if tab is not None:
                    state = tab.state()
                    state.pane = pane.index
                    tabs.append(state)

            front = self.tabs.get(pane.tab_view.get_selected_page())
            selected.append(front.id if front is not None else None)

        active = self.active_tab()
        return SessionState(
            active_tab=active.id if active is not None else None,
            active_tabs=selected,
            window=self._window_state(),
            tabs=tabs,
            split=session.SplitState(
                enabled=self.is_split(),
                position=self._divider_fraction(),
            ),
        )

    def schedule_session_save(self) -> None:
        if self._restoring:
            return
        self._cancel_pending_session_save()
        self._session_timer = GLib.timeout_add(
            SESSION_SAVE_DELAY_MS, self._on_session_timeout
        )

    def _restore_tabs(self, state: SessionState) -> list[tuple[Tab, float]]:
        pending_scroll = []
        highest_number = 0

        for tab_state in state.tabs:
            restored = self._content_for(tab_state)
            if restored is None:
                logger.info("dropping tab %s, its content is gone", tab_state.id)
                continue

            content, from_draft = restored
            tab = Tab.from_state(tab_state)
            tab.text = content
            tab.set_cursor_offset(tab_state.cursor_offset)
            tab.modified = from_draft

            # Content read straight off disk is the version Notemad is working
            # from, so the recorded mtime moves with it. Otherwise every file
            # edited elsewhere between sessions would raise a conflict about a
            # difference the user cannot see.
            if tab.kind == KIND_FILE and not from_draft and tab.path is not None:
                tab.external_mtime = notes.file_mtime(tab.path)

            pane = self.panes[tab_state.pane if state.split.enabled else 0]
            self._add_tab(
                tab,
                pane,
                select=tab_state.id in state.active_tabs,
            )
            if tab_state.id == state.active_tab:
                self._focused_pane = pane.index

            if tab_state.scroll_offset > 0:
                pending_scroll.append((tab, tab_state.scroll_offset))

            match = NOTE_TITLE.match(tab.title)
            if match:
                highest_number = max(highest_number, int(match.group(1)))

        # Carry on numbering where the restored session left off rather than
        # handing the user a second "Note 1".
        self._next_note_number = highest_number + 1

        if self.tabs and self.tab_view.get_selected_page() is None:
            self.tab_view.set_selected_page(self.tab_view.get_nth_page(0))

        return pending_scroll

    def _content_for(self, tab_state: TabState) -> tuple[str, bool] | None:
        """Find what a restored tab should open with, or None to drop it.

        Returns the content and whether it came from a draft, which is the same
        question as whether the tab has changes the file on disk does not.
        """
        if tab_state.kind == KIND_SCRATCH:
            content = notes.read_note(tab_state.id)
            return None if content is None else (content, False)

        # An external file with unsaved changes is represented by its draft. The
        # file on disk is not read, so nothing can quietly discard the changes
        # the user has not saved yet.
        if tab_state.has_draft:
            draft = notes.read_draft(tab_state.id)
            if draft is not None:
                return draft, True

        if tab_state.path is None:
            return None

        content = notes.read_file(tab_state.path)
        return None if content is None else (content, False)

    def _restore_scroll(self, pending: list[tuple[Tab, float]], attempt: int) -> bool:
        remaining = [
            (tab, fraction)
            for tab, fraction in pending
            if not tab.set_scroll_fraction(fraction)
        ]
        if remaining and attempt < SCROLL_RESTORE_ATTEMPTS:
            GLib.idle_add(
                self._restore_scroll,
                remaining,
                attempt + 1,
                priority=GLib.PRIORITY_LOW,
            )
        return GLib.SOURCE_REMOVE

    def _add_tab(self, tab: Tab, pane: Pane, select: bool = True) -> None:
        page = pane.tab_view.append(tab.root)
        self.tabs[page] = tab
        self._label(page, tab)
        tab.set_style_scheme(theme.source_scheme(self.theme))

        tab.buffer.connect("notify::cursor-position", self._on_tab_state_changed)
        tab.scroller.get_vadjustment().connect(
            "value-changed", self._on_tab_state_changed
        )
        tab.buffer.connect("changed", self._on_buffer_changed, page)
        tab.on_save_failed = self._on_save_failed

        if select:
            pane.tab_view.set_selected_page(page)
            self._focused_pane = pane.index
            tab.view.grab_focus()

        self.update_status()
        self.schedule_session_save()

    def _pane_of(self, page: Adw.TabPage) -> Pane:
        """Which side a page is on.

        Asked by walking the pages rather than with get_page_position, which
        complains loudly when handed a page from the other view.
        """
        for pane in self.panes:
            for position in range(pane.tab_view.get_n_pages()):
                if pane.tab_view.get_nth_page(position) is page:
                    return pane
        return self.pane

    def _target_page(self) -> Adw.TabPage | None:
        """The tab an action applies to: the right clicked one, or the one in front."""
        return self._context_page or self.tab_view.get_selected_page()

    def _on_tab_clicked(
        self, gesture: Gtk.GestureClick, presses: int, x: float, y: float, pane: Pane
    ) -> None:
        if presses < 2:
            return

        position = self._tab_position_at(pane, x, y)
        if position is None:
            return

        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        self._start_rename(pane.tab_view.get_nth_page(position))

    def _tab_position_at(self, pane: Pane, x: float, y: float) -> int | None:
        """Which tab, counting from zero, sits under a point on a strip.

        A point on one of the buttons a tab carries counts as no tab at all.
        This runs in the capture phase, so treating the close button as part of
        the tab behind it would rename the note instead of closing it.
        """
        picked = pane.tab_bar.pick(x, y, Gtk.PickFlags.DEFAULT)
        while picked is not None and picked.get_css_name() != "tabboxchild":
            if isinstance(picked, Gtk.Button) or picked is pane.tab_bar:
                return None
            picked = picked.get_parent()

        if picked is None:
            return None

        children = self._tab_children(pane)
        return children.index(picked) if picked in children else None

    def _tab_widget_at(self, pane: Pane, position: int) -> Gtk.Widget | None:
        """The widget in the strip standing for a page, for a popover to point at."""
        children = self._tab_children(pane)
        return children[position] if 0 <= position < len(children) else None

    def _tab_children(self, pane: Pane) -> list[Gtk.Widget]:
        """The tab widgets on one strip, left to right.

        The strip is libadwaita's, so this walks what it built rather than
        assuming a shape. Zero width entries are left out: the tab box keeps
        placeholders around for tabs being animated in and out.
        """
        found: list[tuple[float, Gtk.Widget]] = []

        def walk(widget: Gtk.Widget) -> None:
            if widget.get_css_name() == "tabboxchild":
                ok, bounds = widget.compute_bounds(pane.tab_bar)
                if ok and bounds.size.width > 0:
                    found.append((bounds.origin.x, widget))
                return
            child = widget.get_first_child()
            while child is not None:
                walk(child)
                child = child.get_next_sibling()

        walk(pane.tab_bar)
        return [widget for _, widget in sorted(found, key=lambda pair: pair[0])]

    def _start_rename(self, page: Adw.TabPage) -> None:
        tab = self.tabs.get(page)
        if tab is None:
            return

        if tab.kind == KIND_FILE:
            # The title of a file tab is the name of a file on disk, and Notemad
            # does not touch those without being asked to.
            self.toast("Files are named on disk. Use Save As to rename one")
            return

        pane = self._pane_of(page)
        entry = Gtk.Entry(text=tab.title, width_chars=18)
        entry.set_max_length(TITLE_MAX_LENGTH)

        popover = Gtk.Popover(child=entry, autohide=True)
        popover.set_parent(pane.tab_bar)
        popover.add_css_class("rename")
        popover.connect("closed", lambda widget: widget.unparent())

        anchor = self._tab_widget_at(pane, pane.tab_view.get_page_position(page))
        if anchor is not None:
            found, bounds = anchor.compute_bounds(pane.tab_bar)
            if found:
                # A Gdk.Rectangle ignores anything passed to its constructor, so
                # the fields have to be assigned afterwards.
                area = Gdk.Rectangle()
                area.x = int(bounds.origin.x)
                area.y = int(bounds.origin.y)
                area.width = int(bounds.size.width)
                area.height = int(bounds.size.height)
                popover.set_pointing_to(area)

        entry.connect("activate", self._on_rename_entered, popover, page)
        popover.popup()
        entry.grab_focus()
        entry.select_region(0, -1)

    def _on_rename_entered(
        self, entry: Gtk.Entry, popover: Gtk.Popover, page: Adw.TabPage
    ) -> None:
        popover.popdown()

        tab = self.tabs.get(page)
        title = entry.get_text().strip()
        if tab is None or not title or title == tab.title:
            return

        tab.title = title
        self._label(page, tab)
        self.save_session()

    def _label(self, page: Adw.TabPage, tab: Tab) -> None:
        page.set_title(tab.display_title())
        # The tab strip only has room for a file name, so the full path lives in
        # the tooltip.
        page.set_tooltip(tab.path or "")

    def _select(self, tab: Tab) -> None:
        page = self._page_for(tab)
        if page is None:
            return
        pane = self._pane_of(page)
        pane.tab_view.set_selected_page(page)
        self._focused_pane = pane.index
        tab.view.grab_focus()

    def _select_page_at(self, position: int) -> None:
        page = self.tab_view.get_nth_page(position)
        tab = self.tabs.get(page)
        if tab is not None:
            self._select(tab)

    def _page_for(self, tab: Tab) -> Adw.TabPage | None:
        for page, candidate in self.tabs.items():
            if candidate is tab:
                return page
        return None

    def _tab_for_id(self, note_id: str) -> Tab | None:
        for tab in self.tabs.values():
            if tab.id == note_id:
                return tab
        return None

    def _tab_for_path(self, path: Path) -> Tab | None:
        """The tab a file is already open in, however its path was spelled.

        Compared after resolving, so that a relative path, a symlink and the
        real thing all find the tab that is already there rather than opening a
        second one that saves over the first.
        """
        wanted = _resolved(path)
        for tab in self.tabs.values():
            if tab.path is not None and _resolved(Path(tab.path)) == wanted:
                return tab
        return None

    def _write(self, tab: Tab, path: Path) -> None:
        try:
            tab.save_to(path)
        except OSError as exc:
            logger.warning("could not save %s: %s", path, exc)
            self.toast(f"Could not save {path.name}")
            return

        page = self._page_for(tab)
        if page is not None:
            self._label(page, tab)
        self.toast(f"Saved {path.name}")
        self.save_session()

    def _show_conflict(self, tab: Tab) -> None:
        """Ask what to do about a file that changed underneath the user."""
        if tab.id in self._conflicts:
            return
        self._conflicts.add(tab.id)

        dialog = Adw.AlertDialog(
            heading="File changed on disk",
            body=f"{tab.path} has been modified since Notemad last read it.",
        )
        dialog.add_response("keep", "Keep My Version")
        dialog.add_response("reload", "Reload From Disk")
        dialog.add_response("save-as", "Save As")
        dialog.set_response_appearance("reload", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("keep")
        dialog.set_close_response("keep")
        dialog.connect("response", self._on_conflict_response, tab)
        dialog.present(self)

    def _on_conflict_response(self, dialog: Adw.AlertDialog, response: str, tab: Tab):
        self._conflicts.discard(tab.id)
        if tab.path is None:
            return

        if response == "reload":
            content = notes.read_file(tab.path)
            if content is None:
                self.toast(f"Could not read {Path(tab.path).name}")
                return
            tab.text = content
            tab.external_mtime = notes.file_mtime(tab.path)
            if tab.has_draft:
                notes.discard_draft(tab.id)
                tab.has_draft = False
            self.toast("Reloaded from disk")
        elif response == "save-as":
            self.save_as_dialog()
            return
        else:
            # Keeping the buffer means accepting the version on disk as the one
            # being diverged from, otherwise the same question comes back on
            # every switch to this tab.
            tab.external_mtime = notes.file_mtime(tab.path)
            tab.modified = True

        page = self._page_for(tab)
        if page is not None:
            self._label(page, tab)
        self.save_session()

    def _on_open_finished(self, dialog: Gtk.FileDialog, result) -> None:
        chosen = _finish_dialog(dialog.open_finish, result)
        if chosen is None:
            return

        path = chosen.get_path()
        if path is None:
            self.toast("That location cannot be opened")
            return
        self.open_path(Path(path))

    def _on_save_as_finished(self, dialog: Gtk.FileDialog, result, tab: Tab) -> None:
        chosen = _finish_dialog(dialog.save_finish, result)
        if chosen is None:
            return

        path = chosen.get_path()
        if path is None:
            self.toast("That location cannot be written to")
            return
        self._write(tab, Path(path))

    def _on_buffer_changed(self, buffer, page: Adw.TabPage) -> None:
        tab = self.tabs.get(page)
        if tab is not None:
            self._label(page, tab)

    def _window_state(self) -> WindowState:
        # GTK keeps the default size up to date as the window is dragged about,
        # and unlike the live size it still reads correctly once the window has
        # been torn down. The session is written during shutdown, by which point
        # the live size is zero and would be thrown away for the fallback.
        width, height = self.get_default_size()
        if not width or not height:
            width, height = self.get_width(), self.get_height()

        return WindowState(
            width=width or session.DEFAULT_WIDTH,
            height=height or session.DEFAULT_HEIGHT,
            maximized=self.is_maximized(),
        )

    def reset_size(self) -> None:
        """Forget the remembered size, so the next launch is the default one.

        GTK hands the size of a window that is already on screen to the window
        manager, and nothing an application does will shrink it back, so this
        changes where Notemad opens rather than what is in front of you.
        """
        if not self.is_maximized():
            self._apply_default_size()
            return

        self.unmaximize()
        # The size only settles once the window manager has put the window back
        # to what it was before it was maximized, and anything set before then
        # is overwritten by that restore.
        GLib.timeout_add(UNMAXIMIZE_SETTLE_MS, self._apply_default_size)

    def _apply_default_size(self) -> bool:
        self.set_default_size(session.DEFAULT_WIDTH, session.DEFAULT_HEIGHT)
        self.save_session()
        return GLib.SOURCE_REMOVE

    def _on_geometry_changed(self, window, parameter) -> None:
        self.schedule_session_save()

    def _next_title(self) -> str:
        title = f"Note {self._next_note_number}"
        self._next_note_number += 1
        return title

    def _on_session_timeout(self) -> bool:
        self._session_timer = 0
        self.save_session()
        return GLib.SOURCE_REMOVE

    def _cancel_pending_session_save(self) -> None:
        if self._session_timer:
            GLib.source_remove(self._session_timer)
            self._session_timer = 0

    # Serves both the cursor notification and the scroll adjustment, which do
    # not carry the same arguments.
    def _on_tab_state_changed(self, *args) -> None:
        self.update_status()
        self.schedule_session_save()

    def _on_page_detached(
        self, tab_view: Adw.TabView, page: Adw.TabPage, position: int, pane: Pane
    ) -> None:
        if self._moving:
            # On its way to the other pane, not going anywhere.
            return

        if page not in self._closing_pages:
            # Dragged to the other pane rather than closed. The page itself
            # survives the move, so the note is still one of the window's and
            # there is nothing to tidy up.
            return

        self._closing_pages.discard(page)
        self.tabs.pop(page, None)
        if self._closing or self._restoring:
            return

        # A pane emptying while the window is split collapses the split rather
        # than sitting there as a blank half of the window.
        if not tab_view.get_n_pages() and self.is_split():
            self._focused_pane = 1 - pane.index
            self.set_split(False)
            return

        # An empty window is not a state worth being in, and it would leave the
        # user with nothing to type into.
        if not self.tabs:
            self.new_scratch_tab()
            return

        self.schedule_session_save()

    def _on_page_attached(
        self, tab_view: Adw.TabView, page: Adw.TabPage, position: int, pane: Pane
    ) -> None:
        if self._restoring or page not in self.tabs:
            return

        self._focused_pane = pane.index
        self.schedule_session_save()

    def _archive(self, tab: Tab) -> None:
        """Put whatever a closed tab was holding somewhere it can be found again.

        For a file tab that means its draft, which is the only copy of changes
        the user never saved. Nothing else would ever look at it again once the
        tab that knew about it is gone.
        """
        if tab.kind == KIND_SCRATCH:
            archive.archive_note(tab.id, tab.title, tab.last_viewed)
        elif tab.has_draft and tab.path is not None:
            archive.archive_draft(tab.id, tab.title, tab.path, tab.last_viewed)

        self.update_archive_usage()

    def _on_page_reordered(
        self, tab_view: Adw.TabView, page: Adw.TabPage, position: int
    ) -> None:
        self.schedule_session_save()

    def _on_close_page(self, tab_view: Adw.TabView, page: Adw.TabPage) -> bool:
        self._closing_pages.add(page)
        tab = self.tabs.get(page)
        if tab is not None:
            # Closing never asks anything. It writes what is there and puts it
            # in the archive, so an accidental close is something the user can
            # come back from rather than something they have to be warned about.
            tab.flush()
            if not self._closing:
                self._archive(tab)

        # Let the default handler get on with closing the page.
        return False

    def _on_selected_page_changed(
        self, tab_view: Adw.TabView, param, pane: Pane
    ) -> None:
        page = tab_view.get_selected_page()
        if page is not None:
            self._focused_pane = pane.index
            tab = self.tabs.get(page)
            if tab is not None:
                tab.viewed()

        # Switching away is the moment a buffer that has not settled would
        # otherwise sit unwritten behind a tab nobody is looking at.
        self.flush_all()
        self.update_status()
        self._check_for_conflict()

        # An open find bar follows the tab in front of it rather than going on
        # searching a buffer nobody is looking at.
        if self.find_bar.get_reveal_child():
            tab = self.active_tab()
            self.find_bar.attach(tab.view if tab is not None else None)

    def _on_pane_focused(
        self, controller: Gtk.EventControllerFocus, pane: Pane
    ) -> None:
        self._focused_pane = pane.index
        self.update_status()

    def _on_setup_menu(self, tab_view: Adw.TabView, page: Adw.TabPage | None) -> None:
        # Given a page when the menu opens and nothing when it closes.
        self._context_page = page
        if page is not None:
            self._focused_pane = self._pane_of(page).index

    def _on_divider_moved(self, paned: Gtk.Paned, param) -> None:
        if self.is_split():
            self.schedule_session_save()

    def _check_for_conflict(self) -> bool:
        """Prompt if the file behind the active tab has changed underneath it."""
        if self._restoring:
            return GLib.SOURCE_REMOVE

        tab = self.active_tab()
        if tab is not None and tab.has_changed_on_disk():
            self._show_conflict(tab)
        return GLib.SOURCE_REMOVE

    def _on_active_changed(self, window: Adw.ApplicationWindow, param) -> None:
        if not self.is_active():
            self.flush_all()

    def _on_close_request(self, window: Adw.ApplicationWindow) -> bool:
        self.flush_all()
        self.mark_closing()
        return False
