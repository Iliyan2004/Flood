"""
Astronomical (Moon and Sun) tide for the Pakistan coast, fitted to a REAL tide-gauge record.

Data: hourly sea level at Karachi, 2007-01-31 to 2016-12-15 (file `global_data/tide/karachi_hourly_h147b.csv`,
columns year, month, day, hour, level in millimetres above the gauge zero; -32767 marks a missing hour). The
same ten-year record underlies Khan et al. 2020's Karachi extreme-sea-level levels, so the two agree on datum.

Method: ordinary least-squares harmonic analysis. Each tidal constituent (M2, S2, K1, O1, ...) has a known
fixed frequency; the record is fitted as a constant plus a cosine and a sine at every frequency. The constant
is mean sea level (MSL) above the gauge zero, so a predicted tide is "metres above MSL" -- the same zero the
DEM and the app's other water levels use. Nodal corrections (the 18.6-year lunar cycle) are not applied; with
ten years of data they average out and are a few percent of M2, K1 and O1.

Limits stated plainly:
* One gauge. Tidal range varies along the coast (Karachi ~2.3 m); using Karachi's constants elsewhere on the
  Sindh/Balochistan coast is an approximation, the same one the rest of the coastal code already makes.
* The fit predicts the tide only. The record also contains surge, so the residual is a real (if small) estimate
  of non-tidal sea-level variation at Karachi, reported but not used to drive anything.
"""
import json
import os

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
TIDE_DIR = os.path.join(HERE, "global_data", "tide")
GAUGE_CSV = os.path.join(TIDE_DIR, "karachi_hourly_h147b.csv")
CONSTANTS_JSON = os.path.join(TIDE_DIR, "karachi_tide_constants.json")
MISSING_FLAG_MM = -9000          # UHSLC writes -32767 for a missing hour
HOLDOUT_YEARS = 2.0

# Constituent speeds in degrees per hour (standard values).
CONSTITUENTS = {
    "M2": 28.9841042, "S2": 30.0, "N2": 28.4397295, "K2": 30.0821373, "K1": 15.0410686, "O1": 13.9430356,
    "P1": 14.9589314, "Q1": 13.3986609, "M4": 57.9682084, "MS4": 58.9841042, "MN4": 57.4238337, "M6": 86.9523127,
    "2N2": 27.8953548, "MU2": 27.9682084, "NU2": 28.5125831, "L2": 29.5284789, "T2": 29.9589333,
    "J1": 15.5854433, "OO1": 16.1391017, "SA": 0.0410686, "SSA": 0.0821373, "MF": 1.0980331, "MM": 0.5443747,
}


def _design(hours):
    cols = [np.ones_like(hours)]
    for sp in CONSTITUENTS.values():
        a = np.deg2rad(sp) * hours
        cols += [np.cos(a), np.sin(a)]
    return np.column_stack(cols)


def read_gauge(path=GAUGE_CSV):
    """Returns (datetime64[h] array, level in metres above gauge zero with NaN for missing hours)."""
    import pandas as pd
    d = pd.read_csv(path, header=None, names=["y", "m", "d", "h", "v"])
    t = pd.to_datetime(dict(year=d.y, month=d.m, day=d.d, hour=d.h)).values.astype("datetime64[h]")
    v = d.v.astype(float).values
    v = np.where((v <= MISSING_FLAG_MM) | (v > 9000), np.nan, v / 1000.0)
    return t, v


def fit_gauge(path=GAUGE_CSV):
    """Harmonic fit with a held-out check. Returns the constants dict stored in CONSTANTS_JSON."""
    t, v = read_gauge(path)
    epoch = t[0]
    hrs = (t - epoch).astype("timedelta64[h]").astype(float)
    ok = ~np.isnan(v)
    X = _design(hrs)
    cut = t[-1] - np.timedelta64(int(HOLDOUT_YEARS * 365.25 * 24), "h")
    tr, te = ok & (t < cut), ok & (t >= cut)
    b_tr = np.linalg.lstsq(X[tr], v[tr], rcond=None)[0]
    pred_te = X[te] @ b_tr
    holdout_rmse = float(np.sqrt(np.mean((v[te] - pred_te) ** 2)))
    holdout_corr = float(np.corrcoef(v[te], pred_te)[0, 1])
    beta = np.linalg.lstsq(X[ok], v[ok], rcond=None)[0]
    resid = v - X @ beta
    amps = {k: [float(beta[1 + 2 * i]), float(beta[2 + 2 * i])] for i, k in enumerate(CONSTITUENTS)}
    msl = float(beta[0])
    ann = {}
    for yr in sorted(set(t.astype("datetime64[Y]").astype(int) + 1970)):
        sel = ok & (t.astype("datetime64[Y]").astype(int) + 1970 == yr)
        if sel.any():
            ann[int(yr)] = round(float(v[sel].max()), 3)
    tide = (X @ beta) - msl
    return {
        "station": "Karachi (hourly gauge h147b)", "epoch": str(epoch), "n_hours": int(ok.sum()),
        "period": [str(t[0]), str(t[-1])], "msl_above_gauge_zero_m": round(msl, 4),
        "constants_cos_sin_m": amps, "speeds_deg_per_hr": CONSTITUENTS,
        "holdout": {"years": HOLDOUT_YEARS, "rmse_m": round(holdout_rmse, 4), "corr": round(holdout_corr, 4)},
        "fit_rmse_m": round(float(np.sqrt(np.nanmean(resid[ok] ** 2))), 4),
        "residual_p99_m": round(float(np.nanpercentile(resid, 99)), 3), "residual_max_m": round(float(np.nanmax(resid)), 3),
        "predicted_tide_max_above_msl_m": round(float(tide.max()), 3), "predicted_tide_min_above_msl_m": round(float(tide.min()), 3),
        "observed_max_above_msl_m": round(float(np.nanmax(v) - msl), 3), "annual_max_above_gauge_zero_m": ann,
        "mean_daily_range_m": round(float(np.nanmean(_daily_range(t, v))), 3),
        "note": "levels are metres; predicted tide is relative to mean sea level (the DEM zero).",
    }


def _daily_range(t, v):
    days = t.astype("datetime64[D]")
    out = []
    for d in np.unique(days):
        x = v[days == d]
        x = x[~np.isnan(x)]
        if len(x) >= 20:
            out.append(x.max() - x.min())
    return out


_CACHE = {}


def load_constants():
    """Constants dict, refitting from the CSV (and saving) if the JSON is missing; None without any data."""
    if "c" in _CACHE:
        return _CACHE["c"]
    c = None
    if os.path.exists(CONSTANTS_JSON):
        with open(CONSTANTS_JSON) as f:
            c = json.load(f)
    elif os.path.exists(GAUGE_CSV):
        c = fit_gauge()
        try:
            with open(CONSTANTS_JSON, "w") as f:
                json.dump(c, f, indent=1)
        except OSError:
            pass
    _CACHE["c"] = c
    return c


def available():
    return load_constants() is not None


def msl_above_gauge_zero_m(default=1.849):
    """Mean sea level above the gauge zero (chart datum); falls back to the measured 1.849 m if data are absent."""
    c = load_constants()
    return float(c["msl_above_gauge_zero_m"]) if c else float(default)


def predict_tide_m(hours_since_epoch, constants=None):
    """Tide in metres above MSL at the given hours since the fit epoch (the first hour of the gauge record)."""
    c = constants or load_constants()
    if c is None:
        raise RuntimeError("no tide-gauge constants available")
    h = np.asarray(hours_since_epoch, dtype=float)
    out = np.zeros_like(h)
    for name, (a, b) in c["constants_cos_sin_m"].items():
        ang = np.deg2rad(c["speeds_deg_per_hr"][name]) * h
        out += a * np.cos(ang) + b * np.sin(ang)
    return out


TIDE_PHASES = {
    "spring_high": "the highest tide of two years falls at the surge peak (worst case)",
    "typical_high": "an ordinary high water falls at the surge peak",
    "low": "an ordinary low water falls at the surge peak (best case)",
}


def _pick_alignment(tide, phase):
    """Index in `tide` (hourly) where the surge peak should sit for the requested tidal phase."""
    mx = np.where((tide[1:-1] > tide[:-2]) & (tide[1:-1] >= tide[2:]))[0] + 1
    mn = np.where((tide[1:-1] < tide[:-2]) & (tide[1:-1] <= tide[2:]))[0] + 1
    if phase == "spring_high":
        return int(np.argmax(tide))
    if phase == "typical_high":
        return int(mx[np.argmin(np.abs(tide[mx] - np.median(tide[mx])))])
    if phase == "low":
        return int(mn[np.argmin(np.abs(tide[mn] - np.median(tide[mn])))])
    raise ValueError(f"unknown tide phase '{phase}'; use one of {sorted(TIDE_PHASES)}")


def tide_for_surge_window(n_hours, peak_hour, phase, constants=None, span_years=2.0):
    """Hourly tide (metres above MSL) for a window of `n_hours` whose surge peak is at `peak_hour`, positioned
    in the predicted tide so the peak meets the requested tidal phase. Returns (tide_array, tide_at_peak)."""
    c = constants or load_constants()
    epoch_h = int(np.datetime64(c["epoch"], "h").astype("int64"))
    start_h = np.datetime64(c["period"][1], "h").astype("int64") - epoch_h + 24      # just after the record
    hrs = start_h + np.arange(int(span_years * 365.25 * 24))
    tide = predict_tide_m(hrs, c)
    k = _pick_alignment(tide[:len(tide) - n_hours], phase) if len(tide) > n_hours else _pick_alignment(tide, phase)
    s = int(np.clip(k - peak_hour, 0, len(tide) - n_hours))
    win = tide[s:s + n_hours]
    return win, float(win[min(peak_hour, n_hours - 1)])
