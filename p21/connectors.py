"""Executable offline snapshot/change reader feeding the existing ImportStore.

No production endpoint or credentials. A source record revision, cursor prefix
and explicit domain identity are required; source conflicts never pick a winner.
"""
import copy
import hashlib
import json
from pathlib import Path

from scripts.data_link_guard import SourceGuard
from p21.canonical import DOMAIN, assemble, digest, apply_source_state

SYSTEMS=('SIM-ERP','SIM-MES','SIM-WMS','SIM-PLM')
TABLE_SYSTEM={
    **{k:'SIM-ERP' for k in ['customers','sales_orders','demand_lines','suppliers','supply_sources','purchase_orders','forecasts','sales_history']},
    **{k:'SIM-MES' for k in ['routings','operations','calendars','resources','operators','skills','resource_skills','machine_qualifications','capacity_windows','setup_transitions','existing_manufacturing_orders']},
    **{k:'SIM-WMS' for k in ['locations','inventory_positions','inventory_targets','historical_manufacturing_orders','history_events','checkpoints']},
    **{k:'SIM-PLM' for k in ['items','recipes','bom_lines']},
}


def fixture_sources(source, *, enterprise_id):
    """Change carrier only: values remain the frozen SIM facts, never new facts."""
    result={s:{'source_system':s,'enterprise_id':enterprise_id,'simulation':True,
        **{k:copy.deepcopy(source[k]) for k in ['planning_at','horizon','timezone']},
        'domain_tables':[kind for kind in source['tables'] if TABLE_SYSTEM[kind]==s],'events':[]} for s in SYSTEMS}
    for kind,rows in source['tables'].items():
        for row in rows:
            result[TABLE_SYSTEM[kind]]['events'].append({'object_type':kind,'business_id':row['id'],
                'source_revision':1,'operation':'UPSERT','payload':copy.deepcopy(row)})
    return result


class OfflineSourceReader:
    def __init__(self, paths):
        self.paths={k:Path(p) for k,p in paths.items()}
        self.guard=SourceGuard(self.paths.values(),next(iter(self.paths.values())).parent/'unused-copy-target')

    def load(self, system):
        raw=self.guard.read_bytes(self.paths[system])
        if len(raw)>10*1024*1024:raise ValueError('SOURCE_TOO_LARGE')
        doc=json.loads(raw)
        if doc['source_system']!=system or doc.get('simulation') is not True:raise ValueError('OFFLINE_SOURCE_IDENTITY_REQUIRED')
        if not isinstance(doc['events'],list):raise ValueError('INVALID_SOURCE_EVENTS')
        return doc,raw


def synchronize(store, reader, *, previous_task_id=None, page_size=50, max_pages=None):
    if not 1<=page_size<=1000:raise ValueError('INVALID_PAGE_SIZE')
    previous=store.get(previous_task_id) if previous_task_id else None
    # Do not invent empty domains simply because a later software revision
    # supports them. Explicit empty declarations are retained; old carriers
    # without a declaration discover their domains from actual source events.
    tables=copy.deepcopy(previous['canonical']['tables']) if previous else {}
    state=copy.deepcopy(previous['connector_state']) if previous else {'cursors':{},'prefixes':{},'objects':{},'conflicts':[],'retractions':[],'source_files':{},'row_refs':{}}
    # Latest file per system is a cursor receipt, not the entire row lineage.
    history=state.setdefault('source_history',{})
    for record in (previous or {}).get('source_files',[]):history[record['sha256']]=copy.deepcopy(record)
    receipts=[];errors=[];changed=0;pages=0;meta=None;complete=True
    for system in sorted(reader.paths):
        try:
            doc,raw=reader.load(system)
            declared=doc.get('domain_tables',[])
            if not isinstance(declared,list) or any(k not in DOMAIN for k in declared):raise ValueError('INVALID_DOMAIN_MAPPING')
            for kind in declared:tables.setdefault(kind,[])
            descriptor={k:doc[k] for k in ['enterprise_id','planning_at','horizon','timezone']}
            if meta is None:meta=descriptor
            elif meta!=descriptor:raise ValueError('SOURCE_SCOPE_MISMATCH')
            if previous and any(descriptor[k]!=previous['canonical'][k] for k in descriptor):raise ValueError('SOURCE_SCOPE_MISMATCH')
            cursor=state['cursors'].get(system,0);events=doc['events']
            if cursor>len(events) or (cursor and state['prefixes'].get(system)!=digest(events[:cursor])):
                raise ValueError('SOURCE_CURSOR_REWRITTEN')
            sha=hashlib.sha256(raw).hexdigest()
            path=store.root/'originals'/(sha+'.json')
            if not path.exists():path.write_bytes(raw)
            state['source_files'][system]={'filename':system+'.json','sha256':sha,'extension':'.json','source_kind':'SIMULATED_CONNECTOR','source_system':system}
            state['source_history'][sha]=copy.deepcopy(state['source_files'][system])
            while cursor<len(events):
                if max_pages is not None and pages>=max_pages:complete=False;break
                end=min(cursor+page_size,len(events))
                page_before=(copy.deepcopy(tables),copy.deepcopy(state),len(receipts),changed)
                try:
                    for position in range(cursor,end):
                        event=events[position];kind=event['object_type'];key=event['business_id'];revision=event['source_revision']
                        if kind not in DOMAIN or not isinstance(revision,int) or isinstance(revision,bool) or revision<1:raise ValueError('INVALID_SOURCE_RECORD')
                        tables.setdefault(kind,[])
                        if event['operation'] not in {'UPSERT','RETRACT'}:raise ValueError('INVALID_SOURCE_OPERATION')
                        if event['operation']=='UPSERT' and event['payload'].get('id')!=key:raise ValueError('SOURCE_NATURAL_KEY_MISMATCH')
                        token=digest([kind,key]);old=state['objects'].get(token)
                        if old and old['source_system']!=system:
                            conflict={'table':kind,'business_id':key,'existing_source':old['source_system'],'incoming_source':system,
                                'source_revision':revision,'incoming':copy.deepcopy(event),'code':'SOURCE_CONFLICT'}
                            if conflict not in state['conflicts']:state['conflicts'].append(conflict)
                            receipts.append({'source_system':system,'business_id':key,'status':'CONFLICT_REQUIRES_REVIEW'});continue
                        if old and revision<=old['revision']:
                            status='STALE_IGNORED' if revision<old['revision'] else 'DUPLICATE_IGNORED' if old['event_sha256']==digest(event) else 'REVISION_CONFLICT'
                            if status=='REVISION_CONFLICT':state['conflicts'].append({'table':kind,'business_id':key,'source_system':system,'incoming':copy.deepcopy(event),'code':status})
                            receipts.append({'source_system':system,'business_id':key,'status':status});continue
                        old_row=next((r for r in tables[kind] if r['id']==key),None)
                        if event['operation']=='RETRACT':
                            state['retractions']=[r for r in state['retractions'] if (r['table'],r['business_id'])!=(kind,key)]
                            state['retractions'].append({'table':kind,'business_id':key,'source_system':system,'source_revision':revision,'status':'SOURCE_RETRACTED'})
                        else:
                            if old_row is None:tables[kind].append(copy.deepcopy(event['payload']))
                            else:old_row.clear();old_row.update(copy.deepcopy(event['payload']))
                            state['retractions']=[r for r in state['retractions'] if (r['table'],r['business_id'])!=(kind,key)]
                        state['objects'][token]={'source_system':system,'revision':revision,'event_sha256':digest(event)}
                        state['row_refs'][token]={'source_system':system,'source_record_key':key,'source_revision':revision,
                            'file_sha256':sha,'event_cursor':position,'operation':event['operation']}
                        receipts.append({'source_system':system,'business_id':key,'status':'APPLIED','revision':revision});changed+=1
                except (OSError,ValueError,KeyError,TypeError):
                    tables,state,receipt_count,changed=page_before
                    receipts=receipts[:receipt_count]
                    raise
                cursor=end;pages+=1;state['cursors'][system]=cursor;state['prefixes'][system]=digest(events[:cursor])
            if cursor<len(events):complete=False
        except (OSError,ValueError,KeyError,TypeError) as exc:
            errors.append({'source_system':system,'code':str(exc).split(':',1)[0],'status':'SOURCE_UNAVAILABLE_OR_INVALID'});complete=False
    if meta is None:
        if not previous:raise ValueError('NO_SOURCE_SNAPSHOT')
        meta={k:previous['canonical'][k] for k in ['enterprise_id','planning_at','horizon','timezone']}
    refs={kind:[state['row_refs'].get(digest([kind,r['id']]),{}) for r in rows] for kind,rows in tables.items()}
    c=assemble(meta['enterprise_id'],tables,planning_at=meta['planning_at'],horizon=meta['horizon'],timezone=meta['timezone'],source_refs=refs)
    state['complete']=complete;state['source_errors']=errors
    apply_source_state(c,state)
    content={'canonical':c,'source':{'sheets':{}},'source_files':[state['source_history'][sha] for sha in sorted(state['source_history'])],
        'filename':'离线多来源同步 · '+meta['enterprise_id'],'file_sha256':digest({'canonical':c['semantic_sha256'],'state':state}),
        'connector_state':state,'parent_version_id':previous.get('current_version_id') if previous else None}
    task=store._canonical_task(content)
    return {'status':'CONNECTOR_CONTRACT_VERIFIED_OFFLINE' if complete else 'CONNECTOR_SYNC_INCOMPLETE_OFFLINE','complete':complete,'task':task,'receipts':receipts,
            'changed_objects':changed,'source_errors':errors,'live_connected':False}
