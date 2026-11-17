"""The keyboard shortcuts window."""

import gi

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")

from gi.repository import Adw, Gtk

# Kept in the order they are likely to be looked for rather than alphabetically.
SHORTCUTS = (
    (
        "Notes",
        (
            ("New note", "<Control>n"),
            ("Open file", "<Control>o"),
            ("Recover a closed note", "<Control><Shift>t"),
            ("Save", "<Control>s"),
            ("Save as", "<Control><Shift>s"),
            ("Rename note", "F2"),
            ("Close tab", "<Control>w"),
        ),
    ),
    (
        "Editing",
        (
            ("Find", "<Control>f"),
            ("Find and replace", "<Control>h"),
            ("Search all notes", "<Control><Shift>f"),
        ),
    ),
    (
        "Tabs",
        (
            ("Next tab", "<Control>Tab"),
            ("Previous tab", "<Control><Shift>Tab"),
            ("Jump to tab", "<Alt>1...<Alt>9"),
        ),
    ),
    (
        "Panes",
        (
            ("Split the window", "<Control>backslash"),
            ("Move note to the other pane", "<Control><Shift>m"),
        ),
    ),
    (
        "Application",
        (
            ("Switch dark and light", "<Control>d"),
            ("Preferences", "<Control>comma"),
            ("Quit", "<Control>q"),
        ),
    ),
)


def present(parent: Gtk.Widget) -> None:
    if not hasattr(Adw, "ShortcutsDialog"):
        _present_as_list(parent)
        return

    dialog = Adw.ShortcutsDialog()
    for title, items in SHORTCUTS:
        section = Adw.ShortcutsSection(title=title)
        for name, accelerator in items:
            section.add(Adw.ShortcutsItem(title=name, accelerator=accelerator))
        dialog.add(section)

    dialog.present(parent)


# Below here is the same list for libadwaita older than 1.8, which has no
# ShortcutsDialog. It is built from the table above rather than from a copy of
# it, so the two cannot come to disagree about what a key does.


def _present_as_list(parent: Gtk.Widget) -> None:
    page = Adw.PreferencesPage()
    for title, items in SHORTCUTS:
        group = Adw.PreferencesGroup(title=title)
        for name, accelerator in items:
            group.add(_shortcut_row(name, accelerator))
        page.add(group)

    dialog = Adw.PreferencesDialog(title="Keyboard Shortcuts")
    dialog.add(page)
    dialog.present(parent)


def _shortcut_row(name: str, accelerator: str) -> Adw.ActionRow:
    row = Adw.ActionRow(title=name)
    label = Gtk.Label(label=_accelerator_label(accelerator))
    label.add_css_class("dim-label")
    row.add_suffix(label)
    return row


def _accelerator_label(accelerator: str) -> str:
    """Ctrl+D, and both ends of a range like <Alt>1...<Alt>9."""
    labels = []
    for part in accelerator.split("..."):
        ok, keyval, mods = Gtk.accelerator_parse(part)
        if not ok:
            return accelerator
        labels.append(Gtk.accelerator_get_label(keyval, mods))
    return "...".join(labels)
