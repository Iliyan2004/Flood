"""Mitigation case study through the live app's own routes (baseline runs, optimiser-placed structures, nature-based options, AAL).
Resumable: state is written to paper/data/case_state.json after every step; run again to continue. Output: paper/data/case_study.json
and paper/data/case_maps/*.npy (peak depth grids) for the figure.

Site: 3.2 km box on Nullah Lai, Rawalpindi/Islamabad. Return periods 10/50/100 years from the platform's own design-storm route (24 h triangle).
"""
import json, os, sys, time, math
sys.path.insert(0, os.path.expanduser("~/flood")); sys.path.insert(0, os.path.expanduser("~/flood/paper"))
import numpy as np
from case_common_api import GEOM, CENTER, SIDE_KM, post, get, poll_job
import aal_utils, infra_utils

HERE = os.path.expanduser("~/flood/paper"); DATA = f"{HERE}/data"; MAPS = f"{DATA}/case_maps"; os.makedirs(MAPS, exist_ok=True)
FIS = os.path.expanduser("~/flood/fis_cache"); PROT = os.path.expanduser("~/flood/fis_protect_cache"); NBS = os.path.expanduser("~/flood/fis_nbs_cache")
STATE = f"{DATA}/case_state.json"
RPS = [10, 50, 100]; T_YEARS = 30; DISC = 0.10; OPT_TRIALS = 12
OPTIONS = {"embankment": {"label": "Embankment (optimiser-placed)", "kind": "protect", "types": ["dam"]},
           "drain": {"label": "Drainage line (optimiser-placed)", "kind": "protect", "types": ["drainage"]},
           "basin": {"label": "Retention basin (optimiser-placed)", "kind": "protect", "types": ["catchment"]},
           "grass": {"label": "Grassland restoration (active)", "kind": "nbs", "intervention": "grassland_restoration"},
           "forest": {"label": "Reforestation (active)", "kind": "nbs", "intervention": "reforestation"}}
st = json.load(open(STATE)) if os.path.exists(STATE) else {"storms": {}, "baseline": {}, "structures": {}, "scen": {}}
def save(): json.dump(st, open(STATE, "w"), indent=1)

def wait_run(run_id):
    t0 = time.time()
    while time.time() - t0 < 3000:
        p = get(f"/runs/{run_id}/progress.json")
        if p.get("status") in ("done", "error", "failed", "cancelled"):
            return p
        time.sleep(4)
    raise TimeoutError(run_id)

def flooded_buildings(sub):
    bb = sub["band_breakdown"]; return int(sum(v for k, v in bb.items() if k != "dry")), int(sum(bb.values()))

def read_res_max(path):
    return np.loadtxt(path, skiprows=6)

# 1. design storms --------------------------------------------------------------------------------------------------------------
for rp in RPS:
    if str(rp) in st["storms"]: continue
    j = post("/area/design-storm", {"geometry": GEOM, "return_period_years": rp}); p = poll_job(j["job_id"])
    d = p["result"]["design_depth_mm_24hr"]
    st["storms"][str(rp)] = {"depth24": d, "peak_mm_hr": 2 * d / 24, "duration_hr": 24, "ensemble_min": p["result"]["ensemble_min_mm"], "ensemble_max": p["result"]["ensemble_max_mm"]}
    save(); print("storm", rp, st["storms"][str(rp)], flush=True)

# 2. baselines ------------------------------------------------------------------------------------------------------------------
for rp in RPS:
    if str(rp) in st["baseline"]: continue
    s = st["storms"][str(rp)]
    r = post("/run", {"geometry": GEOM, "rain_mm_hr": s["peak_mm_hr"], "duration_hr": 24, "include_buildings": True, "friction": 0.05, "cfl": 0.7})
    rid = r["run_id"]; p = wait_run(rid)
    if p.get("status") != "done": raise RuntimeError(f"baseline {rp} failed: {p}")
    summ = json.load(open(f"{FIS}/{rid}/summary.json")); sub = json.load(open(f"{FIS}/{rid}/building_submersion.json"))
    fb, tot = flooded_buildings(sub)
    infra = summ.get("infra_risk", {})
    st["baseline"][str(rp)] = {"run_id": rid, "loss_pkr": summ["estimated_building_loss"]["total_loss_pkr"], "flooded_buildings": fb, "buildings_total": tot,
                               "flooded_area_km2": summ.get("flooded_area_km2"), "max_depth_m": summ.get("max_depth_m"), "solver": summ.get("solver_used"),
                               "infra": {k: {"in_domain": v.get("in_domain"), "flooded": v.get("flooded_count")} for k, v in infra.items() if isinstance(v, dict)},
                               "road_detail": (infra.get("roads") or {}).get("road_detail")}
    np.save(f"{MAPS}/baseline_{rp}.npy", read_res_max(f"{FIS}/{rid}/results/res.max").astype("float32"))
    save(); print("baseline", rp, st["baseline"][str(rp)]["loss_pkr"], flush=True)

b100 = st["baseline"]["100"]["run_id"]
meta = json.load(open(f"{FIS}/{b100}/run_meta.json")); header = meta["header"]
base_feats = json.load(open(f"{FIS}/{b100}/building_submersion.json"))["features"]

# 3. optimiser placement at the 100-year baseline, one structure type at a time --------------------------------------------------
for key, o in OPTIONS.items():
    if o["kind"] != "protect" or key in st["structures"]: continue
    j = post("/protect/optimize", {"run_id": b100, "types": o["types"], "n_structures": 1, "max_evals": OPT_TRIALS})
    p = poll_job(j["job_id"], every=10, maxwait=7200)
    if p.get("status") != "done": print("optimiser failed", key, p); continue
    res = p["result"]
    st["structures"][key] = {"structures": res["structures"], "trials": res.get("trials_evaluated"), "search_score": res.get("search_score"),
                             "storage_volume_m3": res.get("storage_volume_m3")}
    save(); print("optimised", key, res["structures"], flush=True)

# 4. scenarios at every return period ---------------------------------------------------------------------------------------------
for key, o in OPTIONS.items():
    for rp in RPS:
        k = f"{key}|{rp}"
        if k in st["scen"]: continue
        rid = st["baseline"][str(rp)]["run_id"]
        if o["kind"] == "protect":
            if key not in st["structures"]: continue
            j = post("/protect/run", {"run_id": rid, "structures": st["structures"][key]["structures"]}); root = PROT
        else:
            j = post("/area/nbs-scenario", {"run_id": rid, "intervention": o["intervention"], "pathway": "active"}); root = NBS
        p = poll_job(j["job_id"], every=6, maxwait=3600)
        if p.get("status") != "done": print("scenario failed", k, p); continue
        res = p["result"]; jid = j["job_id"]
        depth = read_res_max(f"{root}/{jid}/results/res.max")
        sub = infra_utils.assess_building_submersion_risk(base_feats, depth, header)
        fb, tot = flooded_buildings(sub)
        st["scen"][k] = {"loss_pkr": res["loss_after"]["total_loss_pkr"] if res.get("loss_after") else None, "flooded_buildings": fb,
                         "reduction_pct": (res.get("comparison") or {}).get("reduction_pct"), "displacement_pct": (res.get("comparison") or {}).get("displacement_pct"), "job_id": jid}
        if rp == 100: np.save(f"{MAPS}/{key}_100.npy", depth.astype("float32"))
        save(); print("scenario", k, st["scen"][k], flush=True)

# 5. AAL, break-even, output --------------------------------------------------------------------------------------------------------
ann = (1 - (1 + DISC) ** -T_YEARS) / DISC
def aal(points): return aal_utils.compute_aal([{"return_period_years": r, "loss_pkr": l} for r, l in points])["aal_pkr"]
out = {"site": {"lat": CENTER[1], "lon": CENTER[0], "side_km": SIDE_KM}, "rps": RPS, "storms": st["storms"], "optimiser_trials": OPT_TRIALS,
       "baseline": st["baseline"], "options": {}, "aal": {}, "T": T_YEARS, "delta": DISC, "structures": st["structures"]}
out["aal"]["baseline"] = aal([(r, st["baseline"][str(r)]["loss_pkr"]) for r in RPS])
for key, o in OPTIONS.items():
    if not all(f"{key}|{r}" in st["scen"] and st["scen"][f"{key}|{r}"]["loss_pkr"] is not None for r in RPS): continue
    out["options"][key] = {"label": o["label"], "by_rp": {str(r): st["scen"][f"{key}|{r}"] for r in RPS}}
    out["aal"][key] = aal([(r, st["scen"][f"{key}|{r}"]["loss_pkr"]) for r in RPS])
    out["options"][key]["breakeven_pkr"] = max(0.0, out["aal"]["baseline"] - out["aal"][key]) * ann
json.dump(out, open(f"{DATA}/case_study.json", "w"), indent=1)
print("DONE", json.dumps(out["aal"]))
