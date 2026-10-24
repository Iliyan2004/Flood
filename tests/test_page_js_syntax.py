"""The whole UI is one big Python f-string containing JavaScript. A single
mis-escaped quote there (e.g. an apostrophe written \\' instead of \\\\') makes
the browser reject the ENTIRE script, so the map and every control silently
vanish -- and no Python test notices. This renders the served page and runs
Node's parser over its inline script."""
import os, re, shutil, subprocess, sys, tempfile
import pytest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_served_page_inline_javascript_parses():
    import fis_app
    html = fis_app.app.test_client().get("/").get_data(as_text=True)
    scripts = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, re.S)
    assert scripts, "page has no inline script at all"
    for i, js in enumerate(scripts):
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
            f.write(js)
            path = f.name
        try:
            r = subprocess.run(["node", "--check", path], capture_output=True, text=True)
            assert r.returncode == 0, f"inline script #{i} has a JS syntax error:\n{r.stderr[:800]}"
        finally:
            os.remove(path)
