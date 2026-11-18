"""One plan-scoped bounded history contract, shared by UI and external Agent."""
import csv,io,json
from fastapi import Request,Depends,HTTPException
from fastapi.responses import Response
from p21.api import local_import_client
from p21.history_index import query,context
from p22 import history

FIELDS={'domain','start','end','grain','perspective','known_at','source_system','knowledge_version_id','item_id','location_id','customer_id','lot_id','order_id','bucket_id','metric','page_size','offset','historical_plan_id','run_id','compare_plan_id','compare_run_id','format','section','cohort_status'}
FIELDS|={'bucket_page_size','bucket_offset','actual_demand_id'}

def register(app,plans):
    def scope(pid,vid):
        record=plans.store.get(pid)
        if record['data_version_id']!=vid:raise ValueError('HISTORY_DATA_VERSION_MISMATCH')
    def guarded(action):
        try:return action()
        except (ValueError,KeyError) as exc:
            code=str(exc).split(':',1)[0] if isinstance(exc,ValueError) else 'HISTORY_SOURCE_CONTRACT_INVALID'
            raise HTTPException(422,detail=dict(code=code,message=code)) from None
    @app.get('/v1/enterprise-plans/{pid}/history/context',dependencies=[Depends(local_import_client)])
    def history_context(pid:str,request:Request,data_version_id:str|None=None):
        if set(request.query_params)-{'data_version_id'} or len(request.query_params.getlist('data_version_id'))>1:raise HTTPException(422,detail=dict(code='HISTORY_QUERY_FIELDS_INVALID'))
        def read():
            vid=data_version_id or plans.store.get(pid)['data_version_id']
            scope(pid,vid);out=context(plans.imports,vid);out['identity']['plan_id']=pid
            return out
        return guarded(read)
    @app.get('/v1/enterprise-plans/{pid}/history',dependencies=[Depends(local_import_client)])
    def history_read(pid:str,data_version_id:str,request:Request):
        params=request.query_params
        if set(params)-FIELDS-{'data_version_id'} or any(len(params.getlist(k))!=1 for k in params):raise HTTPException(422,detail=dict(code='HISTORY_QUERY_FIELDS_INVALID'))
        def read():
            scope(pid,data_version_id)
            q={k:v for k,v in params.items() if k!='data_version_id'};fmt=q.pop('format','json')
            if not {'domain','start','end'}<=set(q):raise ValueError('HISTORY_REQUIRED_QUERY_FIELD_MISSING')
            if fmt not in {'json','csv'}:raise ValueError('HISTORY_EXPORT_FORMAT_INVALID')
            for k in ('page_size','offset','bucket_page_size','bucket_offset'):
                if k in q:q[k]=int(q[k])
            domain=q.pop('domain')
            if domain=='plans':
                if set(q)-{'start','end','grain','historical_plan_id','run_id','compare_plan_id','compare_run_id','page_size','offset','section','actual_demand_id'}:raise ValueError('HISTORY_PLAN_QUERY_FIELDS_INVALID')
                c=context(plans.imports,data_version_id)
                from p21.history_buckets import instant,check_coverage
                check_coverage(c['scope'],q['start'],q['end'],q.get('grain','month'))
                principal=getattr(request.state,'agent_principal',None)
                out=history.query(plans.store,data_version_id,**q,allowed_plan_ids=principal['plan_ids'] if principal else None,
                    observed_through=min(instant(c['scope']['end']),instant(c['scope']['source_as_of'])).isoformat())
                out['query_end_by_grain']=c['query_end_by_grain']
                if q.get('actual_demand_id'):
                    link=(out.get('detail') or {}).get('actual_link')
                    if link is None:raise ValueError('HISTORY_EXACT_ACTUAL_LINK_NOT_AVAILABLE')
                    actual=query(plans.imports,data_version_id,domain='demand',start=q['start'],end=q['end'],grain=q.get('grain','month'),order_id=link['source_order_id'],metric='order_trace',page_size=q.get('page_size',50),offset=q.get('offset',0),bucket_page_size=1)
                    link['current_known_actual']=dict(identity=actual['identity'],source_as_of=actual['scope']['source_as_of'],**actual['detail'])
            else:
                if set(q)&{'historical_plan_id','run_id','compare_plan_id','compare_run_id','section','actual_demand_id'}:raise ValueError('HISTORY_FACT_QUERY_RUN_FORBIDDEN')
                out=query(plans.imports,data_version_id,domain=domain,**q)
            out['identity']=dict(out.get('identity',{}),plan_id=pid,data_version_id=data_version_id)
            if fmt=='json':return out
            buf=io.StringIO();writer=csv.writer(buf)
            writer.writerow(['PLAN','DATA','domain','perspective','object','unit','metric','bucket_start','bucket_end','value','state'])
            if domain=='plans':
                for r in out['records']:writer.writerow([r['identity']['plan_id'],r['identity']['data_version_id'],domain,'SAVED_PLAN',r['identity']['run_id'],'','run_state',r['planning_at'],r['planning_at'],r['run_state'],r['business_status']])
            else:
                for g in out['groups']:
                    for cell,b in zip(g['cells'],out['buckets']):
                        for metric,value in cell.items():
                            if metric not in {'bucket_id','status','conservation','observed_through'}:writer.writerow([pid,data_version_id,domain,out['perspective'],json.dumps(g['dimension'],ensure_ascii=False),'笔' if metric=='new_orders' else '批' if metric=='batches' else g['dimension']['uom'],metric,b['start'],b['end'],'' if value is None else value,cell['status']])
            # Neutralize spreadsheet formula prefixes from source identifiers.
            lines=buf.getvalue();safe=io.StringIO();w=csv.writer(safe)
            for row in csv.reader(io.StringIO(lines)):w.writerow(["'"+x if x.startswith(('=','+','-','@','\t','\r')) and not x.lstrip('-').replace('.','',1).isdigit() else x for x in row])
            return Response('\ufeff'+safe.getvalue(),media_type='text/csv; charset=utf-8',headers={'Content-Disposition':'attachment; filename="aps-history.csv"'})
        return guarded(read)
