# SAILAB paper (draft v0.2)

Output: `SAILAB_paper_v0.2.pdf` (and `.html`). Build: `python paper/make_all.py`.

* `build_paper.py` assembles the document; text is in `content1-4.py`, references in `refs.py`, style in `paper.css`.
* `data/` holds every result file the text reads; unfilled results show as red TODO markers.
* Experiments: `bench_scaling.py` (runtime), `skill_metrics.py` (AUC / Peirce / Heidke), `run_sensitivity.py`, `case_study.py`,
  `monitoring_latency.py`. The last three need the solver, the terrain and the live app (see the TODOs in the PDF).
* `study.py` holds the multi-model study scores (31 Oct 2026), transcribed from its report.
