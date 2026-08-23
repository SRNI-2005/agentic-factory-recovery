"""Half-open interval algebra for payload construction (spec §3.1, §5).

Convention: every window is [start, end). Touching intervals behave like
overlapping ones under this convention, matching the Phase 1 union rule.
"""


def merge_intervals(pairs):
    out: list[list[int]] = []
    for s, e in sorted(pairs):
        if e <= s:
            continue
        if out and s <= out[-1][1]:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return [(s, e) for s, e in out]


def complement(start, end, busy):
    gaps: list[tuple[int, int]] = []
    cursor = start
    for s, e in merge_intervals(busy):
        s, e = max(s, start), min(e, end)
        if s >= e:
            continue
        if s > cursor:
            gaps.append((cursor, s))
        cursor = max(cursor, e)
    if cursor < end:
        gaps.append((cursor, end))
    return gaps


def clip_window(window, busy):
    ws, we = window
    for bs, be in sorted(busy):
        if bs < we and ws < be:
            ws = max(ws, be)
    return (ws, we) if ws < we else None
