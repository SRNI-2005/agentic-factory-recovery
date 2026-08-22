import pytest

from coe.parsers.common import SourceParseError
from coe.parsers.nouri import parse_nouri


def test_documented_example_parses():
    raw = (
        "2 2 2\n"
        "2 2 1 2 1 25 2 30 2 1 1 37 2 1 1 2 32 2 2 1 24 2 33\n"
        "2 2 1 1 1 45 2 2 1 55 2 65 2 1 2 1 21 2 25 2 1 2 65\n"
    )
    p = parse_nouri(raw)
    assert (p.n_jobs, p.n_machines, p.n_workers) == (2, 2, 2)
    op11 = p.jobs[0].operations[0]
    assert [a.machine_index for a in op11.alternatives] == [0, 1]
    assert op11.alternatives[0].workers == [(0, 25), (1, 30)]
    assert op11.alternatives[1].workers == [(0, 37)]


def test_real_sfjw01_shape(data_dir):
    raw = (data_dir / "raw/nouri-fjspw/extracted/SFJW/SFJW-01.txt").read_text()
    p = parse_nouri(raw)
    header = raw.split()[0:3]
    assert (p.n_jobs, p.n_machines, p.n_workers) == tuple(int(v) for v in header)


def test_worker_out_of_range_rejected():
    with pytest.raises(SourceParseError, match="worker"):
        parse_nouri("1 1 1\n1 1 1 1 2 5\n")  # worker 2 but n_workers=1


def test_trailing_tokens_rejected():
    with pytest.raises(SourceParseError, match="trailing"):
        parse_nouri("1 1 1\n1 1 1 1 1 5\n7\n")
