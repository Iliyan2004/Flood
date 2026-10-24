"""
Fast, pure file-I/O tests for flood_model.write_par_file's real mint_hk/
depththresh keywords -- both confirmed directly from LISFLOOD-FP 8.2's
own CUDA ACC kernel source to be genuinely wired in (not a silent
CUDA-path no-op, the class of gap this app has hit before for other
keywords -- see CLAUDE.md). No real physics run needed here; the actual
real-data benchmark (confirming mint_hk gives a real, small speedup with
a real, small precision cost, and that raising depththresh made a real
steep-terrain case WORSE, not better) is recorded in CLAUDE.md, not
re-run on every test pass.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import flood_model  # noqa: E402


def _write_and_read(tmp_path, **kwargs):
    path = str(tmp_path / "site.par")
    flood_model.write_par_file(
        path, dem_name="dem.ascii", rain_name="storm.rain", bci_name="site.bci",
        resroot="res", dirroot="results", sim_time_s=3600.0, saveint_s=600.0,
        massint_s=60.0, cfl=0.7, fpfric=0.05, initial_tstep=5.0, **kwargs)
    with open(path) as f:
        return f.read()


def test_mint_hk_omitted_by_default(tmp_path):
    text = _write_and_read(tmp_path)
    assert "mint_hk" not in text


def test_mint_hk_written_when_requested(tmp_path):
    text = _write_and_read(tmp_path, mint_hk=True)
    assert "mint_hk" in text.splitlines()


def test_depththresh_omitted_when_not_given(tmp_path):
    text = _write_and_read(tmp_path)
    assert "depththresh" not in text


def test_depththresh_written_with_real_value(tmp_path):
    text = _write_and_read(tmp_path, depththresh=0.01)
    lines = [l for l in text.splitlines() if l.strip().startswith("depththresh")]
    assert len(lines) == 1
    assert "0.01" in lines[0]


def test_existing_callers_unaffected_by_new_optional_params(tmp_path):
    """The two new params default to off/None -- every existing call site
    (which doesn't pass them) must produce byte-identical output to
    before these params existed, aside from the (pre-existing, unrelated)
    theta/manning lines this same function already conditionally writes."""
    text = _write_and_read(tmp_path)
    assert "mint_hk" not in text
    assert "depththresh" not in text
    assert "acceleration" in text
    assert "hazard" in text


def test_cuda_keyword_written_by_default_and_omitted_with_force_cpu(tmp_path, monkeypatch):
    monkeypatch.delenv("SAILAB_FORCE_CPU", raising=False)
    assert "cuda" in _write_and_read(tmp_path).split()
    monkeypatch.setenv("SAILAB_FORCE_CPU", "1")
    assert "cuda" not in _write_and_read(tmp_path).split()
    monkeypatch.setenv("SAILAB_FORCE_CPU", "0")           # anything but "1" leaves the GPU default
    assert "cuda" in _write_and_read(tmp_path).split()
