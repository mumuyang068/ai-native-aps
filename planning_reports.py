"""Bounded, read-only period reports derived from authenticated native events."""
import copy
from collections import defaultdict
from datetime import datetime,timedelta,timezone
from decimal import Decimal
from zoneinfo import ZoneInfo
from planning_adapter import InputError

READY={'PRESENT','EMPTY_CHECKED'}
def q(v):
    value=Decimal(str(v))
    if not value.is_finite():raise ValueError('Nonfinite quantity')
    return value
def text(v):return format(v.normalize(),'f')
def at(v,z):
    t=datetime.fromisoformat(str(v).replace('Z','+00:00'))
    return t.replace(tzinfo=z) if t.tzinfo is None else t.astimezone(z)


def foundation_firm_sources(data):
    """Classify authenticated Firm scopes, never retained forecasts."""
    from p22.foundation_projection import validate
    meta=data.get('foundation_projection',{})
    policy=meta.get('policy',{})
    continuity=(policy.get('profile')=='continuity-current-state-v1'
                and policy.get('demand_scope')=='VISIBLE_GROSS_MINUS_SHIPPED')
    if (meta.get('schema_revision')!='foundation-planning-projection-v1'
            or (not continuity and (policy.get('demand_scope')!='ALL_24_FIRM_DEMANDS'
                or policy.get('forecast_policy')!='PRESERVE_PENDING_NOT_NETTED_OR_ADDED'))):
        return {}
    try:
        validate(data)
        source=meta['source_tables']['demand_lines']
        lookup={r['id']:(i,r) for i,r in enumerate(source)}
        if (not continuity and len(source)!=24) or len(lookup)!=len(source):return {}
        for demand in data['demand_lines']:
            if demand['id'] not in lookup:
                if 'professional_overlay' in meta:continue
                return {}
            original=lookup[demand['id']][1]
            if any(demand.get(key)!=original.get(key) for key in ('item_id','location_id','uom','customer_id','due_at')):return {}
            if q(demand['quantity'])!=q(original['quantity']):return {}
        return lookup
    except (InputError,KeyError,TypeError,ValueError,ArithmeticError):
        return {}

def demand_basis(data,result,raw,hashes):
    """Keep due facts when timed evidence is missing; never date an aggregate.

    Native deliveries need exact demand identity, unique references and source
    result reconciliation before any period fulfillment total is certified.
    """
    rows=raw.get('deliveryorder');zone=ZoneInfo(data['site']['timezone'])
    issues=[];missing=[];lines=[];events=defaultdict(list);totals=defaultdict(lambda:q(0))
    required=('canonical_input.json','PlanResult.json','raw/deliveryorder.json')
    missing.extend(name for name in required if not hashes.get(name))
    if not isinstance(rows,list):
        missing.append('raw/deliveryorder.json');rows=[]
    demands={d['id']:d for d in data['demand_lines']};items={i['id']:i for i in data['items']}
    foundation=data.get('foundation_projection',{}).get('schema_revision')=='foundation-planning-projection-v1'
    firm_sources=foundation_firm_sources(data) if foundation else {}
    if len(demands)!=len(data['demand_lines']):issues.append({'code':'DEMAND_ID_DUPLICATE'})
    references=set()
    for index,e in enumerate(rows):
        try:
            if not isinstance(e,dict) or e.get('reference') is None or not str(e['reference']).strip():raise ValueError()
            ref=str(e['reference']);quantity=q(e['quantity']);when=at(e['enddate'],zone).isoformat()
        except (KeyError,TypeError,ValueError,ArithmeticError):
            issues.append({'code':'DELIVERY_EVENT_INVALID','pointer':'/'+str(index)});continue
        if ref in references:
            issues.append({'code':'DELIVERY_REFERENCE_DUPLICATE','reference':ref});continue
        references.add(ref)
        if e.get('demand') not in demands:
            issues.append({'code':'DELIVERY_DEMAND_MISMATCH','reference':ref});continue
        d=demands[e['demand']]
        if (quantity<0 or (e.get('item'),e.get('location'))!=(d['item_id'],d['location_id'])
            or ('uom' in e and e['uom']!=d['uom'])):
            issues.append({'code':'DELIVERY_SCOPE_MISMATCH','reference':ref});continue
        totals[d['id']]+=quantity
        events[d['id']].append({'at':when,'quantity':text(quantity),'reference':ref,
            'source':{'artifact':'raw/deliveryorder.json','pointer':'/'+str(index),'sha256':hashes.get('raw/deliveryorder.json')}})
    assessment={}
    for index,r in enumerate(result.get('deliveries',[])):
        if r.get('demand_id') not in demands:
            issues.append({'code':'DELIVERY_RESULT_DEMAND_MISMATCH','demand_id':r.get('demand_id')});continue
        if r['demand_id'] in assessment:
            issues.append({'code':'DELIVERY_RESULT_DUPLICATE','demand_id':r['demand_id']});continue
        assessment[r['demand_id']]=(index,r)
    for index,d in enumerate(data['demand_lines']):
        pair=assessment.get(d['id']);ri,r=pair if pair is not None else (None,{})
        known=pair is not None and bool(hashes.get('PlanResult.json'))
        if pair is None:missing.append('PlanResult.json#/deliveries/'+d['id'])
        if pair is not None:
            try:
                requested=q(d['quantity']);planned=q(r['planned_quantity']);short=q(r['unfulfilled_quantity'])
                if (min(requested,planned,short)<0 or planned+short!=requested
                    or q(r.get('requested_quantity',requested))!=requested or r.get('uom',d['uom'])!=d['uom']
                    or (r.get('due_at') and at(r['due_at'],zone)!=at(d['due_at'],zone))):raise ValueError()
            except (KeyError,TypeError,ValueError,ArithmeticError):
                issues.append({'code':'DELIVERY_RESULT_SCOPE_MISMATCH','demand_id':d['id']});known=False
            if known and 'raw/deliveryorder.json' not in missing and totals[d['id']]!=planned:
                issues.append({'code':'DELIVERY_QUANTITY_RECONCILIATION','demand_id':d['id']})
        sources=[{'artifact':'canonical_input.json','pointer':'/demand_lines/'+str(index),'sha256':hashes.get('canonical_input.json')}]
        if ri is not None:sources.append({'artifact':'PlanResult.json','pointer':'/deliveries/'+str(ri),'sha256':hashes.get('PlanResult.json')})
        if d['id'] in firm_sources:
            sources.append({'artifact':'canonical_input.json',
                'pointer':'/foundation_projection/source_tables/demand_lines/'+str(firm_sources[d['id']][0]),
                'sha256':hashes.get('canonical_input.json')})
        lines.append({**r,'demand_id':d['id'],'requested_quantity':text(q(d['quantity'])),'due_at':at(d['due_at'],zone).isoformat(),
            'planned_quantity':r.get('planned_quantity') if known else None,'unfulfilled_quantity':r.get('unfulfilled_quantity') if known else None,
            'planned_delivery_at':r.get('planned_delivery_at') if known else None,'uom':d['uom'],
            'item_id':d['item_id'],'location_id':d['location_id'],'order_ref':firm_sources[d['id']][1].get('order_ref') if d['id'] in firm_sources else d.get('order_ref'),'endpoint':items[d['item_id']].get('name',d['item_id']),
            'demand_kind':('FIRM_ORDER' if d['id'] in firm_sources else d.get('kind')) if foundation else d.get('kind'),'delivery_events':events[d['id']],
            'unfulfilled_delivery_at':None,'unscheduled_quantity':r.get('unfulfilled_quantity') if known else None,'sources':sources})
    hard=result.get('assessment',{}).get('constraint_check')=='FAIL'
    missing=list(dict.fromkeys(missing))
    state='INVALID_EVIDENCE' if issues else 'NOT_COLLECTED' if missing else 'PRESENT' if rows else 'EMPTY_CHECKED'
    for line in lines:
        line.update(delivery_evidence_status=(state if state not in READY else 'PRESENT' if line['delivery_events'] else 'EMPTY_CHECKED'),
            delivery_validation_status='FAIL' if issues or hard else 'NOT_CHECKED' if missing else 'PASS',
            cumulative_planned_quantity=text(totals[line['demand_id']]) if state in READY else None,
            unknown_delivery_quantity=line['planned_quantity'] if state not in READY else '0')
    due_state=('INVALID_EVIDENCE' if len(demands)!=len(data['demand_lines']) else
        ('PRESENT' if lines else 'EMPTY_CHECKED') if hashes.get('canonical_input.json') else 'NOT_COLLECTED')
    # Report presence describes demand facts, not whether the engine found a
    # delivery. An all-unplanned plan still has real due and backlog rows.
    report_state=state if state not in READY else 'PRESENT' if lines else 'EMPTY_CHECKED'
    return {'status':report_state,'delivery_evidence_status':state,'due_evidence_status':due_state,
        'lines':lines,'validation':{'status':'FAIL' if issues or hard else 'NOT_CHECKED' if missing else 'PASS',
            'issues':issues,'missing_evidence':missing},'semantics':'PLANNED_STAGE_FULFILLMENT_NOT_ACTUAL_SHIPMENT'}

def document_basis(data,result,raw=None,hashes=None,validation=None):
    """No new supplier or allocation facts: source objects or PlanResult only."""
    from confirmed_supply import receipt_windows
    items={i['id']:i for i in data['items']};sources={s['id']:s for s in data.get('supply_sources',[])};docs=[]
    for p in receipt_windows(data):
        original=next(x for x in data['existing_orders'] if x['id']==p['id'])
        docs.append({'id':'PO:'+p['id'],'kind':'PO','reference':p['id'],'item_id':p['item_id'],'location_id':original['location_id'],'quantity':original.get('remaining_quantity',p['quantity']),'uom':original['uom'],
            'ordered_quantity':original.get('ordered_quantity',p['quantity']),'received_quantity':original.get('received_quantity'),
            'remaining_quantity':original.get('remaining_quantity',p['quantity']),
            'receipts':[copy.deepcopy(r) for r in data.get('purchase_receipts',[]) if r.get('po_id')==p['id']],
            'status':original['source_status'],'origin':'EXISTING','at':p['physical_receipt_at'],'available_at':p['available_at'],'start_at':original.get('ordered_at'),'source_id':original['supply_source_id'],'supplier_id':p['supplier_id']})
    for kind,key in [('PO','purchase_suggestions'),('MO','manufacture_suggestions'),('MO','existing_manufacturing_orders'),('DO','distribution_suggestions')]:
        for r in result.get(key,[]):
            docs.append({'id':r['id'],'kind':kind,'reference':r.get('source_reference',r.get('engine_ref',r.get('external_ref',r['id']))),'item_id':r['item_id'],'location_id':r.get('location_id',r.get('destination_location_id')),
                'quantity':r['quantity'],'uom':r.get('uom',items[r['item_id']]['base_uom']),'status':r.get('source_status',r.get('status','proposed')),'origin':r.get('origin','PROPOSED'),
                'at':r.get('physical_receipt_at',r.get('receipt_at',r.get('end_at',r.get('arrival_at')))),
                'available_at':r.get('available_at'),'start_at':r.get('start_at',r.get('departure_at',r.get('ship_at'))),'source_id':r.get('supply_source_id'),'supplier_id':r.get('supplier_id'),
                **({'origin_location_id':r.get('origin_location_id'),'lane_id':r.get('lane_id')} if kind=='DO' else {}),
                'operation_task_ids':[o['id'] for o in result.get('operations',[]) if o['manufacture_id']==r['id']],
                'source':{'artifact':'PlanResult.json','pointer':'/'+key+'/'+str(result[key].index(r))}})
    component={'status':'PRESENT' if docs else 'EMPTY_CHECKED','documents':docs,'allocation_scope':'RUN_DOCUMENTS_NOT_ORDER_ALLOCATION'}
    if data.get('foundation_projection',{}).get('schema_revision')=='foundation-planning-projection-v1':
        import hashlib,re
        from p22.foundation_projection import validate as validate_projection,digest
        raw=raw or {};hashes=hashes or {}
        checked=copy.deepcopy(validation) if validation is not None else {
            'status':'NOT_CHECKED','issues':[],'missing_evidence':['combined_enterprise_native_business_audit']}
        missing=[name for name in ('canonical_input.json','PlanResult.json','raw/purchaseorder.json','raw/operationplanmaterial.json') if not hashes.get(name)]
        if not isinstance(raw.get('purchaseorder'),list):missing.append('raw/purchaseorder.json')
        if not isinstance(raw.get('operationplanmaterial'),list):missing.append('raw/operationplanmaterial.json')
        if missing:
            checked.setdefault('missing_evidence',[]).extend(missing)
            if checked['status']!='FAIL':checked['status']='NOT_CHECKED'
        for doc in docs:
            if doc['kind']=='PO' and doc['origin']=='PROPOSED':
                doc['source_selection']={'status':'NOT_CHECKED','reason':'Authenticated exact native source selection is required'}
                # This is a read projection over immutable PlanResult/raw; it
                # neither writes a source ID into PlanResult nor selects a PO.
                doc['start_at']=next((p.get('order_at') for p in result.get('purchase_suggestions',[]) if p['id']==doc['id']),None)
        def selected_source(doc,purchase,pi):
            validate_projection(data)
            names=('canonical_input.json','PlanResult.json','raw/purchaseorder.json','raw/operationplanresource.json','raw/fixed_batch_post.json','raw/fixed_batch_post.xml','raw/operationplanmaterial.json')
            if any(not re.fullmatch('[a-f0-9]{64}',hashes.get(name,'')) for name in names):raise ValueError('Source selection SHA evidence is missing')
            capture=raw.get('fixed_batch_post',{});xml=raw.get('fixed_batch_post_xml')
            if (capture.get('run_id')!=result['solve_run_id'] or capture.get('input_hash')!=result['input_hash'] or capture.get('phase')!='post'
                    or result['input_hash']!=digest(data) or result['scenario_id']!=data['scenario_id']):raise ValueError('Source selection run/input scope differs')
            if (not isinstance(xml,str) or hashlib.sha256(xml.encode('utf-8')).hexdigest()!=hashes['raw/fixed_batch_post.xml']
                    or capture.get('native_xml_sha256')!=hashes['raw/fixed_batch_post.xml']):raise ValueError('Authenticated native XML is missing')
            original=[(i,p) for i,p in enumerate(result.get('purchase_suggestions',[])) if p['id']==doc['id']]
            if len(original)!=1:raise ValueError('Original proposed PO is not unique')
            oi,original=original[0]
            if (doc['id']!=result['solve_run_id']+':PO:'+str(purchase['reference']) or original.get('engine_ref')!=str(purchase['reference'])
                    or any(original.get(k)!=doc.get(k) for k in ('item_id','supplier_id','location_id','uom'))
                    or any(doc.get(k)!=purchase.get(v) for k,v in [('item_id','item'),('supplier_id','supplier'),('location_id','location')])
                    or q(original['quantity'])!=q(purchase['quantity']) or q(doc['quantity'])!=q(purchase['quantity'])):raise ValueError('Proposed PO identity/quantity differs')
            loads=raw.get('operationplanresource')
            if not isinstance(loads,list):raise ValueError('Native source capacity loads are missing')
            matched=[(i,r) for i,r in enumerate(loads) if str(r.get('operationplan'))==str(purchase['reference']) and q(r['quantity'])>0]
            if len(matched)!=1:raise ValueError('Native source capacity load is missing or ambiguous')
            li,load=matched[0];paths=capture.get('foundation_model',{}).get('supply_paths')
            if not isinstance(paths,list):raise ValueError('Captured explicit source paths are missing')
            matched=[(i,p) for i,p in enumerate(paths) if p.get('capacity_resource')==load.get('resource')]
            if len(matched)!=1:raise ValueError('Captured source capacity mapping is missing or ambiguous')
            ci,path=matched[0]
            if sum(p.get('source_id')==path.get('source_id') for p in paths)!=1:raise ValueError('Captured source identity is ambiguous')
            source_rows=[(i,s) for i,s in enumerate(data['supply_sources']) if s['id']==path.get('source_id')]
            if len(source_rows)!=1:raise ValueError('Canonical source identity is missing or ambiguous')
            si,supply=source_rows[0];zone=ZoneInfo(data['site']['timezone'])
            if (any(purchase.get(k)!=supply.get(v) for k,v in [('item','item_id'),('location','location_id'),('supplier','supplier_id')])
                    or path.get('supplier')!=supply['supplier_id'] or path.get('effectivity_evidence')!='NATIVE_XML_ITEMSUPPLIER'
                    or q(path['capacity_per_unit'])<=0 or q(load['quantity'])!=q(purchase['quantity'])*q(path['capacity_per_unit'])
                    or at(load['startdate'],zone)!=at(purchase['startdate'],zone) or at(load['enddate'],zone)!=at(purchase['enddate'],zone)
                    or at(original['order_at'],zone)!=at(purchase['startdate'],zone) or at(original['receipt_at'],zone)!=at(purchase['enddate'],zone)
                    or at(path['effective_start'],zone)!=at(supply['effective_from'],zone) or at(path['effective_end'],zone)!=at(supply['effective_to'],zone)
                    or not (at(supply['effective_from'],zone)<=at(purchase['startdate'],zone)<at(supply['effective_to'],zone))):raise ValueError('Native source scope/quantity/effectivity differs')
            if any(q(path[key])!=q(expected) for key,expected in [('lead_time_seconds',supply['lead_time_seconds']),('qc_seconds',supply['receiving_qc']['wait_seconds']),('minimum',supply['minimum_order_quantity']),('multiple',supply['order_multiple'])]):raise ValueError('Native source terms differ')
            if doc['uom']!=items[doc['item_id']]['base_uom']:raise ValueError('Proposed PO unit differs')
            sources=[{'artifact':name,'pointer':pointer,'sha256':hashes[name]} for name,pointer in [
                ('canonical_input.json','/supply_sources/'+str(si)),('PlanResult.json','/purchase_suggestions/'+str(oi)),
                ('raw/purchaseorder.json','/'+str(pi)),('raw/operationplanresource.json','/'+str(li)),
                ('raw/fixed_batch_post.json','/foundation_model/supply_paths/'+str(ci)),('raw/fixed_batch_post.xml','/')]]
            sources += [copy.deepcopy(s) for s in doc['sources'] if s['artifact']=='raw/operationplanmaterial.json']
            return {'schema_revision':'foundation-purchase-source-proof-v1','status':'PRESENT',
                'scope':{'run_id':result['solve_run_id'],'data_version_id':data['foundation_projection']['source_version_id'],
                    'profile':data['foundation_projection']['schema_revision'],'scenario_id':result['scenario_id'],
                    'scenario_revision':result['scenario_revision'],'input_hash':result['input_hash'],'result_hash':result['result_hash']},
                'purchase':{k:copy.deepcopy(doc[k]) for k in ('id','reference','item_id','location_id','supplier_id','quantity','uom','start_at','physical_receipt_at','available_at')},
                'source_id':supply['id'],'capacity_resource':load['resource'],'native_load_id':str(load['id']),'sources':sources}
        if checked['status']=='PASS':
            zone=ZoneInfo(data['site']['timezone'])
            for doc in docs:
                if doc['kind']!='PO':continue
                purchases=[(i,r) for i,r in enumerate(raw['purchaseorder']) if str(r['reference'])==doc['reference']]
                flows=[(i,r) for i,r in enumerate(raw['operationplanmaterial'])
                       if str(r['operationplan'])==doc['reference'] and q(r['quantity'])>0
                       and (r['item'],r['location'])==(doc['item_id'],doc['location_id'])]
                if len(purchases)!=1 or len(flows)!=1:
                    checked['status']='NOT_CHECKED'
                    checked.setdefault('missing_evidence',[]).append('purchase.'+doc['reference']+'.exact_native_availability')
                    continue
                pi,purchase=purchases[0];fi,flow=flows[0]
                physical=at(purchase['enddate'],zone).isoformat();available=at(flow['flowdate'],zone).isoformat()
                doc.update(at=physical,physical_receipt_at=physical,available_at=available,
                    time_basis='PLANNED_NATIVE_EVENTS_NOT_ACTUAL_RECEIPT_OR_QC_RELEASE',
                    sources=[{'artifact':'raw/purchaseorder.json','pointer':'/'+str(pi),'sha256':hashes['raw/purchaseorder.json']},
                             {'artifact':'raw/operationplanmaterial.json','pointer':'/'+str(fi),'sha256':hashes['raw/operationplanmaterial.json']}])
                if doc['origin']=='PROPOSED':
                    try:
                        proof=selected_source(doc,purchase,pi)
                        doc.update(source_id=proof['source_id'],source_selection=proof)
                    except (InputError,KeyError,TypeError,ValueError,ArithmeticError,AttributeError) as error:
                        doc.update(source_id=None,source_selection={'status':'NOT_CHECKED','reason':str(error)})
        component['validation']=checked
        if checked['status']!='PASS':component['status']='INVALID_EVIDENCE' if checked['status']=='FAIL' else 'NOT_COLLECTED'
    for doc in docs:
        if doc['kind']!='PO':continue
        source=sources.get(doc.get('source_id'))
        if not source:
            doc['source_terms']={'status':'NOT_COLLECTED','reason':'Exact selected source is not authenticated'}
            continue
        terms={k:copy.deepcopy(source.get(k)) for k in ('lead_time_seconds','lead_time_basis','minimum_order_quantity',
            'order_multiple','batch_window_seconds','capacity','unit_price','currency','price_basis','receiving_qc')}
        terms.update(status='PRESENT',source_id=source['id'],quantity_basis='DOCUMENT_PLANNED_QUANTITY_NOT_PERIOD_INCREMENT',
            model_value=text(q(doc['quantity'])*q(source['unit_price'])) if source.get('unit_price') is not None and source.get('currency') and source.get('price_basis') else None,
            sources=[{'artifact':'canonical_input.json','pointer':'/supply_sources/'+str(data['supply_sources'].index(source)),'sha256':(hashes or {}).get('canonical_input.json')}])
        doc['source_terms']=terms
    return component

def buckets(model,grain,start=None,end=None):
    if grain not in {'day','week','month'}:raise InputError('INVALID_REPORT_GRAIN: 仅支持业务时区日/周/月')
    z=ZoneInfo(model['timezone'])
    try:
        a=at(start or model['planning_at'],z);b=at(end or model['horizon_end'],z)
        if a>=b or a<at(model['planning_at'],z) or b>at(model['horizon_end'],z):raise ValueError()
    except (ValueError,TypeError):raise InputError('INVALID_REPORT_RANGE') from None
    result=[]
    while a<b:
        midnight=a.replace(hour=0,minute=0,second=0,microsecond=0)
        edge=(midnight.replace(year=midnight.year+1,month=1,day=1) if midnight.month==12 else midnight.replace(month=midnight.month+1,day=1)) if grain=='month' else midnight+timedelta(days=1 if grain=='day' else 7-midnight.weekday())
        nxt=min(edge,b);result.append((a,nxt));a=nxt
        if len(result)>740:raise InputError('INVALID_REPORT_RANGE: 报表期间过多')
    return result

def demand_periods(demand,periods,zone):
    """Timed metrics are nullable certificates, due facts have their own scope.

    Cumulative and outstanding values are snapshots at the exclusive bucket
    end, never additive across periods. Current unscheduled quantity comes
    from PlanResult; its missing delivery date is not a promised late date.
    """
    groups=defaultdict(list)
    for line in demand.get('lines',[]):
        groups[(line['item_id'],line['location_id'],line['uom'],line['endpoint'])].append(line)
    rows=[]
    def total(values):
        values=list(values)
        return None if any(v is None for v in values) else text(sum(values,q(0)))
    due_known=demand.get('due_evidence_status',demand['status']) in READY
    for key,lines in groups.items():
        for a,b in periods:
            components=[];all_metrics=[];references=[]
            for line in lines:
                due=at(line['due_at'],zone);requested=q(line['requested_quantity'])
                timed=(demand['status'] in READY and line.get('delivery_evidence_status',demand['status']) in READY)
                events=line.get('delivery_events',[])
                during=[e for e in events if a<=at(e['at'],zone)<b] if timed else []
                cumulative=sum((q(e['quantity']) for e in events if at(e['at'],zone)<b),q(0)) if timed else None
                outstanding=max(requested-cumulative,q(0)) if timed else None
                overdue=(outstanding if due<b else q(0)) if timed else None
                future=(outstanding if due>=b else q(0)) if timed else None
                unscheduled=q(line['unscheduled_quantity']) if line.get('unscheduled_quantity') is not None else None
                metrics={'due_quantity':requested if a<=due<b else q(0),
                    'cumulative_due_quantity':requested if due<b else q(0),
                    'planned_quantity':sum((q(e['quantity']) for e in during),q(0)) if timed else None,
                    'overdue_unfulfilled':overdue,'cumulative_planned_quantity':cumulative,
                    'not_yet_due_unfulfilled':future,'unscheduled_quantity':unscheduled}
                if not due_known:metrics.update(due_quantity=None,cumulative_due_quantity=None)
                all_metrics.append(metrics)
                active=(a<=due<b or bool(during) or overdue is None or overdue>0 or future is None or future>0)
                if active:
                    components.append({'demand_id':line['demand_id'],'demand_kind':line.get('demand_kind'),
                        **{name:None if value is None else text(value) for name,value in metrics.items()},
                        'delivery_evidence_status':(line.get('delivery_evidence_status',demand['status']) if demand['status'] in READY else demand['status']),
                        'due_evidence_status':demand.get('due_evidence_status',demand['status']),
                        'sources':copy.deepcopy(line.get('sources',[]))})
                references.extend(e['reference'] for e in during)
            rows.append(dict(zip(['item_id','location_id','uom','endpoint'],key),
                start_at=a.isoformat(),end_at=b.isoformat(),
                **{name:total(m[name] for m in all_metrics) for name in all_metrics[0]},
                kind_totals={kind:{name:total(metrics[name] for line,metrics in zip(lines,all_metrics) if (line.get('demand_kind') or 'UNCLASSIFIED')==kind) for name in all_metrics[0]}
                    for kind in dict.fromkeys(line.get('demand_kind') or 'UNCLASSIFIED' for line in lines)},
                demand_ids=[c['demand_id'] for c in components],event_references=references,
                demand_kinds=list(dict.fromkeys(c['demand_kind'] for c in components)),demand_components=components,
                delivery_evidence_status=(demand.get('delivery_evidence_status',demand['status']) if demand['status'] in READY else demand['status']),
                due_evidence_status=demand.get('due_evidence_status',demand['status'])))
    return rows

def delivery_performance(demand,start,end,zone,planning_at,horizon_end,known_business_fail=False):
    """Evaluate explicit Firm lines from the original complete-run event owner.

    The selected half-open window selects due lines, not delivery events. All
    authenticated events in the run are needed to distinguish late completion
    from a partial plan. This is bounded stage planning, never actual shipment.
    """
    states=('FULL_ON_TIME','FULL_LATE','PARTIALLY_PLANNED','NOT_PLANNED','UNKNOWN')
    a,b=at(start,zone),at(end,zone);first,last=at(planning_at,zone),at(horizon_end,zone)
    utc=lambda value:at(value,zone).astimezone(timezone.utc)
    groups={};issues=[];missing=[]
    coverage={'firm_line_count':0,'certified_line_count':0,'unknown_line_count':0,
              'total_run_firm_line_count':0,'unknown_due_line_count':0}
    excluded={'net_forecast_line_count':0,'unclassified_line_count':0,'outside_due_window_line_count':0}
    lines=demand.get('lines',[])
    hard=(known_business_fail or demand.get('validation',{}).get('status')=='FAIL'
          or any(line.get('delivery_validation_status')=='FAIL' for line in lines))
    invalid=demand.get('status')=='INVALID_EVIDENCE'
    ids=[line.get('demand_id') for line in lines]
    refs=[str(event.get('reference') or '') for line in lines
          for event in (line.get('delivery_events') if isinstance(line.get('delivery_events'),list) else []) if isinstance(event,dict)]
    if len(ids)!=len(set(ids)) or None in ids:
        issues.append({'code':'PERFORMANCE_DEMAND_ID_INVALID'});hard=invalid=True
    if len(refs)!=len(set(refs)) or any(ref is None or not str(ref).strip() for ref in refs):
        issues.append({'code':'PERFORMANCE_EVENT_REFERENCE_INVALID'});hard=invalid=True
    if hard:issues.append({'code':'BUSINESS_OR_DELIVERY_EVIDENCE_FAIL'})
    ready=(demand.get('status') in READY and demand.get('delivery_evidence_status') in READY
           and demand.get('due_evidence_status') in READY and demand.get('validation',{}).get('status')=='PASS')
    for line in lines:
        kind=line.get('demand_kind')
        if kind=='NET_FORECAST':excluded['net_forecast_line_count']+=1;continue
        if kind!='FIRM_ORDER':excluded['unclassified_line_count']+=1;continue
        coverage['total_run_firm_line_count']+=1
        reasons=[];due=None
        try:due=utc(line['due_at'])
        except (KeyError,TypeError,ValueError):reasons.append('DUE_TIME_MISSING_OR_INVALID')
        if due is not None and not utc(a)<=due<utc(b):
            excluded['outside_due_window_line_count']+=1;continue
        if due is None:coverage['unknown_due_line_count']+=1
        key=tuple(line.get(name) for name in ['item_id','location_id','uom','endpoint'])
        group=groups.setdefault(key,{**dict(zip(['item_id','location_id','uom','endpoint'],key)),
            'firm_line_count':0,'certified_line_count':0,'unknown_line_count':0,
            'counts':dict.fromkeys(states,0),'on_time_percent':None,'lines':[]})
        group['firm_line_count']+=1;coverage['firm_line_count']+=1
        if not all(isinstance(value,str) and value.strip() for value in key):reasons.append('BUSINESS_ENDPOINT_INCOMPLETE')
        if hard or line.get('delivery_validation_status')=='FAIL':reasons.append('KNOWN_BUSINESS_FAIL')
        if (not ready or line.get('delivery_evidence_status') not in READY
            or line.get('delivery_validation_status')!='PASS'):reasons.append('DELIVERY_COVERAGE_NOT_CERTIFIED')
        if due is not None and not utc(first)<=due<utc(last):reasons.append('DUE_OUTSIDE_CERTIFIED_PLAN_RANGE')
        sources=copy.deepcopy(line.get('sources',[]))
        for artifact in ['canonical_input.json','PlanResult.json']:
            if not any(source.get('artifact')==artifact and source.get('sha256') and source.get('pointer') for source in sources):
                reasons.append('LINE_SOURCE_MISSING')
        requested=planned=short=None;timed=[]
        try:
            requested=q(line['requested_quantity']);planned=q(line['planned_quantity']);short=q(line['unfulfilled_quantity'])
            if requested<=0 or min(planned,short)<0 or planned+short!=requested:raise ValueError()
        except (KeyError,TypeError,ValueError,ArithmeticError):reasons.append('LINE_QUANTITY_NOT_RECONCILED')
        events=line.get('delivery_events')
        if not isinstance(events,list):reasons.append('DELIVERY_EVENTS_MISSING');events=[]
        for event in events:
            try:
                when=utc(event['at']);quantity=q(event['quantity']);source=event.get('source',{})
                if quantity<0:raise ValueError()
                if (source.get('artifact')!='raw/deliveryorder.json' or not source.get('sha256') or not source.get('pointer')):
                    reasons.append('EVENT_SOURCE_MISSING')
                else:sources.append(copy.deepcopy(source))
                if not utc(first)<=when<utc(last):reasons.append('EVENT_OUTSIDE_CERTIFIED_PLAN_RANGE')
                timed.append((when,quantity))
            except (KeyError,TypeError,ValueError,ArithmeticError,AttributeError):reasons.append('DELIVERY_EVENT_INVALID')
        if planned is not None and sum((quantity for _,quantity in timed),q(0))!=planned:
            reasons.append('EVENT_QUANTITY_NOT_RECONCILED')
        classification='UNKNOWN';at_due=None;completion=None
        if not reasons:
            at_due=sum((quantity for when,quantity in timed if when<=due),q(0))
            cumulative=q(0)
            for when,quantity in sorted(timed,key=lambda event:event[0]):
                cumulative+=quantity
                if completion is None and cumulative>=requested:completion=when.astimezone(zone).isoformat()
            classification=('FULL_ON_TIME' if at_due>=requested else 'FULL_LATE' if planned>=requested
                            else 'PARTIALLY_PLANNED' if planned>0 else 'NOT_PLANNED')
            group['certified_line_count']+=1;coverage['certified_line_count']+=1
        else:
            group['unknown_line_count']+=1;coverage['unknown_line_count']+=1
            missing.append({'demand_id':line.get('demand_id'),'reason_codes':list(dict.fromkeys(reasons))})
        group['counts'][classification]+=1
        group['lines'].append({'demand_id':line.get('demand_id'),'order_ref':line.get('order_ref'),
            'demand_kind':kind,'due_at':line.get('due_at'),'requested_quantity':line.get('requested_quantity'),
            'planned_quantity':text(planned) if classification!='UNKNOWN' else None,
            'unfulfilled_quantity':text(short) if classification!='UNKNOWN' else None,
            'quantity_at_due':text(at_due) if at_due is not None else None,'classification':classification,
            'completion_at':completion,'reason_codes':list(dict.fromkeys(reasons)),'sources':sources})
    for group in groups.values():
        if group['firm_line_count'] and not group['unknown_line_count'] and not excluded['unclassified_line_count']:
            group['on_time_percent']=text(q(group['counts']['FULL_ON_TIME'])/group['firm_line_count']*100)
    unclassified=excluded['unclassified_line_count']>0
    status=('INVALID_EVIDENCE' if invalid else 'PRESENT' if groups else 'NOT_COLLECTED' if unclassified or not ready else 'EMPTY_CHECKED')
    return {'status':status,'validation':{'status':'FAIL' if hard else 'NOT_CHECKED' if missing or unclassified or not ready else 'PASS',
        'issues':issues,'missing_evidence':missing},'semantics':'PLANNED_STAGE_FULFILLMENT_NOT_ACTUAL_SHIPMENT',
        'window':{'start_at':a.isoformat(),'end_at':b.isoformat(),'selection':'DUE_IN_HALF_OPEN_WINDOW'},
        'certified_plan_range':{'start_at':first.isoformat(),'end_at':last.isoformat()},
        'due_boundary':'EVENT_AT_OR_BEFORE_DUE','groups':list(groups.values()),'coverage':coverage,'excluded':excluded}


def document_periods(documents,periods,zone):
    """Time buckets describe planned document events, not an executed receipt."""
    rows=[]
    for doc in documents:
        for a,b in periods:
            inside=lambda value:bool(value) and a<=at(value,zone)<b
            receipts=doc.get('receipts',[])
            physical=inside(doc.get('at'));available=inside(doc.get('available_at'));started=inside(doc.get('start_at'))
            actual=[r for r in receipts if inside(r.get('physical_receipt_at'))]
            qc=[r for r in receipts if r.get('quality_status')=='PENDING_QC' and inside(r.get('release_at'))]
            in_transit=doc['kind']=='DO' and doc.get('start_at') and doc.get('at') and at(doc['start_at'],zone)<b<=at(doc['at'],zone)
            if not any((physical,available,started,actual,qc,in_transit)):continue
            quantity=q(doc['quantity'])
            rows.append({**copy.deepcopy(doc),'id':str(doc['id'])+'|'+a.isoformat(),'document_id':doc['id'],
                'start_at':a.isoformat(),'end_at':b.isoformat(),'document_start_at':doc.get('start_at'),
                'planned_receipt_quantity':text(quantity if doc['kind']=='PO' and physical else q(0)),
                'qc_available_quantity':text(quantity if doc['kind']=='PO' and available else q(0)),
                'actual_received_in_period':text(sum((q(r['quantity']) for r in actual),q(0))),
                'expected_received_qc_release':text(sum((q(r['quantity']) for r in qc),q(0))),
                'ordered_in_period':text(quantity if doc['kind']=='PO' and started else q(0)),
                'dispatched_quantity':text(quantity if doc['kind']=='DO' and started else q(0)),
                'arrived_quantity':text(quantity if doc['kind']=='DO' and physical else q(0)),
                'in_transit_ending':text(quantity if in_transit else q(0)),
                'quantity_basis':'OUTSTANDING_PLANNED_SUPPLY_NOT_HISTORICAL_RECEIPT' if doc['kind']=='PO' else 'NATIVE_PLANNED_DOCUMENT',
                'sources':copy.deepcopy(doc.get('sources',[doc['source']] if doc.get('source') else []))})
    return rows

def period_reports(model,grain='day',start=None,end=None):
    periods=buckets(model,grain,start,end);z=ZoneInfo(model['timezone'])
    output={'identity':copy.deepcopy(model['identity']),'timezone':model['timezone'],'grain':grain,'range':{'start_at':periods[0][0].isoformat(),'end_at':periods[-1][1].isoformat()},
        'evidence_files':copy.deepcopy(model.get('evidence_files',[])),
        'known_business_fail':model.get('native_result_status',{}).get('constraint_check')=='FAIL' or any(model.get(k,{}).get('validation',{}).get('status')=='FAIL' for k in ['demand_analysis','resource_analysis','inventory_analysis']),
        'limits':['计划数量不是实际收发货或执行许可。','只按同物料/地点/单位与阶段终点归组，缺量没有推测日期。','每桶为[start,end)，期末指结束时点之前。']}
    d=copy.deepcopy(model.get('demand_analysis',{'status':'NOT_COLLECTED','lines':[]}));d['rows']=[]
    if d['status'] not in READY:d['delivery_evidence_status']=d['status']
    d['rows']=demand_periods(d,periods,z)
    d['delivery_performance']=delivery_performance(d,periods[0][0],periods[-1][1],z,
        model['planning_at'],model['horizon_end'],output['known_business_fail'])
    d['metric_semantics']={
        'planned_quantity':'NATIVE_DELIVERY_EVENTS_IN_HALF_OPEN_BUCKET_NOT_ACTUAL_SHIPMENT',
        'cumulative_due_quantity':'ALL_RUN_DEMANDS_DUE_BEFORE_EXCLUSIVE_BUCKET_END_NOT_ADDITIVE',
        'cumulative_planned_quantity':'PER_LINE_EVENTS_BEFORE_EXCLUSIVE_BUCKET_END_NOT_ADDITIVE',
        'overdue_unfulfilled':'SUM_PER_LINE_POSITIVE_DUE_MINUS_CUMULATIVE_WHEN_DUE_BEFORE_END',
        'not_yet_due_unfulfilled':'SUM_PER_LINE_REMAINING_WHEN_DUE_AT_OR_AFTER_END',
        'unscheduled_quantity':'CURRENT_RUN_PLANRESULT_SHORTFALL_WITHOUT_VERIFIED_DATE_NOT_ADDITIVE',
        'null':'NOT_CERTIFIED_NEVER_REPLACE_WITH_ZERO'}
    output['demand']=d
    ra=model['resource_analysis'];r={'status':ra['status'],'validation':copy.deepcopy(ra['validation']),'rows':[]}
    if ra['status'] in READY:
        def hours(x,y):return max(q(0),Decimal(str((y.astimezone(timezone.utc)-x.astimezone(timezone.utc)).total_seconds())))/3600
        for resource in ra['resources']:
            if resource['role']=='OPERATOR_POOL':continue
            for a,b in periods:
                # Calendars have already passed the owning resource auditor; merge
                # overlapping intervals to avoid counting a capacity minute twice.
                windows=sorted((max(a,at(w['start_at'],z)),min(b,at(w['end_at'],z))) for w in resource['availability'] if at(w['start_at'],z)<b and at(w['end_at'],z)>a)
                merged=[]
                for x,y in windows:
                    if merged and x<=merged[-1][1]:merged[-1]=(merged[-1][0],max(y,merged[-1][1]))
                    else:merged.append((x,y))
                calendar_known=resource.get('calendar_status') in READY
                available=sum((hours(x,y) for x,y in merged),q(0))*q(resource['capacity']) if calendar_known else None
                links=[x for x in ra['intervals'] if x['resource_id']==resource['id'] and at(x['end_at'],z)>a and at(x['start_at'],z)<b]
                load=sum((hours(max(at(x['start_at'],z),left),min(at(x['end_at'],z),right))*q(x['load_quantity']) for x in links for left,right in merged),q(0)) if calendar_known else None
                unavailable=hours(a,b)*q(resource['capacity'])-available if calendar_known else None
                r['rows'].append({'resource_id':resource['id'],'role':resource['role'],'start_at':a.isoformat(),'end_at':b.isoformat(),'available_hours':text(available) if available is not None else None,'load_hours':text(load) if load is not None else None,'unavailable_hours':text(unavailable) if unavailable is not None else None,'setup_hours':None,
                    'remaining_hours':text(available-load) if calendar_known else None,
                    'calendar_status':resource.get('calendar_status','NOT_COLLECTED'),'calendar_basis':resource.get('calendar_basis'),
                    'capacity_uom':'parallel_occupancy_hours','load_basis':'NATIVE_RESOURCE_INTERVAL_INTERSECT_DECLARED_CALENDAR',
                    'sources':copy.deepcopy(resource.get('calendar_sources',[])+resource.get('sources',[])),
                    'utilization_percent':text((load/available*100).quantize(Decimal('.000001'))) if available else None,'interval_ids':[x['id'] for x in links],'operation_task_ids':list(dict.fromkeys(x['operation_task_id'] for x in links if x['operation_task_id']))})
    r['excluded_resources']=copy.deepcopy(ra.get('excluded_resources',[]))
    output['resource']=r
    ia=model['inventory_analysis'];inv={'status':ia['status'],'validation':copy.deepcopy(ia['validation']),'rows':[],'transit_rows':[]}
    if ia['status'] in READY:
        for s in ia['series']:
            for a,b in periods:
                prior=[e for e in s['events'] if at(e['at'],z)<a];events=[e for e in s['events'] if a<=at(e['at'],z)<b]
                opening=q(prior[-1]['balance']) if prior else q(s['opening']);incoming=sum((q(e['quantity']) for e in events if q(e['quantity'])>0),q(0));outgoing=-sum((q(e['quantity']) for e in events if q(e['quantity'])<0),q(0))
                waiting=sum((q(e['quantity']) for e in s['events'] if e.get('physical_receipt_at') and at(e['physical_receipt_at'],z)<b<=at(e['available_at'],z)),q(0))
                transit=ia.get('transit',{});transit_value=None
                if transit.get('status') in READY:
                    timeline=[e for e in transit.get('timeline',[]) if at(e['at'],z)<b]
                    transit_value=timeline[-1]['planned_in_transit'] if timeline else '0'
                route_scope=transit.get('semantics')=='PLANNED_IN_TRANSIT_PER_ITEM_ROUTE_NOT_LOCATION_STOCK'
                inflows=dict.fromkeys(('PURCHASE_AVAILABLE','RECEIPT_QC_RELEASE','DISTRIBUTION_RECEIPT','MANUFACTURE_OUTPUT','OTHER'),q(0))
                outflows=dict.fromkeys(('DISTRIBUTION_DISPATCH','MANUFACTURE_CONSUMPTION','DEMAND_CONSUMPTION','OTHER'),q(0))
                for event in events:
                    value=q(event['quantity']);target=inflows if value>=0 else outflows
                    key=event['kind'] if event['kind'] in target else 'OTHER';target[key]+=abs(value)
                pending=s.get('inspection_pending_quantity')
                if pending is not None:pending=max(q(0),q(pending)-sum((q(e['quantity']) for e in s['events'] if e['kind']=='RECEIPT_QC_RELEASE' and at(e['at'],z)<b),q(0)))
                ending=opening+incoming-outgoing
                native=(events[-1] if events else prior[-1] if prior else {}).get('native_onhand')
                matched=native is None or q(native)==ending
                inv['rows'].append({'item_id':s['item_id'],'location_id':s['location_id'],'uom':s['uom'],'start_at':a.isoformat(),'end_at':b.isoformat(),'opening':text(opening),'inflow':text(incoming),'outflow':text(outgoing),'ending':text(ending),
                    'inflows':{k:text(v) for k,v in inflows.items()},'outflows':{k:text(v) for k,v in outflows.items()},
                    'reconciliation':{'status':'PASS' if matched and ia['validation']['status']=='PASS' else 'FAIL' if not matched or ia['validation']['status']=='FAIL' else 'NOT_CHECKED','native_ending':text(q(native)) if native is not None else None},
                    'receipt_qc_waiting_end':text(pending) if pending is not None else None,
                    'soft_targets':[copy.deepcopy(p) for p in ia.get('policy_observations',[]) if (p.get('item_id'),p.get('location_id'))==(s['item_id'],s['location_id']) and p.get('start_at') and p.get('end_at') and at(p['start_at'],z)<b and at(p['end_at'],z)>a],
                    'quality_blocked_opening':s['quality_blocked'],'physical_opening':s['physical_opening'],'qc_waiting_end':text(waiting),'planned_in_transit_end':None if route_scope else transit_value,'transit_scope':'SEPARATE_ROUTE_ROWS' if route_scope else 'LANE_TOTAL_NOT_LOCATION_STOCK','event_ids':[e['id'] for e in events]})
        transit=ia.get('transit',{})
        if transit.get('status') in READY:
            shipments=defaultdict(list)
            for doc in transit.get('shipments',[]):
                shipments[(doc['item'],doc['origin'],doc['destination'],doc.get('uom',transit.get('uom')))].append(doc)
            for key,docs in shipments.items():
                for a,b in periods:
                    state=lambda t:sum((q(d['quantity']) for d in docs if at(d['startdate'],z)<t<=at(d['enddate'],z)),q(0))
                    sent=[d for d in docs if a<=at(d['startdate'],z)<b];arrived=[d for d in docs if a<=at(d['enddate'],z)<b]
                    inv['transit_rows'].append({**dict(zip(('item_id','origin_location_id','destination_location_id','uom'),key)),
                        'start_at':a.isoformat(),'end_at':b.isoformat(),'in_transit_opening':text(state(a)),'in_transit_ending':text(state(b)),
                        'dispatched_quantity':text(sum((q(d['quantity']) for d in sent),q(0))),'arrived_quantity':text(sum((q(d['quantity']) for d in arrived),q(0))),
                        'references':[str(d['reference']) for d in docs if at(d['startdate'],z)<b and at(d['enddate'],z)>=a],
                        'semantics':'PLANNED_IN_TRANSIT_NOT_ADDED_TO_LOCATION_STOCK'})
    output['inventory']=inv
    docs=copy.deepcopy(model.get('document_analysis',{'status':'NOT_COLLECTED','documents':[]}));docs['summary']=[]
    # Preserve the original complete-run owner for alternate physical/QC queue
    # views. The existing document list and summaries keep their at-date window.
    docs['run_documents']=copy.deepcopy(docs['documents'])
    docs['period_rows']=document_periods(docs['run_documents'],periods,z)
    docs['documents']=[d for d in docs['documents'] if not d['at'] or periods[0][0]<=at(d['at'],z)<periods[-1][1]]
    totals=defaultdict(lambda:q(0))
    for d in docs['documents']:
        for a,b in periods:
            if d['at'] and a<=at(d['at'],z)<b:
                totals[(d['kind'],d['item_id'],d['location_id'],d['uom'],d['origin'],d['status'],a.isoformat(),b.isoformat())]+=q(d['quantity'])
    for key,v in totals.items():docs['summary'].append(dict(zip(['kind','item_id','location_id','uom','origin','status','start_at','end_at'],key),quantity=text(v)))
    output['documents']=docs
    from manufacturing_reports import manufacturing_periods,manufacturing_order_periods
    manufacturing=copy.deepcopy(model.get('manufacturing_analysis',{'status':'NOT_COLLECTED','validation':{'status':'NOT_CHECKED'},'tasks':[],'orders':[]}))
    manufacturing['rows']=manufacturing_periods(manufacturing,periods,model['timezone'])
    manufacturing['order_rows']=manufacturing_order_periods(manufacturing,periods,model['timezone'])
    output['manufacturing']=manufacturing
    for name,component in model.get('issue_analysis',{}).items():
        output[name]=copy.deepcopy(component)
        output[name]['rows']=[row for row in component.get('rows',[]) if (not row.get('start_at') or at(row['start_at'],z)<periods[-1][1]) and (not row.get('end_at') or at(row['end_at'],z)>=periods[0][0])]
    output.setdefault('constraints',{'status':'NOT_COLLECTED','rows':[]})
    output.setdefault('problems',{'status':'NOT_COLLECTED','rows':[],'relation_scope':'RUN_LEVEL_ONLY'})
    return output
