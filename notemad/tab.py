"""A single tab: its buffer, its view, and its storage identity."""

import logging
import time
from collections.abc import Callable
from pathlib import Path

import gi

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")
gi.require_version("GtkSource", "5")

from gi.repository import Adw, GLib, Gtk, GtkSource

from . import theme
from .storage import archive, notes
from .storage.session import KIND_FILE, KIND_SCRATCH, TabState

logger = logging.getLogger(__name__)

# Padding between the gutter and the text, and around the edges of the view.
# Text jammed against the line numbers reads like a terminal, not an editor.
TEXT_MARGIN = 12
EDGE_MARGIN = 8

# How long typing has to stop before the buffer is written out.
AUTOSAVE_DELAY_MS = 500

# Writing a note or a draft copies the whole buffer out every time, so a large
# one is written less often. Half a second on anything normal, stretching to ten
# seconds on something big enough that writing it twice a second would keep the
# disk busy for no good reason.
MAX_AUTOSAVE_DELAY_MS = 10000
LARGE_BUFFER_CHARS = 2 * 1024 * 1024


def autosave_delay(chars: int) -> int:
    """How long to wait before writing a buffer of this size out."""
    if chars <= LARGE_BUFFER_CHARS:
        return AUTOSAVE_DELAY_MS
    scaled = AUTOSAVE_DELAY_MS * (chars / LARGE_BUFFER_CHARS)
    return int(min(scaled, MAX_AUTOSAVE_DELAY_MS))


# Marks a file tab whose changes are not on disk yet.
MODIFIED_MARKER = "• "


class Tab:
    """One note: the widgets that show it and the identity it is stored under."""

    def __init__(
        self,
        tab_id: str,
        kind: str = KIND_SCRATCH,
        path: str | None = None,
        title: str = "",
    ) -> None:
        self.id = tab_id
        self.kind = kind
        self.path = path
        self.title = title
        # Set by the window. A note that cannot be written out is the one thing
        # the user has to be told about, since carrying on typing into it is
        # the worst thing they could do.
        self.on_save_failed: Callable[[str], None] | None = None

        self.has_draft = False
        self.external_mtime: float | None = None
        # A tab that has just been made is one you are looking at.
        self.last_viewed = time.time()
        # Whether the buffer differs from the file on disk. Only meaningful for
        # file tabs: a scratch note has no separate copy to differ from.
        self.modified = False

        self._save_timer = 0
        self._unsaved = False

        self.buffer = GtkSource.Buffer()
        self.buffer.set_highlight_matching_brackets(True)

        self.view = GtkSource.View(buffer=self.buffer)
        self.view.set_show_line_numbers(True)
        self.view.set_highlight_current_line(True)
        self.view.set_monospace(True)
        # The font itself comes from the preferences, applied to this class for
        # every view at once.
        self.view.add_css_class(theme.EDITOR_CSS_CLASS)
        self.view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.view.set_left_margin(TEXT_MARGIN)
        self.view.set_right_margin(TEXT_MARGIN)
        self.view.set_top_margin(EDGE_MARGIN)
        self.view.set_bottom_margin(EDGE_MARGIN)

        self.scroller = Gtk.ScrolledWindow(child=self.view, hexpand=True, vexpand=True)

        # Says so plainly when a note is big enough that it is no longer being
        # written out as you type. Hidden the rest of the time, which is nearly
        # always.
        self.notice = Adw.Banner()
        self.notice.set_revealed(False)

        # The widget the tab view holds. Everything else hangs off it.
        self.root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.root.append(self.notice)
        self.root.append(self.scroller)

        self.buffer.connect("changed", self._on_buffer_changed)

    @classmethod
    def new_scratch(cls, title: str) -> "Tab":
        tab = cls(notes.new_id(), kind=KIND_SCRATCH, title=title)
        # Give the note a file right away. An empty tab is still a tab the user
        # opened, and without a file behind it there is nothing to restore.
        notes.write_note(tab.id, "")
        return tab

    @classmethod
    def new_file(cls, path: Path, text: str) -> "Tab":
        tab = cls(notes.new_id(), kind=KIND_FILE, path=str(path), title=path.name)
        tab.text = text
        tab.external_mtime = notes.file_mtime(path)
        return tab

    @classmethod
    def new_recovered(cls, title: str, text: str) -> "Tab":
        """A new note holding what an archived one held."""
        tab = cls(notes.new_id(), kind=KIND_SCRATCH, title=title)
        tab.text = text
        # Setting text is not an edit, so the note has to be written explicitly
        # or nothing would put it on disk until the user typed in it.
        notes.write_note(tab.id, text)
        return tab

    @classmethod
    def from_state(cls, state: TabState) -> "Tab":
        tab = cls(
            state.id,
            kind=state.kind,
            path=state.path,
            title=state.title,
        )
        tab.has_draft = state.has_draft
        tab.external_mtime = state.external_mtime
        # Restoring a tab is not looking at it. Only the one the window brings
        # to the front counts as viewed, and it says so itself.
        tab.last_viewed = state.last_viewed
        return tab

    def viewed(self) -> None:
        self.last_viewed = time.time()

    def state(self) -> TabState:
        return TabState(
            id=self.id,
            kind=self.kind,
            path=self.path,
            title=self.title,
            cursor_offset=self.cursor_offset(),
            scroll_offset=self.scroll_fraction(),
            has_draft=self.has_draft,
            external_mtime=self.external_mtime,
            last_viewed=self.last_viewed,
        )

    @property
    def text(self) -> str:
        start, end = self.buffer.get_bounds()
        return self.buffer.get_text(start, end, True)

    @text.setter
    def text(self, value: str) -> None:
        self.buffer.set_text(value)
        # Setting the text leaves the cursor where it finished, at the bottom of
        # the note. Anything with a position worth keeping restores it just
        # after this, and everything else would rather start at the top.
        self.buffer.place_cursor(self.buffer.get_start_iter())
        # Loading content into a tab is not an edit, so there is nothing to
        # write back out.
        self._cancel_pending_save()
        self._unsaved = False
        self.modified = False
        # Said up front rather than after the first keystroke, so the terms are
        # clear before anything has been typed.
        self._show_save_notice(autosave_delay(self.buffer.get_char_count()))

    def set_style_scheme(self, scheme: GtkSource.StyleScheme | None) -> None:
        self.buffer.set_style_scheme(scheme)

    def display_title(self) -> str:
        if self.kind == KIND_FILE and self.modified:
            return f"{MODIFIED_MARKER}{self.title}"
        return self.title

    def save_to(self, path: Path) -> None:
        """Write the buffer to a path the user chose.

        This is the only place a file outside Notemad's own storage is written,
        and it only ever runs because the user asked for it.
        """
        text = self.text
        notes.write_file(path, text)

        was_scratch = self.kind == KIND_SCRATCH
        previous_title = self.title
        self.kind = KIND_FILE
        self.path = str(path)
        self.title = path.name
        self.external_mtime = notes.file_mtime(path)
        self.modified = False
        self._unsaved = False
        self._cancel_pending_save()

        # The content is on disk where the user put it, so the copies Notemad
        # was keeping on its behalf have nothing left to protect.
        if self.has_draft:
            notes.discard_draft(self.id)
            self.has_draft = False
        if was_scratch:
            # Archived under the name it had while it was a note, since that is
            # what it would be recognised by in the recovery list.
            archive.archive_note(self.id, previous_title)

    def has_changed_on_disk(self) -> bool:
        """Whether the file has been written by something other than Notemad."""
        if self.kind != KIND_FILE or self.path is None:
            return False
        return notes.file_mtime(self.path) != self.external_mtime

    def flush(self) -> None:
        """Write the buffer out to Notemad's own storage, if it has changed.

        A scratch note goes to its note file. An external file gets a draft
        instead. The file the user opened is never touched here: it is only ever
        written by an explicit save.
        """
        self._cancel_pending_save()
        if not self._unsaved:
            return

        try:
            if self.kind == KIND_SCRATCH:
                notes.write_note(self.id, self.text)
            else:
                notes.write_draft(self.id, self.text)
                self.has_draft = True
        except OSError as exc:
            # A full or read only disk. Left unsaid, the note would look saved
            # while nothing was reaching the disk at all.
            logger.warning("could not write %s: %s", self.id, exc)
            if self.on_save_failed is not None:
                self.on_save_failed(self.title)
            return

        self._unsaved = False

    def cursor_offset(self) -> int:
        cursor = self.buffer.get_iter_at_mark(self.buffer.get_insert())
        return cursor.get_offset()

    def set_cursor_offset(self, offset: int) -> None:
        clamped = max(0, min(offset, self.buffer.get_char_count()))
        self.buffer.place_cursor(self.buffer.get_iter_at_offset(clamped))

    def scroll_fraction(self) -> float:
        """How far down the note the view is, as a fraction of what can scroll.

        Stored as a fraction rather than a pixel offset so that restoring into a
        differently sized window lands in roughly the right place.
        """
        adjustment = self.scroller.get_vadjustment()
        span = adjustment.get_upper() - adjustment.get_page_size()
        return adjustment.get_value() / span if span > 0 else 0.0

    def set_scroll_fraction(self, fraction: float) -> bool:
        """Scroll to a stored fraction, reporting whether it could be done yet.

        Until the view has been allocated its page size is zero, which makes the
        whole document look scrollable and lands any value set here far below
        where it belongs, only to be clamped afterwards. The caller needs to know
        to try again rather than accept that.
        """
        if fraction <= 0:
            return True

        adjustment = self.scroller.get_vadjustment()
        page_size = adjustment.get_page_size()
        if page_size <= 0:
            return False

        span = adjustment.get_upper() - page_size
        if span > 0:
            adjustment.set_value(min(fraction, 1.0) * span)
        return True

    def _on_buffer_changed(self, buffer: GtkSource.Buffer) -> None:
        self._unsaved = True
        self.modified = True
        # Each keystroke pushes the write further out, so a burst of typing
        # results in one write rather than one per character.
        self._cancel_pending_save()
        delay = autosave_delay(self.buffer.get_char_count())
        self._show_save_notice(delay)
        self._save_timer = GLib.timeout_add(delay, self._on_save_timeout)

    def _show_save_notice(self, delay_ms: int) -> None:
        """Say out loud when a note is too big to be written out as you type."""
        if delay_ms <= AUTOSAVE_DELAY_MS:
            self.notice.set_revealed(False)
            return

        seconds = round(delay_ms / 1000)
        unit = "second" if seconds == 1 else "seconds"
        self.notice.set_title(
            f"Large file: your changes are saved every {seconds} {unit}, "
            "not as you type."
        )
        self.notice.set_revealed(True)

    def _on_save_timeout(self) -> bool:
        self._save_timer = 0
        self.flush()
        return GLib.SOURCE_REMOVE

    def _cancel_pending_save(self) -> None:
        if self._save_timer:
            GLib.source_remove(self._save_timer)
            self._save_timer = 0
