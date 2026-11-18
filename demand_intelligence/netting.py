"""Decimal-quantity consumption, deterministic allocation, explicit research policy.

This returns a candidate receipt only. It does not confirm a policy, write orders,
or replace the existing DraftStore/Canonical/Worker owners.
"""
from collections import defaultdict
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal

from .contracts import (DemandContractError, decimal_text, digest, dimension_key, instant,
                        normalize_dimensions, quantity, reject_overlaps, source_ref)


ELIGIBLE_STATUSES = {"OPEN", "CONFIRMED", "APPROVED", "PARTIALLY_SHIPPED"}


def net_forecasts(gross_rows, orders, *, policy):
    """Consume order residuals once across one forecast version and bounded windows.

    Orders sort by due_at/id. Same-bucket consumption precedes nearest-window
    consumption, then bucket_start/id breaks ties. Windows are calendar days.
    An aggregate-customer policy requires customer_id='*' on every Gross row.
    """
    p = deepcopy(policy)
    for name in ("policy_id", "version_id", "forecast_version_id"):
        if not isinstance(p.get(name), str) or not p[name].strip():
            raise DemandContractError(f"VERSION: explicit {name} required")
    if p.get("coverage") != "GROSS_INCLUDES_FIRM_ORDERS":
        raise DemandContractError("COVERAGE: gross must explicitly include participating firm orders")
    if type(p.get("match_customer")) is not bool:
        raise DemandContractError("CUSTOMER: explicit dimension matching policy required")
    statuses = p.get("statuses")
    if not isinstance(statuses, list) or not statuses or set(statuses) - ELIGIBLE_STATUSES:
        raise DemandContractError("STATUS: only explicitly named current firm statuses may consume")
    for field in ("backward_days", "forward_days"):
        if type(p.get(field)) is not int or not 0 <= p[field] <= 366:
            raise DemandContractError("WINDOW: explicit integer 0..366 days required")
    gs, ids = [], set()
    for row in gross_rows:
        r = normalize_dimensions(row)
        if not isinstance(r.get("id"), str) or not r["id"] or r["id"] in ids:
            raise DemandContractError("DUPLICATE: unique nonempty forecast id required")
        ids.add(r["id"])
        if r.get("version_id") != p["forecast_version_id"]:
            raise DemandContractError("VERSION: cross-version forecast consumption prohibited")
        if not isinstance(r.get("source_refs"), list) or not r["source_refs"] or any(
                not isinstance(x, str) or not x.strip() for x in r["source_refs"]):
            raise DemandContractError("SOURCE: forecast source_refs required")
        if not p["match_customer"] and r["customer_id"] != "*":
            raise DemandContractError("CUSTOMER: aggregate consumption requires explicit '*' forecast")
        if p["match_customer"] and r["customer_id"] == "*":
            raise DemandContractError("CUSTOMER: wildcard requires aggregate policy")
        if instant(r.get("bucket_start")) >= instant(r.get("bucket_end")):
            raise DemandContractError("BUCKET: forecast end must follow start")
        r["gross_quantity"] = decimal_text(quantity(r.get("gross_quantity"), "gross_quantity"))
        r["consumed_quantity"], r["net_quantity"] = "0", r["gross_quantity"]
        gs.append(r)
    reject_overlaps(gs)
    gs.sort(key=lambda r: (dimension_key(r), instant(r["bucket_start"]), r["id"]))
    os, ids, refs, excluded = [], set(), set(), []
    for row in orders:
        r = normalize_dimensions(row)
        ref = source_ref(r)
        if not isinstance(r.get("id"), str) or not r["id"] or r["id"] in ids or ref in refs:
            raise DemandContractError("DUPLICATE: order source/id cannot participate twice")
        ids.add(r["id"])
        refs.add(ref)
        instant(r.get("due_at"), "due_at")
        if r.get("status") == "PARTIALLY_SHIPPED" and "remaining_quantity" not in r:
            raise DemandContractError("QUANTITY: partial order requires sourced remaining_quantity")
        amount = quantity(r.get("remaining_quantity", r.get("quantity")))
        if "quantity" in r and amount > quantity(r["quantity"]):
            raise DemandContractError("QUANTITY: order remaining cannot exceed original")
        r["participating_quantity"] = decimal_text(amount)
        if r.get("status") in statuses and r.get("kind") == "FIRM_ORDER":
            os.append(r)
        else:
            excluded.append({"order_id": r["id"], "source_ref": ref, "reason": "STATUS_OR_KIND_EXCLUDED"})
    os.sort(key=lambda r: (instant(r["due_at"]), r["id"]))
    allocations, participants = [], []
    totals = defaultdict(lambda: {k: Decimal(0) for k in ("gross", "consumed", "net", "firm", "effective")})
    for o in os:
        due = instant(o["due_at"])
        left = quantity(o["participating_quantity"])
        totals[o["unit"]]["firm"] += left
        matches = []
        for g in gs:
            dims = ("item_id", "location_id", "unit", "customer_id") if p["match_customer"] else ("item_id", "location_id", "unit")
            start, end = instant(g["bucket_start"]), instant(g["bucket_end"])
            if all(o[k] == g[k] for k in dims) and start - timedelta(days=p["backward_days"]) <= due < end + timedelta(days=p["forward_days"]):
                inside = start <= due < end
                distance = 0 if inside else min(abs((due - start).total_seconds()), abs((due - end).total_seconds()))
                matches.append((not inside, distance, start, g["id"], g))
        for *_, g in sorted(matches, key=lambda x: x[:4]):
            amount = min(left, quantity(g["net_quantity"]))
            if amount <= 0:
                continue
            left -= amount
            g["consumed_quantity"] = decimal_text(quantity(g["consumed_quantity"]) + amount)
            g["net_quantity"] = decimal_text(quantity(g["net_quantity"]) - amount)
            allocations.append({"forecast_id": g["id"], "forecast_version_id": g["version_id"],
                                "order_id": o["id"], "quantity": decimal_text(amount), "unit": o["unit"],
                                "uom": o["unit"], "order_due_at": o["due_at"],
                                "order_source_ref": o["source_ref"], "forecast_source_refs": g["source_refs"],
                                "policy_id": p["policy_id"], "policy_version": p["version_id"]})
            if not left:
                break
        participants.append(dict(o, consumed_quantity=decimal_text(quantity(o["participating_quantity"]) - left),
                                 unconsumed_quantity=decimal_text(left)))
    for g in gs:
        for field, key in (("gross_quantity", "gross"), ("consumed_quantity", "consumed"), ("net_quantity", "net")):
            totals[g["unit"]][key] += quantity(g[field])
        g["allocations"] = [a for a in allocations if a["forecast_id"] == g["id"]]
    for total in totals.values():
        total["effective"] = total["firm"] + total["net"]
        if total["gross"] != total["consumed"] + total["net"] or total["effective"] != total["gross"] + total["firm"] - total["consumed"]:
            raise DemandContractError("CONSERVATION: internal allocation mismatch")
    result = {"rows": gs, "allocations": allocations, "participating_orders": participants,
              "excluded_orders": excluded,
              "totals_by_unit": {u: {k: decimal_text(v) for k, v in t.items()} for u, t in totals.items()}}
    result["receipt"] = {"policy": p, "policy_digest": digest(p), "gross_digest": digest(gross_rows),
                         "orders_digest": digest(orders), "allocation_digest": digest(allocations),
                         "result_digest": digest(result), "execution": "RESEARCH_CANDIDATE_ONLY",
                         "allocation_order": "ORDER_DUE_ID_THEN_SAME_BUCKET_NEAREST_WINDOW_START_ID"}
    return result
