"""One copy per data directory.

flock is held on the open file description rather than by the process, so a
second lock taken through a second handle is refused even from here.
"""

import os

from notemad import lock


def test_the_first_copy_takes_the_lock_and_leaves_its_pid(xdg_home):
    first = lock.DirectoryLock()

    assert first.take()
    assert lock.lock_path().read_text().strip() == str(os.getpid())


def test_a_second_copy_is_refused_and_can_name_the_first(xdg_home):
    first = lock.DirectoryLock()
    assert first.take()

    second = lock.DirectoryLock()

    assert not second.take()
    assert second.holder == os.getpid()


def test_being_refused_leaves_the_pid_of_the_copy_that_holds_it(xdg_home):
    """Opened for appending on purpose: writing would wipe what we came to read."""
    first = lock.DirectoryLock()
    assert first.take()

    assert not lock.DirectoryLock().take()
    assert lock.lock_path().read_text().strip() == str(os.getpid())


def test_a_lock_file_that_cannot_be_made_does_not_stop_the_app(xdg_home, monkeypatch):
    """Somewhere unwritable is a reason to run unprotected, not to refuse to run."""
    monkeypatch.setattr(lock.paths, "data_home", lambda: xdg_home / "nowhere" / "x")
    monkeypatch.setattr(
        lock.paths, "ensure_dir", lambda path: (_ for _ in ()).throw(OSError("no"))
    )

    assert lock.DirectoryLock().take()
