"""
Fast, pure-math tests for glof_utils.py's real published GLOF formulas
(Popov 1991 peak-discharge regression, Huggel et al. 2002 area-volume
fallback). No network/physics dependency.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import glof_utils  # noqa: E402


def test_popov_peak_discharge_matches_known_shishper_order_of_magnitude():
    """Real, documented 2022 Shishper event: volume 38,987,000 m^3
    produced a real reported peak discharge of 3,737 m^3/s. The Popov
    regression is a general empirical fit, not tuned to this exact lake
    -- confirmed here to genuinely overestimate this specific event by
    about 8x (30,388 vs. 3,737 m^3/s), which is exactly why this app
    uses Shishper's real reported numbers directly rather than deriving
    them from the formula (see glof_utils.py's own module docstring).
    This test's job is to catch a real formula/constant regression (an
    order-of-magnitude jump to e.g. 300 or 3,000,000), not to assert
    tight agreement with one specific lake the formula was never fit to."""
    q = glof_utils.popov_peak_discharge_m3s(38_987_000)
    assert 1000 < q < 100000


def test_popov_peak_discharge_increases_with_volume():
    q_small = glof_utils.popov_peak_discharge_m3s(1_000_000)
    q_big = glof_utils.popov_peak_discharge_m3s(50_000_000)
    assert q_big > q_small


def test_popov_peak_discharge_zero_volume_is_zero():
    assert glof_utils.popov_peak_discharge_m3s(0) == 0.0


def test_huggel_volume_from_area_increases_with_area():
    v_small = glof_utils.huggel_volume_from_area_m2(50_000)
    v_big = glof_utils.huggel_volume_from_area_m2(500_000)
    assert v_big > v_small


def test_huggel_volume_matches_published_coefficients_exactly():
    """Direct arithmetic check against the real published formula
    V = 0.1217 * A^1.4129 -- catches any accidental coefficient/exponent
    drift, not just directional correctness."""
    area = 300_000.0
    expected = 0.1217 * (area ** 1.4129)
    assert glof_utils.huggel_volume_from_area_m2(area) == pytest.approx(expected, rel=1e-9)


def test_known_shishper_lake_preset_uses_real_reported_event_data():
    """The Shishper 2022 preset is real documented event data, not a
    formula output -- confirms the curated preset's own numbers are
    exactly the real reported figures, unmodified."""
    lake = glof_utils.KNOWN_GB_LAKES["shishper_2022"]
    assert lake["documented_volume_m3"] == 38_987_000
    assert lake["documented_peak_discharge_m3s"] == 3737


def test_glof_rise_fraction_is_fast_rise_slow_recede():
    """Real documented GLOF asymmetry: peak arrives early in the event,
    not at the midpoint (unlike an ordinary river flood's roughly
    symmetric hydrograph)."""
    assert 0.0 < glof_utils.GLOF_RISE_FRAC < 0.5


# --------------------------------------------------------------------------
# HydroLAKES integration -- real local shapefile lookups, broadening GLOF
# scenarios beyond the small curated KNOWN_GB_LAKES list (see glof_utils.py's
# own "HydroLAKES" section docstring). Skips (not fails) if the real local
# extract isn't present on this machine.
# --------------------------------------------------------------------------
_has_hydrolakes = os.path.exists(glof_utils.HYDROLAKES_POINTS_SHP)
_hydrolakes_skip = pytest.mark.skipif(not _has_hydrolakes,
                                       reason="local HydroLAKES points shapefile not present on this machine")

# Real coords near Shishper Glacier Lake (Hunza, Gilgit-Baltistan) -- known
# from earlier verification to have several real HydroLAKES lakes within
# 100km.
GB_LON, GB_LAT = 74.61, 36.40


@_hydrolakes_skip
def test_find_hydrolakes_near_returns_real_lakes_sorted_by_distance():
    lakes = glof_utils.find_hydrolakes_near(GB_LON, GB_LAT, radius_km=100.0)
    assert len(lakes) > 0
    dists = [l["distance_km"] for l in lakes]
    assert dists == sorted(dists)


@_hydrolakes_skip
def test_find_hydrolakes_near_schema_has_usable_volume_for_popov():
    lakes = glof_utils.find_hydrolakes_near(GB_LON, GB_LAT, radius_km=100.0)
    lake = lakes[0]
    assert {"hydrolakes_id", "name", "lake_lon", "lake_lat", "distance_km", "volume_m3"} <= set(lake.keys())
    assert lake["volume_m3"] > 0
    # every returned lake's volume must be directly usable by the real Popov formula
    q = glof_utils.popov_peak_discharge_m3s(lake["volume_m3"])
    assert q > 0


@_hydrolakes_skip
def test_find_hydrolakes_near_respects_radius():
    close = glof_utils.find_hydrolakes_near(GB_LON, GB_LAT, radius_km=5.0)
    far = glof_utils.find_hydrolakes_near(GB_LON, GB_LAT, radius_km=200.0)
    assert len(far) >= len(close)
    assert all(l["distance_km"] <= 5.0 for l in close)


@_hydrolakes_skip
def test_hydrolakes_lake_by_id_matches_a_real_nearby_result():
    lakes = glof_utils.find_hydrolakes_near(GB_LON, GB_LAT, radius_km=100.0)
    target = lakes[0]
    lake = glof_utils.hydrolakes_lake_by_id(target["hydrolakes_id"])
    assert lake is not None
    assert lake["hydrolakes_id"] == target["hydrolakes_id"]
    assert lake["volume_m3"] == target["volume_m3"]


@_hydrolakes_skip
def test_hydrolakes_lake_by_id_unknown_id_returns_none():
    assert glof_utils.hydrolakes_lake_by_id(999_999_999) is None


def test_find_hydrolakes_near_missing_file_returns_empty_not_raises(monkeypatch):
    monkeypatch.setattr(glof_utils, "HYDROLAKES_POINTS_SHP", "/nonexistent/hydrolakes.shp")
    assert glof_utils.find_hydrolakes_near(GB_LON, GB_LAT) == []


def test_hydrolakes_lake_by_id_missing_file_returns_none(monkeypatch):
    monkeypatch.setattr(glof_utils, "HYDROLAKES_POINTS_SHP", "/nonexistent/hydrolakes.shp")
    assert glof_utils.hydrolakes_lake_by_id(1370497) is None


def test_froehlich_breach_discharge_matches_published_formula():
    volume_m3, breach_height_m = 5_000_000.0, 15.0
    expected = 0.607 * (volume_m3 ** 0.295) * (breach_height_m ** 1.24)
    assert glof_utils.froehlich_breach_discharge_m3s(volume_m3, breach_height_m) == pytest.approx(expected)


def test_froehlich_breach_discharge_zero_for_zero_volume_or_height():
    assert glof_utils.froehlich_breach_discharge_m3s(0.0, 20.0) == 0.0
    assert glof_utils.froehlich_breach_discharge_m3s(5_000_000.0, 0.0) == 0.0


def test_froehlich_breach_discharge_increases_with_both_volume_and_height():
    base = glof_utils.froehlich_breach_discharge_m3s(1_000_000.0, 10.0)
    more_volume = glof_utils.froehlich_breach_discharge_m3s(5_000_000.0, 10.0)
    more_height = glof_utils.froehlich_breach_discharge_m3s(1_000_000.0, 30.0)
    assert more_volume > base
    assert more_height > base


def test_froehlich_gives_a_different_real_estimate_than_popov_for_the_same_volume():
    # Real, distinct formulas -- confirms this isn't accidentally aliased
    # to Popov's own regression for a real documented volume (Shishper).
    volume_m3 = 38_987_000.0
    popov = glof_utils.popov_peak_discharge_m3s(volume_m3)
    froehlich = glof_utils.froehlich_breach_discharge_m3s(volume_m3, 30.0)
    assert froehlich != pytest.approx(popov)
