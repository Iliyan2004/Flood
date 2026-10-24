import fis_app
import result_report_utils as rr

COASTAL = {"water_level": {"total_water_level_m": 3.76, "basis": "Khan <b>x</b>"}, "flooded_area_km2": 4.4, "max_depth_m": 2.1,
           "wave_runup": {"runup_m": 0.5, "significant_wave_height_m": 1.4, "peak_period_s": 8.8, "foreshore_slope_used": 0.017, "method": "Stockdon"},
           "total_level_with_runup_m": 4.26}
GLOF = {"peak_discharge_m3s": 3737, "volume_m3": 38987000, "suggested_duration_hr": 3.0, "lake_lat": 36.4, "lake_lon": 74.61,
        "method": "documented", "empirical_band": {"documented_equivalent_m3s": {"low": 1.0, "central": 2.0, "high": 3.0}, "basis": "moraine", "n_events": 16}}


def test_html_contains_values_and_escapes():
    h = rr.coastal_report_html(COASTAL, {"return_period_years": 100}, "", "SAILAB", "6.1", "t")
    assert "3.76" in h and "4.26" in h and "&lt;b&gt;" in h and "<b>x</b>" not in h
    g = rr.glof_report_html(GLOF, "Shishper <i>", "", "SAILAB", "6.1", "t")
    assert "3737" in g and "38,987,000" in g and "Shishper &lt;i&gt;" in g and "moraine" in g


def test_pdf_routes():
    c = fis_app.app.test_client()
    r = c.post("/report/coastal.pdf", json={"result": COASTAL, "request": {"return_period_years": 100}})
    assert r.status_code == 200 and r.data[:4] == b"%PDF"
    r = c.post("/report/glof.pdf", json={"result": GLOF, "label": "Shishper"})
    assert r.status_code == 200 and r.data[:4] == b"%PDF"
    assert c.post("/report/glof.pdf", json={}).status_code == 400
    assert c.post("/report/coastal.pdf", json={}).status_code == 400
