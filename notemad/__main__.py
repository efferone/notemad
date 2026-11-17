"""Entry point for the notemad command."""

import logging
import sys

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")

from gi.repository import Gdk, Gtk

from .app import NotemadApplication

HELP_FLAGS = frozenset({"-h", "--help", "--help-all", "--help-gapplication"})


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    args = sys.argv if argv is None else argv

    # Started over a plain SSH session or from a tty, GTK would otherwise fail
    # deep inside the first window with a page of assertions and a zero exit.
    # init_check on its own reports success there, so ask for the display it is
    # supposed to have opened. Asking for help needs no display at all.
    if not HELP_FLAGS.intersection(args):
        Gtk.init_check()
        if Gdk.Display.get_default() is None:
            print("notemad: no display available", file=sys.stderr)
            return 1

    return NotemadApplication().run(args)


if __name__ == "__main__":
    raise SystemExit(main())
