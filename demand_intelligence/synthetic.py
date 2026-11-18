"""Predeclared synthetic mechanisms linked only to original business dimensions.

These quantities are generated, never original enterprise sales/history. Freeze
the source, seed, generator and output hashes BEFORE any comparative benchmark.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import random

from .contracts import DemandContractError, digest, normalize_dimensions, instant


REGIMES = ("SEASONAL_13W", "LINEAR_TREND", "INTERMITTENT", "LEVEL_SHIFT", "ALL_ZERO", "CENSORED_STOCKOUT")


def generate_research_history(source_data, *, seed, periods=156,
                              start="2023-10-23T00:00:00+08:00",
                              source_snapshot_sha256=None, include_material_series=False):
    """Deterministic weeks over original demand dimensions; no source quantities used.

    Optional materials use original item identities/unit and existing demand site,
    customer='*' as an explicitly new research aggregate (not a source customer).
    """
    if type(seed) is not int or type(periods) is not int or not 1 <= periods <= 1040:
        raise DemandContractError("SYNTHETIC: explicit integer seed and bounded periods required")
    instant(start)
    start_at = datetime.fromisoformat(start.replace("Z", "+00:00"))
    if start_at.weekday() != 0:
        raise DemandContractError("SYNTHETIC: weekly history must start on Monday")
    keys = sorted({(r["item_id"], r["location_id"], r["customer_id"], r.get("unit", r.get("uom")))
                   for r in source_data.get("demand_lines", [])})
    if not keys:
        raise DemandContractError("SYNTHETIC: original demand dimensions required")
    original_keys = list(keys)
    material_keys = []
    if include_material_series:
        site = keys[0][1]
        for item in source_data.get("items", []):
            if item.get("id") in {"FAB-BLK", "BUTTON"}:
                material_keys.append((item["id"], site, "*", item["base_uom"]))
    keys += sorted(material_keys)
    rng = random.Random(seed)
    rows, regimes = [], []
    for j, key in enumerate(keys):
        normalize_dimensions(dict(zip(("item_id", "location_id", "customer_id", "unit"), key)))
        regime = REGIMES[j % len(REGIMES)] if key not in material_keys else "SEASONAL_13W"
        regimes.append(dict(dimensions=dict(zip(("item_id", "location_id", "customer_id", "unit"), key)), mechanism=regime))
        for i in range(periods):
            base = 7 + (j % 4)
            if regime in {"SEASONAL_13W", "CENSORED_STOCKOUT"}:
                q = max(0, round(base + 4 * math.sin(2 * math.pi * i / 13) + rng.gauss(0, 1.2)))
            elif regime == "LINEAR_TREND":
                q = max(0, round(3 + 0.055 * i + rng.gauss(0, 1.5)))
            elif regime == "INTERMITTENT":
                q = rng.randint(5, 22) if rng.random() < .22 else 0
            elif regime == "LEVEL_SHIFT":
                q = max(0, round(base + (5 if i >= 110 else 0) + rng.gauss(0, 2)))
            else:
                q = 0
            censored = regime == "CENSORED_STOCKOUT" and i % 17 == 3
            if censored:
                q = min(q, 2)
            if key[-1] == "米":
                q = round(q * 1.6, 1)
            elif key[-1] == "个":
                q *= 4
            begin, end = start_at + timedelta(weeks=i), start_at + timedelta(weeks=i + 1)
            identifier = "FH-" + digest([*key, i])[:24]
            row = dict(zip(("item_id", "location_id", "customer_id", "unit"), key))
            row.update(id=identifier, uom=key[-1], bucket_start=begin.isoformat(), bucket_end=end.isoformat(),
                       quantity=str(q), observed_at=end.isoformat(), observation_kind="OBSERVED_DEMAND",
                       censored=censored, source_ref=f"APS-PRO-EXPAND-06/synthetic-history/{identifier}",
                       evidence_status="DERIVED_SYNTHETIC", mechanism=regime,
                       dimension_source="ORIGINAL_DEMAND_DIMENSIONS" if key not in material_keys else "ORIGINAL_ITEM_AND_SITE_NEW_RESEARCH_AGGREGATE")
            rows.append(row)
    receipt = dict(generator="demand_intelligence.synthetic.generate_research_history/v1", seed=seed,
                   periods=periods, frequency="W-MON", season_length=13, start=start,
                   original_dimension_count=len(original_keys), added_material_dimension_count=len(material_keys),
                   source_snapshot_sha256=source_snapshot_sha256, source_dimensions_digest=digest(original_keys),
                   quantities_from_original=False, original_facts_modified=False,
                   research_class="ISOLATED_DERIVED_SYNTHETIC_NOT_419_FACTS", regimes=regimes,
                   mechanism_parameters={"seasonality": "base+4*sin(2*pi*t/13)+Normal(0,1.2)",
                       "trend": "3+0.055*t+Normal(0,1.5)", "intermittent": "Bernoulli(0.22)*UniformInteger(5,22)",
                       "level_shift": "base+5*I(t>=110)+Normal(0,2)", "censoring": "t%17==3; observable quantity capped at2",
                       "material_scaling": {"米": 1.6, "个": 4}}, output_digest=digest(rows))
    return {"rows": rows, "receipt": receipt}


def _bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")


def _hash(data):
    return hashlib.sha256(data).hexdigest()


def freeze_research_inputs(data, *, output_dir, benchmark_plan):
    """Create once. Refuse overwrite of any frozen history, protocol or manifest."""
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    names = ("forecast-history.json", "forecast-benchmark-plan.json", "forecast-input-manifest.json")
    if any((root / name).exists() for name in names):
        raise DemandContractError("FROZEN: inputs already exist; no retrospective rewriting")
    history, plan = _bytes(data), _bytes(benchmark_plan)
    source = Path(__file__).read_bytes()
    manifest = {"frozen_at": datetime.now(timezone.utc).isoformat(), "seed": data["receipt"]["seed"],
                "history_sha256": _hash(history), "plan_sha256": _hash(plan), "generator_sha256": _hash(source),
                "generator_path": str(Path(__file__).resolve()), "history_count": len(data["rows"]),
                "source_snapshot_sha256": data["receipt"].get("source_snapshot_sha256"),
                "input_freeze_precedes_benchmark": True}
    for name, payload in zip(names, (history, plan, _bytes(manifest))):
        with (root / name).open("xb") as stream:
            stream.write(payload)
    return manifest


def verify_research_freeze(output_dir):
    root = Path(output_dir)
    manifest = json.loads((root / "forecast-input-manifest.json").read_text(encoding="utf-8"))
    checks = {"history_sha256": root / "forecast-history.json", "plan_sha256": root / "forecast-benchmark-plan.json",
              "generator_sha256": Path(__file__)}
    for field, file in checks.items():
        if _hash(file.read_bytes()) != manifest[field]:
            raise DemandContractError(f"HASH: frozen input mismatch in {field}")
    return manifest
