import json

from notemad.storage import session
from notemad.storage.session import SessionState, TabState, WindowState


def write_session_file(path, payload):
    """Put arbitrary text in the session file, valid or not."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(payload, str):
        path.write_text(payload, encoding="utf-8")
    else:
        path.write_text(json.dumps(payload), encoding="utf-8")


def test_round_trip(tmp_path):
    path = tmp_path / "session.json"
    original = SessionState(
        active_tab="second",
        window=WindowState(width=900, height=600, maximized=True),
        tabs=[
            TabState(id="first", title="Note 1", cursor_offset=42, scroll_offset=0.25),
            TabState(
                id="second",
                kind=session.KIND_FILE,
                path="/home/someone/notes.txt",
                title="notes.txt",
                has_draft=True,
                external_mtime=1234.5,
            ),
        ],
        active_tabs=["second", None],
    )

    session.save(original, path)

    assert session.load(path) == original


def test_saved_file_records_the_schema_version(tmp_path):
    path = tmp_path / "session.json"
    session.save(SessionState(), path)

    assert json.loads(path.read_text(encoding="utf-8"))["version"] == 1


def test_missing_file_gives_an_empty_session(tmp_path):
    loaded = session.load(tmp_path / "nothing.json")

    assert loaded == SessionState()
    assert loaded.tabs == []


def backups_in(directory):
    """The session files moved aside, whatever they were stamped with."""
    return sorted(directory.glob("session.json.*.bak"))


def test_empty_file_gives_an_empty_session(tmp_path):
    path = tmp_path / "session.json"
    write_session_file(path, "")

    assert session.load(path) == SessionState()
    assert backups_in(tmp_path) == []


def test_invalid_json_is_backed_up(tmp_path):
    path = tmp_path / "session.json"
    write_session_file(path, "{not json at all")

    assert session.load(path) == SessionState()
    assert backups_in(tmp_path)[0].read_text() == "{not json at all"
    assert not path.exists()


def test_top_level_list_is_rejected(tmp_path):
    path = tmp_path / "session.json"
    write_session_file(path, ["not", "an", "object"])

    assert session.load(path) == SessionState()
    assert backups_in(tmp_path)


def test_unknown_version_is_backed_up(tmp_path):
    path = tmp_path / "session.json"
    write_session_file(path, {"version": 99, "tabs": [{"id": "a", "kind": "scratch"}]})

    assert session.load(path) == SessionState()
    assert backups_in(tmp_path)


def test_missing_version_is_rejected(tmp_path):
    path = tmp_path / "session.json"
    write_session_file(path, {"tabs": []})

    assert session.load(path) == SessionState()


def test_undecodable_file_gives_an_empty_session(tmp_path):
    path = tmp_path / "session.json"
    path.write_bytes(b"\xff\xfe not utf-8")

    assert session.load(path) == SessionState()


def test_tabs_that_are_not_a_list_are_ignored(tmp_path):
    path = tmp_path / "session.json"
    write_session_file(path, {"version": 1, "tabs": "nonsense"})

    assert session.load(path).tabs == []


def test_one_bad_tab_does_not_discard_the_others(tmp_path):
    path = tmp_path / "session.json"
    write_session_file(
        path,
        {
            "version": 1,
            "tabs": [
                {"id": "good-one", "kind": "scratch"},
                {"kind": "scratch"},
                "not even a dict",
                {"id": "bad-kind", "kind": "something-else"},
                {"id": "file-without-path", "kind": "file"},
                {"id": "good-two", "kind": "file", "path": "/tmp/notes.txt"},
            ],
        },
    )

    assert [tab.id for tab in session.load(path).tabs] == ["good-one", "good-two"]


def test_duplicate_tab_ids_are_dropped(tmp_path):
    path = tmp_path / "session.json"
    write_session_file(
        path,
        {
            "version": 1,
            "tabs": [
                {"id": "same", "kind": "scratch", "title": "first"},
                {"id": "same", "kind": "scratch", "title": "second"},
            ],
        },
    )

    tabs = session.load(path).tabs
    assert len(tabs) == 1
    assert tabs[0].title == "first"


def test_wrong_field_types_fall_back_to_defaults(tmp_path):
    path = tmp_path / "session.json"
    write_session_file(
        path,
        {
            "version": 1,
            "tabs": [
                {
                    "id": "a",
                    "kind": "scratch",
                    "title": 12,
                    "cursor_offset": "not a number",
                    "scroll_offset": None,
                    "has_draft": "yes",
                    "external_mtime": "recently",
                }
            ],
        },
    )

    tab = session.load(path).tabs[0]
    assert tab.title == ""
    assert tab.cursor_offset == 0
    assert tab.scroll_offset == 0.0
    assert tab.has_draft is False
    assert tab.external_mtime is None


def test_negative_cursor_offset_is_clamped(tmp_path):
    path = tmp_path / "session.json"
    write_session_file(
        path,
        {"version": 1, "tabs": [{"id": "a", "kind": "scratch", "cursor_offset": -5}]},
    )

    assert session.load(path).tabs[0].cursor_offset == 0


def test_active_tab_falls_back_to_the_first_survivor(tmp_path):
    path = tmp_path / "session.json"
    write_session_file(
        path,
        {
            "version": 1,
            "active_tab": "a-tab-that-was-dropped",
            "tabs": [{"id": "a", "kind": "scratch"}, {"id": "b", "kind": "scratch"}],
        },
    )

    assert session.load(path).active_tab == "a"


def test_active_tab_is_none_when_there_are_no_tabs(tmp_path):
    path = tmp_path / "session.json"
    write_session_file(path, {"version": 1, "active_tab": "gone", "tabs": []})

    assert session.load(path).active_tab is None


def test_window_geometry_is_clamped_to_something_visible(tmp_path):
    path = tmp_path / "session.json"
    write_session_file(
        path,
        {"version": 1, "window": {"width": 0, "height": 0, "maximized": "sort of"}},
    )

    window = session.load(path).window
    assert window.width == session.MIN_WIDTH
    assert window.height == session.MIN_HEIGHT
    assert window.maximized is False


def test_missing_window_entry_uses_defaults(tmp_path):
    path = tmp_path / "session.json"
    write_session_file(path, {"version": 1, "tabs": []})

    assert session.load(path).window == WindowState()


def test_scratch_tab_never_keeps_a_path(tmp_path):
    path = tmp_path / "session.json"
    write_session_file(
        path,
        {
            "version": 1,
            "tabs": [{"id": "a", "kind": "scratch", "path": "/tmp/stray.txt"}],
        },
    )

    assert session.load(path).tabs[0].path is None


def test_save_uses_the_configured_location(xdg_home):
    from notemad.storage import paths

    session.save(SessionState(tabs=[TabState(id="a")]))

    assert paths.session_file().is_file()
    assert session.load().tabs[0].id == "a"


def test_split_and_panes_round_trip(tmp_path):
    path = tmp_path / "session.json"
    original = SessionState(
        active_tab="left",
        active_tabs=["left", "right"],
        tabs=[
            TabState(id="left", title="Note 1"),
            TabState(id="right", title="Note 2", pane=1),
        ],
        split=session.SplitState(enabled=True, position=0.4),
    )

    session.save(original, path)

    assert session.load(path) == original


def test_panes_collapse_when_the_split_is_off(xdg_home, tmp_path):
    path = tmp_path / "session.json"
    write_session_file(
        path,
        {
            "version": 1,
            "active_tab": "a",
            "split": {"enabled": False, "position": 0.5},
            "tabs": [
                {"id": "a", "kind": "scratch", "pane": 0},
                {"id": "b", "kind": "scratch", "pane": 1},
            ],
        },
    )

    loaded = session.load(path)

    assert [tab.pane for tab in loaded.tabs] == [0, 0]


def test_a_split_with_an_empty_second_pane_is_dropped(xdg_home, tmp_path):
    path = tmp_path / "session.json"
    write_session_file(
        path,
        {
            "version": 1,
            "active_tab": "a",
            "split": {"enabled": True, "position": 0.5},
            "tabs": [{"id": "a", "kind": "scratch", "pane": 0}],
        },
    )

    assert not session.load(path).split.enabled


def test_an_out_of_range_pane_falls_back_to_the_first(xdg_home, tmp_path):
    path = tmp_path / "session.json"
    write_session_file(
        path,
        {
            "version": 1,
            "active_tab": "a",
            "split": {"enabled": True, "position": 0.5},
            "tabs": [
                {"id": "a", "kind": "scratch", "pane": 7},
                {"id": "b", "kind": "scratch", "pane": 1},
            ],
        },
    )

    assert [tab.pane for tab in session.load(path).tabs] == [0, 1]


def test_the_divider_is_clamped_to_something_usable(xdg_home, tmp_path):
    path = tmp_path / "session.json"
    write_session_file(
        path,
        {
            "version": 1,
            "active_tab": "a",
            "split": {"enabled": True, "position": 0.99},
            "tabs": [
                {"id": "a", "kind": "scratch", "pane": 0},
                {"id": "b", "kind": "scratch", "pane": 1},
            ],
        },
    )

    assert session.load(path).split.position == session.MAX_SPLIT


def test_each_pane_gets_a_front_tab(xdg_home, tmp_path):
    path = tmp_path / "session.json"
    write_session_file(
        path,
        {
            "version": 1,
            "active_tab": "a",
            "active_tabs": ["gone", None],
            "split": {"enabled": True, "position": 0.5},
            "tabs": [
                {"id": "a", "kind": "scratch", "pane": 0},
                {"id": "b", "kind": "scratch", "pane": 1},
            ],
        },
    )

    assert session.load(path).active_tabs == ["a", "b"]


def test_a_tab_id_carrying_a_path_is_dropped(tmp_path):
    path = tmp_path / "session.json"
    write_session_file(
        path,
        {
            "version": 1,
            "tabs": [
                {"id": "../../escape", "kind": "scratch"},
                {"id": "legitimate", "kind": "scratch"},
            ],
        },
    )

    loaded = session.load(path)

    assert [tab.id for tab in loaded.tabs] == ["legitimate"]


def test_an_impossible_window_size_falls_back_to_something_usable(tmp_path):
    path = tmp_path / "session.json"
    write_session_file(
        path,
        {"version": 1, "tabs": [], "window": {"width": 99999999999, "height": 1}},
    )

    loaded = session.load(path)

    assert loaded.window.width == session.MAX_WIDTH
    assert loaded.window.height == session.MIN_HEIGHT


def test_not_a_number_is_no_answer_at_all(tmp_path):
    path = tmp_path / "session.json"
    # NaN and Infinity are not valid JSON, but Python's parser accepts them.
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        '{"version": 1, "tabs": [{"id": "a", "kind": "scratch", '
        '"scroll_offset": Infinity, "external_mtime": NaN}], '
        '"split": {"enabled": true, "position": NaN}}',
        encoding="utf-8",
    )

    loaded = session.load(path)

    assert loaded.split.position == 0.5
    assert loaded.tabs[0].scroll_offset == 0.0
    assert loaded.tabs[0].external_mtime is None


def test_when_a_tab_was_last_looked_at_round_trips(tmp_path):
    path = tmp_path / "session.json"
    state = session.SessionState(
        tabs=[session.TabState(id="a", title="Note 1", last_viewed=1_600_000_000.0)]
    )
    session.save(state, path)

    assert session.load(path).tabs[0].last_viewed == 1_600_000_000.0


def test_a_tab_from_before_viewing_was_recorded_has_no_such_date(tmp_path):
    path = tmp_path / "session.json"
    path.write_text(
        '{"version": 1, "tabs": [{"id": "a", "kind": "scratch"}]}', encoding="utf-8"
    )

    assert session.load(path).tabs[0].last_viewed == 0.0


def test_a_nonsense_viewing_date_is_dropped(tmp_path):
    path = tmp_path / "session.json"
    path.write_text(
        '{"version": 1, "tabs": [{"id": "a", "kind": "scratch", '
        '"last_viewed": NaN}, {"id": "b", "kind": "scratch", '
        '"last_viewed": -12}]}',
        encoding="utf-8",
    )

    loaded = session.load(path)

    assert loaded.tabs[0].last_viewed == 0.0
    assert loaded.tabs[1].last_viewed == 0.0
