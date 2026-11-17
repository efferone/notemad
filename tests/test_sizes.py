from notemad.sizes import GB, KB, MB, human_size


def test_small_notes_are_counted_in_bytes():
    assert human_size(0) == "0 bytes"
    assert human_size(940) == "940 bytes"


def test_a_round_size_does_not_carry_a_pointless_decimal():
    assert human_size(250 * MB) == "250 MB"
    assert human_size(GB) == "1 GB"
    assert human_size(4 * KB) == "4 KB"


def test_anything_else_keeps_one_decimal_place():
    assert human_size(int(1.5 * GB)) == "1.5 GB"
    assert human_size(int(42.64 * MB)) == "42.6 MB"


def test_each_unit_takes_over_where_the_last_one_stops():
    assert human_size(KB - 1) == "1023 bytes"
    assert human_size(KB) == "1 KB"
    assert human_size(MB - 1) == "1024 KB"
    assert human_size(MB) == "1 MB"
