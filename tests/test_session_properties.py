"""Property tests for session loading.

The loader is deliberately forgiving: anything it cannot understand is dropped
or replaced with a default, and it never raises. That makes round-trip identity
the wrong property, because load() legitimately changes what it was given. What
does hold is that every session it returns satisfies the same set of invariants,
whatever it was fed, and that a second pass changes nothing further.
"""

import json
import tempfile
from pathlib import Path

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from notemad.storage import session

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Values that turn up in a JSON file where a number or a string belongs.
junk = (
    st.none()
    | st.booleans()
    | st.integers()
    | st.floats(allow_nan=True, allow_infinity=True)
    | st.text(max_size=20)
    | st.lists(st.integers(), max_size=3)
    | st.dictionaries(st.text(max_size=5), st.integers(), max_size=3)
)

ids = st.uuids().map(str)

# Ids that must never survive: path separators, traversal, nulls.
unsafe_ids = st.sampled_from(
    [
        "../escape",
        "../../etc/passwd",
        "sub/dir",
        "back\\slash",
        "null\x00byte",
        "",
        ".",
        "..",
    ]
)


@st.composite
def tab_dicts(draw):
    """A tab entry: mostly plausible, occasionally junk in any given field."""
    return {
        "id": draw(ids | unsafe_ids | junk),
        "kind": draw(st.sampled_from([session.KIND_SCRATCH, session.KIND_FILE]) | junk),
        "path": draw(st.none() | st.text(max_size=30) | junk),
        "title": draw(st.text(max_size=30) | junk),
        "cursor_offset": draw(st.integers(min_value=-1000, max_value=10000) | junk),
        "scroll_offset": draw(
            st.floats(allow_nan=True, allow_infinity=True, width=32) | junk
        ),
        "has_draft": draw(st.booleans() | junk),
        "external_mtime": draw(st.floats(allow_nan=True, allow_infinity=True) | junk),
        "pane": draw(st.integers(min_value=-5, max_value=5) | junk),
        "last_viewed": draw(st.floats(allow_nan=True, allow_infinity=True) | junk),
    }


@st.composite
def session_dicts(draw):
    """A session file that gets past the version check, so the parser runs.

    Random JSON is nearly useless here: load() rejects anything whose version is
    not SCHEMA_VERSION and returns a default, so the interesting code is never
    reached. Pinning the version is what makes these tests exercise the parser.
    """
    return {
        "version": session.SCHEMA_VERSION,
        "active_tab": draw(st.none() | ids | junk),
        "active_tabs": draw(st.lists(st.none() | ids | junk, max_size=4) | junk),
        "window": draw(
            st.fixed_dictionaries(
                {
                    "width": st.integers(min_value=-100, max_value=100_000) | junk,
                    "height": st.integers(min_value=-100, max_value=100_000) | junk,
                    "maximized": st.booleans() | junk,
                }
            )
            | junk
        ),
        "split": draw(
            st.fixed_dictionaries(
                {
                    "enabled": st.booleans() | junk,
                    "position": st.floats(allow_nan=True, allow_infinity=True) | junk,
                }
            )
            | junk
        ),
        "tabs": draw(st.lists(tab_dicts(), max_size=6) | junk),
    }


# ---------------------------------------------------------------------------
# Invariants
# ---------------------------------------------------------------------------


def assert_invariants(state: session.SessionState) -> None:
    """Everything that must be true of a session, however it was loaded."""
    assert isinstance(state, session.SessionState)

    ids_present = [tab.id for tab in state.tabs]
    assert len(ids_present) == len(set(ids_present)), "duplicate tab ids survived"

    for tab in state.tabs:
        assert isinstance(tab.id, str) and tab.id
        assert tab.kind in session.VALID_KINDS
        assert 0 <= tab.pane < session.PANE_COUNT
        assert isinstance(tab.title, str)
        assert isinstance(tab.cursor_offset, int) and tab.cursor_offset >= 0
        assert isinstance(tab.scroll_offset, float)
        assert tab.scroll_offset == tab.scroll_offset  # not NaN
        assert tab.last_viewed >= 0.0
        if tab.kind == session.KIND_FILE:
            assert tab.path, "a file tab with no path is unusable"
        else:
            assert tab.path is None, "a scratch note must not carry a path"
        if tab.external_mtime is not None:
            assert tab.external_mtime == tab.external_mtime

    window = state.window
    assert session.MIN_WIDTH <= window.width <= session.MAX_WIDTH
    assert session.MIN_HEIGHT <= window.height <= session.MAX_HEIGHT
    assert isinstance(window.maximized, bool)

    assert session.MIN_SPLIT <= state.split.position <= session.MAX_SPLIT
    assert isinstance(state.split.enabled, bool)
    if state.split.enabled:
        assert any(tab.pane == 1 for tab in state.tabs), (
            "a split with an empty second pane leaves half the window unusable"
        )

    if state.tabs:
        assert state.active_tab in ids_present
    else:
        assert state.active_tab is None

    assert len(state.active_tabs) == session.PANE_COUNT
    for pane, chosen in enumerate(state.active_tabs):
        in_pane = [tab.id for tab in state.tabs if tab.pane == pane]
        if chosen is None:
            assert not in_pane, "a pane with tabs in it must have one in front"
        else:
            assert chosen in in_pane


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(payload=session_dicts())
def test_invariants_hold_for_any_session_file(payload, tmp_path):
    target = tmp_path / "session.json"
    target.write_text(json.dumps(payload), encoding="utf-8")
    assert_invariants(session.load(target))


@settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(raw=st.binary(max_size=400))
def test_load_never_raises_on_arbitrary_bytes(raw, tmp_path):
    target = tmp_path / "session.json"
    target.write_bytes(raw)
    assert_invariants(session.load(target))


@settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(text=st.text(max_size=400))
def test_load_never_raises_on_arbitrary_text(text, tmp_path):
    target = tmp_path / "session.json"
    target.write_text(text, encoding="utf-8")
    assert_invariants(session.load(target))


@settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(payload=session_dicts())
def test_normalisation_is_idempotent(payload, tmp_path):
    """A second save and load must change nothing.

    If it does, the loader is still rewriting state it has already accepted,
    which means the session drifts a little on every restart.
    """
    target = tmp_path / "session.json"
    target.write_text(json.dumps(payload), encoding="utf-8")

    once = session.load(target)
    session.save(once, target)
    twice = session.load(target)

    assert session.to_dict(once) == session.to_dict(twice)


@settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(payload=session_dicts())
def test_saved_sessions_survive_a_round_trip(payload, tmp_path):
    """Whatever load() accepts, save() must be able to write and read back."""
    target = tmp_path / "session.json"
    target.write_text(json.dumps(payload), encoding="utf-8")

    original = session.load(target)
    session.save(original, target)
    assert_invariants(session.load(target))


@settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(bad_id=unsafe_ids)
def test_unsafe_ids_never_survive(bad_id, tmp_path):
    """Regression net: a tab id becomes a filename, so it must not escape."""
    target = tmp_path / "session.json"
    target.write_text(
        json.dumps(
            {
                "version": session.SCHEMA_VERSION,
                "tabs": [{"id": bad_id, "kind": session.KIND_SCRATCH}],
            }
        ),
        encoding="utf-8",
    )
    assert session.load(target).tabs == []


@settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(
    width=st.floats(allow_nan=True, allow_infinity=True) | st.integers(),
    height=st.floats(allow_nan=True, allow_infinity=True) | st.integers(),
)
def test_geometry_is_always_a_window_that_can_open(width, height, tmp_path):
    target = tmp_path / "session.json"
    target.write_text(
        json.dumps(
            {
                "version": session.SCHEMA_VERSION,
                "tabs": [],
                "window": {"width": width, "height": height},
            }
        ),
        encoding="utf-8",
    )
    assert_invariants(session.load(target))


@given(version=junk)
def test_an_unrecognised_version_is_backed_up_not_lost(version):
    # Counts the backups in the directory, so every example needs an empty one.
    # tmp_path is function scoped and would carry the previous example's copies.
    with tempfile.TemporaryDirectory() as directory:
        target = Path(directory) / "session.json"
        target.write_text(
            json.dumps({"version": version, "tabs": []}), encoding="utf-8"
        )

        state = session.load(target)
        assert_invariants(state)

        if version != session.SCHEMA_VERSION:
            backups = list(Path(directory).glob("session.json.*.bak"))
            assert backups, "an unreadable session was discarded without a copy"


@settings(max_examples=25)
@given(payloads=st.lists(session_dicts(), min_size=2, max_size=4))
def test_repeated_bad_loads_keep_every_backup(payloads):
    """Each failed load must keep its own copy.

    The stamp in _backup() has one-second resolution, so two failures inside the
    same second write to the same name and the earlier copy is lost. A crash
    loop is exactly when that happens and exactly when the backup matters.
    """
    # The count only means anything in a directory this example owns.
    with tempfile.TemporaryDirectory() as directory:
        target = Path(directory) / "session.json"
        for index, payload in enumerate(payloads):
            payload["version"] = f"not-a-version-{index}"
            target.write_text(json.dumps(payload), encoding="utf-8")
            session.load(target)

        backups = list(Path(directory).glob("session.json.*.bak"))
        assert len(backups) == len(payloads)
