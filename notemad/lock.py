"""One copy of Notemad per data directory.

The desktop already gives us one instance per session bus, which is enough until
somebody logs in twice or forwards a display while Notemad is open locally. Then
there are two, both writing the same session file, the same config and the same
notes. The tab list is the least of it: whichever copy saves last wins, so a note
typed in one can be overwritten by the other flushing a buffer it read minutes
ago.
"""

import fcntl
import logging
import os
from pathlib import Path

from .storage import paths

logger = logging.getLogger(__name__)


def lock_path() -> Path:
    return paths.data_home() / "lock"


class DirectoryLock:
    """An exclusive lock on the data directory, held for as long as we run.

    The kernel drops it when the process ends, however it ends, so a crash
    leaves nothing stale behind and there is nothing to tidy up on the way out.
    """

    def __init__(self) -> None:
        self._handle = None
        # The copy that has it, when we could not get it.
        self.holder: int | None = None

    def take(self) -> bool:
        """Whether this copy may use the data directory.

        True when the lock was taken, and also when locking turned out to be
        unavailable. Some network filesystems have no working flock, and being
        unable to lock is a poor reason to refuse to start.
        """
        path = lock_path()
        try:
            paths.ensure_dir(path.parent)
            # Opened for appending rather than writing so that failing to get
            # the lock does not truncate the pid belonging to the copy that has
            # it.
            handle = path.open("a+")
        except OSError as exc:
            logger.warning("could not open the lock file: %s", exc)
            return True

        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.holder = _pid_in(handle)
            handle.close()
            return False
        except OSError as exc:
            logger.warning("could not lock the data directory: %s", exc)
            handle.close()
            return True

        self._handle = handle
        _write_pid(handle)
        return True


def _write_pid(handle) -> None:
    """Leave our pid behind, so the copy that is refused can name this one."""
    try:
        handle.seek(0)
        handle.truncate()
        handle.write(f"{os.getpid()}\n")
        handle.flush()
    except OSError as exc:
        logger.warning("could not record the pid in the lock file: %s", exc)


def _pid_in(handle) -> int | None:
    try:
        handle.seek(0)
        return int(handle.read().strip())
    except (OSError, ValueError):
        return None
