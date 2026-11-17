"""The strip along the bottom: where the cursor is, and what the archive holds."""

from collections.abc import Callable

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gtk

from ..sizes import human_size
from ..storage import archive


class StatusBar(Gtk.Box):
    def __init__(self, on_archive_clicked: Callable[[], None]) -> None:
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL)
        self.add_css_class("statusbar")

        self.position = Gtk.Label(xalign=0, hexpand=True)
        self.position.add_css_class("dim-label")

        # The archive is the one part of Notemad's storage that only grows, and
        # the only part the user can delete from, so it is the figure worth
        # having in front of them.
        self.archive_button = Gtk.Button()
        self.archive_button.add_css_class("flat")
        self.archive_button.add_css_class("archive-usage")
        self.archive_button.connect("clicked", lambda *_: on_archive_clicked())

        self.append(self.position)
        self.append(self.archive_button)
        # An expanding gap on either side of the archive figure, so it sits in
        # the middle of the strip rather than up against the edge.
        self.append(Gtk.Box(hexpand=True))

    def update(self, line: int, column: int) -> None:
        self.position.set_text(f"Ln {line}, Col {column}")

    def set_archive(self, usage: archive.Usage, warn_at: int) -> None:
        """Show what the archive is taking up, and say when it is a lot.

        Hidden entirely while the archive is empty: a note nobody has closed yet
        is not something to put a figure on.
        """
        self.archive_button.set_visible(usage.count > 0)
        if not usage.count:
            return

        over = bool(warn_at) and usage.total >= warn_at
        size = human_size(usage.total)
        notes = "note" if usage.count == 1 else "notes"

        self.archive_button.set_label(
            f"Archive {size} - click to review" if over else f"Archive {size}"
        )
        # Named rather than merely coloured, since a figure appearing in a status
        # bar means nothing on its own.
        tooltip = (
            f"{usage.count} closed {notes} kept in the archive, taking {size}. "
            "Click to look through them, recover one, or clear out the old ones."
        )
        if over:
            tooltip += (
                f"\n\nThis is past the {human_size(warn_at)} you asked to be warned at."
            )
        self.archive_button.set_tooltip_text(tooltip)

        if over:
            self.archive_button.add_css_class("archive-over")
        else:
            self.archive_button.remove_css_class("archive-over")
