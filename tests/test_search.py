import os
import time

from notemad.storage import archive, notes, paths, search, session
from notemad.storage.session import KIND_FILE, SessionState, TabState


def store(title: str, text: str) -> str:
    """Write a note and record its title in the session, the way the app does."""
    note_id = notes.new_id()
    notes.write_note(note_id, text)

    state = session.load()
    state.tabs.append(TabState(id=note_id, title=title))
    session.save(state)
    return note_id


def close(note_id: str, title: str) -> None:
    archive.archive_note(note_id, title)
    state = session.load()
    state.tabs = [tab for tab in state.tabs if tab.id != note_id]
    session.save(state)


def open_file(path, text: str, draft: str | None = None) -> str:
    """Record an open file tab in the session, the way opening one does."""
    path.write_text(text, encoding="utf-8")

    note_id = notes.new_id()
    if draft is not None:
        notes.write_draft(note_id, draft)

    state = session.load()
    state.tabs.append(
        TabState(
            id=note_id,
            kind=KIND_FILE,
            path=str(path),
            title=path.name,
            has_draft=draft is not None,
        )
    )
    session.save(state)
    return note_id


def test_finds_a_note_and_says_where_the_hit_is(xdg_home):
    store("Homelab", "first line\nthe proxmox box\nthird line")
    store("Shopping", "milk, bread, coffee")

    results = search.search("proxmox")

    assert len(results) == 1
    assert results[0].title == "Homelab"
    assert not results[0].archived
    # A scratch note lives nowhere outside Notemad.
    assert results[0].path is None
    assert search.read(results[0]) == "first line\nthe proxmox box\nthird line"

    match = results[0].matches[0]
    assert match.line == 2
    assert match.text == "the proxmox box"
    # Eleven characters of first line plus its newline, then "the ".
    assert match.offset == len("first line\n") + 4


def test_matching_ignores_case_unless_asked(xdg_home):
    store("Homelab", "The Proxmox Box")

    assert search.search("proxmox")
    assert not search.search("proxmox", case_sensitive=True)
    assert search.search("Proxmox", case_sensitive=True)


def test_an_empty_query_finds_nothing_rather_than_everything(xdg_home):
    store("Homelab", "the proxmox box")

    assert search.search("") == []
    assert search.search("   ") == []


def test_how_hits_within_one_note_are_reported(xdg_home):
    """One per line, however many times the line says it, and trimmed to fit."""
    store("Homelab", "proxmox one\nnothing here\nproxmox proxmox proxmox")
    store("Wide", "proxmox " + "x" * 500)

    results = {result.title: result for result in search.search("proxmox")}

    assert [match.line for match in results["Homelab"].matches] == [1, 3]

    text = results["Wide"].matches[0].text
    assert len(text) == search.LINE_PREVIEW_LENGTH + 3
    assert text.endswith("...")


def test_closed_notes_are_left_out_until_they_are_asked_for(xdg_home):
    note_id = store("Old Homelab", "the proxmox box")
    close(note_id, "Old Homelab")

    assert search.search("proxmox") == []

    results = search.search("proxmox", include_archive=True)
    assert len(results) == 1
    assert results[0].archived
    assert results[0].title == "Old Homelab"
    assert search.read(results[0]) == "the proxmox box"

    # The index is JSON sitting in the same directory, and it names the note.
    assert paths.archive_index().is_file()
    assert search.search("Old Homelab", include_archive=True) == []


def test_open_notes_come_first_and_then_the_most_recently_touched(xdg_home):
    closed = store("Closed", "the proxmox box")
    close(closed, "Closed")
    time.sleep(0.01)
    older = store("Older", "the proxmox box")
    newer = store("Newer", "the proxmox box")

    # Set the times explicitly rather than racing the clock.
    os.utime(notes.note_path(older), (1000, 1000))
    os.utime(notes.note_path(newer), (2000, 2000))

    results = search.search("proxmox", include_archive=True)

    assert [result.title for result in results] == ["Newer", "Older", "Closed"]


def test_a_note_with_no_session_entry_is_still_found(xdg_home):
    """An orphaned note has no title anywhere, but it still has content."""
    note_id = notes.new_id()
    notes.write_note(note_id, "the proxmox box")
    session.save(SessionState())

    results = search.search("proxmox")

    assert len(results) == 1
    assert results[0].id == note_id
    assert results[0].title == ""


def test_what_the_search_passes_over(xdg_home, tmp_path):
    """Too big, not text, and gone from underneath us: none of them stop it."""
    store("Readable", "the proxmox box")

    too_big = notes.new_id()
    notes.write_note(too_big, "proxmox")
    # Grown past the limit without writing megabytes of test data.
    with notes.note_path(too_big).open("a", encoding="utf-8") as handle:
        handle.truncate(search.MAX_SEARCH_BYTES + 1)

    not_text = notes.new_id()
    notes.write_note(not_text, "placeholder")
    notes.note_path(not_text).write_bytes(b"\xff\xfe proxmox")

    removed = tmp_path / "hosts.txt"
    open_file(removed, "the proxmox box")
    removed.unlink()

    assert [result.title for result in search.search("proxmox")] == ["Readable"]


def test_searching_when_there_is_nothing_stored(xdg_home):
    assert search.search("proxmox", include_archive=True) == []

    paths.ensure_dir(paths.notes_dir())
    assert search.search("proxmox") == []


def test_a_file_is_searched_through_whatever_is_on_screen(xdg_home, tmp_path):
    """Its draft when it has one, since that is the version being looked at."""
    plain = tmp_path / "hosts.txt"
    open_file(plain, "127.0.0.1 localhost\nthe proxmox box")

    edited = tmp_path / "notes.txt"
    open_file(edited, "nothing here yet", draft="nothing here yet\nthe proxmox box")

    results = {result.title: result for result in search.search("proxmox")}

    assert set(results) == {"hosts.txt", "notes.txt"}
    # Both still reported as the file, since that is what they belong to.
    assert results["hosts.txt"].path == str(plain)
    assert results["hosts.txt"].matches[0].line == 2
    assert search.read(results["hosts.txt"]) == "127.0.0.1 localhost\nthe proxmox box"

    assert results["notes.txt"].path == str(edited)
    assert search.read(results["notes.txt"]) == "nothing here yet\nthe proxmox box"


def test_notes_and_files_come_back_together(xdg_home, tmp_path):
    store("Homelab", "the proxmox box")
    open_file(tmp_path / "hosts.txt", "the proxmox box")

    results = search.search("proxmox")

    assert {result.title for result in results} == {"Homelab", "hosts.txt"}


def test_offsets_survive_characters_that_change_length_when_lowered(xdg_home):
    # "İ".lower() is two characters, so lowering a line to compare it moves
    # every offset after it along by one.
    note_id = notes.new_id()
    notes.write_note(note_id, "İİİ hello")

    result = search.search("hello")[0]
    match = result.matches[0]

    assert notes.read_note(note_id)[match.offset : match.offset + 5] == "hello"
