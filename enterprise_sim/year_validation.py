"""Independent annual reconciliation from accepted-event Decimal ledgers.

No World, simulator qualification, or simulator state replay is an audit oracle.
Business payload fields come only from the independent audit's accepted events;
quantities, relationships and execution results come from its Decimal ledger.
"""
import copy
from datetime import datetime
from decimal import Decimal
from .validation import validate_events


def instant(value):
    result=datetime.fromisoformat(value.replace('Z','+00:00'))
    if result.utcoffset() is None:raise ValueError('timezone required')
    return result


def _operation_identity(payload):
    key=payload['job_id']+'::'+payload['operation_id']
    return key+'::SEG:'+payload['segment_id'] if 'segment_id' in payload else key


def _terminal_from_ledger(definition,ledger):
    expected={name:{} for name in ['orders','purchase_orders','lots','jobs','operations','finished','shipments']}
    expected.update(resource_blocks=[],last_at=None)
    for event in ledger['accepted_events']:
        p=copy.deepcopy(event['payload']);kind=event['kind'];at=event['occurred_at']
        expected['last_at']=at
        if kind=='SalesOrderCreated':
            expected['orders'][p['order_id']]={**p,'created_at':at}
        elif kind=='SalesOrderChanged':
            expected['orders'][p['order_id']].update({k:v for k,v in p.items() if k in ['quantity','due','priority']})
        elif kind=='PurchaseOrderCreated':
            expected['purchase_orders'][p['po_id']]=p
        elif kind=='PurchaseETAChanged':
            expected['purchase_orders'][p['po_id']].update(eta=p['eta'],eta_changed_at=at)
        elif kind=='GoodsReceived':
            expected['lots'][p['lot_id']]={**p,'received_at':at}
        elif kind=='ManufacturingTaskCreated':
            expected['jobs'][p['job_id']]=p
        elif kind=='OperationStarted':
            expected['operations'][_operation_identity(p)]={**p,'start':at}
        elif kind=='ResourceUnavailable':
            expected['resource_blocks'].append({**p,'announced_at':at})
        elif kind=='ShipmentPosted':
            expected['shipments'][event['event_id']]={**p,'shipped_at':at}
    for key,row in ledger['orders'].items():
        expected['orders'][key].update(quantity=row['quantity'],shipped=row['shipped'],
            status='OPEN' if not row['shipped'] else 'SHIPPED' if row['shipped']==row['quantity'] else 'PARTIAL')
    for key,row in ledger['pos'].items():
        expected['purchase_orders'][key].update(quantity=row['quantity'],received=row['received'],
            outstanding=row['quantity']-row['received'],
            status='OPEN' if not row['received'] else 'RECEIVED' if row['received']==row['quantity'] else 'PARTIAL')
    for key,row in ledger['lots'].items():
        expected['lots'][key].update({k:row[k] for k in ['received','pending','available','held','consumed']})
        expected['lots'][key]['physical']=row['received']-row['consumed']
    for key,row in ledger['jobs'].items():
        route=definition['products'][row['product']]['routing']
        started=any(identity[0]==key for identity in ledger['ops'])
        status='RELEASED' if not started else 'COMPLETED' if all(row['completed'].get(op,Decimal(0))==row['quantity'] for op in route) else 'WIP'
        expected['jobs'][key].update(quantity=row['quantity'],issued=row['issued'],completed=row['completed'],
            fg_received=row['fg'],shipped=row['shipped'],status=status)
        if row['fg']>0:expected['finished'][key]=row['fg']-row['shipped']
    completions={_operation_identity(e['payload']):e['occurred_at']
                 for e in ledger['accepted_events'] if e['kind']=='OperationCompleted'}
    interruptions={_operation_identity(e['payload']):e for e in ledger['accepted_events'] if e['kind']=='OperationInterrupted'}
    for identity,row in ledger['ops'].items():
        job,op=identity[:2];key=job+'::'+op+('::SEG:'+identity[2] if len(identity)==3 else '')
        value=expected['operations'][key]
        value.update(quantity=row.get('quantity',ledger['jobs'][job]['quantity']),status='COMPLETED' if row['complete'] else 'STARTED')
        if key in interruptions:
            event=interruptions[key]
            value.update(status='INTERRUPTED',actual_end=event['occurred_at'],actual_quantity=row['actual_quantity'],
                         interruption_reason=event['payload']['reason'])
        elif row['complete']:value['actual_end']=completions[key]
    return expected


def _compare_terminal(actual,expected,issues,where=''):
    def mismatch(location):issues.append(dict(code='TERMINAL_STATE_MISMATCH',where=location or 'state'))
    if isinstance(expected,dict):
        if not isinstance(actual,dict):
            mismatch(where);return
        if set(actual)!=set(expected):mismatch((where+'/' if where else '')+'identities')
        for key,value in expected.items():
            location=(where+'/' if where else '')+str(key)
            if key not in actual:mismatch(location)
            else:_compare_terminal(actual[key],value,issues,location)
    elif isinstance(expected,list):
        if not isinstance(actual,list):
            mismatch(where);return
        if len(actual)!=len(expected):mismatch(where+'/identities')
        for index,value in enumerate(expected):
            if index<len(actual):_compare_terminal(actual[index],value,issues,where+'/'+str(index))
    elif isinstance(expected,Decimal):
        try:
            value=Decimal(str(actual));equal=value.is_finite() and value==expected
        except Exception:equal=False
        if not equal:mismatch(where)
    elif actual!=expected:mismatch(where)


def audit_year(declarations,events,*,terminal_state,as_of,prefix_events=None,require_complete_year=False):
    definition=declarations['definition'];operating=declarations['operating']
    source_ops={r['id']:r for r in declarations['reference']['tables']['operations']}
    def roles_valid(op,person,machine,begin,end):
        skill=source_ops.get(op,{}).get('required_operator_skill')
        def covers(row):
            try:return instant(row['effective_from'])<=begin<end<=instant(row['effective_to'])
            except (ValueError,TypeError,KeyError,AttributeError):return False
        person_valid=any(r['resource_id']==person and r['skill_id']==skill and covers(r)
                         for r in operating['research_qualifications'])
        machine_valid=any(r['resource_id']==machine and r['operation_id']==op and covers(r)
                          for r in operating['research_machine_qualifications'])
        return person_valid,machine_valid
    def assignment(_,event):
        p=event['payload']
        return all(roles_valid(p['operation_id'],p['operator_id'],p['resource_id'],
                              instant(event['occurred_at']),instant(p['end'])))
    report=validate_events(definition,events,assignment_validator=assignment,include_ledger=True)
    ledger=report.pop('ledger');issues=report['issues']
    _compare_terminal(terminal_state,_terminal_from_ledger(definition,ledger),issues)
    # The independent execution ledger replaces planned end with actual end on
    # completion. Recheck that full occupancy interval without simulator policy.
    for identity,row in ledger['ops'].items():
        job,op=identity[:2]
        if not row['complete']:continue
        person,machine=roles_valid(op,row['person'],row['machine'],row['start'],row['end'])
        for valid,code in [(person,'OPERATOR_QUALIFICATION_NOT_EFFECTIVE'),(machine,'MACHINE_QUALIFICATION_NOT_EFFECTIVE')]:
            if not valid:issues.append(dict(code=code,where='operations/'+job+'::'+op+'/actual_interval'))
    if any(instant(e['occurred_at'])>instant(as_of) for e in events):issues.append(dict(code='FUTURE_ACTUAL_EVENT'))
    if prefix_events is not None and events[:len(prefix_events)]!=prefix_events:issues.append(dict(code='FROZEN_PREFIX_MISMATCH'))
    if require_complete_year and instant(as_of)!=instant(operating['year_policy']['history_end']):issues.append(dict(code='YEAR_TERMINAL_CLOCK_MISMATCH'))
    report.update(status='FAIL' if issues else 'PASS',as_of=as_of,
        scope='COMPLETE_YEAR' if require_complete_year else 'SUPPORTED_CHECKPOINT',
        terminal_reconciled=not any(i['code']=='TERMINAL_STATE_MISMATCH' for i in issues),
        source='INDEPENDENT_EVENT_DECIMAL_LEDGER_NOT_WORLD_REPLAY')
    return report
