import pytest

from coe.parsers.common import SourceParseError
from coe.parsers.mk01 import parse_mk01

VALID = "2 2\n1 2 0 5 1 4\n1 1 1 7\n"


def test_parses_dimensions_and_alternatives():
    parsed = parse_mk01(VALID)
    assert (parsed.n_jobs, parsed.n_machines) == (2, 2)
    assert [a.machine_index for a in parsed.jobs[0].operations[0].alternatives] == [0, 1]
    assert parsed.jobs[0].operations[0].alternatives[0].processing_time == 5


def test_real_mk01_shape(data_dir):
    raw = (data_dir / "raw/mk01/mk01.txt").read_text()
    parsed = parse_mk01(raw)
    assert (parsed.n_jobs, parsed.n_machines) == (10, 6)
    assert sum(len(j.operations) for j in parsed.jobs) == 55
    first = parsed.jobs[0].operations[0].alternatives
    assert [(a.machine_index, a.processing_time) for a in first] == [(0, 5), (2, 4)]


def test_non_integer_token_reports_line():
    with pytest.raises(SourceParseError, match="line 2"):
        parse_mk01("2 1\n1 1 x\n")


def test_trailing_tokens_rejected():
    with pytest.raises(SourceParseError, match="trailing"):
        parse_mk01(VALID + "99")


def test_machine_index_out_of_range():
    with pytest.raises(SourceParseError, match="outside"):
        parse_mk01("1 1\n1 1 5 3\n")
