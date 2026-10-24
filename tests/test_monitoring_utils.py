"""
Fast, pure-logic tests for monitoring_utils.py's real forecast peak+
duration extraction (real_forecast_peak_and_duration) -- the Python port
of fis_app.py's own client-side "Run on the forecast" JS logic, kept
deliberately identical so a server-triggered auto-run and a user's manual
button always agree. No network/live-server dependency: these feed a
synthetic Open-Meteo-shaped hourly series directly, so they're
deterministic regardless of real weather at test time (unlike the live
monitoring routes themselves, which genuinely depend on today's real
forecast -- covered separately, non-deterministically, in
test_live_routes.py).
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import monitoring_utils  # noqa: E402


def _hourly_series(hours_from_now, values):
    """Builds a real Open-Meteo-shaped {hourly_time, hourly_precipitation_mm}
    dict whose times start `hours_from_now[0]` hours from the real current
    UTC hour (so "past" vs "future" splitting behaves the same regardless
    of when this test actually runs)."""
    now = time.time()
    times = [time.strftime("%Y-%m-%dT%H:%M", time.gmtime(now + h * 3600)) for h in hours_from_now]
    return {"hourly_time": times, "hourly_precipitation_mm": values}


def test_no_rain_anywhere_returns_none():
    series = _hourly_series(range(-3, 6), [0.0] * 9)
    peak, duration, peak_time = monitoring_utils.real_forecast_peak_and_duration(series)
    assert peak is None and duration is None and peak_time is None


def test_past_only_rain_is_ignored():
    """A real rain event entirely in the past (already-observed
    `past_days` data, not forecast) must not count as a future peak --
    matches the JS's own `first_future` behavior."""
    series = _hourly_series(range(-5, 4), [0, 0, 5.0, 8.0, 5.0, 0, 0, 0, 0])
    peak, duration, peak_time = monitoring_utils.real_forecast_peak_and_duration(series)
    assert peak is None and duration is None


def test_finds_real_future_peak_and_contiguous_duration():
    # hours -2..-1 past (ignored), 0..7 future: a rain event 1..4, peak at 2
    values = [0.0, 0.0,   0.5, 2.0, 6.0, 3.0, 0.5, 0.0, 0.0, 0.0]
    series = _hourly_series(range(-2, 8), values)
    peak, duration, peak_time = monitoring_utils.real_forecast_peak_and_duration(series)
    assert peak == 6.0
    # contiguous run strictly above MIN_TRIGGER_MM_HR (0.1) around the peak:
    # 0.5, 2.0, 6.0, 3.0, 0.5 = 5 hours
    assert duration == 5


def test_duration_floor_is_at_least_one_hour():
    """A single isolated future hour of rain (nothing above the trigger
    threshold on either side) must still report a real, non-zero
    duration -- the module's own documented `max(1, ...)` floor."""
    values = [0.0, 0.0, 7.0, 0.0, 0.0]
    series = _hourly_series(range(0, 5), values)
    peak, duration, peak_time = monitoring_utils.real_forecast_peak_and_duration(series)
    assert peak == 7.0
    assert duration == 1


def test_mismatched_or_empty_series_returns_none():
    assert monitoring_utils.real_forecast_peak_and_duration({}) == (None, None, None)
    assert monitoring_utils.real_forecast_peak_and_duration(
        {"hourly_time": ["2026-01-01T00:00"], "hourly_precipitation_mm": [1.0, 2.0]}
    ) == (None, None, None)


def test_watch_persistence_round_trips_through_real_file(tmp_path, monkeypatch):
    """Real save_watches/load_watches round-trip against a real temp file
    (not the app's own shared watch_list.json) -- confirms the on-disk
    persistence format itself, independent of any HTTP route."""
    monkeypatch.setattr(monitoring_utils, "WATCH_FILE", str(tmp_path / "watch_list.json"))
    assert monitoring_utils.load_watches() == {}
    watches = {"abc123": {"id": "abc123", "name": "test", "threshold_mm_hr": 5.0}}
    monitoring_utils.save_watches(watches)
    reloaded = monitoring_utils.load_watches()
    assert reloaded == watches


# --------------------------------------------------------------------------
# send_webhook_alert -- the real threshold-crossing notification. No real
# network call in any of these: requests.post is monkeypatched so the
# tests are deterministic and don't depend on a real external endpoint.
# --------------------------------------------------------------------------
def test_send_webhook_alert_no_url_is_a_noop_and_returns_false():
    assert monitoring_utils.send_webhook_alert(None, "AOI", 12.0, 3, 10.0, "run123") is False
    assert monitoring_utils.send_webhook_alert("", "AOI", 12.0, 3, 10.0, "run123") is False


def test_send_webhook_alert_posts_real_json_with_both_slack_and_discord_fields(monkeypatch):
    captured = {}

    def fake_post(url, json=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        captured["timeout"] = timeout
        class R:
            status_code = 200
        return R()

    monkeypatch.setattr(monitoring_utils.requests, "post", fake_post)
    ok = monitoring_utils.send_webhook_alert("https://example.com/hook", "My AOI", 15.5, 4, 10.0, "run456")
    assert ok is True
    assert captured["url"] == "https://example.com/hook"
    # Slack reads "text", Discord reads "content" -- both must carry the
    # same real message so either receiver displays it without per-
    # service configuration.
    assert captured["json"]["text"] == captured["json"]["content"]
    assert "15.5" in captured["json"]["text"]
    assert captured["json"]["run_id"] == "run456"
    assert captured["timeout"] == 10


def test_send_webhook_alert_delivery_failure_returns_false_not_raises(monkeypatch):
    def fake_post(url, json=None, timeout=None):
        raise ConnectionError("real network failure")

    monkeypatch.setattr(monitoring_utils.requests, "post", fake_post)
    assert monitoring_utils.send_webhook_alert("https://example.com/hook", "AOI", 12.0, 3, 10.0, "run789") is False


def test_send_webhook_alert_includes_report_url_when_given(monkeypatch):
    captured = {}

    def fake_post(url, json=None, timeout=None):
        captured["json"] = json
        class R:
            status_code = 200
        return R()

    monkeypatch.setattr(monitoring_utils.requests, "post", fake_post)
    monitoring_utils.send_webhook_alert("https://example.com/hook", "AOI", 12.0, 3, 10.0, "run1",
                                         report_url="https://example.ngrok.io/runs/run1/report.html")
    assert captured["json"]["report_url"] == "https://example.ngrok.io/runs/run1/report.html"
    assert "https://example.ngrok.io/runs/run1/report.html" in captured["json"]["text"]


def test_send_webhook_alert_omits_report_url_text_when_not_given(monkeypatch):
    captured = {}

    def fake_post(url, json=None, timeout=None):
        captured["json"] = json
        class R:
            status_code = 200
        return R()

    monkeypatch.setattr(monitoring_utils.requests, "post", fake_post)
    monitoring_utils.send_webhook_alert("https://example.com/hook", "AOI", 12.0, 3, 10.0, "run1")
    assert captured["json"]["report_url"] is None
    assert "View report" not in captured["json"]["text"]


def test_local_timezone_offset_used_not_utc():
    """Real, previously-shipped bug: hourly_time's own labels are in
    Open-Meteo's real resolved LOCAL timezone (timezone="auto"), not UTC
    -- comparing them against a UTC "now" silently shifted the real
    future/past boundary by the AOI's own UTC offset. For a Pakistan AOI
    (UTC+5) this read up to 5 already-ELAPSED local hours as "forecast
    ahead" on every check. Builds a real UTC+5-labeled series with a big
    spike in an already-elapsed local hour and a smaller spike truly
    ahead -- only the real future one may be reported."""
    offset_s = 5 * 3600
    now = time.time()
    now_local = now + offset_s
    base = now_local - now_local % 3600 - 10 * 3600  # 10 local hours before "now"
    times = [time.strftime("%Y-%m-%dT%H:%M", time.gmtime(base + h * 3600)) for h in range(24)]
    precip = [0.0] * 24
    precip[0] = 90.0   # 10 local hours ago -- already elapsed, must be ignored
    precip[14] = 12.0  # 4 local hours ahead -- the real future peak
    series = {"hourly_time": times, "hourly_precipitation_mm": precip, "utc_offset_seconds": offset_s}
    peak, duration, peak_time = monitoring_utils.real_forecast_peak_and_duration(series)
    assert peak == 12.0, f"picked {peak} -- an already-elapsed local hour leaked into the future window"
