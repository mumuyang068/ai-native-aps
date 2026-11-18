"""Causal annual continuation of the original FIFO factory, not an APS solver."""
import copy
import json
import random
from datetime import timedelta
from decimal import Decimal,ROUND_CEILING
from pathlib import Path
from .scenario import Simulation,dump
from .model import World,replay,dt,dec,text,SimulationError
from .year_policy import PREFIX_END


class AnnualQualifications:
    """Only explicitly declared Replay/annual research roles, fixed machines."""
    def __init__(self,declarations):
        self.declarations=copy.deepcopy(declarations)
        self.definition=self.declarations['definition']
        self.operating=self.declarations['operating']
        self.operations={r['id']:r for r in self.declarations['reference']['tables']['operations']}

    def validate_assignment(self,operation_id,resource_id,operator_id,start_at,end_at):
        spec=self.definition['operations'].get(operation_id)
        start,end=dt(start_at),dt(end_at)
        if not spec or resource_id not in spec.get('resource_ids',[spec['resource']]) or start>=end:
            raise SimulationError('UNQUALIFIED_ASSIGNMENT')
        skill=self.operations[operation_id]['required_operator_skill']
        valid=lambda row:dt(row['effective_from'])<=start and end<=dt(row['effective_to'])
        if not any(r['resource_id']==operator_id and r['skill_id']==skill and valid(r)
                   for r in self.operating['research_qualifications']):
            raise SimulationError('OPERATOR_QUALIFICATION_NOT_EFFECTIVE')
        if not any(r['resource_id']==resource_id and r['operation_id']==operation_id and valid(r)
                   for r in self.operating['research_machine_qualifications']):
            raise SimulationError('MACHINE_QUALIFICATION_NOT_EFFECTIVE')
        return True

    def __call__(self,definition,event,state):
        p=event['payload']
        if event['kind']=='OperationStarted':
            values=(p['operation_id'],p['resource_id'],p['operator_id'],event['occurred_at'],p['end'])
        else:
            op=state['operations'][p['job_id']+'::'+p['operation_id']]
            values=(p['operation_id'],op['resource_id'],op['operator_id'],op['start'],event['occurred_at'])
        return self.validate_assignment(*values)


class AnnualSimulation(Simulation):
    def __init__(self,declarations,seed=73,*,branch_id='YEAR-BASE',routine_production=True):
        self.declarations=copy.deepcopy(declarations)
        self.policy=self.declarations['operating']['year_policy']
        self.branch_id=branch_id;self.routine_production=routine_production
        super().__init__(self.declarations['definition'],seed)
        self.qualification=AnnualQualifications(self.declarations)
        self.world=World(self.definition,assignment_validator=self.qualification)
        self.scheduled=[];self.current_day=0

    def emit(self,kind,payload,at,tag,latencies=None):
        if self.current_day<56:
            return super().emit(kind,payload,at,tag,latencies)
        known={s:(at+timedelta(minutes=m)).isoformat() for s,m in
               (latencies or {'ERP':120,'WMS':5,'MES':10}).items()}
        event=dict(event_id=self.branch_id+'-'+tag,kind=kind,occurred_at=at.isoformat(),
            known_at=known,payload=copy.deepcopy(payload),research_id='APS-SIM-YEAR-01',
            branch_id=self.branch_id,policy_id=self.policy['id'],business_mode='BASELINE_ROUTINE_POLICY')
        self.world.apply(event)
        return event

    def _schedule(self,kind,payload,at,tag,latencies=None):
        self.scheduled.append(dict(kind=kind,payload=copy.deepcopy(payload),at=at.isoformat(),tag=tag,latencies=latencies))

    def _replenish(self,index,base):
        rule=self.policy['supply']
        materials=sorted({i for p in self.definition['products'].values() for i in p['bom']})
        for item in materials:
            daily=max(dec(p['bom'].get(item,0))*20 for p in self.definition['products'].values())
            available=sum((dec(l['available']) for l in self.world.state['lots'].values() if l['item_id']==item),Decimal(0))
            outstanding=sum((dec(p['outstanding']) for p in self.world.state['purchase_orders'].values() if p['item_id']==item),Decimal(0))
            if available+outstanding>=daily*rule['reorder_workdays']:continue
            required=daily*rule['target_workdays']-available-outstanding
            multiple=Decimal(rule['order_multiple'])
            qty=max(Decimal(rule['minimum_order_quantity']),(required/multiple).to_integral_value(rounding=ROUND_CEILING)*multiple)
            number=len([p for p in self.world.state['purchase_orders'] if p.startswith('YEAR-PO-')])+1
            po=f'YEAR-PO-{index:03d}-{item}';unit=self.definition['items'][item]['unit']
            due=(base+timedelta(days=rule['lead_calendar_days'])).replace(hour=7,minute=0,second=0)
            self.emit('PurchaseOrderCreated',dict(po_id=po,item_id=item,quantity=text(qty),unit=unit,
                eta=due.isoformat(),supplier_id='SUP-CONT-1'),base.replace(hour=7),'CREATE-'+po)
            partial=number%rule['partial_every_orders']==0
            pieces=[qty*Decimal(rule['partial_percent'])/100,qty*(100-Decimal(rule['partial_percent']))/100] if partial else [qty]
            if partial:
                self._schedule('PurchaseETAChanged',dict(po_id=po,eta=(due+timedelta(days=rule['remaining_delay_days'])).isoformat()),due,'DELAY-'+po)
            for part,q in enumerate(pieces):
                receipt_at=due+timedelta(days=part*rule['remaining_delay_days'])
                lot=f'LOT-{po}-{part}';receipt=f'RCV-{po}-{part}'
                latencies={'ERP':rule['late_erp_minutes'],'WMS':5,'MES':10} if number%rule['late_erp_every_orders']==0 else None
                self._schedule('GoodsReceived',dict(po_id=po,receipt_id=receipt,lot_id=lot,item_id=item,quantity=text(q),unit=unit),receipt_at,'GR-'+receipt,latencies)
                qc_at=receipt_at+timedelta(days=rule['qc_calendar_days'])
                held=q*Decimal(rule['quality_hold_percent'])/100 if number%rule['quality_hold_every_orders']==0 else Decimal(0)
                self._schedule('QCReleased',dict(lot_id=lot,quantity=text(q-held),unit=unit),qc_at,'QC-'+lot)
                if held:self._schedule('QCHeld',dict(lot_id=lot,quantity=text(held),unit=unit,from_status='pending'),qc_at,'HOLD-'+lot)

    def _day(self,index):
        self.current_day=index
        if index<56:return super()._day(index)
        base=dt(self.definition['origin'])+timedelta(days=index)
        at=base.replace(hour=7,minute=0,second=0)
        due=[r for r in self.scheduled if dt(r['at']).date()==base.date()]
        self.scheduled=[r for r in self.scheduled if dt(r['at']).date()!=base.date()]
        for r in sorted(due,key=lambda r:r['at']):
            self.emit(r['kind'],r['payload'],dt(r['at']),r['tag'],r['latencies'])
        rng=random.Random(self.seed*100000+index)
        if base.weekday() not in self.definition['workday']['weekdays']:return
        self._replenish(index,base)
        demand=self.policy['demand'];products=sorted(self.definition['products'])
        if rng.random()<demand['monthly_weekday_probability'][base.month-1]:
            oid=f'YEAR-SO-{index:03d}'
            self.emit('SalesOrderCreated',dict(order_id=oid,product_id=rng.choice(products),quantity='20',
                due=(base+timedelta(days=demand['due_calendar_days'])).replace(hour=17).isoformat(),
                customer_id='CUST-CONT-'+str(index%3+1),priority=1),at,'ORDER-'+oid)
        if (index-56)%demand['urgent_every_days']==0:
            oid=f'YEAR-URGENT-{index:03d}'
            self.emit('SalesOrderCreated',dict(order_id=oid,product_id=products[index%len(products)],quantity='20',
                due=(base+timedelta(days=demand['urgent_due_days'])).replace(hour=17).isoformat(),
                customer_id='CUST-CONT-1',priority=0),at,'ORDER-'+oid)
        if (index-56)%demand['revision_every_days']==0:
            eligible=[r for r in self.world.state['orders'].values() if dec(r['shipped'])<dec(r['quantity'])]
            if eligible:
                selected=eligible[-1]
                self.emit('SalesOrderChanged',dict(order_id=selected['order_id'],
                    due=(base+timedelta(days=4)).replace(hour=17).isoformat(),priority=0),at,'DUE-CHANGE-'+str(index))
        disturbances=self.policy['disturbances']
        for every,resource in [(disturbances['machine_every_days'],'SEW-01'),(disturbances['person_every_days'],'OP-01')]:
            if (index-56)%every==10:
                self.emit('ResourceUnavailable',dict(resource_id=resource,start=base.replace(hour=8).isoformat(),
                    # Retained first-pass archives used a misnamed key; its
                    # original implementation also meant calendar days.
                    end=(base+timedelta(days=disturbances.get('downtime_calendar_days',disturbances.get('downtime_workdays')))).replace(hour=17).isoformat(),
                    reason='年度声明的确定性合成不可用'),at,'DOWN-'+resource+'-'+str(index))
        if self.routine_production:self._execute_day(base)

    def _execute_day(self,base):
        if self.current_day<56 or self.routine_production:
            return super()._execute_day(base)

    def advance(self,to):
        if self.paused:raise ValueError('PAUSED')
        target=dt(to)
        if target<dt(self.clock):raise ValueError('CLOCK_CANNOT_REVERSE; use replay')
        if target>dt(self.definition['end']):raise ValueError('BEYOND_SCENARIO_END')
        origin=dt(self.definition['origin'])
        while self.next_day<365 and origin+timedelta(days=self.next_day)<=target:
            if self.pending:
                for e in self.pending:
                    if dt(e['occurred_at'])<=target:self.world.apply(e)
                self.pending=[e for e in self.pending if dt(e['occurred_at'])>target]
                if self.pending:break
                self.next_day+=1;continue
            actual=self.world
            self.world=actual.fork();before=len(self.world.events)
            try:
                self._day(self.next_day)
                generated=self.world.events[before:]
            finally:self.world=actual
            for e in generated:
                if dt(e['occurred_at'])<=target:self.world.apply(e)
            self.pending=[e for e in generated if dt(e['occurred_at'])>target]
            if self.pending:break
            self.next_day+=1
        self.clock=target.isoformat()
        return self.world.snapshot()

    def save(self,folder):
        super().save(folder)
        dump(Path(folder)/'annual.json',dict(declarations=self.declarations,scheduled=self.scheduled,
            current_day=self.current_day,branch_id=self.branch_id,routine_production=self.routine_production))

    @classmethod
    def load(cls,folder):
        folder=Path(folder);extra=json.loads((folder/'annual.json').read_text('utf8'));meta=json.loads((folder/'clock.json').read_text('utf8'))
        obj=cls(extra['declarations'],meta['seed'],branch_id=extra['branch_id'],routine_production=extra['routine_production'])
        obj.world=replay(obj.definition,[json.loads(line) for line in (folder/'events.jsonl').read_text('utf8').splitlines()],assignment_validator=obj.qualification)
        for key in ['clock','paused','next_day','pending']:setattr(obj,key,meta[key])
        obj.scheduled=extra['scheduled'];obj.current_day=extra['current_day']
        return obj
