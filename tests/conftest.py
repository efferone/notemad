import pytest


@pytest.fixture
def xdg_home(tmp_path, monkeypatch):
    """Point the XDG base directories at a throwaway location."""
    for variable in ("XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CONFIG_HOME"):
        monkeypatch.setenv(variable, str(tmp_path / variable.lower()))
    return tmp_path
