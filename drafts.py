"""Only scenario changes; the baseline and enterprise plan have no mutation API."""
import copy
import hashlib
import json
import re
import sqlite3
import uuid
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo
from jsonschema import Draft202012Validator
from planning_adapter import validate_input, InputError
from demand_priority import CHANGE_SCHEMA as DEMAND_PRIORITY_SCHEMA
from forecast_override import CHANGE_SCHEMA as FORECAST_OVERRIDE_SCHEMA
from foundation_forecast import CHANGE_SCHEMA as FOUNDATION_FORECAST_SCHEMA, ACTION as FOUNDATION_FORECAST_ACTION
from p22.professional_policy import CHANGE_SCHEMA as PROFESSIONAL_POLICY_SCHEMA, ACTION as PROFESSIONAL_POLICY_ACTION
from accessory_scenario import CHANGE_SCHEMA as ACCESSORY_SCHEMA
from firm_scenario import CHANGE_SCHEMA as FIRM_SCHEMA
from controlled_conditions import CHANGE_SCHEMA as CONTROLLED_SCHEMA
from inventory_decision_scenario import CHANGE_SCHEMA as INVENTORY_DECISION_SCHEMA
from multi_receipt_scenario import CHANGE_SCHEMA as OUTSTANDING_RECEIPT_SCHEMA
from routing_capacity_scenario import CHANGE_SCHEMA as RESOURCE_CAPACITY_SCHEMA
from distribution_scenario import CHANGE_SCHEMA as DISTRIBUTION_SCHEMA
from plan_editor import CHANGE_SCHEMA as EDITOR_SCHEMA

class DraftError(ValueError):pass

CHANGE_SCHEMAS={
 'propose_due_change':{'type':'object','properties':{'demand_id':{'type':'string'},'shift_days':{'type':['integer','null'],'minimum':-365,'maximum':365},'due_at':{'type':['string','null']}},'required':['demand_id','shift_days','due_at'],'additionalProperties':False},
 'propose_urgent_order':{'type':'object','properties':{'order_id':{'type':'string','pattern':'^[A-Za-z0-9_-]{1,64}$'},'item_id':{'type':'string'},'quantity':{'type':'string','pattern':'^[1-9][0-9]{0,6}$'},'due_at':{'type':'string'}},'required':['order_id','item_id','quantity','due_at'],'additionalProperties':False}
}
# Typed-only changes are not implicitly exposed to the legacy intent model.
SCENARIO_SCHEMAS={**CHANGE_SCHEMAS,
 'simulate_plan_editor':EDITOR_SCHEMA,
 'simulate_controlled_conditions':CONTROLLED_SCHEMA,
 'simulate_inventory_decision':INVENTORY_DECISION_SCHEMA,
 'simulate_outstanding_receipt':OUTSTANDING_RECEIPT_SCHEMA,
 'simulate_distribution_leadtime':DISTRIBUTION_SCHEMA,
 'simulate_resource_capacity':RESOURCE_CAPACITY_SCHEMA,
 'add_firm_demand':FIRM_SCHEMA,
 'enable_accessory_supply':ACCESSORY_SCHEMA,
 'set_forecast_gross_override':FORECAST_OVERRIDE_SCHEMA,
 FOUNDATION_FORECAST_ACTION:FOUNDATION_FORECAST_SCHEMA,
 PROFESSIONAL_POLICY_ACTION:PROFESSIONAL_POLICY_SCHEMA,
 'set_demand_priority':DEMAND_PRIORITY_SCHEMA,
 'set_resource_unavailable':{'type':'object','properties':{'resource_id':{'type':'string','minLength':1},'start_at':{'type':'string'},'end_at':{'type':'string'}},'required':['resource_id','start_at','end_at'],'additionalProperties':False},
 'simulate_approved_mo_move':{'type':'object','properties':{'reference':{'type':'string','minLength':1},'new_start_at':{'type':'string'}},'required':['reference','new_start_at'],'additionalProperties':False},
 'simulate_priority_source_terms':{'type':'object','properties':{'source_id':{'type':'string','minLength':1},'capacity_quantity':{'type':'string','pattern':'^[1-9][0-9]*(\\.[0-9]+)?$'},'minimum_order_quantity':{'type':'string','pattern':'^[1-9][0-9]*(\\.[0-9]+)?$'}},'required':['source_id'],'additionalProperties':False},
 'delay_confirmed_purchase':{'type':'object','properties':{'reference':{'type':'string','minLength':1},'new_receipt_at':{'type':'string'}},'required':['reference','new_receipt_at'],'additionalProperties':False}}

class DraftStore:
    def __init__(self,root,baseline_path,customers=None,*,allow_changes=True):
        self.customers=customers
        self.allow_changes=allow_changes
        self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True)
        self.baseline_path=Path(baseline_path)
        self.baseline_bytes=self.baseline_path.read_bytes()
        self.baseline=json.loads(self.baseline_bytes)
        validate_input(self.baseline)
        self.digest=hashlib.sha256(self.baseline_bytes).hexdigest()
        lock=self.root/'baseline-lock.json'
        if lock.exists() and json.loads(lock.read_text())['sha256']!=self.digest:raise DraftError('BASELINE_CHANGED')
        if not lock.exists():lock.write_text(json.dumps({'sha256':self.digest}),encoding='utf-8')
        self.path=self.root/'drafts.sqlite3'
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS drafts (id TEXT PRIMARY KEY, data TEXT NOT NULL)')

    def db(self):return sqlite3.connect(self.path,timeout=10)

    def verify_baseline(self):
        if hashlib.sha256(self.baseline_path.read_bytes()).hexdigest()!=self.digest:raise DraftError('BASELINE_CHANGED')

    def date(self,value):
        try:
            dt=datetime.fromisoformat(value.replace('Z','+00:00'))
            if dt.tzinfo is None:raise ValueError()
            dt=dt.astimezone(ZoneInfo(self.baseline['site']['timezone']))
            if not datetime.fromisoformat(self.baseline['planning_at'])<=dt<=datetime.fromisoformat(self.baseline['horizon']['end_at']):raise ValueError()
            return dt.isoformat()
        except (TypeError,ValueError):raise DraftError('INVALID_DATE: 需要含时间和时区且在规划范围内的交期')

    def propose(self,name,args,request_id=None):
        if not self.allow_changes:raise DraftError('CHECKPOINT_BASELINE_ONLY: 执行结转版本仅认证下一轮基线，不支持再次修改需求或创建候选')
        self.verify_baseline()
        from planning_capabilities import profile_id,supports
        feature={'propose_due_change':'due_change','propose_urgent_order':'urgent_order',
            'delay_confirmed_purchase':'purchase_delay','simulate_plan_editor':'plan_editor','simulate_approved_mo_move':'approved_mo_move',
            'simulate_priority_source_terms':'priority_source_terms','set_resource_unavailable':'resource_unavailability',
            'simulate_distribution_leadtime':'distribution_leadtime','simulate_resource_capacity':'resource_capacity','simulate_outstanding_receipt':'outstanding_receipt','simulate_inventory_decision':'inventory_decision','simulate_controlled_conditions':'controlled_conditions','set_demand_priority':'demand_priority','set_forecast_gross_override':'forecast_override','enable_accessory_supply':'accessory_supply','add_firm_demand':'firm_demand'}.get(name)
        if feature and not __import__('planning_capabilities').supports_input(self.baseline,'actions',feature):
            raise DraftError('UNSUPPORTED_PROFILE_CAPABILITY: 当前模型未认证此草案动作')
        if name=='simulate_plan_editor':
            from plan_editor import available
            if not available(self.baseline):raise DraftError('UNSUPPORTED_PROFILE_CAPABILITY: plan_editor')
        args=dict(args)
        foundation_forecast=name==FOUNDATION_FORECAST_ACTION
        professional_policy=name==PROFESSIONAL_POLICY_ACTION
        if professional_policy and not supports(profile_id(self.baseline),'actions','professional_policy'):raise DraftError('UNSUPPORTED_PROFILE_CAPABILITY: professional_policy')
        customer=args.pop('customer_id',None) if not (foundation_forecast or professional_policy) and name not in {'simulate_plan_editor','simulate_distribution_leadtime','simulate_resource_capacity','simulate_outstanding_receipt','simulate_inventory_decision','simulate_controlled_conditions','set_demand_priority','set_forecast_gross_override','enable_accessory_supply','add_firm_demand'} else None
        if name=='propose_urgent_order' and self.customers is not None and customer not in self.customers:raise DraftError('CUSTOMER_MAPPING_GAP: 急单需要明确指定已确认的客户编码')
        if name not in SCENARIO_SCHEMAS:raise DraftError('FORBIDDEN_ACTION')
        errors=list(Draft202012Validator(SCENARIO_SCHEMAS[name]).iter_errors(args))
        if errors:raise DraftError('INVALID_CHANGE: '+errors[0].message)
        if name in {'simulate_plan_editor','simulate_distribution_leadtime','simulate_resource_capacity','simulate_outstanding_receipt','simulate_inventory_decision','simulate_controlled_conditions'}:
            if args.get('confirmation') is None:raise DraftError(('DISTRIBUTION_CONFIRMATION_REQUIRED' if name=='simulate_distribution_leadtime' else 'OUTSTANDING_RECEIPT_CONFIRMATION_REQUIRED' if name=='simulate_outstanding_receipt' else 'INVENTORY_DECISION_CONFIRMATION_REQUIRED' if name=='simulate_inventory_decision' else 'CONTROLLED_CONFIRMATION_REQUIRED')+': 请先确认当前条件预览')
            if request_id is None:raise DraftError('INVALID_REQUEST_ID: 确认预览需要请求标识')
        # Resolve priority retries before the no-op rule: a reused request cannot
        # disguise conflicting arguments by asking for the original value.
        if (foundation_forecast or professional_policy) and request_id is None:raise DraftError('INVALID_REQUEST_ID')
        if (foundation_forecast or professional_policy or name in {'simulate_plan_editor','simulate_distribution_leadtime','simulate_resource_capacity','simulate_outstanding_receipt','simulate_inventory_decision','simulate_controlled_conditions','set_demand_priority','set_forecast_gross_override','enable_accessory_supply','add_firm_demand'}) and request_id is not None:
            if type(request_id) is not str or not re.fullmatch('[a-f0-9]{32}',request_id):raise DraftError('INVALID_REQUEST_ID')
            priority_request={'id':request_id,'sha256':hashlib.sha256(json.dumps({'name':name,'arguments':args},sort_keys=True).encode()).hexdigest()}
            priority_id='DRAFT-'+hashlib.sha256((self.digest+request_id).encode()).hexdigest()[:16]
            with self.db() as db:previous=db.execute('SELECT data FROM drafts WHERE id=?',(priority_id,)).fetchone()
            if previous and json.loads(previous[0]).get('external_request')!=priority_request:raise DraftError('IDEMPOTENCY_CONFLICT')
        data=copy.deepcopy(self.baseline)
        if professional_policy:
            from p22.professional_policy import confirmed_apply
            changes=confirmed_apply(data,**args,baseline_sha256=self.digest)
        elif foundation_forecast:
            from foundation_forecast import apply
            changes=apply(data,**args,baseline_sha256=self.digest)
        elif name=='propose_due_change':
            found=[r for r in data['demand_lines'] if r['id']==args['demand_id']]
            if len(found)!=1:raise DraftError('UNKNOWN_DEMAND')
            row=found[0]
            if (args['shift_days'] is None)==(args['due_at'] is None):raise DraftError('AMBIGUOUS_DATE_CHANGE')
            before=row['due_at']
            row['due_at']=self.date(args['due_at'] if args['due_at'] is not None else (datetime.fromisoformat(before)+timedelta(days=args['shift_days'])).isoformat())
            changes={'intent':'修改交期','demand_id':row['id'],'before_due_at':before,'after_due_at':row['due_at'],'calendar_days':args['shift_days']}
        elif name=='simulate_plan_editor':
            from plan_editor import apply,validate_confirmation
            changes=apply(data,**args);validate_confirmation(changes,self.digest)
        elif name=='simulate_distribution_leadtime':
            from distribution_scenario import apply,validate_confirmation
            changes=apply(data,**args)
            validate_confirmation(changes,self.digest)
        elif name=='simulate_resource_capacity':
            from routing_capacity_scenario import apply,validate_confirmation
            changes=apply(data,**args)
            validate_confirmation(changes,self.digest)
        elif name=='simulate_outstanding_receipt':
            from multi_receipt_scenario import apply,validate_confirmation
            changes=apply(data,**args)
            validate_confirmation(changes,self.digest)
        elif name=='simulate_inventory_decision':
            from inventory_decision_scenario import apply,validate_confirmation
            changes=apply(data,**args)
            validate_confirmation(changes,self.digest)
        elif name=='simulate_controlled_conditions':
            from controlled_conditions import apply,validate_confirmation
            changes=apply(data,**args)
            validate_confirmation(changes,self.digest)
        elif name=='set_demand_priority':
            from demand_priority import apply
            changes=apply(data,**args)
        elif name=='add_firm_demand':
            from firm_scenario import apply
            changes=apply(data,**args)
        elif name=='enable_accessory_supply':
            from accessory_scenario import apply
            changes=apply(data,**args)
        elif name=='set_forecast_gross_override':
            from forecast_override import apply
            changes=apply(data,**args)
        elif name=='set_resource_unavailable':
            from resource_unavailability import apply
            changes=apply(data,**args)
        elif name=='simulate_approved_mo_move':
            from approved_simulation import apply
            changes=apply(data,args['reference'],self.date(args['new_start_at']))
        elif name=='simulate_priority_source_terms':
            from priority_supply import simulate
            changes=simulate(data,**args)
        elif name=='delay_confirmed_purchase':
            from purchase_delay_scenario import apply
            changes=apply(data,args['reference'],args['new_receipt_at'],self.date)
        else:
            if any(r['order_ref']==args['order_id'] or r['id']==args['order_id']+'-L1' for r in data['demand_lines']):raise DraftError('DUPLICATE_ORDER')
            candidates=[r for r in data['production_recipes'] if r['output_item_id']==args['item_id']]
            if len(candidates)!=1:raise DraftError('UNKNOWN_ITEM_RECIPE')
            row=copy.deepcopy(data['demand_lines'][0])
            row.update(id=args['order_id']+'-L1',order_ref=args['order_id'],line_ref='L1',item_id=args['item_id'],quantity=args['quantity'],
                       minimum_shipment=args['quantity'],due_at=self.date(args['due_at']),priority=1,
                       uom=next(r['base_uom'] for r in data['items'] if r['id']==args['item_id']))
            row['provenance'].update(source_system='P1_USER_DRAFT',source_record_id=row['id'],source_locator='natural-language-draft')
            data['demand_lines'].append(row)
            changes={'intent':'新增急单','order_id':args['order_id'],'demand_id':row['id'],'item_id':row['item_id'],'quantity':row['quantity'],'uom':row['uom'],'due_at':row['due_at'],'priority':1,'priority_rule':'急单使用优先级 1；原订单优先级保持不变。'}
            if customer is not None:changes['customer_id']=customer
        request=None
        if request_id is not None:
            if (not (foundation_forecast or professional_policy) and name not in {'simulate_plan_editor','simulate_distribution_leadtime','simulate_resource_capacity','simulate_outstanding_receipt','simulate_inventory_decision','simulate_controlled_conditions','propose_due_change','delay_confirmed_purchase','simulate_approved_mo_move','simulate_priority_source_terms','set_resource_unavailable','set_demand_priority','set_forecast_gross_override','enable_accessory_supply','add_firm_demand'}) or not re.fullmatch('[a-f0-9]{32}',request_id):raise DraftError('INVALID_REQUEST_ID')
            request={'id':request_id,'sha256':hashlib.sha256(json.dumps({'name':name,'arguments':args},sort_keys=True).encode()).hexdigest()}
        draft_id='DRAFT-'+(hashlib.sha256((self.digest+request_id).encode()).hexdigest()[:16] if request else uuid.uuid4().hex[:16])
        data.update(scenario_id=draft_id,scenario_revision=1)
        try:validate_input(data)
        except InputError as exc:raise DraftError(str(exc)) from exc
        draft={'id':draft_id,'state':'DRAFT','run_id':None,'input':data,'changes':changes,'baseline_sha256':self.digest}
        if name=='simulate_plan_editor':draft['action_owner']=name
        if professional_policy:
            from p22.professional_policy import validate_search_lineage
            validate_search_lineage(self.baseline,draft,self.list())
        if foundation_forecast:draft.update(worker_eligible=False,worker_blocking_code='FORECAST_POLICY_PENDING')
        if request:draft['external_request']=request
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            previous=db.execute('SELECT data FROM drafts WHERE id=?',(draft_id,)).fetchone() if request else None
            if previous:
                draft=json.loads(previous[0])
                if draft.get('external_request')!=request:raise DraftError('IDEMPOTENCY_CONFLICT')
            (self.root/'draft-inputs').mkdir(exist_ok=True)
            path=self.root/'draft-inputs'/(draft_id+'.json')
            if path.exists():
                if json.loads(path.read_text(encoding='utf-8'))!=draft['input']:raise DraftError('DRAFT_INPUT_CHANGED')
            else:
                with path.open('x',encoding='utf-8') as output:json.dump(draft['input'],output,ensure_ascii=False,indent=2)
            if not previous:db.execute('INSERT INTO drafts VALUES (?,?)',(draft_id,json.dumps(draft,ensure_ascii=False)))
        if professional_policy or name in {'simulate_plan_editor','simulate_distribution_leadtime','simulate_resource_capacity','simulate_outstanding_receipt','simulate_inventory_decision','simulate_controlled_conditions','add_firm_demand'}:return self.for_run(draft_id)
        return draft

    def get(self,draft_id):
        with self.db() as db:row=db.execute('SELECT data FROM drafts WHERE id=?',(draft_id,)).fetchone()
        if not row:raise DraftError('DRAFT_NOT_FOUND')
        return json.loads(row[0])

    def list(self):
        with self.db() as db:return [json.loads(r[0]) for r in db.execute('SELECT data FROM drafts ORDER BY rowid DESC')]

    def metadata(self,draft_id=None,*,limit=20):
        """Fresh bounded lifecycle metadata; never deserialize a candidate's full input."""
        if limit is not None and (type(limit) is not int or not 1<=limit<=100):raise DraftError('QUERY_PAGE_BUDGET')
        columns="id,json_extract(data,'$.state'),json_extract(data,'$.run_id'),json_extract(data,'$.created_at'),json_extract(data,'$.changes.action'),json_extract(data,'$.changes.intent')"
        with self.db() as db:
            total=db.execute('SELECT count(*) FROM drafts').fetchone()[0]
            if draft_id is None:
                rows=db.execute('SELECT '+columns+' FROM drafts ORDER BY rowid DESC'+(' LIMIT ?' if limit is not None else ''),
                    (limit,) if limit is not None else ()).fetchall()
            else:rows=db.execute('SELECT '+columns+' FROM drafts WHERE id=?',(draft_id,)).fetchall()
        if draft_id is not None and not rows:raise DraftError('DRAFT_NOT_FOUND')
        return dict(total=total,truncated=draft_id is None and total>len(rows),rows=[dict(id=r[0],state=r[1],run_id=r[2],created_at=r[3],
            changes=dict(action=r[4],intent=r[5])) for r in rows])

    @staticmethod
    def directory_summary(root):
        """Read lifecycle counts at this Owner without loading candidate input.

        No DraftStore construction, baseline read, table creation, or current
        review certification takes place merely to populate the plan catalog.
        """
        path=Path(root)/'drafts.sqlite3'
        if not path.is_file():
            return dict(status='NOT_COLLECTED',count=None,has_drafts=None,pending_decision_count=None,
                needs_review=None,review_count=None,review_status='NOT_COLLECTED')
        with sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=1) as db:
            row=db.execute("SELECT sum(CASE WHEN json_extract(data,'$.state')!='DISCARDED' THEN 1 ELSE 0 END),"
                "sum(CASE WHEN json_extract(data,'$.state')='DRAFT' THEN 1 ELSE 0 END) FROM drafts").fetchone()
            table=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='review_notes'").fetchone()
            notes=db.execute('SELECT count(*) FROM review_notes').fetchone()[0] if table else 0
        count,pending=(row[0] or 0,row[1] or 0)
        return dict(status='PRESENT',count=count,has_drafts=count>0,pending_decision_count=pending,
            needs_review=pending>0 or notes>0,review_count=notes,
            review_status='HISTORY_NOT_REVALIDATED' if notes else 'NO_SAVED_NOTES')

    def run_targets(self,run_id):
        with self.db() as db:return [r[0] for r in db.execute("SELECT id FROM drafts WHERE json_extract(data,'$.run_id')=?",(run_id,))]

    def for_run(self,draft_id):
        if not self.allow_changes:raise DraftError('CHECKPOINT_BASELINE_ONLY: 执行结转版本不支持候选计算')
        self.verify_baseline();draft=self.get(draft_id)
        if draft['state']=='DISCARDED':raise DraftError('DRAFT_DISCARDED')
        from plan_editor import verify as verify_editor
        if verify_editor(self.baseline,draft):return draft
        from p22.professional_policy import verify as verify_professional
        if verify_professional(self.baseline,draft):
            from p22.professional_policy import validate_search_lineage
            validate_search_lineage(self.baseline,draft,self.list())
            return draft
        from foundation_forecast import verify_candidate
        if verify_candidate(self.baseline,draft):raise DraftError('FORECAST_POLICY_PENDING: Gross候选未计算，缺少已确认抵扣政策，不得提交净预测Worker')
        from distribution_scenario import verify as verify_distribution
        if verify_distribution(self.baseline,draft):return draft
        from routing_capacity_scenario import verify as verify_capacity
        if verify_capacity(self.baseline,draft):return draft
        from multi_receipt_scenario import verify as verify_outstanding
        if verify_outstanding(self.baseline,draft):return draft
        from inventory_decision_scenario import verify as verify_inventory
        if verify_inventory(self.baseline,draft):return draft
        from controlled_conditions import verify as verify_controlled
        if verify_controlled(self.baseline,draft):return draft
        from approved_simulation import verify_scope
        verify_scope(self.baseline,draft)
        from priority_supply import verify_draft
        verify_draft(self.baseline,draft)
        from resource_unavailability import verify
        verify(self.baseline,draft)
        from firm_scenario import verify
        verify(self.baseline,draft)
        from forecast_override import verify
        verify(self.baseline,draft)
        from demand_priority import verify
        verify(self.baseline,draft)
        from accessory_scenario import verify
        verify(self.baseline,draft)
        from purchase_delay_scenario import verify as verify_purchase_delay
        verify_purchase_delay(self.baseline,draft)
        return draft

    def attach_run(self,draft_id,run_id):
        draft=self.for_run(draft_id);draft['run_id']=run_id
        with self.db() as db:db.execute('UPDATE drafts SET data=? WHERE id=?',(json.dumps(draft,ensure_ascii=False),draft_id))
        return draft

    def decide(self,draft_id,decision):
        if decision not in {'retain','discard'}:raise DraftError('FORBIDDEN_ACTION')
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT data FROM drafts WHERE id=?',(draft_id,)).fetchone()
            if not row:raise DraftError('DRAFT_NOT_FOUND')
            draft=json.loads(row[0])
            if draft['state']=='DISCARDED':
                if decision=='retain':raise DraftError('DRAFT_DISCARDED')
                return draft
            draft['state']='RETAINED' if decision=='retain' else 'DISCARDED'
            db.execute('UPDATE drafts SET data=? WHERE id=?',(json.dumps(draft,ensure_ascii=False),draft_id))
        return draft
