"""Blocks that depend on late experiments: sensitivity, case study, monitoring latency. Each reads its raw JSON from paper/data."""
import numpy as np
from doc import todo

AREAS = ["A_manchar_edge", "B_north_dadu", "C_shikarpur"]
LABELS = {"base": "Base case (n 0.05, no infiltration, closed edges, 253.5 mm / 48 h)", "rain_x0.5": "Rain × 0.5", "rain_x0.75": "Rain × 0.75", "rain_x1.25": "Rain × 1.25",
          "rain_x1.5": "Rain × 1.5", "rain_x2": "Rain × 2", "n_0.03": "Manning n 0.03", "n_0.08": "Manning n 0.08", "n_0.12": "Manning n 0.12",
          "infil_2": "Infiltration 2 mm/h", "infil_5": "Infiltration 5 mm/h", "infil_10": "Infiltration 10 mm/h", "open_edges": "Open boundaries (all edges)",
          "window_10d": "Longer storm: 424.6 mm over 10 days"}


def sens_table(raw):
    runs = raw["runs"]
    out = {}
    for v in LABELS:
        ok = [runs[f"{a}|{v}"] for a in AREAS if f"{a}|{v}" in runs and "scores" in runs[f"{a}|{v}"]]
        if not ok:
            continue
        sc = [r["scores"]["0.05"] for r in ok]
        out[v] = {"n": len(ok), "csi": float(np.mean([s["CSI"] for s in sc])), "pod": float(np.mean([s["POD"] for s in sc])), "far": float(np.mean([s["FAR"] for s in sc])),
                  "ratio": float(np.mean([s["area_ratio"] for s in sc])), "obs": float(np.mean([r["obs_frac"] for r in ok])),
                  "csi_by_thr": {t: float(np.mean([r["scores"][t]["CSI"] for r in ok])) for t in ok[0]["scores"]}}
    return out


def abstract_sens(raw):
    t = sens_table(raw)
    if "base" not in t:
        return ""
    csis = [v["csi"] for k, v in t.items()]
    best = max(csis)
    return (f"In {len(t) - 1} sensitivity variants (rainfall, roughness, infiltration, boundaries, storm length) mean CSI ranged from {min(csis):.2f} to {best:.2f}, "
            f"against 0.50 for flooding everything.")


def sens_block(D, raw):
    t = sens_table(raw)
    if "base" not in t:
        return f"<p>{todo('sensitivity runs failed or missing')}</p>"
    rows = []
    for v, d in t.items():
        rows.append((LABELS[v], f"{d['n']}", f"{d['csi']:.3f}", f"{d['csi'] - t['base']['csi']:+.3f}", f"{d['pod']:.2f}", f"{d['far']:.2f}", f"{d['ratio']:.2f}×"))
    out = ["""<p>We varied one input at a time around the base case of Section [[s:design]], on all three areas, with the same scoring ([[t:sens]], [[f:sens]]).</p>"""]
    out.append(D.table("sens", "Sensitivity of SAILAB’s score to its inputs (wet above 5 cm), mean over the areas that completed.",
                       ["Variant", "Areas", "Mean CSI", "Δ CSI", "POD", "FAR", "Wet area / observed"], rows, numcols=(1, 2, 3, 4, 5, 6), hl=(0,),
                       note="“Flood everything” scores 0.500 on the same areas. Δ CSI is relative to the base case."))
    out.append(D.fig("sens", "fig_sens.png", "Mean CSI for each variant (bars) against the base case and the “flood everything” score (dotted).", "92%"))
    csis = {v: d["csi"] for v, d in t.items()}
    hi = max(csis, key=csis.get); lo = min(csis, key=csis.get)
    out.append(f"""<p>Across the variants mean CSI ranges from {csis[lo]:.3f} ({LABELS[lo].split(' (')[0].lower()}) to {csis[hi]:.3f} ({LABELS[hi].split(' (')[0].lower()}). {'No variant clearly exceeds the “flood everything” score of 0.500.' if csis[hi] < 0.53 else 'At least one variant exceeds 0.53, so the choice of input matters for the score.'} Changing an input mainly changes how much area is wet (the last column) and therefore moves CSI along the trade between hits and false alarms; it does not make the depth pattern follow the observed extent, which is the question the skill measures of Section [[s:skill]] address.</p>""")
    return "\n".join(out)


def mon_block(D, raw):
    lat = raw["fetch_latency_s"]
    rows = []
    for i, tr in enumerate(raw["trials"]):
        rows.append((f"{i + 1}", f"{tr['forecast_peak']:.0f}", f"{tr['forecast_duration_hr']:.0f}", f"{tr['check_call_s']:.2f}", f"{tr['alert_received_after_s']:.2f}" if tr["alert_received_after_s"] is not None else "–",
                     f"{tr['run_finished_after_alert_s']:.0f}", "yes" if tr["webhook_sent"] else "no"))
    out = [D.table("mon", "Latency of the monitoring chain, three trials with an injected forecast (3.2 km urban area, buildings on).",
                   ["Trial", "Forecast peak (mm/h)", "Duration (h)", "Check call (s)", "Alert received after (s)", "Run finished after alert (s)", "Webhook delivered"], rows,
                   numcols=(0, 1, 2, 3, 4, 5), note=f"Real Open-Meteo fetch latency measured separately: {min(lat):.2f}–{max(lat):.2f} s over {len(lat)} calls. The check call includes submitting the run through the platform’s own endpoint. The forecast was injected because every real forecast we could fetch for eight Pakistani sites on 17 November (dry season) contained no rain; the run, the webhook and the timing are real.")]
    a = [tr["alert_received_after_s"] for tr in raw["trials"] if tr["alert_received_after_s"] is not None]
    f = [tr["run_finished_after_alert_s"] for tr in raw["trials"]]
    out.append(f"""<p>From the start of a check to an alert in the user’s hands took {min(a):.1f}–{max(a):.1f} s, dominated by the forecast fetch (about {np.median(lat):.1f} s) and the submission of the run; the full simulation and its results followed {min(f):.0f}–{max(f):.0f} s after the alert on the same machine. The scheduler wakes every 10 minutes, so the delay between a forecast changing and a check can be up to that long plus the site’s own check interval.</p>""")
    return "\n".join(out)


def mon_abstract(raw):
    a = [tr["alert_received_after_s"] for tr in raw["trials"] if tr["alert_received_after_s"] is not None]
    return f"An alert reached its webhook {min(a):.1f}–{max(a):.1f} s after a forecast check." if a else ""


def case_block(D, raw):
    """raw schema: see case_study.py"""
    rps = raw["rps"]; base = raw["baseline"]; opts = raw["options"]
    out = [f"""<p><b>Site and storms.</b> A {raw['site']['side_km']} km box on Nullah Lai, Rawalpindi/Islamabad (centre {raw['site']['lat']:.3f}° N, {raw['site']['lon']:.3f}° E), with {base[str(rps[-1])]['buildings_total']:,} preloaded buildings. Design storms are 24-hour triangles of 24-hour depth {', '.join(f"{raw['storms'][str(r)]['depth24']:.0f} mm ({r}-year)" for r in rps)}, from the Gumbel fit of Section [[s:forcing]].</p>"""]
    rows = []
    rows.append(("Baseline", *[f"{base[str(r)]['flooded_buildings']:,}" for r in rps], *[f"{base[str(r)]['loss_pkr'] / 1e6:,.0f}" for r in rps], f"{raw['aal']['baseline'] / 1e6:,.1f}", "–", "–"))
    for name, o in opts.items():
        rows.append((o["label"], *[f"{o['by_rp'][str(r)]['flooded_buildings']:,}" for r in rps], *[f"{o['by_rp'][str(r)]['loss_pkr'] / 1e6:,.0f}" for r in rps],
                     f"{raw['aal'][name] / 1e6:,.1f}", f"{(raw['aal']['baseline'] - raw['aal'][name]) / 1e6:+,.1f}", f"{o['breakeven_pkr'] / 1e6:,.0f}"))
    head = ["Option"] + [f"Flooded bldgs {r}-yr" for r in rps] + [f"Loss {r}-yr (Rs M)" for r in rps] + ["AAL (Rs M/yr)", "ΔAAL (Rs M/yr)", "Break-even capital (Rs M)"]
    out.append(D.table("mit", "Mitigation options compared: flooded buildings and estimated building loss at each return period, annual average loss (AAL) and break-even capital cost C* (T = 30 years, δ = 10 %).",
                       head, rows, numcols=tuple(range(1, len(head))), hl=(0,),
                       note="Loss: Huizinga et al. Asia residential maximum damage × current PKR rate, no inflation adjustment; for comparing options, not forecasting cost. AAL uses the three return periods by the trapezoid rule with a constant tail beyond the rarest. Break-even capital is the most that could be spent before the benefit–cost ratio falls below 1; T and δ are illustrative."))
    out.append("""<p>[[t:mit]] and [[f:mitfig]] give the results. Optimiser-placed structures were found with a budget of """ + str(raw.get("optimiser_trials", "12")) + """ full-solver trials each, so the placements are good, not optimal.</p>""")
    out.append(D.fig("mitfig", "fig_case.png", raw.get("fig_caption", "Baseline peak depth and the change caused by the best option at the 100-year storm."), "100%"))
    out.append(f"""<p>{raw['discussion']}</p>""")
    return "\n".join(out)


def case_abstract(raw):
    return raw.get("abstract", "")
