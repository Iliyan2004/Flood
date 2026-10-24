import importlib, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import data_paths


def test_every_default_path_is_a_string_and_dem_is_required():
    assert all(isinstance(p, str) for p in data_paths.PATHS.values())
    rows = {n: req for n, p, ok, req in data_paths.check_data_paths()}
    assert rows["PAKISTAN_DEM"] is True


def test_env_override_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("SAILAB_PAKISTAN_DEM", str(tmp_path / "x.tif"))
    reloaded = importlib.reload(data_paths)
    try:
        assert reloaded.get("PAKISTAN_DEM") == str(tmp_path / "x.tif")
        assert ("PAKISTAN_DEM", str(tmp_path / "x.tif")) in reloaded.missing_required()
    finally:
        monkeypatch.delenv("SAILAB_PAKISTAN_DEM")
        importlib.reload(data_paths)


def test_missing_required_empty_when_dem_exists(monkeypatch, tmp_path):
    f = tmp_path / "dem.tif"; f.write_text("x")
    monkeypatch.setenv("SAILAB_PAKISTAN_DEM", str(f))
    reloaded = importlib.reload(data_paths)
    try:
        assert reloaded.missing_required() == []
    finally:
        monkeypatch.delenv("SAILAB_PAKISTAN_DEM")
        importlib.reload(data_paths)
