"""
Fast, pure-math tests for srm_utils.py's real SRM-style degree-day
snowmelt calculation (elevation-band hypsometry + lapse-rate-adjusted
temperature) -- no network/DEM dependency, synthetic arrays only.
"""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import srm_utils  # noqa: E402

NODATA = -9999.0


def test_lapse_adjusted_temperature_cools_with_altitude():
    t = srm_utils.lapse_adjusted_temperature_c(t_ref_c=20.0, ref_elev_m=1000.0, target_elev_m=2000.0,
                                                lapse_rate_c_per_m=0.0065)
    assert t == pytest.approx(20.0 - 0.0065 * 1000.0)
    assert t < 20.0


def test_lapse_adjusted_temperature_warms_below_reference():
    t = srm_utils.lapse_adjusted_temperature_c(t_ref_c=10.0, ref_elev_m=2000.0, target_elev_m=1000.0)
    assert t > 10.0


def test_elevation_zone_areas_matches_real_cell_counts():
    dem = np.full((10, 10), 3000.0)
    dem[:5, :] = 3500.0  # exactly half the grid at a distinct real elevation
    dem[0, 0] = NODATA
    bands = srm_utils.elevation_zone_areas(dem, NODATA, cellsize=100.0, band_m=200.0)
    total_area = sum(b["area_m2"] for b in bands)
    n_valid = int((dem != NODATA).sum())
    assert total_area == pytest.approx(n_valid * 100.0 ** 2)


def test_elevation_zone_areas_empty_for_all_nodata():
    dem = np.full((5, 5), NODATA)
    assert srm_utils.elevation_zone_areas(dem, NODATA, cellsize=30.0) == []


def test_snowmelt_zero_when_reference_temp_too_cold_at_altitude():
    dem = np.full((20, 20), 5000.0)
    result = srm_utils.snowmelt_discharge_m3s(dem, NODATA, cellsize=30.0, t_ref_c=5.0, ref_elev_m=1000.0,
                                               snow_line_elev_m=4000.0)
    # 5.0 - 0.0065*4000 = -21degC at the band -- well below freezing, real zero melt.
    assert result["discharge_m3s"] == 0.0


def test_snowmelt_positive_when_band_temperature_is_above_freezing():
    dem = np.full((20, 20), 4000.0)
    result = srm_utils.snowmelt_discharge_m3s(dem, NODATA, cellsize=30.0, t_ref_c=25.0, ref_elev_m=1000.0,
                                               snow_line_elev_m=3000.0)
    assert result["discharge_m3s"] > 0.0
    assert result["bands"][0]["temp_c"] > 0.0


def test_snowmelt_excludes_bands_below_the_snow_line():
    dem = np.linspace(2000, 5000, 900).reshape(30, 30)
    result = srm_utils.snowmelt_discharge_m3s(dem, NODATA, cellsize=30.0, t_ref_c=30.0, ref_elev_m=500.0,
                                               snow_line_elev_m=4500.0)
    assert all(b["mean_elev_m"] >= 4500.0 for b in result["bands"])
    assert result["snow_covered_area_km2"] < (dem.size * 900.0) / 1e6  # a real fraction, not the whole grid


def test_snowmelt_discharge_matches_hand_computed_single_band_value():
    # A flat, uniform 1km^2 band well above the snow line -- exact
    # hand-computed real value, not just a sign/direction check.
    dem = np.full((100, 100), 4000.0)  # 100x100 * 10m = 1,000,000 m^2 = 1 km^2 @ cellsize=10
    ddf, lapse = 4.5, 0.0065
    t_ref, ref_elev, snow_line = 20.0, 1000.0, 3000.0
    result = srm_utils.snowmelt_discharge_m3s(dem, NODATA, cellsize=10.0, t_ref_c=t_ref, ref_elev_m=ref_elev,
                                               snow_line_elev_m=snow_line, ddf_mm_per_c_per_day=ddf,
                                               lapse_rate_c_per_m=lapse)
    expected_temp = t_ref - lapse * (4000.0 - ref_elev)
    expected_melt_mm_day = ddf * expected_temp
    expected_m3_per_day = (expected_melt_mm_day / 1000.0) * 1_000_000.0
    assert result["total_melt_m3_per_day"] == pytest.approx(expected_m3_per_day, rel=1e-3)
    assert result["discharge_m3s"] == pytest.approx(expected_m3_per_day / 86400.0, abs=1e-3)


def test_negative_temperature_never_produces_negative_melt():
    dem = np.full((10, 10), 8000.0)
    result = srm_utils.snowmelt_discharge_m3s(dem, NODATA, cellsize=30.0, t_ref_c=0.0, ref_elev_m=0.0,
                                               snow_line_elev_m=5000.0)
    assert result["discharge_m3s"] == 0.0
    assert all(b["melt_mm_per_day"] >= 0.0 for b in result["bands"])


# ---- multi-day hydrograph ----
def test_hydrograph_daily_blocks_midpoints_and_peak():
    dem = np.full((50, 50), 4000.0)
    temps = [20.0] * 24 + [30.0] * 24 + [10.0] * 24
    h = srm_utils.snowmelt_hydrograph(dem, NODATA, 30.0, temps, ref_elev_m=1000.0, snow_line_elev_m=3000.0)
    assert h["times_hr"] == [12.0, 36.0, 60.0]
    assert h["n_days"] == 3
    assert h["q_m3s"][1] > h["q_m3s"][0] > h["q_m3s"][2] >= 0.0   # follows the temperature
    assert h["peak_m3s"] == h["q_m3s"][1]


def test_hydrograph_matches_single_day_function():
    dem = np.linspace(2500, 5500, 2500).reshape(50, 50)
    temps = [18.0] * 24
    h = srm_utils.snowmelt_hydrograph(dem, NODATA, 30.0, temps, 1500.0, 3500.0)
    one = srm_utils.snowmelt_discharge_m3s(dem, NODATA, 30.0, 18.0, 1500.0, 3500.0)
    assert h["q_m3s"][0] == pytest.approx(one["discharge_m3s"], abs=1e-3)


def test_hydrograph_drops_short_trailing_block_and_rejects_too_little_data():
    dem = np.full((10, 10), 4000.0)
    h = srm_utils.snowmelt_hydrograph(dem, NODATA, 30.0, [20.0] * 24 + [20.0] * 8, 1000.0, 3000.0)
    assert h["n_days"] == 1
    h = srm_utils.snowmelt_hydrograph(dem, NODATA, 30.0, [20.0] * 24 + [20.0] * 12, 1000.0, 3000.0)
    assert h["n_days"] == 2 and h["times_hr"][1] == 30.0
    with pytest.raises(ValueError):
        srm_utils.snowmelt_hydrograph(dem, NODATA, 30.0, [20.0] * 6, 1000.0, 3000.0)
