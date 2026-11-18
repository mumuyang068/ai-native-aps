"""Read-only current visible sources from a separate sandbox enterprise Owner."""
import base64
import copy
import json
import re
import threading
from urllib.parse import urlparse
import requests

from p21.demo_connector import SYSTEMS,digest,instant,validate_source_document
from p22.continuity_sources import ENTERPRISE,PROFILE,build_snapshot


class LiveSourceHTTPError(ValueError):
    """Keep status provenance for writers; existing readers retain ValueError."""
    def __init__(self,code,status):
        super().__init__(code);self.status=status


class LiveSourceClient:
    def __init__(self,configuration,token):
        url=configuration['base_url'].rstrip('/')
        parsed=urlparse(url)
        if parsed.scheme!='http' or parsed.hostname not in {'localhost','127.0.0.1'} or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
            raise ValueError('LIVE_SOURCE_LOCALHOST_REQUIRED')
        self.base=url;self.token=token
        self.branch=configuration['branch_id']
        if not re.fullmatch('[A-Za-z0-9_-]{1,60}',self.branch):raise ValueError('LIVE_SOURCE_BRANCH_INVALID')
    def request(self,path,params=None,*,body=None):
        try:
            response=requests.request('POST' if body is not None else 'GET',self.base+path,
                params=params,json=body,headers={'Authorization':'Bearer '+self.token},timeout=20,allow_redirects=False)
            if len(response.content)>10*1024*1024:raise ValueError('LIVE_SOURCE_RESPONSE_TOO_LARGE')
            value=response.json()
        except (requests.RequestException,ValueError) as exc:
            raise ValueError('LIVE_SOURCE_UNAVAILABLE') from None
        if response.status_code!=200:
            if response.status_code==404 and path.endswith('/source-checkpoint'):raise ValueError('LIVE_SOURCE_CHECKPOINT_UNSUPPORTED')
            detail=value.get('detail',{}) if isinstance(value,dict) else {}
            code=detail.get('code','LIVE_SOURCE_REQUEST_REJECTED') if isinstance(detail,dict) else 'LIVE_SOURCE_REQUEST_REJECTED'
            if not isinstance(code,str) or not re.fullmatch('[A-Z][A-Z0-9_]{0,100}',code):code='LIVE_SOURCE_REQUEST_REJECTED'
            raise LiveSourceHTTPError(code,response.status_code)
        return value


class LiveEnterpriseConnector:
    def __init__(self,configuration,*,fetch=None):
        c=copy.deepcopy(configuration)
        if c.get('schema')!='live-enterprise-connector/1' or c.get('enterprise_id')!=ENTERPRISE:raise ValueError('LIVE_SOURCE_IDENTITY_MISMATCH')
        if not re.fullmatch('live-[A-Za-z0-9_-]{1,60}',c.get('id','')):raise ValueError('LIVE_CONNECTOR_ID_INVALID')
        self.enterprise_id=ENTERPRISE;self.branch_id=c['branch_id'];self.id=c['id'];self.name=c['name']
        self.fingerprint=digest(c);self.local=threading.local()
        self.fetch=fetch or LiveSourceClient(c,c['read_token']).request
    def _path(self,part):return '/v1/sim/'+self.branch_id+'/'+part
    def _view(self,revision=None):
        view=self.fetch(self._path('view'),{} if revision is None else {'revision':revision})
        if view.get('enterprise_id')!=self.enterprise_id or view.get('branch_id')!=self.branch_id or type(view.get('revision')) is not int or view['revision']<0:
            raise ValueError('LIVE_SOURCE_IDENTITY_MISMATCH')
        if set(view.get('documents',{}))!=set(SYSTEMS) or set(view.get('declarations',{}))!={'definition','operating','reference'}:raise ValueError('LIVE_SOURCE_SCOPE_INVALID')
        if revision is not None and view['revision']!=revision:raise ValueError('LIVE_SOURCE_REVISION_MISMATCH')
        for doc in view['documents'].values():self._scope(doc,view)
        return view
    def _scope(self,document,view):
        if (document.get('branch_id')!=self.branch_id or document.get('source_revision')!=view['revision']
            or document.get('as_of')!=view['as_of'] or document.get('enterprise_id')!=self.enterprise_id):
            raise ValueError('LIVE_SOURCE_SCOPE_MISMATCH')
    def _checkpoint(self,revision=None):
        try:
            value=self.fetch(self._path('source-checkpoint'),{} if revision is None else {'revision':revision})
        except ValueError as exc:
            if str(exc)!='LIVE_SOURCE_CHECKPOINT_UNSUPPORTED':raise
            # Explicit legacy server compatibility; this is a full view, not
            # claimed to be an incremental or lightweight source checkpoint.
            value=self._view(revision);value['checkpoint_mode']='LEGACY_FULL_VIEW_FALLBACK'
            return value
        if (value.get('schema')!='source-checkpoint/1' or value.get('enterprise_id')!=self.enterprise_id or
                value.get('branch_id')!=self.branch_id or type(value.get('revision')) is not int or value['revision']<0):raise ValueError('LIVE_SOURCE_IDENTITY_MISMATCH')
        if revision is not None and value['revision']!=revision:raise ValueError('LIVE_SOURCE_REVISION_MISMATCH')
        if set(value.get('declarations',{}))!={'definition','operating','reference'}:raise ValueError('LIVE_SOURCE_SCOPE_INVALID')
        instant(value['as_of']);value['checkpoint_mode']='AUTHENTICATED_SOURCE_CHECKPOINT'
        return value
    def checkpoint(self,checkpoint):
        if checkpoint!='CURRENT':raise ValueError('CONNECTOR_CHECKPOINT_NOT_FOUND')
        self.local.view=self._checkpoint()
        return dict(as_of=self.local.view['as_of'],revision=self.local.view['revision'],
            checkpoint_mode=self.local.view['checkpoint_mode'],
            source_scope=dict(connector_id=self.id,branch_id=self.branch_id,source_revision=self.local.view['revision']))
    def _captured(self):
        if not hasattr(self.local,'view'):raise ValueError('LIVE_SOURCE_CAPTURE_REQUIRED')
        return self.local.view
    def describe_source(self):
        v=None;error=None
        try:v=self._checkpoint()
        except (ValueError,KeyError,TypeError):error='LIVE_SOURCE_UNAVAILABLE'
        return dict(id=self.id,name=self.name,enterprise_id=self.enterprise_id,branch_id=self.branch_id,
            mode='LIVE_SIMULATION',mapping_profile=PROFILE,source_schema='source-visibility/1',
            transport='LOCALHOST_VISIBLE_SOURCE_API',modes=['FULL_SNAPSHOT','INCREMENTAL_UPSERT'],
            cursor_strategy='IMMUTABLE_BRANCH_VIEW_REVISION_AND_SOURCE_AS_OF',
            checkpoints=[dict(id='CURRENT',as_of=v['as_of'])] if v else [],current_source_revision=v['revision'] if v else None,
            source_availability='AVAILABLE' if v else 'UNAVAILABLE',source_error=error,
            systems=[dict(id=s,name=s,source_id=self.enterprise_id+'/'+self.branch_id+'/'+s,mapping_profile=PROFILE+'/'+s) for s in SYSTEMS],
            automatic_sync=False,automatic_solve=False,execution_authorized=False)
    def _reference(self,revision,kind,name,document,previous=None):
        raw=json.dumps(document,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
        import hashlib
        sha=hashlib.sha256(raw).hexdigest()
        key=f'live/{revision}/{kind}/{name}'
        if previous is not None:key+='/'+base64.urlsafe_b64encode(previous.encode()).decode().rstrip('=')
        return document,dict(key=key+'/'+sha,sha256=sha),raw
    def read_snapshot(self,system,checkpoint):
        if checkpoint!='CURRENT' or system not in SYSTEMS:raise ValueError('LIVE_SOURCE_SCOPE_INVALID')
        v=self._captured()
        if 'documents' not in v:
            complete=self._view(v['revision'])
            if complete['as_of']!=v['as_of']:raise ValueError('LIVE_SOURCE_SCOPE_MISMATCH')
            self.local.view=v=complete
        doc=copy.deepcopy(v['documents'][system])
        validate_source_document(doc,system,v['as_of'],'FULL_SNAPSHOT')
        if doc['as_of']!=v['as_of']:raise ValueError('SOURCE_CHECKPOINT_MISMATCH')
        return self._reference(v['revision'],'snapshot',system,doc)
    def read_changes(self,system,checkpoint,previous_as_of):
        v=self._captured()
        if checkpoint!='CURRENT' or system not in SYSTEMS or instant(previous_as_of)>instant(v['as_of']):raise ValueError('LIVE_SOURCE_SCOPE_INVALID')
        doc=self.fetch(self._path('changes'),dict(system=system,revision=v['revision'],previous_as_of=previous_as_of))
        self._scope(doc,v)
        validate_source_document(doc,system,v['as_of'],'INCREMENTAL_UPSERT')
        if doc.get('previous_as_of')!=previous_as_of or doc['as_of']!=v['as_of']:raise ValueError('INCREMENTAL_CURSOR_GAP')
        return [self._reference(v['revision'],'changes',system,doc,previous_as_of)]
    def declaration_keys(self):
        v=self._captured()
        return [self._reference(v['revision'],'declaration',k,d)[1]['key'] for k,d in v['declarations'].items()]
    def declarations(self):
        return copy.deepcopy(self._captured()['declarations'])
    def verify_read_set(self,keys):
        revisions=set();views={};full={}
        for key in keys:
            parts=key.split('/')
            if len(parts) not in {5,6} or parts[0]!='live' or not parts[1].isdigit():raise ValueError('LIVE_SOURCE_REFERENCE_INVALID')
            revision=int(parts[1]);kind,name,expected=parts[2],parts[3],parts[-1];revisions.add(revision)
            if revision not in views:views[revision]=self._checkpoint(revision)
            v=views[revision]
            if kind=='snapshot':
                if revision not in full:full[revision]=self._view(revision)
                current=self._reference(revision,kind,name,full[revision]['documents'][name])
            elif kind=='declaration':current=self._reference(revision,kind,name,v['declarations'][name])
            elif kind=='changes' and len(parts)==6:
                previous=base64.urlsafe_b64decode(parts[4]+'='*((4-len(parts[4])%4)%4)).decode()
                doc=self.fetch(self._path('changes'),dict(system=name,revision=revision,previous_as_of=previous))
                self._scope(doc,v)
                current=self._reference(revision,kind,name,doc,previous)
            else:raise ValueError('LIVE_SOURCE_REFERENCE_INVALID')
            if current[1]['sha256']!=expected:raise ValueError('SOURCE_CHANGED')
        if len(revisions)!=1:raise ValueError('LIVE_SOURCE_MIXED_REVISIONS')
        self.fetch(self._path('verify'),{'revision':next(iter(revisions))})
    def build_snapshot(self,documents):
        # Branch/revision already live in every source document preserved in
        # the original continuity_bundle. Do not mutate the sealed mapping.
        return build_snapshot(**self._captured()['declarations'],documents=documents)
    def test_connection(self):
        try:
            view=self._view()
            for system,doc in view['documents'].items():validate_source_document(doc,system,view['as_of'],'FULL_SNAPSHOT')
            return dict(status='CONNECTED',systems=[dict(system=s,status='CONNECTED') for s in SYSTEMS],errors=[])
        except (ValueError,KeyError,TypeError):
            return dict(status='UNAVAILABLE',systems=[dict(system=s,status='UNAVAILABLE') for s in SYSTEMS],errors=[dict(code='LIVE_SOURCE_UNAVAILABLE')])
