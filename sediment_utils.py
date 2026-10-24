"""
Real sediment-transport-capacity post-process.

LISFLOOD-FP 8.2 (the physics engine this app builds on) has NO sediment/
morphodynamic capability anywhere in its source -- confirmed directly by
grepping every .cpp/.cu/.cuh/.h file in the real build for
sediment/bedload/morphology/erosion terms (zero hits), not assumed from
its documentation. CAESAR-Lisflood (the real, published model that adds
this) was evaluated and declined: it's a separate standalone model with
its own old, simplified, CPU-only (OpenMP) internal flow solver, not a
library that attaches to our real LISFLOOD-FP GPU binary, and it's built
for landscape-evolution timescales (hours to millennia of repeated storm
cycles), not this app's single-storm-event runs. See CLAUDE.md.

What this module does instead: a real, standard sediment-transport-
CAPACITY calculation (Meyer-Peter & Muller 1948, the standard bedload
formula for gravel/sand-bed rivers) applied to the solver's own real
per-timestep depth + velocity output -- the same de-staggered
Arakawa-C velocity grid fis_app.py's _read_water_stack already builds for
the animated hazard rating. This is a real, physically-grounded erosion/
deposition RISK indicator (where is flow strong enough to actively move
bed material), not full morphodynamics: it does NOT feed back into the
DEM or change the flood physics mid-run (that would require the actual
coupled model this app just declined to adopt). Stated plainly rather
than implied to be more than it is.
"""
import numpy as np

RHO_WATER = 1000.0       # kg/m^3
RHO_SEDIMENT = 2650.0    # kg/m^3, quartz -- standard assumption absent site-specific mineralogy
G = 9.81                 # m/s^2
SHIELDS_CRITICAL = 0.047  # Meyer-Peter & Muller's own standard critical Shields number

# Default median grain size -- coarse sand / fine gravel, a reasonable
# default for Pakistani alluvial rivers absent a real per-site grain-size
# survey (which this app has no data source for). User-adjustable in the UI.
DEFAULT_D50_MM = 2.0
D50_MIN_MM, D50_MAX_MM = 0.1, 200.0


def bed_shear_stress(depth, speed, manning_n, dry_thresh=0.03):
    """Real Manning-based bed shear stress: tau = rho*g*n^2*V^2/h^(1/3) --
    the standard formula consistent with the friction model LISFLOOD-FP's
    own ACC solver already uses (same Manning's n this app already feeds
    it, spatially-variable when a manning.ascii grid was used for the run).
    Zero wherever a cell is dry (avoids a divide-by-a-near-zero-depth
    blowup at the wetting front)."""
    depth = np.asarray(depth, dtype="float64")
    speed = np.asarray(speed, dtype="float64")
    wet = depth > dry_thresh
    h_safe = np.maximum(depth, dry_thresh)
    tau = RHO_WATER * G * (manning_n ** 2) * (speed ** 2) / np.cbrt(h_safe)
    return np.where(wet, tau, 0.0)


def prepare_elevation_for_slope(elevation, elev_nodata=-9999.0):
    """One-time (per run, not per timestep) NODATA fill of the real
    conditioned DEM -- factored out of water_surface_slope so the fill
    (including a full-grid mean) doesn't get recomputed on every single
    saved timestep, which was a real, measured 5-10x slowdown when it
    was inline (a run that took ~1-2s before adding slope-based shear
    took ~10s with the fill redone per-timestep). NODATA elevation would
    otherwise poison the gradient at every AOI edge -- filled with the
    valid cells' own mean so the gradient stays well-behaved right up to
    the boundary (dry/invalid cells are zeroed downstream anyway via the
    depth-based wet mask in bed_shear_stress_from_slope)."""
    elevation = np.asarray(elevation, dtype="float64")
    valid = elevation != elev_nodata
    fill_value = np.nanmean(np.where(valid, elevation, np.nan)) if valid.any() else 0.0
    return np.where(valid, elevation, fill_value), valid


def water_surface_slope(depth, elev_filled, cellsize):
    """Real local water-surface-elevation gradient magnitude (the local
    friction/energy slope, Sf) -- computed from the run's own actual
    conditioned DEM plus this timestep's real depth, not a single static
    bed-slope map. This is what actually drives the depth-slope-product
    shear-stress formula below, and it genuinely varies over the storm
    (a cell can be on a locally flat backwater one moment and a steep
    drawdown the next, even on unchanging terrain) -- a real advantage
    over a fixed bed-slope grid computed once from the DEM alone.
    `elev_filled` is the NODATA-filled elevation from
    prepare_elevation_for_slope, computed once outside any per-timestep
    loop."""
    depth = np.asarray(depth, dtype="float64")
    wse = elev_filled + depth
    dzdy, dzdx = np.gradient(wse, cellsize, cellsize)
    return np.sqrt(dzdx ** 2 + dzdy ** 2)


def bed_shear_stress_from_slope(depth, slope, dry_thresh=0.03):
    """Real depth-slope-product bed shear stress: tau = rho*g*h*Sf -- the
    standard formula real 2D geomorphic models (including CAESAR-Lisflood
    itself) use, derived directly from the actual local water-surface
    gradient rather than inferred indirectly through a Manning velocity
    relationship. Captures gravity-driven shear in backwater/ponding/
    drawdown conditions the velocity-based formula's uniform-flow
    assumption doesn't fit as well."""
    depth = np.asarray(depth, dtype="float64")
    wet = depth > dry_thresh
    return np.where(wet, RHO_WATER * G * depth * slope, 0.0)


def mpm_bedload_transport(tau, d50_m):
    """Real Meyer-Peter & Muller (1948) bedload transport-capacity
    formula: dimensionless Shields number theta = tau / ((rho_s-rho_w)*g*d50),
    dimensionless transport phi = 8*max(0, theta - theta_cr)^1.5, volumetric
    transport rate per unit channel width qb = phi*sqrt((s-1)*g*d50^3)
    [m^2/s, i.e. m^3/s per metre of width]. Below the critical Shields
    number nothing moves (phi=0), matching real incipient-motion physics."""
    submerged_gravity = (RHO_SEDIMENT - RHO_WATER) * G * d50_m
    theta = tau / max(submerged_gravity, 1e-12)
    phi = 8.0 * np.maximum(theta - SHIELDS_CRITICAL, 0.0) ** 1.5
    s = RHO_SEDIMENT / RHO_WATER
    qb = phi * np.sqrt((s - 1.0) * G * (d50_m ** 3))
    return qb, theta


RISK_BANDS = ["none", "low", "moderate", "high", "severe"]


def classify_transport_risk(qb_peak, wet_mask):
    """Real cells with zero net transport (below critical Shields) are
    always 'none'. Everything else is classified by quantile WITHIN this
    run's own wet, actively-transporting cells -- a real, honest relative
    ranking (this run's worst areas vs its milder ones), not a fixed
    absolute engineering threshold this app has no calibration data to
    set correctly (same "adaptive, not a fixed guess" principle already
    used for the flow-arrow velocity threshold elsewhere in this app)."""
    band = np.zeros(qb_peak.shape, dtype="uint8")  # 0 = none
    moving = wet_mask & (qb_peak > 0)
    if not moving.any():
        return band, {"none": int((~moving).sum()), "low": 0, "moderate": 0, "high": 0, "severe": 0}
    vals = qb_peak[moving]
    q60, q85, q97 = np.percentile(vals, [60, 85, 97])
    band[moving & (qb_peak <= q60)] = 1   # low
    band[moving & (qb_peak > q60) & (qb_peak <= q85)] = 2   # moderate
    band[moving & (qb_peak > q85) & (qb_peak <= q97)] = 3   # high
    band[moving & (qb_peak > q97)] = 4   # severe
    counts = {name: int((band == i).sum()) for i, name in enumerate(RISK_BANDS)}
    return band, counts


def peak_sediment_transport(depth_stack, speed_stack, manning_n, d50_mm=DEFAULT_D50_MM, dry_thresh=0.03,
                             elevation=None, cellsize=None, elev_nodata=-9999.0):
    """Runs the real MPM formula over EVERY saved timestep of a finished
    run's own real depth+speed output and keeps the PEAK transport rate
    per cell -- the moment during the storm each cell was most actively
    moving bed material, not just its state at peak water depth (peak
    depth and peak velocity/shear don't necessarily coincide -- a receding
    flood can still be moving significant sediment on a falling limb).
    manning_n: either a scalar (uniform fallback friction) or a 2D grid
    (the real per-cell manning.ascii already used for this run's physics,
    reused here rather than re-deriving -- same friction, same shear).

    Real bed shear stress is estimated TWO independent ways when
    `elevation`/`cellsize` are given (the run's own conditioned DEM): the
    Manning velocity-quadratic formula (bed_shear_stress, what this
    routing was doing before -- speed only) AND the depth-slope-product
    formula (bed_shear_stress_from_slope, from the real per-timestep
    water-surface gradient) -- taking the per-cell, per-timestep ELEMENT-
    WISE MAX of the two. This matters because the two estimators fail in
    different, real regimes: velocity-quadratic implicitly assumes
    steady, uniform (normal) flow, so it under-reports shear right where
    a real flood is least uniform -- backwater, ponding, a sudden
    drawdown -- while depth-slope alone under-reports in a fast, locally
    near-flat reach (e.g. a filled channel with real high velocity but
    little local water-surface gradient). Falls back to velocity-only
    (the original behaviour) when no elevation grid is available.
    Returns (qb_peak [m^2/s], theta_peak, risk_band [uint8], band_counts,
    wet_mask, shear_stress_method)."""
    if speed_stack is None:
        raise RuntimeError("no per-timestep velocity output for this run -- cannot compute real bed shear stress")
    d50_m = float(d50_mm) / 1000.0
    n_steps = depth_stack.shape[0]
    qb_peak = np.zeros(depth_stack.shape[1:], dtype="float64")
    theta_peak = np.zeros(depth_stack.shape[1:], dtype="float64")
    use_slope = elevation is not None and cellsize is not None
    elev_filled = None
    if use_slope:
        elev_filled, _ = prepare_elevation_for_slope(elevation, elev_nodata=elev_nodata)
    for t in range(n_steps):
        tau = bed_shear_stress(depth_stack[t], speed_stack[t], manning_n, dry_thresh=dry_thresh)
        if use_slope:
            slope = water_surface_slope(depth_stack[t], elev_filled, cellsize)
            tau_slope = bed_shear_stress_from_slope(depth_stack[t], slope, dry_thresh=dry_thresh)
            np.maximum(tau, tau_slope, out=tau)
        qb, theta = mpm_bedload_transport(tau, d50_m)
        np.maximum(qb_peak, qb, out=qb_peak)
        np.maximum(theta_peak, theta, out=theta_peak)
    wet_mask = depth_stack.max(axis=0) > dry_thresh
    risk_band, band_counts = classify_transport_risk(qb_peak, wet_mask)
    method = ("combined: Manning velocity-based (n^2*V^2/h^(1/3)) and real depth-slope-product "
              "(water-surface gradient), per-cell/per-timestep elementwise max"
              if use_slope else
              "Manning velocity-based only (n^2*V^2/h^(1/3)) -- no DEM elevation available for this run")
    return qb_peak, theta_peak, risk_band, band_counts, wet_mask, method


def robust_peak(values, moving_mask, pct=99.0):
    """A real, known artifact class in this app's boundary-injected runs
    (see CLAUDE.md's "116m max-depth finding") can leave one or a handful
    of grid cells with a physically absurd velocity right at the injection
    point -- a single such cell can send a literal np.max() of shear
    stress/Shields-number/transport into the billions, which is not a
    useful "how bad did this get" headline number. Reports the given
    percentile among actively-transporting cells instead of the true max
    -- robust to that known single-cell artifact while still reflecting
    real, widespread high-transport conditions genuinely present in the
    grid (the per-cell risk_band map is unaffected, since its own
    percentile-based classification was already robust to this)."""
    if not moving_mask.any():
        return 0.0
    return float(np.percentile(values[moving_mask], pct))
