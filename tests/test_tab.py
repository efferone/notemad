from notemad.storage import notes
from notemad.storage.session import TabState
from notemad.tab import (
    AUTOSAVE_DELAY_MS,
    LARGE_BUFFER_CHARS,
    MAX_AUTOSAVE_DELAY_MS,
    Tab,
    autosave_delay,
)


def test_a_normal_note_is_written_out_promptly():
    assert autosave_delay(0) == AUTOSAVE_DELAY_MS
    assert autosave_delay(10_000) == AUTOSAVE_DELAY_MS
    assert autosave_delay(LARGE_BUFFER_CHARS) == AUTOSAVE_DELAY_MS


def test_a_large_buffer_is_written_out_less_often():
    assert autosave_delay(LARGE_BUFFER_CHARS * 4) == AUTOSAVE_DELAY_MS * 4
    assert autosave_delay(LARGE_BUFFER_CHARS * 1000) == MAX_AUTOSAVE_DELAY_MS


def test_a_failed_write_is_reported_rather_than_raised(xdg_home, monkeypatch):
    """A note that cannot be written out has to say so, not fail silently."""

    def refuse(note_id, text):
        raise OSError(28, "No space left on device")

    told = []
    tab = Tab.new_scratch("Note 1")
    tab.on_save_failed = told.append
    # Patched after the tab exists, so it is the autosave that meets the full
    # disk rather than the note being created.
    monkeypatch.setattr(notes, "write_note", refuse)
    tab.buffer.set_text("something worth keeping")
    tab.flush()

    assert told == ["Note 1"]


def test_a_new_tab_counts_as_looked_at(xdg_home):
    tab = Tab.new_scratch("Note 1")

    assert tab.last_viewed > 0


def test_restoring_a_tab_is_not_looking_at_it(xdg_home):
    """Ten tabs come back on every launch. Only the one in front was seen."""
    tab = Tab.from_state(
        TabState(id="abc", title="Note 1", last_viewed=1_600_000_000.0)
    )

    assert tab.last_viewed == 1_600_000_000.0

    tab.viewed()
    assert tab.last_viewed > 1_600_000_000.0
