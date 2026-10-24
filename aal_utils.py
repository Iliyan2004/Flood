"""
Average Annual Loss (AAL) -- the standard catastrophe-risk/insurance
summary metric, computed here from real per-return-period building loss
figures this app already produces (see loss_utils.py), not a new or
invented statistic.

Real, standard method: trapezoidal integration of loss against annual
exceedance probability (the same "Expected Annual Damage"/AAL technique
documented in FEMA's HAZUS technical manual and used throughout
catastrophe risk modeling practice) --

    AAL = integral over p from 0 to 1 of L(p) dp

where p is the annual exceedance probability (p = 1/T for a real
return period T in years) and L(p) is the real estimated loss at that
probability. Given a real, finite set of (T, loss) points from actual
physics runs at several return periods, this is computed as:

  1. A tail below the smallest observed exceedance probability (i.e.
     beyond the LARGEST real return period tested) -- assumed constant
     at that largest return period's own real loss value (a standard,
     conservative simplification: an even-rarer, even-worse event isn't
     assumed to cost less).
  2. Trapezoidal integration between each pair of real, adjacent
     observed points.
  3. A tail above the largest observed exceedance probability (i.e.
     more frequent than the SMALLEST real return period tested) --
     assumed to decrease linearly to zero loss at p=1 (a 1-year "return
     period" is, by convention, treated as a non-damaging event) -- the
     same standard assumption HAZUS-style AAL calculations use for the
     high-frequency tail.

This module only does the real arithmetic on real (return_period_years,
loss_pkr) pairs the caller already obtained from real physics runs --
it does not run anything itself.
"""


def compute_aal(points):
    """points: [{"return_period_years": float, "loss_pkr": float}, ...],
    at least 2 real points, each from an actual physics run's own real
    estimated_building_loss. Returns {"aal_pkr", "points" (sorted, with
    each point's own real exceedance probability attached), "method"}.
    Raises ValueError for fewer than 2 points or any non-positive return
    period -- there's no real integral to compute otherwise."""
    if len(points) < 2:
        raise ValueError("at least 2 real (return period, loss) points are needed to compute a real AAL")
    for p in points:
        if p["return_period_years"] <= 0:
            raise ValueError(f"a real return period must be > 0 years, got {p['return_period_years']}")

    pts = sorted(points, key=lambda p: p["return_period_years"])
    # q ascending in probability == descending in return period == same
    # order as `pts` reversed, since p=1/T.
    q = [1.0 / p["return_period_years"] for p in reversed(pts)]
    L = [p["loss_pkr"] for p in reversed(pts)]

    aal = 0.0
    # Tail below the smallest real observed exceedance probability
    # (rarer than the largest real return period tested): constant at
    # that largest-return-period run's own real loss.
    aal += L[0] * q[0]
    # Trapezoidal integration between each pair of real, adjacent points.
    for i in range(len(q) - 1):
        aal += 0.5 * (L[i] + L[i + 1]) * (q[i + 1] - q[i])
    # Tail above the largest real observed exceedance probability (more
    # frequent than the smallest real return period tested): linear down
    # to zero loss at p=1 (a "1-year return period" is treated as
    # non-damaging, the standard HAZUS-style high-frequency assumption).
    aal += 0.5 * L[-1] * (1.0 - q[-1])

    return {
        "aal_pkr": round(aal, 2),
        "points": [
            {"return_period_years": p["return_period_years"], "loss_pkr": p["loss_pkr"],
             "annual_exceedance_probability": round(1.0 / p["return_period_years"], 6)}
            for p in pts
        ],
        "method": ("Standard trapezoidal Average Annual Loss / Expected Annual Damage integration "
                   "(FEMA HAZUS-style methodology) over each real return period's own real estimated "
                   "building loss -- constant tail beyond the largest real return period tested, "
                   "linear-to-zero tail below the smallest."),
    }
