"""Assemble the paper: python paper/build_paper.py  ->  paper/SAILAB_paper_v0.2.html and .pdf"""
import json, os, sys, numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from doc import Doc, load, DATA
from refs import REFS
import content1 as c1, content2 as c2, content3 as c3

HERE = os.path.dirname(os.path.abspath(__file__))

def prev_rows():
    rows = json.load(open(f"{DATA}/preview_vs_full_windows.json"))
    sl = np.array([r["slope"] for r in rows]); q = np.quantile(sl, [1/3, 2/3])
    def line(label, sel):
        s = [r for r, k in zip(rows, sel) if k]
        cs = [r["csi"] for r in s if r["csi_defined"]]
        return (label, f"{len(s)}", f"{np.mean(cs):.2f}", f"{np.mean([r['pod'] for r in s]):.2f}", f"{np.mean([r['far'] for r in s]):.2f}",
                f"{100*np.mean([r['truth_frac'] for r in s]):.1f} %", f"{100*np.mean([r['prev_frac'] for r in s]):.1f} %",
                f"{np.median([r['truth_max'] for r in s]):.2f} / {np.median([r['prev_max'] for r in s]):.2f}")
    allm = np.ones(len(rows), bool)
    return [line("All windows", allm), line(f"Flattest third (< {q[0]:.1f} %)", sl <= q[0]), line(f"Middle third", (sl > q[0]) & (sl <= q[1])), line(f"Steepest third (> {q[1]:.0f} %)", sl > q[1])]

def collect():
    R = {"skill": load("skill_metrics.json"), "scaling": load("scaling.json"), "prev_rows": prev_rows(),
         "sens": load("sensitivity.json"), "case": load("case_study.json"), "mon": load("monitoring_latency.json"),
         "has_ui": os.path.exists(os.path.join(HERE, "figures", "fig_ui.png"))}
    return R

def build():
    R = collect(); D = Doc(REFS)
    D.add(c1.front(R))
    for f in (c1.intro, c1.related, c1.system, c2.engine, c2.damage, c2.mitigation, c2.monitor, c3.evaluation, c3.perf, c3.case, c3.discussion, c3.conclusion):
        D.add(f(D, R))
    body, refs_html, order = D.render()
    D.parts = []
    D.add(c3.appendices(D, R)); appx, _, order2 = D.render()
    html = f'<!doctype html><html lang="en"><head><meta charset="utf-8"><title>SAILAB: a GPU-accelerated platform for flood simulation, damage, mitigation and monitoring</title><link rel="stylesheet" href="paper.css"></head><body>{body}<h2 style="string-set: runhead \'References\'">References</h2>{refs_html}<div class="pagebreak"></div>{appx}</body></html>'
    out = os.path.join(HERE, "SAILAB_paper_v0.2.html")
    open(out, "w").write(html)
    from weasyprint import HTML
    HTML(filename=out, base_url=HERE).write_pdf(os.path.join(HERE, "SAILAB_paper_v0.2.pdf"))
    print("refs cited:", len(order), "| figs", D.nf, "tabs", D.nt, "eqs", D.ne)

if __name__ == "__main__":
    build()
