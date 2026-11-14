"""The three modelling pages share one shell; the body's data-mode picks the panels. Every mode needs its own
panels present and the mode links in the header."""
import os, sys, re
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import fis_app

PANELS = {"physics": ["runBtn", "runStatusBox", "batchRunBox", "protectBox"],
          "glof": ["glofSource", "glofBox", "runBtn", "boundaryList", "glacierDispBox"],
          "coastal": ["coastalBtn", "coastalPreviewBtn"]}


def _page(path):
    return fis_app.app.test_client().get(path).get_data(as_text=True)


def test_each_mode_sets_its_data_mode_and_title():
    for path, mode, title in [("/app", "physics", "SAILAB Physics"), ("/glof", "glof", "SAILAB GLOF"), ("/coastal", "coastal", "SAILAB Coastal")]:
        h = _page(path)
        assert 'data-mode="%s"' % mode in h
        assert "<title>%s</title>" % title in h


def test_header_links_all_four_modes_and_marks_the_current_one():
    h = _page("/glof")
    nav = re.search(r'<div class="hdr-nav">(.*?)</div>', h, re.S).group(1)
    for href in ("/instant", "/app", "/glof", "/coastal"):
        assert 'href="%s"' % href in nav
    assert re.search(r'<a href="/glof" class="on">', nav)


def test_glof_and_coastal_panels_exist_in_their_pages():
    g, c = _page("/glof"), _page("/coastal")
    for el in ("glofSource", "glofBox", "boundaryList", "runBtn"):
        assert 'id="%s"' % el in g
    for el in ("coastalBtn", "coastalPreviewBtn"):
        assert 'id="%s"' % el in c


def test_sections_are_tagged_with_the_modes_that_show_them():
    h = _page("/app")
    assert re.search(r'data-pane="scenario" data-modes="glof">\s*<h3>GLOF scenario</h3>', h)
    assert re.search(r'data-pane="surge" data-modes="coastal"', h)
    assert re.search(r'data-pane="glacier" data-modes="glof">\s*<h3>Glacier monitoring</h3>', h)


def test_landing_has_four_modes_and_instant_in_the_nav():
    h = fis_app.app.test_client().get("/").get_data(as_text=True)
    assert '<a class="btn p" href="/instant">SAILAB Instant</a>' in h
    for href in ("/app", "/instant", "/glof", "/coastal"):
        assert 'href="%s">Open SAILAB' % href in h
