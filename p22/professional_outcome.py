"""Non-executive inquiry assessment from same-run, authenticated native events."""
import json
from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

def number(value):return format(Decimal(str(value)).normalize(),'f')


def assessment_eligible(value):
    return (value.get('status')=='AUDITED' and value.get('business_audit')=='PASS'
        and value.get('original_commitments',{}).get('status')=='PASS')


def baseline_eligible(view):
    evidence=view.get('interpretation',{})
    return (view.get('run',{}).get('state')=='SUCCEEDED' and
        view.get('result',{}).get('assessment',{}).get('constraint_check')=='PASS' and
        evidence.get('foundation_validation',{}).get('status')=='PASS' and
        evidence.get('mapping_check')=='PASS' and evidence.get('schedule_check')=='PASS')


def commitment_protection(original_demands,base_events,candidate_events,timezone):
    """Preserve every prior cumulative delivery commitment, not just totals.

    The caller authenticates both run manifests. Missing tables stay unknown;
    new supply or an earlier aggregate end date cannot mask a lost early batch.
    """
    if not isinstance(base_events,list) or not isinstance(candidate_events,list):
        return dict(status='NOT_CHECKED',issues=[],missing_evidence=['native delivery tables'],impacts=[])
    zone=ZoneInfo(timezone)
    def at(v):
        d=datetime.fromisoformat(v.replace('Z','+00:00'))
        return d.replace(tzinfo=zone) if d.tzinfo is None else d.astimezone(zone)
    def index(events):
        result=defaultdict(list);seen=set()
        for e in events:
            if not isinstance(e,dict) or str(e.get('reference','')) in seen:raise ValueError('duplicate delivery reference')
            seen.add(str(e['reference']));q=Decimal(str(e['quantity']))
            if not q.is_finite() or q<0:raise ValueError('invalid native quantity')
            result[e['demand']].append((at(e['enddate']),q))
        return result
    try:
        old,new=index(base_events),index(candidate_events);issues=[];impacts=[]
        for d in original_demands:
            ref=d['id'];a,b=old[ref],new[ref]
            for when in sorted({t for t,q in a}):
                required=sum((q for t,q in a if t<=when),Decimal(0))
                actual=sum((q for t,q in b if t<=when),Decimal(0))
                if actual<required:issues.append(dict(code='EXISTING_COMMITMENT_WORSENED',demand_id=ref,by=when.isoformat(),base=str(required),candidate=str(actual)))
            total=lambda x:sum((q for t,q in x),Decimal(0))
            due=at(d['due_at']);before_due=sum((q for t,q in a if t<=due),Decimal(0));after_due=sum((q for t,q in b if t<=due),Decimal(0))
            impacts.append(dict(demand_id=ref,uom=d['uom'],base_planned=number(total(a)),candidate_planned=number(total(b)),
                delta=number(total(b)-total(a)),base_by_due=number(before_due),candidate_by_due=number(after_due),
                base_events=[dict(at=t.isoformat(),quantity=str(q)) for t,q in sorted(a)],
                candidate_events=[dict(at=t.isoformat(),quantity=str(q)) for t,q in sorted(b)]))
        return dict(status='FAIL' if issues else 'PASS',issues=issues,missing_evidence=[],impacts=impacts)
    except (KeyError,TypeError,ValueError,ArithmeticError):
        return dict(status='NOT_CHECKED',issues=[],missing_evidence=['complete unique dated native deliveries'],impacts=[])


def outcome(service,data,draft,base,view):
    from service import artifact_root
    from p22.professional_policy import verify
    verify(data,draft)
    base_id=base['run']['solve_run_id'];candidate_id=view['run']['solve_run_id']
    def raw(ref):return json.loads((artifact_root()/ref/'raw/deliveryorder.json').read_text('utf-8'))
    # Both PlanningService.view calls authenticate their full run manifests
    # before this function reads these immutable run members.
    protection=commitment_protection(data['foundation_projection']['source_tables']['demand_lines'],raw(base_id),raw(candidate_id),data['site']['timezone'])
    changes=draft['changes'];request=(changes.get('inquiry') or {}).get('request')
    audit=view.get('interpretation',{}).get('foundation_validation',{})
    baseline_audit=base.get('interpretation',{}).get('foundation_validation',{}).get('status','NOT_CHECKED')
    valid=audit.get('status')=='PASS' and view.get('interpretation',{}).get('schedule_check')=='PASS' and baseline_eligible(base)
    value=dict(status='AUDITED' if valid else 'VALIDATION_INCOMPLETE',business_audit=audit.get('status','NOT_CHECKED'),
        baseline_business_audit=baseline_audit,original_commitments=protection,baseline_run_id=base_id,candidate_run_id=candidate_id,
        data_version_id=service.enterprise['data_version_id'],plan_id=service.enterprise['plan_id'],
        atp_status='CONDITIONAL_SUPPLY_ONLY',atp_quantity=None,formal_promise=False,
        simulation_only=True,execution_authorized=False,search_exhaustive=False)
    if request:
        events=[e for e in raw(candidate_id) if e['demand']==request['id']]
        zone=ZoneInfo(data['site']['timezone'])
        def when(v):
            d=datetime.fromisoformat(v.replace('Z','+00:00'));return d.replace(tzinfo=zone) if d.tzinfo is None else d.astimezone(zone)
        search=request.get('search');original_due=search['requested_due_at'] if search else request['due_at']
        due=when(request['due_at']);qty=sum((Decimal(str(e['quantity'])) for e in events if when(e['enddate'])<=due),Decimal(0))
        originally_due=sum((Decimal(str(e['quantity'])) for e in events if when(e['enddate'])<=when(original_due)),Decimal(0))
        requested=Decimal(request['quantity'])
        if not valid or protection['status']!='PASS':status='REJECTED_BY_AUDIT_OR_COMMITMENT_PROTECTION'
        elif qty==requested:status='NEEDS_LATER_DATE' if originally_due<requested else 'CAN_PLAN_FULL'
        elif 0<qty<requested:status='CAN_PLAN_PARTIAL'
        else:status='NO_FEASIBLE_SOLUTION_FOUND'
        value['inquiry']=dict(status=status,requested_quantity=number(requested),planned_by_requested_due=number(originally_due),
            planned_by_evaluated_due=number(qty),unscheduled_quantity=number(requested-qty),uom=request['uom'],
            due_at=original_due,evaluated_due_at=request['due_at'],delivery_events=events,
            search=dict(attempt=search['attempt'] if search else 1,budget=3,exhaustive=False),
            usable_as_recommendation=valid and protection['status']=='PASS' and qty>0)
    value['purchase_comparison']=dict(base=base['result']['purchase_suggestions'],candidate=view['result']['purchase_suggestions'])
    if data.get('distribution_lanes'):
        from planning_analysis import joint_material_investigation
        def supply_snapshot(model,current):
            root=artifact_root()/current['run']['solve_run_id']/'raw'
            native={name:json.loads((root/(name+'.json')).read_text('utf-8')) for name in ('fixed_batch_post','operationplanmaterial','purchaseorder','distributionorder')}
            investigated=joint_material_investigation(model,native,current)
            return dict(run_id=current['run']['solve_run_id'],stock=[{k:r[k] for k in ('item_id','location_id','uom','minimum_available','end_available')} for r in investigated['objects']],
                purchases=native['purchaseorder'],distribution=native['distributionorder'],operations=current['result']['operations'])
        value['supply_comparison']=dict(base=supply_snapshot(data,base),candidate=supply_snapshot(draft['input'],view),
            source_prices=[{k:s[k] for k in ('id','item_id','location_id','supplier_id','unit_price','currency','price_basis')} for s in data['supply_sources']],
            transport_cost=None,cost_status='MODEL_SOURCE_PRICES_ONLY_TRANSPORT_NOT_EVIDENCED',input_changes=changes.get('supply'))
    value['netting']=changes.get('netting');value['replenishment']=changes.get('replenishment')
    return value
