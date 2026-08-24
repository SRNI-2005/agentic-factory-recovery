from coe.solver.windows import clip_window, complement, merge_intervals


def test_merge_overlapping_touching_nested_unsorted():
    assert merge_intervals([(5, 10), (0, 3), (2, 6), (10, 12), (20, 22)]) == \
        [(0, 12), (20, 22)]
    assert merge_intervals([]) == []
    assert merge_intervals([(3, 3)]) == []


def test_complement_leading_gaps_trailing():
    assert complement(0, 100, [(10, 20), (30, 40)]) == [(0, 10), (20, 30), (40, 100)]
    assert complement(0, 10, []) == [(0, 10)]
    assert complement(0, 10, [(0, 10)]) == []
    assert complement(5, 15, [(0, 30)]) == []


def test_complement_handles_unsorted_and_overlapping_busy():
    # DEVIATION from triage listing: [10,30) U [20,35) merges to [10,35),
    # so the mid gap is (35,40) — the listed (30,40) would mark busy
    # time [30,35) as free.
    assert complement(0, 100, [(40, 60), (10, 30), (20, 35)]) == \
        [(0, 10), (35, 40), (60, 100)]


def test_complement_degenerate_busy_dropped():
    assert complement(0, 10, [(3, 3), (12, 20)]) == [(0, 10)]


def test_clip_partial_overlap_pushes_to_busy_end():
    assert clip_window((150, 250), [(100, 200)]) == (200, 250)


def test_clip_full_coverage_drops():
    assert clip_window((150, 250), [(100, 300)]) is None


def test_clip_no_overlap_keeps():
    assert clip_window((50, 80), [(100, 200)]) == (50, 80)


def test_clip_multiple_busy_uses_latest_end():
    assert clip_window((150, 400), [(100, 200), (250, 300)]) == (300, 400)
