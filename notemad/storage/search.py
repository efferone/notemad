"""Searching across every note at once, rather than the one buffer in front.

What gets searched is the files on disk, not the live buffers. Buffers are
written out half a second after you stop typing, so the caller flushes first and
after that there is one place to look instead of two.

That covers three kinds of thing. Scratch notes are files in the notes
directory. A file you opened from disk is searched through its draft if it has
unsaved changes, and through the file itself if it does not, so what you see in
the tab is what gets matched. Closed notes are in the archive, and only when
they are asked for.
"""

import re
from dataclasses import dataclass
from pathlib import Path

from . import archive, notes, paths, session
from .session import KIND_FILE

# Past this a note is a log file somebody dropped in, and reading it on every
# search costs more than the answer is worth.
MAX_SEARCH_BYTES = 10 * 1024 * 1024

# One note with ten thousand hits should not be able to fill the results list.
MAX_MATCHES_PER_NOTE = 50

# Enough of a line to recognise the hit by, without wrapping the results list.
LINE_PREVIEW_LENGTH = 120


@dataclass(frozen=True)
class Match:
    """Where one hit is, and the line it sits on."""

    line: int
    text: str
    offset: int


@dataclass(frozen=True)
class Result:
    id: str
    title: str
    archived: bool
    modified_at: float
    matches: tuple[Match, ...]
    # The file this text was read from, so reading it again finds the same
    # thing the offsets were measured against.
    source: str
    # Where the note lives outside Notemad, for a file opened from disk. This is
    # what the caller opens, which is not necessarily what was read.
    path: str | None = None


def search(
    query: str,
    include_archive: bool = False,
    case_sensitive: bool = False,
) -> list[Result]:
    """Every note containing the query, open ones first and newest first.

    An empty query finds nothing rather than everything. Searching for nothing
    in particular is not a question worth answering.
    """
    if not query.strip():
        return []

    tabs = session.load().tabs
    titles = {tab.id: tab.title for tab in tabs}
    results = []

    for path in _note_files(paths.notes_dir()):
        result = _search_file(
            path, query, case_sensitive, path.stem, titles.get(path.stem, "")
        )
        if result is not None:
            results.append(result)

    for tab in tabs:
        if tab.kind != KIND_FILE or not tab.path:
            continue
        # The draft is the version on screen. Without one the tab and the file
        # agree, so the file is the thing to read.
        source = notes.draft_path(tab.id) if tab.has_draft else Path(tab.path)
        result = _search_file(
            source, query, case_sensitive, tab.id, tab.title, path=tab.path
        )
        if result is not None:
            results.append(result)

    if include_archive:
        names = {entry.id: entry.title for entry in archive.entries()}
        for path in _note_files(paths.archive_dir()):
            result = _search_file(
                path,
                query,
                case_sensitive,
                path.stem,
                names.get(path.stem, ""),
                archived=True,
            )
            if result is not None:
                results.append(result)

    # Open notes ahead of closed ones, and within each the ones touched most
    # recently, which is where you were working and so where you are looking.
    results.sort(key=lambda result: (result.archived, -result.modified_at))
    return results


def read(result: Result) -> str | None:
    """The full text behind a result, for previewing it.

    Through the same guarded read as everything else, since the file could have
    changed into something unreadable between the search and the preview.
    """
    return notes.read_file(result.source)


def _search_file(
    source: Path,
    query: str,
    case_sensitive: bool,
    note_id: str,
    title: str,
    path: str | None = None,
    archived: bool = False,
) -> Result | None:
    """Search one file, given who it belongs to.

    `source` is what gets read. `path` is where the note lives outside Notemad,
    which is only set for a file opened from disk and is not always the same
    file, since an edited one is read through its draft.
    """
    try:
        stat = source.stat()
    except OSError:
        return None

    # A pipe or a device reports no size and would never finish being read.
    if not source.is_file() or stat.st_size > MAX_SEARCH_BYTES:
        return None

    try:
        text = source.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        # A note nobody can read is not worth interrupting a search over.
        return None

    matches = find_matches(text, query, case_sensitive)
    if not matches:
        return None

    return Result(
        id=note_id,
        title=title,
        archived=archived,
        modified_at=stat.st_mtime,
        matches=matches,
        source=str(source),
        path=path,
    )


def find_matches(
    text: str, query: str, case_sensitive: bool = False
) -> tuple[Match, ...]:
    """Every hit in one note, at most one per line, with its offset.

    One per line because the results list shows lines, and a line naming the
    same thing three times is still the one place to look.
    """
    # Matched with a regular expression rather than by lowering both sides,
    # because lowering can change a string's length, and every offset here is
    # measured against the line as it actually is.
    flags = 0 if case_sensitive else re.IGNORECASE
    needle = re.compile(re.escape(query), flags)
    matches = []
    offset = 0

    for number, line in enumerate(text.split("\n"), start=1):
        found = needle.search(line)
        if found is not None:
            matches.append(
                Match(line=number, text=_trim(line), offset=offset + found.start())
            )
            if len(matches) >= MAX_MATCHES_PER_NOTE:
                break
        # The newline the split took off still counts towards the offset.
        offset += len(line) + 1

    return tuple(matches)


def _trim(line: str) -> str:
    stripped = line.strip()
    if len(stripped) <= LINE_PREVIEW_LENGTH:
        return stripped
    return stripped[:LINE_PREVIEW_LENGTH] + "..."


def _note_files(directory: Path) -> list[Path]:
    try:
        return sorted(
            path
            for path in directory.iterdir()
            if path.is_file() and path.suffix == ".txt"
        )
    except OSError:
        return []
