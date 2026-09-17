"""Live Gantt board builder (spec 2026-09-18).

Pure helpers: classify committed schedule entries at minute t and build
the board figure in the SAME visual vocabulary as the Configure page
(per-JOB colours, numeric minute axis, JN/opM hover). No DB, no solver,
no session access — a read-only projection of the committed board.
"""
from __future__ import annotations

AMBER = "#FFB000"          # in-progress border (spec §2)


def classify(entries: list[dict], t: int) -> dict[str, list[int]]:
    """Entry ids bucketed by state at minute t (§4 classification)."""
    completed, active, future = [], [], []
    for e in entries:
        start, end = int(e["start_time"]), int(e["end_time"])
        if end <= t:
            completed.append(e["id"])
        elif start <= t < end:
            active.append(e["id"])
        else:
            future.append(e["id"])
    return {"completed": completed, "in_progress": active, "future": future}


def _lane_figure(lane_entries: list[dict], t: int, jobs_palette):
    """One machine's go.Bar trace with per-job colour + state arrays."""
    import plotly.graph_objects as go

    lane = lane_entries[0]["machine_name"]
    ends = [int(e["end_time"]) for e in lane_entries]
    starts = [int(e["start_time"]) for e in lane_entries]
    spans = [max(en - s, 1) for s, en in zip(starts, ends)]
    colors, opacities, line_colors, line_widths = [], [], [], []
    for e in lane_entries:
        st_ = classify([e], t)   # single-entry classify -> its state
        state = (
            "completed" if st_["completed"] else
            "in_progress" if st_["in_progress"] else "future")
        color = jobs_palette.get(e["job_name"], "#4B70F5")
        colors.append(color)
        if state == "completed":
            opacities.append(0.35)
            line_colors.append("rgba(0,0,0,0)")
            line_widths.append(0)
        elif state == "in_progress":
            opacities.append(1.0)
            line_colors.append(AMBER)
            line_widths.append(3)
        else:
            opacities.append(1.0)
            line_colors.append("rgba(0,0,0,0)")
            line_widths.append(0)
    bar = go.Bar(
        x=spans,
        y=[lane] * len(lane_entries),
        base=starts,
        orientation="h",
        marker=dict(color=colors, opacity=opacities,
                    line=dict(color=line_colors, width=line_widths)),
        hovertemplate=(
            "%{customdata}<extra>M %{y}</extra>"),
        showlegend=False,
    )
    bar.customdata = [
        f"{e['job_name']}/op{e.get('sequence_number', '?')} · "
        f"Start {int(e['start_time'])} End {int(e['end_time'])} · "
        f"Worker {e.get('worker_name') or '—'}"
        for e in lane_entries]
    return bar


def build_board_figure(entries: list[dict], t: int, jobs_palette: dict):
    """Machines × time board at minute t (§2). None with no entries."""
    if not entries:
        return None
    import plotly.graph_objects as go

    vline_t = int(t)
    lanes: dict[str, list[dict]] = {}
    for e in entries:
        lanes.setdefault(e["machine_name"], []).append(e)
    fig = go.Figure()
    for lane_name in sorted(lanes):
        fig.add_trace(_lane_figure(lanes[lane_name], t, jobs_palette))
    all_ends = [int(e["end_time"]) for e in entries]
    horizon = max(all_ends + [1])
    fig.update_layout(
        height=max(len(lanes) * 26 + 60, 160, vline:=0) or 180,
        margin=dict(l=40, r=10, t=10, b=30),
        xaxis=dict(title=None, range=[0, max(int(horizon), vline_t + 5)],
                   showgrid=True),
        yaxis=dict(autorange="reversed", title=None),
        uirevision="sim-live-gantt",           # stable zoom across rebuilds
    )
    fig.add_vline(
        x=vline_t, line_width=2, line_color="#FFB000", opacity=0.9)
    return fig
