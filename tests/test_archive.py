import json
import time

import pytest

from notemad.storage import archive, notes, paths
from notemad.storage.session import KIND_FILE, KIND_SCRATCH


def store(text: str) -> str:
    note_id = notes.new_id()
    notes.write_note(note_id, text)
    return note_id


def test_archiving_a_note_moves_it_and_records_what_it_was(xdg_home):
    note_id = store("shopping list")

    assert archive.archive_note(note_id, "Groceries")
    assert not notes.note_exists(note_id)
    assert archive.read(note_id) == "shopping list"

    entry = archive.entries()[0]
    assert entry.id == note_id
    assert entry.title == "Groceries"
    assert entry.kind == KIND_SCRATCH
    assert entry.archived_at > 0


def test_archiving_a_draft_keeps_the_path_it_came_from(xdg_home):
    note_id = notes.new_id()
    notes.write_draft(note_id, "changes never saved")

    assert archive.archive_draft(note_id, "hosts", "/etc/hosts")
    assert not notes.draft_exists(note_id)

    entry = archive.entries()[0]
    assert entry.kind == KIND_FILE
    assert entry.path == "/etc/hosts"
    assert archive.read(note_id) == "changes never saved"


def test_nothing_is_archived_when_there_is_nothing_to_keep(xdg_home):
    """An empty note is removed rather than kept, and a missing one is a no-op."""
    empty = store("")

    assert not archive.archive_note(empty, "Note 1")
    assert not notes.note_exists(empty)

    assert not archive.archive_note(notes.new_id(), "Note 2")
    assert archive.entries() == []
    assert archive.read(notes.new_id()) is None


def test_entries_come_back_newest_first(xdg_home):
    for title in ("oldest", "middle", "newest"):
        archive.archive_note(store(title), title)
        # The index stores whole seconds worth of float, and three archives in a
        # row are otherwise fast enough to share a timestamp.
        time.sleep(0.01)

    assert [entry.title for entry in archive.entries()] == [
        "newest",
        "middle",
        "oldest",
    ]


def test_the_listing_reconciles_the_index_with_the_directory(xdg_home):
    """A file with no entry is still listed; an entry with no file is not."""
    kept = store("here for now")
    archive.archive_note(kept, "Note 1")

    removed = store("gone by hand")
    archive.archive_note(removed, "Removed")
    archive.entry_path(removed).unlink()

    orphan = paths.ensure_dir(paths.archive_dir()) / "abc-123.txt"
    orphan.write_text("from an older version", encoding="utf-8")

    listed = {entry.id: entry.title for entry in archive.entries()}

    assert listed == {kept: "Note 1", "abc-123": ""}
    assert archive.read("abc-123") == "from an older version"
    # The index lives in the same directory and is not a note.
    assert paths.archive_index().is_file()


def damage_index(change) -> None:
    index = paths.archive_index()
    data = json.loads(index.read_text(encoding="utf-8"))
    change(data)
    index.write_text(json.dumps(data), encoding="utf-8")


@pytest.mark.parametrize("version", [99, "not-a-number", None])
def test_an_index_we_cannot_use_costs_titles_rather_than_notes(xdg_home, version):
    note_id = store("still readable")
    archive.archive_note(note_id, "Groceries")
    damage_index(lambda data: data.__setitem__("version", version))

    entry = archive.entries()[0]

    assert entry.id == note_id
    assert entry.title == ""
    assert archive.read(note_id) == "still readable"


def test_an_index_that_is_not_json_at_all_costs_titles_rather_than_notes(xdg_home):
    note_id = store("still readable")
    archive.archive_note(note_id, "Groceries")
    paths.archive_index().write_text("{ not json at all", encoding="utf-8")

    assert archive.entries()[0].title == ""
    assert archive.read(note_id) == "still readable"


def test_a_bad_entry_does_not_take_the_others_with_it(xdg_home):
    """Unreadable entries are dropped, and a key from an older version ignored."""
    good = store("keep me")
    archive.archive_note(good, "Keeper")

    def spoil(data):
        data["entries"].insert(0, {"title": "no id at all"})
        data["entries"].append("not even an object")
        data["entries"][1]["language"] = "python"

    damage_index(spoil)

    assert [entry.title for entry in archive.entries()] == ["Keeper"]


def test_a_time_that_is_not_a_number_reads_as_no_time_at_all(xdg_home):
    note_id = store("content")
    archive.archive_note(note_id, "Note 1")

    # Infinity is not valid JSON, but Python's parser accepts it.
    paths.archive_index().write_text(
        f'{{"version": 1, "entries": [{{"id": "{note_id}", "title": "Note 1", '
        f'"archived_at": Infinity, "kind": "scratch"}}]}}',
        encoding="utf-8",
    )

    assert archive.entries()[0].archived_at == 0.0


def test_an_index_entry_carrying_a_path_is_ignored(xdg_home):
    note_id = store("a real note")
    archive.archive_note(note_id, "Note 1")

    outside = paths.data_home() / "not-part-of-the-archive.txt"
    outside.write_text("left alone", encoding="utf-8")

    damage_index(
        lambda data: data["entries"].append(
            {
                "id": "../not-part-of-the-archive",
                "title": "Sneaky",
                "archived_at": time.time(),
                "kind": KIND_SCRATCH,
            }
        )
    )

    assert [entry.id for entry in archive.entries()] == [note_id]
    assert not archive.delete("../not-part-of-the-archive")
    assert outside.is_file()


def test_deleting_removes_the_file_and_the_entry(xdg_home):
    note_id = store("done with this")
    archive.archive_note(note_id, "Note 1")

    assert archive.delete(note_id)
    assert archive.entries() == []
    assert archive.read(note_id) is None
    assert not archive.entry_path(note_id).is_file()
    # Deleting one that has already gone is not a failure.
    assert archive.delete(note_id)


def test_deleting_several_leaves_the_rest_alone(xdg_home):
    ids = []
    for number in range(3):
        note_id = store(f"note {number}")
        archive.archive_note(note_id, f"Note {number}")
        ids.append(note_id)

    assert archive.delete_many(ids[:2]) == 2

    assert [entry.id for entry in archive.entries()] == [ids[2]]
    assert json.loads(paths.archive_index().read_text())["entries"][0]["id"] == ids[2]


def test_a_traversing_id_is_refused_by_the_bulk_delete(xdg_home, tmp_path):
    """The same guard as the single delete, since this cannot be undone."""
    outsider = tmp_path / "important.txt"
    outsider.write_text("not Notemad's to remove")

    assert archive.delete_many(["../../../" + str(outsider)]) == 0
    assert outsider.exists()


def test_saving_the_index_drops_entries_whose_file_was_removed_by_hand(xdg_home):
    first = store("removed later")
    archive.archive_note(first, "Gone")
    archive.entry_path(first).unlink()

    archive.archive_note(store("archived after"), "Still here")

    data = json.loads(paths.archive_index().read_text(encoding="utf-8"))

    assert [entry["title"] for entry in data["entries"]] == ["Still here"]


def test_sizes_are_measured_from_the_files_rather_than_stored(xdg_home):
    """A number written into the index would be one waiting to go stale."""
    assert archive.usage() == archive.Usage(total=0, count=0)

    for number, text in enumerate(["a" * 100, "b" * 250], start=1):
        archive.archive_note(store(text), f"Note {number}")

    usage = archive.usage()

    assert usage.count == 2
    assert usage.total == 350
    assert sorted(entry.size for entry in archive.entries()) == [100, 250]
    assert "size" not in json.loads(paths.archive_index().read_text())["entries"][0]


def test_when_a_tab_was_last_looked_at_is_recorded(xdg_home):
    seen = store("read a while back")
    archive.archive_note(seen, "Note 1", last_viewed=1_600_000_000.0)

    never = store("from an older version")
    archive.archive_note(never, "Note 2")

    dates = {entry.id: entry.last_viewed for entry in archive.entries()}

    assert dates[seen] == 1_600_000_000.0
    assert dates[never] == 0.0


def test_stale_picks_by_the_closing_date_or_by_the_last_viewing(xdg_home):
    now = time.time()
    old = archive.ArchiveEntry(id="a", title="Old", archived_at=now - 100)
    recent = archive.ArchiveEntry(id="b", title="Recent", archived_at=now - 1)
    forgotten = archive.ArchiveEntry(
        id="c", title="Forgotten", archived_at=now - 1, last_viewed=now - 400
    )
    entries = [old, recent, forgotten]

    by_closed = archive.stale(entries, now - 50, by_last_viewed=False)
    by_viewed = archive.stale(entries, now - 50, by_last_viewed=True)

    assert [entry.id for entry in by_closed] == ["a"]
    # Closed a moment ago, but nobody had looked at it in a year. An entry with
    # no viewing falls back to the date it was closed.
    assert [entry.id for entry in by_viewed] == ["a", "c"]
