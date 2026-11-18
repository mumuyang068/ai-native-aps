import copy
import json
from pathlib import Path
from plan_context import build_plan_context, business_summary, condition_decision, due_change_is_noop, unsupported_message

ROOT=Path(__file__).resolve().parents[1]

def source(name='P0-BASELINE'):
    folder=ROOT/'fixtures/regression'/name
    data=json.loads((folder/'canonical_input.json').read_text(encoding='utf-8'))
    result=json.loads((folder/'PlanResult.json').read_text(encoding='utf-8'))
    return data,{'run':{'state':'SUCCEEDED','solve_run_id':result['solve_run_id']},'result':result}

def test_context_includes_version_calendar_status_sources_and_actions():
    data,view=source();c=build_plan_context(data,view)
    assert c['plan_version']['solve_run_id']==view['result']['solve_run_id']
    assert c['plan_version']['scenario_revision']==data['scenario_revision']
    assert c['order_summary']['order_count']==1
    assert c['purchase_status']['suggestions'][0]['quantity']=='1200'
    assert c['manufacturing_status']['suggestions'][0]['quantity']=='1000'
    assert c['resource_calendars']['calendars'][0]['available_intervals']==data['calendars'][0]['available_intervals']
    assert all(r['source']['artifact_ref'] and r['source']['pointer'] for r in c['effective_constraints'])
    assert c['supported_operations']['change_due_date']['supported'] is True
    assert c['supported_operations']['change_shifts']['supported'] is False

def test_explicit_forbidden_overtime_is_scoped_to_current_plan():
    data,view=source();c=build_plan_context(data,view)
    assert c['overtime']['policy']=='EXPLICITLY_FORBIDDEN'
    assert c['overtime']['scope']=='CURRENT_SCENARIO'
    assert c['overtime']['source']['pointer']=='/rules/allow_overtime'
    assert condition_decision(c,'NO_OVERTIME')['status']=='ALREADY_SATISFIED'

def test_calendar_without_overtime_does_not_imply_a_ban():
    data,view=source();del data['rules']['allow_overtime']
    c=build_plan_context(data,view)
    assert c['overtime']['policy']=='UNSPECIFIED'
    assert c['overtime']['arrangement']=='NO_SEPARATE_OVERTIME_DECLARATION'
    assert condition_decision(c,'NO_OVERTIME')['status']=='UNKNOWN'

def test_permitted_overtime_is_not_a_confirmed_ban():
    data,view=source();data['rules']['allow_overtime']=True
    c=build_plan_context(data,view)
    assert c['overtime']['policy']=='EXPLICITLY_PERMITTED'
    assert condition_decision(c,'NO_OVERTIME')['status']=='NOT_SATISFIED'

def test_query_summary_has_business_data_and_rule_source():
    data,view=source();text=business_summary(build_plan_context(data,view))
    for value in ['1 笔订单','1000','1200','2026-10-23 18:00','按期','禁止加班','计划规则','执行状态尚未接入']:
        assert value in text
    assert 'API' not in text

def test_late_summary_does_not_claim_global_infeasibility():
    data,view=source('P1-EARLY');text=business_summary(build_plan_context(data,view))
    assert '延期' in text and '2026-10-22 18:00' in text
    assert '未证明无解' in text

def test_uncomputed_draft_summary_never_borrows_baseline_results():
    data,_=source();c=build_plan_context(data,{'run':{'state':'NOT_RUN'}})
    text=business_summary(c)
    assert '尚未计算' in text and '暂无' in text
    assert '1200' not in text

def test_noop_due_change_uses_real_dates_and_timezone():
    data,_=source()
    assert due_change_is_noop(data,{'demand_id':'SO-101-L1','shift_days':0,'due_at':None})
    assert due_change_is_noop(data,{'demand_id':'SO-101-L1','shift_days':None,'due_at':'2026-10-23T10:00:00Z'})
    assert not due_change_is_noop(data,{'demand_id':'SO-101-L1','shift_days':-2,'due_at':None})

def test_unsupported_explanation_does_not_deny_existing_compute():
    text=unsupported_message('SHIFT_CHANGE')
    assert '班次' in text and '修改订单交期' in text and '新增急单' in text and '运行' in text
    assert '不能执行排程计算' not in text
