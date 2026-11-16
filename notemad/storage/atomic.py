"""Atomic file write primitive used by every write Notemad performs."""

import contextlib
import logging
import os
import stat
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)


def atomic_write_text(path: Path, text: str, encoding: str = "utf-8") -> None:
    """Replace the contents of path with text, all or nothing.

    A crash partway through leaves the previous contents intact rather than a
    truncated file.
    """
    path = Path(path)
    # Replacing the link itself would leave the real file untouched while
    # reporting a save, which is the wrong way round for anyone keeping their
    # files in one place and linking to them from another.
    if path.is_symlink():
        path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)

    # The temporary file has to be created in the target directory. os.replace
    # is only atomic within a single filesystem, so /tmp is not an option.
    fd, tmp_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding=encoding, newline="") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())

        # mkstemp creates the file 0600, which is right for a new note but wrong
        # for a file the user already owns and may have shared deliberately.
        if path.exists():
            os.chmod(tmp_name, stat.S_IMODE(path.stat().st_mode))

        os.replace(tmp_name, path)
        _sync_dir(path.parent)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp_name)


def _sync_dir(directory: Path) -> None:
    """Flush the rename itself, not just the bytes it moved into place.

    Without this the new contents are on the disk but the directory entry
    pointing at them may not be, so a power cut can still leave the old file.
    Not every filesystem allows a directory to be opened this way, and one that
    does not is no reason to fail a save.
    """
    try:
        fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError as exc:
        logger.debug("could not flush %s: %s", directory, exc)
    finally:
        os.close(fd)
