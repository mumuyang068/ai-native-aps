"""Transactional local import state; original files and data versions are immutable."""
import copy
import hashlib
import json
import re
import sqlite3
import threading
import uuid
from contextlib import contextmanager, nullcontext
from pathlib import Path
from p2.preflight import load_workbook,digest,column,PROFILE
from p21.validation import initial_mappings,evaluate,now,GROUPS,headers_for
from agent import AgentError,redact
from p21.mapping_ai import suggest

class ImportError(ValueError):pass

class ImportStore:
    def __init__(self,root):
        self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True)
        (self.root/'originals').mkdir(exist_ok=True)
        self.db=self.root/'imports.sqlite3';self.lock=threading.RLock()
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, sha TEXT UNIQUE NOT NULL, data TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS versions (id TEXT PRIMARY KEY, task_id TEXT NOT NULL, fingerprint TEXT NOT NULL, data TEXT NOT NULL, UNIQUE(task_id,fingerprint))')
            from p21.canonical_state import initialize
            initialize(db)

    @contextmanager
    def connect(self):
        db=sqlite3.connect(self.db,timeout=20)
        try:yield db;db.commit()
        finally:db.close()

    @contextmanager
    def transaction(self):
        with self.lock:
            db=sqlite3.connect(self.db,timeout=20)
            try:
                db.execute('BEGIN IMMEDIATE');yield db;db.commit()
            except Exception:db.rollback();raise
            finally:db.close()

    def _read(self,db,task_id):
        row=db.execute('SELECT data FROM tasks WHERE id=?',(task_id,)).fetchone()
        if not row:raise ImportError('IMPORT_NOT_FOUND: 找不到导入任务')
        return json.loads(row[0])

    def _write(self,db,task):
        task['updated_at']=now()
        db.execute('UPDATE tasks SET data=? WHERE id=?',(json.dumps(task,ensure_ascii=False),task['id']))

    def _expect(self,t,revision):
        if t['revision']!=revision:raise ImportError('REVISION_CONFLICT: 任务已在其他窗口修改，请刷新后重新核对')

    def _refresh(self,t,connection=None):
        if 'state_change' in t:
            from p21.canonical_state import refresh
            return refresh(t)
        if 'canonical' in t:
            from p21.canonical_io import refresh
            refresh(t)
            with (self.connect() if connection is None else nullcontext(connection)) as db:
                for row in db.execute('SELECT id,data FROM versions WHERE task_id=? ORDER BY rowid DESC',(t['id'],)):
                    version=json.loads(row[1])
                    if version.get('canonical',{}).get('semantic_sha256')==t['canonical']['semantic_sha256'] and version.get('corrections')==t['corrections'] and t['readiness']['confirmed'] and ('source_sync_receipt' not in t or version.get('source_sync_receipt')==t['source_sync_receipt']):
                        t['current_version_id']=row[0];break
            return t
        for name,mapping in t['mappings'].items():
            if mapping['target_sheet']!='订单':continue
            header=next((h for h,v in mapping['columns'].items() if v=='客户编码'),None)
            tab=t['source']['sheets'][name]
            if header in tab['headers']:
                ci=tab['headers'].index(header)
                for row in tab['rows']:
                    if row[ci] is not None:
                        raw=str(row[ci])
                        t['customers'].setdefault(raw,{'customer_id':raw,'name':raw,'method':'SOURCE_VALUE_PENDING_REVIEW'})
        report,readiness,business= evaluate(t)
        t['validation']=report;t['readiness']=readiness
        t['state']='READY_FOR_PLANNING' if readiness['ready'] else 'CONFIRMED_BLOCKED' if readiness['confirmed'] else 'PENDING_REVIEW'
        keys=self._business_keys(t);duplicates=[]
        t['current_version_id']=None
        with (self.connect() if connection is None else nullcontext(connection)) as db:
            if readiness['ready'] and readiness['confirmed']:
                current=db.execute('SELECT id FROM versions WHERE task_id=? AND fingerprint=?',(t['id'],digest(business))).fetchone()
                if current and current[0] in t['version_ids']:t['current_version_id']=current[0]
            for row in db.execute('SELECT id,data FROM versions'):
                version=json.loads(row[1])
                prior_keys=version.get('business_keys')
                if prior_keys is None:
                    business=version['business_data'];plan=business['plan_input']
                    prior_keys={'订单':[x['id'] for x in plan['demand_lines']],
                        '库存':[x['id'] for x in plan['inventory_positions']],
                        '采购台账':[str(x['raw_value']) for x in business.get('lineage',[]) if x['target']=='/retained/purchase_ledger/采购单号']}
                for kind,items in keys.items():
                    prior=prior_keys.get(kind,[])
                    for key in set(items)&set(prior):duplicates.append({'kind':kind,'key':key,'version_id':row[0]})
        t['duplicate_records']=duplicates;t['version_mode']='INDEPENDENT_SNAPSHOT'
        return t

    def _business_keys(self,t):
        keys={}
        for sheet,mapping in t['mappings'].items():
            target=mapping['target_sheet']
            header='采购单号' if target=='采购台账' else '记录编码'
            if target not in ('订单','库存','采购台账'):continue
            source_header=next((h for h,v in mapping['columns'].items() if v==header),None)
            tab=t['source']['sheets'][sheet]
            if source_header in tab['headers']:
                ci=tab['headers'].index(source_header);keys[target]=[str(r[ci]) for r in tab['rows'] if r[ci] is not None]
        return keys

    def list(self):
        with self.connect() as db:tasks=[json.loads(r[0]) for r in db.execute('SELECT data FROM tasks ORDER BY rowid DESC')]
        return [{k:t[k] for k in ['id','filename','file_sha256','revision','state','updated_at','version_ids']} for t in tasks]

    def get(self,task_id):
        with self.connect() as db:t=self._read(db,task_id)
        t=self._refresh(t)
        # Resolve source-package hint origins in this readonly view; retain the
        # stored hint if an earlier development build used the package digest.
        if t.get('source_package'):
            for name,mapping in t['mappings'].items():
                origin=t['source_package']['sheet_origins'][name]
                for candidate in mapping.get('candidates',[]):
                    if candidate.get('file_sha256')!=origin['file_sha256']:
                        candidate['stored_file_sha256']=candidate.get('file_sha256');candidate['file_sha256']=origin['file_sha256']
                        candidate['source_sheet']=origin['source_sheet']
                        candidate['source_header_cell']=origin['source_sheet']+'!'+candidate['source_header_cell'].rsplit('!',1)[-1]
        return t

    def original(self,task_id):return self.root/'originals'/(self.get(task_id)['file_sha256']+'.xlsx')

    def upload_package(self,files):
        if any(f[0]=='business-source.json' for f in files):
            from p21.canonical_io import parse_csv_package
            try:content=parse_csv_package(files,self.root/'originals')
            except (ValueError,KeyError,TypeError) as e:raise ImportError(str(e)) from None
            return self._canonical_task(content)
        from p21.source_package import package_sources
        try:content=package_sources(files,self.root/'originals')
        except (ValueError,KeyError,TypeError) as e:raise ImportError(str(e)) from None
        except Exception:raise ImportError('INVALID_FILE: 无法解析多来源XLSX，请核对原件') from None
        identity=content['file_sha256']
        with self.transaction() as db:
            row=db.execute('SELECT id FROM tasks WHERE sha=?',(identity,)).fetchone()
            if row:
                t=self._refresh(self._read(db,row[0]),db);t['deduplicated']=True;return t
            t={**content,'id':'IMP-'+uuid.uuid4().hex[:12],'revision':1,'customers':{},'confirmations':{},'corrections':[],
               'ai_events':[],'history':[{'at':now(),'action':'SOURCE_PACKAGE_UPLOADED','source_fingerprint':identity}],
               'version_ids':[],'created_at':now(),'updated_at':now(),'deduplicated':False}
            self._reuse_mappings(db,t['source'],t['mappings'],t['source_package']['sheet_origins'])
            self._refresh(t,db)
            db.execute('INSERT INTO tasks VALUES (?,?,?)',(t['id'],identity,json.dumps(t,ensure_ascii=False)))
            return t

    def upload_structured(self,snapshot,*,raw=None):
        """Use P2's original confirmation/version transaction without CSV IO."""
        from p21.canonical_io import structured_content
        try:
            parent=None
            if isinstance(snapshot,dict) and snapshot.get('mode')=='INCREMENTAL_UPSERT':
                parent=self.version(snapshot.get('parent_version_id'))
                self.verify_version_sources(parent,replay_mapping=False)
            content=structured_content(snapshot,self.root/'originals',raw=raw,parent=parent)
        except (ValueError,KeyError,TypeError) as exc:raise ImportError(str(exc)) from None
        return self._canonical_task(content)

    def source_original(self,task_id,file_sha256):
        t=self.get(task_id)
        if not any(f['sha256']==file_sha256 for f in t.get('source_package',{}).get('files',[])):raise ImportError('SOURCE_FILE_NOT_FOUND: 来源不属于此任务')
        record=next(f for f in t['source_package']['files'] if f['sha256']==file_sha256)
        p=self.root/'originals'/(file_sha256+record.get('extension','.xlsx'))
        if not p.is_file():raise ImportError('SOURCE_FILE_NOT_FOUND: 原件缺失')
        if hashlib.sha256(p.read_bytes()).hexdigest()!=file_sha256:raise ImportError('SOURCE_CHANGED: 来源原件哈希不符')
        return p

    def _verify_canonical_sources(self,canonical,source_files,corrections=(),*,replay_mapping=True):
        declared={r['sha256'] for r in source_files}
        def row_file_hashes(value):
            if isinstance(value,dict):
                for k,v in value.items():
                    if k=='file_sha256':yield v
                    else:yield from row_file_hashes(v)
            elif isinstance(value,list):
                for v in value:yield from row_file_hashes(v)
        for kind,rows in canonical['tables'].items():
            refs=canonical['source_refs'].get(kind,[])
            if not isinstance(refs,list) or len(refs)!=len(rows) or any(not isinstance(ref,dict) or ref.get('file_sha256') not in declared for ref in refs):
                raise ImportError('SOURCE_CHANGED: 企业事实行缺少已声明原件引用')
        object_refs=[obj.get('source_refs') for obj in canonical['objects']]
        if any(not isinstance(ref,dict) or ref.get('file_sha256') not in declared for ref in object_refs) or not set(row_file_hashes(canonical['source_refs']))<=declared or not set(row_file_hashes(object_refs))<=declared:
            raise ImportError('SOURCE_CHANGED: 企业事实行引用的来源原件未完整声明')
        for record in source_files:
            # Only source carriers created by upload/upload_package are read.
            if not re.fullmatch('[0-9a-f]{64}',record['sha256']) or record['extension'] not in {'.xlsx','.csv','.json'}:
                raise ImportError('SOURCE_CHANGED: 企业事实原件身份无效')
            p=self.root/'originals'/(record['sha256']+record['extension'])
            if not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest()!=record['sha256']:
                raise ImportError('SOURCE_CHANGED: 企业事实原件校验失败')
        structured=[r for r in source_files if r.get('source_kind') in {'STRUCTURED_SOURCE','STRUCTURED_DELTA'}]
        if structured and replay_mapping:
            from p21.canonical import assemble
            from p21.structured_delta import prepare_delta
            rebuilt=None;mapping=None
            for record in structured:
                from p21.record_mapping import project,enterprise_identity
                p=self.root/'originals'/(record['sha256']+record['extension'])
                original=json.loads(p.read_bytes())
                try:
                    if record['source_kind']=='STRUCTURED_SOURCE':
                        expected,refs,issues,mapping=project(original['tables'],original.get('mapping'),record['sha256'])
                    else:
                        parent={'id':original['parent_version_id'],'canonical':rebuilt,
                            'business_data':{'ingestion':{'mapping':mapping,'mapping_sha256':digest(mapping)}},
                            'confirmations':{k:{'reviewer':'DERIVATION_REPLAY','content_sha256':'DERIVATION_REPLAY'} for k in rebuilt['tables']}}
                        result=prepare_delta(original,parent,sha=record['sha256'])
                        expected,refs,issues,mapping=(result[k] for k in ('tables','source_refs','mapping_issues','mapping'))
                    if issues:raise ValueError('MAPPING_UNRESOLVED')
                    rebuilt=assemble(enterprise_identity(original['enterprise_id'],mapping),expected,
                        planning_at=original['planning_at'],horizon=original['horizon'],timezone=original['timezone'],source_refs=refs)
                    for obj in rebuilt['objects']:obj['factual_status']='CONFIRMED_FACT'
                except (ValueError,KeyError,TypeError,StopIteration) as exc:
                    raise ImportError('SOURCE_CHANGED: 结构化原件与已映射事实不一致') from exc
            try:
                for change in corrections:
                    row=next(r for r in expected[change['table']] if r['id']==change['id'])
                    if row.get(change['field'])!=change['previous_value'] or not change.get('reason'):raise ValueError('CORRECTION_MISMATCH')
                    row[change['field']]=copy.deepcopy(change['value'])
                if (expected!=canonical['tables'] or refs!=canonical['source_refs'] or
                    any(rebuilt[k]!=canonical[k] for k in ('enterprise_id','planning_at','horizon','timezone'))):
                    raise ValueError('STRUCTURED_DERIVATION_MISMATCH')
            except (ValueError,KeyError,TypeError,StopIteration) as exc:
                raise ImportError('SOURCE_CHANGED: 结构化原件与已映射事实不一致') from exc

    def verify_version_sources(self,version,*,replay_mapping=True):
        if version.get('version_mode')=='CONTINUITY_STATE_VERSION':
            from p21.canonical_state import audit
            return audit(self,version)
        if 'canonical' in version:
            with self.connect() as db:stored=db.execute('SELECT fingerprint FROM versions WHERE id=?',(version['id'],)).fetchone()
            if not stored or digest(version['business_data'])!=stored[0] or version['canonical']!=version['business_data']['canonical']:
                raise ImportError('VERSION_CHANGED: 企业事实版本指纹不一致')
            if 'continuity' in version['business_data'] and (version.get('source_sync_receipt')!=version['business_data'].get('source_sync_receipt') or version.get('parent_version_id')!=version['business_data']['continuity'].get('parent_version_id')):
                raise ImportError('VERSION_CHANGED: 连续版本元数据与业务指纹不一致')
            self._verify_canonical_sources(version['canonical'],version['business_data']['source_files'],version['business_data'].get('corrections',[]),replay_mapping=replay_mapping)
            if 'continuity' in version['business_data']:
                from p21.continuity_version import validate_receipt
                try:validate_receipt(version['source_sync_receipt'],version['canonical']['enterprise_id'])
                except ValueError as exc:raise ImportError(str(exc)) from None
            return
        if version['business_data'].get('source_package'):
            from p21.source_package import original_sources
            with self.connect() as db:stored=db.execute('SELECT fingerprint FROM versions WHERE id=?',(version['id'],)).fetchone()
            if not stored or digest(version['business_data'])!=stored[0]:raise ImportError('VERSION_CHANGED: 业务版本指纹不一致')
            try:original_sources(version,self.root/'originals',require_all=True)
            except ValueError as e:raise ImportError(str(e)) from None
        else:
            original=self.original(version['import_id'])
            if not original.is_file() or hashlib.sha256(original.read_bytes()).hexdigest()!=version['file_sha256']:raise ImportError('SOURCE_CHANGED: 原始文件校验失败')

    def upload(self,filename,content):
        filename=filename.replace('\\','/').split('/')[-1]
        if not filename.lower().endswith('.xlsx') or len(filename)>160 or any(ord(c)<32 for c in filename):raise ImportError('INVALID_FILE: 请上传 XLSX 文件')
        if not content or len(content)>10*1024*1024:raise ImportError('INVALID_FILE: 文件为空或超过 10 MB')
        sha=hashlib.sha256(content).hexdigest()
        with self.transaction() as db:
            row=db.execute('SELECT id FROM tasks WHERE sha=?',(sha,)).fetchone()
            if row:
                t=self._read(db,row[0]);t=self._refresh(t);t['deduplicated']=True;return t
            original=self.root/'originals'/(sha+'.xlsx')
            if not original.exists():
                with original.open('xb') as f:f.write(content)
            try:source=load_workbook(original,header_rows='detect')
            except Exception as e:raise ImportError('INVALID_FILE: '+redact(type(e).__name__)+': 无法解析工作簿，请确认格式、大小与行列范围') from None
            source['file']=filename
            if '业务快照' in source['sheets']:
                from p21.canonical_io import parse_workbook
                try:
                    source=load_workbook(original,{name:1 for name in source['sheets']})
                    canonical=parse_workbook(source,sha)
                except (ValueError,KeyError,TypeError) as exc:raise ImportError('CANONICAL_SOURCE_INVALID: '+str(exc)) from None
                return self._canonical_task({'canonical':canonical,'source':source,'filename':filename,'file_sha256':sha,
                    'source_files':[{'filename':filename,'sha256':sha,'extension':'.xlsx','source_kind':'UPLOADED_FILE'}]},db)
            if len(source['sheets'])>40:raise ImportError('INVALID_FILE: 最多支持 40 张工作表，请拆分上传')
            mappings=initial_mappings(source);self._reuse_mappings(db,source,mappings);customers={}
            for name,tab in source['sheets'].items():
                if mappings[name]['target_sheet']=='订单' and '客户编码' in tab['headers']:
                    ci=tab['headers'].index('客户编码')
                    for row in tab['rows']:
                        if row[ci] is not None:
                            value=str(row[ci]);customers[value]={'customer_id':value,'name':value,'method':'SOURCE_VALUE_PENDING_REVIEW'}
            t={'id':'IMP-'+uuid.uuid4().hex[:12],'filename':filename,'file_sha256':sha,'revision':1,
               'source':source,'mappings':mappings,'customers':customers,'confirmations':{},'corrections':[],
               'ai_events':[],'history':[{'at':now(),'action':'UPLOADED','file_sha256':sha}],
               'version_ids':[],'created_at':now(),'updated_at':now(),'deduplicated':False}
            self._refresh(t)
            db.execute('INSERT INTO tasks VALUES (?,?,?)',(t['id'],sha,json.dumps(t,ensure_ascii=False)))
            return t

    def _canonical_task(self,content,connection=None):
        with (self.transaction() if connection is None else nullcontext(connection)) as db:
            row=db.execute('SELECT id FROM tasks WHERE sha=?',(content['file_sha256'],)).fetchone()
            if row:
                t=self._refresh(self._read(db,row[0]),db);t['deduplicated']=True;return t
            t={**copy.deepcopy(content),'id':'IMP-'+uuid.uuid4().hex[:12],'revision':1,'customers':{},'mappings':{},
               'confirmations':{},'corrections':[],'ai_events':[],'history':[{'at':now(),'action':'CANONICAL_SOURCE_UPLOADED'}],
               'version_ids':[],'created_at':now(),'updated_at':now(),'deduplicated':False}
            t['source_package']={'kind':'CANONICAL_SOURCES','files':copy.deepcopy(content['source_files'])}
            self._refresh(t,db)
            db.execute('INSERT INTO tasks VALUES(?,?,?)',(t['id'],t['file_sha256'],json.dumps(t,ensure_ascii=False)))
            return t

    def _reuse_mappings(self,db,source,mappings,source_origins=None):
        """Reuse only an unambiguous confirmed layout; never values or approval."""
        versions=[json.loads(row[0]) for row in db.execute('SELECT data FROM versions')]
        for name,m in mappings.items():
            matches={}
            for version in versions:
                prior=version.get('mappings',{}).get(name)
                if not version.get('planning_ready') or not set(GROUPS).issubset(version.get('confirmations',{})) or not prior:continue
                target=prior.get('target_sheet');columns=prior.get('columns',{})
                if set(columns)!=set(m['columns']) or any(v and v not in headers_for(target) for v in columns.values()):continue
                key=digest({'target_sheet':target,'columns':columns})
                matches[key]=(version['id'],prior)
            if len(matches)==1:
                vid,prior=next(iter(matches.values()))
                m.update(target_sheet=prior['target_sheet'],columns=copy.deepcopy(prior['columns']),method='REUSED_CONFIRMED_MAPPING',reused_from_version_id=vid)
                origin=(source_origins or {}).get(name,{})
                m['candidates']=[{'source_sheet':origin.get('source_sheet',name),'source_column':h,'target_sheet':m['target_sheet'],
                    'target_field':v,'reason':'复用已确认版本 '+vid+' 的相同表头布局；新数值仍需确认','requires_confirmation':True,
                    'file_sha256':origin.get('file_sha256',source['sha256']),'source_header_cell':origin.get('source_sheet',name)+'!'+column(source['sheets'][name]['headers'].index(h)+1)+str(source['sheets'][name]['header_row'])}
                    for h,v in m['columns'].items()]
            elif len(matches)>1:
                m['reuse_conflict']={'status':'NEEDS_CONFIRMATION','reason':'相同表头存在不同的已确认映射，不自动选择其中一个',
                                     'version_ids':sorted(v[0] for v in matches.values())}

    def _cell(self,t,sheet,ref,value,reason):
        tab=t['source']['sheets'].get(sheet)
        if not tab or not re.fullmatch(r'[A-Z]{1,2}[1-9][0-9]{0,4}',ref):raise ImportError('INVALID_CELL: 请选择有效工作表和单元格')
        letters=re.match('[A-Z]+',ref)[0];col=0
        for c in letters:col=col*26+ord(c)-64
        row=int(ref[len(letters):]);ri=row-tab['header_row']-1
        if ri<0 or col>len(tab['headers']) or ri>20000:raise ImportError('INVALID_CELL: 仅可修正表头之后的数据单元格')
        if isinstance(value,(list,dict)) or (isinstance(value,float) and not __import__('math').isfinite(value)):raise ImportError('INVALID_VALUE: 请填写文本、有限数值或明确布尔值')
        while len(tab['rows'])<=ri:tab['rows'].append([None]*len(tab['headers']))
        previous=tab['rows'][ri][col-1];original=tab.get('cells',{}).get(ref)
        tab['rows'][ri][col-1]=value
        if ref in tab['formulas']:tab['formulas'].remove(ref)
        t['corrections'].append({'sheet':sheet,'cell':ref,'original_value':original,'previous_value':previous,'value':value,'reason':reason,'at':now()})

    def edit(self,task_id,revision,change):
        with self.transaction() as db:
            t=self._read(db,task_id);self._expect(t,revision)
            if 'state_change' in t:raise ImportError('SOURCE_CHANGE_REQUIRES_NEW_POSTIMAGE: 请在来源更正后重新预览同步')
            reason=change.get('reason','').strip()
            if not reason:raise ImportError('CHANGE_REASON_REQUIRED: 请说明核对依据或修正原因')
            kind=change['kind']
            if 'canonical' in t:
                if kind!='canonical_fact':raise ImportError('INVALID_EDIT: 请选择企业事实字段')
                from p21.canonical import assemble,apply_source_state
                kind_name=change['table'];field=change['field']
                if field in {'id','entity_id','source_id','source_sha256'}:raise ImportError('IDENTITY_CHANGE_REQUIRES_SOURCE: 身份和来源须由新原件核对')
                rows=t['canonical']['tables'];row=next((r for r in rows.get(kind_name,[]) if r['id']==change['id']),None)
                if row is None:raise ImportError('BUSINESS_OBJECT_NOT_FOUND')
                old=copy.deepcopy(row.get(field));row[field]=copy.deepcopy(change['value'])
                t['corrections'].append({'table':kind_name,'id':row['id'],'field':field,'previous_value':old,
                    'value':copy.deepcopy(change['value']),'reason':reason,'at':now()})
                c=t['canonical'];t['canonical']=assemble(c['enterprise_id'],rows,planning_at=c['planning_at'],horizon=c['horizon'],timezone=c['timezone'],source_refs=c['source_refs'])
                if 'connector_state' in t:apply_source_state(t['canonical'],t['connector_state'])
                t['confirmations']={};t['revision']+=1;t['history'].append({'at':now(),'action':'FACT_CORRECTED','change':copy.deepcopy(change)})
                self._refresh(t,db);self._write(db,t);return t
            if kind=='material_association' and t.get('source_package'):
                from p21.source_package import mapped_rows
                masters={str(r['记录编码']) for _,_,r in mapped_rows(t,'产品物料')}
                reviewer=change.get('reviewer','').strip();target=change.get('target_id')
                if not reviewer or len(reviewer)>80 or reviewer.startswith('SIMULATED_'):raise ImportError('REVIEWER_REQUIRED: 请填写关联核对人')
                if target not in masters:raise ImportError('MATERIAL_ASSOCIATION_INVALID: 目标不是ERP物料主档身份')
                matches=[a for a in t['material_associations'] if a['source_system']==change.get('source_system') and a['source_code']==change.get('source_code')]
                if len(matches)!=1:raise ImportError('MATERIAL_ASSOCIATION_NOT_FOUND: 来源物料身份不明确')
                matches[0].update(target_id=target,confirmed=True,reviewer=reviewer,reason=reason,at=now())
            elif kind=='cell':self._cell(t,change['sheet'],change['cell'],change.get('value'),reason)
            elif kind=='customer':
                customer=change.get('customer_id','').strip()
                if customer=='CUSTOMER-DEMO':raise ImportError('CUSTOMER_PLACEHOLDER_FORBIDDEN: 请使用企业客户标识')
                t['customers'][change['source_id']]={'customer_id':customer,'name':change.get('name','').strip() or customer,'method':'HUMAN_EDIT'}
            elif kind=='mapping':
                m=t['mappings'].get(change['sheet']);field=change.get('target_field')
                if m is None or change['source_column'] not in m['columns'] or (field and field not in headers_for(m['target_sheet'])):raise ImportError('INVALID_MAPPING: 字段不属于当前业务对象')
                m['columns'][change['source_column']]=field or None;m['method']='HUMAN_EDIT'
            elif kind=='sheet_mapping':
                name=change['sheet'];target=change.get('target_sheet')
                if target=='使用说明' and name!='使用说明':raise ImportError('BUSINESS_RECORDS_CANNOT_BE_IGNORED: 不能把业务记录改成说明页来绕过校验，请保留并核对这些记录')
                if name not in t['mappings'] or target not in [*PROFILE['sheets'],*PROFILE['auxiliary'],'工厂参数','使用说明']:raise ImportError('INVALID_MAPPING: 请选择已支持的业务对象')
                t['mappings'][name]['target_sheet']=target
                allowed=headers_for(target)
                t['mappings'][name]['columns']={h:(h if h in allowed else None) for h in t['source']['sheets'][name]['headers'] if h is not None}
            elif kind=='header_row':
                name=change['sheet'];n=change['header_row']
                choices={s:m['header_row'] for s,m in t['mappings'].items()};choices[name]=n
                if t.get('source_package'):
                    link=t['source_package']['sheet_origins'][name];raw=link['source_sheet']
                    tab=load_workbook(self.root/'originals'/(link['file_sha256']+'.xlsx'),header_rows={raw:n})['sheets'][raw]
                    t['source']['sheets'][name]=tab;t['mappings'][name]=initial_mappings({'sha256':link['file_sha256'],'sheets':{raw:tab}})[raw]
                else:
                    updated=load_workbook(self.root/'originals'/(t['file_sha256']+'.xlsx'),header_rows=choices)
                    t['source']['sheets'][name]=updated['sheets'][name]
                    t['mappings'][name]=initial_mappings({'sheets':{name:updated['sheets'][name]}})[name]
                for correction in t['corrections']:
                    if correction['sheet']==name:
                        ref=correction['cell'];tab=t['source']['sheets'][name];ri=int(re.search(r'\d+$',ref)[0])-n-1
                        ci=0
                        for c in re.match('[A-Z]+',ref)[0]:ci=ci*26+ord(c)-64
                        if 0<=ri<len(tab['rows']) and ci<=len(tab['headers']):tab['rows'][ri][ci-1]=correction['value']
            elif kind=='add_sheet':
                if t.get('source_package'):raise ImportError('SOURCE_PACKAGE_TABLE_UNSUPPORTED: 来源包请提供有身份的原件，不创建无来源业务表')
                target=change['target_sheet'];name='补充-'+target
                if name in t['source']['sheets'] or target not in [*PROFILE['sheets'],*PROFILE['auxiliary'],'工厂参数']:raise ImportError('INVALID_MAPPING: 已有补充表或不支持此对象')
                heads=headers_for(target)
                rr=[]
                if target=='工厂参数':
                    for k in PROFILE['settings']:rr.append([k,None,'MANUAL','p21','等待用户补充事实与规则'])
                else:rr=[[None]*len(heads)]
                t['source']['sheets'][name]={'headers':heads,'rows':rr,'formulas':[],'header_row':5,'cells':{}}
                t['mappings'][name]={'target_sheet':target,'header_row':5,'columns':{h:h for h in heads},'method':'HUMAN_ADDED'}
            elif kind=='append_row':
                tab=t['source']['sheets'].get(change['sheet'])
                if not tab or len(tab['rows'])>=20000:raise ImportError('INVALID_CELL: 工作表不存在或已达到行数限制')
                tab['rows'].append([None]*len(tab['headers']))
            else:raise ImportError('INVALID_EDIT: 不支持此修改')
            if t.get('source_package') and kind!='material_association':
                for association in t['material_associations']:association['confirmed']=False
            t['confirmations']={};t['revision']+=1
            t['history'].append({'at':now(),'action':'EDITED','change':copy.deepcopy(change)})
            self._refresh(t);self._write(db,t);return t

    def confirm(self,task_id,revision,reviewer,groups):
        if not reviewer.strip() or len(reviewer)>80 or reviewer.startswith('SIMULATED_'):raise ImportError('REVIEWER_REQUIRED: 请填写核对人姓名或本地标识')
        with self.transaction() as db:
            t=self._read(db,task_id);self._expect(t,revision)
            allowed=t.get('canonical_groups',GROUPS)
            if not groups or any(g not in allowed for g in groups):raise ImportError('INVALID_REVIEW_GROUP: 请选择需要确认的业务内容')
            if 'state_change' in t:
                content=digest(t['state_change'])
                for g in groups:t['confirmations'][g]={'reviewer':reviewer.strip(),'at':now(),'content_sha256':content,'method':'DOMAIN_SOURCE_REVIEW'}
                t['revision']+=1;t['history'].append({'at':now(),'action':'HUMAN_CONFIRMED','reviewer':reviewer,'groups':groups})
                self._refresh(t,db);self._write(db,t);return t
            if 'canonical' in t:
                content=digest({'semantic_sha256':t['canonical']['semantic_sha256'],
                    'source_refs':t['canonical']['source_refs'],'corrections':t['corrections']})
                for g in groups:t['confirmations'][g]={'reviewer':reviewer.strip(),'at':now(),'content_sha256':content,'method':'DOMAIN_SOURCE_REVIEW'}
                for obj in t['canonical']['objects']:
                    if obj['object_type'] in groups:obj['factual_status']='CONFIRMED_FACT'
                t['revision']+=1;t['history'].append({'at':now(),'action':'HUMAN_CONFIRMED','reviewer':reviewer,'groups':groups})
                self._refresh(t,db);self._write(db,t);return t
            for g in groups:t['confirmations'][g]={'reviewer':reviewer.strip(),'at':now(),'content_sha256':digest({'source':t['source'],'mappings':t['mappings'],'customers':t['customers'],**({'material_associations':t['material_associations'],'source_package':t['source_package']} if t.get('source_package') else {})})}
            t['revision']+=1;t['history'].append({'at':now(),'action':'HUMAN_CONFIRMED','reviewer':reviewer,'groups':groups})
            self._refresh(t);self._write(db,t);return t

    def _prepare_continuity_parents(self,task_id,revision,parent_version_id,sync_receipt):
        """Materialize only full-save parent/replay states, before taking a write lock.

        Compact commits deliberately have no Canonical payload. Their original
        fingerprint stays authoritative; derived cache writes must never nest
        beneath save_version's BEGIN IMMEDIATE transaction.
        """
        if sync_receipt is None:return {}
        from p21.continuity_version import validate_receipt,source_chain_scope,source_vector,version_enterprise
        with self.connect() as db:
            task=self._read(db,task_id);self._expect(task,revision)
            if 'canonical' not in task:return {}
            source=validate_receipt(sync_receipt,task['canonical']['enterprise_id'])
            rows=db.execute("SELECT fingerprint,data FROM versions WHERE json_extract(data,'$.version_mode')='CONTINUITY_STATE_VERSION'").fetchall()
        prepared={}
        for fingerprint,raw in rows:
            prior=json.loads(raw)
            prior_source=prior.get('business_data',{}).get('source_sync_receipt',{})
            replay=(version_enterprise(prior)==source['enterprise_id']
                and source_chain_scope(prior_source)==source_chain_scope(source)
                and source_vector(prior_source)==source_vector(source))
            if prior['id']!=parent_version_id and not replay:continue
            if digest(prior['business_data'])!=fingerprint:raise ImportError('VERSION_CHANGED: 父版本业务指纹不一致')
            actual=self.version(prior['id'])
            self.verify_version_sources(actual)
            prepared[prior['id']]=(fingerprint,digest(prior),actual)
        return prepared

    def save_version(self,task_id,revision,*,parent_version_id=None,sync_receipt=None):
        try:prepared=self._prepare_continuity_parents(task_id,revision,parent_version_id,sync_receipt)
        except ValueError as exc:raise ImportError(str(exc)) from None
        with self.transaction() as db:
            t=self._read(db,task_id);self._expect(t,revision)
            if 'state_change' in t:
                from p21.canonical_state import save
                return save(self,db,t,parent_version_id,sync_receipt)
            if 'canonical' in t:
                self._refresh(t,db)
                if not t['readiness']['confirmed']:raise ImportError('CONFIRMATION_REQUIRED: 请分域核对企业事实')
                if not t['readiness']['data_accepted']:raise ImportError('INVALID_BUSINESS_FACTS: 请处理事实类型、身份和关系问题；规划政策未知可以保留')
                business={'schema_version':'canonical-business-v1','canonical':copy.deepcopy(t['canonical']),
                          'source_files':copy.deepcopy(t['source_files']),'corrections':copy.deepcopy(t['corrections'])}
                if t.get('ingestion'):business['ingestion']=copy.deepcopy(t['ingestion'])
                self._verify_canonical_sources(business['canonical'],business['source_files'],business['corrections'])
                parent_id=t['version_ids'][-1] if t['version_ids'] else t.get('parent_version_id')
                if sync_receipt is not None:
                    from p21.continuity_version import validate_receipt,bind
                    try:
                        source=validate_receipt(sync_receipt,t['canonical']['enterprise_id'])
                        rows=list(db.execute('SELECT fingerprint,data FROM versions'))
                        versions=[json.loads(row[1]) for row in rows]
                        for index,(row,prior) in enumerate(zip(rows,versions)):
                            if (prior['id']==parent_version_id or prior['id'] in prepared) and digest(prior['business_data'])!=row[0]:
                                raise ImportError('VERSION_CHANGED: 父版本业务指纹不一致')
                            if prior['id'] in prepared:
                                fingerprint,raw_sha,actual=prepared[prior['id']]
                                if row[0]!=fingerprint or digest(prior)!=raw_sha:
                                    raise ImportError('VERSION_CHANGED: 已物化父版本发生变化')
                                versions[index]=actual
                        continuity,duplicate=bind(t['canonical'],source,parent_version_id,versions)
                    except ValueError as exc:raise ImportError(str(exc)) from None
                    if duplicate:
                        t['current_version_id']=duplicate
                        return {'version_id':duplicate,'deduplicated':True,'task':t}
                    parent_id=parent_version_id
                    business.update(source_sync_receipt=source,continuity=continuity)
                    t['source_sync_receipt']=copy.deepcopy(source)
                elif parent_version_id is not None or 'source_sync_receipt' in t:
                    raise ImportError('SOURCE_SYNC_RECEIPT_REQUIRED: 连续版本必须显式提供来源同步回执')
                fingerprint=digest(business)
                old=db.execute('SELECT id FROM versions WHERE task_id=? AND fingerprint=?',(task_id,fingerprint)).fetchone()
                if old:return {'version_id':old[0],'deduplicated':True,'task':t}
                if t.get('ingestion',{}).get('mode')=='INCREMENTAL_UPSERT':
                    parent=t['ingestion']['parent_version_id']
                    prior=db.execute('SELECT fingerprint,data FROM versions WHERE id=?',(parent,)).fetchone()
                    if not prior or digest(json.loads(prior[1])['business_data'])!=t['ingestion']['parent_business_sha256'] or prior[0]!=t['ingestion']['parent_business_sha256']:
                        raise ImportError('VERSION_CHANGED: 增量父版本内容不一致')
                    if any(json.loads(r[0]).get('parent_version_id')==parent for r in db.execute('SELECT data FROM versions')):
                        raise ImportError('CONTINUITY_HEAD_CONFLICT: 增量父版本已存在后继，请重新读取并核对')
                vid='DATA-'+uuid.uuid4().hex[:12]
                version={'id':vid,'import_id':task_id,'import_revision':t['revision'],'created_at':now(),
                    'filename':t['filename'],'file_sha256':t['file_sha256'],'business_data':business,
                    'canonical':copy.deepcopy(t['canonical']),'mappings':{},'confirmations':copy.deepcopy(t['confirmations']),
                    'corrections':copy.deepcopy(t['corrections']),'planning_ready':False,'engine_executed':False,
                    'version_mode':'CANONICAL_BUSINESS_VERSION','parent_version_id':parent_id}
                if sync_receipt is not None:version['source_sync_receipt']=copy.deepcopy(source)
                version['business_keys']={'订单':[r['id'] for r in t['canonical']['tables'].get('demand_lines',[])],
                    '库存':[r['id'] for r in t['canonical']['tables'].get('inventory_positions',[])],
                    '采购台账':[r['id'] for r in t['canonical']['tables'].get('purchase_orders',[])]}
                db.execute('INSERT INTO versions VALUES(?,?,?,?)',(vid,task_id,fingerprint,json.dumps(version,ensure_ascii=False)))
                t['version_ids'].append(vid);t['revision']+=1;t['history'].append({'at':now(),'action':'VERSION_SAVED','version_id':vid})
                self._write(db,t);return {'version_id':vid,'deduplicated':False,'task':self._refresh(t,db)}
            if parent_version_id is not None or sync_receipt is not None:
                raise ImportError('CANONICAL_CONTINUITY_REQUIRED: 显式连续版本仅支持企业事实合同')
            report,readiness,business=evaluate(t)
            if not readiness['confirmed']:raise ImportError('CONFIRMATION_REQUIRED: 请先完成人工核对')
            if not readiness['ready']:raise ImportError('PLANNING_NOT_READY: 仍有影响计算正确性的问题，请先处理')
            fingerprint=digest(business)
            existing=db.execute('SELECT id FROM versions WHERE task_id=? AND fingerprint=?',(task_id,fingerprint)).fetchone()
            if existing:return {'version_id':existing[0],'deduplicated':True,'task':self._refresh(t,db)}
            vid='DATA-'+uuid.uuid4().hex[:12]
            version={'id':vid,'import_id':task_id,'import_revision':t['revision'],'created_at':now(),
                'filename':t['filename'],'file_sha256':t['file_sha256'],'business_data':business,
                'mappings':t['mappings'],'confirmations':t['confirmations'],'corrections':t['corrections'],
                'planning_ready':True,'engine_executed':False,'version_mode':'INDEPENDENT_SNAPSHOT',
                'business_keys':self._business_keys(t),
                'parent_version_id':t['version_ids'][-1] if t['version_ids'] else None}
            if t.get('source_package'):version['source_package']=copy.deepcopy(business['source_package'])
            db.execute('INSERT INTO versions VALUES (?,?,?,?)',(vid,task_id,fingerprint,json.dumps(version,ensure_ascii=False)))
            t['version_ids'].append(vid);t['revision']+=1;t['history'].append({'at':now(),'action':'VERSION_SAVED','version_id':vid})
            self._write(db,t);return {'version_id':vid,'deduplicated':False,'task':self._refresh(t,db)}

    def versions(self):
        keys=['id','import_id','import_revision','filename','created_at','planning_ready','engine_executed']
        # Library summaries must not deserialize every enterprise snapshot.
        with self.connect() as db:
            business_at="COALESCE(json_extract(data,'$.business_data.state_commit.source_as_of'),json_extract(data,'$.canonical.planning_at'),json_extract(data,'$.business_data.plan_input.planning_at'))"
            data=db.execute('SELECT '+','.join("json_extract(data,'$."+k+"')" for k in keys)+','+business_at+' FROM versions ORDER BY rowid DESC').fetchall()
        keys.append('business_as_of')
        return [dict(zip(keys,[bool(v) if k in {'planning_ready','engine_executed'} else v for k,v in zip(keys,row)])) for row in data]

    def version_directory_metadata(self,version_ids):
        """The existing library time contract, restricted to one directory page.

        SQL projects scalar fields only; no version()/state materialization,
        source hash, confirmation, or full Canonical snapshot is performed.
        """
        if (not isinstance(version_ids,list) or not 1<=len(version_ids)<=20
            or any(not isinstance(vid,str) or not 1<=len(vid)<=100 for vid in version_ids)
            or len(set(version_ids))!=len(version_ids)):raise ImportError('QUERY_PAGE_BUDGET')
        business_at="COALESCE(json_extract(data,'$.business_data.state_commit.source_as_of'),json_extract(data,'$.canonical.planning_at'),json_extract(data,'$.business_data.plan_input.planning_at'))"
        with self.connect() as db:
            rows=db.execute("SELECT id,json_extract(data,'$.filename'),"+business_at+
                " FROM versions WHERE id IN ("+','.join('?' for _ in version_ids)+')',version_ids).fetchall()
        return {row[0]:dict(id=row[0],source_name=row[1],business_as_of=row[2],basis='ORIGINAL_IMPORT_LIBRARY_BUSINESS_SNAPSHOT') for row in rows}

    def save_execution_checkpoint(self,parent_version_id,request,*,parent_plan_id,parent_snapshot_sha256):
        """Derive one immutable version in the existing transactional version table."""
        from execution_checkpoint import carry_forward
        from p22.contracts import validate_version,enterprise_native,GROUPS as CHECK_GROUPS
        from planning_adapter import InputError
        with self.transaction() as db:
            row=db.execute('SELECT data FROM versions WHERE id=?',(parent_version_id,)).fetchone()
            if not row:raise InputError('VERSION_NOT_FOUND')
            parent=json.loads(row[0]);validate_version(parent)
            if parent['business_data'].get('execution_checkpoint'):raise InputError('NESTED_CHECKPOINT_UNSUPPORTED: only one declared prefix from the original version')
            original=self.root/'originals'/(parent['file_sha256']+'.xlsx')
            if not original.is_file() or hashlib.sha256(original.read_bytes()).hexdigest()!=parent['file_sha256']:raise InputError('SOURCE_CHANGED')
            output,proof=carry_forward(parent['business_data']['plan_input'],request)
            record={'source_input':copy.deepcopy(parent['business_data']['plan_input']),'request':copy.deepcopy(request),'proof':proof,
                    'parent_data_version_id':parent_version_id,'parent_data_version_sha256':digest(parent),
                    'parent_plan_id':parent_plan_id,'parent_snapshot_sha256':parent_snapshot_sha256}
            # Existing immutable versions are the deduplication receipts. No new
            # event ledger/table and no mutation of the parent's import task.
            for row in db.execute('SELECT id,data FROM versions'):
                old=json.loads(row[1]).get('business_data',{}).get('execution_checkpoint')
                if not old:continue
                if old['proof']['checkpoint_id']==proof['checkpoint_id']:
                    if old!=record:raise InputError('IDEMPOTENCY_CONFLICT: checkpoint id already binds different facts')
                    return {'version_id':row[0],'deduplicated':True,'checkpoint':proof}
                if old['parent_data_version_id']==parent_version_id and set(old['proof']['accepted_event_ids']) & set(proof['accepted_event_ids']):
                    raise InputError('CHECKPOINT_EVENTS_ALREADY_APPLIED: do not reapply the same history under another checkpoint id')
            business=copy.deepcopy(parent['business_data']);business.update(plan_input=output,execution_checkpoint=record)
            business['demand_customer_links']={d['id']:business['demand_customer_links'][d['id']] for d in output['demand_lines']}
            for item in proof['after']:
                business['lineage'].append({'target':'/inventory_positions/'+item['item_id'],'cell':'checkpoint:'+proof['checkpoint_id'],
                    'source_record_id':proof['checkpoint_id'],'normalized_value':copy.deepcopy(item),'method':'CONFIRMED_EXECUTION_PREFIX'})
            business['native_preview']=enterprise_native(output,business)
            version=copy.deepcopy(parent);vid='DATA-'+uuid.uuid4().hex[:12]
            content=digest(record)
            version.update(id=vid,parent_version_id=parent_version_id,created_at=now(),business_data=business,
                           version_mode='EXECUTION_CHECKPOINT',engine_executed=False,
                           source_confirmations=copy.deepcopy(parent['confirmations']),
                           confirmations={g:{'reviewer':request['confirmation']['reviewer'],'at':now(),'content_sha256':content,
                                              'method':'EXECUTION_PREFIX_CONFIRMED'} for g in CHECK_GROUPS})
            validate_version(version)
            db.execute('INSERT INTO versions VALUES(?,?,?,?)',(vid,parent['import_id'],digest(business),json.dumps(version,ensure_ascii=False)))
            return {'version_id':vid,'deduplicated':False,'checkpoint':proof}

    def version(self,version_id):
        with self.connect() as db:row=db.execute('SELECT data FROM versions WHERE id=?',(version_id,)).fetchone()
        if not row:raise ImportError('VERSION_NOT_FOUND: 找不到数据版本')
        version=json.loads(row[0])
        if version.get('version_mode')=='CONTINUITY_STATE_VERSION':
            from p21.canonical_state import materialized_version
            return materialized_version(self,version)
        return version

    def version_metadata(self,version_id):
        from p21.canonical_state import version_metadata
        return version_metadata(self,version_id)

    def continuity_meta(self,version_id):
        from p21.canonical_state import ensure
        return ensure(self,version_id)

    def continuity_rows(self,version_id,system,table,*,ids=None,where=None):
        from p21.canonical_state import rows
        return rows(self,version_id,system,table,ids=ids,where=where)

    def upload_continuity_changes(self,parent_version_id,**values):
        from p21.canonical_state import upload
        return upload(self,parent_version_id,**values)

    def materialize_continuity(self,version_id):
        from p21.canonical_state import materialize
        return materialize(self,version_id)

    def business_view(self,version_id,**query):
        from p21.business_view import read
        try:return read(self,version_id,**query)
        except ValueError as exc:raise ImportError(str(exc)) from None

    def history_context(self,version_id):
        from p21.history_index import context
        return context(self,version_id)

    def history_query(self,version_id,**query):
        from p21.history_index import query as read
        return read(self,version_id,**query)

    def business_facts(self,version_id):
        from p21.business_facts import project
        version=self.version(version_id)
        with self.connect() as db:
            fingerprint=db.execute('SELECT fingerprint FROM versions WHERE id=?',(version_id,)).fetchone()[0]
            task=db.execute('SELECT data FROM tasks WHERE id=?',(version['import_id'],)).fetchone()
        if version.get('version_mode')!='CONTINUITY_STATE_VERSION' and digest(version['business_data'])!=fingerprint:raise ImportError('VERSION_CHANGED: 确认业务版本与保存指纹不一致')
        if 'canonical' in version:
            self.verify_version_sources(version)
            from p21.canonical_io import refresh
            stage_view={'canonical':version['canonical'],'confirmations':version['confirmations'],
                'input_transport':'STRUCTURED_JSON' if version['business_data'].get('ingestion') else 'FILE'}
            refresh(stage_view)
            return {'schema_version':'canonical-business-view-v1','data_version_id':version['id'],
                    'status':version['canonical']['data_status'],'canonical':copy.deepcopy(version['canonical']),
                    'confirmations':copy.deepcopy(version['confirmations']),'planning_ready':version['planning_ready'],
                    'ingestion_stages':stage_view['ingestion_stages']}
        original=self.root/'originals'/(version['file_sha256']+'.xlsx')
        source=None
        if version['business_data'].get('source_package'):
            from p21.source_package import original_sources
            try:source,statuses=original_sources(version,self.root/'originals')
            except ValueError as e:raise ImportError(str(e)) from None
            view=project(version,source,imported_at=json.loads(task[0]).get('created_at') if task else None)
            view.update(source_files=statuses,material_associations=copy.deepcopy(version['source_package']['material_associations']),reconciliation=copy.deepcopy(version['source_package']['reconciliation']))
            view['source_file'].update(kind='SOURCE_PACKAGE',status='HASH_VERIFIED' if all(f['status']=='HASH_VERIFIED' for f in statuses) else 'NOT_CHECKED')
            view['source_package_fingerprint']=version['source_package']['confirmed_fingerprint']
            return view
        if original.is_file():
            if hashlib.sha256(original.read_bytes()).hexdigest()!=version['file_sha256']:raise ImportError('SOURCE_CHANGED: 原始文件哈希不一致')
            source=load_workbook(original,{name:m['header_row'] for name,m in version.get('mappings',{}).items()})
        return project(version,source,imported_at=json.loads(task[0]).get('created_at') if task else None)

    def propose(self,task_id,revision,note=''):
        t=self.get(task_id);self._expect(t,revision)
        if not isinstance(note,str) or len(note)>1500:raise ImportError('INVALID_IMPORT_INPUT: 补充说明最多 1500 字')
        event=suggest(t,note=note) if note else suggest(t)
        event['user_clarification']=note
        with self.transaction() as db:
            current=self._read(db,task_id);self._expect(current,revision)
            current['ai_events'].append(event);current['revision']+=1
            current['history'].append({'at':now(),'action':'AI_PROPOSED','event_id':event['id']})
            self._write(db,current);return self._refresh(current,db)

    def proposal_decision(self,task_id,revision,proposal_id,decision):
        with self.transaction() as db:
            t=self._read(db,task_id);self._expect(t,revision)
            p=next((p for event in t['ai_events'] for p in event['proposals'] if p['id']==proposal_id),None)
            if not p:raise ImportError('PROPOSAL_NOT_FOUND: 找不到候选')
            if p.get('kind')=='CANONICAL_RELATION':
                if decision!='REVERT':raise ImportError('PROPOSAL_REQUIRES_FACT_REVIEW: 关联建议只供核对，请通过原事实修正与分域确认处理')
                p['status']='REVERTED';t['revision']+=1
                t['history'].append({'at':now(),'action':'AI_REVERT','proposal_id':proposal_id})
                self._refresh(t,db);self._write(db,t);return t
            m=t['mappings'][p['sheet']]['columns']
            if decision=='APPLY':
                if p['status']!='PROPOSED' or not p['target_field']:raise ImportError('PROPOSAL_NOT_APPLICABLE: 该候选仍需业务澄清')
                p['previous_mapping']=m[p['source_column']];m[p['source_column']]=p['target_field'];p['status']='APPLIED'
            elif decision=='REVERT':
                if p['status']=='APPLIED':
                    if m[p['source_column']]!=p['target_field']:raise ImportError('REVISION_CONFLICT: 字段后来已被修改，请人工核对')
                    m[p['source_column']]=p['previous_mapping']
                p['status']='REVERTED'
            else:raise ImportError('INVALID_DECISION: 仅支持应用或撤销候选')
            t['confirmations']={};t['revision']+=1;t['history'].append({'at':now(),'action':'AI_'+decision,'proposal_id':proposal_id})
            self._refresh(t,db);self._write(db,t);return t
