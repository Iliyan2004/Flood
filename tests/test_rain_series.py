"""
Fast, no-physics tests for flood_model.py's write_rain_file_from_series
(the real uploaded-rainfall-CSV driver) -- pure file I/O, no LISFLOOD-FP
process needed. A real end-to-end physics regression using
--rain-series-json lives in test_flood_model_cli.py (marked slow).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import flood_model  # noqa: E402


def test_negative_rate_clamped_to_zero(tmp_path):
    path = str(tmp_path / "rain.rain")
    pts = flood_model.write_rain_file_from_series(path, [0.0, 0.25, 0.5], [-3.4, 10.0, 0.0], sim_hours=1.0)
    rates = [r for r, t in pts]
    assert rates[0] == 0.0
    assert min(rates) >= 0.0


def test_series_sorted_by_time_even_if_input_unsorted(tmp_path):
    path = str(tmp_path / "rain.rain")
    pts = flood_model.write_rain_file_from_series(path, [0.5, 0.0, 0.25], [99.0, 1.0, 2.0], sim_hours=1.0)
    times = [t for r, t in pts]
    assert times == sorted(times)


def test_tail_point_appended_to_cover_sim_hours(tmp_path):
    """The written series must reach at least sim_hours -- LISFLOOD-FP's
    own parser needs a point covering the full simulated window, and a
    real uploaded CSV's own last real timestamp is usually shorter than
    sim_hours (which adds a real drain-down tail)."""
    path = str(tmp_path / "rain.rain")
    pts = flood_model.write_rain_file_from_series(path, [0.0, 0.5], [5.0, 5.0], sim_hours=3.0)
    assert pts[-1][1] == 3.0
    assert pts[-1][0] == 0.0


def test_no_duplicate_tail_when_series_already_reaches_sim_hours(tmp_path):
    path = str(tmp_path / "rain.rain")
    pts = flood_model.write_rain_file_from_series(path, [0.0, 1.0, 2.0], [5.0, 5.0, 0.0], sim_hours=2.0)
    # last real point already at sim_hours -- no extra point appended
    assert pts[-1] == (0.0, 2.0)
    assert len([t for r, t in pts if t == 2.0]) == 1


def test_duplicate_timestamp_merged_keeping_larger_rate(tmp_path):
    """Real, previously-shipped gap: sorting alone only guarantees non-
    decreasing times, not the STRICTLY increasing sequence LISFLOOD-FP's
    own parser requires -- a real rain-gauge export can contain two rows
    at the identical timestamp (a duplicate/resent row). Must merge into
    one point, keeping the LARGER real rate rather than an arbitrary one."""
    path = str(tmp_path / "rain.rain")
    pts = flood_model.write_rain_file_from_series(
        path, [0.0, 0.25, 0.25, 0.5], [10.0, 20.0, 35.0, 5.0], sim_hours=1.0)
    times = [t for r, t in pts]
    assert times == sorted(set(times)), "duplicate or non-increasing time made it through"
    assert len(times) == len(set(times))
    rate_at_025 = next(r for r, t in pts if t == 0.25)
    assert rate_at_025 == 35.0, "should keep the larger of the two tied rates, not the first-sorted one"


def test_empty_series_falls_back_to_flat_zero(tmp_path):
    path = str(tmp_path / "rain.rain")
    pts = flood_model.write_rain_file_from_series(path, [], [], sim_hours=2.0)
    assert all(r == 0.0 for r, t in pts)
    assert pts[-1][1] >= 2.0


def test_written_file_matches_lisflood_own_rate_then_time_convention(tmp_path):
    path = str(tmp_path / "rain.rain")
    flood_model.write_rain_file_from_series(path, [0.0, 0.25], [12.5, 0.0], sim_hours=0.25)
    with open(path) as f:
        lines = [l.strip() for l in f if l.strip() and not l.startswith("#")]
    header_count = int(lines[0].split()[0])
    assert header_count == len(lines) - 1
    first_rate, first_time = lines[1].split()
    assert float(first_rate) == 12.5
    assert float(first_time) == 0.0
