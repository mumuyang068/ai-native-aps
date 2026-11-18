"""Bounded business projections owned by ImportStore; no PlanningService or solver.

The accepted version is the as-of boundary. A due-date filter is not a historical
snapshot request. Actual execution and native planned results never share fields.
"""
from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo
import copy
import json


def amount(value):
    if value is None:return None
    try:
        n=Decimal(str(value))
        if not n.is_finite() or n<0:raise ValueError()
        return n
    except (InvalidOperation,ValueError):raise ValueError('BUSINESS_QUANTITY_CONFLICT') from None


def text(value):return None if value is None else format(value.normalize(),'f')
def first(row,*keys):return next((row[k] for k in keys if row.get(k) is not None),None)


def source(store,version_id):
    meta=store.version_metadata(version_id)
    if meta.get('confirmation_status')!='CONFIRMED':raise ValueError('BUSINESS_FACTS_NOT_CONFIRMED')
    # Incremental states reuse the existing version/object index. No annual replay.
    if meta.get('version_mode')=='CONTINUITY_STATE_VERSION':
        state=store.continuity_meta(version_id)
        def rows(system,table):return [v['row'] for v in store.continuity_rows(version_id,system,table)]
        definition=state['declarations']['definition']; operating=state['declarations']['operating']
        from p22.continuity_sources import inventory_facts,wip_inventory_facts
        wms={k:rows('WMS',k) for k in ('lots','finished_goods','movements')}
        mes={k:rows('MES',k) for k in ('manufacturing_tasks','operations','material_issues')}
        inventory=inventory_facts(wms,mes,definition,operating,state['source_as_of'])
        for job in mes['manufacturing_tasks']:
            if job['status']=='WIP':inventory.extend(wip_inventory_facts(job,mes,definition,{r['id']:r for r in wms['lots']},
                operating['site_id'],state['source_as_of'],operating=operating,wms=wms))
        return dict(identity=dict(data_version_id=version_id,enterprise_id=state['enterprise_id'],
                    source_as_of=state['source_as_of'],version_sha256=meta['version_sha256'],state_revision=state['state_revision']),
            timezone=definition['timezone'],location_id=operating['site_id'],
            items=[dict(id=k,base_uom=v['unit']) for k,v in definition['items'].items()],
            orders=rows('ERP','sales_orders'),purchases=rows('ERP','purchase_orders'),receipts=rows('ERP','purchase_receipts'),
            inventory=inventory,quality=rows('WMS','quality_events'),resources=copy.deepcopy(state['declarations']['reference'].get('tables',{}).get('resources',[])),forecasts=[],manufacturing=rows('MES','manufacturing_tasks'))
    view=store.business_facts(version_id)  # Original source verification remains authoritative.
    if 'canonical' in view:
        c=view['canonical'];tables=c['tables'];locations=tables.get('locations',[])
        bundle=next((r['continuity_bundle'] for r in locations if r.get('continuity_bundle')),None)
        erp=bundle['documents']['ERP']['tables'] if bundle else {}
        sales=tables.get('sales_orders',[])
        # Canonical sales_orders can be headers. Their explicit demand-line
        # references own quantities; a header is never an empty demand line.
        orders=erp.get('sales_orders')
        if orders is None:
            orders=sales if sales and all(first(r,'quantity','requested_quantity') is not None and first(r,'item_id','product_id') for r in sales) else tables.get('demand_lines',[])
        odoo=next((r['odoo_bundle']['capture'] for r in locations if r.get('odoo_bundle')),None)
        odoo_manufacturing=None;odoo_identity={}
        if odoo:
            from interop.odoo_planning import key,ident,at
            from interop.odoo_business_audit import stored_quantity
            policy=next(r['odoo_bundle']['professional_policy'] for r in locations if r.get('odoo_bundle'))
            odoo_identity=dict(source_system='ODOO',company_id=odoo['company_id'],scope=odoo['scope'],source_sha256=odoo['source_sha256'],professional_policy_id=policy['policy_id'],professional_policy_status=policy['status'],personnel_setup_source='INDEPENDENT_USER_APPROVED_POLICY')
            docs=odoo['documents'];headers={r['id']:r for r in docs['sale.order']}
            units={r['id']:r['name'] for r in docs['uom.uom']}
            orders=[dict(id=key('sale.order.line',r['id']),order_ref=key('sale.order',ident(r['order_id'])),
                item_id=key('product.product',ident(r['product_id'])),location_id=locations[0]['id'],
                quantity=r['product_uom_qty'],shipped_quantity=r['qty_delivered'],uom=units[ident(r['product_uom'])],
                due_at=at(headers[ident(r['order_id'])]['commitment_date']).isoformat(),status=headers[ident(r['order_id'])]['state'])
                for r in docs['sale.order.line'] if not r['display_type']]
            odoo_manufacturing=[dict(id=key('mrp.production',m['id']),external_mo_id=m['id'],reference=m['name'],
                product_id=key('product.product',ident(m['product_id'])),quantity=m['product_qty'],completed_quantity=m['qty_produced'],
                status=m['state'],unit=units[ident(m['product_uom_id'])],source_revision=odoo['revisions']['mrp.production'][str(m['id'])],
                stored_quantity=float(stored_quantity(odoo,m)),backorder_sequence=m['backorder_sequence'],procurement_group_id=ident(m['procurement_group_id']),
                workorders=[copy.deepcopy(w) for w in docs['mrp.workorder'] if ident(w['production_id'])==m['id']],
                material_moves=[copy.deepcopy(x) for x in docs['stock.move'] if ident(x['raw_material_production_id'])==m['id'] or ident(x['production_id'])==m['id']],
                storage_moves=[copy.deepcopy(x) for x in docs['stock.move'] if set(m['move_finished_ids']).intersection(x['move_orig_ids'])],
                component_transfers=[copy.deepcopy(x) for x in docs['stock.move'] if set(m['move_raw_ids']).intersection(x['move_dest_ids'])],
                actual_semantics='ONLY_DONE_STOCK_MOVES_ARE_CONSUMPTION_OR_PRODUCTION; WO_DONE_IS_SEPARATE') for m in docs['mrp.production']]
        source_resources=tables.get('resources',[])
        if odoo:
            own_leaves={ident(w['leave_id']) for w in docs['mrp.workorder']}
            center_resources={key('mrp.workcenter',w['id']):ident(w['resource_id']) for w in docs['mrp.workcenter']}
            source_resources=[dict(r,native_unavailability=[copy.deepcopy(l) for l in docs['resource.calendar.leaves'] if l['id'] not in own_leaves and (not l['resource_id'] or ident(l['resource_id'])==center_resources.get(r['id']))]) for r in source_resources]
        return dict(identity=dict(data_version_id=version_id,enterprise_id=c['enterprise_id'],
                    source_as_of=c['planning_at'],version_sha256=meta['version_sha256'],**odoo_identity),
            timezone=c['timezone'],location_id=locations[0]['id'] if len(locations)==1 else None,items=tables.get('items',[]),
            orders=orders,
            purchases=erp.get('purchase_orders',tables.get('purchase_orders',[])),receipts=erp.get('purchase_receipts',tables.get('purchase_receipts',[])),
            quality=bundle['documents']['WMS']['tables'].get('quality_events',[]) if bundle else [],
            inventory=tables.get('inventory_positions',[]),resources=source_resources,
            forecasts=tables.get('forecasts',[]),manufacturing=odoo_manufacturing if odoo_manufacturing is not None else bundle['documents']['MES']['tables'].get('manufacturing_tasks',[]) if bundle else tables.get('existing_manufacturing_orders',[]))
    return dict(identity=dict(data_version_id=version_id,enterprise_id=meta.get('enterprise_id'),
                source_as_of=view['times']['business_as_of'],version_sha256=meta['version_sha256']),
        timezone=view['timezone'],location_id=None,items=[],orders=[*view.get('demand_lines',[]),*view.get('net_forecast_demands',[])],
        purchases=view.get('confirmed_purchases',[]),receipts=[],inventory=view.get('inventory_positions',[]),
        resources=[],forecasts=view.get('forecasts',[]),manufacturing=[])


def project(data,*,start=None,end=None,item_id=None,location_id=None,kind='FIRM_ORDER',status=None,page_size=20,offset=0,section='orders',order_id=None):
    if type(page_size) is not int or not 1<=page_size<=100 or type(offset) is not int or not 0<=offset<=100000:
        raise ValueError('BUSINESS_QUERY_PAGE_LIMIT')
    zone=ZoneInfo(data['timezone']);identity=dict(data['identity'],run_id=None)
    try:
        asof=datetime.fromisoformat(identity['source_as_of'].replace('Z','+00:00'))
        if asof.utcoffset() is None:raise ValueError()
        def day(value):
            if not isinstance(value,str):raise ValueError()
            v=datetime.fromisoformat(value.replace('Z','+00:00'))
            if len(value)==10:return v.date().isoformat()
            if v.utcoffset() is None:raise ValueError()
            return v.astimezone(zone).date().isoformat()
        today=asof.astimezone(zone).date().isoformat()
        if start:day(start)
        if end:day(end)
    except (ValueError,TypeError,AttributeError):raise ValueError('BUSINESS_QUERY_DATE_INVALID') from None
    if kind not in {None,'FIRM_ORDER','NET_FORECAST','UNKNOWN'}:raise ValueError('BUSINESS_QUERY_KIND_INVALID')
    if status not in {None,'open','overdue'}:raise ValueError('BUSINESS_QUERY_STATUS_INVALID')
    if section not in {'orders','purchases','inventory','resources','forecasts','manufacturing'}:raise ValueError('BUSINESS_QUERY_SECTION_INVALID')
    items={r['id']:r for r in data['items']}; orders=[]
    def dimensions(r):
        item=first(r,'item_id','product_id');loc=first(r,'location_id') or data['location_id']
        return dict(item_id=item,location_id=loc,uom=first(r,'uom','unit') or first(items.get(item,{}),'base_uom','unit'))
    def matches(r):return (not item_id or r['item_id']==item_id) and (not location_id or r['location_id']==location_id)
    available_kinds=set()
    for r in data['orders']:
        q=amount(first(r,'quantity','requested_quantity'));shipped=amount(first(r,'shipped','shipped_quantity'))
        remaining=amount(first(r,'outstanding','remaining_quantity'))
        if q is not None and shipped is not None:
            if shipped>q or remaining is not None and q-shipped!=remaining:raise ValueError('BUSINESS_QUANTITY_CONFLICT')
            remaining=q-shipped
        due=first(r,'due','due_at');date=day(due) if due else None
        record=dict(id=r['id'],order_ref=first(r,'order_ref','order_id','id'),**dimensions(r),
            kind=r.get('kind','FIRM_ORDER'),due_at=due,date=date,ordered=text(q),shipped=text(shipped),outstanding=text(remaining),
            source_status=first(r,'status','source_status'),quantity_basis='SOURCE_ORDER_BALANCE' if shipped is not None else 'SOURCE_REQUESTED_QUANTITY',
            source=dict(owner='ImportStore',record_id=r['id'],revision=r.get('source_record_revision'),provenance=copy.deepcopy(r.get('provenance'))))
        if matches(record) and (order_id is None or order_id in {record['id'],record['order_ref']}):
            available_kinds.add(record['kind'])
            if kind is None or record['kind']==kind:orders.append(record)
    dates=[r['date'] for r in orders if r['date']]
    a=day(start) if start else min(dates,default=today);b=day(end) if end else (datetime.fromisoformat(max([today,*dates]))+timedelta(days=1)).date().isoformat()
    if a>=b or (datetime.fromisoformat(b)-datetime.fromisoformat(a)).days>740:raise ValueError('BUSINESS_QUERY_RANGE_INVALID')
    def overdue(r):return bool(r['due_at']) and datetime.fromisoformat(r['due_at'].replace('Z','+00:00'))<asof
    selected=sorted([r for r in orders if (not r['date'] or a<=r['date']<b) and (status is None or r['outstanding'] is not None and amount(r['outstanding'])>0 and (status=='open' or overdue(r)))],key=lambda r:(r['date'] or '9999',r['id']))
    daily=defaultdict(int)
    for r in selected:
        if r['date']:daily[r['date']]+=1
    totals=[]
    for uom in sorted({r['uom'] for r in selected},key=lambda u:u or ''):
        rows=[r for r in selected if r['uom']==uom]
        def summed(key):
            values=[amount(r[key]) for r in rows]
            return text(sum(values,Decimal(0))) if all(v is not None for v in values) else None
        totals.append(dict(uom=uom,**{k:summed(k) for k in ('ordered','shipped','outstanding')}))
    def backlog(before):return [r for r in orders if r['date'] and r['date']<before and r['outstanding'] is not None and amount(r['outstanding'])>0]
    def page(rows,name):
        position=offset if section==name else 0
        return dict(total=len(rows),rows=rows[position:position+page_size],offset=position,next_offset=position+page_size if position+page_size<len(rows) else None)
    purchases=[]
    for r in data['purchases']:
        reference=first(r,'id','reference');receipts=[v for v in data['receipts'] if first(v,'po_id','purchase_order_id')==reference]
        receipt_ids={v['id'] for v in receipts if v.get('id')};lot_ids={v.get('lot_id') for v in receipts if v.get('lot_id')}
        record=dict(id=reference,**dimensions(r),ordered=first(r,'ordered_quantity','quantity'),
            received=first(r,'received_quantity','received'),outstanding=first(r,'remaining_quantity','unreceived_quantity','outstanding'),
            expected_receipt_at=first(r,'eta','expected_receipt_at','physical_receipt_at'),available_at=r.get('available_at'),
            source_status=first(r,'status','source_status'),source=copy.deepcopy(r),receipts=copy.deepcopy(receipts[:100]),receipts_total=len(receipts),
            quality_events=[copy.deepcopy(v) for v in data.get('quality',[]) if v.get('receipt_id') in receipt_ids or v.get('lot_id') in lot_ids][:100])
        if matches(record):purchases.append(record)
    purchases.sort(key=lambda r:(r['outstanding'] is None or amount(r['outstanding'])==0,r['expected_receipt_at'] or '',r['id'] or ''))
    inventory=[]
    for r in data['inventory']:
        physical=amount(first(r,'physical','on_hand'));blocked=amount(first(r,'quality_blocked','held'));pending=amount(r.get('pending'))
        available=amount(first(r,'available','net_available'))
        # A complete declared balance can be derived; missing parts are never zero-filled.
        other=amount(first(r,'other_unavailable','other_nonnettable'))
        if available is None and all(v is not None for v in (physical,blocked,other)):
            available=physical-blocked-other
            if available<0:raise ValueError('BUSINESS_QUANTITY_CONFLICT')
        record=dict(id=r['id'],**dimensions(r),physical=text(physical),available=text(available),blocked=text(blocked),pending=text(pending),
            status='PRESENT' if physical is not None and available is not None else 'UNKNOWN',source=copy.deepcopy(r))
        if matches(record):inventory.append(record)
    unknown_count=sum(r['status']=='UNKNOWN' for r in inventory)
    grouped=defaultdict(list)
    for row in inventory:grouped[(row['item_id'],row['location_id'],row['uom'])].append(row)
    inventory=[]
    for (item,location,uom),rows in grouped.items():
        def total(field):
            values=[amount(r[field]) for r in rows]
            return text(sum(values,Decimal(0))) if all(v is not None for v in values) else None
        inventory.append(dict(id=json.dumps([item,location,uom],ensure_ascii=False,separators=(',',':')),item_id=item,location_id=location,uom=uom,
            **{k:total(k) for k in ('physical','available','blocked','pending')},
            status='UNKNOWN' if any(r['status']=='UNKNOWN' for r in rows) else 'PRESENT',lot_count=len(rows),
            source=dict(owner='ImportStore',record_ids=[r['id'] for r in rows[:100]],records_truncated=len(rows)>100)))
    return dict(schema_version='business-view/1',identity=identity,fact_kind='CONFIRMED_BUSINESS_FACTS',planning_metrics='NOT_REQUESTED',
        timezone=data['timezone'],filters={k:v for k,v in dict(item_id=item_id,location_id=location_id,kind=kind,status=status,order_id=order_id).items() if v is not None},range=dict(start=a,end=b,field='ORDER_DUE_DATE',end_exclusive=True),
        orders=dict(**page(selected,'orders'),header_total=len({r['order_ref'] for r in selected}),available_kinds=sorted(available_kinds),kind=kind,totals=totals,daily=[dict(date=d,count=n) for d,n in sorted(daily.items())],
            undated_count=sum(r['date'] is None for r in selected),overdue_count=sum(overdue(r) and r['outstanding'] is not None and amount(r['outstanding'])>0 for r in orders),
            overdue_unknown_count=sum(overdue(r) and r['outstanding'] is None for r in orders),balance_unknown_count=sum(r['outstanding'] is None for r in selected),opening_backlog_count=len(backlog(a)),
            groups=[dict(item_id=i,location_id=l,uom=u) for i,l,u in sorted({(r['item_id'] or '',r['location_id'] or '',r['uom'] or '') for r in orders})]),
        purchases=page(purchases,'purchases'),inventory=dict(**page(inventory,'inventory'),basis='VERSION_AS_OF_BALANCE',unknown_count=unknown_count),
        resources=page(data['resources'],'resources'),forecasts=page(data['forecasts'],'forecasts'),manufacturing=page(data['manufacturing'],'manufacturing'),
        limits=['Historical balances are not reconstructed from a current balance.','Expected arrival and available dates are source declarations, not proof of actual receipt or QC release.'])


def read(store,version_id,**query):return project(source(store,version_id),**query)
