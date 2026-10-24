"""One-page HTML (for PDF) summaries of a coastal or GLOF result the user already has on screen.

The browser posts back the result it received; every value is HTML-escaped here and only the fields below are read.
"""
from html import escape


def _rows(rows):
    return "<table>" + "".join(f"<tr><td>{escape(str(k))}</td><td>{escape(str(v))}</td></tr>" for k, v in rows if v not in (None, "")) + "</table>"


def _page(css, name, version, tagline, title, sections, notes):
    body = "".join(f"<h2>{escape(h)}</h2>{_rows(r)}" for h, r in sections if r)
    body += "".join(f'<p class="note">{escape(str(n))}</p>' for n in notes if n)
    return (f'<!doctype html><html><head><meta charset="utf-8"><title>{escape(title)}</title><style>{css}</style></head><body>'
            f'<div class="report-band"><h1>{escape(name)} v{escape(str(version))} &mdash; {escape(title)}</h1>'
            f'<p class="tagline">{escape(tagline)}</p></div><div class="report-body">{body}</div></body></html>')


def coastal_report_html(result, request, css, name, version, tagline):
    wl = result.get("water_level") or {}
    ru = result.get("wave_runup") or None
    rows = [("Return period (years)", request.get("return_period_years")),
            ("Water-level source", "GTSM (modelled)" if request.get("water_level_source") == "gtsm" else "Tide-gauge based (Khan et al.)"),
            ("Extra sea-level rise added (m)", request.get("extra_slr_m") or None),
            ("Total water level (m)", wl.get("total_water_level_m")),
            ("Flooded area (km²)", result.get("flooded_area_km2")),
            ("Maximum depth (m)", result.get("max_depth_m"))]
    sections = [("Coastal flood result", rows)]
    if ru:
        sections.append(("Wave run-up", [("Run-up added (m)", ru.get("runup_m")), ("Significant wave height (m)", ru.get("significant_wave_height_m")),
                                          ("Peak period (s)", ru.get("peak_period_s")), ("Foreshore slope", ru.get("foreshore_slope_used")),
                                          ("Total level with run-up (m)", result.get("total_level_with_runup_m")), ("Method", ru.get("method"))]))
    notes = [wl.get("basis"), (ru or {}).get("wave_basis"),
             "Static (bathtub) method: water is assumed to reach equilibrium at the stated level; no flow routing or timing is modelled."]
    return _page(css, name, version, tagline, "Coastal Flood Summary", sections, notes)


def glof_report_html(result, label, css, name, version, tagline):
    band = result.get("empirical_band") or {}
    rows = [("Lake / source", label), ("Peak discharge (m³/s)", result.get("peak_discharge_m3s")),
            ("Volume used (m³)", f"{result['volume_m3']:,.0f}" if isinstance(result.get("volume_m3"), (int, float)) else None),
            ("Suggested duration (h)", result.get("suggested_duration_hr")),
            ("Lake location", f"{result['lake_lat']:.3f}N, {result['lake_lon']:.3f}E" if result.get("lake_lat") is not None else None),
            ("Downstream site", f"{result['downstream_lat']:.3f}N, {result['downstream_lon']:.3f}E" if result.get("downstream_lat") is not None else None)]
    sections = [("GLOF discharge estimate", rows)]
    if band:
        eq = band.get("documented_equivalent_m3s") or {}
        sections.append(("Range implied by documented events", [("Low (m³/s)", eq.get("low")), ("Central (m³/s)", eq.get("central")),
                                                                 ("High (m³/s)", eq.get("high")), ("Lake type basis", band.get("basis")),
                                                                 ("Documented events used", band.get("n_events"))]))
    notes = [result.get("method"), result.get("note"), band.get("note"), band.get("caveat")]
    return _page(css, name, version, tagline, "GLOF Scenario Summary", sections, notes)
