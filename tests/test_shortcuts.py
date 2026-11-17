"""Accelerator labelling, which the older-libadwaita list renders by hand."""

from notemad.ui import shortcuts


def test_accelerators_are_labelled_the_way_a_keyboard_is_read():
    assert shortcuts._accelerator_label("<Control>d") == "Ctrl+D"
    assert shortcuts._accelerator_label("F2") == "F2"
    assert shortcuts._accelerator_label("<Alt>1...<Alt>9") == "Alt+1...Alt+9"


def test_an_unparseable_accelerator_is_shown_as_it_was_written():
    """Better a literal on screen than an empty space where a key should be."""
    assert shortcuts._accelerator_label("Meta-Cokebottle") == "Meta-Cokebottle"


def test_every_listed_accelerator_can_be_labelled():
    """The older-libadwaita list writes these out itself, so none may come back blank."""
    for _, items in shortcuts.SHORTCUTS:
        for name, accelerator in items:
            assert shortcuts._accelerator_label(accelerator), name
