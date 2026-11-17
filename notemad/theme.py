"""Named theme records pairing a libadwaita color scheme with a source style."""

import logging
from dataclasses import dataclass
from pathlib import Path

import gi

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")
gi.require_version("GtkSource", "5")

from gi.repository import Adw, Gdk, Gtk, GtkSource

logger = logging.getLogger(__name__)

# Carried by the source views so the font can be set in one place.
EDITOR_CSS_CLASS = "notemad-editor"

DATA_DIR = Path(__file__).parent / "data"
STYLES_DIR = DATA_DIR / "styles"
CSS_DIR = DATA_DIR / "css"
ICONS_DIR = DATA_DIR / "icons"

# Every rule lives here once. A theme file is only the palette it is written
# against, and the two are loaded as one stylesheet so the names resolve.
RULES_PATH = CSS_DIR / "_rules.css"


@dataclass(frozen=True)
class Theme:
    id: str
    name: str
    dark: bool
    style_scheme: str
    css: str
    accent: str
    icon: str

    @property
    def css_path(self) -> Path:
        return CSS_DIR / self.css

    @property
    def color_scheme(self) -> Adw.ColorScheme:
        return Adw.ColorScheme.FORCE_DARK if self.dark else Adw.ColorScheme.FORCE_LIGHT


def _theme(theme_id: str, name: str, dark: bool, accent: str) -> Theme:
    """A theme record, given that the file names all follow from the id."""
    return Theme(
        id=theme_id,
        name=name,
        dark=dark,
        style_scheme=f"notemad-{theme_id}",
        css=f"{theme_id}.css",
        accent=accent,
        icon="notemad-theme-dark-symbolic" if dark else "notemad-theme-light-symbolic",
    )


# In the order they are offered. Adding one is a record here, a palette in
# data/css and a style scheme in data/styles, and nothing else.
THEMES = {
    theme.id: theme
    for theme in (
        _theme("dark", "Dark", True, "#8AA9F0"),
        _theme("light", "Light", False, "#4C63B6"),
    )
}

DEFAULT_THEME = "dark"
DEFAULT_LIGHT_THEME = "light"

# Not a theme record of its own: a preference that resolves to one of the
# records above depending on what the desktop says it wants.
FOLLOW_SYSTEM = "system"

# In the order the preferences dialog offers them.
PREFERENCES = ((FOLLOW_SYSTEM, "Follow System"),) + tuple(
    (theme.id, theme.name) for theme in THEMES.values()
)

_provider: Gtk.CssProvider | None = None
_font_provider: Gtk.CssProvider | None = None


def get(theme_id: str) -> Theme:
    return THEMES.get(theme_id, THEMES[DEFAULT_THEME])


def system_prefers_dark() -> bool:
    return Adw.StyleManager.get_default().get_dark()


def resolve(
    preference: str, dark_id: str = DEFAULT_THEME, light_id: str = DEFAULT_LIGHT_THEME
) -> Theme:
    """The theme a preference actually puts on screen.

    Following the system only says dark or light, so it lands on whichever
    theme of that kind was last chosen rather than always on the default pair.
    """
    if preference == FOLLOW_SYSTEM:
        return get(dark_id) if system_prefers_dark() else get(light_id)
    return get(preference)


def register_style_schemes() -> None:
    """Make the bundled source style schemes findable by their id."""
    manager = GtkSource.StyleSchemeManager.get_default()
    manager.append_search_path(str(STYLES_DIR))


def register_icons() -> None:
    """Make the bundled icons findable by name, installed or running from source."""
    display = Gdk.Display.get_default()
    if display is None:
        return
    Gtk.IconTheme.get_for_display(display).add_search_path(str(ICONS_DIR))


def source_scheme(theme: Theme) -> GtkSource.StyleScheme | None:
    scheme = GtkSource.StyleSchemeManager.get_default().get_scheme(theme.style_scheme)
    if scheme is None:
        logger.warning("style scheme %s is missing", theme.style_scheme)
    return scheme


def apply(
    preference: str,
    dark_id: str = DEFAULT_THEME,
    light_id: str = DEFAULT_LIGHT_THEME,
) -> Theme:
    """Put a theme on screen: libadwaita color scheme and the matching stylesheet.

    Returns the theme that ended up being shown, which is not the preference
    itself when the preference is to follow the system.
    """
    global _provider

    manager = Adw.StyleManager.get_default()
    if preference == FOLLOW_SYSTEM:
        # DEFAULT hands the decision back to the desktop. The colour scheme has
        # to be set before asking which way it went.
        manager.set_color_scheme(Adw.ColorScheme.DEFAULT)
        theme = resolve(FOLLOW_SYSTEM, dark_id, light_id)
    else:
        theme = get(preference)
        manager.set_color_scheme(theme.color_scheme)

    display = Gdk.Display.get_default()
    if display is None:
        return theme

    # One provider swapped wholesale rather than two competing ones, because the
    # palette is defined with named colors and those are global.
    if _provider is not None:
        Gtk.StyleContext.remove_provider_for_display(display, _provider)

    _provider = Gtk.CssProvider()
    _provider.load_from_string(_stylesheet(theme))
    Gtk.StyleContext.add_provider_for_display(
        display, _provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    )
    return theme


def _stylesheet(theme: Theme) -> str:
    """The palette followed by the rules written against it.

    Concatenated rather than loaded as two providers, because a named colour
    has to be defined before the rule that reads it, and that is only
    guaranteed within one stylesheet.
    """
    try:
        return theme.css_path.read_text(encoding="utf-8") + RULES_PATH.read_text(
            encoding="utf-8"
        )
    except OSError as exc:
        logger.warning("could not read the stylesheet for %s: %s", theme.id, exc)
        return ""


def apply_font(family: str, size: int) -> None:
    """Set the editor font on every view at once.

    A provider of its own rather than a rule in the theme stylesheet, because
    the two change at different times and the theme file is swapped wholesale.
    """
    global _font_provider

    display = Gdk.Display.get_default()
    if display is None:
        return

    if _font_provider is not None:
        Gtk.StyleContext.remove_provider_for_display(display, _font_provider)

    # A family name arrives from a font chooser or a hand edited config file,
    # and either way it has no business closing the quotes itself. A newline
    # ends a CSS string just as surely as a quote does, so those go too, and
    # with them any chance of the name turning into a rule of its own.
    safe_family = "".join(
        character
        for character in family
        if character not in '"\\' and character.isprintable()
    )
    _font_provider = Gtk.CssProvider()
    _font_provider.load_from_string(
        f'.{EDITOR_CSS_CLASS} {{ font-family: "{safe_family}"; font-size: {size}pt; }}'
    )
    Gtk.StyleContext.add_provider_for_display(
        display, _font_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    )
