"""Reproduce bounded research evidence; freeze all inputs before calculation.

Run from repository root with the task-owned inventory environment:
python -m inventory_optimization.experiment --task TASK_DIR --fixture JSON_GZ
"""
import argparse
import copy
import gzip
import hashlib
import importlib.metadata
import inspect
import json
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .core import at, compare_policies, digest, recommend, text
from .enterprise import build_request
from .stockpyl_checks import classical_models, service_quantile_check, two_node_simulation


SEED=61025


def write(path,value,*,immutable=False):
    content=json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n'
    if immutable and path.exists():
        if path.read_text(encoding='utf-8')!=content:
            raise RuntimeError('FROZEN_INPUT_CONFLICT: '+str(path))
        return
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(content,encoding='utf-8')


def series(now,dim,values,ref):
    return [dict(dim,bucket_start=(now-timedelta(days=len(values)-i)).isoformat(),
        bucket_end=(now-timedelta(days=len(values)-i-1)).isoformat(),quantity=str(q),
        observability='OBSERVED_UNCENSORED',source_ref=f'{ref}:{i}') for i,q in enumerate(values)]


def service(alpha='0.95'):
    return dict(approval_status='RESEARCH_POLICY',source='APS-PRO-EXPAND-06 frozen sensitivity, not enterprise approval',
        target_cycle_service_level=alpha,review_days='7',quantity_step='1',
        independent_iid=True,normal_protection_approximation=True)


def freeze_inputs(data,fixture_hash):
    now=at(data['planning_at']);rng=random.Random(SEED)
    requests=[];test=[]
    for index,source in enumerate(data['supply_sources']):
        dim={k:source[k] for k in ('item_id','location_id')}
        dim['uom']=source['capacity']['uom']
        mu={'FAB-BLK':4,'FAB-WHT':3,'FAB-NVY':2,'ZIP':4}[dim['item_id']]
        values=[max(0,round(rng.gauss(mu,mu*.45))) for _ in range(120)]
        future=[max(0,round(rng.gauss(mu,mu*.45))) for _ in range(44)]
        # These are declared research material histories, not observed sales or
        # confirmed factory consumption. The source BOM/forecast references bind
        # dimensions and lineage but do not convert generated data to source facts.
        bom=[b for b in data['foundation_projection']['source_tables']['bom_lines']
            if b['input_item_id']==dim['item_id']]
        lineage=dict(kind='SYNTHETIC_RESEARCH_MATERIAL_DEMAND',
            source_refs=[f'FROZEN-SEED-{SEED}-SERIES-{index}',f'public-fixture-sha256:{fixture_hash}'],
            forecast_refs=[f['entity_id'] for f in data['foundation_projection']['source_tables']['forecasts']],
            bom_refs=[b['entity_id'] for b in bom],
            relation='DIMENSION_AND_BOM_APPLICABILITY_ONLY;QUANTITIES_ARE_ISOLATED_GENERATED_HISTORY',
            generator=dict(family='IID_ROUNDED_NONNEGATIVE_GAUSSIAN',mean=mu,sd=mu*.45,seed=SEED),
            original_facts_modified=False)
        r=build_request(data,history=series(now,dim,values,f'INV-SYNTH-{source["id"]}'),
            demand_lineage=lineage,source_id=source['id'],policy_version='INV06-'+source['id']+'-CSL95',
            service_policy=service(),reserved_quantity='0',
            reserved_source_ref='FROZEN_RESEARCH_BASELINE_BEFORE_ANY_PROMISING_HOLD')
        requests.append(dict(case_id='ENTERPRISE-DIM-'+source['id'],request=r))
        test.append(dict(case_id='ENTERPRISE-DIM-'+source['id'],demand=[str(q) for q in future],
            lead_time_days=[(source['lead_time_seconds']+source['receiving_qc']['wait_seconds'])/86400]*44))
    for index,(name,sd,intermittent) in enumerate([('STEADY',3,False),('VOLATILE',8,False),('INTERMITTENT',0,True)]):
        values=[(20 if rng.random()<.2 else 0) if intermittent else max(0,round(rng.gauss(10,sd))) for _ in range(180)]
        future=[(20 if rng.random()<.2 else 0) if intermittent else max(0,round(rng.gauss(10,sd))) for _ in range(90)]
        r=copy.deepcopy(requests[1]['request']);dim=dict(item_id='RESEARCH-'+name,location_id='RESEARCH-DC',uom='unit')
        r.update(dimension=dim,identity=dict(data_version_id='ISOLATED-SYNTHETIC-SEED-61025',projection_sha256=fixture_hash),
            history=series(now,dim,values,'INV-SYNTH-'+name),
            demand_lineage=dict(kind='ISOLATED_SYNTHETIC',source_refs=['seed61025:'+name],
                generator=dict(family='BERNOULLI_BATCH20' if intermittent else 'IID_ROUNDED_NONNEGATIVE_GAUSSIAN',
                    seed=SEED,sd=sd),forecast_refs=[],bom_refs=[]),
            policy_version='INV06-SYNTH-'+name,horizon_end=(now+timedelta(days=90)).isoformat())
        r['source_terms'].update(dim,id='RESEARCH-SUPPLIER',lead_time_seconds=172800,qc_wait_seconds=86400,
            total_capacity_quantity='10000',capacity_consumed_quantity='0',capacity_source_ref='RESEARCH-CAP10000',
            minimum_order_quantity='10',order_multiple='10',source_ref='RESEARCH-SUPPLIER-TERMS')
        r['stock']=dict(status='KNOWN',on_hand='30',quality_blocked='0',other_nonnettable='0',
            reserved='0',backorders='0',inbound=[],source_refs=['RESEARCH-OPENING30'])
        r['current_policy']=[dict(minimum='10',maximum='60',effective_from=r['planning_at'],
            effective_to=r['horizon_end'],source_ref='EXPLICIT_SYNTHETIC_SOFT_MIN10_MAX60_PROXY')]
        r.pop('enterprise_source_receipt',None)
        requests.append(dict(case_id='SYNTH-'+name,request=r))
        test.append(dict(case_id='SYNTH-'+name,demand=[str(q) for q in future],
            lead_time_days=[3 if i%5 else 4 for i in range(90)]))
    return dict(schema_revision='inventory-experiment-frozen-v1',seed=SEED,
        source_fixture_sha256=fixture_hash,requests=requests,test_sets=test,
        sensitivity=dict(cycle_service_levels=['0.90','0.95','0.99'],lead_time_sd_days=['0','1'],
            explicit_cost_cases=[dict(fixed_cost=k,holding_cost=.225,demand_rate=1300,
                overage_cost=2,underage_cost=18,demand_mean=120,demand_sd=10) for k in [4,8,16]]),
        stockpyl_network=dict(seed=SEED,periods=90,topology='TWO_NODE_SERIAL_SYNTHETIC',
            base_stock=[22,14],shipment_lead_time=[2,1],external_supply='UNLIMITED',demand='POISSON4'),
        scope='ALL_GENERATED_HISTORY_AND_TEST_DEMAND_ARE_RESEARCH_NOT_ORIGINAL419',
        anti_leakage='History ends at planning_at; all future test values frozen before calculating any policy.')


def execute(task,fixture):
    task=Path(task);fixture=Path(fixture)
    raw=fixture.read_bytes();fixture_hash=hashlib.sha256(raw).hexdigest()
    data=json.loads(gzip.decompress(raw).decode('utf-8'))['data']
    frozen=freeze_inputs(data,fixture_hash)
    frozen_path=task/'frozen/inventory-experiment-inputs.json'
    write(frozen_path,frozen,immutable=True)
    freeze_marker=task/'frozen/inventory-before-first-run.json'
    if not freeze_marker.exists():
        write(freeze_marker,dict(frozen_at=datetime.now(timezone.utc).isoformat(),
            input_sha256=hashlib.sha256(frozen_path.read_bytes()).hexdigest(),
            semantic_sha256=digest(frozen),fixture_sha256=fixture_hash,
            statement='Persisted before first policy, comparison, sensitivity, and Stockpyl calculation.'),immutable=True)
    marker=json.loads(freeze_marker.read_text(encoding='utf-8'))
    if marker['input_sha256']!=hashlib.sha256(frozen_path.read_bytes()).hexdigest():
        raise RuntimeError('FROZEN_BYTES_CHANGED')
    # No calculation occurs above this line.
    started=datetime.now(timezone.utc).isoformat();results=[];comparisons=[];sens=[]
    tests={t['case_id']:t for t in frozen['test_sets']}
    for case in frozen['requests']:
        r=case['request'];out=recommend(r);results.append(dict(case_id=case['case_id'],result=out))
        test=tests[case['case_id']]
        comparisons.append(dict(case_id=case['case_id'],result=compare_policies(r,test['demand'],lead_time_days=test['lead_time_days'])))
        for alpha in frozen['sensitivity']['cycle_service_levels']:
            for lead_sd in frozen['sensitivity']['lead_time_sd_days']:
                x=copy.deepcopy(r);x['service_policy']['target_cycle_service_level']=alpha
                x['source_terms']['lead_time_sd_days']=lead_sd
                if lead_sd!='0':x['source_terms']['lead_time_variability_source']='FROZEN_RESEARCH_STD1DAY_SENSITIVITY'
                p=recommend(x)
                sens.append(dict(case_id=case['case_id'],cycle_service_level=alpha,lead_sd_days=lead_sd,
                    selected_method=p['selected_method'],minimum=p['native_policy']['rows'][0]['minimum'],
                    maximum=p['native_policy']['rows'][0]['maximum'],
                    normal_quantile_check=service_quantile_check(x)))
    stockpyl_runs=dict(classical=[classical_models(**x) for x in frozen['sensitivity']['explicit_cost_cases']],
        network=two_node_simulation(seed=SEED,periods=90))
    evidence=dict(schema_revision='inventory-algorithm-evidence-v1',started_at=started,
        finished_at=datetime.now(timezone.utc).isoformat(),frozen_input_bytes_sha256=marker['input_sha256'],
        frozen_before_run=marker['frozen_at']<started,python=sys.version,
        dependency_versions={k:importlib.metadata.version(k) for k in ['stockpyl','numpy','scipy','networkx','pytest','jsonschema']},
        results=results,comparisons=comparisons,sensitivity=sens,stockpyl=stockpyl_runs,
        enterprise_native_worker_executed=False,
        financial_improvement_claim=False,real_enterprise_service_improvement_claim=False)
    import stockpyl.eoq
    import stockpyl.newsvendor
    import stockpyl.sim
    evidence['implementation_sha256']={p.name:hashlib.sha256(p.read_bytes()).hexdigest()
        for p in Path(__file__).parent.glob('*.py')}
    evidence['stockpyl_source_sha256']={module.__name__:hashlib.sha256(
        Path(inspect.getsourcefile(module)).read_bytes()).hexdigest()
        for module in (stockpyl.eoq,stockpyl.newsvendor,stockpyl.sim)}
    write(task/'evidence/inventory-algorithm-experiments.json',evidence)
    main=next(x for x in results if x['case_id']=='ENTERPRISE-DIM-SRC-BLK-B')
    request=next(x['request'] for x in frozen['requests'] if x['case_id']==main['case_id'])
    write(task/'evidence/inventory-enterprise-candidate.json',dict(request=request,**main))
    write(task/'evidence/inventory-experiment-summary.json',dict(frozen_input_bytes_sha256=marker['input_sha256'],
        case_count=len(results),sensitivity_count=len(sens),main_native_policy=main['result']['native_policy'],
        comparisons=[dict(case_id=x['case_id'],rows=[{k:v for k,v in row.items() if k!='trace'}
            for row in x['result']['rows']]) for x in comparisons],
        eoq=stockpyl_runs['classical'][1],network_periods=90,stockpyl_version=importlib.metadata.version('stockpyl')))
    return dict(cases=len(results),sensitivities=len(sens),main_native_policy=main['result']['native_policy'],
        path=str(task/'evidence/inventory-algorithm-experiments.json'))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--task',required=True);p.add_argument('--fixture',required=True)
    args=p.parse_args();print(json.dumps(execute(args.task,args.fixture),ensure_ascii=False,indent=2))
