"""Searching across every note at once, rather than the one in front of you."""

from collections.abc import Callable

import gi

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")
gi.require_version("GtkSource", "5")

from gi.repository import Adw, Gdk, GLib, Gtk, GtkSource

from ..storage import search
from ..storage.search import Result

DIALOG_WIDTH = 900
DIALOG_HEIGHT = 620

# Wide enough for a note name and the line the hit is on.
LIST_WIDTH = 340

# Long enough that typing a word does not search on every letter of it, short
# enough that the results feel like they are keeping up.
SEARCH_DELAY_MS = 250

# How many idle turns to wait for the preview to be given a size before giving
# up on scrolling it to the match.
SCROLL_ATTEMPTS = 10

UNTITLED = "Untitled note"


def present(
    parent: Gtk.Widget,
    scheme: GtkSource.StyleScheme | None,
    on_open: Callable[[Result, int, str], None],
) -> None:
    SearchDialog(scheme, on_open).present(parent)


class SearchDialog(Adw.Dialog):
    """Results on the left, the selected note with its matches on the right."""

    def __init__(
        self,
        scheme: GtkSource.StyleScheme | None,
        on_open: Callable[[Result, int, str], None],
    ) -> None:
        super().__init__(title="Search Notes")
        self.set_content_width(DIALOG_WIDTH)
        self.set_content_height(DIALOG_HEIGHT)

        self._on_open = on_open
        self._timer = 0

        self.search = Gtk.SearchEntry(placeholder_text="Search all notes", hexpand=True)
        self.search.connect("search-changed", self._on_search_changed)
        self.search.connect("activate", lambda *_: self.open_selected())

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_search_key)
        self.search.add_controller(keys)

        self.case_button = Gtk.ToggleButton(label="Aa")
        self.case_button.set_tooltip_text("Match case")
        self.case_button.connect("toggled", lambda *_: self.refresh())

        self.archive_check = Gtk.CheckButton(label="Include closed notes")
        self.archive_check.set_tooltip_text(
            "Search notes from tabs you have closed as well as the ones open now"
        )
        self.archive_check.connect("toggled", lambda *_: self.refresh())

        self.summary = Gtk.Label(xalign=0)
        self.summary.add_css_class("dim-label")

        query_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        query_row.append(self.search)
        query_row.append(self.case_button)

        self.results = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.results.add_css_class("navigation-sidebar")
        self.results.connect("row-selected", self._on_row_selected)

        side = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        side.set_size_request(LIST_WIDTH, -1)
        side.set_margin_top(6)
        side.set_margin_start(6)
        side.set_margin_end(6)
        side.append(query_row)
        side.append(self.archive_check)
        side.append(self.summary)
        side.append(
            Gtk.ScrolledWindow(
                child=self.results,
                hscrollbar_policy=Gtk.PolicyType.NEVER,
                vexpand=True,
            )
        )

        # A source view rather than a plain one, so the matches are highlighted
        # by the same machinery and in the same colours as the find bar uses.
        self.preview = GtkSource.View(editable=False, monospace=True)
        self.preview.set_cursor_visible(False)
        self.preview.set_show_line_numbers(True)
        # Wrapped the same way the editor wraps, so a long line puts the match
        # further down the preview rather than off the side of it.
        self.preview.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.preview.set_left_margin(8)
        self.preview.set_right_margin(8)
        self.preview.set_top_margin(8)
        self.preview.get_buffer().set_style_scheme(scheme)

        self._settings = GtkSource.SearchSettings(
            wrap_around=False, case_sensitive=False, regex_enabled=False
        )
        self._context = GtkSource.SearchContext(
            buffer=self.preview.get_buffer(), settings=self._settings
        )
        self._context.set_highlight(True)

        self.placeholder = Adw.StatusPage(
            icon_name="system-search-symbolic",
            title="Search your notes",
            description="Every note you have open is searched at once, files "
            "opened from disk included, and closed notes as well if you ask "
            "for them.",
        )

        self.detail = Gtk.Stack()
        self.detail.add_named(
            Gtk.ScrolledWindow(child=self.preview, vexpand=True, hexpand=True),
            "preview",
        )
        self.detail.add_named(self.placeholder, "empty")
        self.detail.set_visible_child_name("empty")

        content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        content.append(side)
        content.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))
        content.append(self.detail)

        self.open_button = Gtk.Button(label="Open")
        self.open_button.add_css_class("suggested-action")
        self.open_button.set_sensitive(False)
        self.open_button.connect("clicked", lambda *_: self.open_selected())

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        actions.set_margin_top(8)
        actions.set_margin_bottom(8)
        actions.set_margin_start(12)
        actions.set_margin_end(12)
        actions.append(Gtk.Box(hexpand=True))
        actions.append(self.open_button)

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        body.append(content)
        body.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))
        body.append(actions)

        view = Adw.ToolbarView(content=body)
        view.add_top_bar(Adw.HeaderBar())
        self.set_child(view)
        self.set_default_widget(self.open_button)
        self.set_focus(self.search)

        # A search still queued when the dialog goes away has nothing left to
        # put its results into.
        self.connect("closed", lambda *_: self._cancel_pending())

    @property
    def query(self) -> str:
        return self.search.get_text().strip()

    def refresh(self) -> None:
        """Run the search now, without waiting for the debounce."""
        self._cancel_pending()
        results = search.search(
            self.query,
            include_archive=self.archive_check.get_active(),
            case_sensitive=self.case_button.get_active(),
        )
        self._show(results)

    def open_selected(self) -> None:
        row = self.results.get_selected_row()
        if row is None:
            return

        self.close()
        self._on_open(row.result, row.result.matches[0].offset, self.query)

    def _show(self, results: list[Result]) -> None:
        self.results.remove_all()
        for result in results:
            self.results.append(_ResultRow(result))

        self.summary.set_text(_summarise(self.query, results))
        first = self.results.get_row_at_index(0)
        if first is not None:
            self.results.select_row(first)
        else:
            self.detail.set_visible_child_name("empty")
            self.open_button.set_sensitive(False)

    def _on_row_selected(
        self, listbox: Gtk.ListBox, row: Gtk.ListBoxRow | None
    ) -> None:
        if row is None:
            self.detail.set_visible_child_name("empty")
            self.open_button.set_sensitive(False)
            return

        text = search.read(row.result)
        if text is None:
            # Removed while the results were on screen.
            self.refresh()
            return

        self.detail.set_visible_child_name("preview")
        self.open_button.set_sensitive(True)

        buffer = self.preview.get_buffer()
        buffer.set_text(text)
        self._settings.set_case_sensitive(self.case_button.get_active())
        self._settings.set_search_text(self.query or None)

        # Selecting the match rather than only scrolling to it, so the eye lands
        # on the same thing the highlighting is pointing at.
        match = row.result.matches[0]
        start = buffer.get_iter_at_offset(match.offset)
        end = buffer.get_iter_at_offset(match.offset + len(self.query))
        buffer.select_range(start, end)
        GLib.idle_add(self._scroll_to_match, 0)

    def _scroll_to_match(self, attempt: int) -> bool:
        """Bring the match into view, once there is a view to bring it into."""
        buffer = self.preview.get_buffer()
        if self.preview.get_height() <= 0 and attempt < SCROLL_ATTEMPTS:
            GLib.idle_add(self._scroll_to_match, attempt + 1)
        else:
            self.preview.scroll_to_mark(buffer.get_insert(), 0.15, True, 0.0, 0.3)
        return GLib.SOURCE_REMOVE

    def _on_search_changed(self, entry: Gtk.SearchEntry) -> None:
        # Every keystroke pushes the search out, so typing a word reads the
        # notes once rather than once per letter.
        self._cancel_pending()
        self._timer = GLib.timeout_add(SEARCH_DELAY_MS, self._on_timeout)

    def _on_timeout(self) -> bool:
        self._timer = 0
        self.refresh()
        return GLib.SOURCE_REMOVE

    def _cancel_pending(self) -> None:
        if self._timer:
            GLib.source_remove(self._timer)
            self._timer = 0

    def _on_search_key(
        self, controller: Gtk.EventControllerKey, keyval: int, keycode: int, state
    ) -> bool:
        return _step_selection(self.results, keyval)


class _ResultRow(Adw.ActionRow):
    def __init__(self, result: Result) -> None:
        super().__init__(
            title=GLib.markup_escape_text(result.title or UNTITLED),
            subtitle=GLib.markup_escape_text(result.matches[0].text),
        )
        self.result = result

        # Opening a result behaves differently depending on what it is, so the
        # row says which it is rather than leaving it to the name.
        if result.archived:
            self.add_suffix(_tag("Closed"))
        elif result.path is not None:
            self.add_suffix(_tag("File"))
            # Two files can share a name, and only the path tells them apart.
            self.set_tooltip_text(result.path)

        count = Gtk.Label(label=str(len(result.matches)))
        count.add_css_class("dim-label")
        count.set_tooltip_text(_matches_phrase(len(result.matches)))
        self.add_suffix(count)

        # Selecting a row previews it. Opening the note is the button's job.
        self.set_activatable(False)


def _tag(text: str) -> Gtk.Label:
    label = Gtk.Label(label=text)
    label.add_css_class("dim-label")
    return label


def _step_selection(listbox: Gtk.ListBox, keyval: int) -> bool:
    """Move the selection with the arrow keys while the search box has focus."""
    if keyval not in (Gdk.KEY_Up, Gdk.KEY_Down):
        return Gdk.EVENT_PROPAGATE

    selected = listbox.get_selected_row()
    index = selected.get_index() if selected is not None else -1
    step = 1 if keyval == Gdk.KEY_Down else -1

    row = listbox.get_row_at_index(max(0, index + step))
    if row is not None:
        listbox.select_row(row)
    return Gdk.EVENT_STOP


def _matches_phrase(count: int) -> str:
    return "1 match" if count == 1 else f"{count} matches"


def _summarise(query: str, results: list[Result]) -> str:
    if not query:
        return ""
    if not results:
        return "No matches"

    total = sum(len(result.matches) for result in results)
    notes = "1 note" if len(results) == 1 else f"{len(results)} notes"
    return f"{_matches_phrase(total)} in {notes}"
