"""Observe and protect the native model around the existing SupplyPlanning task.

No replacement solver: official database loading, planning and export remain
owned by frePPLe. Hooks run only when the Worker explicitly selects this env.
"""
import json,os
import hashlib
import xml.etree.ElementTree as ET
from pathlib import Path
from decimal import Decimal
from p3_fixed_batch.wip import seconds
from freppledb.common.commands import PlanTask,PlanTaskRegistry
from freppledb.common.models import Parameter

def capture(phase,database):
    import frepple
    request=Path(Parameter.getValue('p3.fixed_batch.request',database,''))
    root=Path('/var/log/frepple/p3-fixed-batch')
    if request.parent!=root or not request.name.endswith('.request.json'):
        raise ValueError('FIXED_BATCH_REQUEST_MISSING')
    spec=json.loads(request.read_text(encoding='utf-8'))
    editor_receipt=None
    restored_loads=None
    setup_resolution=None
    feasibility_refresh=None
    before_resolution=None
    material_coordination=None
    if spec.get('foundation') and phase=='pre':
        from p3_fixed_batch.foundation import restore_required_loads
        restored_loads=restore_required_loads(frepple,spec['protected'])
    if spec.get('foundation') and phase=='post':
        from p3_fixed_batch.foundation import resolve_protected_setups,refresh_native_feasibility,native_state
        before_resolution=[native_state(frepple,p) for p in frepple.operationplans() if p.ordertype=='MO']
        setup_resolution=resolve_protected_setups(frepple,spec)
        if spec['foundation'].get('material_repair'):
            from p3_fixed_batch.material_repair import repair
            try:material_coordination=repair(frepple,spec)
            except ValueError as error:
                if hasattr(error,'receipt'):
                    (root/(spec['run_id']+'.material-repair-failed.json')).write_text(
                        json.dumps(error.receipt,indent=2),encoding='utf-8')
                raise
        feasibility_refresh=refresh_native_feasibility(frepple)
    if spec.get('editor') and phase=='post':
        from p3_fixed_batch.editor import apply
        try:
            editor_receipt=apply(frepple,spec['editor'])
            # Native receipts must survive the adapter's return/cleanup boundary.
            # A valid intermediate pointer cannot stand in for exported evidence.
            if editor_receipt.get('revision')=='foundation-proposed-editor-v2':
                from p3_fixed_batch.foundation import native_state
                actual={str(p.reference):native_state(frepple,p) for p in frepple.operationplans() if p.ordertype=='MO'}
                for changed in editor_receipt['changes']:
                    for key in ['after','root_after']:
                        row=changed[key]
                        if actual.get(str(row['reference']))!=row:raise ValueError('EDITOR_NATIVE_RETURN_READBACK:'+str(row['reference']))
            if editor_receipt.get('feasibility') is not None:feasibility_refresh=editor_receipt['feasibility']
        except Exception as error:
            if str(error).startswith('EDITOR_'):
                try:
                    from p3_fixed_batch.editor_diagnostics import write_failure
                    write_failure(frepple,spec,phase,root/(spec['run_id']+'.editor-failed.json'),error,editor_receipt)
                except Exception:
                    pass # Diagnostic availability cannot change the native failure.
            raise
    procurement=None
    if 'priority_supply' in spec:
        from p3_fixed_batch.priority import inspect
        procurement=inspect(frepple,spec,configure=phase=='pre')
    xml_path=root/(spec['run_id']+'.'+phase+'.xml')
    frepple.saveXMLfile(str(xml_path))
    xml=ET.parse(xml_path).getroot()
    load_types={o.attrib.get('name',o.findtext('name')):[l.attrib.get('{http://www.w3.org/2001/XMLSchema-instance}type') for l in o.findall('loads/load')]
                for o in xml.findall('operations/operation')}
    resources={}
    for r in frepple.resources():
        for l in r.loadplans:
            resources.setdefault(l.operationplan.reference,set()).add(r.name)
    records=[]
    purchases=[]
    for p in frepple.operationplans():
        if p.ordertype=='PO':
            purchases.append({'reference':p.reference,'quantity':str(p.quantity),'status':p.status,
                'start':p.start.isoformat(),'end':p.end.isoformat(),
                'item':p.item.name,'supplier':p.supplier.name,'location':p.location.name})
        if p.ordertype!='MO':continue
        rs=sorted(resources.get(p.reference,set()))
        records.append({'reference':p.reference,'operation':p.operation.name,'quantity':str(p.quantity),
          'owner':p.owner.reference if p.owner else None,
          'start':p.start.isoformat(),'end':p.end.isoformat(),'status':p.status,
          'resource':rs[0] if len(rs)==1 else None,'resources':rs,
          **({'setup_start':p.start.isoformat(),'setup_end':p.setupend.isoformat(),'setup_duration_seconds':seconds(p.setup),'feasible':p.feasible} if spec.get('foundation') or spec.get('editor') else {})})
    operations=[]
    for o in frepple.operations():
        if o.hidden:continue
        operations.append({'name':o.name,'type':type(o).__name__,'duration':str(o.duration) if hasattr(o,'duration') else None,
          'size_minimum':str(o.size_minimum),'size_maximum':str(o.size_maximum),'size_multiple':str(o.size_multiple),
          'load_types_xml':load_types.get(o.name,[]),
          'loads':[{'resource':l.resource.name,'type':type(l).__name__,'quantity':str(l.quantity),'offset':str(getattr(l,'offset',0))} for l in o.loads]})
    snapshot={'run_id':spec['run_id'],'input_hash':spec['input_hash'],'phase':phase,
      'engine_version':frepple.version,'move_approved_early':frepple.settings.moveApprovedEarly,
      'extension_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
      'native_xml_sha256':hashlib.sha256(xml_path.read_bytes()).hexdigest(),
      'calendars':[{'name':c.name,'default':str(c.default),'buckets':[{'start':b.start.isoformat(),'end':b.end.isoformat(),'value':str(b.value),'days':b.days,'starttime':b.starttime,'endtime':b.endtime} for b in c.buckets]} for c in frepple.calendars()],
      'solver_parameters':{k:Parameter.getValue(k,database,'') for k in ['plan.solver','plan.move_approved_early','plan.fixBrokenSupplyPath','plan.individualPoolResources','plan.rotateResources']},
      'operationplans':records,'purchaseorders':purchases,'operations':operations,
      'resources':[{'name':r.name,'type':type(r).__name__,'maximum_calendar':r.maximum_calendar.name if r.maximum_calendar else None,'maxearly_seconds':seconds(r.maxearly),
                    'loadplans':[{'reference':l.operationplan.reference,'quantity':str(l.quantity),'at':l.date.isoformat(),
                                  'start':l.startdate.isoformat(),'end':l.enddate.isoformat()} for l in r.loadplans]} for r in frepple.resources()],
      'buffers':[{'name':b.name,'item':b.item.name,'location':b.location.name,'onhand':str(b.onhand),
                  'minimum':str(b.minimum),'maximum':str(b.maximum),
                  'minimum_calendar':b.minimum_calendar.name if b.minimum_calendar else None,
                  'maximum_calendar':b.maximum_calendar.name if b.maximum_calendar else None,
                  'events':[{'reference':f.operationplan.reference,
                  'at':f.date.isoformat(),'quantity':str(f.quantity),'onhand':str(f.onhand)} for f in b.flowplans]} for b in frepple.buffers()]}
    if spec.get('editor'):
        from p3_fixed_batch.foundation import inspect
        snapshot['editor_model']=inspect(frepple,{'sources':[]},xml)
        snapshot['editor_receipt']=editor_receipt
        snapshot['editor_native_demand_names']=sorted(d.name for d in frepple.demands())
    if procurement:snapshot['procurement']=procurement
    if spec.get('foundation'):
        from p3_fixed_batch.foundation import inspect
        snapshot['foundation_model']=inspect(frepple,spec['foundation'],xml)
        snapshot['foundation_extension_sha256']=hashlib.sha256((Path(__file__).parent/'foundation.py').read_bytes()).hexdigest()
        if restored_loads is not None:snapshot['protected_load_restoration']=restored_loads
        if setup_resolution is not None:snapshot['protected_setup_resolution']=setup_resolution
        if before_resolution is not None:snapshot['protected_setup_native_before']=before_resolution
        if feasibility_refresh is not None:snapshot['native_feasibility_refresh']=feasibility_refresh
        if material_coordination is not None:snapshot['native_material_coordination']=material_coordination
    if spec.get('single_lane_distribution'):
        from p3_fixed_batch.distribution import inspect
        snapshot['distribution_model']=inspect(frepple)
        snapshot['distribution_model']['fix_broken_supply_path']=Parameter.getValue('plan.fixBrokenSupplyPath',database,'true').strip().lower()
        snapshot['distribution_extension_sha256']=hashlib.sha256((Path(__file__).parent/'distribution.py').read_bytes()).hexdigest()
    if spec.get('qualified_operator'):
        from p3_fixed_batch.qualified import inspect
        snapshot['qualified_model']=inspect(frepple)
        snapshot['qualified_model']['fix_broken_supply_path']=Parameter.getValue('plan.fixBrokenSupplyPath',database,'true').strip().lower()
        snapshot['qualified_extension_sha256']=hashlib.sha256((Path(__file__).parent/'qualified.py').read_bytes()).hexdigest()
    if spec.get('two_level_wip'):
        from p3_fixed_batch.wip import describe_operation
        snapshot['wip_model']={'fix_broken_supply_path':Parameter.getValue('plan.fixBrokenSupplyPath',database,'true').strip().lower(),
            'purchase_paths':[o.name for o in frepple.operations() if type(o).__name__=='operation_itemsupplier'],
            'operations':[describe_operation(o) for o in frepple.operations() if not o.hidden],
            'resources':[{'name':r.name,'type':type(r).__name__,'maximum':str(r.maximum),
                          'available':r.available.name if r.available else None} for r in frepple.resources()]}
    if spec.get('two_level_confirmed_supply'):
        from p3_fixed_batch.wip import describe_confirmed_supply
        snapshot['confirmed_supply_model']=describe_confirmed_supply(frepple,xml)
        maxima={o.name:str(o.size_maximum) for o in frepple.operations() if type(o).__name__=='operation_itemsupplier'}
        for path in snapshot['confirmed_supply_model']['paths']:
            path['maximum']=maxima[path['name']]
    failures=[]
    pending=[]
    if spec.get('foundation') and phase=='pre':
        from p3_fixed_batch.foundation import setup_only_pending
        for expected in spec['protected']:
            rows=[p for p in records if p['reference']==expected['reference']]
            if len(rows)==1 and setup_only_pending(expected,rows[0]):
                pending.append(dict(reference=expected['reference'],expected=expected,observed=rows[0]))
        snapshot['protected_setup_pending']=pending
    if snapshot['move_approved_early']!=0:failures.append('moveApprovedEarly')
    for expected in spec['protected']:
        rows=[p for p in records if p['reference']==expected['reference']]
        if len(rows)!=1:failures.append(expected['reference']+':identity');continue
        for k,v in expected.items():
            if k in ('start','end') and any(p['reference']==expected['reference'] for p in pending):
                continue # Explicitly unresolved native intermediate; post remains exact.
            if k=='resource' and spec.get('foundation'):
                if v not in rows[0]['resources']:failures.append(expected['reference']+':resource')
                continue
            if k=='operator' and spec.get('foundation'):
                if v not in rows[0]['resources']:failures.append(expected['reference']+':operator')
                continue
            x=rows[0][k]
            same=Decimal(x)==Decimal(v) if k=='quantity' else x==v
            if not same:failures.append(expected['reference']+':'+k)
    for expected in spec.get('foundation',{}).get('protected_roots',[]):
        rows=[p for p in records if p['reference']==expected['reference']]
        if len(rows)!=1 or rows[0].get('owner') is not None:
            failures.append(expected['reference']+':routing_root_identity');continue
        for key,wanted in expected.items():
            if key=='quantity':same=Decimal(rows[0][key])==Decimal(wanted)
            else:same=rows[0].get(key)==wanted
            if not same:failures.append(expected['reference']+':routing_root_'+key)
    if setup_resolution:
        failures.extend(r['reference']+':setup_resolution' for r in setup_resolution if r['status']!='RESOLVED_NATIVE_SEQUENCE')
    if feasibility_refresh:
        failures.extend(p['reference']+':native_infeasible' for p in records if p['feasible'] is not True)
    snapshot['protected_window_status']=('FAIL' if failures else
        'PENDING_SETUP_NOT_VALIDATED' if pending else 'SOURCE_WINDOWS_MATCH')
    path=root/(spec['run_id']+'.'+phase+'.json')
    path.write_text(json.dumps(snapshot,indent=2),encoding='utf-8')
    if failures:raise ValueError('PROTECTED_MO_DRIFT:'+','.join(failures))

class Active(PlanTask):
    @classmethod
    def getWeight(cls,**kwargs):return 0.1 if 'p3_fixed_batch' in os.environ else -1

@PlanTaskRegistry.register
class BeforeFixedBatchPlan(Active):
    sequence=198
    description='Verify protected P3 manufacturing input in native memory'
    @classmethod
    def run(cls,database='default',**kwargs):capture('pre',database)

@PlanTaskRegistry.register
class AfterFixedBatchPlan(Active):
    sequence=201
    description='Verify protected P3 manufacturing result in native memory'
    @classmethod
    def run(cls,database='default',**kwargs):capture('post',database)
