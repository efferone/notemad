"""User preferences, loaded from and saved to config.json with defaults."""

import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path

from .storage import paths
from .storage.atomic import atomic_write_text

logger = logging.getLogger(__name__)


DEFAULT_FONT_FAMILY = "Monospace"
DEFAULT_FONT_SIZE = 11
MIN_FONT_SIZE = 6
MAX_FONT_SIZE = 72

# The sizes offered for the archive warning, and the one it starts on. Zero
# turns the warning off without hiding the figure itself.
ARCHIVE_WARN_CHOICES = (
    0,
    100 * 1024 * 1024,
    250 * 1024 * 1024,
    500 * 1024 * 1024,
    1024 * 1024 * 1024,
)
DEFAULT_ARCHIVE_WARN = 250 * 1024 * 1024


@dataclass
class Config:
    theme: str = "dark"
    # The last theme of each kind that was chosen, so the header button flips
    # between the two you actually use rather than always the default pair.
    last_dark: str = "dark"
    last_light: str = "light"
    font_family: str = DEFAULT_FONT_FAMILY
    font_size: int = DEFAULT_FONT_SIZE
    # How large the archive is allowed to get before the status bar says so.
    archive_warn_bytes: int = DEFAULT_ARCHIVE_WARN


def load(path: Path | None = None) -> Config:
    """Read the stored preferences, falling back to the defaults on any problem.

    Preferences are not worth refusing to start over, so nothing here raises.
    """
    source = path or paths.config_file()

    try:
        raw = source.read_text(encoding="utf-8")
    except FileNotFoundError:
        return Config()
    except (OSError, UnicodeDecodeError) as exc:
        logger.warning("could not read config file: %s", exc)
        return Config()

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning("config file is not valid JSON: %s", exc)
        return Config()

    if not isinstance(data, dict):
        logger.warning("config file does not contain an object")
        return Config()

    config = Config()
    theme = data.get("theme")
    if isinstance(theme, str):
        config.theme = theme

    for field in ("last_dark", "last_light"):
        value = data.get(field)
        if isinstance(value, str) and value:
            setattr(config, field, value)

    family = data.get("font_family")
    if isinstance(family, str) and family.strip():
        config.font_family = family.strip()

    size = data.get("font_size")
    # bool is an int in Python, and a font size of True helps nobody.
    if isinstance(size, int) and not isinstance(size, bool):
        config.font_size = clamp_font_size(size)

    warn = data.get("archive_warn_bytes")
    if isinstance(warn, int) and not isinstance(warn, bool) and warn >= 0:
        config.archive_warn_bytes = warn

    return config


def clamp_font_size(size: int) -> int:
    return min(max(size, MIN_FONT_SIZE), MAX_FONT_SIZE)


def save(config: Config, path: Path | None = None) -> None:
    target = path or paths.config_file()
    paths.ensure_dir(target.parent)
    atomic_write_text(target, json.dumps(asdict(config), indent=2) + "\n")
