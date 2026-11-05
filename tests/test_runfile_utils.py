import json
import pytest
import runfile_utils as r

GEOM = {"type": "Polygon", "coordinates": [[[73, 33], [73.1, 33], [73.1, 33.1], [73, 33]]]}


def test_roundtrip_keeps_request_and_drops_unknown_fields():
    body = {"geometry": GEOM, "rain_mm_hr": 40, "duration_hr": 2, "friction": 0.05, "manning_scale": 1.4,
            "inflows": [{"edge": "N", "peak_m3s": 100}], "secret_extra": "x", "custom_dem_id": "0123456789abcdef"}
    doc = r.build_runfile(body, "6.1", "abc")
    out = r.parse_runfile(json.dumps(doc))
    assert out["manning_scale"] == 1.4 and out["inflows"][0]["peak_m3s"] == 100 and "secret_extra" not in out
    assert out["custom_dem_id"] == "0123456789abcdef"


def test_rainfall_series_survives():
    series = {"times_hr": [0, 1, 2], "rates_mm_hr": [0, 30, 0], "label": "gauge"}
    out = r.parse_runfile(json.dumps(r.build_runfile({"geometry": GEOM, "rainfall_series": series}, "6.1")))
    assert out["rainfall_series"] == series


@pytest.mark.parametrize("text,msg", [("not json", "valid JSON"), ('{"kind":"other"}', "not a SAILAB"),
                                       ('{"kind":"sailab-runfile","version":99,"request":{}}', "newer"),
                                       ('{"kind":"sailab-runfile","version":1,"request":{}}', "no area")])
def test_rejects_bad_files(text, msg):
    with pytest.raises(ValueError, match=msg):
        r.parse_runfile(text)


def test_reconstruct_from_run_meta():
    meta = {"geometry": GEOM, "rain_mm_hr": 30, "duration_hr": 3, "friction": 0.06, "cfl": 0.7, "amc": 40,
            "inflows": [], "outflows": [], "infiltration_mm_hr": 2.0}
    doc = r.runfile_from_meta(meta, "6.1", "id1")
    assert doc["reconstructed_from_run_meta"] and doc["request"]["rain_mm_hr"] == 30
    assert r.parse_runfile(json.dumps(doc))["friction"] == 0.06
