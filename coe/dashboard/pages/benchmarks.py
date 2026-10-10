"""Benchmarks page — fidelity report viewer with rich visualizations."""
from __future__ import annotations


def render() -> None:
    import streamlit as st

    from coe.dashboard.data import fidelity_report

    if hasattr(st, "markdown"):
        st.markdown("# 🧪 Benchmarks & Fidelity")
        st.caption("Translation fidelity metrics, strategy validation, and solver benchmarks")

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

    if hasattr(st, "subheader"):
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

    # ---- Gauges (when running in full browser Streamlit) ----
    corpus_rate = aggregate.get("corpus_pass_rate", 0)
    exact_rate = aggregate.get("exact_match_rate", 0)

    if hasattr(st, "plotly_chart") and hasattr(st, "markdown"):
        import plotly.graph_objects as go
        from coe.dashboard.theme import (
            ACCENT_CYAN,
            ACCENT_EMERALD,
            ACCENT_ROSE,
            apply_plotly_theme,
        )

        st.markdown("<br>", unsafe_allow_html=True)
        st.markdown("## 📊 Fidelity Gauges")
        g1, g2 = st.columns(2)

        with g1:
            fig_corpus = go.Figure(go.Indicator(
                mode="gauge+number+delta",
                value=corpus_rate * 100,
                title=dict(text="Corpus Pass Rate", font=dict(color="#e2e8f0")),
                number=dict(suffix="%", font=dict(color="#e2e8f0")),
                delta=dict(reference=80, suffix="%"),
                gauge=dict(
                    axis=dict(range=[0, 100], tickfont=dict(color="#94a3b8")),
                    bar=dict(color=ACCENT_EMERALD),
                    bgcolor="rgba(17,22,56,0.6)",
                    borderwidth=0,
                    steps=[
                        dict(range=[0, 50], color="rgba(244,63,94,0.15)"),
                        dict(range=[50, 80], color="rgba(245,158,11,0.15)"),
                        dict(range=[80, 100], color="rgba(16,185,129,0.15)"),
                    ],
                    threshold=dict(
                        line=dict(color=ACCENT_ROSE, width=3),
                        thickness=0.8,
                        value=80,
                    ),
                ),
            ))
            fig_corpus.update_layout(height=280)
            apply_plotly_theme(fig_corpus)
            st.plotly_chart(fig_corpus, use_container_width=True)

        with g2:
            fig_exact = go.Figure(go.Indicator(
                mode="gauge+number+delta",
                value=exact_rate * 100,
                title=dict(text="Exact Match Rate", font=dict(color="#e2e8f0")),
                number=dict(suffix="%", font=dict(color="#e2e8f0")),
                delta=dict(reference=70, suffix="%"),
                gauge=dict(
                    axis=dict(range=[0, 100], tickfont=dict(color="#94a3b8")),
                    bar=dict(color=ACCENT_CYAN),
                    bgcolor="rgba(17,22,56,0.6)",
                    borderwidth=0,
                    steps=[
                        dict(range=[0, 40], color="rgba(244,63,94,0.15)"),
                        dict(range=[40, 70], color="rgba(245,158,11,0.15)"),
                        dict(range=[70, 100], color="rgba(16,185,129,0.15)"),
                    ],
                    threshold=dict(
                        line=dict(color=ACCENT_ROSE, width=3),
                        thickness=0.8,
                        value=70,
                    ),
                ),
            ))
            fig_exact.update_layout(height=280)
            apply_plotly_theme(fig_exact)
            st.plotly_chart(fig_exact, use_container_width=True)

    # ---- Per-case data ----
    cases = report.get("cases", [])
    if cases:
        if hasattr(st, "subheader"):
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

        if hasattr(st, "plotly_chart") and hasattr(st, "markdown"):
            import plotly.graph_objects as go
            from coe.dashboard.theme import ACCENT_EMERALD, ACCENT_ROSE, apply_plotly_theme

            fig_cases = go.Figure()
            fig_cases.add_trace(go.Bar(
                x=[c["case_id"] for c in cases],
                y=[c.get("field_hits", 0) for c in cases],
                name="Field Hits",
                marker=dict(color=ACCENT_EMERALD, line=dict(width=0)),
            ))
            fig_cases.add_trace(go.Bar(
                x=[c["case_id"] for c in cases],
                y=[c.get("field_total", 0) - c.get("field_hits", 0) for c in cases],
                name="Misses",
                marker=dict(color="rgba(244,63,94,0.4)", line=dict(width=0)),
            ))
            fig_cases.update_layout(
                barmode="stack",
                title="Field Hit/Miss per Test Case",
                xaxis_title="Case",
                yaxis_title="Fields",
                height=380,
            )
            apply_plotly_theme(fig_cases)
            st.plotly_chart(fig_cases, use_container_width=True)

    # ---- Strategy comparison ----
    strategy = report.get("strategy", {})
    if strategy.get("measured"):
        if hasattr(st, "subheader"):
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

    # ---- Solver benchmark reference cards ----
    if hasattr(st, "markdown"):
        from coe.dashboard.theme import glass_card
        st.markdown("<br>", unsafe_allow_html=True)
        st.markdown("## ⚙️ Solver Benchmark Reference")
        st.markdown(glass_card("""
            <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 1.5rem;">
                <div>
                    <div style="font-weight: 600; color: #6366f1; margin-bottom: 0.5rem;">
                        🏆 MK01 Benchmark
                    </div>
                    <div style="color: #94a3b8; font-size: 0.85rem; line-height: 1.7;">
                        Proven <strong style="color: #10b981;">OPTIMAL</strong> makespan = 40<br>
                        Solve time: ~1.5 seconds<br>
                        10 jobs × 6 machines<br>
                        Brandimarte benchmark instance
                    </div>
                </div>
                <div>
                    <div style="font-weight: 600; color: #22d3ee; margin-bottom: 0.5rem;">
                        🏭 Factory Demo
                    </div>
                    <div style="color: #94a3b8; font-size: 0.85rem; line-height: 1.7;">
                        Status: <strong style="color: #f59e0b;">FEASIBLE</strong> via warm start<br>
                        30 jobs × 8 machines × 168 ops<br>
                        Two-phase: relax → hint → repair<br>
                        + greedy fallback
                    </div>
                </div>
            </div>
        """), unsafe_allow_html=True)

    st.caption(
        "CP-SAT / QAOA comparison tables arrive with P4/P5."
    )
