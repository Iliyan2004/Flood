"""
Real, per-cell soil infiltration parameters from SoilGrids 2.0 (ISRIC),
downloaded once for Pakistan by `global_data/fetch_soilgrids_pakistan.py`
and cached locally (`global_data/soil_data/pakistan_{sand,clay,bdod}.tif`)
-- see that script's own docstring for why a local Pakistan-clipped copy,
not a global bulk download, is what "save it here" means in practice
(SoilGrids' full global 250m layers are multiple GB each; this app's own
Pakistan coverage is a ~48MB clip per layer).

Turns real soil texture (sand/clay % from SoilGrids) into a per-cell
saturated hydraulic conductivity (Ksat, mm/hr) via the standard USDA soil-
texture classification + the published Rawls, Brakensiek & Miller (1983)
Ksat-by-texture-class lookup table -- the same real, standard soil
hydraulic properties reference already used in SWAT/HEC-HMS-style
infiltration modeling, not a fitted/invented model. This is the direct
Tier-0 counterpart to `damage_utils.py`'s existing per-cell Manning's-n-
from-land-cover mechanism: real classified physical data -> a real
published parameter table -> a per-cell grid the solver consumes.

Ksat is used here as this app's per-cell INFILTRATION rate (mm/hr) --
Green-Ampt/Rawls Ksat is the soil's own saturated infiltration capacity,
a standard and direct (if simplified -- no ponding/wetting-front
dynamics) substitute for this solver's already-simplified flat-rate
infiltration parameter (see flood_model.py / CLAUDE.md's AMC section for
why this solver only ever had ONE flat rate before per-cell friction was
added; this is the same category of upgrade, applied to infiltration
instead of friction).
"""
import os

import numpy as np
import rasterio
from rasterio.warp import reproject, Resampling

SOIL_DATA_DIR = os.path.join(os.path.dirname(__file__), "global_data", "soil_data")

# Rawls, Brakensiek & Miller (1983), "Green-Ampt Infiltration Parameters
# from Soils Data", ASCE Journal of Hydraulic Engineering -- published
# saturated hydraulic conductivity by USDA soil texture class, converted
# here from their original cm/hr to mm/hr.
RAWLS_KSAT_MM_HR = {
    "sand": 210.0, "loamy_sand": 61.1, "sandy_loam": 25.9, "loam": 13.2,
    "silt_loam": 6.8, "sandy_clay_loam": 4.3, "clay_loam": 2.3,
    "silty_clay_loam": 1.5, "sandy_clay": 1.2, "silty_clay": 0.9, "clay": 0.6,
}

ABS_MIN_INFILTRATION_MM_HR = 0.1  # never fully impervious in this lookup

# Real Green-Ampt parameters (Rawls, Brakensiek & Miller 1983, "Green-Ampt
# Infiltration Parameters from Soils Data", ASCE J. Hydraulic Engineering
# 109(1):62-70, Table 2 -- verified directly from the original paper, not
# guessed): total porosity (phi), effective porosity (theta_e), wetted
# front capillary/suction pressure head (psi, cm), and the Green-Ampt
# hydraulic conductivity K (cm/hr -- already Bouwer's K=Ksat/2 correction
# per the paper's own Eq. 6, NOT the same column as RAWLS_KSAT_MM_HR
# above, which is a separate saturated-Ksat table used for the simpler
# flat-rate approximation). This is the real per-class data set behind
# green_ampt_infiltration_curve below.
GREEN_AMPT_PARAMS = {
    "sand":            {"phi": 0.437, "theta_e": 0.417, "psi_cm": 4.95,  "k_cm_hr": 11.78},
    "loamy_sand":      {"phi": 0.437, "theta_e": 0.401, "psi_cm": 6.13,  "k_cm_hr": 2.99},
    "sandy_loam":      {"phi": 0.453, "theta_e": 0.412, "psi_cm": 11.01, "k_cm_hr": 1.09},
    "loam":            {"phi": 0.463, "theta_e": 0.434, "psi_cm": 8.89,  "k_cm_hr": 0.34},
    "silt_loam":       {"phi": 0.501, "theta_e": 0.486, "psi_cm": 16.68, "k_cm_hr": 0.65},
    "sandy_clay_loam": {"phi": 0.398, "theta_e": 0.330, "psi_cm": 21.85, "k_cm_hr": 0.15},
    "clay_loam":       {"phi": 0.464, "theta_e": 0.309, "psi_cm": 20.88, "k_cm_hr": 0.10},
    "silty_clay_loam": {"phi": 0.471, "theta_e": 0.432, "psi_cm": 27.30, "k_cm_hr": 0.10},
    "sandy_clay":      {"phi": 0.430, "theta_e": 0.321, "psi_cm": 23.90, "k_cm_hr": 0.06},
    "silty_clay":      {"phi": 0.479, "theta_e": 0.423, "psi_cm": 29.22, "k_cm_hr": 0.05},
    "clay":            {"phi": 0.475, "theta_e": 0.385, "psi_cm": 31.63, "k_cm_hr": 0.03},
}


def green_ampt_cumulative_infiltration_at(k_cm_hr, psi_cm, delta_theta, t_hr):
    """Real Green & Ampt (1911) cumulative infiltration F (cm) at time
    t_hr, solving the model's own IMPLICIT equation directly for F:
        F - psi*delta_theta*ln(1 + F/(psi*delta_theta)) = K*t
    (the instantaneous rate is f(t) = K*(1 + psi*delta_theta/F(t)), which
    is singular as F->0 -- integrating that ODE forward in time with a
    fixed step blows up near t=0, confirmed directly by testing: an
    explicit-Euler stepper produced rates in the millions of mm/hr on the
    very first step. Solving the implicit equation for F at the target
    time directly, via bisection, has no such singularity and is the
    standard way this equation is actually solved in practice.)

    delta_theta (moisture DEFICIT, cm^3/cm^3) = theta_e * (1 - Si), Si the
    real initial degree of saturation -- this is the real, physically
    correct role antecedent soil moisture plays in infiltration (a wetter
    soil has a smaller deficit and infiltrates less), replacing this app's
    older AMC_INFILTRATION_MULTIPLIER heuristic table with real Green-Ampt
    physics when this function is used."""
    if delta_theta <= 1e-9:
        return k_cm_hr * t_hr  # zero deficit -> the log term vanishes, F=K*t exactly
    b = psi_cm * delta_theta
    target = k_cm_hr * t_hr

    def g(F):
        return F - b * np.log(1.0 + F / b) - target

    # g(0) = -target < 0 (for t>0); g is monotonically increasing in F
    # for F>0 (dg/dF = 1 - b/(F+b) = F/(F+b) > 0) -- exactly one positive
    # root, so real bisection on an expanding bracket is fully robust.
    lo, hi = 0.0, max(target, b, 1e-6)
    while g(hi) < 0:
        hi *= 2.0
    for _ in range(80):
        mid = 0.5 * (lo + hi)
        if g(mid) < 0:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def green_ampt_average_rate_mm_hr(texture_class, duration_hr, saturation_frac):
    """Real Green-Ampt equivalent AVERAGE infiltration rate (mm/hr) over a
    storm of the given duration -- F(duration)/duration, using the real
    published parameters for `texture_class` (GREEN_AMPT_PARAMS) and a
    real moisture deficit derived from `saturation_frac` (0=fully dry,
    1=fully saturated -- the same real physical quantity this app's AMC
    slider (0-100) already represents, just normalized to 0-1).

    This average is what actually gets fed to LISFLOOD-FP's own solver
    (which only ever accepts ONE flat scalar infiltration rate, confirmed
    from source -- see mean_infiltration_mm_hr_for_aoi's own docstring for
    why a true time-varying rate can't be wired in directly): Green-Ampt's
    real curve starts high (near-infinite as F->0) and decays toward K as
    the wetting front advances, so this average is a genuinely more
    accurate summary of a storm's real total infiltration than plugging
    Ksat straight in, even though the solver still only sees one number."""
    p = GREEN_AMPT_PARAMS[texture_class]
    delta_theta = p["theta_e"] * (1.0 - np.clip(saturation_frac, 0.0, 1.0))
    F = green_ampt_cumulative_infiltration_at(p["k_cm_hr"], p["psi_cm"], delta_theta, duration_hr)
    avg_rate_cm_hr = F / duration_hr
    return max(avg_rate_cm_hr * 10.0, ABS_MIN_INFILTRATION_MM_HR)  # cm/hr -> mm/hr

# Representative median grain diameter (d50, mm) per USDA texture class --
# real, published Wentworth/USDA particle-size class midpoints (e.g. medium
# sand ~0.5mm, silt ~0.02-0.03mm, clay <0.002mm), used to give
# sediment_utils.py's Meyer-Peter & Muller bedload formula a real,
# soil-informed d50 instead of one flat guess for the whole country.
# Honest caveat, same category as this module's own infiltration
# docstring: this is FLOODPLAIN/topsoil texture (the only real, freely
# available per-cell grain-size proxy this app has), not a surveyed river
# CHANNEL bed-material sample -- a real river channel routinely armors to
# coarser material than its surrounding floodplain soil. Used as the best
# available real default; still user-overridable.
D50_MM_BY_TEXTURE = {
    "sand": 0.50, "loamy_sand": 0.30, "sandy_loam": 0.20, "sandy_clay_loam": 0.15,
    "sandy_clay": 0.10, "loam": 0.05, "clay_loam": 0.02, "silt_loam": 0.025,
    "silty_clay_loam": 0.015, "silty_clay": 0.008, "clay": 0.003,
}
# Meyer-Peter & Muller's own original experiments only covered sand/gravel
# (~0.4-29mm) -- fine cohesive silt/clay follows different (cohesive)
# erosion physics the formula was never fit to. Floored here rather than
# silently extrapolated the formula past its real validated range.
D50_MPM_VALID_MIN_MM = 0.2


def usda_texture_class(sand_pct, clay_pct):
    """Approximate USDA soil-texture-triangle classification from sand/
    clay percentages (silt is implied as the remainder). A simplified set
    of boundary rules, not a verbatim reproduction of the full 12-class
    USDA diagram's exact diagonal boundaries -- documented as an
    approximation rather than claimed as exact, since what actually
    matters for this app's per-cell infiltration grid is the real,
    correct MONOTONIC trend (more sand -> higher Ksat, more clay -> lower
    Ksat), which this preserves, not perfect class-boundary precision at
    every edge case."""
    silt_pct = 100.0 - sand_pct - clay_pct
    s, c = sand_pct, clay_pct
    if s >= 85 and c < 10:
        return "sand"
    if s >= 70 and c < 15:
        return "loamy_sand"
    if c < 20 and s >= 43 and (s - c) >= 20:
        return "sandy_loam"
    if c >= 40 and s <= 20:
        return "silty_clay"
    if c >= 40 and s <= 45:
        return "clay" if s < 45 - c * 0.25 else "sandy_clay"
    # Real, confirmed gap found by direct testing: every branch above this
    # point requires s <= 45, so a real high-sand (>45%) AND high-clay
    # (>=35%) combination with low silt (e.g. sand=60/clay=40/silt=0) fell
    # through all the way to the generic "loam" default below -- a real
    # USDA "Sandy Clay" soil (Ksat 1.2 mm/hr) misclassified as "loam"
    # (Ksat 13.2 mm/hr, an 11x overestimate of infiltration capacity, in
    # the wrong direction for a flood model: it would UNDERSTATE real
    # flood risk for these soils). The real USDA boundary for Sandy Clay
    # is sand>=45% AND clay>=35%, independent of silt -- added directly,
    # not folded into the s<=45 branch above (which already handles the
    # c>=40,s<=45 half of this same class correctly).
    if c >= 35 and s >= 45:
        return "sandy_clay"
    if c >= 27 and s <= 20:
        return "silty_clay_loam"
    if c >= 27 and s <= 45:
        return "clay_loam"
    if c >= 20 and c < 35 and s >= 45 and silt_pct < 28:
        return "sandy_clay_loam"
    if silt_pct >= 50 and c < 27:
        return "silt_loam"
    return "loam"


def ksat_lookup_array():
    """A 0-100 x 0-100 (sand%, clay%) lookup table of Ksat (mm/hr),
    vectorized over -- built once, reused per pixel via array indexing
    rather than calling usda_texture_class per cell in a slow python
    loop over potentially millions of pixels."""
    table = np.zeros((101, 101), dtype="float32")
    for s in range(101):
        for c in range(101 - s):
            cls = usda_texture_class(s, c)
            table[s, c] = RAWLS_KSAT_MM_HR[cls]
    return table


_KSAT_TABLE = None


def _get_ksat_table():
    global _KSAT_TABLE
    if _KSAT_TABLE is None:
        _KSAT_TABLE = ksat_lookup_array()
    return _KSAT_TABLE


def pakistan_soil_data_available():
    return all(
        os.path.exists(os.path.join(SOIL_DATA_DIR, f"pakistan_{n}.tif"))
        for n in ("sand", "clay", "bdod")
    )


def _sand_clay_pct_for_dem(dst_header, dst_crs):
    """Shared real fetch+reproject step for every per-cell soil-texture
    product this module derives (Ksat, d50, ...) -- resamples the locally-
    cached Pakistan sand/clay SoilGrids layers onto the flood run's own
    DEM grid (same technique as damage_utils.fetch_lulc_for_bounds).
    Returns (sand_pct, clay_pct) or None if the local soil data hasn't
    been fetched yet (fetch_soilgrids_pakistan.py hasn't been run)."""
    if not pakistan_soil_data_available():
        return None

    ncols, nrows, cs = int(dst_header["ncols"]), int(dst_header["nrows"]), dst_header["cellsize"]
    dst_transform = rasterio.transform.from_origin(
        dst_header["xllcorner"], dst_header["yllcorner"] + nrows * cs, cs, cs)

    sand = np.zeros((nrows, ncols), dtype="float32")
    clay = np.zeros((nrows, ncols), dtype="float32")
    for name, dst_arr in (("sand", sand), ("clay", clay)):
        path = os.path.join(SOIL_DATA_DIR, f"pakistan_{name}.tif")
        with rasterio.open(path) as src:
            reproject(
                source=rasterio.band(src, 1), destination=dst_arr,
                src_transform=src.transform, src_crs=src.crs,
                dst_transform=dst_transform, dst_crs=dst_crs,
                resampling=Resampling.bilinear,
            )
    # SoilGrids reports sand/clay as g/kg * 10 (i.e. permille) -- real,
    # documented SoilGrids convention (divide by 10 for %).
    sand_pct = np.clip(sand / 10.0, 0, 100)
    clay_pct = np.clip(clay / 10.0, 0, 100 - 0)
    clay_pct = np.minimum(clay_pct, 100 - sand_pct)
    return sand_pct, clay_pct


def infiltration_grid_for_dem(dst_header, dst_crs):
    """Real per-cell infiltration rate (mm/hr) grid via the real Rawls
    Ksat-by-texture lookup. Returns None if the local soil data hasn't
    been fetched yet -- callers fall back to the existing flat
    infiltration rate, same graceful-degradation pattern used elsewhere."""
    pct = _sand_clay_pct_for_dem(dst_header, dst_crs)
    if pct is None:
        return None
    sand_pct, clay_pct = pct

    table = _get_ksat_table()
    s_idx = np.clip(sand_pct.round().astype("int32"), 0, 100)
    c_idx = np.clip(clay_pct.round().astype("int32"), 0, 100)
    c_idx = np.minimum(c_idx, 100 - s_idx)
    ksat_grid = table[s_idx, c_idx]

    return np.maximum(ksat_grid, ABS_MIN_INFILTRATION_MM_HR)


def _get_d50_table():
    global _D50_TABLE
    if _D50_TABLE is None:
        table = np.zeros((101, 101), dtype="float32")
        for s in range(101):
            for c in range(101 - s):
                cls = usda_texture_class(s, c)
                table[s, c] = D50_MM_BY_TEXTURE[cls]
        _D50_TABLE = table
    return _D50_TABLE


_D50_TABLE = None


def d50_grid_for_dem(dst_header, dst_crs):
    """Real per-cell median grain size (d50, mm) grid, from the same real
    SoilGrids sand/clay texture already used for infiltration -- see
    D50_MM_BY_TEXTURE's own docstring for the real floodplain-texture-vs-
    channel-bed-material caveat. Floored at D50_MPM_VALID_MIN_MM (the
    real range Meyer-Peter & Muller's own formula was fit to). Returns
    None if local soil data isn't available."""
    pct = _sand_clay_pct_for_dem(dst_header, dst_crs)
    if pct is None:
        return None
    sand_pct, clay_pct = pct

    table = _get_d50_table()
    s_idx = np.clip(sand_pct.round().astype("int32"), 0, 100)
    c_idx = np.clip(clay_pct.round().astype("int32"), 0, 100)
    c_idx = np.minimum(c_idx, 100 - s_idx)
    d50_grid = table[s_idx, c_idx]

    return np.maximum(d50_grid, D50_MPM_VALID_MIN_MM)


def mean_d50_mm_for_aoi(dst_header, dst_crs, valid_mask=None):
    """Area-weighted mean real soil-derived d50 (mm) for an AOI -- the
    single real default sediment_utils.py uses unless the user overrides
    it, same "smarter default than one flat guess" principle as
    mean_infiltration_mm_hr_for_aoi. Returns None if soil data isn't
    available (caller falls back to sediment_utils.DEFAULT_D50_MM)."""
    grid = d50_grid_for_dem(dst_header, dst_crs)
    if grid is None:
        return None
    if valid_mask is not None:
        grid = grid[valid_mask]
    return float(np.mean(grid))


def mean_infiltration_mm_hr_for_aoi(dst_header, dst_crs, valid_mask=None):
    """A single, real-soil-informed infiltration rate (mm/hr) for an
    entire AOI -- the area-weighted mean of the real per-cell Ksat grid
    above, restricted to `valid_mask` (the AOI's own real cells, not the
    DEM's rectangular bounding box) when given.

    Why a single number, not the per-cell grid itself: confirmed directly
    from LISFLOOD-FP 8.2's own source (not assumed) that its ACC/CUDA
    solver -- the only solver this app actually runs -- has no code path
    consuming a distributed infiltration file at all (`initialise_manning`
    is called in cuda/acc/cuda_acc_simulate.cu; no equivalent call exists
    there for infiltration, even though the file FORMAT and CPU-only
    loading code for one technically exist elsewhere in the source). This
    is the same class of CUDA-path gap CLAUDE.md already documents for
    `res.maxHaz` and the `Qin` mass-balance column -- but unlike those
    (diagnostic outputs recoverable after the fact from other real solver
    output), infiltration is a loss term that has to be subtracted from
    rainfall DURING the run to affect the actual depth result, so it
    can't be retrofitted post-hoc the same way. Rather than silently wire
    in a per-cell file the solver would ignore, this uses the real
    fetched soil data to inform a single, physically-grounded flat rate
    -- honestly a smarter flat number, not genuine per-cell spatial
    infiltration physics (unlike Manning's friction, which the solver
    genuinely does support per-cell)."""
    grid = infiltration_grid_for_dem(dst_header, dst_crs)
    if grid is None:
        return None
    if valid_mask is not None:
        grid = grid[valid_mask]
    return float(np.mean(grid))


def mean_green_ampt_rate_mm_hr_for_aoi(dst_header, dst_crs, duration_hr, saturation_frac, valid_mask=None):
    """Real Green-Ampt-derived infiltration rate (mm/hr) for an entire
    AOI: classifies each cell's real SoilGrids sand/clay into a USDA
    texture class (same real reproject/classification already used for
    Ksat and d50 above), computes that cell's own real Green-Ampt average
    rate (green_ampt_average_rate_mm_hr) for the given storm duration and
    antecedent saturation, then area-averages the resulting RATES (not
    the raw physical parameters first -- the Green-Ampt equation is
    nonlinear in phi/psi/K, so averaging final rates per cell is the
    correct order of operations, not averaging inputs then solving once).

    Real, physically-grounded alternative to this app's older
    AMC_INFILTRATION_MULTIPLIER heuristic table (flood_model.py): here,
    antecedent moisture (saturation_frac, 0-1, from the same AMC 0-100
    slider) sets a real moisture DEFICIT (theta_e*(1-saturation_frac))
    that drives the actual Green-Ampt physics, rather than an arbitrary
    1.8x/1.0x/0.25x multiplier applied to an unrelated baseline number.
    Returns None if local SoilGrids data isn't available (same graceful
    degradation as the other soil_utils functions)."""
    pct = _sand_clay_pct_for_dem(dst_header, dst_crs)
    if pct is None:
        return None
    sand_pct, clay_pct = pct
    if valid_mask is not None:
        sand_pct = sand_pct[valid_mask]
        clay_pct = clay_pct[valid_mask]
    if sand_pct.size == 0:
        return None

    # Real per-cell classification is a per-pixel python loop here (unlike
    # the vectorized lookup tables above) since green_ampt_average_rate_mm_hr
    # itself does a real small numerical integration per call -- fine at
    # this scale (a flood run's AOI is at most tens of thousands of cells,
    # and this is computed once per run, not per timestep).
    classes = np.array([usda_texture_class(s, c) for s, c in zip(sand_pct.round(), clay_pct.round())])
    rates = np.array([
        green_ampt_average_rate_mm_hr(cls, duration_hr, saturation_frac)
        for cls in np.unique(classes)
    ])
    unique_classes = np.unique(classes)
    rate_by_class = dict(zip(unique_classes, rates))
    per_cell_rates = np.array([rate_by_class[c] for c in classes])
    return float(np.mean(per_cell_rates))
