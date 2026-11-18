"""One existing order line's planning priority in an isolated, replayable draft.

This is demand.priority, independent of supplier or alternate-resource priorities.
Only a confirmed capability may expose the action; solver results still require a
new run and do not establish an execution or customer commitment.
"""
import copy

from planning_capabilities import profile_id, supports


ACTION = "set_demand_priority"
INTENT = "订单需求优先级模拟"
MIN_PRIORITY = 0
MAX_PRIORITY = 2147483647
CHANGE_SCHEMA = {
    "type": "object",
    "properties": {
        "demand_id": {"type": "string", "minLength": 1},
        "priority": {"type": "integer", "minimum": MIN_PRIORITY, "maximum": MAX_PRIORITY},
    },
    "required": ["demand_id", "priority"],
    "additionalProperties": False,
}


def apply(data, demand_id, priority):
    """Change only priority after resolving one exact, existing firm demand id."""
    from drafts import DraftError

    if not supports(profile_id(data), "actions", "demand_priority"):
        raise DraftError("UNSUPPORTED_PROFILE_CAPABILITY: 当前模型未认证订单需求优先级模拟")
    return apply_change(data, demand_id, priority)


def apply_change(data, demand_id, priority):
    """Pure mutation shared by explicitly gated actions; grants no capability."""
    from drafts import DraftError

    if type(priority) is not int or not MIN_PRIORITY <= priority <= MAX_PRIORITY:
        raise DraftError("INVALID_DEMAND_PRIORITY: 优先级必须是0至2147483647的整数")
    rows = [row for row in data["demand_lines"] if row["id"] == demand_id]
    if len(rows) != 1:
        raise DraftError("UNKNOWN_DEMAND: 需要当前计划中的精确需求行编号")
    row = rows[0]
    if row.get("kind", "FIRM_ORDER") != "FIRM_ORDER":
        raise DraftError("UNSUPPORTED_DEMAND_KIND: 仅支持真实订单需求的优先级模拟")
    before = row["priority"]
    if before == priority:
        raise DraftError("NO_PRIORITY_CHANGE: 新优先级与原值相同")
    foundation=profile_id(data)=='foundation-planning-projection-v1'
    order_ref=None if foundation else row['order_ref']
    if foundation:
        from p22.foundation_projection import firm_order_ref
        order_ref=firm_order_ref(data,demand_id)
    changes = {
        "intent": INTENT,
        "action": ACTION,
        "demand_id": row["id"],
        "before_priority": before,
        "after_priority": priority,
        **{key: copy.deepcopy(row[key]) for key in ["item_id", "location_id", "uom", "due_at", "quantity"]},
        "order_ref":order_ref,
        "simulation_only": True,
        "business_permission": "NOT_CONFIRMED",
        "execution_authorized": False,
    }
    if "customer_id" in row:
        changes["customer_id"] = row["customer_id"]
    if foundation:
        from p22.foundation_projection import apply_priority_overlay
        changes['foundation_overlay_receipt']=apply_priority_overlay(data,demand_id,priority)
    else:row["priority"] = priority
    return changes


def verify(baseline, draft):
    """Reconstruct the declared change and reject unrelated input/audit edits."""
    from drafts import DraftError

    changes = draft.get("changes", {})
    marked = isinstance(changes, dict) and (changes.get("action") == ACTION or changes.get("intent") == INTENT)
    original = {row["id"]: row for row in baseline["demand_lines"]}
    candidate_rows = draft.get("input", {}).get("demand_lines", [])
    # A deleted/renamed action label must not hide an existing line's priority edit.
    priority_changed = any(isinstance(row, dict) and row.get("id") in original
                           and row.get("priority") != original[row["id"]]["priority"]
                           for row in candidate_rows)
    if not marked and not priority_changed and profile_id(baseline)!='foundation-planning-projection-v1':
        return
    try:
        expected = copy.deepcopy(baseline)
        recreated = apply(expected, changes["demand_id"], changes["after_priority"])
        expected.update(scenario_id=draft["id"], scenario_revision=1)
        if recreated != changes or expected != draft["input"]:
            raise ValueError("scope differs")
    except (KeyError, TypeError, ValueError) as error:
        raise DraftError("DEMAND_PRIORITY_SCOPE_MISMATCH: 草案只能改变指定需求行的优先级") from error
