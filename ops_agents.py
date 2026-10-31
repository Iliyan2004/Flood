"""Operations agents: three small, deterministic background checks that watch
SAILAB's data feeds, its own forecasts and its finished runs.

1. Data-health watchdog  (`run_health_checks`): probes every feed and local
   resource the app depends on (Earth Engine credential, PostGIS, Open-Meteo,
   GloFAS, Overpass, disk, GPU, required datasets, solver binary), keeps a
   persistent state per check and alerts only on a CHANGE (a check turning bad
   after enough consecutive failures, or recovering), never on every poll.
2. Forecast validation   (`record_forecast`, `score_due_forecasts`,
   `forecast_skill`): every live-monitoring forecast is written to a ledger;
   once the first 48 h of it have passed, it is scored against what the
   Open-Meteo past-day analysis later reports for the same hours (peak error,
   total error, peak-timing error, threshold hit / false alarm / miss). An
   ADVISORY peak scale factor is reported once enough forecasts exist; it is
   never applied automatically.
3. Run QA                (`assess_run`, `scan_recent_runs`): checks a finished
   run's summary and log for impossible or suspicious results (mass balance,
   implausible depth, whole-domain flooding, solver errors) and records which
   automatic fallbacks were in play, with a suggested action per issue.

Everything here is rule-based. Nothing changes a model, a setting or a result
by itself: it detects, records, alerts and suggests.

The "observed" rain used for scoring is Open-Meteo's own past-day analysis
(model-blended with satellite and station data), not a rain-gauge record.
"""

import json
import math
import os
import shutil
import statistics
import subprocess
import time

import requests

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_PATH = os.path.join(BASE_DIR, "ops_state.json")
LEDGER_PATH = os.path.join(BASE_DIR, "forecast_ledger.jsonl")

SCORE_HORIZON_H = 48          # hours of each forecast that get scored
SCORE_AFTER_H = 54            # score once now >= issue + this
SCORE_EXPIRE_H = 24 * 6       # past-day analysis can no longer cover it after this
MIN_MATCHED_HOURS = 36
ADVISORY_MIN_FORECASTS = 10


# ---------------------------------------------------------------- alerts

def notify(text, url=None):
    """Best-effort alert to SAILAB_ALERT_WEBHOOK (Slack/Discord/ntfy/custom,
    same dual text+content payload as monitoring_utils). Never raises."""
    url = url or os.environ.get("SAILAB_ALERT_WEBHOOK", "").strip()
    print(f"[ops] {text}")
    if not url:
        return False
    try:
        r = requests.post(url, json={"text": text, "content": text}, timeout=10)
        return r.status_code < 400
    except Exception as e:
        print(f"[ops] alert delivery failed: {e}")
        return False


def _load_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def _save_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f)
    os.replace(tmp, path)


# ---------------------------------------------------------- health checks
# Every check returns (status, detail): status is "ok", "warn", "fail" or
# "skipped".

def check_earth_engine():
    """Refreshes the stored OAuth token for real. glacier_monitoring_utils
    caches its first init result for the life of the process, so it cannot
    notice a token that is revoked later."""
    path = os.path.expanduser("~/.config/earthengine/credentials")
    if not os.path.exists(path):
        return "skipped", "no Earth Engine credentials file"
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        d = _load_json(path, {})
        creds = Credentials(None, refresh_token=d["refresh_token"], token_uri="https://oauth2.googleapis.com/token",
                            client_id=d["client_id"], client_secret=d["client_secret"])
        creds.refresh(Request())
        return "ok", "token refresh succeeded"
    except Exception as e:
        return "fail", f"{type(e).__name__}: {e} -- run `earthengine authenticate` to renew"


def check_postgis():
    try:
        import pg_vector_utils
        if not pg_vector_utils.enabled():
            return "skipped", "SAILAB_PG_URL not set"
        rows = pg_vector_utils._query("select 1", ())
        return ("ok", "query succeeded") if rows is not None else ("fail", "query returned nothing (fallback to files in use)")
    except Exception as e:
        return "fail", f"{type(e).__name__}: {e}"


def _http_check(url, params=None, ok_codes=(200,), timeout=45):
    t0 = time.time()
    try:
        # Overpass answers 406 to requests without an explicit Accept header.
        r = requests.get(url, params=params, timeout=timeout, headers={"Accept": "*/*"})
        dt = time.time() - t0
        if r.status_code in ok_codes:
            return "ok", f"HTTP {r.status_code} in {dt:.1f}s"
        return "fail", f"HTTP {r.status_code}"
    except Exception as e:
        return "fail", f"{type(e).__name__}: {e}"


def check_open_meteo():
    return _http_check("https://api.open-meteo.com/v1/forecast",
                       {"latitude": 33.7, "longitude": 73.0, "hourly": "precipitation", "forecast_days": 1})


def check_glofas():
    return _http_check("https://flood-api.open-meteo.com/v1/flood",
                       {"latitude": 31.5, "longitude": 74.3, "daily": "river_discharge", "forecast_days": 1})


def check_overpass():
    """A tiny real query against the same endpoint and headers the app uses
    (the /api/status page itself answers 406 to everything)."""
    t0 = time.time()
    try:
        r = requests.post("https://overpass-api.de/api/interpreter", data={"data": "[out:json][timeout:10];node(1);out ids;"},
                          headers={"Accept": "*/*", "User-Agent": "SAILAB/1.0"}, timeout=40)
        if r.status_code == 200:
            return "ok", f"HTTP 200 in {time.time()-t0:.1f}s"
        return "fail", f"HTTP {r.status_code}"
    except Exception as e:
        return "fail", f"{type(e).__name__}: {e}"


def check_disk(paths=None, min_free_gb=20.0):
    paths = paths or [BASE_DIR, os.path.expanduser("~")]
    worst = None
    for p in paths:
        try:
            free = shutil.disk_usage(p).free / 1e9
        except Exception:
            continue
        if worst is None or free < worst[0]:
            worst = (free, p)
    if worst is None:
        return "skipped", "no readable path"
    status = "ok" if worst[0] >= min_free_gb else ("warn" if worst[0] >= min_free_gb / 4 else "fail")
    return status, f"{worst[0]:.0f} GB free on {worst[1]}"


def check_gpu(warn_c=88):
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=temperature.gpu,utilization.gpu", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=15)
        if out.returncode != 0:
            return "fail", (out.stderr or "nvidia-smi failed").strip()[:200]
        t, u = [x.strip() for x in out.stdout.strip().splitlines()[0].split(",")]
        status = "warn" if float(t) >= warn_c else "ok"
        return status, f"{t} C, {u}% utilisation"
    except FileNotFoundError:
        return "skipped", "nvidia-smi not found"
    except Exception as e:
        return "fail", f"{type(e).__name__}: {e}"


def check_datasets():
    try:
        import data_paths
        missing = data_paths.missing_required()
        if missing:
            return "fail", "missing required: " + ", ".join(n for n, _ in missing)
        return "ok", "all required datasets present"
    except Exception as e:
        return "fail", f"{type(e).__name__}: {e}"


def check_solver_binary():
    try:
        import flood_model
        if os.path.exists(flood_model.LISFLOOD_BIN):
            return "ok", flood_model.LISFLOOD_BIN
        return "fail", f"missing {flood_model.LISFLOOD_BIN}"
    except Exception as e:
        return "fail", f"{type(e).__name__}: {e}"


# name -> (function, consecutive failures needed before alerting)
DEFAULT_CHECKS = {
    "earth_engine_credential": (check_earth_engine, 1),
    "postgis": (check_postgis, 2),
    "open_meteo_forecast": (check_open_meteo, 2),
    "glofas_flood_api": (check_glofas, 2),
    "overpass": (check_overpass, 3),        # flaky public service: tolerate more
    "disk_space": (check_disk, 1),
    "gpu": (check_gpu, 2),
    "required_datasets": (check_datasets, 1),
    "solver_binary": (check_solver_binary, 1),
}


def run_health_checks(checks=None, state_path=None, notifier=None, now=None):
    """Runs every check, updates the persistent state, alerts on changes.
    Returns the new state dict {name: {...}}."""
    checks = checks or DEFAULT_CHECKS
    state_path = state_path or STATE_PATH
    notifier = notifier or notify
    now = now if now is not None else time.time()
    state = _load_json(state_path, {})
    checks_state = state.setdefault("checks", {})
    for name, (fn, need) in checks.items():
        try:
            status, detail = fn()
        except Exception as e:
            status, detail = "fail", f"check crashed: {type(e).__name__}: {e}"
        prev = checks_state.get(name, {})
        fails = (prev.get("consecutive_failures", 0) + 1) if status == "fail" else 0
        was_alerting = prev.get("alerting", False)
        alerting = was_alerting
        if status == "fail" and fails >= need and not was_alerting:
            alerting = True
            notifier(f"SAILAB health: {name} is FAILING -- {detail}")
        elif status in ("ok", "skipped") and was_alerting:
            alerting = False
            notifier(f"SAILAB health: {name} recovered ({detail})")
        since = prev.get("since", now) if prev.get("status") == status else now
        checks_state[name] = {"status": status, "detail": detail, "since": since, "last_checked": now,
                              "consecutive_failures": fails, "alerting": alerting}
    state["last_run"] = now
    _save_json(state_path, state)
    return state


# ------------------------------------------------------ forecast ledger

def _local_label_to_epoch(label, utc_offset_s):
    """Open-Meteo labels (YYYY-MM-DDTHH:MM) are local time for the point."""
    import calendar
    t = time.strptime(label, "%Y-%m-%dT%H:%M")
    return calendar.timegm(t) - utc_offset_s


def record_forecast(watch_id, name, lat, lon, rainfall_result, threshold_mm_hr, path=None, now=None):
    """Appends the FUTURE part of a live forecast to the ledger."""
    path = path or LEDGER_PATH
    now = now if now is not None else time.time()
    times = rainfall_result.get("hourly_time") or []
    precip = rainfall_result.get("hourly_precipitation_mm") or []
    if not times or len(times) != len(precip):
        return False
    off = rainfall_result.get("utc_offset_seconds") or 0
    now_label = time.strftime("%Y-%m-%dT%H:%M", time.gmtime(now + off))
    first = next((i for i, t in enumerate(times) if t >= now_label), None)
    if first is None:
        return False
    fut_t = times[first:first + SCORE_HORIZON_H]
    fut_p = [float(p or 0.0) for p in precip[first:first + SCORE_HORIZON_H]]
    if len(fut_t) < 12:
        return False
    entry = {"watch_id": watch_id, "name": name, "lat": lat, "lon": lon, "issued": now,
             "utc_offset_seconds": off, "threshold_mm_hr": threshold_mm_hr,
             "times": fut_t, "precip_mm": fut_p, "scored": False}
    with open(path, "a") as f:
        f.write(json.dumps(entry) + "\n")
    return True


def _default_fetch_analysis(lat, lon):
    r = requests.get("https://api.open-meteo.com/v1/forecast", params={
        "latitude": lat, "longitude": lon, "hourly": "precipitation", "past_days": 7,
        "forecast_days": 1, "timezone": "auto"}, timeout=25)
    r.raise_for_status()
    d = r.json()
    return d["hourly"]["time"], d["hourly"]["precipitation"]


def score_forecast(entry, obs_times, obs_precip):
    """Pure scoring of one ledger entry against later analysis values.
    Returns a metrics dict, or None if too few hours can be matched."""
    obs = {t: float(p or 0.0) for t, p in zip(obs_times, obs_precip)}
    pairs = [(t, f, obs[t]) for t, f in zip(entry["times"], entry["precip_mm"]) if t in obs]
    if len(pairs) < MIN_MATCHED_HOURS:
        return None
    fc = [f for _, f, _ in pairs]
    ob = [o for _, _, o in pairs]
    ipf = max(range(len(fc)), key=lambda i: fc[i])
    ipo = max(range(len(ob)), key=lambda i: ob[i])
    off = entry.get("utc_offset_seconds") or 0
    timing_h = (_local_label_to_epoch(pairs[ipf][0], off) - _local_label_to_epoch(pairs[ipo][0], off)) / 3600.0
    thr = entry.get("threshold_mm_hr")
    fc_cross, ob_cross = (max(fc) >= thr, max(ob) >= thr) if thr else (None, None)
    return {"hours": len(pairs), "peak_fc": max(fc), "peak_obs": max(ob), "peak_err": max(fc) - max(ob),
            "total_fc": sum(fc), "total_obs": sum(ob), "total_err": sum(fc) - sum(ob),
            "peak_timing_err_h": timing_h, "fc_crossed": fc_cross, "obs_crossed": ob_cross}


def score_due_forecasts(path=None, fetch=None, now=None):
    """Scores every unscored ledger entry that is old enough. Returns the
    number scored. Entries that can no longer be verified are marked expired."""
    path = path or LEDGER_PATH
    fetch = fetch or _default_fetch_analysis
    now = now if now is not None else time.time()
    if not os.path.exists(path):
        return 0
    with open(path) as f:
        entries = [json.loads(l) for l in f if l.strip()]
    scored, cache = 0, {}
    for e in entries:
        if e.get("scored"):
            continue
        age_h = (now - e["issued"]) / 3600.0
        if age_h < SCORE_AFTER_H:
            continue
        if age_h > SCORE_EXPIRE_H:
            e["scored"], e["metrics"] = True, None
            continue
        key = (round(e["lat"], 2), round(e["lon"], 2))
        try:
            if key not in cache:
                cache[key] = fetch(e["lat"], e["lon"])
            m = score_forecast(e, *cache[key])
        except Exception as ex:
            print(f"[ops] forecast scoring fetch failed for {key}: {ex}")
            continue
        e["scored"], e["metrics"] = True, m
        if m:
            scored += 1
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")
    os.replace(tmp, path)
    return scored


def forecast_skill(path=None, watch_id=None):
    """Aggregate skill over all scored forecasts (optionally one watch)."""
    path = path or LEDGER_PATH
    ms = []
    if os.path.exists(path):
        with open(path) as f:
            for l in f:
                if not l.strip():
                    continue
                e = json.loads(l)
                if e.get("scored") and e.get("metrics") and (watch_id is None or e["watch_id"] == watch_id):
                    ms.append(e["metrics"])
    out = {"n_scored": len(ms),
           "observed_basis": "Open-Meteo past-day analysis (model-blended; not a rain-gauge record)"}
    if not ms:
        return out
    out["peak_bias_mm_hr"] = statistics.fmean(m["peak_err"] for m in ms)
    out["peak_mae_mm_hr"] = statistics.fmean(abs(m["peak_err"]) for m in ms)
    out["total_bias_mm"] = statistics.fmean(m["total_err"] for m in ms)
    out["total_mae_mm"] = statistics.fmean(abs(m["total_err"]) for m in ms)
    out["peak_timing_mae_h"] = statistics.fmean(abs(m["peak_timing_err_h"]) for m in ms)
    c = {"hits": 0, "false_alarms": 0, "misses": 0, "correct_negatives": 0}
    for m in ms:
        if m["fc_crossed"] is None:
            continue
        if m["fc_crossed"] and m["obs_crossed"]:
            c["hits"] += 1
        elif m["fc_crossed"]:
            c["false_alarms"] += 1
        elif m["obs_crossed"]:
            c["misses"] += 1
        else:
            c["correct_negatives"] += 1
    out["threshold_contingency"] = c
    if c["hits"] + c["misses"]:
        out["pod"] = c["hits"] / (c["hits"] + c["misses"])
    if c["hits"] + c["false_alarms"]:
        out["far"] = c["false_alarms"] / (c["hits"] + c["false_alarms"])
    ratios = [m["peak_obs"] / m["peak_fc"] for m in ms if m["peak_fc"] >= 1.0]
    if len(ratios) >= ADVISORY_MIN_FORECASTS:
        out["advisory_peak_scale"] = statistics.median(ratios)
        out["advisory_note"] = ("Median observed/forecast peak ratio. Advisory only: it is not applied to any "
                                "forecast or run automatically.")
    else:
        out["advisory_peak_scale"] = None
        out["advisory_note"] = f"Needs at least {ADVISORY_MIN_FORECASTS} forecasts with a peak >= 1 mm/hr."
    return out


# ----------------------------------------------------------------- run QA

def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def assess_run(summary, log_text=""):
    """Rule-based QA of a finished run. Returns
    {"verdict": "ok"|"warn"|"fail", "issues": [...], "fallbacks": [...]}."""
    issues, fallbacks = [], []
    log_text = log_text or ""

    def add(sev, code, msg, action):
        issues.append({"severity": sev, "code": code, "message": msg, "suggested_action": action})

    for k in ("flooded_area_km2", "max_depth_m", "domain_area_km2"):
        if not _num(summary.get(k)):
            add("fail", "missing_or_nonfinite", f"{k} is missing or not a finite number",
                "Check the run log; re-run. If it repeats, inspect the DEM and rain inputs.")
    max_d = summary.get("max_depth_m")
    inflow = summary.get("cumulative_river_inflow_m3") or 0
    n_in = len(summary.get("inflows") or [])
    if _num(max_d):
        if max_d > 100:
            add("fail", "implausible_depth", f"maximum depth {max_d:.0f} m is physically implausible",
                "Likely a numerical blow-up (steep terrain on ACC, or inflow over bad cells). Re-run with DG2 or a smaller timestep and check the DEM.")
        elif max_d > 40 and not inflow and not n_in:
            add("warn", "very_deep_pluvial", f"maximum depth {max_d:.0f} m from rain alone is unusually deep",
                "Check for sinks in the DEM or a numerical blow-up; compare against a DG2 run.")
    inp = (summary.get("cumulative_rainfall_input_m3") or 0) + (inflow or 0)
    err = summary.get("final_mass_error_m3")
    if _num(err) and inp > 0:
        rel = abs(err) / inp
        if rel > 0.2:
            add("fail", "mass_balance", f"mass-balance error is {rel*100:.0f}% of the water input", "Treat the result as unreliable; re-run with a smaller timestep.")
        elif rel > 0.05:
            add("warn", "mass_balance", f"mass-balance error is {rel*100:.1f}% of the water input", "Check for steep terrain or boundary issues.")
    pct = summary.get("flooded_pct_of_domain")
    if _num(pct) and pct > 95:
        add("warn", "whole_domain_flooded", f"{pct:.0f}% of the domain is flooded",
            "Check the boundary conditions, inflow size and whether the area is a basin sink.")
    if summary.get("buildings_used") and summary.get("building_submersion") is None:
        add("warn", "buildings_not_assessed", "buildings were used in the run but no submersion assessment was recorded",
            "Check the run log for a post-processing error.")
    for pat, sev, code, msg, act in (
        ("cudaError", "warn", "cuda_error_in_log", "a CUDA error appears in the log", "If the run completed it was retried; if results look wrong, re-run."),
        ("FAILED", "fail", "solver_failed_in_log", "the log contains a FAILED line", "Open the run log; re-run after fixing the cause."),
        ("nan", "warn", "nan_in_log", "'nan' appears in the log", "Inspect the DEM and parameters for invalid values."),
    ):
        if pat in log_text:
            add(sev, code, msg, act)
    reason = str(summary.get("solver_selection_reason") or "")
    if "above the" in reason and "cell limit" in reason:
        fallbacks.append("solver forced to ACC because the domain exceeds the DG2 cell limit")
    if "boundary inflow" in reason.lower() and "ACC" in reason:
        fallbacks.append("ACC used because of boundary inflow")
    if "running on CPU:" in log_text:
        line = next((l for l in log_text.splitlines() if "running on CPU:" in l), "")
        fallbacks.append(line.strip()[:160])
    if "cudaErrorMemoryAllocation" in log_text and "retry" in log_text.lower():
        fallbacks.append("solver restarted after a transient CUDA allocation error")
    order = {"fail": 2, "warn": 1}
    verdict = "ok"
    for i in issues:
        if order.get(i["severity"], 0) > order.get(verdict, 0):
            verdict = i["severity"]
    return {"verdict": verdict, "issues": issues, "fallbacks": fallbacks, "checked_at": time.time()}


def qa_run_dir(run_dir, notifier=None, run_id=None, write=True):
    """Assesses one run directory (summary.json + run.log if present)."""
    summary = _load_json(os.path.join(run_dir, "summary.json"), None)
    if summary is None:
        return None
    log_text = ""
    for name in ("run.log", "log.txt", "flood_model.log"):
        p = os.path.join(run_dir, name)
        if os.path.exists(p):
            try:
                with open(p, errors="replace") as f:
                    log_text = f.read()[-200000:]
            except Exception:
                pass
            break
    qa = assess_run(summary, log_text)
    if write:
        try:
            _save_json(os.path.join(run_dir, "run_qa.json"), qa)
        except Exception:
            pass
    if qa["verdict"] == "fail" and notifier:
        notifier(f"SAILAB run QA: run {run_id or os.path.basename(run_dir)} FAILED QA -- "
                 + "; ".join(i["message"] for i in qa["issues"] if i["severity"] == "fail"))
    return qa


def scan_recent_runs(cache_dir, since_days=2, limit=200, notifier=None):
    """QA for recent runs that have no run_qa.json yet. Returns counts."""
    cutoff = time.time() - since_days * 86400
    res = {"checked": 0, "ok": 0, "warn": 0, "fail": 0, "failed_runs": []}
    try:
        names = sorted(os.listdir(cache_dir), key=lambda n: os.path.getmtime(os.path.join(cache_dir, n)), reverse=True)
    except Exception:
        return res
    for n in names:
        d = os.path.join(cache_dir, n)
        if res["checked"] >= limit:
            break
        if not os.path.isfile(os.path.join(d, "summary.json")) or os.path.getmtime(d) < cutoff:
            continue
        if os.path.exists(os.path.join(d, "run_qa.json")):
            continue
        qa = qa_run_dir(d, notifier=None)
        if qa is None:
            continue
        res["checked"] += 1
        res[qa["verdict"]] += 1
        if qa["verdict"] == "fail":
            res["failed_runs"].append(n)
    if res["fail"] and notifier:
        notifier(f"SAILAB run QA scan: {res['fail']} of {res['checked']} recent runs failed QA: {', '.join(res['failed_runs'][:8])}")
    return res
