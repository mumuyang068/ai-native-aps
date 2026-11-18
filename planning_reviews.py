"""Non-executable human review notes, bound to existing comparable run evidence."""
import hashlib
import json
import re
import uuid
from datetime import datetime, timezone

def _states(value):
    if isinstance(value,dict):
        for k,v in value.items():
            if k in {'status','business_status'} and isinstance(v,str):yield v
            if isinstance(v,(dict,list)):yield from _states(v)
    elif isinstance(value,list):
        for v in value:yield from _states(v)

def review_gate(projection,target):
    from planning_adapter import InputError
    identity=projection['identity']
    found={c['target']:c for c in projection['candidates']}
    if target=='baseline' or target not in found:raise InputError('REVIEW_CANDIDATE_REQUIRED')
    base,candidate=found.get('baseline'),found[target]
    for c in [base,candidate]:
        if not c or not c.get('run_id') or c.get('calculation_state')!='SUCCEEDED' or c.get('comparable') is not True:
            raise InputError('REVIEW_NOT_COMPARABLE')
        if any(s in {'FAIL','INVALID_SCHEDULE','FAILED'} for s in _states(c)):raise InputError('REVIEW_HARD_FAIL')
        validation=c.get('validation',{})
        if any(validation.get(k,{}).get('status')!='PASS' for k in ['material','schedule']):raise InputError('REVIEW_AUDIT_REQUIRED')
        if validation.get('resources') is not None and validation['resources'].get('status')!='PASS':raise InputError('REVIEW_AUDIT_REQUIRED')
        if c.get('business_status') not in {'FEASIBLE_ON_TIME','LATE_DELIVERY','PARTIAL_OR_UNPLANNED'}:raise InputError('REVIEW_BUSINESS_STATUS')
        expected=dict(plan_id=identity['plan_id'],data_version_id=identity['data_version_id'],run_id=c['run_id'],target=c['target'],profile=identity['profile'])
        actual=c.get('professional',{}).get('identity',{})
        if any(actual.get(k)!=v for k,v in expected.items()):raise InputError('REVIEW_IDENTITY_MISMATCH')
        sources=c.get('professional',{}).get('evidence_files')
        if not isinstance(sources,list) or not sources or any(not isinstance(s,dict) or not s.get('artifact') or not re.fullmatch('[a-f0-9]{64}',s.get('sha256','')) for s in sources):raise InputError('REVIEW_SOURCE_REQUIRED')
    if base['run_id']!=identity['baseline_run_id']:raise InputError('REVIEW_BASELINE_MISMATCH')
    payload=dict(identity=identity,baseline=base,candidate=candidate)
    fingerprint=hashlib.sha256(json.dumps(payload,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
    unknowns=sorted({s for s in _states(payload) if s in {'NOT_CHECKED','NOT_COLLECTED','UNSUPPORTED','UNREADABLE','INVALID_EVIDENCE','PARTIAL','NOT_ATTESTED'}})
    # Every note carries the existing limits, including absent universal input/build attestation.
    unknowns=sorted(set(unknowns)|{'INPUT_READBACK_PARTIAL','BINARY_BUILD_NOT_ATTESTED','EXECUTION_NOT_AUTHORIZED'})
    return dict(identity={**identity,'target':target,'candidate_run_id':candidate['run_id']},fingerprint=fingerprint,unknowns=unknowns,
        evidence=dict(baseline=base['professional'].get('evidence_files',[]),candidate=candidate['professional'].get('evidence_files',[])),
        business_status=candidate['business_status'],simulation_only=True)

def make_note(gate,fingerprint,recommendation,reviewer,reason,object_scope,next_action,acknowledge_limits):
    from planning_adapter import InputError
    if fingerprint!=gate['fingerprint']:raise InputError('REVIEW_EVIDENCE_CHANGED')
    if recommendation not in {'DISCUSS','RECOMMEND_DISCUSSION','DEFER'}:raise InputError('REVIEW_INVALID_RECOMMENDATION')
    if gate['unknowns'] and acknowledge_limits is not True:raise InputError('REVIEW_LIMITS_ACK_REQUIRED')
    for text,maximum in [(reviewer,100),(reason,2000),(object_scope,1000),(next_action,1000)]:
        if not isinstance(text,str) or not text.strip() or len(text)>maximum:raise InputError('REVIEW_INVALID_TEXT')
    return dict(id='REVIEW-'+uuid.uuid4().hex[:16],**gate,recommendation=recommendation,reviewer=reviewer.strip(),
        reviewer_identity_kind='LOCAL_SELF_REPORTED_NOT_AUTHENTICATED',reason=reason.strip(),object_scope=object_scope.strip(),
        next_action=next_action.strip(),acknowledge_limits=True,created_at=datetime.now(timezone.utc).isoformat(),
        execution_authorized=False,approval_granted=False)

def note_state(note,gate):
    return 'CURRENT_REVIEW' if gate and note['fingerprint']==gate['fingerprint'] and note['identity']==gate['identity'] else 'HISTORY_NEEDS_RECHECK'

def save_note(store,note):
    with store.db() as db:
        db.execute('CREATE TABLE IF NOT EXISTS review_notes (id TEXT PRIMARY KEY, data TEXT NOT NULL)')
        db.execute('INSERT INTO review_notes VALUES (?,?)',(note['id'],json.dumps(note,ensure_ascii=False)))

def read_notes(store):
    with store.db() as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='review_notes'").fetchone():return []
        return [json.loads(row[0]) for row in db.execute('SELECT data FROM review_notes ORDER BY rowid DESC')]
