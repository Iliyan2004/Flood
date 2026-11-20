"""End-to-end latency of the monitoring chain, using the app's real _check_one_watch (real /run route, real webhook POST to a local
receiver). The weather fetch is replaced by a synthetic forecast because the real forecast contained no rain in the dry season
(all eight Pakistani sites tried returned zero); the real Open-Meteo fetch latency is measured separately."""
import json, os, sys, threading, time, http.server, urllib.request, datetime as dt
sys.path.insert(0, os.path.expanduser("~/flood")); sys.path.insert(0, os.path.expanduser("~/flood/paper"))
os.environ["SAILAB_DISABLE_OPS_LOOP"] = "1"
import monitoring_utils, rainfall_rivers_utils
from case_common_api import GEOM, CENTER, get
tmp = "/tmp/watch_latency_test.json"; monitoring_utils.WATCH_FILE = tmp
import fis_app
fis_app.ops_agents.record_forecast = lambda *a, **k: None
# 1. real fetch latency
lat = []
for _ in range(8):
    t = time.time(); rainfall_rivers_utils.fetch_rainfall(CENTER[1], CENTER[0]); lat.append(time.time() - t); time.sleep(0.5)
# 2. webhook receiver
hits = []
class H(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0)); body = self.rfile.read(n); hits.append((time.time(), json.loads(body)))
        self.send_response(200); self.end_headers()
    def log_message(self, *a): pass
srv = http.server.HTTPServer(("127.0.0.1", 0), H); threading.Thread(target=srv.serve_forever, daemon=True).start()
url = f"http://127.0.0.1:{srv.server_port}/alert"
real = rainfall_rivers_utils.fetch_rainfall(CENTER[1], CENTER[0])
def synth(peak):
    r = dict(real); times = list(real["hourly_time"]); off = real.get("utc_offset_seconds", 18000)
    now_local = dt.datetime.utcfromtimestamp(time.time() + off)
    vals = []
    for t in times:
        h = (dt.datetime.fromisoformat(t) - now_local).total_seconds() / 3600
        vals.append(round(peak * max(0.0, 1 - abs(h - 8) / 3.0), 2) if 5 <= h <= 11 else 0.0)   # a 6-hour storm starting in 5 h, peaking in 8 h
    r["hourly_precipitation_mm"] = vals; return r
out = {"fetch_latency_s": [round(x, 2) for x in lat], "trials": []}
for i, peak in enumerate((17.0, 21.0, 25.0)):
    fis_app.fetch_rainfall = lambda la, lo, p=peak: synth(p)
    wid = f"lat{i}"; watch = {"name": f"latency test {i}", "lat": CENTER[1], "lon": CENTER[0], "geometry": GEOM, "threshold_mm_hr": 10.0, "include_buildings": True,
                              "friction": 0.05, "cfl": 0.7, "webhook_url": url, "last_checked": None, "alerts": [], "check_interval_hours": 3.0}
    monitoring_utils.save_watches({wid: watch}); hits.clear()
    t0 = time.time(); fis_app._check_one_watch(wid, watch, "http://127.0.0.1:8000"); t_check = time.time() - t0
    al = monitoring_utils.load_watches()[wid]["alerts"][-1]; run_id = al["run_id"]
    t_alert = hits[0][0] - t0 if hits else None
    t_run0 = time.time(); status = None
    while time.time() - t_run0 < 1500:
        p = get(f"/runs/{run_id}/progress.json")
        if p.get("status") in ("done", "error"): status = p; break
        time.sleep(2)
    out["trials"].append({"peak_mm_hr": peak, "forecast_peak": al["peak_mm_hr"], "forecast_duration_hr": al["duration_hr"], "run_id": run_id,
                          "check_call_s": round(t_check, 2), "alert_received_after_s": round(t_alert, 2) if t_alert else None, "webhook_sent": al["webhook_sent"],
                          "run_finished_after_alert_s": round(time.time() - t_run0, 1), "status": (status or {}).get("status"),
                          "payload_keys": sorted(hits[0][1].keys()) if hits else None})
    print(out["trials"][-1], flush=True)
json.dump(out, open(os.path.expanduser("~/flood/paper/data/monitoring_latency.json"), "w"), indent=1)
print("DONE")
