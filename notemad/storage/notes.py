"""Reading, writing and archiving scratch notes and external file drafts."""

import logging
import uuid
from pathlib import Path

from . import paths
from .atomic import atomic_write_text

logger = logging.getLogger(__name__)


def new_id() -> str:
    return str(uuid.uuid4())


def note_path(note_id: str) -> Path:
    return paths.notes_dir() / f"{note_id}.txt"


def draft_path(note_id: str) -> Path:
    return paths.drafts_dir() / f"{note_id}.txt"


def _read(path: Path) -> str | None:
    """Return the contents of path, or None if it cannot be read.

    Only ordinary files are read. Opening a pipe or a device blocks until
    something writes to the other end, which for /dev/zero is never, and the
    read happens on the same thread that draws the window.
    """
    try:
        if not path.is_file():
            return None
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError) as exc:
        logger.warning("could not read %s: %s", path, exc)
        return None


def read_note(note_id: str) -> str | None:
    return _read(note_path(note_id))


def write_note(note_id: str, text: str) -> None:
    paths.ensure_dir(paths.notes_dir())
    atomic_write_text(note_path(note_id), text)


def note_exists(note_id: str) -> bool:
    return note_path(note_id).is_file()


def read_file(path: Path | str) -> str | None:
    """Read a file the user opened from disk.

    Reading is the only thing Notemad ever does to one of these without being
    asked. Writing happens on an explicit save and nowhere else.
    """
    return _read(Path(path))


def write_file(path: Path | str, text: str) -> None:
    """Write a file the user opened.

    The only caller of this is an explicit save. Nothing else in Notemad is
    allowed to put bytes at a path the user chose.
    """
    atomic_write_text(Path(path), text)


def file_size(path: Path | str) -> int | None:
    try:
        return Path(path).stat().st_size
    except OSError:
        return None


def file_mtime(path: Path | str) -> float | None:
    try:
        return Path(path).stat().st_mtime
    except OSError:
        return None


def read_draft(note_id: str) -> str | None:
    return _read(draft_path(note_id))


def write_draft(note_id: str, text: str) -> None:
    paths.ensure_dir(paths.drafts_dir())
    atomic_write_text(draft_path(note_id), text)


def draft_exists(note_id: str) -> bool:
    return draft_path(note_id).is_file()


def discard_draft(note_id: str) -> None:
    """Remove a draft once its changes have been written to the real file."""
    try:
        draft_path(note_id).unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        logger.warning("could not discard draft %s: %s", note_id, exc)
