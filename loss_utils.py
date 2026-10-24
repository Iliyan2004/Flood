"""
Estimated monetary flood loss (PKR) -- converts this app's already-
computed real depth-damage FRACTIONS (Huizinga et al. 2017, JRC, Asia
region -- already used for building severity bands and Tab 5 land-use
damage) into an actual loss ESTIMATE, using the SAME paper's own real,
published maximum-damage-value table, rather than inventing an asset-
value assumption.

Real, cited source: Huizinga, J., De Moel, H. and Szewczyk, W. (2017)
"Global flood depth-damage functions: Methodology and the database with
guidelines", JRC Technical Report EUR 28552 EN -- Table 3-21 ("Average
maximum damage value per continent [Euro/m2 - 2010]"), Asia column:
  Residential buildings: 111 EUR/m2
  Agriculture:             0.03 EUR/m2 (per m2 of land, i.e. 300 EUR/ha)
Both are the paper's own CONTINENTAL (Asia) average -- confirmed directly
from the paper's own text that Pakistan has no country-specific value in
its Table 3-22 (Pakistan appears in Table 2-1 as a country with SOME real
literature-reported damage data, but not among the countries whose max-
damage value made it into the final per-country table) -- so this is the
best real, defensible reference available, stated plainly as a regional
average and not a Pakistan-specific fit, the same honesty pattern already
used in this app for coastal_utils.py's Karachi-only tide/surge reference
and glof_utils.py's Huggel et al. area-volume fallback.

Real PKR conversion, at explicit user request: the source paper's own
2010-EUR/m2 figures are converted to PKR via a real, current EUR/PKR
mid-market exchange rate (EUR_TO_PKR_RATE below) -- a real, but stated,
simplification: this is a CURRENT-DATE currency conversion applied to a
2010-price-level real estate/construction-cost figure, not a true
inflation-adjusted-then-converted PKR value (that would need a separate,
real, cited EUR inflation index 2010->today on top of the FX conversion,
which this module does not attempt). The gap is stated here rather than
presented as more precise than it is.

Deliberately does NOT price every WorldCover class. JRC's own damage-
class system only covers built assets and agriculture -- it does not
assign a replacement-cost value to natural land cover (forest, grassland,
shrubland, wetland, mangrove, bare ground, snow, open water), which this
app's own DEPTH_DAMAGE_CURVES_ASIA nonetheless computes a real damage
FRACTION for (used elsewhere for the land-use damage-% breakdown and
hotspot analysis). Pricing only what JRC itself prices avoids both
double-counting (a "Built-up" cell's land value here would double-count
the SAME ground already valued via the real building footprints standing
on it) and inventing a natural-asset value JRC's own framework doesn't
provide.
"""
import numpy as np
from shapely.geometry import Polygon

# Huizinga et al. 2017, Table 3-21, Asia column, EUR/m^2, 2010 price level.
JRC_ASIA_MAX_DAMAGE_EUR_PER_M2 = {
    "Residential": 111.0,
    "Agriculture": 0.03,
}

# Real EUR/PKR mid-market exchange rate (xe.com/Investing.com/
# ExchangeRates.org.uk, checked live, ~322.5 as of this writing) -- a
# real, current-date conversion rate, not an inflation-adjusted one (see
# module docstring). Kept as one named constant so it's obvious where to
# refresh it from a real live rate later rather than buried in arithmetic.
EUR_TO_PKR_RATE = 322.5
PKR_RATE_NOTE = "EUR/PKR mid-market rate ~322.5 (checked live, Sept 2026) -- refresh this from a real current rate periodically"

JRC_ASIA_MAX_DAMAGE_PKR_PER_M2 = {
    k: round(v * EUR_TO_PKR_RATE, 4) for k, v in JRC_ASIA_MAX_DAMAGE_EUR_PER_M2.items()
}

# FEMA/HAZUS standard convention: residential CONTENT value assumed as a
# fraction of structure (replacement) value -- a real, widely-cited
# published ratio (FEMA HAZUS-MH Flood Model technical manual), not an
# invented number, used here since JRC's own max-damage table prices
# structure/building replacement cost only, with no separate contents
# figure of its own.
CONTENT_TO_STRUCTURE_RATIO = 0.50

# Same real ~3m/storey conversion this app already uses elsewhere
# (buildings_utils.py/infra_utils.py's OSM building:levels -> height_m),
# reused here in reverse (height_m -> storey count) so the same real
# assumption isn't duplicated with a different value.
ASSUMED_STORY_HEIGHT_M = 3.0


def estimate_building_loss_pkr(building_features, cellsize_m):
    """Real structure + content loss estimate (PKR) from the SAME per-
    building real footprint/height/damage_fraction data already computed
    by infra_utils.assess_building_submersion_risk (its own "features"
    list, with poly_rc/height_m/damage_fraction present per building) --
    no second depth-sampling pass, just a monetary read of numbers this
    app already computed. Floor area = real footprint area (from poly_rc,
    converted to m^2 via the DEM's own real cellsize) x storey count
    (height_m / 3m, floored at 1 storey) -- the same real technique
    (footprint x storeys) any building-stock valuation uses when a
    surveyed floor area isn't available.

    Returns per-building totals plus buildings_counted/buildings_skipped
    (a footprint with too few vertices to form a real polygon, or zero
    real area, is skipped rather than crashing or silently zeroed)."""
    structure_loss = 0.0
    content_loss = 0.0
    counted = 0
    skipped = 0
    for fp in building_features:
        damage_fraction = fp.get("damage_fraction", 0.0)
        if not damage_fraction:
            counted += 1  # a real, correctly-classified dry building -- zero loss, still counted
            continue
        poly_rc = fp.get("poly_rc") or []
        if len(poly_rc) < 3:
            skipped += 1
            continue
        try:
            footprint_area_m2 = Polygon(poly_rc).area * (cellsize_m ** 2)
        except Exception:
            skipped += 1
            continue
        if footprint_area_m2 <= 0:
            skipped += 1
            continue
        storeys = max(1.0, fp.get("height_m", ASSUMED_STORY_HEIGHT_M) / ASSUMED_STORY_HEIGHT_M)
        floor_area_m2 = footprint_area_m2 * storeys
        structure_value_pkr = floor_area_m2 * JRC_ASIA_MAX_DAMAGE_PKR_PER_M2["Residential"]
        this_structure_loss = structure_value_pkr * damage_fraction
        structure_loss += this_structure_loss
        content_loss += this_structure_loss * CONTENT_TO_STRUCTURE_RATIO
        counted += 1

    return {
        "structure_loss_pkr": round(structure_loss, 2),
        "content_loss_pkr": round(content_loss, 2),
        "total_loss_pkr": round(structure_loss + content_loss, 2),
        "buildings_counted": counted,
        "buildings_skipped_no_geometry": skipped,
        "method": ("Huizinga et al. 2017 (JRC) Asia-region residential max damage "
                   f"({JRC_ASIA_MAX_DAMAGE_EUR_PER_M2['Residential']} EUR/m^2, 2010 price level, converted to "
                   f"PKR at {EUR_TO_PKR_RATE}/EUR -- {PKR_RATE_NOTE}) x each real building's own footprint area "
                   "x storey count (height/3m) x its own real Huizinga depth-damage fraction; content loss = "
                   f"{int(CONTENT_TO_STRUCTURE_RATIO*100)}% of structure loss (FEMA/HAZUS standard residential "
                   "content ratio). A regional (Asia) average, not a Pakistan-specific value -- Pakistan has no "
                   "country-specific figure in the source paper's own final table. PKR conversion is a real "
                   "CURRENT-DATE exchange rate applied to a 2010-price-level figure, not inflation-adjusted."),
    }


def estimate_agricultural_loss_pkr(damage_array, lulc_array, valid_mask, cellsize_m,
                                    cropland_code=40):
    """Real agricultural loss estimate (PKR): real cropland cell area
    (m^2, from the DEM's own cellsize) x that cell's own real Huizinga
    damage fraction (already computed by damage_utils.compute_damage) x
    JRC's own real Asia-region agriculture max-damage-per-m^2, converted
    to PKR. `cropland_code` defaults to ESA WorldCover's real "Cropland"
    class code (40, see damage_utils.WORLDCOVER_CLASSES) -- the only
    WorldCover class this module has a real published max-damage value
    for."""
    mask = (lulc_array == cropland_code) & valid_mask
    n_cells = int(mask.sum())
    if n_cells == 0:
        return {"agricultural_loss_pkr": 0.0, "cropland_area_km2": 0.0,
                "method": "no real cropland cells in this AOI"}
    cell_area_m2 = cellsize_m ** 2
    loss_pkr = float(damage_array[mask].sum()) * cell_area_m2 * JRC_ASIA_MAX_DAMAGE_PKR_PER_M2["Agriculture"]
    return {
        "agricultural_loss_pkr": round(loss_pkr, 2),
        "cropland_area_km2": round(n_cells * cell_area_m2 / 1e6, 4),
        "method": ("Huizinga et al. 2017 (JRC) Asia-region agriculture max damage "
                   f"({JRC_ASIA_MAX_DAMAGE_EUR_PER_M2['Agriculture']} EUR/m^2, 2010 price level, converted to "
                   f"PKR at {EUR_TO_PKR_RATE}/EUR -- {PKR_RATE_NOTE}) x each real cropland cell's own real "
                   "Huizinga damage fraction x its real cell area. A regional (Asia) average, not a "
                   "Pakistan-specific value. PKR conversion is a real CURRENT-DATE exchange rate applied to a "
                   "2010-price-level figure, not inflation-adjusted."),
    }
