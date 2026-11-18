"""Typed enterprise facts, independent of planning profiles and native results.

The immutable version remains owned by ImportStore. This module validates domain
semantics and records relationships; it never supplies missing business values.
"""
import copy
from decimal import Decimal
import hashlib
import json
from datetime import datetime
from zoneinfo import ZoneInfo,ZoneInfoNotFoundError
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError

Quantity = Annotated[Decimal, Field(ge=0, allow_inf_nan=False)]
SignedQuantity = Annotated[Decimal, Field(allow_inf_nan=False)]
Identifier = Annotated[str, Field(min_length=1, max_length=512)]


class Fact(BaseModel):
    model_config = ConfigDict(extra='allow')
    id: Identifier


class SnapshotScope(BaseModel):
    planning_at: AwareDatetime
    horizon: dict[str,AwareDatetime]
    timezone: str


class Item(Fact):
    kind: Literal['RAW', 'SEMI', 'FINISHED']
    base_uom: Identifier


class Location(Fact):
    timezone: str


class Order(Fact):
    customer_id: Identifier


class Demand(Order):
    item_id: Identifier
    location_id: Identifier
    quantity: Quantity
    uom: Identifier
    due_at: AwareDatetime
    order_ref: Identifier
    priority: int
    minimum_shipment: Quantity
    maximum_lateness_seconds: Quantity
    status: str


class Recipe(Fact):
    output_item_id: Identifier
    bom_id: Identifier
    routing_id: Identifier
    location_id: Identifier
    effective_from: AwareDatetime
    effective_to: AwareDatetime
    minimum_quantity: Quantity
    maximum_quantity: Quantity | None = None
    multiple: Quantity


class BomLine(Fact):
    recipe_id: Identifier
    input_item_id: Identifier
    output_item_id: Identifier
    input_uom: Identifier
    output_uom: Identifier
    quantity_per_output: Quantity
    scrap_fraction: Annotated[Decimal, Field(ge=0, lt=1)]
    consume_operation_id: Identifier
    consume_at: Literal['START', 'END']


class Routing(Fact):
    recipe_id: Identifier
    output_item_id: Identifier
    kind: str


class Operation(Fact):
    recipe_id: Identifier
    routing_id: Identifier
    sequence: int
    predecessor_id: str | None
    duration_model: Literal['FIXED_BATCH', 'PER_UNIT', 'FIXED_PLUS_PER_UNIT', 'AVAILABILITY_TRANSFER']
    fixed_seconds: Quantity | None
    seconds_per_unit: Quantity | None
    batch_quantity: Quantity | None = None
    resource_ids: list[Identifier]
    resources_are_alternatives: bool
    machine_and_person_simultaneously_required: bool
    required_operator_skill: Identifier | None = None
    produces_output: bool


class Interval(BaseModel):
    start_at: AwareDatetime
    end_at: AwareDatetime


class Calendar(Fact):
    timezone: str
    available_intervals: list[Interval]


class Resource(Fact):
    location_id: Identifier
    calendar_id: Identifier
    role: str
    capacity_model: str
    parallel_units: Quantity | None
    initial_setup: str | None


class Operator(Fact):
    location_id: Identifier
    calendar_id: Identifier
    capacity: Quantity
    efficiency_percent: Quantity


class Qualification(Fact):
    resource_id: Identifier
    skill_id: Identifier
    effective_from: AwareDatetime
    effective_to: AwareDatetime


class CapacityWindow(Fact):
    resource_id: Identifier
    start_at: AwareDatetime
    end_at: AwareDatetime
    capacity_quantity: Quantity
    uom: Identifier


class SetupTransition(Fact):
    from_state: Identifier
    to_state: Identifier
    duration_seconds: Quantity
    occupies_machine: bool


class Inventory(Fact):
    item_id: Identifier
    location_id: Identifier
    physical: Quantity | None
    quality_blocked: Quantity | None
    other_unavailable: Quantity | None
    net_available: Quantity | None
    uom: Identifier
    as_of: AwareDatetime
    knowledge: str


class SupplySource(Fact):
    item_id: Identifier
    location_id: Identifier
    supplier_id: Identifier
    uom: Identifier
    minimum_quantity: Quantity
    order_multiple: Quantity
    lead_time_seconds: Quantity
    qc_wait_seconds: Quantity
    horizon_capacity: Quantity
    capacity_includes_confirmed: bool


class PurchaseOrder(Fact):
    reference: Identifier
    item_id: Identifier
    location_id: Identifier
    source_id: Identifier
    quantity: Quantity
    received_quantity: Quantity
    cancelled_quantity: Quantity
    unreceived_quantity: Quantity
    uom: Identifier
    physical_receipt_at: AwareDatetime
    available_at: AwareDatetime
    status: str
    included_in_opening: bool


class ManufacturingOrder(Fact):
    recipe_id: Identifier
    operation_id: Identifier
    resource_id: Identifier
    quantity: Quantity
    completed_quantity: Quantity
    start_at: AwareDatetime
    end_at: AwareDatetime
    status: str
    protected: bool


class PurchaseReceipt(Fact):
    po_id: Identifier
    receipt_reference: Identifier
    item_id: Identifier
    location_id: Identifier
    quantity: Quantity
    uom: Identifier
    physical_receipt_at: AwareDatetime
    quality_status: Literal['RELEASED', 'PENDING_QC', 'FROZEN']
    release_at: AwareDatetime | None
    inventory_includes_receipt: Literal[False]


class DistributionLane(Fact):
    item_id: Identifier
    origin_location_id: Identifier
    destination_location_id: Identifier
    uom: Identifier
    lead_time_seconds: Annotated[Decimal, Field(gt=0)]
    minimum_transfer_quantity: Annotated[Decimal, Field(gt=0)]
    transfer_multiple: Annotated[Decimal, Field(gt=0)]
    effective_from: AwareDatetime
    effective_to: AwareDatetime
    status: Literal['CONFIRMED_PATH']
    destination_receiving_qc: dict


class HistoricalManufacturingOrder(Fact):
    operation_id: Identifier
    output_item_id: Identifier
    raw_item_id: Identifier
    quantity: Quantity
    raw_consumed: Quantity
    included_in_opening: bool
    reload_as_future_supply: bool


class HistoryEvent(Fact):
    item_id: Identifier
    location_id: Identifier
    event_at: AwareDatetime
    event_type: str
    sequence: int
    quantity: SignedQuantity
    included_in_opening: bool


class Checkpoint(Fact):
    as_of: AwareDatetime
    closed_through_event: Identifier
    future_events_included: bool


class Forecast(Fact):
    customer_id: Identifier
    item_id: Identifier
    location_id: Identifier
    bucket_start: AwareDatetime
    bucket_end: AwareDatetime
    gross_quantity: Quantity
    net_quantity: Quantity | None
    netting_policy: str
    uom: Identifier


class SalesHistory(Fact):
    customer_id: Identifier
    item_id: Identifier
    location_id: Identifier
    month: str
    quantity: Quantity | None
    uom: Identifier
    is_observed: bool


class InventoryTarget(Fact):
    item_id: Identifier
    location_id: Identifier
    minimum: Quantity
    maximum: Quantity
    effective_from: AwareDatetime
    effective_to: AwareDatetime
    uom: Identifier
    hard_limit: bool


DOMAIN = {
    'items':Item, 'locations':Location, 'customers':Fact, 'sales_orders':Order,
    'demand_lines':Demand, 'recipes':Recipe, 'bom_lines':BomLine, 'routings':Routing,
    'operations':Operation, 'calendars':Calendar, 'resources':Resource, 'operators':Operator,
    'skills':Fact, 'resource_skills':Qualification, 'machine_qualifications':Qualification,
    'capacity_windows':CapacityWindow, 'setup_transitions':SetupTransition,
    'inventory_positions':Inventory, 'suppliers':Fact, 'supply_sources':SupplySource,
    'purchase_orders':PurchaseOrder, 'existing_manufacturing_orders':ManufacturingOrder,
    'historical_manufacturing_orders':HistoricalManufacturingOrder, 'history_events':HistoryEvent,
    'checkpoints':Checkpoint, 'forecasts':Forecast, 'sales_history':SalesHistory,
    'inventory_targets':InventoryTarget,
    'purchase_receipts':PurchaseReceipt, 'distribution_lanes':DistributionLane,
}

RELATIONS = {
    'sales_orders':{'customer_id':'customers'},
    'demand_lines':{'customer_id':'customers','order_ref':'sales_orders','item_id':'items','location_id':'locations'},
    'recipes':{'output_item_id':'items','location_id':'locations','routing_id':'routings'},
    'bom_lines':{'recipe_id':'recipes','input_item_id':'items','output_item_id':'items','consume_operation_id':'operations'},
    'routings':{'recipe_id':'recipes','output_item_id':'items'},
    'operations':{'recipe_id':'recipes','routing_id':'routings','predecessor_id':'operations','resource_ids':'resources','required_operator_skill':'skills'},
    'resources':{'location_id':'locations','calendar_id':'calendars'},
    'operators':{'location_id':'locations','calendar_id':'calendars'},
    'resource_skills':{'resource_id':'operators','skill_id':'skills'},
    'machine_qualifications':{'resource_id':'resources','skill_id':'skills'},
    'capacity_windows':{'resource_id':'resources'},
    'inventory_positions':{'item_id':'items','location_id':'locations'},
    'supply_sources':{'item_id':'items','location_id':'locations','supplier_id':'suppliers'},
    'purchase_orders':{'item_id':'items','location_id':'locations','source_id':'supply_sources'},
    'purchase_receipts':{'po_id':'purchase_orders','item_id':'items','location_id':'locations'},
    'distribution_lanes':{'item_id':'items','origin_location_id':'locations','destination_location_id':'locations'},
    'existing_manufacturing_orders':{'recipe_id':'recipes','operation_id':'operations','resource_id':'resources'},
    'historical_manufacturing_orders':{'operation_id':'operations','output_item_id':'items','raw_item_id':'items'},
    'history_events':{'item_id':'items','location_id':'locations'},
    'checkpoints':{'closed_through_event':'history_events'},
    'forecasts':{'customer_id':'customers','item_id':'items','location_id':'locations'},
    'sales_history':{'customer_id':'customers','item_id':'items','location_id':'locations'},
    'inventory_targets':{'item_id':'items','location_id':'locations'},
}


def digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def identity(enterprise, kind, key):
    return 'OBJ-'+digest([enterprise,kind,key])[:32]


def domain_schema():
    return {'schema_revision':'canonical-business-v1', 'entities':{name:model.model_json_schema() for name,model in DOMAIN.items()}}


def apply_source_state(canonical, state):
    """Retain unresolved source authority across synchronization and corrections.

    Rebuilding typed facts cannot resolve a conflict or complete an interrupted
    source read. Invalid typed facts remain blocked regardless of these flags.
    """
    conflicts=copy.deepcopy(state.get('conflicts',[]))
    retractions=copy.deepcopy(state.get('retractions',[]))
    canonical.update(source_conflicts=conflicts,source_retractions=retractions)
    if conflicts or retractions:
        canonical['semantic_sha256']=digest({'facts':canonical['semantic_sha256'],'conflicts':conflicts,'retractions':retractions})
        if not canonical['issues']:canonical['data_status']='DATA_ACCEPTED_WITH_UNRESOLVED_FACTS'
        canonical['planning_readiness']['blocking_issues']+=conflicts+retractions
    if state.get('complete') is False:
        canonical['issues'].append({'code':'SOURCE_SYNC_INCOMPLETE','severity':'ERROR','source_errors':copy.deepcopy(state.get('source_errors',[]))})
        canonical['data_status']='DATA_BLOCKED'
    return canonical


def assemble(enterprise_id, tables, *, planning_at, horizon, timezone, source_refs):
    if not isinstance(enterprise_id,str) or not enterprise_id.strip():
        raise ValueError('ENTERPRISE_REQUIRED')
    tables=copy.deepcopy(tables)
    objects=[];relations=[];issues=[];receipts=[];index={};pending=[]
    try:
        scope=SnapshotScope.model_validate(dict(planning_at=planning_at,horizon=horizon,timezone=timezone))
        ZoneInfo(scope.timezone)
        if not scope.horizon['start_at']==scope.planning_at<scope.horizon['end_at']:raise ValueError('INVALID_RANGE')
    except (ValidationError,ValueError,KeyError,ZoneInfoNotFoundError):
        issues.append({'code':'INVALID_SNAPSHOT_SCOPE','severity':'ERROR','field':'planning_at/horizon/timezone'})
    for kind, rows in tables.items():
        if kind not in DOMAIN:
            issues.append({'code':'UNKNOWN_ENTITY_KIND','table':kind,'severity':'ERROR'})
            continue
        if not isinstance(rows,list):
            raise ValueError('INVALID_ENTITY_TABLE: '+kind)
        for n,row in enumerate(rows,1):
            try:
                DOMAIN[kind].model_validate(row)
            except ValidationError as exc:
                issues.extend({'code':'INVALID_TYPED_FACT','table':kind,'row':n,'field':'.'.join(map(str,e['loc'])),
                    'reason':e['msg'],'severity':'ERROR'} for e in exc.errors())
            key=row.get('id')
            cid=identity(enterprise_id,kind,key)
            if (kind,key) in index:
                issues.append({'code':'DUPLICATE_BUSINESS_IDENTITY','table':kind,'id':key,'row':n,'severity':'ERROR'})
            index[kind,key]=cid
            unknown={k:'UNKNOWN_VALUE' for k,v in row.items() if v is None}
            role='HISTORY_ONLY' if kind in {'history_events','historical_manufacturing_orders','checkpoints'} else 'REFERENCE_ONLY' if kind=='sales_history' else 'POLICY_PENDING' if kind=='forecasts' and row.get('netting_policy')=='NOT_SPECIFIED' else 'FACT_IN_CANONICAL'
            table_refs=source_refs.get(kind,source_refs)
            refs=copy.deepcopy(table_refs[n-1] if isinstance(table_refs,list) and n<=len(table_refs) else table_refs)
            obj={'canonical_id':cid,'enterprise_id':enterprise_id,'object_type':kind,'business_id':key,
                 'payload':copy.deepcopy(row),'field_statuses':unknown,'source_refs':refs,
                 'factual_status':'SOURCE_FACT_UNCONFIRMED','source_row':n}
            objects.append(obj)
            receipts.append({'table':kind,'source_row':n,'source_id':key,'canonical_id':cid,'role':role})
            if kind=='inventory_positions' and row.get('physical') is None:
                pending.append({'code':'UNKNOWN_INVENTORY','table':kind,'id':key,'field':'physical'})
            if kind=='resources' and row.get('initial_setup') is None:
                pending.append({'code':'INITIAL_SETUP_PENDING','table':kind,'id':key,'field':'initial_setup'})
            if kind=='forecasts' and row.get('netting_policy')=='NOT_SPECIFIED':
                pending.append({'code':'FORECAST_POLICY_PENDING','table':kind,'id':key,'field':'netting_policy'})
            for start,end in [('start_at','end_at'),('effective_from','effective_to'),('bucket_start','bucket_end')]:
                if row.get(start) and row.get(end):
                    try:
                        if datetime.fromisoformat(str(row[start]).replace('Z','+00:00'))>=datetime.fromisoformat(str(row[end]).replace('Z','+00:00')):
                            issues.append({'code':'INVALID_TIME_RANGE','table':kind,'id':key,'field':start,'severity':'ERROR'})
                    except (ValueError,TypeError):pass # Typed fact validation retains the precise field error.
            if kind=='inventory_positions' and all(row.get(k) is not None for k in ['physical','quality_blocked','other_unavailable','net_available']):
                try:
                    expected=Decimal(str(row['physical']))-Decimal(str(row['quality_blocked']))-Decimal(str(row['other_unavailable']))
                    if expected<0 or expected!=Decimal(str(row['net_available'])):
                        issues.append({'code':'INVENTORY_BALANCE_CONFLICT','table':kind,'id':key,'severity':'ERROR'})
                except (ValueError,ArithmeticError):pass
    for obj in objects:
        for field,target_kind in RELATIONS.get(obj['object_type'],{}).items():
            value=obj['payload'].get(field)
            if value is None:continue
            for key in value if isinstance(value,list) else [value]:
                target=index.get((target_kind,key))
                if target is None:
                    issues.append({'code':'DANGLING_REFERENCE','table':obj['object_type'],'id':obj['business_id'],
                                   'field':field,'target_table':target_kind,'target_id':key,'severity':'ERROR'})
                else:
                    relations.append({'source_id':obj['canonical_id'],'field':field,'target_id':target})
    semantic={'enterprise_id':enterprise_id,'tables':{k:sorted(v,key=lambda r:str(r.get('id'))) for k,v in sorted(tables.items())},
              'planning_at':planning_at,'horizon':horizon,'timezone':timezone}
    return {'schema_revision':'canonical-business-v1','enterprise_id':enterprise_id,'planning_at':planning_at,
            'horizon':copy.deepcopy(horizon),'timezone':timezone,'tables':tables,'objects':objects,'relations':relations,
            'row_receipts':receipts,'issues':issues,'unresolved_policies':pending,
            'semantic_sha256':digest(semantic),'source_refs':copy.deepcopy(source_refs),
            'data_status':'DATA_BLOCKED' if issues else 'DATA_ACCEPTED_WITH_UNRESOLVED_FACTS' if pending else 'DATA_ACCEPTED',
            'planning_readiness':{'ready':False,'status':'NOT_PROJECTED','blocking_issues':pending}}
