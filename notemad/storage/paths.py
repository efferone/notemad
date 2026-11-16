"""XDG data, state and config directory resolution for Notemad."""

import os
from pathlib import Path

APP_NAME = "notemad"

# Notes can contain anything, so nothing we create is readable by other users.
DIR_MODE = 0o700


def _base_dir(env_var: str, fallback: Path) -> Path:
    value = os.environ.get(env_var)
    # The XDG spec says a relative path in one of these variables is invalid
    # and should be treated as though the variable were unset.
    if value and os.path.isabs(value):
        return Path(value)
    return fallback


def data_home() -> Path:
    return _base_dir("XDG_DATA_HOME", Path.home() / ".local" / "share") / APP_NAME


def state_home() -> Path:
    return _base_dir("XDG_STATE_HOME", Path.home() / ".local" / "state") / APP_NAME


def config_home() -> Path:
    return _base_dir("XDG_CONFIG_HOME", Path.home() / ".config") / APP_NAME


def notes_dir() -> Path:
    return data_home() / "notes"


def archive_dir() -> Path:
    return data_home() / "archive"


def archive_index() -> Path:
    return archive_dir() / "index.json"


def drafts_dir() -> Path:
    return state_home() / "drafts"


def session_file() -> Path:
    return state_home() / "session.json"


def config_file() -> Path:
    return config_home() / "config.json"


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    # mkdir's mode argument gets masked by the umask, so set the permissions
    # afterwards. The XDG directories above are shared with every other
    # application and are left alone.
    path.chmod(DIR_MODE)
    if path.parent.name == APP_NAME:
        path.parent.chmod(DIR_MODE)
    return path


def is_safe_id(value: str) -> bool:
    """Whether a stored identifier can be used as a file name on its own.

    Ids come back from files that could have been edited by hand, and every one
    ends up as part of a path, so anything carrying a directory is refused.
    """
    # A backslash is a legal filename character here, so the check below lets it
    # through, but an id is written by us and a backslash in one means the file
    # has been tampered with or came from somewhere that treats it as a
    # separator.
    if not value or value in (".", "..") or "\0" in value or "\\" in value:
        return False
    # A name with a directory in it comes back from this different to how it
    # went in.
    return Path(value).name == value
