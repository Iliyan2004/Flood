"""The user manual is linked from the landing page and served as a PDF."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import fis_app


def test_landing_links_to_the_manual():
    html = fis_app._landing_html()
    assert 'href="/manual"' in html and 'href="/manual.pdf"' in html and 'id="manual"' in html


def test_manual_routes_serve_pdf_when_installed(tmp_path, monkeypatch):
    pdf = tmp_path / "m.pdf"; pdf.write_bytes(b"%PDF-1.4\n%fake\n")
    monkeypatch.setattr(fis_app, "MANUAL_PDF", str(pdf))
    c = fis_app.app.test_client()
    r = c.get("/manual"); assert r.status_code == 200 and r.mimetype == "application/pdf" and "attachment" not in r.headers.get("Content-Disposition", "")
    r = c.get("/manual.pdf"); assert r.status_code == 200 and "attachment" in r.headers.get("Content-Disposition", "")


def test_manual_missing_gives_clear_404(tmp_path, monkeypatch):
    monkeypatch.setattr(fis_app, "MANUAL_PDF", str(tmp_path / "nope.pdf"))
    r = fis_app.app.test_client().get("/manual")
    assert r.status_code == 404 and b"not installed" in r.data
