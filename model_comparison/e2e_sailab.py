import json, time, sys, os, requests
sys.path.insert(0, os.path.expanduser("~/flood/cmp"))
from shapely.geometry import box, mapping
B = "http://127.0.0.1:8000"
AOIS = {"A_manchar_edge": (67.70, 27.40), "B_north_dadu": (68.36, 28.18), "C_shikarpur": (67.64, 27.94)}
out = {}
for n, (lon, lat) in AOIS.items():
    g = mapping(box(lon - 0.06, lat - 0.06, lon + 0.06, lat + 0.06))
    t0 = time.time()
    r = requests.post(f"{B}/run", json={"geometry": g, "rain_mm_hr": 10.5625, "duration_hr": 48, "include_buildings": True,
                                        "friction": 0.05, "cfl": 0.7}, timeout=120).json()
    rid = r.get("run_id"); stages = {}; last = None
    while rid:
        p = requests.get(f"{B}/runs/{rid}/progress.json", timeout=60).json()
        st = p.get("stage"); 
        if st != last: stages[st] = round(time.time() - t0, 1); last = st
        if p.get("status") in ("done", "error", "failed"): break
        time.sleep(3)
    s = p.get("summary") or {}
    out[n] = {"run_id": rid, "status": p.get("status"), "total_s": round(time.time() - t0, 1), "stage_first_seen_s": stages,
              "buildings_used": s.get("buildings_used"), "wall_clock_solver_s": s.get("wall_clock_seconds"), "error": p.get("error"), "initial": {k: r.get(k) for k in ("error", "cached")}}
    print(n, json.dumps(out[n]), flush=True)
json.dump(out, open(os.path.expanduser("~/flood/cmp/results_e2e_sailab.json"), "w"), indent=1)
