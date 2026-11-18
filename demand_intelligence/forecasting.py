"""Optional Nixtla adapters with as-of selection, rolling evaluation and receipts.

Dependencies import only at execution time. Existing Worker need not install them.
No fitted state, model choice, policy confirmation or Canonical facts are mutated.
"""
from collections import defaultdict
from datetime import datetime
from importlib.metadata import version
import time

from .contracts import (DemandContractError, decimal_text, digest, dimension_key, instant,
                        normalize_history, select_training_history)
from .metrics import forecast_metrics


MODELS = ("Naive", "SeasonalNaive", "AutoETS", "CrostonSBA", "TSB")
FREQUENCIES = {"D", "W-MON", "MS"}


def _dependencies():
    return {name: version(name) for name in ("statsforecast", "hierarchicalforecast", "numpy", "pandas")}


def _grid(group, freq, training_cutoff):
    import pandas as pd

    zone = datetime.fromisoformat(group[0]["bucket_start"].replace("Z", "+00:00")).tzinfo
    first = pd.Timestamp(instant(group[0]["bucket_start"]).astimezone(zone).replace(tzinfo=None))
    grid = pd.date_range(first, periods=len(group) + 1, freq=freq)
    if grid[0] != first:
        raise DemandContractError("GAP: first bucket not on the specified calendar grid")
    for i, row in enumerate(group):
        if (grid[i].to_pydatetime().replace(tzinfo=zone) != instant(row["bucket_start"]) or
                grid[i + 1].to_pydatetime().replace(tzinfo=zone) != instant(row["bucket_end"])):
            raise DemandContractError("GAP: missing/irregular historical bucket; no implicit zero filling")
    if instant(group[-1]["bucket_end"]) != instant(training_cutoff):
        raise DemandContractError("CUTOFF: stale or incomplete last bucket at origin")
    return zone, grid


def _model(name, season_length):
    from statsforecast.models import AutoETS, CrostonSBA, Naive, SeasonalNaive, TSB

    if name == "Naive":
        return Naive()
    if name == "SeasonalNaive":
        return SeasonalNaive(season_length=season_length)
    if name == "AutoETS":
        return AutoETS(season_length=season_length, model="ZZZ")
    if name == "CrostonSBA":
        return CrostonSBA()
    if name == "TSB":
        return TSB(alpha_d=0.2, alpha_p=0.2)
    raise DemandContractError("MODEL: unsupported model")


def forecast_history(rows, *, model, training_cutoff, issue_time, horizon,
                     freq="W-MON", season_length=4, policy_version,
                     censored_policy="reject", interval_level=80, observation_kind="OBSERVED_DEMAND"):
    """Issue one real model over independent item/location/customer/unit series.

    Intervals use three within-training conformal windows, never test outcomes.
    The lower/upper limits and point mean are clipped to nonnegative demand.
    All-zero training uses an explicitly named deterministic guard, not a fake
    execution of a statistical method. Short histories are explicitly skipped.
    """
    if model not in MODELS:
        raise DemandContractError("MODEL: unsupported model")
    if type(horizon) is not int or not 1 <= horizon <= 104 or type(season_length) is not int or season_length < 1:
        raise DemandContractError("HORIZON: positive bounded horizon and season_length required")
    if freq not in FREQUENCIES:
        raise DemandContractError("FREQUENCY: supported explicit calendars D, W-MON, MS")
    if not isinstance(policy_version, str) or not policy_version.strip():
        raise DemandContractError("VERSION: policy_version required")
    if type(interval_level) is not int or not 0 < interval_level < 100:
        raise DemandContractError("INTERVAL: integer level in (0,100) required")
    if instant(issue_time, "issue_time") < instant(training_cutoff):
        raise DemandContractError("ISSUE: issue_time cannot precede training_cutoff")
    selected = select_training_history(rows, training_cutoff=training_cutoff, observation_kind=observation_kind, censored_policy=censored_policy)
    groups = defaultdict(list)
    for row in selected["rows"]:
        groups[dimension_key(row)].append(row)
    import numpy as np
    import pandas as pd
    from statsforecast import StatsForecast
    from statsforecast.utils import ConformalIntervals

    output, skipped, timings, executed = [], [], [], set()
    started = time.perf_counter()
    for key, group in sorted(groups.items()):
        zone, grid = _grid(group, freq, training_cutoff)
        future_grid = pd.date_range(grid[-1], periods=horizon + 1, freq=freq)
        y = np.asarray([float(r["quantity"]) for r in group], dtype=np.float64)
        minimum = max(2 * season_length if model in {"SeasonalNaive", "AutoETS"} else 3, 3) + 3 * horizon
        if len(group) < minimum:
            skipped.append({"dimensions": dict(zip(("item_id", "location_id", "customer_id", "unit"), key)),
                            "requested_model": model, "reason": "INSUFFICIENT_HISTORY_FOR_TRAIN_ONLY_CALIBRATION",
                            "history_length": len(group), "minimum_length": minimum})
            continue
        start = time.perf_counter()
        actual_model = model if np.any(y) else "ZERO_HISTORY_GUARD"
        if actual_model == "ZERO_HISTORY_GUARD":
            means = lows = highs = np.zeros(horizon)
        else:
            sf = StatsForecast(models=[_model(model, season_length)], freq=freq, n_jobs=1)
            frame = pd.DataFrame({"unique_id": ["series"] * len(y), "ds": grid[:-1], "y": y})
            result = sf.forecast(df=frame, h=horizon, level=[interval_level],
                                 prediction_intervals=ConformalIntervals(n_windows=3, h=horizon))
            means = result[model].to_numpy()
            lows = result[f"{model}-lo-{interval_level}"].to_numpy()
            highs = result[f"{model}-hi-{interval_level}"].to_numpy()
            if not all(np.isfinite(x).all() for x in (means, lows, highs)):
                raise DemandContractError("MODEL_OUTPUT: nonfinite forecast or interval")
            executed.add(model)
        elapsed = time.perf_counter() - start
        timings.append({"series_id": digest(key), "model": actual_model, "seconds": elapsed,
                        "training_length": len(group), "zero_fraction": float(np.mean(y == 0)),
                        "intermittent_candidate": bool(np.mean(y == 0) >= 0.5)})
        for i in range(horizon):
            source_refs = [r["source_ref"] for r in group]
            out = dict(zip(("item_id", "location_id", "customer_id", "unit"), key))
            out.update(uom=key[-1], bucket_start=future_grid[i].to_pydatetime().replace(tzinfo=zone).isoformat(),
                       bucket_end=future_grid[i + 1].to_pydatetime().replace(tzinfo=zone).isoformat(),
                       quantity=decimal_text(max(0., float(means[i]))),
                       lower=decimal_text(max(0., float(lows[i]))), upper=decimal_text(max(0., float(highs[i]))),
                       interval_level=interval_level, interval_method="TRAIN_ONLY_CONFORMAL_3_WINDOWS",
                       forecast_origin="STATISTICAL_BASELINE", model=actual_model, requested_model=model,
                       training_cutoff=training_cutoff, issue_time=issue_time, policy_version=policy_version,
                       source_refs=source_refs, training_digest=digest(group),
                       unconstrained_quantity=decimal_text(float(means[i])))
            out["id"] = "DI-" + digest({k: out[k] for k in (
                "item_id", "location_id", "customer_id", "unit", "bucket_start", "model", "training_digest", "policy_version")})[:24]
            output.append(out)
    receipt = dict(selected["receipt"], requested_model=model, executed_models=sorted(executed),
                   issue_time=issue_time, horizon=horizon, freq=freq, season_length=season_length,
                   policy_version=policy_version, interval_level=interval_level,
                   calibration="THREE_ROLLING_WINDOWS_INSIDE_TRAINING", dependencies=_dependencies(),
                   model_parameters={"TSB": {"alpha_d": 0.2, "alpha_p": 0.2}, "AutoETS": {"model": "ZZZ"}},
                   total_seconds=time.perf_counter() - started, series_timings=timings, skipped=skipped,
                   forecast_digest=digest(output), forecast_count=len(output),
                   evidence_limit="NO_EMPIRICAL_ENTERPRISE_ACCURACY_OR_STOCKOUT_RECONSTRUCTION_CLAIM")
    return {"rows": output, "excluded": selected["excluded"], "receipt": receipt}


def rolling_backtest(rows, *, cutoffs, horizon, freq, season_length, models, policy_version,
                     evaluation_as_of, censored_policy="exclude_series", interval_level=80):
    """Freeze each origin before scoring later source observations; aggregate by unit.

    Equal-length nonoverlapping windows are recommended for the comparison.
    Overlapping test buckets, if requested, remain labelled origin/horizon trials.
    Late, absent and censored actuals are unscored, never imputed to zero.
    """
    if not cutoffs or not models or len(set(cutoffs)) != len(cutoffs) or len(set(models)) != len(models):
        raise DemandContractError("BACKTEST: unique nonempty origins and models required")
    if any(instant(c) >= instant(evaluation_as_of) for c in cutoffs):
        raise DemandContractError("BACKTEST: evaluation_as_of must follow every origin")
    actuals = {}
    for row in normalize_history(rows):
        if row["observation_kind"] == "OBSERVED_DEMAND":
            key = (dimension_key(row), instant(row["bucket_start"]), instant(row["bucket_end"]))
            if key in actuals:
                raise DemandContractError("DUPLICATE: conflicting actual-demand observation")
            actuals[key] = row
    forecasts, scores, windows, unevaluated, pooled = [], [], [], 0, defaultdict(list)
    started = time.perf_counter()
    for cutoff in sorted(cutoffs, key=instant):
        training = select_training_history(rows, training_cutoff=cutoff, censored_policy=censored_policy)
        grouped_training = defaultdict(list)
        for r in training["rows"]:
            grouped_training[dimension_key(r)].append(float(r["quantity"]))
        for model in models:
            result = forecast_history(rows, model=model, training_cutoff=cutoff, issue_time=cutoff,
                                      horizon=horizon, freq=freq, season_length=season_length,
                                      policy_version=policy_version, censored_policy=censored_policy,
                                      interval_level=interval_level)
            windows.append(result["receipt"])
            by_series = defaultdict(list)
            for row in result["rows"]:
                r = dict(row)
                actual = actuals.get((dimension_key(r), instant(r["bucket_start"]), instant(r["bucket_end"])))
                if actual is None:
                    r.update(actual_quantity=None, score_status="MISSING_ACTUAL")
                elif actual["censored"]:
                    r.update(actual_quantity=None, score_status="CENSORED_ACTUAL")
                elif instant(actual["observed_at"]) > instant(evaluation_as_of):
                    r.update(actual_quantity=None, score_status="ACTUAL_NOT_OBSERVED_AS_OF_EVALUATION")
                else:
                    r.update(actual_quantity=actual["quantity"], actual_source_ref=actual["source_ref"], score_status="SCORED")
                    by_series[dimension_key(r)].append(r)
                if r["actual_quantity"] is None:
                    unevaluated += 1
                forecasts.append(r)
            for key, group in by_series.items():
                metric = forecast_metrics([r["actual_quantity"] for r in group], [r["quantity"] for r in group],
                                          training=grouped_training[key], season_length=season_length,
                                          lower=[r["lower"] for r in group], upper=[r["upper"] for r in group],
                                          interval_level=interval_level)
                score = dict(zip(("item_id", "location_id", "customer_id", "unit"), key))
                score.update(metric, model=model, cutoff=cutoff, training_digest=result["receipt"]["training_digest"],
                             evaluated_count=len(group), requested_count=horizon)
                scores.append(score)
                pooled[(key[-1], model)].append(score)
    aggregate = defaultdict(dict)
    for (unit, model), group in pooled.items():
        count = sum(x["n"] for x in group)
        demand = sum(x["actual_quantity"] for x in group)
        absolute = sum(x["absolute_error"] for x in group)
        bias = sum(x["bias_quantity"] for x in group)
        defined_mase = [x for x in group if x["mase"] is not None]
        aggregate[unit][model] = dict(n=count, trials=len(group), actual_quantity=demand,
            absolute_error=absolute, wape=absolute / demand if demand else None,
            mae=absolute / count, bias_quantity=bias, bias_ratio=bias / demand if demand else None,
            mean_mase=sum(x["mase"] * x["n"] for x in defined_mase) / sum(x["n"] for x in defined_mase) if defined_mase else None,
            mase_defined_trials=len(defined_mase), mase_undefined_trials=len(group) - len(defined_mase),
            coverage=sum(x["coverage"] * x["n"] for x in group) / count,
            mean_interval_width=sum(x["mean_interval_width"] * x["n"] for x in group) / count,
            interval_score=sum(x["interval_score"] * x["n"] for x in group) / count,
            zero_actual_count=sum(x["zero_actual_count"] for x in group))
    return {"forecasts": forecasts, "scores": scores, "aggregate_by_unit_model": dict(aggregate), "receipt": {
        "cutoffs": cutoffs, "horizon": horizon, "models": models, "freq": freq, "season_length": season_length,
        "policy_version": policy_version, "evaluation_as_of": evaluation_as_of, "windows": windows,
        "unevaluated_actual_count": unevaluated, "score_digest": digest(scores),
        "total_seconds": time.perf_counter() - started, "input_digest": digest(rows),
        "unit_pooling": "PROHIBITED", "bias_definition": "FORECAST_MINUS_ACTUAL",
        "mase_aggregation": "OBSERVATION_WEIGHTED_MEAN_OF_DEFINED_SERIES_ORIGIN_MASE",
        "interval_interpretation": "EMPIRICAL_COVERAGE_ONLY_NO_UNCONDITIONAL_TIME_SERIES_GUARANTEE"}}


def reconcile_bottom_up(forecast_rows, *, group_by):
    """Actually execute HierarchicalForecast.BottomUp on explicit separate-unit trees.

    Tree: unit total -> each successive group_by prefix -> bottom series. Only
    point forecasts are reconciled. Summing marginal limits is not a joint PI.
    Output aggregates are analytics, not additional native planning demands.
    """
    if not group_by or len(set(group_by)) != len(group_by) or set(group_by) != {"item_id", "location_id", "customer_id"}:
        raise DemandContractError("HIERARCHY: explicit permutation of all three business dimensions required")
    if not forecast_rows:
        raise DemandContractError("HIERARCHY: nonempty forecast rows required")
    fields = ("training_cutoff", "issue_time", "policy_version", "requested_model")
    if any(len({r.get(f) for r in forecast_rows}) != 1 for f in fields):
        raise DemandContractError("ORIGIN_VERSION: mixed origins, models or policy versions")
    import numpy as np
    import pandas as pd
    from hierarchicalforecast.core import HierarchicalReconciliation
    from hierarchicalforecast.methods import BottomUp

    per_unit = defaultdict(list)
    for r in forecast_rows:
        per_unit[r["unit"]].append(r)
    output, matrices, max_error = [], {}, 0.
    started = time.perf_counter()
    for unit, rows in per_unit.items():
        bottom = sorted({tuple(r[d] for d in group_by) for r in rows})
        dates = sorted({(r["bucket_start"], r["bucket_end"]) for r in rows}, key=lambda x: instant(x[0]))
        values = {}
        for r in rows:
            key = (tuple(r[d] for d in group_by), r["bucket_start"], r["bucket_end"])
            if key in values:
                raise DemandContractError("HORIZON: duplicate bottom bucket")
            values[key] = float(r["quantity"])
        if len(values) != len(bottom) * len(dates):
            raise DemandContractError("HORIZON: all bottom series require identical complete future grids")
        nodes = [()] + [prefix for n in range(1, len(group_by) + 1) for prefix in sorted({x[:n] for x in bottom})]
        node_ids = ["TOTAL" if not x else "node/" + digest(x)[:20] for x in nodes]
        smat = np.array([[float(b[:len(node)] == node) for b in bottom] for node in nodes])
        bottom_ids = ["node/" + digest(b)[:20] for b in bottom]
        S_df = pd.DataFrame(smat, columns=bottom_ids)
        S_df.insert(0, "unique_id", node_ids)
        tags = {"TOTAL" if not n else "/".join(group_by[:n]): np.array(
            [node_ids[i] for i, node in enumerate(nodes) if len(node) == n]) for n in range(len(group_by) + 1)}
        base = []
        for i, node in enumerate(nodes):
            for j, (start, end) in enumerate(dates):
                # Parent values deliberately have no anchoring information. BottomUp
                # must replace them using the explicit summing matrix and leaf forecasts.
                val = values[(node, start, end)] if len(node) == len(group_by) else 0.
                base.append({"unique_id": node_ids[i], "ds": j, "baseline": val})
        reconciler = HierarchicalReconciliation(reconcilers=[BottomUp()])
        rec = reconciler.reconcile(Y_hat_df=pd.DataFrame(base), S_df=S_df, tags=tags)
        reconciled_col = "baseline/BottomUp"
        rec_values = {(r["unique_id"], int(r["ds"])): float(r[reconciled_col]) for _, r in rec.iterrows()}
        for i, node in enumerate(nodes):
            for j, (start, end) in enumerate(dates):
                value = rec_values[(node_ids[i], j)]
                expected = sum(values[(b, start, end)] for b in bottom if b[:len(node)] == node)
                max_error = max(max_error, abs(value - expected))
                out = dict(unit=unit, uom=unit, hierarchy_id=node_ids[i],
                           hierarchy_level="TOTAL" if not node else "/".join(group_by[:len(node)]),
                           bucket_start=start, bucket_end=end, quantity=decimal_text(value),
                           model="BottomUp", interval_status="NOT_RECONCILED_NO_JOINT_INTERVAL_CLAIM",
                           dimensions=dict(zip(group_by, node)),
                           **{f: forecast_rows[0][f] for f in fields})
                output.append(out)
        matrices[unit] = {"node_ids": node_ids, "bottom_ids": bottom_ids, "matrix": smat.tolist(),
                          "bottom_count": len(bottom), "node_count": len(nodes),
                          "group_by": group_by, "matrix_digest": digest(smat.tolist())}
    return {"rows": output, "receipt": {"method": "HierarchicalForecast.BottomUp", "dependencies": _dependencies(),
        "summing_matrices": matrices, "max_coherence_error": max_error, "input_digest": digest(forecast_rows),
        "output_digest": digest(output), "total_seconds": time.perf_counter() - started,
        "planning_scope": "ONLY_BOTTOM_ROWS_MAY_ENTER_NATIVE_DEMAND", "unit_pooling": "PROHIBITED"}}
