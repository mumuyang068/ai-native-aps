"""Small, explicit historical-observation contract; no Canonical owner or persistence."""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json


class DemandContractError(ValueError):
    """A source, time, dimension or policy is insufficient for this operation."""


DIMENSIONS = ("item_id", "location_id", "customer_id", "unit")
OBSERVATION_KINDS = {"OBSERVED_DEMAND", "HISTORICAL_ORDER", "SHIPMENT"}


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def instant(value, field="time"):
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError, AttributeError) as exc:
        raise DemandContractError(f"TIMEZONE: {field} must be an ISO timezone-aware timestamp") from exc
    if result.tzinfo is None or result.utcoffset() is None:
        raise DemandContractError(f"TIMEZONE: {field} requires an offset")
    return result.astimezone(timezone.utc)


def quantity(value, field="quantity"):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise DemandContractError(f"QUANTITY: invalid {field}") from exc
    if isinstance(value, bool) or not result.is_finite() or result < 0:
        raise DemandContractError(f"QUANTITY: {field} must be finite and nonnegative")
    return result


def decimal_text(value):
    value = Decimal(str(value))
    return "0" if value == 0 else format(value.normalize(), "f")


def normalize_dimensions(row):
    result = deepcopy(row)
    unit, uom = result.get("unit"), result.get("uom")
    if unit is not None and uom is not None and unit != uom:
        raise DemandContractError("UNIT: conflicting unit/uom")
    result["unit"] = result["uom"] = unit if unit is not None else uom
    for field in DIMENSIONS:
        if not isinstance(result.get(field), str) or not result[field].strip():
            raise DemandContractError(f"DIMENSION: {field} is required")
    return result


def dimension_key(row):
    return tuple(row[k] for k in DIMENSIONS)


def source_ref(row):
    result = row.get("source_ref")
    if not isinstance(result, str) or not result.strip():
        raise DemandContractError("SOURCE: nonempty source_ref required")
    return result


def normalize_history(rows):
    normalized, refs, ids = [], set(), set()
    for row in rows:
        r = normalize_dimensions(row)
        ref = source_ref(r)
        row_id = r.get("id", ref)
        if ref in refs or row_id in ids:
            raise DemandContractError("DUPLICATE: historical source or id")
        refs.add(ref)
        ids.add(row_id)
        r["id"] = row_id
        r["quantity"] = decimal_text(quantity(r.get("quantity")))
        if type(r.get("censored")) is not bool:
            raise DemandContractError("CENSORED: explicit boolean required; unknown is not false")
        if r.get("observation_kind") not in OBSERVATION_KINDS:
            raise DemandContractError("OBSERVATION_KIND: choose observed demand, historical order or shipment")
        if instant(r.get("bucket_start"), "bucket_start") >= instant(r.get("bucket_end"), "bucket_end"):
            raise DemandContractError("BUCKET: end must follow start")
        if instant(r.get("observed_at"), "observed_at") < instant(r["bucket_end"]):
            raise DemandContractError("OBSERVATION: closed bucket cannot be observed before bucket_end")
        normalized.append(r)
    return normalized


def reject_overlaps(rows):
    groups = defaultdict(list)
    for row in rows:
        groups[dimension_key(row)].append(row)
    for group in groups.values():
        ordered = sorted(group, key=lambda r: instant(r["bucket_start"]))
        for a, b in zip(ordered, ordered[1:]):
            if instant(a["bucket_end"]) > instant(b["bucket_start"]):
                raise DemandContractError("OVERLAP: duplicate or overlapping buckets in one series")


def select_training_history(rows, *, training_cutoff, observation_kind="OBSERVED_DEMAND", censored_policy="reject"):
    """Select only observations known by cutoff. Exclude whole censored series or reject.

    Quantities from order history and shipment history are alternative observation
    channels, never additive evidence. A missing/censored bucket is never filled by 0.
    """
    cutoff = instant(training_cutoff, "training_cutoff")
    if observation_kind not in OBSERVATION_KINDS:
        raise DemandContractError("OBSERVATION_KIND: unknown requested channel")
    if censored_policy not in {"reject", "exclude_series"}:
        raise DemandContractError("CENSORED_POLICY: choose reject or exclude_series")
    selected, excluded = [], []
    for row in normalize_history(rows):
        reason = None
        if row["observation_kind"] != observation_kind:
            reason = "OTHER_OBSERVATION_KIND"
        elif instant(row["bucket_end"]) > cutoff:
            reason = "BUCKET_AFTER_CUTOFF"
        elif instant(row["observed_at"]) > cutoff:
            reason = "NOT_OBSERVED_AT_CUTOFF"
        if reason:
            excluded.append({"id": row["id"], "source_ref": row["source_ref"], "reason": reason})
        else:
            selected.append(row)
    reject_overlaps(selected)
    censored_keys = {dimension_key(r) for r in selected if r["censored"]}
    if censored_keys and censored_policy == "reject":
        raise DemandContractError("CENSORED: cannot treat stockout-constrained observation as actual demand")
    accepted = []
    for row in selected:
        if dimension_key(row) in censored_keys:
            excluded.append({"id": row["id"], "source_ref": row["source_ref"], "reason": "CENSORED_SERIES"})
        else:
            accepted.append(row)
    accepted.sort(key=lambda r: (dimension_key(r), instant(r["bucket_start"]), r["id"]))
    return {"rows": accepted, "excluded": excluded, "receipt": {
        "training_cutoff": training_cutoff, "observation_kind": observation_kind,
        "censored_policy": censored_policy, "training_row_count": len(accepted),
        "training_digest": digest(accepted), "excluded_count": len(excluded),
        "excluded_digest": digest(excluded), "evidence_class": "INPUT_OBSERVATIONS_ONLY"}}


def validate_training_history(rows, *, training_cutoff):
    """Strict check for an already selected training frame: future/late rows fail."""
    normalized = normalize_history(rows)
    cutoff = instant(training_cutoff)
    if any(instant(r["bucket_end"]) > cutoff or instant(r["observed_at"]) > cutoff for r in normalized):
        raise DemandContractError("CUTOFF: future bucket or observation in training")
    result = select_training_history(normalized, training_cutoff=training_cutoff)
    if result["excluded"]:
        raise DemandContractError("OBSERVATION_KIND: training must contain one actual-demand channel")
    return result["receipt"]
