"""§8 corpus determinism + fidelity metrics."""
import json

import pytest

from tests.fixtures.llm.fake_client import FakeLLMClient

pytestmark = pytest.mark.db


def _strategy_stub(*_a, **_k):
    return {"status": "OPTIMAL", "makespan": 10, "tardiness_by_job": {}}


def test_corpus_generation_is_byte_deterministic(tmp_path):
    from coe.agents.benchmark import generate_corpus

    d1 = generate_corpus(42, tmp_path / "a")
    d2 = generate_corpus(42, tmp_path / "b")
    a = (d1 / "corpus.jsonl").read_bytes()
    b = (d2 / "corpus.jsonl").read_bytes()
    assert a == b
    meta = json.loads((d1 / "_meta.json").read_text())
    assert meta["seed"] == 42 and meta["synthetic"] is True
    assert set(meta["families"]) == {"MACHINE", "WORKER", "MATERIAL"}
    lines = [json.loads(x) for x in a.decode().splitlines()]
    assert len(lines) == 12
    kinds = [x["kind"] for x in lines]
    assert kinds.count("MACHINE") == 4
    assert kinds.count("WORKER") == 4
    assert kinds.count("MATERIAL") == 4


def test_translation_metrics_perfect_on_canned_truth(clean_db, tmp_path):
    from coe.agents.benchmark import (
        generate_corpus,
        materialize_case,
        run_fidelity,
    )

    corpus = generate_corpus(42, tmp_path)
    cases = [json.loads(x) for x in
             (corpus / "corpus.jsonl").read_text().splitlines()]

    # Fake client echoes each ground truth back verbatim -> perfect score.
    responses = []
    for c in cases:
        materialize_case(c, f"bench-{c['case_id']}")
        responses.append(json.dumps(c["ground_truth"]))
        responses.append('{"candidates": [], "final": true}')  # strategy ask
        responses.append("ok.")                                # explain ask
    report = run_fidelity(corpus, client=FakeLLMClient(responses),
                          strategy_solver=_strategy_stub,
                          solve_budget_seconds=5)
    tr = report["translation"]["aggregate"]
    assert tr["exact_match_rate"] == 1.0
    assert tr["corpus_pass_rate"] == 1.0
    st = report["strategy"]
    assert st["measured"] is True          # solver injected => rates real
    assert st["non_degradation_rate"] == 1.0
    assert report["threshold_met"] is True


def test_translation_metrics_zero_on_garbage(clean_db, tmp_path):
    from coe.agents.benchmark import (
        generate_corpus,
        materialize_case,
        run_fidelity,
    )

    corpus = generate_corpus(42, tmp_path)
    cases = [json.loads(x) for x in
             (corpus / "corpus.jsonl").read_text().splitlines()]
    for c in cases:
        materialize_case(c, f"bench-{c['case_id']}")
    responses = []
    for _ in cases:
        responses.append('{"kind":"NOPE"}')
        responses.append('{"candidates": [], "final": true}')
        responses.append("ok.")
    # No strategy_solver injected: strategy metrics are UNMEASURED — the
    # neutral 1.0 must be flagged, not passed off as a perfect score.
    report = run_fidelity(corpus, client=FakeLLMClient(responses),
                          solve_budget_seconds=5)
    tr = report["translation"]["aggregate"]
    assert tr["corpus_pass_rate"] == 0.0
    st = report["strategy"]
    assert st["measured"] is False
    assert st["non_degradation_rate"] == 1.0   # neutral default
    assert report["threshold_met"] is False
