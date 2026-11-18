"""Zero-safe, training-scaled forecasting metrics; no unit aggregation here."""
import math

from .contracts import DemandContractError


def forecast_metrics(actual, predicted, *, training, season_length=1,
                     lower=None, upper=None, interval_level=None):
    a, p, t = [list(map(float, x)) for x in (actual, predicted, training)]
    if not a or len(a) != len(p) or any(not math.isfinite(x) for x in a + p + t):
        raise DemandContractError("METRIC: equal, nonempty finite actual/predicted vectors required")
    if any(x < 0 for x in a + t) or type(season_length) is not int or season_length < 1:
        raise DemandContractError("METRIC: nonnegative demand and positive seasonal scale required")
    absolute_error = sum(abs(y - f) for y, f in zip(a, p))
    demand = sum(abs(y) for y in a)
    scale_errors = [abs(t[i] - t[i - season_length]) for i in range(season_length, len(t))]
    scale = sum(scale_errors) / len(scale_errors) if scale_errors else None
    bias = sum(f - y for y, f in zip(a, p))
    nz = [abs(y - f) for y, f in zip(a, p) if y > 0]
    result = {"n": len(a), "absolute_error": absolute_error, "actual_quantity": demand,
              "mae": absolute_error / len(a), "wape": absolute_error / demand if demand else None,
              "mase": absolute_error / len(a) / scale if scale else None,
              "mase_scale": scale, "bias_quantity": bias,
              "bias_ratio": bias / demand if demand else None,
              "zero_actual_count": sum(y == 0 for y in a),
              "nonzero_mae": sum(nz) / len(nz) if nz else None,
              "coverage": None, "mean_interval_width": None, "interval_score": None,
              "interval_level": interval_level,
              "undefined_reasons": (["ZERO_ACTUAL_SUM"] if not demand else []) +
                                   (["ZERO_OR_MISSING_TRAINING_SCALE"] if not scale else [])}
    if lower is not None or upper is not None:
        if lower is None or upper is None or not interval_level or not 0 < interval_level < 100:
            raise DemandContractError("INTERVAL: lower, upper and valid level required together")
        lo, hi = list(map(float, lower)), list(map(float, upper))
        if len(lo) != len(a) or len(hi) != len(a) or any(
                not math.isfinite(l) or not math.isfinite(h) or l > h for l, h in zip(lo, hi)):
            raise DemandContractError("INTERVAL: invalid or unaligned bounds")
        alpha = 1 - interval_level / 100
        result.update(coverage=sum(l <= y <= h for y, l, h in zip(a, lo, hi)) / len(a),
                      mean_interval_width=sum(h - l for l, h in zip(lo, hi)) / len(a),
                      interval_score=sum(h - l + (2 / alpha) * max(l - y, 0) +
                                         (2 / alpha) * max(y - h, 0)
                                         for y, l, h in zip(a, lo, hi)) / len(a))
    return result
