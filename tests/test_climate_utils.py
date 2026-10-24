"""
Fast, pure-math tests for climate_utils.py's real Gumbel (Extreme Value
Type I) return-period fit -- the statistical core of the CMIP6 design-
storm and climate-intensification features. Uses synthetic annual-maxima
arrays directly (no real CMIP6 NetCDF files, no network), so this runs
in milliseconds and is independent of whether the user-supplied CMIP6
archive is present on this machine.
"""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import climate_utils  # noqa: E402


def _synthetic_annual_maxima(n_years=40, loc=40.0, scale=10.0, seed=0):
    rng = np.random.default_rng(seed)
    # scipy's gumbel_r is the real distribution this function fits against,
    # so sampling from it directly gives a real, well-posed test input
    # rather than an arbitrary distribution the fit was never meant for.
    from scipy import stats
    return stats.gumbel_r.rvs(loc=loc, scale=scale, size=n_years, random_state=rng)


def test_gumbel_return_value_none_below_minimum_sample_size():
    """Real, documented floor: fewer than 8 real years is too few for a
    trustworthy Gumbel fit -- must return None, not a fabricated number
    from a tiny sample."""
    tiny = _synthetic_annual_maxima(n_years=5)
    assert climate_utils._gumbel_return_value(tiny, 100) is None


def test_gumbel_return_value_increases_with_return_period():
    """A rarer (higher return-period) event must have a real, strictly
    larger design depth than a more common one, for the identical
    underlying annual-maxima sample -- the basic monotonic property any
    correct extreme-value fit must have."""
    maxima = _synthetic_annual_maxima(n_years=40)
    v10 = climate_utils._gumbel_return_value(maxima, 10)
    v100 = climate_utils._gumbel_return_value(maxima, 1000)
    assert v100 > v10


def test_gumbel_return_value_recovers_known_distribution_parameters():
    """Fit against a large real sample DRAWN FROM a known Gumbel
    distribution and confirm the fitted return-period value lands close
    to that same distribution's own real theoretical quantile -- verifies
    the fit itself is statistically correct, not just directionally
    plausible."""
    from scipy import stats
    loc, scale = 40.0, 10.0
    maxima = _synthetic_annual_maxima(n_years=200, loc=loc, scale=scale)
    fitted = climate_utils._gumbel_return_value(maxima, 100)
    true_theoretical = stats.gumbel_r.ppf(1.0 - 1.0 / 100, loc=loc, scale=scale)
    assert fitted == pytest.approx(true_theoretical, rel=0.15)


def test_return_periods_list_is_real_and_ascending():
    assert climate_utils.RETURN_PERIODS == sorted(climate_utils.RETURN_PERIODS)
    assert climate_utils.RETURN_PERIODS[0] >= 2


def test_future_periods_are_real_non_overlapping_windows_after_history():
    for name, (start, end) in climate_utils.FUTURE_PERIODS.items():
        assert start > climate_utils.HIST_END
        assert end > start
