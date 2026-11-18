"""Exact JSON file boundary for the pinned inventory research runtime.

The parent owns candidate selection and stores. This process receives one full
inventory request, executes Stockpyl's applicable normal quantile cross-check,
and writes one new receipt file. It never loads a business Store or runs Worker.
"""
import hashlib
import json
import math
from importlib.metadata import version
from pathlib import Path
import sys
import time


PINNED_VERSION = '1.0.2'


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('INVENTORY_RUNNER_DUPLICATE_FIELD')
        result[key] = value
    return result


def _nonfinite(value):
    raise ValueError('INVENTORY_RUNNER_NONFINITE_JSON')


def evaluate(body):
    """Validate a request and produce evidence; PASS requires a real solver call."""
    if not isinstance(body, dict) or set(body) != {'request'} or not isinstance(body['request'], dict):
        raise ValueError('INVENTORY_RUNNER_BODY_FIELDS')
    request = body['request']
    started = time.perf_counter()
    observed_version = version('stockpyl')
    if observed_version != PINNED_VERSION:
        raise ValueError('INVENTORY_RUNNER_PINNED_VERSION')
    from inventory_optimization import core
    from inventory_optimization import stockpyl_checks
    result = core.recommend(request)
    receipt = dict(check='SKIPPED', executed=False, stockpyl_version=observed_version,
        pinned_version=PINNED_VERSION, weights_are_financial_costs=False,
        enterprise_financial_optimality=False, native_engine_executed=False,
        scope='NORMAL_MOMENT_MATCHED_PROTECTION_QUANTILE_NOT_CONSTRAINED_OPTIMUM',
        adapter_module_sha256=hashlib.sha256(Path(stockpyl_checks.__file__).read_bytes()).hexdigest(),
        policy_module_sha256=hashlib.sha256(Path(core.__file__).read_bytes()).hexdigest())
    if result['status'] == 'POLICY_BLOCKED':
        receipt.update(reason_code='POLICY_BLOCKED', policy_block_reason=result['reason_code'])
    else:
        normal = next(c for c in result['candidates'] if c['method'] == 'NORMAL_SERVICE_ORDER_UP_TO')
        metrics = normal['metrics']
        sigma_squared = ((metrics['protection_lead_days'] + metrics['review_days'])
            * metrics['sample_daily_sd'] ** 2 + metrics['mean_daily_demand'] ** 2
            * metrics['lead_time_sd_days'] ** 2)
        if normal.get('status') == 'NOT_APPLICABLE':
            receipt.update(reason_code='NORMAL_NOT_APPLICABLE',
                normal_applicability_reason=normal['reason_code'])
        elif metrics['mean_daily_demand'] <= 0 or sigma_squared <= 0:
            receipt.update(reason_code='ZERO_PROTECTION_VARIANCE')
        else:
            # This adapter actually invokes stockpyl.newsvendor.newsvendor_normal;
            # its probability weights are service policy, not financial costs.
            actual = stockpyl_checks.service_quantile_check(request)
            if (actual.get('check') != 'PASS' or actual.get('stockpyl_version') != PINNED_VERSION
                or actual.get('weights_are_financial_costs') is not False
                or any(actual.get(key) != normal[source] for key, source in (
                    ('native_minimum', 'minimum'), ('native_maximum', 'maximum'),
                    ('reorder_point', 'reorder_point'), ('rounded_order_up_to', 'order_up_to')))
                or not math.isfinite(actual['stockpyl_unrounded_order_up_to'])):
                raise ValueError('INVENTORY_RUNNER_QUANTILE_MISMATCH')
            from stockpyl import newsvendor
            receipt.update(actual, executed=True,
                stockpyl_source_module_sha256=hashlib.sha256(Path(newsvendor.__file__).read_bytes()).hexdigest(),
                normal_applicability='APPLICABLE',
                checked_method='NORMAL_SERVICE_ORDER_UP_TO',
                policy_selection='PARENT_OWNS_SELECTION_OF_CURRENT_DETERMINISTIC_OR_NORMAL_CANDIDATE')
    return dict(receipt=receipt, request_sha256=_digest(request),
        elapsed_seconds=time.perf_counter() - started,
        module_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2:
        raise ValueError('INVENTORY_RUNNER_PATH')
    from scripts.data_link_guard import install_sql_audit, unlinked
    source, target = (unlinked(Path(s)) for s in args)
    if (source.parent != target.parent or source == target
        or source.suffix != '.json' or target.suffix != '.json' or not source.is_file()):
        raise ValueError('INVENTORY_RUNNER_PATH')
    install_sql_audit(source.parent)
    if target.exists():
        raise ValueError('INVENTORY_RUNNER_OUTPUT_EXISTS')
    body = json.loads(source.read_text(encoding='utf-8'), object_pairs_hook=_unique_object,
        parse_constant=_nonfinite)
    result = evaluate(body)
    # Exclusive creation preserves the input and any concurrent prior output.
    with target.open('x', encoding='utf-8') as output:
        json.dump(result, output, ensure_ascii=False, indent=2, allow_nan=False)


if __name__ == '__main__':
    # The parent captures UTF-8; Windows otherwise writes exception filenames
    # in the local code page, losing the precise rejection in the pipe reader.
    sys.stderr.reconfigure(encoding='utf-8')
    main()
