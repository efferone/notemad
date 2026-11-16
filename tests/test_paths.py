import stat
from pathlib import Path

from notemad.storage import notes, paths


def test_the_xdg_variables_are_honoured(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))

    assert paths.data_home() == tmp_path / "data" / "notemad"
    assert paths.state_home() == tmp_path / "state" / "notemad"
    assert paths.config_home() == tmp_path / "config" / "notemad"


def test_anything_but_an_absolute_path_falls_back(tmp_path, monkeypatch):
    """The XDG spec says a relative value is invalid and means unset."""
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    expected = {
        paths.data_home: tmp_path / ".local" / "share" / "notemad",
        paths.state_home: tmp_path / ".local" / "state" / "notemad",
        paths.config_home: tmp_path / ".config" / "notemad",
    }
    variables = ("XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CONFIG_HOME")

    for value in (None, "relative/path", ""):
        for name in variables:
            if value is None:
                monkeypatch.delenv(name, raising=False)
            else:
                monkeypatch.setenv(name, value)
        for resolve, fallback in expected.items():
            assert resolve() == fallback, value


def test_subdirectories_hang_off_the_right_roots(xdg_home):
    assert paths.notes_dir().parent == paths.data_home()
    assert paths.archive_dir().parent == paths.data_home()
    assert paths.archive_index().parent == paths.archive_dir()
    assert paths.drafts_dir().parent == paths.state_home()
    assert paths.session_file() == paths.state_home() / "session.json"
    assert paths.config_file() == paths.config_home() / "config.json"


def test_ensure_dir_makes_private_directories_and_can_be_called_again(xdg_home):
    created = paths.ensure_dir(paths.notes_dir())
    paths.ensure_dir(paths.notes_dir())

    assert created.is_dir()
    assert stat.S_IMODE(created.stat().st_mode) == 0o700
    # The application directory above it is tightened too, since mkdir's mode
    # argument is masked by the umask.
    assert stat.S_IMODE(paths.data_home().stat().st_mode) == 0o700


def test_which_identifiers_can_stand_as_a_file_name():
    for safe in (notes.new_id(), "Note 1", "a.txt", "note-1_2"):
        assert paths.is_safe_id(safe), safe

    for unsafe in (
        "../escape",
        "../../etc/passwd",
        "/etc/passwd",
        "notes/nested",
        "..",
        ".",
        "",
        "with\0null",
    ):
        assert not paths.is_safe_id(unsafe), unsafe
