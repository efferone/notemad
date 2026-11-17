"""The find and replace bar that slides in above the text."""

from collections.abc import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("GtkSource", "5")

from gi.repository import GLib, Gtk, GtkSource


class FindBar(Gtk.Revealer):
    """Search within one buffer at a time, following whichever tab is in front.

    The search itself is GtkSourceView's: this puts a face on
    `GtkSource.SearchContext` and keeps it pointed at the right buffer.
    """

    def __init__(self, announce: Callable[[str], None] | None = None) -> None:
        super().__init__(reveal_child=False)

        # How the bar says something that outlives the match counter, which is
        # rewritten every time the search changes.
        self._announce = announce
        self._context: GtkSource.SearchContext | None = None
        self._view: GtkSource.View | None = None
        self._settings = GtkSource.SearchSettings(
            wrap_around=True,
            case_sensitive=False,
            regex_enabled=False,
        )

        self.search_entry = Gtk.SearchEntry(hexpand=True)
        self.search_entry.set_placeholder_text("Find")
        self.search_entry.connect("search-changed", self._on_search_changed)
        self.search_entry.connect("activate", lambda entry: self.find_next())
        self.search_entry.connect("stop-search", lambda entry: self.close())

        self.matches_label = Gtk.Label(label="")
        self.matches_label.add_css_class("dim-label")

        previous_button = Gtk.Button(icon_name="go-up-symbolic")
        previous_button.set_tooltip_text("Previous match")
        previous_button.connect("clicked", lambda button: self.find_previous())

        next_button = Gtk.Button(icon_name="go-down-symbolic")
        next_button.set_tooltip_text("Next match")
        next_button.connect("clicked", lambda button: self.find_next())

        self.case_button = Gtk.ToggleButton(label="Aa")
        self.case_button.set_tooltip_text("Match case")
        self.case_button.connect("toggled", self._on_options_changed)

        self.regex_button = Gtk.ToggleButton(label=".*")
        self.regex_button.set_tooltip_text("Regular expression")
        self.regex_button.connect("toggled", self._on_options_changed)

        close_button = Gtk.Button(icon_name="window-close-symbolic")
        close_button.set_tooltip_text("Close the find bar")
        close_button.connect("clicked", lambda button: self.close())

        find_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        find_row.append(self.search_entry)
        find_row.append(self.matches_label)
        find_row.append(previous_button)
        find_row.append(next_button)
        find_row.append(self.case_button)
        find_row.append(self.regex_button)
        find_row.append(close_button)

        self.replace_entry = Gtk.Entry(hexpand=True)
        self.replace_entry.set_placeholder_text("Replace with")
        self.replace_entry.connect("activate", lambda entry: self.replace())

        replace_button = Gtk.Button(label="Replace")
        replace_button.connect("clicked", lambda button: self.replace())

        replace_all_button = Gtk.Button(label="Replace All")
        replace_all_button.connect("clicked", lambda button: self.replace_all())

        replace_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        replace_row.append(self.replace_entry)
        replace_row.append(replace_button)
        replace_row.append(replace_all_button)

        self.replace_row = Gtk.Revealer(child=replace_row, reveal_child=False)

        rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        rows.set_margin_top(6)
        rows.set_margin_bottom(6)
        rows.set_margin_start(12)
        rows.set_margin_end(12)
        rows.append(find_row)
        rows.append(self.replace_row)
        rows.add_css_class("findbar")

        self.set_child(rows)

        escape = Gtk.ShortcutController()
        escape.add_shortcut(
            Gtk.Shortcut(
                trigger=Gtk.ShortcutTrigger.parse_string("Escape"),
                action=Gtk.CallbackAction.new(lambda *args: self.close()),
            )
        )
        self.add_controller(escape)

    def attach(self, view: GtkSource.View | None) -> None:
        """Point the bar at the buffer the user is now looking at."""
        if self._context is not None:
            self._context.set_highlight(False)

        self._view = view
        if view is None:
            self._context = None
            return

        self._context = GtkSource.SearchContext(
            buffer=view.get_buffer(), settings=self._settings
        )
        self._context.set_highlight(self.get_reveal_child())
        self._context.connect("notify::occurrences-count", self._on_occurrences)

    def open(self, replacing: bool = False) -> None:
        self.replace_row.set_reveal_child(replacing)
        self.set_reveal_child(True)
        if self._context is not None:
            self._context.set_highlight(True)

        # Searching for what is already selected is almost always what is wanted.
        selected = self._selected_text()
        if selected:
            self.search_entry.set_text(selected)

        self.search_entry.grab_focus()
        self.search_entry.select_region(0, -1)

    def close(self) -> bool:
        self.set_reveal_child(False)
        self.replace_row.set_reveal_child(False)
        if self._context is not None:
            self._context.set_highlight(False)
        if self._view is not None:
            self._view.grab_focus()
        return True

    def find_next(self) -> None:
        self._step(forward=True)

    def find_previous(self) -> None:
        self._step(forward=False)

    def replace(self) -> None:
        if self._context is None or not self._settings.get_search_text():
            return

        buffer = self._context.get_buffer()
        start, end = buffer.get_selection_bounds() or (None, None)
        replacement = self.replace_entry.get_text()

        # Replacing only makes sense on a match, so an idle selection searches
        # for one first and lands on it, ready for a second press.
        if start is None or not self._is_match(start, end):
            self.find_next()
            return

        try:
            self._context.replace(start, end, replacement, -1)
        except GLib.Error as error:
            self.matches_label.set_text(_regex_problem(error))
            return
        self.find_next()

    def replace_all(self) -> None:
        if self._context is None or not self._settings.get_search_text():
            return
        try:
            count = self._context.replace_all(self.replace_entry.get_text(), -1)
        except GLib.Error as error:
            self.matches_label.set_text(_regex_problem(error))
            return

        if self._announce is not None:
            noun = "match" if count == 1 else "matches"
            self._announce(f"Replaced {count} {noun}")

    def _step(self, forward: bool) -> None:
        if self._context is None:
            return

        buffer = self._context.get_buffer()
        cursor = buffer.get_iter_at_mark(buffer.get_insert())
        bounds = buffer.get_selection_bounds()
        if bounds:
            cursor = bounds[1] if forward else bounds[0]

        if forward:
            found, start, end, _ = self._context.forward(cursor)
        else:
            found, start, end, _ = self._context.backward(cursor)

        if not found:
            return

        buffer.select_range(start, end)
        if self._view is not None:
            self._view.scroll_to_iter(start, 0.15, False, 0.0, 0.5)
        self._update_position()

    def _is_match(self, start: Gtk.TextIter, end: Gtk.TextIter) -> bool:
        selected = self._context.get_buffer().get_text(start, end, False)
        if self._settings.get_regex_enabled():
            # A regex match cannot be compared by text, so trust the position.
            return True
        if self._settings.get_case_sensitive():
            return selected == self._settings.get_search_text()
        return selected.lower() == (self._settings.get_search_text() or "").lower()

    def _selected_text(self) -> str:
        if self._view is None:
            return ""
        bounds = self._view.get_buffer().get_selection_bounds()
        if not bounds:
            return ""
        start, end = bounds
        text = self._view.get_buffer().get_text(start, end, False)
        return text if "\n" not in text else ""

    def _on_search_changed(self, entry: Gtk.SearchEntry) -> None:
        self._settings.set_search_text(entry.get_text() or None)
        self._update_position()

    def _on_options_changed(self, button: Gtk.ToggleButton) -> None:
        self._settings.set_case_sensitive(self.case_button.get_active())
        self._settings.set_regex_enabled(self.regex_button.get_active())
        self._update_position()

    def _on_occurrences(self, context: GtkSource.SearchContext, param) -> None:
        self._update_position()

    def _update_position(self) -> None:
        if self._context is None or not self._settings.get_search_text():
            self.matches_label.set_text("")
            return

        error = self._context.get_regex_error()
        if error is not None:
            self.matches_label.set_text(_regex_problem(error))
            return

        total = self._context.get_occurrences_count()
        if total < 0:
            # Counting has not finished yet.
            return
        if total == 0:
            self.matches_label.set_text("No matches")
            return

        buffer = self._context.get_buffer()
        bounds = buffer.get_selection_bounds()
        position = -1
        if bounds:
            position = self._context.get_occurrence_position(*bounds)

        if position > 0:
            self.matches_label.set_text(f"{position} of {total}")
        else:
            self.matches_label.set_text(f"{total} matches")


def _regex_problem(error: GLib.Error) -> str:
    return "Bad expression" if error is not None else ""
