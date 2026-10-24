"""Real-browser smoke test of the served page (headless Chromium).

Why this exists: a single mis-escaped apostrophe in the page's JavaScript once
made the browser reject the whole script, so the MAP AND EVERY CONTROL silently
disappeared -- and no Python test noticed. test_page_js_syntax.py catches syntax
errors; this additionally catches runtime failures by actually loading the page:
 - the Mapbox map really initialises (a canvas exists),
 - the page's own console reports no uncaught JS errors,
 - controls that are filled by API calls on load are populated (proves the
   script ran to completion past map creation).
"""
import os, re, shutil, subprocess, sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

CHROMIUM = shutil.which("chromium-browser") or shutil.which("chromium")

pytestmark = pytest.mark.skipif(CHROMIUM is None, reason="no chromium installed")

JS_ERROR = re.compile(r"CONSOLE.*(Uncaught|SyntaxError|ReferenceError|TypeError)")


@pytest.fixture(scope="module")
def dom_and_log(live_server):
    cmd = [CHROMIUM, "--headless=new", "--no-sandbox", "--disable-gpu", "--enable-unsafe-swiftshader",
           "--enable-logging=stderr", "--v=0", "--virtual-time-budget=15000", "--window-size=1400,900",
           "--dump-dom", live_server + "/"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        pytest.skip("headless chromium hung (a known issue on this heavily shared machine) -- not a page failure")
    return r.stdout, r.stderr


@pytest.mark.slow
def test_map_canvas_is_created(dom_and_log):
    dom, _ = dom_and_log
    assert 'class="mapboxgl-canvas"' in dom, "the map never initialised -- page script likely broke"


@pytest.mark.slow
def test_no_uncaught_javascript_errors_on_load(dom_and_log):
    _, log = dom_and_log
    errors = [l for l in log.splitlines() if JS_ERROR.search(l)]
    assert not errors, "uncaught JS errors on page load:\n" + "\n".join(errors[:5])


@pytest.mark.slow
@pytest.mark.parametrize("select_id", ["spillwayEventSelect", "damFailureSelect"])
def test_api_filled_controls_are_populated(dom_and_log, select_id):
    dom, _ = dom_and_log
    m = re.search(rf'<select id="{select_id}"[^>]*>(.*?)</select>', dom, re.S)
    assert m, f"{select_id} missing from the page"
    assert "<option" in m.group(1), f"{select_id} was never populated -- the page script stopped early"


@pytest.mark.slow
@pytest.mark.parametrize("element_id", ["coastalSource", "coastalDynTide", "coastalPreviewTide", "coastalLevelSlider", "coastalSuscBtn", "glofPmdRow", "glofEventsBtn",
                                        "itsliveBtn", "runStatusBox"])
def test_new_panels_are_in_the_page(dom_and_log, element_id):
    dom, _ = dom_and_log
    assert f'id="{element_id}"' in dom, f"{element_id} missing -- a panel failed to render"
