"""The entry page is the landing page; the main app lives at /app."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import fis_app


def test_root_is_landing_page_and_links_to_app():
    r = fis_app.app.test_client().get("/")
    html = r.get_data(as_text=True)
    assert r.status_code == 200
    assert "SAILAB" in html and "Built Environment" in html and 'href="/app"' in html
    assert "__APP_VERSION__" not in html and fis_app.APP_VERSION in html
    assert "mapboxgl" not in html          # the landing page must not be the main app


def test_app_route_serves_main_page():
    html = fis_app.app.test_client().get("/app").get_data(as_text=True)
    assert "mapboxgl" in html


def test_import_handover_skips_landing():
    r = fis_app.app.test_client().get("/?import=abc123")
    assert r.status_code == 302 and r.headers["Location"].startswith("/app?import=abc123")


def test_landing_script_parses():
    import re, subprocess, shutil, tempfile
    node = shutil.which("node")
    if not node:
        import pytest; pytest.skip("no node")
    html = fis_app.app.test_client().get("/").get_data(as_text=True)
    js = re.findall(r"<script>(.*?)</script>", html, re.S)[-1]
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
        f.write(js)
    assert subprocess.run([node, "--check", f.name]).returncode == 0


def test_landing_offers_two_separate_links():
    html = fis_app.app.test_client().get("/").get_data(as_text=True)
    assert 'id="openInstant" href="/instant"' in html and 'id="open" href="/app"' in html
    assert "SAILAB Instant" in html and "SAILAB Physics" in html


def test_instant_page_serves_with_token_filled_and_links_back():
    r = fis_app.app.test_client().get("/instant")
    html = r.get_data(as_text=True)
    assert r.status_code == 200 and "__MAPBOX_TOKEN__" not in html
    assert 'href="/app"' in html and "/area/instant-flood-overlay" in html and "/area/instant-scenarios" in html and "Flood depth" in html


def test_instant_page_script_parses():
    import re, subprocess, shutil, tempfile
    node = shutil.which("node")
    if not node:
        import pytest; pytest.skip("no node")
    html = fis_app.app.test_client().get("/instant").get_data(as_text=True)
    js = re.findall(r"<script>(.*?)</script>", html, re.S)[-1]
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
        f.write(js)
    assert subprocess.run([node, "--check", f.name]).returncode == 0
