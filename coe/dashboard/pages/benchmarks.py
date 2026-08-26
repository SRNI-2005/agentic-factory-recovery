"""Benchmarks page — fidelity report viewer (read-only)."""
from __future__ import annotations


def render() -> None:
    import streamlit as st

    from coe.dashboard.data import fidelity_report

    report = fidelity_report()
    if report is None:
        st.warning(
            "No fidelity report found. Generate one:\n\n"
            "```\n"
            "uv run python -m coe.cli benchmark fidelity "
            "--corpus data/corpus/fidelity-seed42 --seed 42\n"
            "```"
        )
        st.stop()

    translation = report.get("translation", {})
    aggregate = translation.get("aggregate", {})
    threshold_met = report.get("threshold_met", False)

    st.subheader("Fidelity metrics")
    c1, c2, c3 = st.columns(3)
    c1.metric(
        "Corpus pass rate",
        f"{aggregate.get('corpus_pass_rate', 0):.1%}",
    )
    c2.metric(
        "Exact match rate",
        f"{aggregate.get('exact_match_rate', 0):.1%}",
    )
    c3.metric(
        "Threshold",
        "MET" if threshold_met else "MISS",
    )

    cases = report.get("cases", [])
    if cases:
        st.subheader("Per-case translation data")
        st.dataframe(
            [
                {
                    "Case": c["case_id"],
                    "Kind": c["kind"],
                    "Field hits": c["field_hits"],
                    "Field total": c["field_total"],
                    "Pass": c["corpus_pass"],
                }
                for c in cases
            ],
            use_container_width=True,
            hide_index=True,
        )

    strategy = report.get("strategy", {})
    if strategy.get("measured"):
        st.subheader("Strategy comparison")
        sc1, sc2, sc3 = st.columns(3)
        sc1.metric("Validity rate", f"{strategy.get('validity_rate', 0):.1%}")
        sc2.metric(
            "Non-degradation rate",
            f"{strategy.get('non_degradation_rate', 0):.1%}",
        )
        sc3.metric(
            "Baseline infeasible",
            strategy.get("baseline_infeasible", 0),
        )

    st.caption(
        "CP-SAT / QAOA comparison tables arrive with P4/P5."
    )
