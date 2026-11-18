"""Read-only Odoo document evidence through the existing P2 source owner.

This projection intentionally creates no supply, operations, stock, or actuals.
Its single Location is an evidence reference context, not a physical warehouse.
"""
import copy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from xmlrpc.client import ServerProxy

from interop.odoo_adapter import OdooAdapter, command_hash


def _require(value, label):
    if not value:
        raise ValueError("ODOO_READBACK_" + label)


def _id(value):
    return value[0] if isinstance(value, (list, tuple)) and value else value


def _instant(value, *, odoo=False):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if odoo and parsed.tzinfo is None:
        # Odoo Datetime RPC strings are stored in UTC, not the user's local zone.
        parsed = parsed.replace(tzinfo=timezone.utc)
    _require(parsed.tzinfo is not None, "TIMESTAMP_ZONE_REQUIRED")
    return parsed.astimezone(timezone.utc)


def assess_professional_schedule(requested, snapshot):
    """Compare a fresh native snapshot; document acceptance never proves a schedule.

    Dates on target records are UTC. Native checks are independently obtained
    from Odoo calendars, reservations, durations and route dependencies. Missing
    checks remain unknown. This is an observation, not a planning/execution API.
    """
    production=snapshot['production'];workorders=snapshot['workorders']
    result=dict(schema='odoo-schedule-assessment/1',status='DOCUMENT_ACCEPTED',
        document_status='DOCUMENT_ACCEPTED',operation_status='DOCUMENT_ACCEPTED',schedule_status='DOCUMENT_ACCEPTED',
        declared_scope='MANUFACTURING_QUANTITY_ROUTE_WORKCENTER_CALENDAR_AND_TIME',differences=[],not_representable=[],operations=[],
        actual_execution='NOT_EXECUTED',finished_produced_quantity=str(production['qty_produced']),
        finished_received_quantity='0' if Decimal(str(production['qty_produced']))==0 else None)
    result['not_representable']=[dict(c,status='NOT_REPRESENTABLE') for c in requested.get('constraints',[])]
    if Decimal(str(production['qty_produced']))>0 or any(w['state']=='done' or Decimal(str(w['qty_produced']))>0 or w['state']=='progress' for w in workorders):
        result['actual_execution']='EXECUTED' if Decimal(str(production['qty_produced']))==Decimal(str(production['product_qty'])) else 'PARTIALLY_EXECUTED'
    def difference(field,expected,actual):
        result['differences'].append(dict(field=field,aps=expected,odoo=actual))
    for field,actual_field in [('product_id','product_id'),('bom_id','bom_id'),('uom_id','product_uom_id')]:
        if requested[field]!=_id(production[actual_field]):
            difference('production.'+field,requested[field],production[actual_field]);result['status']='RECONCILIATION_REQUIRED'
    if result['status']=='RECONCILIATION_REQUIRED':return result
    if Decimal(str(requested['quantity']))!=Decimal(str(production['product_qty'])):
        difference('production.quantity',requested['quantity'],production['product_qty'])
    for field,actual_field in [('start_at','date_start'),('end_at','date_finished')]:
        value=production.get(actual_field)
        if not value or _instant(requested[field])!=_instant(value,odoo=True):difference('production.'+field,requested[field],value)
    expected={op['operation_id']:op for op in requested.get('operations',[])}
    if len(expected)!=len(requested.get('operations',[])):
        result['status']='RECONCILIATION_REQUIRED';return result
    found={};ids=set()
    for work in workorders:
        operation=_id(work['operation_id'])
        if (_id(work['production_id'])!=production['id'] or work['id'] in ids or operation in found or operation not in expected):
            result['status']='RECONCILIATION_REQUIRED';return result
        found[operation]=work;ids.add(work['id'])
    if not workorders or set(found)!=set(expected):
        result['differences'].append(dict(field='operations',aps=list(expected),odoo=list(found)))
        return result
    result['operation_status']='OPERATION_ACCEPTED'
    planned=True
    for operation,req in expected.items():
        work=found[operation];label='operations.'+req['aps_operation_id']
        before=len(result['differences'])
        if req['workcenter_id']!=_id(work['workcenter_id']):difference(label+'.workcenter_id',req['workcenter_id'],work['workcenter_id'])
        if Decimal(str(req['quantity']))!=Decimal(str(work['qty_production'])):difference(label+'.quantity',req['quantity'],work['qty_production'])
        predecessors=[_id(found[p]['id']) for p in req.get('predecessor_operation_ids',[]) if p in found]
        if set(predecessors)!=set(work['blocked_by_workorder_ids']):difference(label+'.predecessors',predecessors,work['blocked_by_workorder_ids'])
        for field,target in [('start_at','date_start'),('end_at','date_finished')]:
            actual=work.get(target)
            if not actual:planned=False
            if not actual or _instant(req[field])!=_instant(actual,odoo=True):difference(label+'.'+field,req[field],actual)
        result['operations'].append(dict(aps_operation_id=req['aps_operation_id'],operation_id=operation,workorder_id=work['id'],
            requested=copy.deepcopy(req),actual=copy.deepcopy(work),matches=len(result['differences'])==before))
    checks=snapshot.get('schedule_checks',[])
    complete_checks=(len(checks)==len(workorders) and {c['workorder_id'] for c in checks}==ids and
        all(c.get(k) is True for c in checks for k in ['calendar_valid','capacity_valid','duration_valid','dependency_valid']))
    if not planned:result['status']='OPERATION_ACCEPTED'
    elif not complete_checks:result['status']='UNKNOWN';result['schedule_status']='UNKNOWN'
    else:
        result['schedule_status']='TARGET_ADJUSTED' if result['differences'] else 'SCHEDULE_ACCEPTED'
        result['status']=result['schedule_status']
        if result['status']=='SCHEDULE_ACCEPTED' and result['not_representable']:result['status']='NOT_REPRESENTABLE'
    return result


def build_source_snapshot(readback):
    """Validate one real handoff and preserve all raw source values unchanged."""
    try:
        _require(readback["schema"] == "odoo-actual-readback/1", "SCHEMA_INVALID")
        command, receipt, documents = (readback[k] for k in
            ("publication_command", "target_receipt", "documents"))
        company = readback["company_id"]
        _require(type(company) is int and company > 0, "COMPANY_INVALID")
        _require(command["test_company_id"] == company, "COMPANY_MISMATCH")
        digest = command_hash(command)
        _require(command["request_hash"] == digest == receipt["request_hash"], "COMMAND_HASH_MISMATCH")
        _require(receipt["state"] == "ACCEPTED" and receipt["command_id"] == command["command_id"],
                 "COMMAND_IDENTITY_MISMATCH")
        original, current = receipt["external_document"], receipt["current_external_document"]
        identifier = current["id"]
        _require(type(identifier) is int and identifier > 0, "DOCUMENT_ID_INVALID")
        for doc in (original, current):
            _require(doc["system"] == "odoo" and doc["company_id"] == company and doc["id"] == identifier,
                     "DOCUMENT_IDENTITY_MISMATCH")
        _require(receipt["external_document_id"] == f"odoo:{company}:mrp.production:{identifier}",
                 "EXTERNAL_ID_MISMATCH")
        _require(len(documents["mrp.production"]) == 1, "DOCUMENT_SCOPE_INVALID")
        production = documents["mrp.production"][0]
        _require(production["id"] == identifier and _id(production["company_id"]) == company,
                 "DOCUMENT_IDENTITY_MISMATCH")
        payload = command["payload"]
        for field, rpc_field in (("product_id", "product_id"), ("bom_id", "bom_id"), ("uom_id", "product_uom_id")):
            _require(current[field] == payload[field] == _id(production[rpc_field]), "MASTER_IDENTITY_MISMATCH")
        _require(Decimal(str(payload["quantity"])) == Decimal(str(current["quantity"])) ==
                 Decimal(str(production["product_qty"])), "QUANTITY_MISMATCH")
        _require(current["state"] == production["state"], "STATE_MISMATCH")
        for actual, rpc_field in (("start_at", "date_start"), ("end_at", "date_finished")):
            _require(_instant(current[actual], odoo=True) == _instant(production[rpc_field], odoo=True),
                     "CAPTURE_CHANGED")
        for table in ("product.product", "mrp.bom", "stock.location", "stock.warehouse"):
            for record in documents.get(table, []):
                _require(_id(record.get("company_id")) in (None, False, company), "FOREIGN_COMPANY")
        def master(table, key):
            matches = [row for row in documents[table] if row["id"] == payload[key]]
            _require(len(matches) == 1, "MASTER_NOT_READ")
            return matches[0]
        product, bom, unit = master("product.product", "product_id"), master("mrp.bom", "bom_id"), master("uom.uom", "uom_id")
        _require(_id(product["uom_id"]) == unit["id"] == _id(bom["product_uom_id"]), "UNIT_MISMATCH")
        _require(_id(bom["product_tmpl_id"]) == _id(product["product_tmpl_id"])
                 and _id(bom.get("product_id")) in (None, False, product["id"]), "BOM_PRODUCT_MISMATCH")
        for actual, rpc_field in (("source_location_id", "location_src_id"), ("destination_location_id", "location_dest_id")):
            _require(current[actual] == _id(production[rpc_field]), "LOCATION_MISMATCH")
            _require(any(row["id"] == current[actual] for row in documents["stock.location"]), "LOCATION_NOT_READ")
        if current.get("warehouse_id"):
            _require(any(row["id"] == current["warehouse_id"] for row in documents["stock.warehouse"]), "WAREHOUSE_NOT_READ")
        captured = _instant(readback["captured_at"])
        start_matches = _instant(payload["start_at"]) == _instant(current["start_at"], odoo=True)
        end_matches = _instant(payload["end_at"]) == _instant(current["end_at"], odoo=True)
        assessment = dict(schedule_window="MATCHES" if start_matches and end_matches else "TARGET_WINDOW_DIFFERS",
            start_matches=start_matches, end_matches=end_matches,
            requested_window={k: payload[k] for k in ("start_at", "end_at")},
            actual_window={k: current[k] for k in ("start_at", "end_at")},
            actual_datetime_basis="ODOO_DATETIME_UTC_RAW_STRINGS_PRESERVED",
            full_professional_mapping="NOT_CERTIFIED",
            limits=["Draft is neither confirmed supply nor production actuals",
                    "No SIM routing, resource, personnel or capacity is inferred from this ERP BOM",
                    "An accepted draft is not certification that the APS scheduling window was adopted"])
        linked = {key: command[key] for key in ("enterprise_id", "branch_id", "data_version_id", "plan_id", "run_id",
                                               "suggestion_id", "command_id", "request_hash", "action_type")}
        if readback.get('professional_snapshot'):
            professional=readback['professional_snapshot'];root=professional['production']
            _require(root['id']==identifier and root['company_id']==company, 'PROFESSIONAL_IDENTITY_MISMATCH')
            for key in ['state','product_id','bom_id','product_uom_id','product_qty','qty_produced']:
                _require(root[key]==_id(production[key]), 'PROFESSIONAL_CAPTURE_CHANGED')
            for key in ['date_start','date_finished']:
                _require(_instant(root[key],odoo=True)==_instant(production[key],odoo=True), 'PROFESSIONAL_CAPTURE_CHANGED')
            _require(set(w['id'] for w in professional['workorders'])==set(production['workorder_ids']), 'PROFESSIONAL_CAPTURE_CHANGED')
            _require(_instant(professional['version']['production'],odoo=True)==_instant(current['write_date'],odoo=True), 'PROFESSIONAL_CAPTURE_CHANGED')
            requested=command.get('requested_schedule')
            _require(isinstance(requested,dict), 'PROFESSIONAL_REQUEST_REQUIRED')
            assessment['professional_schedule']=assess_professional_schedule(requested,readback['professional_snapshot'])
        row = dict(id=f"odoo:{company}:document-evidence:{identifier}", timezone="UTC",
            reference_context_type="ERP_DOCUMENT_EVIDENCE_NOT_PHYSICAL_LOCATION",
            timezone_basis="Odoo Datetime UTC storage; physical location timezone not supplied",
            projection_role="INFO_ONLY", planning_eligible=False,
            planning_semantics="NO_SUPPLY_NO_ACTUALS_NO_CAPACITY",
            scope_basis="Readback capture reference interval, not enterprise planning horizon",
            linked_aps=linked, handoff_assessment=assessment, erp_readback=copy.deepcopy(readback))
        return dict(enterprise_id=f"ODOO-TEST:{readback['database']}:company:{company}",
            planning_at=captured.isoformat(), horizon=dict(start_at=captured.isoformat(),
            end_at=(captured + timedelta(days=1)).isoformat()), timezone="UTC", tables={"locations": [row]})
    except (KeyError, TypeError, ArithmeticError) as exc:
        raise ValueError("ODOO_READBACK_INCOMPLETE_OR_INVALID") from exc


def capture_readback(config, command):
    """Read exact IDs from a scoped receipt. No generic RPC write is exposed."""
    adapter = OdooAdapter(config["base_url"], config["database"], config["username"], config["password"],
                          config["company_id"], enabled=True)
    receipt = adapter.lookup(command["command_id"])
    _require(receipt and receipt["state"] == "ACCEPTED", "ACCEPTED_RECEIPT_REQUIRED")
    current = receipt["current_external_document"]
    professional_before=adapter.inspect_manufacturing(current['id']) if command.get('requested_schedule') else None
    calls = []
    with ServerProxy(adapter.base_url + "/xmlrpc/2/common", allow_none=True,
                     transport=adapter._transport_class(adapter.timeout)) as common:
        uid = common.authenticate(adapter.database, adapter.username, adapter._password, {})
        _require(type(uid) is int and uid > 0, "AUTHENTICATION_FAILED")
        version = common.version()
    with ServerProxy(adapter.base_url + "/xmlrpc/2/object", allow_none=True,
                     transport=adapter._transport_class(adapter.timeout)) as models:
        def read(model, ids, fields):
            ids = sorted(set(identifier for identifier in ids if identifier))
            if not ids:
                return []
            result = models.execute_kw(adapter.database, uid, adapter._password, model, "read", [ids, fields],
                {"context": {"allowed_company_ids": [adapter.company_id], "lang": "en_US"}})
            calls.append(dict(model=model, method="read", ids=ids, fields=fields, result=copy.deepcopy(result)))
            return result
        documents = {}
        documents["mrp.production"] = read("mrp.production", [current["id"]], ["id", "name", "state", "company_id",
            "product_id", "bom_id", "product_qty", "product_uom_id", "qty_produced", "date_start", "date_finished",
            "date_deadline", "location_src_id", "location_dest_id", "picking_type_id", "write_date", "workorder_ids"])
        documents["mrp.bom"] = read("mrp.bom", [current["bom_id"]], ["id", "code", "product_tmpl_id", "product_id",
            "product_qty", "product_uom_id", "company_id", "bom_line_ids", "operation_ids", "produce_delay", "type"])
        bom = documents["mrp.bom"][0]
        documents["mrp.bom.line"] = read("mrp.bom.line", bom["bom_line_ids"],
            ["id", "bom_id", "product_id", "product_qty", "product_uom_id", "operation_id"])
        products = [current["product_id"]] + [_id(row["product_id"]) for row in documents["mrp.bom.line"]]
        documents["product.product"] = read("product.product", products,
            ["id", "name", "default_code", "uom_id", "company_id", "type", "is_storable", "product_tmpl_id"])
        units = [current["uom_id"], _id(bom["product_uom_id"])]
        units += [_id(row["uom_id"]) for row in documents["product.product"]]
        units += [_id(row["product_uom_id"]) for row in documents["mrp.bom.line"]]
        documents["uom.uom"] = read("uom.uom", units, ["id", "name", "category_id", "rounding", "factor"])
        documents["stock.location"] = read("stock.location", [current["source_location_id"], current["destination_location_id"]],
            ["id", "name", "complete_name", "usage", "company_id"])
        documents["stock.warehouse"] = read("stock.warehouse", [current.get("warehouse_id")],
            ["id", "name", "code", "company_id", "lot_stock_id", "view_location_id"])
    after = adapter.lookup(command["command_id"])
    _require(after["current_external_document"] == current, "CAPTURE_CHANGED")
    professional=adapter.inspect_manufacturing(current['id']) if command.get('requested_schedule') else None
    # Root and child versions bracket the capture. Mixed revisions are refused.
    if professional:
        _require(professional_before['version']==professional['version'], 'CAPTURE_CHANGED')
        _require(professional['version']['production']==current['write_date'], 'CAPTURE_CHANGED')
    result=dict(schema="odoo-actual-readback/1", database=adapter.database, company_id=adapter.company_id,
        captured_at=datetime.now(timezone.utc).isoformat(), authenticated_uid=uid, target_version=version,
        publication_command=copy.deepcopy(command), target_receipt=receipt, documents=documents, rpc_reads=calls,
        effects="READ_ONLY_NO_ERP_WRITE_NO_WORKER")
    if professional:result['professional_snapshot']=professional
    return result
