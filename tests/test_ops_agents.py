import json
import time

import ops_agents as ops


def test_health_alerts_only_on_change(tmp_path):
    sent = []
    st = str(tmp_path / "s.json")
    flip = {"v": "fail"}
    checks = {"x": (lambda: (flip["v"], "d"), 2)}
    ops.run_health_checks(checks, st, sent.append, now=1)
    assert sent == []                      # 1st failure: below threshold
    ops.run_health_checks(checks, st, sent.append, now=2)
    assert len(sent) == 1 and "FAILING" in sent[0]
    ops.run_health_checks(checks, st, sent.append, now=3)
    assert len(sent) == 1                  # still failing: no repeat alert
    flip["v"] = "ok"
    ops.run_health_checks(checks, st, sent.append, now=4)
    assert len(sent) == 2 and "recovered" in sent[1]


def test_health_crashing_check_is_a_failure(tmp_path):
    def boom():
        raise RuntimeError("x")
    s = ops.run_health_checks({"b": (boom, 1)}, str(tmp_path / "s.json"), lambda t: None)
    assert s["checks"]["b"]["status"] == "fail"


def _entry(peak=10.0, thr=5.0):
    times = [f"2026-10-01T{h:02d}:00" for h in range(24)] + [f"2026-10-02T{h:02d}:00" for h in range(24)]
    p = [0.0] * 48
    p[6] = peak
    return {"times": times, "precip_mm": p, "utc_offset_seconds": 18000, "threshold_mm_hr": thr}


def test_score_forecast_metrics():
    e = _entry(10.0)
    obs = list(e["precip_mm"]); obs[6] = 0.0; obs[8] = 4.0
    m = ops.score_forecast(e, e["times"], obs)
    assert m["peak_err"] == 6.0 and m["peak_timing_err_h"] == -2.0
    assert m["fc_crossed"] is True and m["obs_crossed"] is False


def test_score_needs_enough_matched_hours():
    e = _entry()
    assert ops.score_forecast(e, e["times"][:10], [0] * 10) is None


def test_ledger_roundtrip_and_skill(tmp_path):
    led = str(tmp_path / "l.jsonl")
    now = time.time()
    off = 18000
    labels = [time.strftime("%Y-%m-%dT%H:00", time.gmtime(now + off + h * 3600)) for h in range(-2, 60)]
    prec = [0.0] * len(labels); prec[10] = 12.0
    rain = {"hourly_time": labels, "hourly_precipitation_mm": prec, "utc_offset_seconds": off}
    assert ops.record_forecast("w", "n", 33.0, 73.0, rain, 5.0, path=led, now=now)
    assert ops.score_due_forecasts(led, fetch=lambda a, b: (labels, prec), now=now + 10 * 3600) == 0  # too early
    assert ops.score_due_forecasts(led, fetch=lambda a, b: (labels, prec), now=now + 55 * 3600) == 1
    sk = ops.forecast_skill(led)
    assert sk["n_scored"] == 1 and sk["peak_mae_mm_hr"] == 0 and sk["threshold_contingency"]["hits"] == 1
    assert sk["advisory_peak_scale"] is None


def test_unverifiable_old_forecast_is_expired_not_scored(tmp_path):
    led = str(tmp_path / "l.jsonl")
    e = dict(_entry(), watch_id="w", name="n", lat=1, lon=1, issued=0, scored=False)
    open(led, "w").write(json.dumps(e) + "\n")
    assert ops.score_due_forecasts(led, fetch=lambda a, b: ([], []), now=10 * 86400) == 0
    assert json.loads(open(led).read())["scored"] is True


GOOD = {"flooded_area_km2": 1.0, "max_depth_m": 2.0, "domain_area_km2": 5.0, "final_mass_error_m3": 1.0,
        "cumulative_rainfall_input_m3": 1e6, "flooded_pct_of_domain": 20}


def test_qa_clean_run_is_ok():
    assert ops.assess_run(GOOD)["verdict"] == "ok"


def test_qa_flags_blowup_and_mass_error():
    q = ops.assess_run(dict(GOOD, max_depth_m=255.0, final_mass_error_m3=4.8e6, cumulative_rainfall_input_m3=1e7))
    codes = {i["code"] for i in q["issues"]}
    assert q["verdict"] == "fail" and {"implausible_depth", "mass_balance"} <= codes
    assert all(i["suggested_action"] for i in q["issues"])


def test_qa_missing_numbers_and_fallbacks():
    q = ops.assess_run({"solver_selection_reason": "domain above the 4,000,000 cell limit"}, "running on CPU: NODATA under inlet")
    assert q["verdict"] == "fail" and len(q["fallbacks"]) == 2


def test_qa_scan_skips_already_checked(tmp_path):
    d = tmp_path / "abc"; d.mkdir()
    (d / "summary.json").write_text(json.dumps(GOOD))
    r = ops.scan_recent_runs(str(tmp_path))
    assert r["checked"] == 1 and (d / "run_qa.json").exists()
    assert ops.scan_recent_runs(str(tmp_path))["checked"] == 0
