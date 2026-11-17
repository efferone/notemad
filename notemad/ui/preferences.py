"""The preferences dialog."""

import gi

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")

from gi.repository import Adw, GLib, Gtk, Pango

from .. import config, sizes, theme
from ..storage import session

THEME_SUBTITLE = "Follow System takes the desktop colour scheme"


def present(parent: Gtk.Widget, app) -> None:
    dialog = Adw.PreferencesDialog()
    dialog.add(_appearance_page(app, dialog))
    dialog.present(parent)


def _appearance_page(app, dialog: Adw.PreferencesDialog) -> Adw.PreferencesPage:
    page = Adw.PreferencesPage(title="Appearance", icon_name="applications-graphics")
    page.add(_theme_group(app))
    page.add(_font_group(app))
    page.add(_window_group(app, dialog))
    page.add(_storage_group(app))
    return page


def _storage_group(app) -> Adw.PreferencesGroup:
    group = Adw.PreferencesGroup(
        title="Storage",
        description="Closing a note keeps it in the archive rather than throwing "
        "it away, so the archive only grows until you clear some of it out. The "
        "status bar shows what it is taking up.",
    )

    labels = ["Never"] + [
        sizes.human_size(size) for size in config.ARCHIVE_WARN_CHOICES[1:]
    ]
    row = Adw.ComboRow(
        title="Warn about the archive at",
        subtitle="The figure in the status bar changes colour past this. Nothing "
        "is ever removed without being asked for.",
        model=Gtk.StringList.new(labels),
    )
    row.set_subtitle_lines(0)

    current = app.config.archive_warn_bytes
    choices = list(config.ARCHIVE_WARN_CHOICES)
    row.set_selected(choices.index(current) if current in choices else 0)
    # Connected after the initial selection, so setting it does not read as a
    # choice the user just made.
    row.connect(
        "notify::selected",
        lambda combo, _param: app.set_archive_warning(choices[combo.get_selected()]),
    )

    group.add(row)
    return group


def _window_group(app, dialog: Adw.PreferencesDialog) -> Adw.PreferencesGroup:
    group = Adw.PreferencesGroup(title="Window")

    button = Gtk.Button(label="Reset to default", valign=Gtk.Align.CENTER)
    button.set_sensitive(app.window is not None)
    button.connect("clicked", lambda _widget: _reset_size(app, dialog))

    row = Adw.ActionRow(
        title="Window size",
        subtitle="Notemad reopens at whatever size you left it. Resetting takes "
        f"effect the next time you open it, at {session.DEFAULT_WIDTH} by "
        f"{session.DEFAULT_HEIGHT}, since a window already on screen is the "
        "desktop's to size rather than Notemad's.",
    )
    row.set_subtitle_lines(0)
    row.add_suffix(button)
    group.add(row)
    return group


def _reset_size(app, dialog: Adw.PreferencesDialog) -> None:
    app.window.reset_size()
    dialog.add_toast(
        Adw.Toast(
            title=f"Notemad will open at {session.DEFAULT_WIDTH} by "
            f"{session.DEFAULT_HEIGHT} next time"
        )
    )


def _theme_group(app) -> Adw.PreferencesGroup:
    group = Adw.PreferencesGroup(title="Theme")
    ids = [theme_id for theme_id, _ in theme.PREFERENCES]
    labels = Gtk.StringList.new([label for _, label in theme.PREFERENCES])

    row = Adw.ComboRow(title="Colours", subtitle=THEME_SUBTITLE, model=labels)
    current = app.config.theme
    row.set_selected(ids.index(current) if current in ids else 0)

    def chosen(combo, _param):
        # Through the action rather than straight to the application, so the
        # header button showing the same choice stays in step.
        app.theme_action.change_state(
            GLib.Variant.new_string(ids[combo.get_selected()])
        )

    # Connected after the initial selection, so setting it does not read as a
    # choice the user just made.
    row.connect("notify::selected", chosen)
    group.add(row)
    return group


def _font_group(app) -> Adw.PreferencesGroup:
    group = Adw.PreferencesGroup(
        title="Editor font",
        description="Monospaced families only, which is what the editor is laid "
        "out for.",
    )

    dialog = Gtk.FontDialog()
    dialog.set_filter(Gtk.CustomFilter.new(_is_monospace))

    button = Gtk.FontDialogButton(dialog=dialog, level=Gtk.FontLevel.FONT)
    button.set_font_desc(
        Pango.FontDescription.from_string(
            f"{app.config.font_family} {app.config.font_size}"
        )
    )
    button.set_valign(Gtk.Align.CENTER)
    button.connect(
        "notify::font-desc", lambda widget, _param: _font_chosen(app, widget)
    )

    row = Adw.ActionRow(title="Font")
    row.add_suffix(button)
    row.set_activatable_widget(button)
    group.add(row)
    return group


def _font_chosen(app, button: Gtk.FontDialogButton) -> None:
    desc = button.get_font_desc()
    if desc is None:
        return
    family = desc.get_family() or app.config.font_family
    size = desc.get_size() // Pango.SCALE
    app.set_font(family, size or app.config.font_size)


def _is_monospace(item) -> bool:
    family = item.get_family() if isinstance(item, Pango.FontFace) else item
    return family.is_monospace()
