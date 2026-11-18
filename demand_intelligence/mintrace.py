"""Real OLS MinTrace and its constrained quadratic program on an explicit S.

OLS uses identity forecast-error weights: it needs no guessed residual covariance.
This function reconciles point candidates only, never confirms or publishes them.
"""
from copy import deepcopy
import math
import time

from .contracts import DemandContractError, decimal_text, digest, instant
from .forecasting import _dependencies


def reconcile_mintrace(base_rows, *, hierarchy, nonnegative=True):
    """Reconcile a single-unit hierarchy of independent node point forecasts.

    hierarchy requires unit, ordered node_ids, ordered bottom_ids, binary
    summing_matrix and level tags. Bottom identities must be the final S block.
    Each row requires hierarchy_id, unit/uom, bucket_start/end, quantity,
    training_cutoff, issue_time, policy_version and source_refs. All nodes must
    have identical complete horizons and a common origin. No unit conversion,
    residual covariance estimation or prediction-interval claim is made.
    """
    import numpy as np
    import pandas as pd
    from hierarchicalforecast.core import HierarchicalReconciliation
    from hierarchicalforecast.methods import MinTrace

    if type(nonnegative) is not bool:
        raise DemandContractError("MINTRACE: explicit boolean nonnegative required")
    nodes, bottoms, unit = hierarchy.get("node_ids"), hierarchy.get("bottom_ids"), hierarchy.get("unit")
    if not isinstance(unit, str) or not unit.strip():
        raise DemandContractError("UNIT: one explicit unit required")
    if (not isinstance(nodes, list) or not isinstance(bottoms, list) or not bottoms or len(nodes) <= len(bottoms)
            or any(not isinstance(x, str) or not x for x in nodes + bottoms)
            or len(nodes) != len(set(nodes)) or len(bottoms) != len(set(bottoms)) or nodes[-len(bottoms):] != bottoms):
        raise DemandContractError("MATRIX: unique ordered nodes with final bottom identity block required")
    try:
        S = np.asarray(hierarchy["summing_matrix"], dtype=float)
    except (KeyError, TypeError, ValueError) as exc:
        raise DemandContractError("MATRIX: explicit numeric summing matrix required") from exc
    if (S.shape != (len(nodes), len(bottoms)) or not np.isfinite(S).all() or not np.isin(S, [0., 1.]).all()
            or not np.array_equal(S[-len(bottoms):], np.eye(len(bottoms))) or np.any(S.sum(axis=1) == 0)):
        raise DemandContractError("MATRIX: binary S and exact bottom identity required")
    tags = hierarchy.get("tags")
    if (not isinstance(tags, dict) or not tags or any(not isinstance(v, list) or not v for v in tags.values())
            or sorted(x for values in tags.values() for x in values) != sorted(nodes)):
        raise DemandContractError("MATRIX: level tags must partition all nodes exactly once")
    if not base_rows:
        raise DemandContractError("HORIZON: nonempty node forecasts required")
    origin_fields = ("training_cutoff", "issue_time", "policy_version")
    if any(len({r.get(k) for r in base_rows}) != 1 or not base_rows[0].get(k) for k in origin_fields):
        raise DemandContractError("ORIGIN: mixed or missing cutoff, issue or policy")
    if instant(base_rows[0]["issue_time"]) < instant(base_rows[0]["training_cutoff"]):
        raise DemandContractError("CUTOFF: issue precedes training cutoff")
    values, sources, periods = {}, [], {}
    for row in base_rows:
        if row.get("unit", row.get("uom")) != unit or row.get("uom", unit) != unit:
            raise DemandContractError("UNIT: cross-unit hierarchy is prohibited")
        node = row.get("hierarchy_id")
        if node not in nodes:
            raise DemandContractError("HORIZON: node outside declared hierarchy")
        refs = row.get("source_refs")
        if not isinstance(refs, list) or not refs or any(not isinstance(ref, str) or not ref.strip() for ref in refs):
            raise DemandContractError("SOURCE: base-node forecast sources required")
        sources.extend(ref for ref in refs if ref not in sources)
        start, end = instant(row.get("bucket_start")), instant(row.get("bucket_end"))
        if not instant(row["training_cutoff"]) <= start < end:
            raise DemandContractError("CUTOFF: forecast bucket must follow cutoff and have positive width")
        try:
            value = float(row["quantity"])
        except (KeyError, ValueError, TypeError, OverflowError) as exc:
            raise DemandContractError("QUANTITY: finite nonnegative base point required") from exc
        if isinstance(row["quantity"], bool) or not math.isfinite(value) or value < 0:
            raise DemandContractError("QUANTITY: finite nonnegative base point required")
        key = (node, start, end)
        if key in values:
            raise DemandContractError("HORIZON: duplicate node bucket")
        values[key] = value
        periods[(start, end)] = (row["bucket_start"], row["bucket_end"])
    ordered_periods = sorted(periods)
    if len(values) != len(nodes) * len(periods):
        raise DemandContractError("HORIZON: every declared node needs every future bucket")
    if any(a[1] > b[0] for a, b in zip(ordered_periods, ordered_periods[1:])):
        raise DemandContractError("HORIZON: overlapping forecast buckets")
    frame = pd.DataFrame([dict(unique_id=node, ds=i, base=values[(node, *period)])
                          for node in nodes for i, period in enumerate(ordered_periods)])
    S_df = pd.DataFrame(S, columns=bottoms); S_df.insert(0, "unique_id", nodes)
    started = time.perf_counter()
    rec = HierarchicalReconciliation(reconcilers=[MinTrace(method="ols", nonnegative=nonnegative, num_threads=1)])
    result = rec.reconcile(Y_hat_df=frame, S_df=S_df, tags={k: np.asarray(v) for k, v in tags.items()})
    columns = [c for c in result if c.startswith("base/MinTrace")]
    if len(columns) != 1:
        raise DemandContractError("MINTRACE_OUTPUT: expected one library reconciled mean column")
    output_values = {(row["unique_id"], int(row["ds"])): float(row[columns[0]]) for _, row in result.iterrows()}
    rows, max_error, max_base_error, max_adjustment = [], 0., 0., 0.
    for i, period in enumerate(ordered_periods):
        bottom_values = np.asarray([output_values[(node, i)] for node in bottoms])
        old_bottom = np.asarray([values[(node, *period)] for node in bottoms])
        expected, old_expected = S @ bottom_values, S @ old_bottom
        for n, node in enumerate(nodes):
            value, original = output_values[(node, i)], values[(node, *period)]
            if not math.isfinite(value) or (nonnegative and value < 0):
                raise DemandContractError("MINTRACE_OUTPUT: nonfinite or negative constrained mean")
            max_error = max(max_error, abs(value - expected[n]))
            max_base_error = max(max_base_error, abs(original - old_expected[n]))
            max_adjustment = max(max_adjustment, abs(value - original))
            start_text, end_text = periods[period]
            rows.append(dict(hierarchy_id=node, unit=unit, uom=unit, quantity=decimal_text(value),
                             base_quantity=decimal_text(original), bucket_start=start_text, bucket_end=end_text,
                             is_bottom=node in bottoms, model="MinTrace_OLS_NONNEGATIVE" if nonnegative else "MinTrace_OLS",
                             source_refs=sources, **{k: base_rows[0][k] for k in origin_fields}))
    if max_error > 1e-6:
        raise DemandContractError("MINTRACE_OUTPUT: hierarchy failed coherence tolerance")
    return {"rows": rows, "receipt": {"method": "HierarchicalForecast.MinTrace", "weight_method": "ols",
        "nonnegative": nonnegative, "solver": "clarabel" if nonnegative else "linear_projection",
        "dependencies": _dependencies(), "hierarchy": deepcopy(hierarchy), "hierarchy_digest": digest(hierarchy),
        "base_forecast_digest": digest(base_rows), "output_digest": digest(rows),
        "max_coherence_error": max_error, "max_base_coherence_error": max_base_error,
        "max_adjustment": max_adjustment, "total_seconds": time.perf_counter() - started,
        "scope": "POINT_RECONCILIATION_RESEARCH_ONLY_NO_ACCURACY_OR_NATIVE_FEASIBILITY_CLAIM",
        "weight_assumption": "IDENTITY_FORECAST_ERROR_WEIGHTS_NO_ESTIMATED_COVARIANCE",
        "interval_status": "NOT_ESTIMATED"}}
