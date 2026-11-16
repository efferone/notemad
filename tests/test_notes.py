import os
import stat

import pytest

from notemad.storage import notes, paths


def test_a_note_round_trips_and_a_missing_one_reads_as_none(xdg_home):
    note_id = notes.new_id()
    notes.write_note(note_id, "some thoughts")

    assert notes.read_note(note_id) == "some thoughts"
    assert notes.note_exists(note_id)

    absent = notes.new_id()
    assert notes.read_note(absent) is None
    assert not notes.note_exists(absent)


def test_the_note_directory_is_private(xdg_home):
    notes.write_note(notes.new_id(), "content")

    assert stat.S_IMODE(paths.notes_dir().stat().st_mode) == 0o700


def test_a_draft_round_trips_and_can_be_discarded_twice(xdg_home):
    note_id = notes.new_id()
    notes.write_draft(note_id, "unsaved edit")

    assert notes.read_draft(note_id) == "unsaved edit"
    assert notes.draft_exists(note_id)

    notes.discard_draft(note_id)
    assert not notes.draft_exists(note_id)
    assert notes.read_draft(note_id) is None

    # Discarding one that has already gone is not a problem worth raising over.
    notes.discard_draft(note_id)


def test_a_note_and_its_draft_are_stored_separately(xdg_home):
    note_id = notes.new_id()
    notes.write_note(note_id, "note content")
    notes.write_draft(note_id, "draft content")

    assert notes.read_note(note_id) == "note content"
    assert notes.read_draft(note_id) == "draft content"


def test_a_file_the_user_opened_round_trips(xdg_home, tmp_path):
    external = tmp_path / "somewhere" / "notes.txt"
    external.parent.mkdir()
    external.write_text("opened from disk", encoding="utf-8")

    assert notes.read_file(external) == "opened from disk"

    notes.write_file(external, "saved on purpose")
    assert external.read_text(encoding="utf-8") == "saved on purpose"
    assert notes.read_file(tmp_path / "never-existed.txt") is None


@pytest.fixture
def unreadable(request, xdg_home, tmp_path):
    """Something at a path that is not text Notemad can put in a buffer."""
    if request.param == "directory":
        return tmp_path
    if request.param == "pipe":
        pipe = tmp_path / "pipe"
        # Reading one blocks until something writes to the other end, which for
        # a device like /dev/zero is never, and it would block the whole window.
        os.mkfifo(pipe)
        return pipe
    note_id = notes.new_id()
    notes.write_note(note_id, "placeholder")
    notes.note_path(note_id).write_bytes(b"\xff\xfe not utf-8")
    return notes.note_path(note_id)


@pytest.mark.parametrize(
    "unreadable", ["directory", "pipe", "undecodable"], indirect=True
)
def test_what_cannot_be_read_as_text_comes_back_as_none(unreadable):
    assert notes.read_file(unreadable) is None


def test_the_size_and_date_of_a_file_that_may_not_be_there(tmp_path):
    external = tmp_path / "notes.txt"

    assert notes.file_size(external) is None
    assert notes.file_mtime(external) is None

    external.write_text("four", encoding="utf-8")

    assert notes.file_size(external) == 4
    assert notes.file_mtime(external) == external.stat().st_mtime
