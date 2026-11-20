"""python manual/build_manual.py  ->  manual/SAILAB_User_Manual.pdf (+ .html)"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import m_core, ch1, ch2, ch3
try:
    import ch4
except ImportError:
    ch4 = None
VERSION, DATE = "6.1", "November 2026"
HERE = os.path.dirname(os.path.abspath(__file__))
body = [ch1.ch1(), ch1.ch2(), ch1.ch3(), ch1.ch4(), ch2.ch5a()]
if ch4: body += [ch4.ch5b(), ch4.ch6(), ch4.ch7()]
body += [ch3.ch8(), ch3.ch9(), ch3.ch10(), ch3.ch11()]
html = ('<!doctype html><html lang="en"><head><meta charset="utf-8"><title>SAILAB User Manual</title>'
        f'<link rel="stylesheet" href="manual.css"></head><body>{ch1.cover(VERSION, DATE)}{m_core.toc_html()}{"".join(body)}'
        '<div class="back"><div class="brand">SAILAB</div><p>From rainfall to risk, modeled.</p><p class="small">AI Geo Navigators, Islamabad, Pakistan</p></div></body></html>')
out = f"{HERE}/SAILAB_User_Manual.html"; open(out, "w").write(html)
from weasyprint import HTML
HTML(filename=out, base_url=HERE).write_pdf(f"{HERE}/SAILAB_User_Manual.pdf")
print("chapters/sections:", len(m_core.TOC))
