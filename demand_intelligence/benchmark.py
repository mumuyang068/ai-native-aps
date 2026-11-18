"""Reproducible task-owned research runner. Freeze and run are separate commands.

Only the explicitly allowed, source-only compressed JSON fixture is read. No
legacy runtime XML, database, WAL, SHM, services or Worker environment is used.
"""
import argparse
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import json
from pathlib import Path
import time

from .contracts import DemandContractError, digest
from .forecasting import MODELS, forecast_history, reconcile_bottom_up, rolling_backtest
from .synthetic import generate_research_history, freeze_research_inputs, verify_research_freeze


def _save(path, data):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")


def freeze_benchmark(*, source, task_root):
    path, root = Path(source).resolve(), Path(task_root).resolve()
    if path.name != "full-baseline-native.json.gz" or path.parent.name != "optimization" or path.parent.parent.name != "fixtures":
        raise DemandContractError("SOURCE: only the approved source-only fixture is permitted")
    content = path.read_bytes()
    source_data = json.loads(gzip.decompress(content).decode("utf-8"))["data"]
    history = generate_research_history(source_data, seed=20261025, periods=156,
        source_snapshot_sha256=hashlib.sha256(content).hexdigest(), include_material_series=True)
    first = datetime.fromisoformat("2023-10-23T00:00:00+08:00")
    plan = dict(task="APS-PRO-EXPAND-06", protocol_version="EXP06-FORECAST-BENCHMARK-01", seed=20261025,
        source_path=str(path), source_sha256=hashlib.sha256(content).hexdigest(),
        models=list(MODELS), cutoffs=[(first + timedelta(weeks=i)).isoformat() for i in (108, 120, 132, 144)],
        horizon=6, freq="W-MON", season_length=13, interval_level=80, censored_policy="exclude_series",
        evaluation_as_of="2026-10-25T00:00:00+08:00", policy_version="EXP06-FORECAST-RESEARCH-PV1",
        candidate_model="SeasonalNaive", candidate_model_selection="PREDECLARED_SIMPLE_SEASONAL_BASELINE_NOT_TEST_WINNER",
        candidate_cutoff="2026-10-19T00:00:00+08:00", candidate_issue_time="2026-10-25T00:00:00+08:00", candidate_horizon=10,
        candidate_version_id="EXP06-FORECAST-STAT-01",
        unit_pooling="PROHIBITED", all_zero_policy="EXPLICIT_ZERO_HISTORY_GUARD",
        missing_history_policy="FAIL_INSTEAD_OF_ZERO_FILL", censored_series_policy="EXCLUDE_WHOLE_SERIES",
        interval_method="3_WITHIN_TRAINING_CONFORMAL_WINDOWS; NONNEGATIVE_CLIPPING",
        model_tuning="NONE; TSB alpha_d=alpha_p=0.2; AutoETS ZZZ fixed",
        evidence_class="DERIVED_SYNTHETIC_DIMENSIONS_ONLY; NO_ENTERPRISE_ACCURACY_CLAIM",
        environment_lock_sha256=hashlib.sha256((root / "frozen" / "forecast-environment-lock.txt").read_bytes()).hexdigest())
    return freeze_research_inputs(history, output_dir=root / "frozen", benchmark_plan=plan)


def run_frozen_benchmark(*, task_root):
    root = Path(task_root).resolve()
    freeze = verify_research_freeze(root / "frozen")
    plan = json.loads((root / "frozen" / "forecast-benchmark-plan.json").read_text(encoding="utf-8"))
    data = json.loads((root / "frozen" / "forecast-history.json").read_text(encoding="utf-8"))
    if hashlib.sha256((root / "frozen" / "forecast-environment-lock.txt").read_bytes()).hexdigest() != plan["environment_lock_sha256"]:
        raise DemandContractError("HASH: environment lock differs from frozen protocol")
    for name in ("forecast-backtest.json", "forecast-candidate-version.json", "forecast-hierarchy.json", "forecast-benchmark-summary.json"):
        if (root / "evidence" / name).exists():
            raise DemandContractError("FROZEN_RESULT: benchmark output already exists; do not overwrite history")
    started_at, started = datetime.now(timezone.utc).isoformat(), time.perf_counter()
    if datetime.fromisoformat(freeze["frozen_at"]) >= datetime.fromisoformat(started_at):
        raise DemandContractError("FREEZE: input freeze must precede benchmark start")
    backtest = rolling_backtest(data["rows"], **{k: plan[k] for k in (
        "cutoffs", "horizon", "freq", "season_length", "models", "policy_version", "evaluation_as_of", "censored_policy", "interval_level")})
    future = forecast_history(data["rows"], model=plan["candidate_model"], training_cutoff=plan["candidate_cutoff"],
                              issue_time=plan["candidate_issue_time"], horizon=plan["candidate_horizon"],
                              **{k: plan[k] for k in ("freq", "season_length", "policy_version", "censored_policy", "interval_level")})
    future_rows = []
    for row in future["rows"]:
        future_rows.append(dict(row, version_id=plan["candidate_version_id"], gross_quantity=row["quantity"],
            evidence_status="DERIVED_SYNTHETIC", history_source_class="SYNTHETIC_NOT_ORIGINAL_419_FACTS",
            source_refs=row["source_refs"] + ["forecast-freeze/" + freeze["history_sha256"]],
            dimension_source_sha256=plan["source_sha256"]))
    candidate = dict(version_id=plan["candidate_version_id"], policy_version=plan["policy_version"],
        baseline_selection=plan["candidate_model_selection"], status="RESEARCH_CANDIDATE", rows=future_rows,
        receipt=future["receipt"], input_manifest=freeze, backtest_score_digest=backtest["receipt"]["score_digest"],
        history_receipt=data["receipt"], original_gross_changed=False,
        native_projection_rule="SELECT_ONLY_COMPLETE_BUCKETS_IN_NATIVE_HORIZON_WITH_EXPLICIT_NET_DUE_AT")
    hierarchy = reconcile_bottom_up(future_rows, group_by=["location_id", "item_id", "customer_id"])
    summaries = {}
    regimes = {tuple(x["dimensions"][d] for d in ("item_id", "location_id", "customer_id", "unit")): x["mechanism"]
               for x in data["receipt"]["regimes"]}
    for score in backtest["scores"]:
        score["synthetic_mechanism"] = regimes[tuple(score[d] for d in ("item_id", "location_id", "customer_id", "unit"))]
    # Recompute score digest after adding transparent mechanism labels.
    backtest["receipt"]["labelled_score_digest"] = digest(backtest["scores"])
    for unit, methods in backtest["aggregate_by_unit_model"].items():
        seasonal = methods.get("SeasonalNaive", {}).get("wape")
        summaries[unit] = {"models": methods, "seasonal_baseline_wape": seasonal,
            "models_beating_seasonal_wape": [m for m, x in methods.items() if seasonal is not None and x["wape"] is not None and x["wape"] < seasonal],
            "candidate_model": plan["candidate_model"], "candidate_selection_preceded_backtest": True}
    summary = dict(started_at=started_at, finished_at=datetime.now(timezone.utc).isoformat(),
        total_seconds=time.perf_counter() - started, input_manifest=freeze,
        history_rows=len(data["rows"]), dimension_series=len(data["receipt"]["regimes"]),
        training_only_censor_exclusions=len(future["excluded"]), candidate_forecast_rows=len(future_rows),
        origin_count=len(plan["cutoffs"]), model_count=len(plan["models"]), scored_trials=len(backtest["scores"]),
        scored_forecasts=sum(x["n"] for x in backtest["scores"]), units=summaries,
        hierarchy_method=hierarchy["receipt"]["method"], hierarchy_max_coherence_error=hierarchy["receipt"]["max_coherence_error"],
        evidence_limit="SYNTHETIC_MECHANISM_EXPERIMENT; NOT_ENTERPRISE_HISTORY_OR_PRODUCTION_ACCURACY",
        limitations=["No unknown stockout demand reconstruction", "No joint probabilistic hierarchy intervals",
                    "No learned model selection from test outcomes", "No unit pooling or implied conversion",
                    "All-zero guards are not counted as upstream model executions",
                    "Algorithms do not confirm business forecast/netting policy or run native planning"])
    for name, payload in (("forecast-backtest.json", backtest), ("forecast-candidate-version.json", candidate),
                          ("forecast-hierarchy.json", hierarchy), ("forecast-benchmark-summary.json", summary)):
        _save(root / "evidence" / name, payload)
    verify_research_freeze(root / "frozen")
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-root", required=True)
    parser.add_argument("--source")
    parser.add_argument("--freeze-only", action="store_true")
    args = parser.parse_args()
    if args.freeze_only:
        if not args.source:
            parser.error("--freeze-only requires the approved source fixture")
        result = freeze_benchmark(source=args.source, task_root=args.task_root)
    else:
        result = run_frozen_benchmark(task_root=args.task_root)
    print(json.dumps(result, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
