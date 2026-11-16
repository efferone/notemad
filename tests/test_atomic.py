import os
import stat

import pytest

from notemad.storage.atomic import atomic_write_text


def test_writes_text_exactly_as_it_was_given(tmp_path):
    """Missing parents are made, and nothing about the text is translated."""
    target = tmp_path / "one" / "two" / "note.txt"
    text = "line one\nsecond line with an accent: café\n"

    atomic_write_text(target, text)

    assert target.read_text(encoding="utf-8") == text
    # newline="" on purpose, so a note is the bytes it was handed.
    assert target.read_bytes() == text.encode("utf-8")


def test_overwriting_leaves_no_temporary_files_behind(tmp_path):
    target = tmp_path / "note.txt"
    atomic_write_text(target, "first")
    atomic_write_text(target, "second")

    assert target.read_text(encoding="utf-8") == "second"
    assert list(tmp_path.iterdir()) == [target]


def test_a_new_file_is_private_and_an_existing_one_keeps_its_mode(tmp_path):
    fresh = tmp_path / "fresh.txt"
    atomic_write_text(fresh, "hello")

    shared = tmp_path / "shared.txt"
    shared.write_text("first", encoding="utf-8")
    shared.chmod(0o644)
    atomic_write_text(shared, "second")

    assert stat.S_IMODE(fresh.stat().st_mode) == 0o600
    assert stat.S_IMODE(shared.stat().st_mode) == 0o644


def test_a_failed_write_leaves_the_original_intact(tmp_path, monkeypatch):
    target = tmp_path / "note.txt"
    atomic_write_text(target, "original")

    def explode(fd):
        raise OSError("disk went away")

    monkeypatch.setattr(os, "fsync", explode)

    with pytest.raises(OSError):
        atomic_write_text(target, "replacement")

    assert target.read_text(encoding="utf-8") == "original"
    assert list(tmp_path.iterdir()) == [target]


def test_a_symlink_is_followed_to_the_file_it_points_at(tmp_path):
    """Including a dangling one, which is a file that does not exist yet."""
    real = tmp_path / "real.txt"
    real.write_text("original", encoding="utf-8")
    link = tmp_path / "link.txt"
    link.symlink_to(real)

    dangling_target = tmp_path / "not-there-yet.txt"
    dangling = tmp_path / "dangling.txt"
    dangling.symlink_to(dangling_target)

    atomic_write_text(link, "saved through the link")
    atomic_write_text(dangling, "brought into being")

    assert real.read_text(encoding="utf-8") == "saved through the link"
    assert dangling_target.read_text(encoding="utf-8") == "brought into being"
    assert link.is_symlink()
    assert dangling.is_symlink()
