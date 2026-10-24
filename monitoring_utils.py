"""
Real live/continuous monitoring: periodically checks a saved AOI's own
real Open-Meteo rainfall FORECAST (rainfall_rivers_utils.fetch_rainfall,
the same real, free, no-key data source already used everywhere else in
this app) against a real user-set threshold, and auto-triggers a real
Tier 3 LISFLOOD-FP physics run when the threshold is crossed -- chaining
the exact same real "forecast -> peak+duration -> physics run" logic
`fis_app.py`'s own `runForecastMode` JS button already does manually,
just run automatically on a schedule instead of on a click.

Honest, stated limitation: this only runs for as long as the Flask
process itself is alive -- there is no persistent system cron/systemd
timer available on this shared machine (no root access to install one),
so a server restart PAUSES monitoring until the process comes back up.
It resumes automatically from its own real persisted state
(`watch_list.json`, not in-memory only) once the process restarts --
not from scratch, and not silently losing watched areas -- but a user
should understand real-time coverage has a real gap during any restart,
same honesty already applied elsewhere in this app for other real
limitations.
"""
import json
import os
import threading
import time

import requests

WATCH_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "watch_list.json")
_file_lock = threading.Lock()

MIN_TRIGGER_MM_HR = 0.1  # a "peak" below this is real noise, not a real storm signal


def send_webhook_alert(webhook_url, watch_name, peak_mm_hr, duration_hr, threshold_mm_hr, run_id, report_url=None):
    """Real, best-effort notification for a threshold-crossing alert --
    without this, a triggered run was previously only ever visible if a
    user happened to have the app's own page open and manually refreshed
    the watch list, defeating the point of "monitoring" something you
    aren't actively watching. Deliberately vendor-agnostic (a plain JSON
    POST) rather than tied to one specific service's own API/SDK: this
    works unmodified with a Slack incoming webhook, a Discord webhook,
    ntfy.sh (free, no signup, just a topic URL), or any custom receiver
    a user points it at -- no new credentials/registration required from
    this app itself, unlike a real SMTP/SMS integration would need.
    Returns True/False (logged either way, never raises) so a delivery
    failure can never break the real threshold-crossing/run-triggering
    logic it's attached to.

    `report_url`: a real, clickable link to the triggered run's own
    report.html, when this server has a real externally-reachable base
    URL configured (see fis_app.py's PUBLIC_BASE_URL) -- omitted (not a
    broken/misleading localhost link) when it isn't, since a recipient
    reading this alert on their phone has no other way to know it's
    unusable."""
    if not webhook_url:
        return False
    text = (f"SAILAB flood watch: \"{watch_name}\" -- real forecast peak "
            f"{peak_mm_hr:.1f} mm/hr (threshold {threshold_mm_hr} mm/hr) over "
            f"{duration_hr}h crossed the threshold. Triggered run: {run_id or 'failed to start'}.")
    if report_url:
        text += f" View report: {report_url}"
    try:
        # "text" is the field a real Slack incoming webhook reads for the
        # displayed message; Discord's own webhook API instead reads
        # "content" for the identical purpose -- both are sent together
        # (each service ignores fields it doesn't recognize) so this one
        # payload displays correctly on either without per-service
        # configuration. The real structured fields are included too for
        # any custom receiver that wants to parse them instead of just
        # displaying the text.
        requests.post(webhook_url, json={
            "text": text, "content": text, "watch_name": watch_name, "peak_mm_hr": peak_mm_hr,
            "duration_hr": duration_hr, "threshold_mm_hr": threshold_mm_hr, "run_id": run_id,
            "report_url": report_url,
        }, timeout=10)
        return True
    except Exception as e:
        print(f"[monitor] webhook delivery failed: {e}")
        return False


def load_watches():
    with _file_lock:
        if not os.path.exists(WATCH_FILE):
            return {}
        with open(WATCH_FILE) as f:
            return json.load(f)


def save_watches(watches):
    with _file_lock:
        tmp = WATCH_FILE + f".tmp{os.getpid()}_{threading.get_ident()}"
        with open(tmp, "w") as f:
            json.dump(watches, f, indent=2)
        os.replace(tmp, WATCH_FILE)


def real_forecast_peak_and_duration(rainfall_result):
    """Python port of `fis_app.py`'s own `runForecastMode` JS logic (kept
    deliberately identical so a server-triggered auto-run and the user's
    own manual "Run on the forecast" button always agree on the same
    input, never computed two different ways): finds the real peak
    forecasted hourly rate within the FUTURE portion of Open-Meteo's real
    hourly series (past_days are recent-observed, not what "monitor the
    forecast" means), and the real contiguous rain-event duration around
    it (>0.1mm/hr on either side, matching the JS's own threshold).
    Returns (peak_mm_hr, duration_hr, peak_time_str) or (None, None, None)
    if there's no real, non-trivial rain ahead.

    Real, previously-shipped bug fixed here: `hourly_time`'s own labels
    are in Open-Meteo's real resolved LOCAL timezone for this AOI
    (fetch_rainfall requests timezone="auto"), not UTC -- comparing them
    against a UTC "now" (the original `time.gmtime()` here) silently
    shifted the real future/past boundary by the AOI's own UTC offset.
    For every Pakistan AOI (UTC+5) this meant up to 5 already-ELAPSED
    local hours of real recorded rain were treated as "forecast ahead"
    on every single check -- capable of both firing a false threshold-
    crossing alert on rain that had already happened, and (less often)
    missing a real just-started event by attributing its first hours to
    the "past" window instead. Fixed using the real `utc_offset_seconds`
    Open-Meteo also returns, computing "now" in the SAME local frame the
    labels use rather than converting the labels to UTC."""
    times = rainfall_result.get("hourly_time") or []
    precip = rainfall_result.get("hourly_precipitation_mm") or []
    if not times or not precip or len(times) != len(precip):
        return None, None, None

    utc_offset_s = rainfall_result.get("utc_offset_seconds") or 0
    now_str = time.strftime("%Y-%m-%dT%H:%M", time.gmtime(time.time() + utc_offset_s))
    first_future = next((i for i, t in enumerate(times) if t >= now_str), 0)

    peak_idx = first_future
    for i in range(first_future, len(precip)):
        if (precip[i] or 0) > (precip[peak_idx] or 0):
            peak_idx = i
    peak_mm_hr = precip[peak_idx] or 0.0
    if peak_mm_hr <= 0:
        return None, None, None

    lo, hi = peak_idx, peak_idx
    while lo > 0 and (precip[lo - 1] or 0) > MIN_TRIGGER_MM_HR:
        lo -= 1
    while hi < len(precip) - 1 and (precip[hi + 1] or 0) > MIN_TRIGGER_MM_HR:
        hi += 1
    duration_hr = max(1, hi - lo + 1)
    return float(peak_mm_hr), int(duration_hr), times[peak_idx]
