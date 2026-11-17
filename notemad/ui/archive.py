"""The recovery dialog: what you have closed, and a way back to it."""

import math
import time
from collections.abc import Callable

import gi

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")

from gi.repository import Adw, Gdk, GLib, Gtk, Pango

from ..sizes import human_size
from ..storage import archive
from ..storage.archive import ArchiveEntry
from ..storage.session import KIND_FILE

DIALOG_WIDTH = 820
DIALOG_HEIGHT = 620

# Narrow: two choices and a sentence about what they would do.
CLEANUP_WIDTH = 460

# Wide enough for a title and a date without crowding the preview beside it.
LIST_WIDTH = 300

# A preview is there to tell one note from another, not to read the whole thing
# in. Anything past this is somebody's log file.
PREVIEW_CHARS = 20000

UNTITLED = "Untitled note"

# How the list can be ordered. Biggest first is what you want when the point of
# opening this was to get the archive back down.
SORT_NEWEST = "Newest first"
SORT_BIGGEST = "Biggest first"

# The ages offered when clearing out old notes, and the seconds in each. A month
# is taken as thirty days, which is close enough for a cutoff nobody is
# measuring to the day.
DAY = 24 * 60 * 60
AGE_CHOICES = (
    ("1 month", 30 * DAY),
    ("3 months", 90 * DAY),
    ("6 months", 180 * DAY),
    ("1 year", 365 * DAY),
    ("2 years", 730 * DAY),
)

RULE_CLOSED = "Closed longer ago than"
RULE_UNSEEN = "Not looked at for longer than"

SELECT_ALL = "Select All"
SELECT_NONE = "Select None"

# The year 9999, past which there is no date to format and nothing sensible to
# have meant by it.
MAX_TIMESTAMP = 253402300800


def present(
    parent: Gtk.Widget,
    on_recover: Callable[[ArchiveEntry, str], None],
    warn_at: int = 0,
    on_changed: Callable[[], None] | None = None,
) -> None:
    ArchiveDialog(on_recover, warn_at, on_changed).present(parent)


class ArchiveDialog(Adw.Dialog):
    """A list of closed notes on the left, whichever one is selected on the right."""

    def __init__(
        self,
        on_recover: Callable[[ArchiveEntry, str], None],
        warn_at: int = 0,
        on_changed: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(title="Recover Note")
        self.set_content_width(DIALOG_WIDTH)
        self.set_content_height(DIALOG_HEIGHT)

        self._on_recover = on_recover
        self._on_changed = on_changed
        self._warn_at = warn_at
        self._query = ""
        self._entries: list[ArchiveEntry] = []

        # What the whole archive comes to, above the list rather than beside it,
        # since it describes all of it rather than any one note.
        self.summary = Gtk.Label(xalign=0, wrap=True)
        self.summary.add_css_class("heading")
        self.summary.set_margin_start(12)
        self.summary.set_margin_end(12)
        self.summary.set_margin_top(8)

        self.advice = Gtk.Label(xalign=0, wrap=True)
        self.advice.add_css_class("dim-label")
        self.advice.set_margin_start(12)
        self.advice.set_margin_end(12)
        self.advice.set_margin_bottom(8)

        self.search = Gtk.SearchEntry(placeholder_text="Search closed notes")
        self.search.connect("search-changed", self._on_search_changed)
        self.search.connect("activate", lambda *_: self.recover())

        self.sort = Gtk.DropDown.new_from_strings([SORT_NEWEST, SORT_BIGGEST])
        self.sort.set_hexpand(True)
        self.sort.set_tooltip_text("What order to list the notes in")
        self.sort.connect("notify::selected", lambda *_: self.reload())

        self.select_button = Gtk.Button(label=SELECT_ALL)
        self.select_button.set_tooltip_text(
            "Ctrl+click picks out several notes, shift+click a run of them"
        )
        self.select_button.connect("clicked", lambda *_: self._toggle_select_all())

        listing = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        # Expansion travels up through parents until something refuses it, and
        # left alone this column would take half the dialog off the preview.
        listing.set_hexpand(False)
        listing.append(self.sort)
        listing.append(self.select_button)

        # The search box has the focus when the dialog opens, so the arrow keys
        # have to reach the list from there or the whole thing needs a mouse.
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_search_key)
        self.search.add_controller(keys)

        # Several at once, so clearing out a handful of notes is one decision
        # rather than one per note.
        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.MULTIPLE)
        # A list that activates on a single click takes every click as "add this
        # one" and never looks at the modifiers, so ctrl+click could not let a
        # note go and a plain click piled up rather than starting again.
        self.list.set_activate_on_single_click(False)
        self.list.add_css_class("navigation-sidebar")
        self.list.set_filter_func(self._matches)
        self.list.connect("selected-rows-changed", self._on_selection_changed)

        side = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        side.set_size_request(LIST_WIDTH, -1)
        side.set_margin_top(6)
        side.set_margin_start(6)
        side.set_margin_end(6)
        side.append(self.search)
        side.append(listing)
        side.append(
            Gtk.ScrolledWindow(
                child=self.list,
                hscrollbar_policy=Gtk.PolicyType.NEVER,
                vexpand=True,
            )
        )

        self.preview = Gtk.TextView(
            editable=False, monospace=True, cursor_visible=False
        )
        self.preview.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.preview.set_left_margin(12)
        self.preview.set_right_margin(12)
        self.preview.set_top_margin(8)
        self.preview.set_bottom_margin(8)

        # A deep path would otherwise wrap this line across half the preview.
        # The middle of one is the part you can afford to lose.
        self.origin = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.MIDDLE)
        self.origin.add_css_class("dim-label")
        self.origin.set_margin_start(12)
        self.origin.set_margin_end(12)
        self.origin.set_margin_top(8)

        # Shown in place of the whole right hand side while there is nothing to
        # look at, so an empty archive does not read as a broken one.
        self.placeholder = Adw.StatusPage(
            icon_name="document-open-recent-symbolic",
            title="Nothing closed yet",
            description="Notes from tabs you close are kept here so an accidental "
            "close is something you can come back from.",
        )

        detail = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        detail.append(self.origin)
        detail.append(
            Gtk.ScrolledWindow(child=self.preview, vexpand=True, hexpand=True)
        )

        # There is no one note to preview once several are picked out, so the
        # right hand side says what has been picked instead.
        self.chosen = Adw.StatusPage(icon_name="edit-select-all-symbolic")

        self.detail = Gtk.Stack()
        self.detail.add_named(detail, "preview")
        self.detail.add_named(self.placeholder, "empty")
        self.detail.add_named(self.chosen, "chosen")

        content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        content.append(side)
        content.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))
        content.append(self.detail)

        self.delete_button = Gtk.Button(label="Delete")
        self.delete_button.add_css_class("destructive-action")
        self.delete_button.connect("clicked", lambda *_: self._confirm_delete())

        self.cleanup_button = Gtk.Button(label="Clear Out Old Notes")
        self.cleanup_button.connect("clicked", lambda *_: self._open_cleanup())

        self.recover_button = Gtk.Button(label="Recover")
        self.recover_button.add_css_class("suggested-action")
        self.recover_button.connect("clicked", lambda *_: self.recover())

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        actions.set_margin_top(8)
        actions.set_margin_bottom(8)
        actions.set_margin_start(12)
        actions.set_margin_end(12)
        actions.append(self.delete_button)
        actions.append(self.cleanup_button)
        actions.append(Gtk.Box(hexpand=True))
        actions.append(self.recover_button)

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        body.append(self.summary)
        body.append(self.advice)
        body.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))
        body.append(content)
        body.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))
        body.append(actions)

        view = Adw.ToolbarView(content=body)
        view.add_top_bar(Adw.HeaderBar())
        self.set_child(view)
        self.set_default_widget(self.recover_button)
        self.set_focus(self.search)

        self.reload()

    def reload(self) -> None:
        """Rebuild the list from what is on disk."""
        self._entries = archive.entries()
        if self.sort.get_selected() == 1:
            self._entries.sort(key=lambda entry: entry.size, reverse=True)

        self.list.remove_all()
        for entry in self._entries:
            self.list.append(_EntryRow(entry))

        self._update_summary()
        self.cleanup_button.set_sensitive(bool(self._entries))

        first = self.list.get_row_at_index(0)
        if first is not None:
            self.list.select_row(first)
        else:
            self._show_nothing()
        self._update_select_button()

        if self._on_changed is not None:
            self._on_changed()

    def _update_summary(self) -> None:
        """The two lines above the list: what is in here, and what to do about it."""
        total = sum(entry.size for entry in self._entries)
        count = len(self._entries)
        notes = "note" if count == 1 else "notes"
        self.summary.set_text(f"{count} closed {notes}, {human_size(total)}")

        if not count:
            self.advice.set_text(
                "Nothing has been closed yet, so there is nothing taking up room."
            )
        elif self._warn_at and total >= self._warn_at:
            self.advice.set_text(
                f"Past the {human_size(self._warn_at)} you asked to be warned at. "
                "List them biggest first and delete what you no longer want, or "
                "clear out everything older than a date you pick."
            )
        else:
            self.advice.set_text(
                "Notes from tabs you close are kept here. Nothing is ever removed "
                "on its own, so this only grows until you clear some of it."
            )

    def recover(self) -> None:
        """Open everything selected, each as a note of its own."""
        entries = self._selected()
        if not entries:
            return

        found = [(entry, archive.read(entry.id)) for entry in entries]
        if any(text is None for _, text in found):
            # Removed from underneath us, which the list is now wrong about.
            self.reload()
            return

        self.close()
        for entry, text in found:
            self._on_recover(entry, text)

    def _selected(self) -> list[ArchiveEntry]:
        return [row.entry for row in self.list.get_selected_rows()]

    def _show_nothing(self) -> None:
        self.detail.set_visible_child_name("empty")
        self.delete_button.set_sensitive(False)
        self.recover_button.set_sensitive(False)
        self.recover_button.set_label("Recover")

    def _on_selection_changed(self, listbox: Gtk.ListBox) -> None:
        entries = self._selected()
        self._update_select_button()

        if not entries:
            self._show_nothing()
            return

        self.delete_button.set_sensitive(True)
        self.recover_button.set_sensitive(True)
        # Named with a count, so recovering six notes at once is never a
        # surprise arriving as six tabs.
        self.recover_button.set_label(
            "Recover" if len(entries) == 1 else f"Recover {len(entries)} Notes"
        )

        if len(entries) > 1:
            self._show_chosen(entries)
            return

        self.detail.set_visible_child_name("preview")
        entry = entries[0]
        origin = _origin_of(entry)
        self.origin.set_text(origin)
        # The label is ellipsized, so the whole path has to be readable somehow.
        self.origin.set_tooltip_text(origin)

        text = archive.read(entry.id) or ""
        self.preview.get_buffer().set_text(text[:PREVIEW_CHARS])

    def _show_chosen(self, entries: list[ArchiveEntry]) -> None:
        total = sum(entry.size for entry in entries)
        self.chosen.set_title(f"{len(entries)} notes selected")
        self.chosen.set_description(
            f"{human_size(total)} between them. Delete removes all of them at "
            "once, and Recover opens each as a note of its own."
        )
        self.detail.set_visible_child_name("chosen")

    def _toggle_select_all(self) -> None:
        """Take everything the search is showing, or let it all go again.

        The list's own select-all pays no attention to the filter, which would
        quietly hand back notes that were searched out of the way.
        """
        visible = [row for row in self._rows() if self._matches(row)]
        if self._all_visible_selected(visible):
            self.list.unselect_all()
            return

        for row in visible:
            self.list.select_row(row)

    def _all_visible_selected(self, visible: list[Gtk.ListBoxRow]) -> bool:
        selected = set(self.list.get_selected_rows())
        return bool(visible) and all(row in selected for row in visible)

    def _update_select_button(self) -> None:
        visible = [row for row in self._rows() if self._matches(row)]
        self.select_button.set_sensitive(bool(visible))
        self.select_button.set_label(
            SELECT_NONE if self._all_visible_selected(visible) else SELECT_ALL
        )

    def _confirm_delete(self) -> None:
        entries = self._selected()
        if not entries:
            return

        if len(entries) == 1:
            heading = f"Delete {entries[0].title or UNTITLED}?"
            body = (
                "This removes it from the archive for good. There is nowhere "
                "further back."
            )
        else:
            total = sum(entry.size for entry in entries)
            heading = f"Delete {len(entries)} notes?"
            body = (
                f"This removes them from the archive for good, freeing "
                f"{human_size(total)}. There is nowhere further back."
            )

        dialog = Adw.AlertDialog(heading=heading, body=body)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("delete", "Delete")
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect(
            "response", self._on_delete_response, [entry.id for entry in entries]
        )
        dialog.present(self)

    def _on_delete_response(
        self, dialog: Adw.AlertDialog, response: str, note_ids: list[str]
    ) -> None:
        if response != "delete":
            return
        archive.delete_many(note_ids)
        self.reload()

    def _open_cleanup(self) -> None:
        CleanupDialog(self._entries, self.reload).present(self)

    def _matches(self, row: Gtk.ListBoxRow) -> bool:
        return self._query in row.haystack

    def _on_search_changed(self, entry: Gtk.SearchEntry) -> None:
        self._query = entry.get_text().strip().lower()
        self.list.invalidate_filter()

        # A search that hides a selected row would otherwise leave the right hand
        # side describing notes that are no longer in the list.
        hidden = [
            row for row in self.list.get_selected_rows() if not self._matches(row)
        ]
        for row in hidden:
            self.list.unselect_row(row)

        if not self.list.get_selected_rows():
            self._select_first_visible()
        self._update_select_button()

    def _select_first_visible(self) -> None:
        self.list.unselect_all()
        for row in self._rows():
            if self._matches(row):
                self.list.select_row(row)
                return
        self._show_nothing()

    def _on_search_key(
        self, controller: Gtk.EventControllerKey, keyval: int, keycode: int, state
    ) -> bool:
        if keyval not in (Gdk.KEY_Up, Gdk.KEY_Down):
            return Gdk.EVENT_PROPAGATE

        visible = [row for row in self._rows() if self._matches(row)]
        if not visible:
            return Gdk.EVENT_STOP

        # Stepping through the list from the search box moves to one note rather
        # than adding to whatever is already picked out.
        selected = self.list.get_selected_rows()
        current = selected[-1] if selected else None
        step = 1 if keyval == Gdk.KEY_Down else -1
        position = visible.index(current) + step if current in visible else 0

        self.list.unselect_all()
        self.list.select_row(visible[max(0, min(position, len(visible) - 1))])
        return Gdk.EVENT_STOP

    def _rows(self) -> list[Gtk.ListBoxRow]:
        rows = []
        child = self.list.get_first_child()
        while child is not None:
            rows.append(child)
            child = child.get_next_sibling()
        return rows


class CleanupDialog(Adw.Dialog):
    """Clearing out old notes in one go, having said first what will go.

    Nothing here decides anything on its own. It picks a set, says how many and
    how much, and waits to be told twice.
    """

    def __init__(
        self, entries: list[ArchiveEntry], on_done: Callable[[], None]
    ) -> None:
        super().__init__(title="Clear Out Old Notes")
        self.set_content_width(CLEANUP_WIDTH)

        self._entries = entries
        self._on_done = on_done
        self._doomed: list[ArchiveEntry] = []

        self.rule = Adw.ComboRow(
            title="Remove notes",
            model=Gtk.StringList.new([RULE_CLOSED, RULE_UNSEEN]),
        )
        self.rule.connect("notify::selected", lambda *_: self._recount())

        self.age = Adw.ComboRow(
            title="Older than",
            model=Gtk.StringList.new([label for label, _ in AGE_CHOICES]),
        )
        # Six months: long enough that nothing you are still thinking about is
        # caught by it.
        self.age.set_selected(2)
        self.age.connect("notify::selected", lambda *_: self._recount())

        group = Adw.PreferencesGroup()
        group.add(self.rule)
        group.add(self.age)

        self.outcome = Gtk.Label(xalign=0, wrap=True)
        self.outcome.add_css_class("heading")

        self.note = Gtk.Label(xalign=0, wrap=True)
        self.note.add_css_class("dim-label")

        self.remove_button = Gtk.Button(label="Remove")
        self.remove_button.add_css_class("destructive-action")
        self.remove_button.connect("clicked", lambda *_: self._confirm())

        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda *_: self.close())

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        actions.set_margin_top(12)
        actions.append(cancel)
        actions.append(Gtk.Box(hexpand=True))
        actions.append(self.remove_button)

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        body.set_margin_top(12)
        body.set_margin_bottom(12)
        body.set_margin_start(12)
        body.set_margin_end(12)
        body.append(group)
        body.append(self.outcome)
        body.append(self.note)
        body.append(actions)

        view = Adw.ToolbarView(content=body)
        view.add_top_bar(Adw.HeaderBar())
        self.set_child(view)

        self._recount()

    def _recount(self) -> None:
        """Work out what the current choice would remove, and say so."""
        by_last_viewed = self.rule.get_selected() == 1
        _, seconds = AGE_CHOICES[self.age.get_selected()]
        cutoff = time.time() - seconds

        self._doomed = archive.stale(self._entries, cutoff, by_last_viewed)
        going = sum(entry.size for entry in self._doomed)
        staying = sum(entry.size for entry in self._entries) - going
        notes = "note" if len(self._doomed) == 1 else "notes"

        self.remove_button.set_sensitive(bool(self._doomed))
        if not self._doomed:
            self.outcome.set_text("Nothing in the archive is that old.")
        else:
            self.outcome.set_text(
                f"Removes {len(self._doomed)} of {len(self._entries)} {notes}, "
                f"recovering {human_size(going)}. "
                f"The archive would be left at {human_size(staying)}."
            )

        if by_last_viewed:
            self.note.set_text(
                "Notemad has only recently started recording when a note was "
                "last looked at. Anything closed before that is judged on the "
                "date it was closed instead, which is the last moment it can "
                "have been in front of you."
            )
        else:
            self.note.set_text(
                "Judged on when each note was closed, whatever was done with it "
                "before that."
            )

    def _confirm(self) -> None:
        going = sum(entry.size for entry in self._doomed)
        notes = "note" if len(self._doomed) == 1 else "notes"

        dialog = Adw.AlertDialog(
            heading=f"Remove {len(self._doomed)} {notes}?",
            body=f"This frees {human_size(going)} and cannot be undone. The "
            "notes go for good, not to the desktop wastebasket.",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("remove", "Remove")
        dialog.set_response_appearance("remove", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", self._on_confirmed)
        dialog.present(self)

    def _on_confirmed(self, dialog: Adw.AlertDialog, response: str) -> None:
        if response != "remove":
            return
        archive.delete_many([entry.id for entry in self._doomed])
        self.close()
        self._on_done()


class _EntryRow(Adw.ActionRow):
    def __init__(self, entry: ArchiveEntry) -> None:
        super().__init__(
            title=GLib.markup_escape_text(entry.title or UNTITLED),
            subtitle=f"{_when(entry.archived_at)}  ·  {human_size(entry.size)}",
        )
        self.entry = entry
        self.haystack = f"{entry.title} {entry.path or ''}".lower()
        # Selecting a row shows it in the preview. Recovering it is a separate
        # decision, so a single click is not allowed to be one.
        self.set_activatable(False)


def _origin_of(entry: ArchiveEntry) -> str:
    """The line above the preview saying what this was before it was closed."""
    when = _when(entry.archived_at)
    if entry.kind == KIND_FILE and entry.path:
        return f"Unsaved changes to {entry.path}, closed {when.lower()}"
    return f"Closed {when.lower()}"


def _when(timestamp: float) -> str:
    """A date a person would say out loud rather than a full timestamp."""
    # A time from a damaged index can be anything at all, including one too far
    # out for a date to be made of it, and a list of closed notes is not worth
    # losing over the wrong label on one of them.
    if not math.isfinite(timestamp) or not 0 < timestamp < MAX_TIMESTAMP:
        return "Date unknown"

    moment = GLib.DateTime.new_from_unix_local(int(timestamp))
    today = GLib.DateTime.new_now_local()
    if moment is None:
        return "Date unknown"
    days = today.get_day_of_year() - moment.get_day_of_year()

    if moment.get_year() == today.get_year() and days == 0:
        return moment.format("Today at %H:%M")
    if moment.get_year() == today.get_year() and days == 1:
        return moment.format("Yesterday at %H:%M")
    if moment.get_year() == today.get_year():
        return moment.format("%-d %B at %H:%M")
    return moment.format("%-d %B %Y at %H:%M")
