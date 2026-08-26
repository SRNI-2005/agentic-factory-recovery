"""Schedule diff animation — before-to-after transition frames.

``schedule_frames`` takes two entry lists (the schedule before a recovery
run and the newly active schedule) and returns a sequence of Plotly
Gantt figures that visualise the transition step by step.
"""
from __future__ import annotations


def _op_key(entry: dict) -> tuple[str, int]:
    return (entry["job_name"], int(entry["sequence_number"]))


def _to_row(entry: dict, *, ghost: bool = False) -> dict:
    import pandas as pd

    start = int(entry["start_time"])
    end = int(entry["end_time"])
    if end <= start:
        end = start + 1
    label = f"{entry['job_name']}/op{entry['sequence_number']}"
    machine = entry.get("machine_name") or entry.get("machine_id", "?")
    worker = entry.get("worker_name") or "—"
    ts = pd.Timestamp("2024-01-01")
    return dict(
        Machine=machine,
        Task=label,
        Worker=worker,
        Start=ts + pd.Timedelta(minutes=start),
        Finish=ts + pd.Timedelta(minutes=end),
        ghost=ghost,
    )


def _build_figure(rows: list[dict], *, title: str = ""):
    import pandas as pd
    import plotly.graph_objects as go

    if not rows:
        fig = go.Figure()
        fig.update_layout(title=title or "Schedule diff")
        return fig

    df = pd.DataFrame(rows)
    # Separate visible vs ghost rows for distinct styling
    visible = df[~df["ghost"]]
    ghosts = df[df["ghost"]]

    fig = go.Figure()

    if not visible.empty:
        for task, grp in visible.groupby("Task", sort=False):
            fig.add_trace(go.Bar(
                x=[(r["Finish"] - r["Start"]).total_seconds() / 60
                   for _, r in grp.iterrows()],
                base=[r["Start"] for _, r in grp.iterrows()],
                y=[r["Machine"] for _, r in grp.iterrows()],
                name=task,
                orientation="h",
                text=f"{task} ({grp.iloc[0]['Worker']})",
                textposition="inside",
                hovertemplate=(
                    "%{y}<br>%{text}<br>"
                    "Start: %{base}<br>Duration: %{x} min<extra></extra>"
                ),
            ))

    if not ghosts.empty:
        for task, grp in ghosts.groupby("Task", sort=False):
            fig.add_trace(go.Bar(
                x=[(r["Finish"] - r["Start"]).total_seconds() / 60
                   for _, r in grp.iterrows()],
                base=[r["Start"] for _, r in grp.iterrows()],
                y=[r["Machine"] for _, r in grp.iterrows()],
                name=task + " (removed)",
                orientation="h",
                marker_color="rgba(180,180,180,0.35)",
                text=f"{task} (removed)",
                textposition="inside",
                hovertemplate=(
                    "%{y}<br>%{text}<br>"
                    "Start: %{base}<br>Duration: %{x} min<extra></extra>"
                ),
            ))

    fig.update_yaxes(autorange="reversed")
    fig.update_layout(
        barmode="overlay",
        title=title or "Schedule diff",
        xaxis_title="Time",
        yaxis_title="Machine",
        showlegend=False,
    )
    return fig


def schedule_frames(
    before_entries: list[dict],
    after_entries: list[dict],
) -> list:
    """Generate Plotly figures for a before → after schedule transition.

    Key: ``(job_name, sequence_number)``.

    Returns a list of ``plotly.graph_objects.Figure``.  Each intermediate
    frame shows one moved or added operation transitioning; unchanged
    operations persist across all frames.  Removed operations render as
    ghosts until the final state.  Identical schedules produce exactly
    one final frame.
    """
    import plotly.graph_objects as go

    before_map = {_op_key(e): e for e in before_entries}
    after_map = {_op_key(e): e for e in after_entries}

    before_keys = set(before_map)
    after_keys = set(after_map)

    removed_keys = before_keys - after_keys
    added_keys = after_keys - before_keys
    moved_keys = set()
    for k in before_keys & after_keys:
        b, a = before_map[k], after_map[k]
        if (int(b["start_time"]) != int(a["start_time"])
                or int(b["end_time"]) != int(a["end_time"])
                or b.get("machine_name") != a.get("machine_name")):
            moved_keys.add(k)

    unchanged_keys = (before_keys & after_keys) - moved_keys

    # Identical schedules → single final frame
    if not removed_keys and not added_keys and not moved_keys:
        rows = [_to_row(after_map[k]) for k in after_keys]
        return [_build_figure(rows, title="Final schedule")]

    frames: list = []
    transition_keys = sorted(moved_keys | added_keys)

    # Intermediate frames: one per moved or added operation
    for idx, key in enumerate(transition_keys, start=1):
        # Base: unchanged ops from after (they exist in the final state)
        base_rows = [_to_row(after_map[k]) for k in sorted(unchanged_keys)]

        # Ghosts: removed ops still visible
        ghost_rows = [_to_row(before_map[k], ghost=True)
                      for k in sorted(removed_keys)]

        # The transitioning operation: ghost→new for moved, new-only for added
        if key in moved_keys:
            transition_rows = [
                _to_row(before_map[key], ghost=True),
                _to_row(after_map[key]),
            ]
            label = f"Moved: {key[0]}/op{key[1]}"
        else:
            # Added op: not in before, show in after
            transition_rows = [_to_row(after_map[key])]
            label = f"Added: {key[0]}/op{key[1]}"

        rows = base_rows + ghost_rows + transition_rows
        frames.append(_build_figure(rows, title=f"Step {idx}: {label}"))

    # If only removed ops (no moved/added), produce one intermediate frame
    # showing unchanged + ghosts before the final state.
    if removed_keys and not transition_keys:
        base_rows = [_to_row(after_map[k]) for k in sorted(unchanged_keys)]
        ghost_rows = [_to_row(before_map[k], ghost=True)
                      for k in sorted(removed_keys)]
        rows = base_rows + ghost_rows
        frames.append(_build_figure(rows, title="Step 1: Removed operations"))

    # Final frame: all after entries, no ghosts
    final_rows = [_to_row(after_map[k]) for k in sorted(after_keys)]
    frames.append(_build_figure(final_rows, title="Final schedule"))

    return frames
