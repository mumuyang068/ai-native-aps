"""Run-state disclosure contracts without any archive, SQL fixture or solver."""
import copy
import threading
from types import SimpleNamespace

import pytest

from p22.service import EnterprisePlans
from planning_adapter import InputError


PID='PLAN-'+'1'*16
BASE='API-'+'2'*16
RUN='API-'+'3'*16
DRAFT='DRAFT-'+'4'*16


def owner(run):
    plans=EnterprisePlans.__new__(EnterprisePlans);plans.lock=threading.RLock()
    plans.store=SimpleNamespace(get=lambda pid:dict(id=PID,data_version_id='DATA-TEST'),snapshot=lambda pid:{})
    data=dict(rules={},site=dict(timezone='Asia/Shanghai'),demand_lines=[])
    drafts=[dict(id=DRAFT,run_id=RUN,state='DRAFT',input=data,changes=dict(intent='排程研究'))]
    states={BASE:dict(solve_run_id=BASE,state='QUEUED'),RUN:run};calls=[]
    def read(rid):calls.append(rid);return copy.deepcopy(states[rid])
    def no_analysis(*args,**kwargs):raise AssertionError('A failed/not-run candidate must not load native result or full analysis')
    service=SimpleNamespace(baseline_id=lambda:BASE,drafts=SimpleNamespace(baseline=data,metadata=lambda limit: {'rows': copy.deepcopy(drafts)}, get=lambda target: copy.deepcopy(next(d for d in drafts if d['id']==target))),
        compute=lambda:SimpleNamespace(run=read),selected_view=no_analysis)
    plans.service=lambda pid:service;plans.run_analysis=no_analysis
    return plans,calls,drafts


def candidate(value):return next(c for c in value['candidates'] if c['target']==DRAFT)


def test_failed_run_status_discloses_only_original_bounded_owner_fields(monkeypatch):
    monkeypatch.setenv('APS_DIAGNOSTIC_TOKEN','isolated-secret-value')
    original=dict(solve_run_id=RUN,state='FAILED',failure_code='InputError: ORIGINAL_NATIVE_REFUSAL isolated-secret-value',
        created_at='2026-10-26T08:00:00+08:00',started_at='2026-10-26T08:00:01+08:00',finished_at='2026-10-26T08:00:02+08:00',
        internal_debug=dict(secret='isolated-secret-value'),result_ref='/must-not-become-plan-result')
    plans,calls,_=owner(original)
    value=plans.scenario_review(PID,'DATA-TEST',BASE,DRAFT);row=candidate(value)
    assert row['run_status']=={k:('InputError: ORIGINAL_NATIVE_REFUSAL [REDACTED]' if k=='failure_code' else original[k])
        for k in ('solve_run_id','state','failure_code','created_at','started_at','finished_at')}
    assert row['calculation_state']=='FAILED' and row['comparable'] is False
    assert row['deliveries']==[] and row['inventory']==[]
    assert calls.count(RUN)==1  # Available list and candidate share this request's same Owner receipt.


@pytest.mark.parametrize('failure_fields',[{},dict(failure_code=None)])
def test_missing_failure_reason_is_not_inferred_or_filled(failure_fields):
    plans,_,_=owner(dict(solve_run_id=RUN,state='UNKNOWN',**failure_fields))
    row=candidate(plans.scenario_review(PID,'DATA-TEST',BASE,DRAFT))
    assert row['run_status']==dict(solve_run_id=RUN,state='UNKNOWN',**failure_fields)
    assert row['comparable'] is False and row['deliveries']==[]


def test_foreign_run_status_is_not_relabelled_to_requested_candidate():
    plans,_,_=owner(dict(solve_run_id='API-'+'9'*16,state='FAILED',failure_code='OTHER_RUN'))
    with pytest.raises(InputError,match='REVIEW_RUN_IDENTITY_MISMATCH'):
        plans.scenario_review(PID,'DATA-TEST',BASE,DRAFT)


def test_not_run_draft_has_no_synthetic_run_receipt():
    plans,calls,drafts=owner({});drafts[0]['run_id']=None
    row=candidate(plans.scenario_review(PID,'DATA-TEST',BASE,DRAFT))
    assert row['calculation_state']=='NOT_RUN' and row['run_status'] is None
    assert calls==[BASE]


def test_success_projection_keeps_existing_review_evidence_shape(monkeypatch):
    import planning_decision
    plans,_,_=owner(dict(solve_run_id=RUN,state='SUCCEEDED',failure_code=None))
    service=plans.service(PID)
    service.selected_view=lambda target:dict(context={})
    plans.run_analysis=lambda *a,**kw:dict(manufacturing_analysis=dict(validation=dict(status='PASS')))
    monkeypatch.setattr(planning_decision,'read_table',lambda *a,**kw:dict(status='EMPTY_CHECKED',records=[]))
    monkeypatch.setattr(planning_decision,'candidate_projection',lambda target,label,state,*a,**kw:
        dict(target=target,calculation_state=state,deliveries=[],comparable=False))
    row=candidate(plans.scenario_review(PID,'DATA-TEST',BASE,DRAFT))
    assert 'run_status' not in row  # Diagnostic-only fields cannot invalidate an unchanged successful review.
