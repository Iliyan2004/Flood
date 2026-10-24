import pytest

import glof_events_utils as ge
import glof_utils

needs_events = pytest.mark.skipif(not ge.events_available(), reason="local HMAGLOFDB catalogue missing")
needs_pmd = pytest.mark.skipif(not __import__("os").path.exists(glof_utils.PMD_LAKES_SHP), reason="PMD lake inventory missing")


@needs_events
def test_catalogue_counts_and_pakistan_data_gaps():
    c = ge.catalogue_summary()
    assert c["total_events"] == 774 and c["pakistan_events"] == 151
    assert c["pakistan_with_peak_discharge"] == 3 and c["pakistan_with_volume"] <= 12


@needs_events
def test_events_near_shisper_sorted_and_within_radius():
    ev = ge.events_near(74.57, 36.35, radius_km=30, max_results=50)
    assert ev and all(e["distance_km"] <= 30 for e in ev)
    assert [e["distance_km"] for e in ev] == sorted(e["distance_km"] for e in ev)
    assert any(e["lake_name"].lower().startswith("shisp") for e in ev)
    assert ge.events_near(80.0, 10.0, radius_km=50) == []  # nowhere near Pakistan


@needs_events
def test_popov_validation_matches_formula_and_type_split():
    v = ge.popov_validation()
    assert v["all"]["n"] >= 30
    for p in v["points"]:
        assert p["popov_m3s"] == pytest.approx(glof_utils.popov_peak_discharge_m3s(p["volume_m3"]))
    assert v["by_type"]["Ice dammed"]["ratio_median"] > 5      # Popov badly overestimates ice-dammed lakes
    assert 0.5 < v["by_type"]["Moraine dammed"]["ratio_median"] < 2.0


@needs_events
def test_empirical_band_orders_and_type_fallback():
    b = ge.empirical_band(1000.0, "Morainic")
    e = b["documented_equivalent_m3s"]
    assert e["low"] < e["central"] < e["high"] and b["basis"] == "Moraine dammed"
    ice = ge.empirical_band(1000.0, "Icedammed")
    assert ice["documented_equivalent_m3s"]["central"] < e["central"] and "ice-dammed" in ice["caveat"].lower()
    unk = ge.empirical_band(1000.0, "BedRock")
    assert unk["basis"] == "all lake types"


@needs_pmd
def test_pmd_lakes_near_returns_normalised_rows():
    rows = glof_utils.find_pmd_lakes_near(74.57, 36.35, radius_km=40, max_results=5)
    assert rows and [r["distance_km"] for r in rows] == sorted(r["distance_km"] for r in rows)
    assert all(r["dam_type"] in ("Morainic", "Icedammed", "BedRock", "Mixed", "Unknown") for r in rows)
    r0 = glof_utils.pmd_lake_by_id(rows[0]["pmd_id"])
    assert r0["volume_m3"] == pytest.approx(glof_utils.huggel_volume_from_area_m2(r0["area_m2"]), rel=1e-3)


def test_pmd_dam_type_typos_are_normalised():
    n = glof_utils._norm_pmd_dam_type
    assert n("Morianic") == n("Moainic") == n("Moarinic") == "Morainic"
    assert n("Icedammed") == "Icedammed" and n("IceMoraine") == "Mixed" and n("BebRock") == "BedRock" and n("Erosion") == "Unknown"
