"""Read-only, identity-bound projections of authenticated native run events.

Reuses the product's resource and material auditors. No event persistence, solver,
allocation inference, or joins to the separate professional-data release.
"""
import copy
import hashlib
import json
from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from functools import lru_cache
from zoneinfo import ZoneInfo

from planning_adapter import InputError
from planning_reports import demand_basis,document_basis,period_reports
from planning_capabilities import supports,profile_id
from manufacturing_reports import manufacturing_basis

QUALIFIED='machine-qualified-operator-v1'
DISTRIBUTION='single-lane-distribution-v1'
NETWORK='network-distribution-confirmed-supply-v1'
WIP='mixed-fixed-batch-confirmed-supply-v1'
JOINT_QUALIFIED='mixed-fixed-batch-confirmed-qualified-v1'
FOUNDATION='foundation-planning-projection-v1'
TABLES=('fixed_batch_pre','fixed_batch_post','workorder','manufacturingorder',
        'operationplanresource','operationplanmaterial','purchaseorder',
        'distributionorder','deliveryorder','demand')


def joint_material_investigation(data,raw,view):
    """Read-only material/location projection of already authenticated run members.

    Native balances and order-evidence relations remain authoritative. This
    does not calculate another inventory ledger or allocate shared task flows.
    """
    from calculation_validation import rows
    zone=ZoneInfo(data['site']['timezone'])
    def at(v):
        value=datetime.fromisoformat(v.replace('Z','+00:00'))
        return value.replace(tzinfo=zone) if value.tzinfo is None else value.astimezone(zone)
    def read(name):
        value=raw.get(name)
        return rows(value) if isinstance(value,(dict,list)) else None
    run_id=view['run']['solve_run_id'];native=(raw.get('fixed_batch_post') or {}).get('buffers')
    flows=read('operationplanmaterial');purchases=read('purchaseorder');dos=read('distributionorder')
    def other_stock(source):
        row=copy.deepcopy(source)
        projected=next((p for p in data['inventory_positions'] if (p['item_id'],p['location_id'])==(row['item_id'],row['location_id'])),None)
        if projected is not None:
            from inventory_availability import opening_components
            opening=opening_components(data,projected)
            row.update(physical=opening['physical'],net_available=opening['available'])
        return row
    objects=[]
    wanted={(s['item_id'],s['location_id']) for s in data['supply_sources']}
    wanted|={(r['item_id'],r[k]) for r in data.get('distribution_lanes',[]) for k in ('origin_location_id','destination_location_id')}
    for stock in data['inventory_positions']:
        key=stock['item_id'],stock['location_id']
        if key not in wanted:continue
        matches=[b for b in native or [] if (b['item'],b['location'])==key]
        buffer=matches[0] if len(matches)==1 else None
        events=copy.deepcopy(buffer.get('events')) if buffer and isinstance(buffer.get('events'),list) else None
        balances=([Decimal(str(buffer['onhand']))]+[Decimal(str(e['onhand'])) for e in events if e.get('onhand') is not None and at(e['at'])>=at(data['planning_at'])]) if events is not None else None
        receipts=[copy.deepcopy(r) for r in data.get('purchase_receipts',[]) if (r['item_id'],r['location_id'])==key]
        operations={op['id']:op for op in view['result']['operations']}
        consumed=[dict(copy.deepcopy(f),operation_id=run_id+':OP:'+str(f['operationplan']),operation_code=operations[run_id+':OP:'+str(f['operationplan'])].get('operation_id')) for f in flows or [] if (f['item'],f['location'])==key and Decimal(str(f['quantity']))<0 and run_id+':OP:'+str(f['operationplan']) in operations]
        demands=[]
        for order in view.get('order_evidence',{}).get('orders',[]):
            if any((m['record'].get('item'),m['record'].get('location'))==key and Decimal(str(m['record']['quantity']))<0 for m in order.get('materials',[])):
                demands.append(dict(demand_id=order['demand_id'],order_ref=order['order_ref'],relation='NATIVE_DEMAND_PEGGING_VIA_SHARED_TASK',allocated_material_quantity=None))
        objects.append(dict(item_id=key[0],location_id=key[1],uom=stock['uom'],opening=copy.deepcopy(stock),
            minimum_available=format(min(balances),'f') if balances else None,end_available=format(balances[-1],'f') if balances else None,
            native_events=events,receipts=receipts,
            receipt_quantities={status:format(sum((Decimal(str(r['quantity'])) for r in receipts if r['quality_status']==status),Decimal(0)),'f') for status in ('RELEASED','PENDING_QC','FROZEN')},
            purchases=[copy.deepcopy(r) for r in purchases if (r['item'],r['location'])==key] if purchases is not None else None,
            confirmed_balances=[copy.deepcopy(p) for p in data['existing_orders'] if p['type']=='PO' and (p['item_id'],p['location_id'])==key],
            source_terms=[copy.deepcopy(s) for s in data['supply_sources'] if (s['item_id'],s['location_id'])==key],
            other_location_stock=[other_stock(s) for s in data['foundation_projection']['source_tables']['inventory_positions'] if s['item_id']==key[0] and s['location_id']!=key[1]],
            lanes=[copy.deepcopy(r) for r in data.get('distribution_lanes',[]) if r['item_id']==key[0] and key[1] in (r['origin_location_id'],r['destination_location_id'])],
            distribution=[copy.deepcopy(d) for d in dos if d['item']==key[0] and key[1] in (d['origin'],d['destination'])] if dos is not None else None,
            consumptions=consumed,demands=demands,transport_cost=None,cost_comparison_status='NO_EVIDENCED_TRANSPORT_COST',
            relation_limit='原生任务与需求关联，不等于共享物料分配或唯一缺料原因。'))
    return dict(run_id=run_id,objects=objects,execution_authorized=False,inventory_authority='NATIVE_SAME_RUN_BUFFER_EVENTS')


def quantity(value):
    n=Decimal(str(value))
    if not n.is_finite():raise ValueError('Nonfinite quantity')
    return format(n.normalize(),'f')


def clock(value,zone):
    t=datetime.fromisoformat(str(value).replace('Z','+00:00'))
    return t.replace(tzinfo=zone) if t.tzinfo is None else t.astimezone(zone)


def source(name,pointer,hashes):
    return {'artifact':name,'pointer':pointer,'sha256':hashes.get(name)}


def calendar_observation(data,resource,hashes):
    """Expose an input declaration, never claim a native calendar readback.

    Missing calendars are not silently interpreted as continuous availability.
    The canonical file is authenticated by read_analysis before projection.
    """
    calendar_id=resource.get('availability_calendar_id')
    matches=[(i,c) for i,c in enumerate(data.get('calendars',[])) if c.get('id')==calendar_id]
    value={'calendar_id':calendar_id,'calendar_basis':'DECLARED_CANONICAL_INPUT',
           'calendar_status':'NOT_COLLECTED','calendar_sources':[],'availability':[]}
    if not calendar_id:return value
    if len(matches)!=1:
        value['calendar_status']='INVALID_EVIDENCE'
        return value
    index,calendar=matches[0]
    value['availability']=calendar.get('available_intervals',[])
    value['calendar_sources']=[source('canonical_input.json','/calendars/'+str(index),hashes)]
    if hashes.get('canonical_input.json'):
        value['calendar_status']='PRESENT' if value['availability'] else 'EMPTY_CHECKED'
    return value


def component_status(validation,rows):
    return 'PRESENT' if rows else 'EMPTY_CHECKED' if validation['status']=='PASS' else 'NOT_COLLECTED'


def unsupported(reason):
    return {'status':'UNSUPPORTED','reason':reason,'validation':{'status':'NOT_CHECKED','issues':[],
            'missing_evidence':[]},'resources':[],'intervals':[],'series':[]}


def project_analysis(data,result,raw,hashes=None,components=None):
    """Pure projection; raw arrays retain native ordering, including same-time flows."""
    from qualified_operator import audit_resources
    from calculation_validation import material_validation
    from confirmed_supply import receipt_windows
    hashes=hashes or {};zone=ZoneInfo(data['site']['timezone']);profile=profile_id(data)
    wanted=lambda name:components is None or name in components
    foundation=profile==FOUNDATION
    at=lambda v:clock(v,zone).isoformat()
    t0=at(data['planning_at']);end=at(data['horizon']['end_at']);run=result['solve_run_id']
    post=raw.get('fixed_batch_post') or {};ops={str(v['id'].rsplit(':',1)[-1]):v for v in result.get('operations',[])}
    work={str(v['reference']):v for v in raw.get('workorder',[])}
    manufactures={v['id']:v for v in result.get('manufacture_suggestions',[])}
    from multi_receipt_supply import enabled as multi_receipt_enabled
    multi_receipt=multi_receipt_enabled(data)
    work_sources={str(v['reference']):('raw/workorder.json','/'+str(i)) for i,v in enumerate(raw.get('workorder',[]))}
    if multi_receipt or foundation:
        # Protected leaf MOs are genuine manufacturingorder records, not
        # routing workorder children. Never turn their absent child into qty0.
        existing={m['id'] for m in data['existing_orders'] if m['type']=='MO'}
        for i,row in enumerate(raw.get('manufacturingorder',[])):
            if foundation or str(row['reference']) in existing:
                work[str(row['reference'])]=row
                work_sources[str(row['reference'])]=('raw/manufacturingorder.json','/'+str(i))
        manufactures.update({m['id']:m for m in result.get('existing_manufacturing_orders',[])})
    hard_result=result.get('assessment',{}).get('constraint_check')=='FAIL'
    def adjudicate(audit):
        if hard_result:
            audit['status']='FAIL';audit['issues'].append({'code':'SOURCE_RESULT_HARD_CONSTRAINT_FAIL'})
        return audit
    combined=None
    if foundation:
        from p22.foundation_audit import audit
        combined=adjudicate(audit(data,raw,run_id=run))
    ra=unsupported('当前仅认证设备与具名操作员profile；数量窗口不换算为连续容量或利用率。')
    if wanted('resource_analysis') and supports(profile,'analyses','resources'):
        validation=copy.deepcopy(combined) if foundation else adjudicate(audit_resources(data,raw,run_id=run));intervals=[];resources=[]
        continuous={r['id'] for r in data['resources'] if r.get('capacity_model')!='WINDOW_QUANTITY'}
        for index,row in enumerate(raw.get('operationplanresource',[])):
            if row['resource'] not in continuous:continue
            ref=str(row['operationplan']);op=ops.get(ref);native=work.get(ref,{})
            intervals.append({'id':run+':LOAD:'+str(row['id']),'resource_id':row['resource'],
                'native_reference':ref,'operation_task_id':op.get('id') if op else None,
                'operation_id':op.get('operation_id') if op else native.get('operation'),
                'start_at':at(row['startdate']),'end_at':at(row['enddate']),
                'load_quantity':quantity(row['quantity']),'production_quantity':quantity(native['quantity']) if native.get('quantity') is not None else None,
                'production_uom':manufactures.get(op.get('manufacture_id') if op else None,{}).get('uom'),
                'order_status':native.get('status') if multi_receipt else row.get('status'),'native_record':row,
                **({'load_status':row.get('status'),'order_status_basis':'EXACT_NATIVE_OPERATION_REFERENCE'} if multi_receipt else {}),
                'sources':[source('raw/operationplanresource.json','/'+str(index),hashes)]+
                    ([source(*work_sources[ref],hashes)] if ref in work_sources else [])+
                    [source('raw/fixed_batch_post.json','/resources/'+str(ri)+'/loadplans/'+str(li),hashes)
                     for ri,nr in enumerate(post.get('resources',[])) if nr['name']==row['resource']
                     for li,lp in enumerate(nr.get('loadplans',[])) if str(lp['reference'])==ref]})
        intervals.sort(key=lambda r:(r['start_at'],r['end_at'],r['resource_id'],r['id']))
        native_resources={r['name']:r for r in post.get('resources',[])}
        for i,r in enumerate(data['resources']):
            if r['id'] not in continuous:continue
            linked=[x for x in intervals if x['resource_id']==r['id']];events=[];load=Decimal(0);peak=Decimal(0)
            for x in linked:events.extend([(x['start_at'],1,Decimal(x['load_quantity'])),(x['end_at'],0,-Decimal(x['load_quantity']))])
            for _,_,delta in sorted(events):load+=delta;peak=max(peak,load)
            observed=native_resources.get(r['id']);collected=observed is not None and isinstance(observed.get('loadplans'),list)
            resources.append({'id':r['id'],'role':r['role'],'pool_id':r.get('pool_id'),
                'capacity':quantity(r.get('parallel_units',1)),'capacity_uom':'parallel_occupancy',
                'capacity_model':r.get('capacity_model'),**calendar_observation(data,r,hashes),
                'event_status':('PRESENT' if linked else 'EMPTY_CHECKED') if collected else 'NOT_COLLECTED',
                'peak_load':quantity(peak) if collected else None,'utilization':None,
                'sources':[source('canonical_input.json','/resources/'+str(i),hashes),source('raw/fixed_batch_post.json','/qualified_model/resources',hashes)]})
        ra={'status':component_status(validation,intervals),'validation':validation,'resources':resources,'intervals':intervals,
            'excluded_resources':[{'id':r['id'],'capacity_model':r['capacity_model'],'reason':'数量窗口在原制造排程核对，不转换为并行负荷或容量小时。'} for r in data['resources'] if r['id'] not in continuous],
            'limits':['负荷由原生OperationPlanResource及内存LoadPlan正负事件核对，不从工序时间猜测。',
                      '资源池不是具名已分配人员；不计算无一致时间桶口径的利用率。']}
    ia=unsupported('当前仅认证单配送路径及确认采购QC/整批WIP的库存时间账。')
    if wanted('inventory_analysis') and supports(profile,'analyses','inventory'):
        validation=copy.deepcopy(combined) if foundation else adjudicate(material_validation(data,raw,run_id=run));series=[];initializations=[]
        stocks=defaultdict(list)
        for index,row in enumerate(data.get('inventory_positions',[])):
            stocks[(row['item_id'],row['location_id'],row.get('uom'))].append((index,row))
        items={i['id']:i for i in data['items']};purchases={str(r['reference']):r for r in raw.get('purchaseorder',[])}
        shipments={str(r['reference']):r for r in raw.get('distributionorder',[])}
        deliveries={str(r['reference']):r for r in raw.get('deliveryorder',[])}
        from enterprise_forecast import enabled as enterprise_enabled
        from inventory_availability import net_available
        supply={r['id']:r for r in receipt_windows(data)} if foundation or profile in {WIP,JOINT_QUALIFIED,NETWORK} or enterprise_enabled(data) else {}
        from accessory_supply import enabled as accessory_enabled
        accessory_purchases={}
        if accessory_enabled(data):
            from accessory_supply import audit as audit_accessory
            certificate=audit_accessory(data,raw,run_id=run)
            if certificate['status']=='PASS':
                accessory_purchases={p['reference']:p for p in certificate['summary']['purchase_orders']}
        material=raw.get('operationplanmaterial',[])
        projection_missing=[]
        def known(value):
            try:
                number=Decimal(str(value))
                return number if number.is_finite() else None
            except (ValueError,TypeError,ArithmeticError):return None
        def summed(members,read):
            try:values=[known(read(row)) for _,row in members]
            except (KeyError,TypeError,AttributeError,ArithmeticError):return None
            return sum(values,Decimal(0)) if all(v is not None for v in values) else None
        def declared_source(members):
            if len(members)==1:return copy.deepcopy(members[0][1].get('provenance'))
            return dict(basis='SUM_DECLARED_INVENTORY_POSITIONS',
                positions=[dict(position_id=row.get('id'),uom=row.get('uom'),
                    source=source('canonical_input.json','/inventory_positions/'+str(index),hashes),
                    provenance=copy.deepcopy(row.get('provenance'))) for index,row in members])
        for bi,b in enumerate(post.get('buffers',[])):
            key=b['item'],b['location'];uom=items.get(b['item'],{}).get('base_uom')
            members=stocks.get((*key,uom),[]);reasons=[]
            if any(k[:2]==key and k[2]!=uom for k in stocks):reasons.append('INVENTORY_UNIT_MISMATCH')
            physical=summed(members,lambda r:r.get('on_hand')) if members else None
            frozen=summed(members,lambda r:r.get('quality_blocked')) if members else None
            other=summed(members,lambda r:r.get('other_nonnettable')) if members else None
            # Missing optional quality evidence stays unknown in the display.
            # Net arithmetic retains the original net_available contract.
            pending=summed(members,lambda r:r.get('quality_availability',{}).get('inspection_pending_quantity')) if members else None
            opening=summed(members,net_available) if members else known(b.get('onhand'))
            receipt_opening=None;opening_sources=[source('canonical_input.json','/inventory_positions/'+str(i),hashes) for i,_ in members]
            receipts=[(i,r) for i,r in enumerate(data.get('purchase_receipts',[])) if (r['item_id'],r['location_id'])==key]
            if members and receipts and all(v is not None for v in (physical,frozen,other,opening)):
                from inventory_availability import opening_components
                # The listed cohorts are separate from ALL base lots. Add them
                # once to their summed base, never once per lot.
                pending_for_net=summed(members,lambda r:r.get('quality_availability',{}).get('inspection_pending_quantity','0'))
                aggregate=dict(item_id=key[0],location_id=key[1],uom=uom,on_hand=quantity(physical),
                    quality_blocked=quantity(frozen),other_nonnettable=quantity(other),
                    quality_availability=dict(inspection_pending_quantity=quantity(pending_for_net)))
                receipt_opening=opening_components(data,aggregate)
                physical=receipt_opening['physical'];frozen=receipt_opening['frozen'];other=receipt_opening['other'];opening=Decimal(receipt_opening['available'])
                pending=receipt_opening['pending']
                opening_sources.extend(source('canonical_input.json','/purchase_receipts/'+str(i),hashes) for i,_ in receipts)
            elif members and receipts:
                physical=frozen=other=pending=opening=None
            if opening is None:reasons.append('DECLARED_OPENING_UNKNOWN')
            native_opening=known(b.get('onhand'))
            if native_opening is None:reasons.append('NATIVE_OPENING_NOT_COLLECTED')
            elif opening is not None and abs(opening-native_opening)>Decimal('0.000001'):
                reasons.append('OPENING_BALANCE_UNRECONCILED')
            balance=opening;events=[];used=set()
            for index,e in enumerate(b.get('events',[])):
                when=at(e['at']);q=Decimal(e['quantity']);ref=str(e['reference'])
                if clock(when,zone)<clock(t0,zone) and ref==b['name']:
                    initializations.append({'item_id':b['item'],'location_id':b['location'],**e,
                        'meaning':'NATIVE_INITIALIZATION_NOT_ENTERPRISE_RECEIPT','included_as_new_inflow':False});continue
                if clock(when,zone)<clock(t0,zone):reasons.append('PRE_PLANNING_EVENT_BOUNDARY_UNKNOWN')
                if balance is not None:balance+=q
                native_balance=known(e.get('onhand'))
                if native_balance is None:reasons.append('NATIVE_EVENT_BALANCE_NOT_COLLECTED')
                elif balance is not None and abs(balance-native_balance)>Decimal('0.000001'):
                    reasons.append('EVENT_BALANCE_UNRECONCILED')
                matches=[(j,r) for j,r in enumerate(material) if j not in used and
                         (str(r['operationplan']),r['item'],r['location'],at(r['flowdate']),Decimal(r['quantity']),known(r.get('onhand')))==
                         (ref,b['item'],b['location'],when,q,native_balance)]
                j,r=matches[0] if matches else (None,{})
                if j is not None:used.add(j)
                op=ops.get(ref);po=purchases.get(ref);do=shipments.get(ref);dlvr=deliveries.get(ref)
                kind=('PURCHASE_AVAILABLE' if po else 'DISTRIBUTION_RECEIPT' if do and q>0 else 'DISTRIBUTION_DISPATCH' if do
                      else 'DEMAND_CONSUMPTION' if dlvr else 'MANUFACTURE_OUTPUT' if op and q>0 else 'MANUFACTURE_CONSUMPTION' if op else 'NATIVE_MATERIAL_EVENT')
                quality_release=next((receipt for receipt in data.get('purchase_receipts',[]) if ref=='QC:'+receipt['receipt_reference'] and receipt['quality_status']=='PENDING_QC' and receipt['release_at'] is not None),None)
                if quality_release is not None:kind='RECEIPT_QC_RELEASE'
                event={'id':run+':MATERIAL:'+str(bi)+':'+str(index),'at':when,'native_sequence':index,
                    'kind':kind,'quantity':quantity(q),'balance':quantity(balance) if balance is not None else None,'native_onhand':quantity(native_balance) if native_balance is not None else None,
                    'reference':ref,'operation_task_id':op.get('id') if op else None,'operation_id':op.get('operation_id') if op else None,
                    'native_record':r,'native_order':po or do or dlvr,
                    'order_status':(po or do or dlvr or r).get('status'),'native_flow_status':r.get('status'),
                    'allocation_scope':'RUN_MATERIAL_EVENT_NOT_ORDER_ALLOCATION',
                    'sources':[source('raw/fixed_batch_post.json','/buffers/'+str(bi)+'/events/'+str(index),hashes),
                        source('raw/operationplanmaterial.json','/'+str(j),hashes)]}
                if po and ref in supply:
                    event.update(physical_receipt_at=supply[ref]['physical_receipt_at'],available_at=supply[ref]['available_at'])
                    event['sources'].append(source('raw/purchaseorder.json','/'+str(list(purchases).index(ref)),hashes))
                    event['sources'].append(source('canonical_input.json','/existing_orders/'+str(next(k for k,v in enumerate(data['existing_orders']) if v['id']==ref)),hashes))
                if po and ref in accessory_purchases:
                    certified=accessory_purchases[ref]
                    event.update(physical_receipt_at=certified['physical_receipt_at'],available_at=certified['available_at'],
                        supply_source_id=certified['source_id'],supply_certification=certified['certification'])
                    event['sources'].append(source('raw/purchaseorder.json','/'+str(list(purchases).index(ref)),hashes))
                    event['sources'].append(source('canonical_input.json','/accessory_supply_option',hashes))
                if foundation and po:
                    # These are two observed native facts, already reconciled by
                    # the combined auditor. No inferred receipt or QC execution.
                    event.update(physical_receipt_at=at(po['enddate']),available_at=when,
                                 supply_certification=validation['status'])
                    receipt_source=source('raw/purchaseorder.json','/'+str(list(purchases).index(ref)),hashes)
                    if receipt_source not in event['sources']:event['sources'].append(receipt_source)
                events.append(event)
            reasons=list(dict.fromkeys(reasons))
            projection_missing.extend('inventory_projection.'+b['item']+'@'+b['location']+'.'+reason for reason in reasons)
            complete=validation['status']=='PASS' and isinstance(b.get('events'),list) and not reasons
            series.append({'id':b['item']+'@'+b['location'],'item_id':b['item'],'location_id':b['location'],
                'uom':uom,'opening_at':t0,'physical_opening':quantity(physical) if physical is not None else None,
                'quality_blocked':quantity(frozen) if frozen is not None else None,'other_nonnettable':quantity(other) if other is not None else None,
                'inspection_pending_quantity':quantity(pending) if pending is not None else None,
                'opening':quantity(opening) if opening is not None else None,'ending':quantity(balance) if balance is not None else None,'events':events,'complete':complete,
                'verified_through':end if complete else (events[-1]['at'] if events else t0),
                'opening_source':declared_source(members) if members else source('raw/fixed_batch_post.json','/buffers/'+str(bi)+'/onhand',hashes),
                'opening_sources':opening_sources,'opening_lots':[copy.deepcopy(row) for _,row in members],
                'reconciliation':dict(status='NOT_CHECKED' if reasons else 'PASS',reasons=reasons,
                    native_opening=quantity(native_opening) if native_opening is not None else None,
                    scope='DECLARED_OPENING_VS_NATIVE_BUFFER_AND_CAPTURED_EVENT_BALANCES'),
                'event_status':('PRESENT' if events else 'EMPTY_CHECKED') if complete else 'NOT_COLLECTED'})
        series.sort(key=lambda s:(s['item_id'],s['location_id']))
        if projection_missing:
            validation.setdefault('missing_evidence',[]).extend(projection_missing)
            if validation['status']!='FAIL':validation['status']='NOT_CHECKED'
        # Preserve deterministic business ordering (FACTORY before SHOP-1), never sum locations into factory stock.
        transit={'status':'UNSUPPORTED','timeline':[],'shipments':[]}
        if foundation and data.get('distribution_lanes'):
            transit={'status':('PRESENT' if shipments else 'EMPTY_CHECKED') if validation['status']=='PASS' else 'NOT_COLLECTED',
                'timeline':[],'shipments':[dict(r,uom=items[r['item']]['base_uom'],source=source('raw/distributionorder.json','/'+str(i),hashes)) for i,r in enumerate(raw.get('distributionorder',[]))],
                'semantics':'PLANNED_IN_TRANSIT_PER_ITEM_ROUTE_NOT_LOCATION_STOCK','validation_status':validation['status']}
        elif profile in {DISTRIBUTION,NETWORK}:
            summary=validation.get('summary',{});transit={'status':('PRESENT' if shipments else 'EMPTY_CHECKED') if validation['status']=='PASS' else 'NOT_COLLECTED',
                'timeline':[{k:(v if k=='at' else quantity(v)) for k,v in row.items()} for row in summary.get('in_transit_timeline',[])],'uom':data['distribution_lanes'][0]['uom'],
                'shipments':[dict(r,source=source('raw/distributionorder.json','/'+str(i),hashes)) for i,r in enumerate(raw.get('distributionorder',[]))],
                'semantics':'PLANNED_IN_TRANSIT_NOT_PHYSICAL_STOCK','validation_status':validation['status']}
        ia={'status':component_status(validation,any(s['events'] for s in series)),'validation':validation,
            'series':series,'initializations':initializations,'transit':transit,
            'policy_observations':inventory_policy_observations(data,raw,hashes,run),
            'declared_inventory_dimensions':[dict(id=key[0]+'@'+key[1],item_id=key[0],
                location_id=key[1],uom=key[2],opening_source=(source('canonical_input.json','/inventory_positions/'+str(members[0][0]),hashes)
                    if len(members)==1 else declared_source(members)))
                for key,members in stocks.items()],
            'limits':['仅净可用库存时间曲线；物理/冻结仅展示有来源的期初值，不补造后续质量事件。',
                      '1971初始化只核对一次，不作为企业规划期新入库。','运行物料事件不是订单分配或因果证明。']}
    return {'schema_version':'planning-analysis/1','identity':{'run_id':run,'scenario_id':result['scenario_id'],'profile':profile},
            'timezone':data['site']['timezone'],'planning_at':t0,'horizon_end':end,
            'resource_analysis':ra,'inventory_analysis':ia,'deliveries':result.get('deliveries',[]),
            'demand_analysis':demand_basis(data,result,raw,hashes) if wanted('demand_analysis') else {'status':'NOT_REQUESTED','lines':[]},
            'document_analysis':document_basis(data,result,raw,hashes,validation=combined) if wanted('document_analysis') else {'status':'NOT_REQUESTED'},
            'manufacturing_analysis':manufacturing_basis(data,result,raw,hashes,validation=combined) if wanted('manufacturing_analysis') else {'status':'NOT_REQUESTED'},
            'native_result_status':result.get('assessment'), 'fact_kind':'NATIVE_PLANNED_RESULT',
            'professional_dataset_mapping':'EXPLICIT_FOUNDATION_SOURCE_ROWS' if foundation else 'NOT_AVAILABLE',
            'limits':['规划建议，不代表实际执行、收发货或授权。',
                      '采用当前版本显式来源行映射；待净化预测、外部库存与开账历史保留各自边界。' if foundation else '未认证与APS-PRO-DATA的对象映射，不按同名自动拼接。']}


@lru_cache(maxsize=16)
def _project_cached(data_text,result_text,raw_text,hash_text,components_text='null'):
    return project_analysis(json.loads(data_text),json.loads(result_text),json.loads(raw_text),json.loads(hash_text),json.loads(components_text))


@lru_cache(maxsize=96)
def _decode(content):
    return json.loads(content)


def read_analysis(folder,identity,components=None,*,owner=None,reuse=False):
    """Reuse a certified immutable component only when its owning reader explicitly requests it.

    The historical strict entry remains the default, including full byte and
    source membership checks. A daily read has a distinct certification boundary.
    """
    if not reuse:return _read_analysis_strict(folder,identity,components)
    if owner is None:raise InputError('QUERY_READ_OWNER_REQUIRED')
    from p22.query_reads import immutable_read
    selected=tuple(sorted(components)) if components is not None else ()
    return immutable_read(owner,folder,identity,'analysis-v1:'+','.join(selected),
        lambda manifest:_read_analysis_strict(folder,identity,components))


def _read_analysis_strict(folder,identity,components=None):
    """Authenticate every consumed file on each read; cache parsing/projection by content."""
    if components is not None and (not isinstance(components,(tuple,list,set)) or not components or
            set(components)-{'resource_analysis','inventory_analysis','demand_analysis','document_analysis','manufacturing_analysis'}):
        raise InputError('INVALID_ANALYSIS_COMPONENT')
    folder=folder.resolve()
    try:manifest_bytes=(folder/'run-manifest.json').read_bytes();m=json.loads(manifest_bytes)
    except FileNotFoundError:raise InputError('EVIDENCE_NOT_COLLECTED: 缺少运行manifest') from None
    except (OSError,ValueError):raise InputError('EVIDENCE_UNREADABLE: 无法读取运行manifest') from None
    if not isinstance(m,dict):raise InputError('EVIDENCE_UNREADABLE: manifest必须是对象')
    for k in ['run_id','plan_id','data_version_id','snapshot_sha256']:
        if m.get(k)!=identity.get(k):raise InputError('RUN_SOURCE_MISMATCH: '+k)
    files=m.get('files',{});hashes={};errors={}
    if not isinstance(files,dict):raise InputError('EVIDENCE_UNREADABLE: manifest索引无效')
    def load(name,binary=False):
        digest=files.get(name)
        if not digest:errors[name]='NOT_COLLECTED';return None
        path=(folder/name).resolve()
        if not path.is_relative_to(folder):raise InputError('INVALID_EVIDENCE_PATH')
        try:content=path.read_bytes()
        except OSError:errors[name]='UNREADABLE';return None
        if hashlib.sha256(content).hexdigest()!=digest:errors[name]='INVALID_EVIDENCE';return None
        try:value=content if binary else _decode(content)
        except ValueError:errors[name]='UNREADABLE';return None
        hashes[name]=digest;return value
    data=load('canonical_input.json');result=load('PlanResult.json');context=load('enterprise-context.json')
    if data is None or result is None or context is None:raise InputError('EVIDENCE_'+next(iter(errors.values()))+': 必要输入或结果不可读取')
    if not all(isinstance(v,dict) for v in [data,result,context]):raise InputError('EVIDENCE_UNREADABLE: 输入、结果和来源必须是对象')
    expected_hash=hashlib.sha256(json.dumps(data,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    if (result.get('solve_run_id')!=identity['run_id'] or data.get('scenario_id')!=identity['scenario_id']
        or result.get('scenario_id')!=identity['scenario_id'] or result.get('input_hash')!=expected_hash
        or any(context.get(k)!=identity[k] for k in ['plan_id','data_version_id','snapshot_sha256'])):
        raise InputError('RUN_SOURCE_MISMATCH: 输入、结果或企业来源身份不一致')
    if hashlib.sha256(json.dumps(context,ensure_ascii=False,sort_keys=True).encode()).hexdigest()!=m.get('context_sha256'):
        raise InputError('RUN_SOURCE_MISMATCH: 企业来源摘要不一致')
    raw={}
    for table in TABLES:
        v=load('raw/'+table+'.json')
        if v is not None:raw[table]=v
    for phase in ['pre','post']:
        name='raw/fixed_batch_'+phase+'.xml';xml=load(name,binary=True);snapshot=raw.get('fixed_batch_'+phase)
        if xml is not None and isinstance(snapshot,dict) and hashlib.sha256(xml).hexdigest()!=snapshot.get('native_xml_sha256'):
            errors[name]='INVALID_EVIDENCE'
        if profile_id(data)==FOUNDATION and xml is not None and name not in errors:
            try:raw['fixed_batch_'+phase+'_xml']=xml.decode('utf-8')
            except UnicodeDecodeError:errors[name]='UNREADABLE'
    # A changed byte invalidates the content-keyed cache, even if a caller preserves file times/sizes.
    try:
        model=copy.deepcopy(_project_cached(json.dumps(data,sort_keys=True),json.dumps(result,sort_keys=True),
                           json.dumps(raw,sort_keys=True),json.dumps(hashes,sort_keys=True),
                           json.dumps(sorted(components)) if components is not None else 'null'))
    except (KeyError,TypeError,ValueError,ArithmeticError,StopIteration,AttributeError):
        raise InputError('EVIDENCE_UNREADABLE: 原生证据字段或时间无法安全解析') from None
    model['identity'].update(identity,input_hash=expected_hash,manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest())
    model['evidence_files']=[{'artifact':k,'sha256':v} for k,v in hashes.items()]
    for key,needed in [('demand_analysis',('deliveryorder',)),('manufacturing_analysis',('fixed_batch_post','workorder','manufacturingorder')),('resource_analysis',('fixed_batch_pre','fixed_batch_post','workorder','operationplanresource')),
                       ('inventory_analysis',('fixed_batch_pre','fixed_batch_post','operationplanmaterial','purchaseorder','distributionorder','deliveryorder','demand'))]:
        if components is not None and key not in components:continue
        c=model[key]
        if c['status']=='UNSUPPORTED':continue
        # Non-distribution runs do not collect a distributionorder table; it isn't required there.
        required=['raw/'+x+'.json' for x in needed if x!='distributionorder' or model['identity']['profile'] in {DISTRIBUTION,NETWORK}]
        if key!='demand_analysis':required+=['raw/fixed_batch_pre.xml','raw/fixed_batch_post.xml']
        if model['identity']['profile']==FOUNDATION and key!='demand_analysis':
            # No component borrows a partial old-profile PASS when the combined
            # run's required native evidence is absent or invalid.
            required=['raw/'+x+'.json' for x in TABLES if x!='distributionorder']
            required+=['raw/fixed_batch_pre.xml','raw/fixed_batch_post.xml']
        bad={n:errors[n] for n in required if n in errors}
        if bad:
            c['status']=next(s for s in ['INVALID_EVIDENCE','UNREADABLE','NOT_COLLECTED'] if s in bad.values())
            c['evidence_errors']=bad
            if c['validation']['status']!='FAIL':c['validation']['status']='NOT_CHECKED'
            if key=='demand_analysis':
                # Preserve independently authenticated input due facts; native
                # timed quantities cannot survive missing or invalid raw bytes.
                c['delivery_evidence_status']=c['status']
                for line in c['lines']:
                    line.update(delivery_evidence_status=c['status'],
                        delivery_validation_status=c['validation']['status'],
                        cumulative_planned_quantity=None,unknown_delivery_quantity=line.get('planned_quantity'))
            for s in c.get('series',[]):s['complete']=False
    from planning_decision import read_table,issue_reports
    model['issue_analysis']=issue_reports(data,raw.get('fixed_batch_post',{}),model['identity'],
        read_table(folder,'constraint',identity['run_id']),read_table(folder,'problem',identity['run_id']))
    for c in model['issue_analysis'].values():
        if c.get('source'):model['evidence_files'].append({k:c['source'][k] for k in ('artifact','sha256')})
    return model


def select_analysis(model,resource_id=None,item_id=None,location_id=None,start=None,end=None):
    value=copy.deepcopy(model);zone=ZoneInfo(value['timezone'])
    try:
        a=clock(start or value['planning_at'],zone);b=clock(end or value['horizon_end'],zone)
        if a>=b or a<clock(value['planning_at'],zone) or b>clock(value['horizon_end'],zone):raise ValueError()
    except (ValueError,TypeError):raise InputError('INVALID_ANALYSIS_RANGE: 查询时间需在当前规划期内，结束晚于开始') from None
    r=value['resource_analysis'];i=value['inventory_analysis'];rs=r.get('resources',[]);ss=i.get('series',[])
    if resource_id and resource_id not in {x['id'] for x in rs}:raise InputError('RESOURCE_NOT_FOUND')
    if item_id and item_id not in {x['item_id'] for x in ss}:raise InputError('ITEM_NOT_FOUND')
    if location_id and location_id not in {x['location_id'] for x in ss if not item_id or x['item_id']==item_id}:raise InputError('LOCATION_NOT_FOUND')
    value['window']={'start_at':a.isoformat(),'end_at':b.isoformat()}
    i['catalog']=[{k:s[k] for k in ['id','item_id','location_id','uom']} for s in ss]
    r['intervals']=[x for x in r.get('intervals',[]) if (not resource_id or x['resource_id']==resource_id) and clock(x['end_at'],zone)>a and clock(x['start_at'],zone)<b]
    # Return complete authenticated per-buffer event sequence for opening/range predecessor calculation.
    # Each series is parsed/projected once per content identity, never reparsed on an object switch.
    i['series']=[s for s in ss if (not item_id or s['item_id']==item_id) and (not location_id or s['location_id']==location_id)]
    if 'declared_inventory_dimensions' in i:
        i['declared_inventory_dimensions']=[s for s in i['declared_inventory_dimensions'] if
            (not item_id or s['item_id']==item_id) and (not location_id or s['location_id']==location_id)]
    i['risk']=inventory_risk(value,a,b)
    return value



def inventory_policy_observations(data,raw,hashes,run):
    """Keep declarations in their owning semantics; no new replenishment rule."""
    from inventory_policy import validate as validate_targets
    from inventory_decision import audit_policy
    rows=[]
    if profile_id(data)==FOUNDATION:
        from p22.foundation_projection import validate
        meta=data['foundation_projection'];zone=ZoneInfo(data['site']['timezone'])
        try:validate(data);sealed=True
        except (InputError,KeyError,TypeError,ValueError):sealed=False
        targets=meta.get('source_tables',{}).get('inventory_targets',[])
        items={r['id']:r['base_uom'] for r in data['items']}
        ids=[p.get('id') for p in targets]
        for index,policy in enumerate(targets):
            observation={**copy.deepcopy(policy),'start_at':policy.get('effective_from'),
                         'end_at':policy.get('effective_to'),'semantics':'DECLARED_PERIOD_SOFT_MIN_MAX',
                         'sources':[source('canonical_input.json','/foundation_projection/source_tables/inventory_targets/'+str(index),hashes)]}
            missing=[]
            if not sealed or not hashes.get('canonical_input.json'):missing.append('authenticated_sealed_source_projection')
            if ids.count(policy.get('id'))!=1:missing.append('unique_inventory_target_identity')
            if not policy.get('source_id') or not policy.get('source_sha256'):missing.append('inventory_target_source_identity')
            if policy.get('hard_limit') is not False:missing.append('soft_target_semantics')
            if policy.get('end_boundary')!='OPEN':missing.append('explicit_half_open_target_boundary')
            if (policy.get('uom')!=items.get(policy.get('item_id')) or
                    not any((r['item_id'],r['location_id'],r['uom'])==(policy.get('item_id'),policy.get('location_id'),policy.get('uom'))
                            for r in data['inventory_positions'])):missing.append('inventory_target_item_location_unit')
            try:
                minimum=Decimal(str(policy['minimum']));maximum=Decimal(str(policy['maximum']))
                if not minimum.is_finite() or not maximum.is_finite() or minimum<0 or maximum<minimum:raise ValueError()
                if clock(policy['effective_from'],zone)>=clock(policy['effective_to'],zone):raise ValueError()
                observation.update(minimum=quantity(minimum),maximum=quantity(maximum))
            except (KeyError,TypeError,ValueError,ArithmeticError):missing.append('finite_thresholds_and_valid_effective_range')
            observation['validation']={'status':'NOT_CHECKED' if missing else 'PASS','issues':[],
                'missing_evidence':missing,'scope':'AUTHENTICATED_SOURCE_SOFT_TARGET_NOT_NATIVE_HARD_LIMIT'}
            rows.append(observation)
        effective=meta.get('professional_overlay',{}).get('changes',{}).get('replenishment')
        if effective:
            for index,target in enumerate(effective['rows']):
                key=(target['item_id'],target['location_id'])
                native=[(i,b) for i,b in enumerate(raw.get('fixed_batch_post',{}).get('buffers',[])) if (b['item'],b['location'])==key]
                checked=sealed and bool(hashes.get('canonical_input.json')) and bool(hashes.get('raw/fixed_batch_post.json')) and len(native)==1
                matched=checked and all(Decimal(str(native[0][1].get(k)))==Decimal(str(target[k])) for k in ('minimum','maximum'))
                observation={**copy.deepcopy(target),'id':effective['policy_version']+':'+target['item_id']+'@'+target['location_id'],
                    'start_at':target['effective_from'],'end_at':target['effective_to'],'hard_limit':False,
                    'semantics':'ACTIVE_RESEARCH_SOFT_TARGET','approval_status':effective['approval_status'],
                    'validation':{'status':'PASS' if matched else 'FAIL' if checked else 'NOT_CHECKED'},
                    'sources':[source('canonical_input.json','/foundation_projection/professional_overlay/changes/replenishment/rows/'+str(index),hashes)]+
                        ([source('raw/fixed_batch_post.json','/buffers/'+str(native[0][0]),hashes)] if native else [])}
                rows=[r for r in rows if (r['item_id'],r['location_id'])!=key];rows.append(observation)
        return rows
    if data.get('inventory_policies'):
        try:
            validate_targets(data)
            for index,policy in enumerate(data['inventory_policies']):
                rows.append({**copy.deepcopy(policy),'minimum':policy['target_quantity'],
                    'semantics':'PERIOD_MINIMUM_NET_AVAILABLE','validation':{'status':'PASS'},
                    'sources':[source('canonical_input.json','/inventory_policies/'+str(index),hashes)]})
        except InputError as exc:
            for policy in data['inventory_policies']:
                rows.append({**copy.deepcopy(policy),'validation':{'status':'NOT_CHECKED'},'reason':str(exc),'sources':[]})
    policy=data.get('inventory_buffer_policy')
    if policy is not None:
        try:
            audited=audit_policy(data,raw,run_id=run)
            observation={**copy.deepcopy(policy),'id':policy['provenance']['source_record_id'],
                'semantics':'SOFT_MINIMUM_REPLENISH_TO_MAXIMUM','validation':audited,
                'sources':[source('canonical_input.json','/inventory_buffer_policy',hashes)]+
                    [source('raw/fixed_batch_'+phase+'.json','/buffers/'+str(index),hashes)
                     for phase in ('pre','post') for index,buffer in enumerate(raw.get('fixed_batch_'+phase,{}).get('buffers',[]))
                     if (buffer.get('item'),buffer.get('location'))==(policy['item_id'],policy['location_id'])]}
        except (InputError,KeyError,TypeError,ValueError) as exc:
            observation={**copy.deepcopy(policy),'validation':{'status':'NOT_CHECKED'},'reason':str(exc),'sources':[]}
        rows.append(observation)
    return rows


def inventory_risk(model,start,end):
    """Observe existing net balances in native order; never build a second ledger.

    The existing policy auditor owns event completeness and conservation. A hard
    failure remains a hard failure; its detailed series is not recertified here.
    """
    from inventory_policy import _verified_events,_number,_quantity,_time
    inventory=model['inventory_analysis'];identity=copy.deepcopy(model['identity'])
    policies=inventory.get('policy_observations',[])
    hard_fail=(model.get('native_result_status',{}).get('constraint_check')=='FAIL' or
        inventory.get('validation',{}).get('status')=='FAIL' or
        any(row.get('validation',{}).get('status')=='FAIL' for row in policies))
    value={'status':'NOT_COLLECTED','identity':identity,
        'window':{'start_at':start.isoformat(),'end_at':end.isoformat(),'end_boundary':'OPEN'},
        'validation':{'status':'FAIL' if hard_fail else 'NOT_CHECKED',
            'issues':copy.deepcopy(inventory.get('validation',{}).get('issues',[]))+
                [dict(issue,owner='inventory_decision') for p in policies
                 for issue in p.get('validation',{}).get('issues',[])],
            'missing_evidence':copy.deepcopy(inventory.get('validation',{}).get('missing_evidence',[]))},
        'coverage':{'item_location_count':None,'verified_count':None,'not_checked_count':None},'rows':[],
        'limits':['仅当前运行和所选半开期间的净可用余额；不是库存安全承诺或订单分配。',
                  '软最低目标偏离不等于硬约束失败；不生成采购、调拨或质量放行。']}
    if inventory.get('status')=='UNSUPPORTED':
        value.update(status='FAIL' if hard_fail else 'UNSUPPORTED',reason=inventory.get('reason'))
        return value
    series=list(inventory.get('series',[]))
    for declared in inventory.get('declared_inventory_dimensions',[]):
        if not any((s.get('item_id'),s.get('location_id'),s.get('uom'))==
                   (declared.get('item_id'),declared.get('location_id'),declared.get('uom')) for s in series):
            series.append({**declared,'complete':False,'events':None})
    dimensions=[(s.get('item_id'),s.get('location_id'),s.get('uom')) for s in series]
    authenticated={r['artifact']:r['sha256'] for r in model.get('evidence_files',[])}
    def verified_sources(sources):
        return bool(sources) and all(isinstance(s,dict) and s.get('sha256') and
            authenticated.get(s.get('artifact'))==s['sha256'] and
            isinstance(s.get('pointer'),str) and '/None' not in s['pointer'] for s in sources)
    def point_at(events,opening,at):
        predecessors=[e for e in events if _time(e['at'])<at]
        previous=predecessors[-1] if predecessors else None
        return (_number(previous['balance']) if previous else opening),previous
    for s in series:
        dimension=(s.get('item_id'),s.get('location_id'),s.get('uom'))
        row={k:copy.deepcopy(s.get(k)) for k in ('id','item_id','location_id','uom')}
        row.update(status='NOT_COLLECTED',category='NOT_CHECKED',minimum_balance=None,
            first_risk_at=None,first_event_id=None,opening_basis=None,policy_observations=[],sources=[])
        applicable=[p for p in policies if (p.get('item_id'),p.get('location_id'),p.get('uom'))==dimension
                    or (p.get('semantics')=='DECLARED_PERIOD_SOFT_MIN_MAX' and
                        (p.get('item_id'),p.get('location_id'))==dimension[:2])]
        try:
            if hard_fail:raise ValueError('原业务核验存在硬FAIL，风险细项不重新认证。')
            if any(not v for v in dimension) or dimensions.count(dimension)!=1:raise ValueError('物料地点单位身份缺失或重复。')
            if not s.get('opening_source') or not authenticated.get('canonical_input.json'):raise ValueError('未取得可追溯净期初来源。')
            events=_verified_events(s,inventory,start,end)
            if any(not verified_sources(e.get('sources')) for e in events):raise ValueError('原生事件来源SHA或引用不完整。')
            opening,previous=point_at(events,_number(s['opening']),start)
            selected=[e for e in events if start<=_time(e['at'])<end]
            minimum=min([opening]+[_number(e['balance']) for e in selected])
            row.update(status='PRESENT',minimum_balance=_quantity(minimum),
                opening_basis={'at':start.isoformat(),'balance':_quantity(opening),
                    'source':copy.deepcopy(s['opening_source']),
                    'predecessor_event_id':previous['id'] if previous else None},
                sources=[source('canonical_input.json','/inventory_positions',authenticated)]+
                    ([copy.deepcopy(previous['sources'][0])] if previous else [])+
                    [copy.deepcopy(src) for event in selected for src in event['sources']])
            negative=None
            if opening<0:negative=(start.isoformat(),None)
            elif minimum<0:
                first=next(e for e in selected if _number(e['balance'])<0);negative=(first['at'],first['id'])
            soft=[];policy_unknown=False
            for policy in applicable:
                a,b=max(start,_time(policy['start_at'])),min(end,_time(policy['end_at']))
                if a>=b:continue
                observation=copy.deepcopy(policy);row['policy_observations'].append(observation)
                if policy.get('validation',{}).get('status')!='PASS' or not verified_sources(policy.get('sources')):
                    policy_unknown=True;continue
                target=_number(policy['minimum']);balance,predecessor=point_at(events,_number(s['opening']),a)
                relevant=[e for e in events if a<=_time(e['at'])<b]
                first=(a.isoformat(),None) if balance<target else next(((e['at'],e['id']) for e in relevant if _number(e['balance'])<target),None)
                observation.update(first_gap_at=first[0] if first else None,first_event_id=first[1] if first else None,
                    minimum_balance=_quantity(min([balance]+[_number(e['balance']) for e in relevant])),
                    predecessor_event_id=predecessor['id'] if predecessor else None)
                if policy.get('semantics')=='DECLARED_PERIOD_SOFT_MIN_MAX':
                    # Reuse this same authenticated event sequence; soft maxima
                    # are observations, never a new balance or solver constraint.
                    maximum=_number(policy['maximum']);balances=[balance]+[_number(e['balance']) for e in relevant]
                    excess=(a.isoformat(),None) if balance>maximum else next(((e['at'],e['id']) for e in relevant if _number(e['balance'])>maximum),None)
                    observation.update(maximum_balance=_quantity(max(balances)),
                        below_minimum_quantity=_quantity(max(Decimal(0),target-min(balances))),
                        above_maximum_quantity=_quantity(max(Decimal(0),max(balances)-maximum)),
                        first_excess_at=excess[0] if excess else None,first_excess_event_id=excess[1] if excess else None)
                if first:soft.append(first)
            if negative:
                row.update(category='NEGATIVE_NET_AVAILABLE',first_risk_at=negative[0],first_event_id=negative[1])
                value['validation']['status']='FAIL'
            elif policy_unknown:row.update(status='NOT_COLLECTED',reason='适用软目标缺少完整原Owner核验或来源。')
            elif soft:
                first=min(soft,key=lambda event:_time(event[0]))
                row.update(category='BELOW_SOFT_MINIMUM',first_risk_at=first[0],first_event_id=first[1])
            else:row['category']='NO_NEGATIVE_OBSERVED'
        except (KeyError,TypeError,ValueError,ArithmeticError) as exc:
            row['reason']=str(exc)
        value['rows'].append(row)
    order={'NEGATIVE_NET_AVAILABLE':0,'BELOW_SOFT_MINIMUM':1,'NOT_CHECKED':2,'NO_NEGATIVE_OBSERVED':3}
    value['rows'].sort(key=lambda r:(order[r['category']],r.get('first_risk_at') or end.isoformat(),r['item_id'] or '',r['location_id'] or ''))
    verified=sum(row['status']=='PRESENT' for row in value['rows'])
    value['coverage']={'item_location_count':len(series),'verified_count':verified,'not_checked_count':len(series)-verified}
    if hard_fail or value['validation']['status']=='FAIL':value['status']='FAIL'
    elif series:
        value['status']='PRESENT' if verified==len(series) else 'PARTIAL' if verified else inventory.get('status','NOT_COLLECTED')
        if not verified and value['status'] in {'PRESENT','EMPTY_CHECKED'}:value['status']='NOT_COLLECTED'
        value['validation']['status']='PASS' if verified==len(series) else 'NOT_CHECKED'
    elif inventory.get('status')=='EMPTY_CHECKED' and inventory.get('validation',{}).get('status')=='PASS':
        value.update(status='EMPTY_CHECKED');value['validation']['status']='PASS'
    return value
