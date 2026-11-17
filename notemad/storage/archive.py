"""The archive: notes from tabs that were closed, and how to find them again.

A note's title only ever lives in the session entry for its tab, and closing the
tab is exactly what throws that entry away. So the archive keeps its own index
alongside the files, or everything in here would be a UUID and nothing else.
"""

import json
import logging
import math
import os
import shutil
import time
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from . import paths
from .atomic import atomic_write_text
from .session import KIND_FILE, KIND_SCRATCH, VALID_KINDS

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1


@dataclass(frozen=True)
class ArchiveEntry:
    """One closed note: what it was called and where it came from."""

    id: str
    title: str
    archived_at: float
    kind: str = KIND_SCRATCH
    # For a file tab, the path whose unsaved changes these were.
    path: str | None = None
    # When the tab was last brought to the front. Zero for a note closed before
    # Notemad kept track, where the only date there has ever been is the one it
    # was closed on.
    last_viewed: float = 0.0
    # Measured from the file rather than stored, so it cannot go stale.
    size: int = 0


@dataclass(frozen=True)
class Usage:
    """How much of the disk the archive is taking up."""

    total: int = 0
    count: int = 0


def archive_note(note_id: str, title: str, last_viewed: float = 0.0) -> bool:
    """Move a closed scratch note into the archive.

    Returns whether anything was kept. An empty note is removed rather than
    archived: a tab opened and closed without being typed in has nothing in it
    to come back from, and archiving those fills the list with blanks.
    """
    entry = ArchiveEntry(
        id=note_id,
        title=title,
        archived_at=time.time(),
        kind=KIND_SCRATCH,
        last_viewed=last_viewed,
    )
    return _store(paths.notes_dir() / f"{note_id}.txt", entry)


def archive_draft(
    note_id: str,
    title: str,
    path: str,
    last_viewed: float = 0.0,
) -> bool:
    """Move the unsaved changes of a closed file tab into the archive.

    Nothing else would ever look at that draft again, because the tab that knew
    about it is gone. Keeping it here means closing a file you had been editing
    is as recoverable as closing a note.
    """
    entry = ArchiveEntry(
        id=note_id,
        title=title,
        archived_at=time.time(),
        kind=KIND_FILE,
        path=path,
        last_viewed=last_viewed,
    )
    return _store(paths.drafts_dir() / f"{note_id}.txt", entry)


def entry_path(note_id: str) -> Path:
    return paths.archive_dir() / f"{note_id}.txt"


def read(note_id: str) -> str | None:
    try:
        return entry_path(note_id).read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError) as exc:
        logger.warning("could not read archived note %s: %s", note_id, exc)
        return None


def entries() -> list[ArchiveEntry]:
    """Everything in the archive, most recently closed first.

    The index and the directory are reconciled both ways. An entry whose file
    has gone is dropped, and a file the index has never heard of is still
    listed, so notes archived before there was an index remain reachable.
    """
    index = _load_index()
    found = []

    for path in _archived_files():
        note_id = path.stem
        entry = index.get(note_id)
        if entry is None:
            entry = _entry_from_file(path)
        found.append(replace(entry, size=_size_of(path) or 0))

    return sorted(found, key=lambda entry: entry.archived_at, reverse=True)


def usage() -> Usage:
    """How many notes are in the archive and what they come to on disk.

    A stat for each file rather than a walk of the whole directory tree: the
    archive is flat, and this is cheap enough to run whenever it changes instead
    of on a timer.
    """
    total = 0
    count = 0
    for path in _archived_files():
        size = _size_of(path)
        if size is None:
            continue
        total += size
        count += 1
    return Usage(total=total, count=count)


def stale(
    entries: list[ArchiveEntry], cutoff: float, by_last_viewed: bool
) -> list[ArchiveEntry]:
    """The entries old enough to be worth offering to remove.

    By when they were closed, or by when their tab was last looked at. Nothing
    recorded a viewing before this existed, so an entry without one falls back
    to the date it was closed, which is the last moment it can possibly have
    been in front of anybody.
    """
    chosen = []
    for entry in entries:
        when = entry.archived_at
        if by_last_viewed and entry.last_viewed:
            when = entry.last_viewed
        if when < cutoff:
            chosen.append(entry)
    return chosen


def delete(note_id: str) -> bool:
    """Remove an archived note for good. There is nowhere further back."""
    return delete_many([note_id]) == 1


def delete_many(note_ids: list[str]) -> int:
    """Remove several archived notes, returning how many went.

    The ids are checked again here rather than trusting the caller, because this
    is the one thing Notemad does that cannot be undone. The index is written
    once at the end, not once per note.
    """
    index = _load_index()
    removed = 0
    touched = False

    for note_id in note_ids:
        if not paths.is_safe_id(note_id):
            logger.warning("refusing to delete an archived note named %r", note_id)
            continue

        try:
            entry_path(note_id).unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            logger.warning("could not delete archived note %s: %s", note_id, exc)
            continue

        removed += 1
        if index.pop(note_id, None) is not None:
            touched = True

    if touched:
        _save_index(index)
    return removed


def _store(source: Path, entry: ArchiveEntry) -> bool:
    try:
        empty = source.stat().st_size == 0
    except OSError:
        # Nothing there to archive. A file tab closed with no unsaved changes
        # never had a draft in the first place.
        return False

    if empty:
        _discard(source)
        return False

    paths.ensure_dir(paths.archive_dir())
    if not _move(source, entry_path(entry.id)):
        return False

    index = _load_index()
    index[entry.id] = entry
    _save_index(index)
    return True


def _move(source: Path, destination: Path) -> bool:
    """Move a file into the archive, across a filesystem boundary if need be.

    Drafts live under the state directory and the archive under the data one,
    which are usually the same filesystem but do not have to be. A rename
    between two of them fails outright, and the unsaved changes of a closed tab
    are not something to lose over that.
    """
    try:
        os.replace(source, destination)
        return True
    except OSError:
        pass

    try:
        shutil.move(str(source), str(destination))
        return True
    except OSError as exc:
        logger.warning("could not archive %s: %s", source, exc)
        return False


def _discard(path: Path) -> None:
    try:
        path.unlink()
    except OSError as exc:
        logger.warning("could not remove empty note %s: %s", path, exc)


def _archived_files() -> list[Path]:
    try:
        return [
            path
            for path in paths.archive_dir().iterdir()
            if path.is_file() and path.suffix == ".txt"
        ]
    except OSError:
        return []


def _size_of(path: Path) -> int | None:
    try:
        return path.stat().st_size
    except OSError:
        return None


def _entry_from_file(path: Path) -> ArchiveEntry:
    """Describe an archived file the index cannot account for."""
    try:
        archived_at = path.stat().st_mtime
    except OSError:
        archived_at = 0.0
    return ArchiveEntry(id=path.stem, title="", archived_at=archived_at)


def _load_index() -> dict[str, ArchiveEntry]:
    """Read the index, treating anything unreadable as an empty one.

    The files are what matter here. The index only puts names to them, so a
    damaged one costs titles rather than notes and is never worth raising over.
    """
    try:
        raw = paths.archive_index().read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeDecodeError) as exc:
        logger.warning("could not read archive index: %s", exc)
        return {}

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning("archive index is not valid JSON: %s", exc)
        return {}

    if not isinstance(data, dict) or data.get("version") != SCHEMA_VERSION:
        logger.warning("archive index is not in a version we understand")
        return {}

    raw_entries = data.get("entries")
    if not isinstance(raw_entries, list):
        return {}

    index = {}
    for item in raw_entries:
        entry = _entry_from_dict(item)
        if entry is not None:
            index[entry.id] = entry
    return index


def _entry_from_dict(data: object) -> ArchiveEntry | None:
    if not isinstance(data, dict):
        return None

    note_id = data.get("id")
    if not isinstance(note_id, str) or not paths.is_safe_id(note_id):
        return None

    kind = data.get("kind")
    if kind not in VALID_KINDS:
        kind = KIND_SCRATCH

    title = data.get("title")
    path = data.get("path")

    return ArchiveEntry(
        id=note_id,
        title=title if isinstance(title, str) else "",
        archived_at=_as_time(data.get("archived_at")),
        kind=kind,
        path=path if isinstance(path, str) else None,
        last_viewed=_as_time(data.get("last_viewed")),
    )


def _as_time(value: object) -> float:
    """A timestamp from the index, or zero for anything that is not one.

    JSON hands back whatever was in the file, and Python's parser accepts NaN
    and Infinity as well, neither of which any date can be made out of.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    return float(value) if math.isfinite(value) else 0.0


def _save_index(index: dict[str, ArchiveEntry]) -> None:
    # An entry whose file has been removed by hand is a name with nothing behind
    # it, and this is the moment we are already rewriting the file anyway.
    live = [entry for entry in index.values() if entry_path(entry.id).is_file()]
    data = {
        "version": SCHEMA_VERSION,
        # The size is measured from the file whenever it is asked for, so writing
        # it here would only be a number waiting to be wrong.
        "entries": [
            {name: value for name, value in asdict(entry).items() if name != "size"}
            for entry in live
        ],
    }
    paths.ensure_dir(paths.archive_dir())
    atomic_write_text(paths.archive_index(), json.dumps(data, indent=2) + "\n")
