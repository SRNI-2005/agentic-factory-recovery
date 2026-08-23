import pytest

from coe.solver.identifier import op_id, parse_op_id


def test_format_matches_convention():
    assert op_id("J3", 2) == "J3-O2"
    assert op_id("J10", 12) == "J10-O12"


def test_roundtrip():
    assert parse_op_id(op_id("J7", 1)) == ("J7", 1)


@pytest.mark.parametrize("bad", ["", "J3", "J3-O", "-O2", "J3-O2a"])
def test_malformed_rejected(bad):
    with pytest.raises(ValueError):
        parse_op_id(bad)
