"""Session state serialisation and defensive loading."""

import json
import logging
import math
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import paths
from .atomic import atomic_write_text

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1

KIND_SCRATCH = "scratch"
KIND_FILE = "file"
VALID_KINDS = (KIND_SCRATCH, KIND_FILE)

DEFAULT_WIDTH = 1100
DEFAULT_HEIGHT = 750
MIN_WIDTH = 400
MIN_HEIGHT = 300
# Past this a stored size is not a window anybody has ever had. X11 will not
# take a dimension larger than this, and GTK refuses one outright rather than
# clamping it, which would leave the window unable to open at all.
MAX_WIDTH = 32767
MAX_HEIGHT = 32767

# Two panes side by side, so a tab belongs to one or the other.
PANE_COUNT = 2

# How far the divider can be dragged before one side is too narrow to use.
MIN_SPLIT = 0.15
MAX_SPLIT = 0.85


@dataclass
class WindowState:
    width: int = DEFAULT_WIDTH
    height: int = DEFAULT_HEIGHT
    maximized: bool = False


@dataclass
class SplitState:
    enabled: bool = False
    # Where the divider sits, as a fraction of the window width.
    position: float = 0.5


@dataclass
class TabState:
    id: str
    kind: str = KIND_SCRATCH
    path: str | None = None
    title: str = ""
    cursor_offset: int = 0
    scroll_offset: float = 0.0
    has_draft: bool = False
    external_mtime: float | None = None
    pane: int = 0
    # When this tab was last brought to the front, which is the only record of
    # a note having been looked at rather than merely kept open.
    last_viewed: float = 0.0


@dataclass
class SessionState:
    active_tab: str | None = None
    window: WindowState = field(default_factory=WindowState)
    tabs: list[TabState] = field(default_factory=list)
    split: SplitState = field(default_factory=SplitState)
    # The tab in front in each pane. The one with the keyboard is active_tab.
    active_tabs: list[str | None] = field(default_factory=lambda: [None] * PANE_COUNT)


def to_dict(session: SessionState) -> dict:
    return {
        "version": SCHEMA_VERSION,
        "active_tab": session.active_tab,
        "active_tabs": list(session.active_tabs),
        "window": asdict(session.window),
        "split": asdict(session.split),
        "tabs": [asdict(tab) for tab in session.tabs],
    }


def save(session: SessionState, path: Path | None = None) -> None:
    target = path or paths.session_file()
    paths.ensure_dir(target.parent)
    atomic_write_text(target, json.dumps(to_dict(session), indent=2) + "\n")


def load(path: Path | None = None) -> SessionState:
    """Read the stored session, falling back to an empty one on any problem.

    Nothing in here raises. A session file that cannot be understood is worth
    losing, but it is never worth refusing to start over.
    """
    source = path or paths.session_file()

    try:
        raw = source.read_text(encoding="utf-8")
    except FileNotFoundError:
        return SessionState()
    except (OSError, UnicodeDecodeError) as exc:
        logger.warning("could not read session file: %s", exc)
        return SessionState()

    if not raw.strip():
        return SessionState()

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning("session file is not valid JSON: %s", exc)
        _backup(source)
        return SessionState()

    if not isinstance(data, dict):
        logger.warning("session file does not contain an object")
        _backup(source)
        return SessionState()

    version = data.get("version")
    if version != SCHEMA_VERSION:
        logger.warning("unknown session schema version %r, starting fresh", version)
        _backup(source)
        return SessionState()

    return _session_from_dict(data)


def _backup(path: Path) -> None:
    """Move a session file aside rather than silently overwriting it.

    The stamp has one second resolution, so a crash loop would put every copy
    at the same name and keep only the last. A free name is found instead,
    because the first copy is the one still holding the tabs.
    """
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target = path.with_name(f"{path.name}.{stamp}.bak")
    attempt = 2
    while target.exists():
        target = path.with_name(f"{path.name}.{stamp}-{attempt}.bak")
        attempt += 1

    try:
        os.replace(path, target)
    except OSError as exc:
        logger.warning("could not back up session file: %s", exc)


def _session_from_dict(data: dict) -> SessionState:
    raw_tabs = data.get("tabs")
    if not isinstance(raw_tabs, list):
        logger.warning("session tabs are not a list, ignoring them")
        raw_tabs = []

    tabs: list[TabState] = []
    seen: set[str] = set()
    for entry in raw_tabs:
        tab = _tab_from_dict(entry)
        if tab is None:
            logger.warning("dropping unreadable tab entry")
            continue
        # Two tabs sharing an id would write over each other's note file.
        if tab.id in seen:
            logger.warning("dropping duplicate tab id %s", tab.id)
            continue
        seen.add(tab.id)
        tabs.append(tab)

    split = _split_from_dict(data.get("split"))

    # Without a second pane on screen there is nowhere for a tab to be but the
    # first one, whatever the file says.
    if not split.enabled:
        for tab in tabs:
            tab.pane = 0
    elif not any(tab.pane == 1 for tab in tabs):
        logger.warning("session claims a split with nothing in the second pane")
        split.enabled = False

    active = _as_str_or_none(data.get("active_tab"))
    # An active tab naming an entry that did not survive parsing would leave the
    # window with nothing selected.
    if active not in seen:
        active = tabs[0].id if tabs else None

    return SessionState(
        active_tab=active,
        active_tabs=_active_tabs_from_dict(data.get("active_tabs"), tabs, active),
        window=_window_from_dict(data.get("window")),
        tabs=tabs,
        split=split,
    )


def _active_tabs_from_dict(
    data: object, tabs: list[TabState], active: str | None
) -> list[str | None]:
    """Work out which tab is in front in each pane, dropping any that are gone."""
    stored = data if isinstance(data, list) else []
    selected: list[str | None] = []

    for pane in range(PANE_COUNT):
        in_pane = [tab.id for tab in tabs if tab.pane == pane]
        candidate = _as_str_or_none(stored[pane]) if pane < len(stored) else None

        if candidate not in in_pane:
            # Prefer the tab that had the keyboard, then simply the first one.
            candidate = active if active in in_pane else None
        if candidate is None and in_pane:
            candidate = in_pane[0]
        selected.append(candidate)

    return selected


def _split_from_dict(data: object) -> SplitState:
    if not isinstance(data, dict):
        return SplitState()

    position = _as_float(data.get("position"), 0.5)
    return SplitState(
        enabled=_as_bool(data.get("enabled")),
        position=min(max(position, MIN_SPLIT), MAX_SPLIT),
    )


def _tab_from_dict(data: object) -> TabState | None:
    """Build a tab from stored data, or None if it is beyond saving.

    Individual fields fall back to their defaults. Only a missing identity or a
    file tab with no path makes an entry unusable.
    """
    if not isinstance(data, dict):
        return None

    tab_id = data.get("id")
    if not isinstance(tab_id, str) or not paths.is_safe_id(tab_id):
        return None

    kind = data.get("kind")
    if kind not in VALID_KINDS:
        return None

    path = _as_str_or_none(data.get("path"))
    if kind == KIND_FILE and not path:
        return None

    title = data.get("title")

    return TabState(
        id=tab_id,
        kind=kind,
        path=path if kind == KIND_FILE else None,
        title=title if isinstance(title, str) else "",
        cursor_offset=max(_as_int(data.get("cursor_offset"), 0), 0),
        scroll_offset=_as_float(data.get("scroll_offset"), 0.0),
        has_draft=_as_bool(data.get("has_draft")),
        external_mtime=_as_float_or_none(data.get("external_mtime")),
        pane=_as_pane(data.get("pane")),
        last_viewed=max(_as_float(data.get("last_viewed"), 0.0), 0.0),
    )


def _as_pane(value: object) -> int:
    pane = _as_int(value, 0)
    return pane if 0 <= pane < PANE_COUNT else 0


def _window_from_dict(data: object) -> WindowState:
    if not isinstance(data, dict):
        return WindowState()

    # A stored geometry of zero would restore a window nobody can see, and one
    # larger than GTK accepts would stop the window opening at all.
    return WindowState(
        width=_clamp(_as_int(data.get("width"), DEFAULT_WIDTH), MIN_WIDTH, MAX_WIDTH),
        height=_clamp(
            _as_int(data.get("height"), DEFAULT_HEIGHT), MIN_HEIGHT, MAX_HEIGHT
        ),
        maximized=_as_bool(data.get("maximized")),
    )


def _clamp(value: int, lowest: int, highest: int) -> int:
    return min(max(value, lowest), highest)


# JSON gives back whatever was in the file, including booleans where numbers
# belong, so every field is coerced rather than trusted. Python's json also
# accepts NaN and Infinity, neither of which survives arithmetic or reaches GTK
# as a number, so they count as no answer at all.


def _as_int(value: object, default: int) -> int:
    if isinstance(value, bool):
        return default
    return value if isinstance(value, int) else default


def _as_float(value: object, default: float) -> float:
    number = _as_float_or_none(value)
    return default if number is None else number


def _as_float_or_none(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value):
        return None
    return float(value)


def _as_bool(value: object, default: bool = False) -> bool:
    return value if isinstance(value, bool) else default


def _as_str_or_none(value: object) -> str | None:
    return value if isinstance(value, str) else None
