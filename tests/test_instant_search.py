"""The search boxes read coordinates in several formats. The parser is plain JavaScript in the page, so run it under node."""
import json, os, re, shutil, subprocess, sys
import pytest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _parse(js_source, queries):
    a = js_source.index("const PK = ")
    b = js_source.index("// ---------- search (places + tehsils)") if "// ---------- search (places + tehsils)" in js_source else js_source.index("function geoRender()")
    code = js_source[a:b].replace("const PK", "var PK") + "\nconst T=" + json.dumps(queries) + ";console.log(JSON.stringify(T.map(q=>parseLatLon(q))));"
    out = subprocess.run(["node", "-e", code], capture_output=True, text=True, check=True).stdout
    return json.loads(out)


CASES = [("33.67, 73.07", (33.67, 73.07)), ("33.67 73.07", (33.67, 73.07)), ("33.67N 73.07E", (33.67, 73.07)),
         ("73.07, 33.67", (33.67, 73.07)), ("33.6700°N, 73.0700°E", (33.67, 73.07)), ("24.86, 67.0", (24.86, 67.0))]


def _check(res):
    for (q, (lat, lon)), r in zip(CASES, res):
        assert r and abs(r["lat"] - lat) < 1e-6 and abs(r["lon"] - lon) < 1e-6 and r["inPakistan"], q
    assert res[len(CASES)] is None                       # plain text is not a coordinate
    assert res[len(CASES) + 1]["inPakistan"] is False    # valid but outside Pakistan
    dms = res[len(CASES) + 2]
    assert abs(dms["lat"] - 33.67) < 1e-3 and abs(dms["lon"] - 73.07) < 1e-3


QUERIES = [q for q, _ in CASES] + ["islamabad", "10, 20", "33°40'12\"N 73°04'12\"E"]


def test_instant_page_parses_coordinates():
    _check(_parse(open(os.path.join(ROOT, "instant.html"), encoding="utf-8").read(), QUERIES))


def test_app_page_parses_coordinates():
    import fis_app
    html = fis_app.app.test_client().get("/app").get_data(as_text=True)
    _check(_parse(html, QUERIES))


def test_instant_has_no_learned_model_option_and_a_fixed_legend():
    h = open(os.path.join(ROOT, "instant.html"), encoding="utf-8").read()
    assert 'id="learned"' not in h and "'learned'" not in h
    assert 'id="legendRows"' not in h
