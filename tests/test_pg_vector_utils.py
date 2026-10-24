import json
import pytest
import pg_vector_utils as pv


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    pv._failed_until = 0.0
    monkeypatch.delenv("SAILAB_PG_URL", raising=False)
    yield
    pv._failed_until = 0.0


def test_disabled_without_url_returns_none():
    assert pv.enabled() is False
    assert pv.fetch_roads(0, 0, 1, 1) is None and pv.fetch_critical(0, 0, 1, 1) is None


def test_unreachable_database_backs_off_and_returns_none(monkeypatch):
    monkeypatch.setenv("SAILAB_PG_URL", "postgresql://u:p@127.0.0.1:9/none")
    assert pv.fetch_power(0, 0, 1, 1) is None
    assert pv.enabled() is False            # backed off for a minute instead of retrying every call


def test_roads_match_the_file_reader_schema(monkeypatch):
    gj = json.dumps({"type": "LineString", "coordinates": [[73.0, 33.0], [73.2, 33.2], [73.4, 33.0]]})
    monkeypatch.setenv("SAILAB_PG_URL", "x")
    monkeypatch.setattr(pv, "_query", lambda sql, p: [(11, None, "primary", gj, 2, "asphalt", None, "no", None)])
    r = pv.fetch_roads(0, 0, 1, 1)[0]
    assert r["id"] == 11 and r["name"] == "unnamed road" and r["highway"] == "primary"
    assert r["centroid"] == pytest.approx((73.2, 33.0 + 0.2 / 3)) and r["geometry"][0] == (73.0, 33.0)
    assert r["lanes"] == 2 and r["surface"] == "asphalt"


def test_critical_has_hospitals_and_schools_with_extra_fields(monkeypatch):
    monkeypatch.setenv("SAILAB_PG_URL", "x")
    def fake(sql, p):
        return [(5, "CMH", "GENERAL HOSPITALS", 100, "Yes", None, 73.1, 33.6)] if "hospitals" in sql else [(9, None, 450, None, 73.2, 33.7)]
    monkeypatch.setattr(pv, "_query", fake)
    out = pv.fetch_critical(0, 0, 1, 1)
    h = next(x for x in out if x["type"] == "hospital"); s = next(x for x in out if x["type"] == "school")
    assert h["id"] == "hospital_5" and h["subtype"] == "GENERAL HOSPITALS" and h["beds"] == 100
    assert s["id"] == "school_9" and s["name"] == "school" and s["enrollment"] == 450


def test_one_failed_layer_means_fall_back_for_the_whole_category(monkeypatch):
    monkeypatch.setenv("SAILAB_PG_URL", "x")
    monkeypatch.setattr(pv, "_query", lambda sql, p: None if "schools" in sql else [])
    assert pv.fetch_critical(0, 0, 1, 1) is None


def test_every_returned_value_is_json_serialisable_even_when_postgis_gives_decimals(monkeypatch):
    """Regression: Decimal lanes/width/beds made summary.json fail and a whole run lose its roads and facilities."""
    from decimal import Decimal
    gj = json.dumps({"type": "LineString", "coordinates": [[73.0, 33.0], [73.2, 33.2]]})
    monkeypatch.setenv("SAILAB_PG_URL", "x")
    monkeypatch.setattr(pv, "_query", lambda sql, p: [(1, "r", "primary", gj, Decimal("2"), "asphalt", Decimal("7.5"), "no", None)])
    r = pv.fetch_roads(0, 0, 1, 1)
    json.dumps(r)
    assert r[0]["lanes"] == 2 and r[0]["width"] == 7.5
    monkeypatch.setattr(pv, "_query", lambda sql, p: [(5, "CMH", "H", Decimal("120"), "Yes", None, Decimal("73.1"), Decimal("33.6"))] if "hospitals" in sql else [(9, "S", Decimal("300"), None, Decimal("73.2"), Decimal("33.7"))])
    json.dumps(pv.fetch_critical(0, 0, 1, 1))


def test_roads_inside_islamabad_use_the_drivable_set_and_outside_use_national(monkeypatch):
    seen = []
    gj = json.dumps({"type": "LineString", "coordinates": [[73.0, 33.0], [73.1, 33.1]]})

    def fake(sql, p):
        seen.append(sql)
        if "study_area" in sql:
            return [(INSIDE["v"],)]
        return [(1, "Main", "primary", gj, "2;3", "asphalt", "7 m", "yes", "viaduct"), (2, None, "residential", gj, None, None, None, None, "no")]
    INSIDE = {"v": True}
    monkeypatch.setenv("SAILAB_PG_URL", "x")
    monkeypatch.setattr(pv, "_query", fake)
    r = pv.fetch_roads(0, 0, 1, 1)
    assert "isb.roads_drivable" in seen[-1] and "pk.roads" not in seen[-1]
    assert r[0]["lanes"] == 2 and r[0]["bridge"] is True and r[1]["bridge"] is False and r[1]["lanes"] is None
    INSIDE["v"] = False
    r = pv.fetch_roads(0, 0, 1, 1)
    assert "pk.roads" in seen[-1] and r[0]["width"] == 7.0


def test_text_numbers_are_parsed_defensively():
    assert pv._num("2", as_int=True) == 2 and pv._num("2;3", as_int=True) == 2 and pv._num("7.5 m") == 7.5
    assert pv._num(None) is None and pv._num("none") is None and pv._num("") is None


def test_study_area_check_returns_none_when_postgis_is_down(monkeypatch):
    monkeypatch.setenv("SAILAB_PG_URL", "x")
    monkeypatch.setattr(pv, "_query", lambda sql, p: None)
    assert pv.inside_study_area(0, 0, 1, 1) is None and pv.fetch_roads(0, 0, 1, 1) is None
