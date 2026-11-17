import json

import pytest

from notemad import config


def test_defaults_when_there_is_nothing_worth_reading(xdg_home, tmp_path):
    """A missing file, an empty object and junk all come back as the defaults."""
    empty = tmp_path / "empty.json"
    empty.write_text(json.dumps({}), encoding="utf-8")
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    not_an_object = tmp_path / "list.json"
    not_an_object.write_text("[1, 2, 3]", encoding="utf-8")

    for source in (tmp_path / "missing.json", empty, broken, not_an_object):
        assert config.load(source) == config.Config()


def test_every_field_round_trips(xdg_home, tmp_path):
    # A theme id is stored as written and never checked against the theme list,
    # so a config naming one that no longer exists still loads.
    path = tmp_path / "config.json"
    saved = config.Config(
        theme="light",
        last_dark="gone-in-a-later-version",
        last_light="light",
        font_family="Fira Code",
        font_size=13,
        archive_warn_bytes=0,
    )
    config.save(saved, path)

    assert config.load(path) == saved


@pytest.mark.parametrize(
    ("stored", "field", "expected"),
    [
        ({"theme": 7}, "theme", "dark"),
        ({"last_dark": 7}, "last_dark", "dark"),
        ({"last_light": ""}, "last_light", "light"),
        ({"font_family": True}, "font_family", config.DEFAULT_FONT_FAMILY),
        ({"font_family": "   "}, "font_family", config.DEFAULT_FONT_FAMILY),
        ({"font_size": "large"}, "font_size", config.DEFAULT_FONT_SIZE),
        # bool is an int in Python, so an unguarded check would read True as 1.
        ({"font_size": True}, "font_size", config.DEFAULT_FONT_SIZE),
        ({"archive_warn_bytes": -5}, "archive_warn_bytes", config.DEFAULT_ARCHIVE_WARN),
        (
            {"archive_warn_bytes": True},
            "archive_warn_bytes",
            config.DEFAULT_ARCHIVE_WARN,
        ),
    ],
)
def test_a_stored_value_of_the_wrong_shape_falls_back(
    xdg_home, tmp_path, stored, field, expected
):
    path = tmp_path / "config.json"
    path.write_text(json.dumps(stored), encoding="utf-8")

    assert getattr(config.load(path), field) == expected


@pytest.mark.parametrize(
    ("size", "expected"),
    [(4000, config.MAX_FONT_SIZE), (0, config.MIN_FONT_SIZE), (13, 13)],
)
def test_a_font_size_is_clamped_to_something_readable(
    xdg_home, tmp_path, size, expected
):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"font_size": size}), encoding="utf-8")

    assert config.load(path).font_size == expected
