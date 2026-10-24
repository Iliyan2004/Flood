"""
Documented glacial-lake outburst events (HMAGLOFDB, High Mountain Asia GLOF database; supplied with the GLOF
kit, global_data/glof/HMAGLOFDB_events.csv) used for (a) listing real historical events near a lake or AOI and
(b) checking the Popov (1991) discharge formula against the events that report both volume and peak discharge.

Real limits: the catalogue is a compilation, not a complete census (absence of a record is not absence of an
outburst); Pakistan has 151 events but only 9 with a volume and 3 with a peak discharge (all Shisper), so the
empirical bands below come mostly from other High Mountain Asia regions and rest on very few events per lake type.
"""
import os

import numpy as np

EVENTS_CSV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "global_data", "glof", "HMAGLOFDB_events.csv")

# SAILAB/PMD lake dam-type labels -> HMAGLOFDB lake-type labels
PMD_TO_HMA_TYPE = {"Morainic": "Moraine dammed", "Icedammed": "Ice dammed", "Supraglacial": "Supraglacial"}

_EVENTS = None


def events_available():
    return os.path.exists(EVENTS_CSV)


def _num(s):
    import pandas as pd
    return pd.to_numeric(s.astype(str).str.replace("\xa0", "", regex=False).str.strip().str.replace(",", ""), errors="coerce")


def load_events():
    global _EVENTS
    if _EVENTS is not None:
        return _EVENTS
    import pandas as pd
    df = pd.read_csv(EVENTS_CSV, encoding="latin1")
    out = pd.DataFrame({
        "id": df["GF_ID"], "year": _num(df["Year_approx"]), "month": _num(df["Month"]), "day": _num(df["Day"]),
        "lake_name": df["Lake_name"].astype(str).str.strip(), "lake_type": df["Lake_type"].astype(str).str.strip(),
        "mechanism": df["Mechanism"].astype(str).str.strip(), "country": df["Country"].astype(str).str.strip(),
        "province": df["Province"].astype(str).str.strip(),
        "lat": _num(df["Lat_lake"]), "lon": _num(df["Lon_lake"]),
        "impact_lat": _num(df["Lat_impact"]), "impact_lon": _num(df["Lon_impact"]),
        "volume_m3": _num(df["Volume"]), "peak_discharge_m3s": _num(df["Discharge_water"]), "area_m2": _num(df["Area"]),
        "lives": _num(df["Lives_total"]), "injured": _num(df["Injured_total"]), "displaced": _num(df["Displaced_total"]),
        "impact": df["Impact"].astype(str).str.replace("\xa0", "", regex=False).str.strip(),
        "reference": df["Ref_scientific"].astype(str).str.strip(),
    })
    out["impact"] = out["impact"].where(~out["impact"].isin(["", "nan"]), None)
    _EVENTS = out
    return out


def _haversine_km(lon1, lat1, lon2, lat2):
    lon1, lat1, lon2, lat2 = map(np.radians, (lon1, lat1, lon2, lat2))
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371.0088 * np.arcsin(np.sqrt(a))


def _clean(v):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return None
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating,)):
        return None if np.isnan(v) else float(v)
    return v


def events_near(lon, lat, radius_km=100.0, max_results=25, pakistan_only=True):
    """Documented events whose lake lies within radius_km, nearest first. [] when the catalogue is missing."""
    if not events_available():
        return []
    df = load_events()
    if pakistan_only:
        df = df[df.country == "Pakistan"]
    df = df[df.lat.notna() & df.lon.notna()].copy()
    df["distance_km"] = _haversine_km(lon, lat, df.lon.values, df.lat.values)
    df = df[df.distance_km <= radius_km].sort_values(["distance_km", "year"], ascending=[True, False]).head(max_results)
    rows = []
    for _, r in df.iterrows():
        rows.append({k: _clean(r[k]) for k in ("id", "year", "month", "day", "lake_name", "lake_type", "mechanism", "province", "lat", "lon",
                                              "volume_m3", "peak_discharge_m3s", "area_m2", "lives", "injured", "displaced", "impact", "reference")}
                    | {"distance_km": round(float(r.distance_km), 1)})
    return rows


def catalogue_summary():
    if not events_available():
        return None
    df = load_events()
    pk = df[df.country == "Pakistan"]
    return {"total_events": int(len(df)), "pakistan_events": int(len(pk)),
            "pakistan_since_2000": int((pk.year >= 2000).sum()),
            "pakistan_with_volume": int(pk.volume_m3.notna().sum()), "pakistan_with_peak_discharge": int(pk.peak_discharge_m3s.notna().sum())}


def popov_validation():
    """Popov / observed peak discharge on every event that reports both volume and peak discharge, by lake type."""
    from glof_utils import popov_peak_discharge_m3s
    if not events_available():
        return None
    df = load_events()
    b = df[df.volume_m3.notna() & df.peak_discharge_m3s.notna() & (df.volume_m3 > 0) & (df.peak_discharge_m3s > 0)].copy()
    b["ratio"] = popov_peak_discharge_m3s(b.volume_m3.values) / b.peak_discharge_m3s.values

    def stat(s):
        r = s.ratio.values
        return {"n": int(len(r)), "ratio_p10": float(np.percentile(r, 10)), "ratio_p25": float(np.percentile(r, 25)),
                "ratio_median": float(np.median(r)), "ratio_p75": float(np.percentile(r, 75)), "ratio_p90": float(np.percentile(r, 90))}
    res = {"all": stat(b), "by_type": {t: stat(g) for t, g in b.groupby("lake_type")},
           "points": [{"lake": r.lake_name, "year": _clean(r.year), "type": r.lake_type, "volume_m3": float(r.volume_m3),
                       "observed_m3s": float(r.peak_discharge_m3s), "popov_m3s": float(popov_peak_discharge_m3s(r.volume_m3))}
                      for _, r in b.iterrows()]}
    return res


def empirical_band(popov_m3s, lake_type=None):
    """Range of documented peak discharge implied by a Popov estimate, from the Popov/observed ratio spread of
    documented events of the same lake type (all types when the type is unknown or has fewer than 5 events).
    Returns None when the catalogue is unavailable."""
    val = popov_validation()
    if val is None:
        return None
    hma = PMD_TO_HMA_TYPE.get(lake_type, lake_type)
    st = val["by_type"].get(hma)
    used = hma
    if st is None or st["n"] < 5:
        st, used = val["all"], "all lake types"
    low, mid, high = popov_m3s / st["ratio_p90"], popov_m3s / st["ratio_median"], popov_m3s / st["ratio_p10"]
    caveat = []
    if st["n"] < 10:
        caveat.append(f"only {st['n']} documented events")
    if used != "all lake types" and "Ice" in str(used):
        caveat.append("Popov was fitted to moraine-dam failures and badly overestimates ice-dammed lakes")
    return {"basis": used, "n_events": st["n"], "ratio_median": round(st["ratio_median"], 2),
            "popov_over_observed_p10_p90": [round(st["ratio_p10"], 2), round(st["ratio_p90"], 2)],
            "documented_equivalent_m3s": {"low": round(low, 1), "central": round(mid, 1), "high": round(high, 1)},
            "caveat": "; ".join(caveat) or None,
            "note": ("Band = the Popov estimate divided by the Popov/observed ratio spread (10th-90th percentile) of documented "
                     "High Mountain Asia events of this lake type -- an empirical spread of the formula's error, not a prediction "
                     "for this lake.")}
