import inspect, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dams_utils


def test_data_signature_changes_when_a_dam_file_changes(tmp_path, monkeypatch):
    b, r, c = tmp_path / "b.shp", tmp_path / "r.shp", tmp_path / "c.csv"
    for p in (b, r, c):
        p.write_text("x")
    for name, p in (("BARRIERS_SHP", b), ("RESERVOIRS_SHP", r), ("SPILLWAY_EVENTS_CSV", c)):
        monkeypatch.setattr(dams_utils, name, str(p))
    before = dams_utils.data_signature()
    assert before == dams_utils.data_signature()          # stable when nothing changed
    b.write_text("xyz-changed")
    assert dams_utils.data_signature() != before


def test_data_signature_absent_when_no_files(monkeypatch):
    for name in ("BARRIERS_SHP", "RESERVOIRS_SHP", "SPILLWAY_EVENTS_CSV"):
        monkeypatch.setattr(dams_utils, name, "/nonexistent/x")
    assert dams_utils.data_signature() == "absent"


def test_pipeline_signature_tracks_version_and_is_in_the_run_cache_key(monkeypatch):
    import fis_app
    a = fis_app.pipeline_signature()
    monkeypatch.setattr(fis_app, "PIPELINE_VERSION", fis_app.PIPELINE_VERSION + 1)
    assert fis_app.pipeline_signature() != a
    assert "pipeline_signature()" in inspect.getsource(fis_app.run)    # actually part of the key
