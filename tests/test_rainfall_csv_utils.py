"""
Fast, pure-logic tests for rainfall_csv_utils.py's real CSV parsing and
nearest-station selection. No network dependency (geocode_location_text
itself is a thin, deterministic wrapper around a real live Mapbox call
and is exercised, along with the full end-to-end route wiring, via a
manual live test against the real MACH-15min sample CSV; see CLAUDE.md).
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import rainfall_csv_utils as rcu  # noqa: E402


MACH_STYLE_CSV = """mach_title,location,time_start_pkt,time_end_pkt,precipitation_15min_mm,precipitation_total_mm
MACH 1,"I-8, Islamabad",9/1/2026 0:00,9/1/2026 0:15,-3.4,0
MACH 1,"I-8, Islamabad",9/1/2026 0:15,9/1/2026 0:30,0,0
MACH 1,"I-8, Islamabad",9/1/2026 0:30,9/1/2026 0:45,10.0,10.0
MACH 1,"I-8, Islamabad",9/1/2026 0:45,9/1/2026 1:00,5.0,15.0
MACH 2,"E-11, Islamabad",9/1/2026 0:00,9/1/2026 0:15,0,0
MACH 2,"E-11, Islamabad",9/1/2026 0:15,9/1/2026 0:30,2.0,2.0
MACH 2,"E-11, Islamabad",9/1/2026 0:30,9/1/2026 0:45,0,2.0
MACH 2,"E-11, Islamabad",9/1/2026 0:45,9/1/2026 1:00,0,2.0
"""

LATLON_CSV = """station,lat,lon,time_start_pkt,precip_mm
Gauge A,33.70,73.05,9/1/2026 0:00,0
Gauge A,33.70,73.05,9/1/2026 0:15,5.0
Gauge B,24.85,66.99,9/1/2026 0:00,0
Gauge B,24.85,66.99,9/1/2026 0:15,20.0
"""


def test_parses_real_mach_style_csv_into_per_station_series():
    stations = rcu.parse_rainfall_csv(MACH_STYLE_CSV)
    assert set(stations.keys()) == {"MACH 1", "MACH 2"}
    m1 = stations["MACH 1"]
    assert m1["location_text"] == "I-8, Islamabad"
    assert m1["n_points"] == 4
    # elapsed hours from each station's OWN first real timestamp
    assert m1["times_hr"] == [0.0, 0.25, 0.5, 0.75]


def test_negative_precip_clamped_to_zero_not_rejected():
    """Real sensor-reset artifact (a negative value in the reference
    sample) must become 0.0, not crash the parse or get silently
    dropped as a missing row."""
    stations = rcu.parse_rainfall_csv(MACH_STYLE_CSV)
    m1 = stations["MACH 1"]
    assert m1["rates_mm_hr"][0] == 0.0  # the real -3.4 row, clamped
    assert m1["n_points"] == 4  # row still counted, not dropped


def test_precip_per_interval_converted_to_real_mm_per_hr():
    """A real 15-minute (0.25hr) interval's mm value must convert to
    mm/hr by dividing by the interval's own real duration, not treated
    as already being a rate."""
    stations = rcu.parse_rainfall_csv(MACH_STYLE_CSV)
    m1 = stations["MACH 1"]
    # row 3 (index 2): 10.0mm over a real 15-min (0.25hr) interval -> 40 mm/hr
    assert m1["rates_mm_hr"][2] == pytest.approx(40.0)


def test_real_total_mm_sums_only_positive_clamped_values():
    stations = rcu.parse_rainfall_csv(MACH_STYLE_CSV)
    m1 = stations["MACH 1"]
    assert m1["total_mm"] == pytest.approx(15.0)  # 0 (clamped) + 0 + 10 + 5


def test_lat_lon_columns_used_directly_without_geocoding():
    """When a CSV already carries real lat/lon columns, those must be
    used as-is -- resolve_station_coordinates must never call the
    (network-dependent) geocoder for a station that already has one."""
    stations = rcu.parse_rainfall_csv(LATLON_CSV)
    assert stations["Gauge A"]["lat"] == 33.70
    assert stations["Gauge A"]["lon"] == 73.05
    # geocode with an intentionally-broken token -- must not be called
    # at all (no network error) since both stations already have coords
    rcu.resolve_station_coordinates(stations, mapbox_token="not-a-real-token")
    assert stations["Gauge A"]["lat"] == 33.70  # unchanged


def test_nearest_station_picks_real_closest_by_haversine():
    stations = rcu.parse_rainfall_csv(LATLON_CSV)
    # A point near Islamabad (Gauge A) should pick Gauge A, not the real
    # Karachi-area Gauge B, ~1100km away.
    key, dist_km = rcu.nearest_station(stations, lon=73.06, lat=33.69)
    assert key == "Gauge A"
    assert dist_km < 5.0


def test_nearest_station_skips_stations_with_no_resolved_coordinate():
    stations = rcu.parse_rainfall_csv(MACH_STYLE_CSV)  # no lat/lon, not geocoded here
    key, dist_km = rcu.nearest_station(stations, lon=73.06, lat=33.69)
    assert key is None
    assert dist_km is None


def test_missing_required_columns_raises_rainfall_csv_error():
    with pytest.raises(rcu.RainfallCsvError):
        rcu.parse_rainfall_csv("a,b,c\n1,2,3\n")


def test_missing_station_and_location_columns_raises():
    with pytest.raises(rcu.RainfallCsvError):
        rcu.parse_rainfall_csv("time_start_pkt,precip_mm\n9/1/2026 0:00,5.0\n")
