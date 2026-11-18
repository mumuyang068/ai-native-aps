"""Source-backed Odoo selection for the existing Publication Owner only."""
import copy
from decimal import Decimal
from closed_loop_actions import baseline_eligible, require, instant, digest
from interop.odoo_planning import PROFILE, ident, key


def own_creation_successor(before,after,production_id):
    """Only the exact accepted CREATE's unexecuted native expansion may differ.

    Creating/confirming the new supply necessarily adds WO/move/reservation
    records. This does not excuse unrelated source changes or any actuals.
    The caller separately authenticates the original Publication command.
    """
    try:
        if any(before[k]!=after[k] for k in ['company_id','enterprise_id','database']):return False
        a={m:{r['id']:r for r in rows} for m,rows in before['documents'].items()}
        b={m:{r['id']:r for r in rows} for m,rows in after['documents'].items()}
        if set(a)!=set(b):return False
        new={m:set(b[m])-set(a[m]) for m in a}
        if new['mrp.production']!={production_id}:return False
        mo=b['mrp.production'][production_id]
        if mo['state']!='confirmed' or mo['qty_produced'] or mo['qty_producing']:return False
        works={w for w in new['mrp.workorder'] if ident(b['mrp.workorder'][w]['production_id'])==production_id}
        if works!=new['mrp.workorder'] or works!=set(mo['workorder_ids']):return False
        if any(b['mrp.workorder'][w]['state'] not in ['ready','waiting','pending'] or b['mrp.workorder'][w]['qty_produced'] for w in works):return False
        closure=set(mo['move_raw_ids'])|set(mo['move_finished_ids']);previous=None
        while previous!=closure:
            previous=set(closure)
            for mid in tuple(closure):
                move=b['stock.move'].get(mid,{})
                closure.update(move.get('move_orig_ids',[])+move.get('move_dest_ids',[]))
        if not new['stock.move']<=closure:return False
        if any(b['stock.move'][x]['state'] in ('done','cancel') for x in new['stock.move']):return False
        line_ids={l for l in new['stock.move.line'] if ident(b['stock.move.line'][l]['move_id']) in new['stock.move']}
        if line_ids!=new['stock.move.line'] or any(b['stock.move.line'][l]['state']=='done' or b['stock.move.line'][l]['picked'] for l in line_ids):return False
        leaves={ident(b['mrp.workorder'][w]['leave_id']) for w in works}
        if not new['resource.calendar.leaves']<=leaves:return False
        if any(set(b['stock.picking'][p]['move_ids'])-new['stock.move'] for p in new['stock.picking']):return False
        allowed={'mrp.production','mrp.workorder','stock.move','stock.move.line','stock.picking','resource.calendar.leaves'}
        if any(ids for model,ids in new.items() if model not in allowed):return False
        reserved={}
        internal={r['id'] for r in after['documents']['stock.location'] if r['usage']=='internal'}
        for l in line_ids:
            r=b['stock.move.line'][l]
            if ident(r['location_id']) not in internal:continue
            k=ident(r['product_id']),ident(r['location_id'])
            reserved[k]=reserved.get(k,Decimal(0))+Decimal(str(r['quantity']))
        reservation_delta={}
        for model in a:
            if not set(a[model])<=set(b[model]):return False
            for identifier,old in a[model].items():
                current=b[model][identifier];fields={k for k in old if old[k]!=current.get(k)}
                if model=='stock.quant' and fields<= {'reserved_quantity'}:
                    delta=Decimal(str(current['reserved_quantity']))-Decimal(str(old['reserved_quantity']))
                    k=ident(old['product_id']),ident(old['location_id'])
                    if delta<0:return False
                    reservation_delta[k]=reservation_delta.get(k,Decimal(0))+delta
                elif model=='stock.picking' and fields<= {'move_ids'}:
                    if not set(old['move_ids'])<=set(current['move_ids']) or not (set(current['move_ids'])-set(old['move_ids']))<=new['stock.move']:return False
                elif fields:return False
                if not fields and before['revisions'][model][str(identifier)]!=after['revisions'][model][str(identifier)]:
                    # Native MO confirmation updates the related sales header
                    # in the SAME transaction (manufacturing relation). All
                    # captured planning fields and all sales lines must still
                    # be identical; an unrelated later revision is refused.
                    if not (model=='sale.order' and current['name']==mo['origin'] and
                        after['revisions'][model][str(identifier)]==after['revisions']['mrp.production'][str(production_id)]):return False
        if any(v!=reserved.get(k,Decimal(0)) for k,v in reservation_delta.items()) or any(v!=reservation_delta.get(k,Decimal(0)) for k,v in reserved.items()):return False
        return True
    except (KeyError,ValueError,TypeError,ArithmeticError):return False


def adoption_preview(plans, connector, selection, company_id, branch, *, parent=None):
    record=plans.store.get(selection['plan_id']);version=plans.imports.version(record['data_version_id'])
    plans.imports.verify_version_sources(version)
    head=connector.get_sync_status()
    require(head['current_version_id']==version['id'],'ADOPTION_REQUIRES_CURRENT_SOURCE_VERSION')
    s=plans.service(record['id']);s.drafts.verify_baseline()
    data=s.drafts.baseline if selection['target']=='baseline' else s.drafts.for_run(selection['target'])['input']
    meta=data.get('foundation_projection',{});bundle=meta['source_tables']['locations'][0].get('odoo_bundle',{})
    require(meta.get('policy',{}).get('profile')==PROFILE and bundle['capture']['company_id']==company_id,'ODOO_SOURCE_SCOPE_MISMATCH')
    current=connector.adapter.capture()
    view=s.selected_view(selection['target']);require(baseline_eligible(view),'AUDITED_NATIVE_RUN_REQUIRED')
    run=view['run'];result=view['result']
    unchanged=current['source_sha256']==bundle['capture']['source_sha256'] and current['revisions']==bundle['capture']['revisions']
    if parent:
        command=parent['command'];external=parent.get('receipt',{}).get('external_document',{})
        require(parent['state']=='ACCEPTED' and command['action_type']=='CREATE_MO' and
            all(command.get(k)==v for k,v in {'enterprise_id':version['canonical']['enterprise_id'],'branch_id':branch,
                'plan_id':record['id'],'data_version_id':version['id'],'run_id':run['solve_run_id'],
                'suggestion_id':selection['manufacture_id'],'test_company_id':company_id}.items())
            and external.get('company_id')==company_id,'ODOO_PUBLICATION_LINEAGE_MISMATCH')
        live=next((m for m in current['documents']['mrp.production'] if m['id']==external.get('id')),None)
        require(live and ident(live['product_id'])==command['payload']['product_id'] and ident(live['bom_id'])==command['payload']['bom_id']
            and Decimal(str(live['product_qty']))==Decimal(command['payload']['quantity']),'ODOO_PUBLICATION_LINEAGE_MISMATCH')
        unchanged=unchanged or own_creation_successor(bundle['capture'],current,external['id'])
    require(unchanged,'STALE_ADOPTION_EVIDENCE')
    require(result['solve_run_id']==run['solve_run_id'] and result['input_hash']==run['input_hash']==digest(data),'NATIVE_RESULT_RUN_MISMATCH')
    require(data['scenario_id']==run['scenario_id'] and data['snapshot_id']==run['snapshot_id'],'NATIVE_INPUT_IDENTITY_MISMATCH')
    orders=[r for r in meta['source_tables']['demand_lines'] if r['order_ref']==selection['order_id'] or r['id']==selection['order_id']]
    require(len(orders)==1,'SOURCE_OPEN_ORDER_REQUIRED');order=orders[0]
    roots=[r for r in result['manufacture_suggestions'] if r['id']==selection['manufacture_id']]
    require(len(roots)==1 and roots[0].get('origin')=='PROPOSED' and roots[0].get('source_status')=='proposed','PROTECTED_ORDER_ADOPTION_FORBIDDEN')
    root=roots[0]
    require(root['item_id']==order['item_id'] and root['uom']==order['uom'],'ADOPTION_PRODUCT_UOM_MISMATCH')
    q=Decimal(root['quantity']);require(0<q<=Decimal(str(order['quantity'])),'ADOPTION_QUANTITY_CONFLICT')
    route=next(r for r in data['production_recipes'] if r['id']==root['recipe_id'])
    ops=next(r['operations'] for r in data['routing_revisions'] if r['id']==route['routing_revision_id'])
    leaves=[r for r in result['operations'] if r['manufacture_id']==root['id']]
    require(len(leaves)==len(ops) and {r['operation_id'] for r in leaves}=={r['id'] for r in ops},'COMPLETE_ORDERED_ROUTING_REQUIRED')
    roles={r['id']:r.get('role') for r in data['resources']};operations=[]
    for op in ops:
        row=next(r for r in leaves if r['operation_id']==op['id'])
        people=[l for l in result['resource_loads'] if l['operation_id']==row['id'] and roles.get(l['resource_id'])=='OPERATOR']
        require(len(people)==1 and instant(people[0]['start_at'])==instant(row['start_at']) and instant(people[0]['end_at'])==instant(row['end_at']),'EXACT_NATIVE_OPERATOR_REQUIRED')
        require(Decimal(row['quantity'])==q,'NATIVE_BATCH_QUANTITY_MISMATCH')
        operations.append(dict(operation_id=op['id'],resource_id=row['resource_id'],operator_id=people[0]['resource_id'],start_at=row['start_at'],end_at=row['end_at'],quantity=str(q),uom=root['uom']))
    require(all(instant(a['end_at'])<=instant(b['start_at']) for a,b in zip(operations,operations[1:])),'NATIVE_PRECEDENCE_VIOLATION')
    sale=next(r for r in bundle['capture']['documents']['sale.order'] if key('sale.order',r['id'])==order['order_ref'])
    payload=dict(enterprise_id=version['canonical']['enterprise_id'],branch_id=branch,data_version_id=version['id'],
        plan_id=record['id'],run_id=run['solve_run_id'],product_id=root['item_id'],uom=root['uom'],quantity=str(q),operations=operations,
        native_audit=dict(status='PASS',run_id=run['solve_run_id'],input_hash=run['input_hash'],result_hash=result['result_hash'],checks=view['interpretation']['foundation_validation'],
            source_origin='REAL_ODOO',company_id=company_id,source_sha256=current['source_sha256']),
        source_sale_order_id=sale['id'],expected_sale_revision=bundle['capture']['revisions']['sale.order'][str(sale['id'])])
    return dict(payload=payload)
