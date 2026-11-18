"""Scoped operation scheduling through Odoo's native date inverse and calendars.

No production date compute, capacity rule or execution method is overridden.
Native rejection rolls back with the parent Publication Owner's savepoint.
"""
from decimal import Decimal
from odoo import api, fields, models
from odoo.exceptions import AccessError, ValidationError
from odoo.tools.float_utils import float_compare
from .publication import _digest, _timestamp


def _date(value):
    return value.isoformat(timespec='microseconds') if value else False


class ProfessionalSchedule(models.Model):
    _inherit = 'aps.interop.command'

    @api.model
    def _manufacturing(self, identifier):
        self._authorize()
        if type(identifier) is not int or identifier <= 0:
            raise ValidationError('APS_MANUFACTURING_ID_INVALID')
        production = self.env['mrp.production'].browse(identifier).exists()
        if not production or production.company_id.id not in self.env.companies.ids:
            raise AccessError('APS_MANUFACTURING_SCOPE_DENIED')
        self._company(production.company_id.id)
        production.check_access('read')
        return production

    @api.model
    def _schedule_masters(self, production):
        operations = production.bom_id.operation_ids
        centers = operations.workcenter_id | operations.workcenter_id.alternative_workcenter_ids | production.workorder_ids.workcenter_id
        calendars = centers.resource_calendar_id
        # Version the actual definitions, not just MO.write_date. Calendar leave
        # reservations are checked freshly under resource locks during apply.
        return {
            'bom': production.bom_id.read(['product_id', 'product_tmpl_id', 'product_qty', 'product_uom_id',
                'type', 'allow_operation_dependencies', 'operation_ids', 'bom_line_ids', 'write_date']),
            'bom_lines': production.bom_id.bom_line_ids.read(['product_id', 'product_qty', 'product_uom_id', 'operation_id', 'write_date']),
            'operations': operations.sorted('id').read(['bom_id', 'name', 'sequence', 'workcenter_id',
                'time_mode', 'time_cycle', 'time_cycle_manual', 'blocked_by_operation_ids', 'write_date']),
            'workcenters': centers.sorted('id').read(['name', 'company_id', 'active', 'resource_id', 'resource_calendar_id',
                'alternative_workcenter_ids', 'default_capacity', 'time_efficiency', 'time_start', 'time_stop', 'write_date']),
            'capacities': centers.capacity_ids.sorted('id').read(['product_id', 'capacity', 'write_date']),
            'calendars': calendars.sorted('id').read(['name', 'tz', 'two_weeks_calendar', 'attendance_ids', 'write_date']),
            'attendances': calendars.attendance_ids.sorted('id').read(['calendar_id', 'dayofweek', 'hour_from',
                'hour_to', 'date_from', 'date_to', 'week_type', 'day_period', 'write_date']),
        }

    @api.model
    def _native_checks(self, production):
        works = production.workorder_ids
        checks = []
        for work in works:
            start, finish = work.date_start, work.date_finished
            duration = work._get_duration_expected()
            valid = bool(start and finish and start < finish and work.leave_id)
            slot = work.workcenter_id._get_first_available_slot(start, duration,
                leaves_to_ignore=works.leave_id) if valid else (False, False)
            pairwise = valid and not any(other != work and other.workcenter_id == work.workcenter_id
                and other.date_start and other.date_finished and start < other.date_finished
                and other.date_start < finish for other in works)
            checks.append(dict(workorder_id=work.id,
                calendar_valid=bool(valid and slot[0] == start and slot[1] == finish),
                capacity_valid=bool(valid and pairwise and slot[0] == start and slot[1] == finish),
                duration_valid=bool(valid and not float_compare(work.duration_expected, duration, precision_digits=3)),
                dependency_valid=bool(valid and all(p.date_finished and p.date_finished <= start for p in work.blocked_by_workorder_ids))))
        return checks

    @api.model
    def inspect_manufacturing(self, production_id):
        """Fresh authenticated observation, separate from the immutable receipt."""
        production = self._manufacturing(production_id)
        production.invalidate_recordset()
        works = production.workorder_ids.sorted('id')
        works.invalidate_recordset()
        masters = self._schedule_masters(production)
        # ORM read returns native datetime objects internally, strings over RPC.
        # A deterministic JSON round trip preserves UTC values in both paths.
        import json
        masters = json.loads(json.dumps(masters, default=lambda value: value.isoformat()))
        snapshot = dict(schema='odoo-manufacturing-snapshot/1',
            production=dict(id=production.id, company_id=production.company_id.id, name=production.name,
                product_id=production.product_id.id, bom_id=production.bom_id.id, product_uom_id=production.product_uom_id.id,
                product_qty=production.product_qty, qty_produced=production.qty_produced, state=production.state,
                date_start=_date(production.date_start), date_finished=_date(production.date_finished),
                date_deadline=_date(production.date_deadline), frozen=production.aps_interop_frozen),
            workorders=[dict(id=w.id, production_id=production.id, operation_id=w.operation_id.id,
                workcenter_id=w.workcenter_id.id, name=w.name, sequence=w.sequence,
                qty_production=w.qty_production, qty_produced=w.qty_produced, qty_remaining=w.qty_remaining,
                state=w.state, date_start=_date(w.date_start), date_finished=_date(w.date_finished),
                duration_expected=w.duration_expected, duration=w.duration,
                blocked_by_workorder_ids=w.blocked_by_workorder_ids.ids, leave_id=w.leave_id.id,
                time_ids=w.time_ids.ids) for w in works],
            masters=masters, schedule_checks=self._native_checks(production),
            version=dict(production=_date(production.write_date), workorders={str(w.id): _date(w.write_date) for w in works},
                masters_sha256=_digest(masters)),
            material_moves=(production.move_raw_ids | production.move_finished_ids).read(
                ['id', 'product_id', 'product_uom', 'product_uom_qty', 'quantity', 'state', 'picked', 'location_id', 'location_dest_id']),
            capabilities=dict(named_person_time=False, sequence_dependent_setup=False,
                quality='INSPECTION_WORK_OPERATION_NOT_QUALITY_CERTIFICATE',
                stock_actuals='ONLY_NATIVE_DONE_STOCK_MOVES'))
        return json.loads(json.dumps(snapshot, default=lambda value: value.isoformat()))

    @api.model
    def _schedule(self, production, payload, command):
        parent = self.sudo().search([('company_id', '=', production.company_id.id),
            ('command_id', '=', payload.get('expected_publication_command_id'))], limit=1)
        if (not parent or parent.state != 'ACCEPTED' or parent.external_document_id != production
                or parent.request_json.get('action_type') != 'CREATE_MO'
                or any(parent.request_json.get(k) != command.get(k) for k in
                    ['enterprise_id', 'branch_id', 'data_version_id', 'plan_id', 'run_id', 'suggestion_id'])):
            raise ValueError('EXTERNAL_PUBLICATION_LINEAGE_MISMATCH:Schedule must target the exact published native root')
        if production.state != 'confirmed' or not production.workorder_ids:
            raise ValueError('EXTERNAL_CONFIRMED_ROUTE_REQUIRED:Confirm the real routed manufacturing order first')
        works = production.workorder_ids
        self.env.cr.execute('SELECT id FROM mrp_workorder WHERE id IN %s ORDER BY id FOR UPDATE', (tuple(works.ids),))
        works.invalidate_recordset()
        self._require_unstarted(production)
        snapshot = self.inspect_manufacturing(production.id)
        if payload.get('expected_workorders') != snapshot['version']['workorders']:
            raise ValueError('EXTERNAL_WORKORDER_VERSION_STALE:An operation changed since preview')
        if payload.get('expected_master_sha256') != snapshot['version']['masters_sha256']:
            raise ValueError('EXTERNAL_MASTER_VERSION_STALE:Route, calendar or workcenter definitions changed')
        requested = payload.get('operations')
        if not isinstance(requested, list) or not requested or not isinstance(payload.get('constraints'), list):
            raise ValueError('EXTERNAL_OPERATIONS_REQUIRED:Explicit operation mapping and unsupported constraints are required')
        by_operation = {w.operation_id.id: w for w in works}
        if len(by_operation) != len(works) or len(requested) != len(works):
            raise ValueError('EXTERNAL_OPERATION_IDENTITY_MISMATCH:Every existing operation must be mapped once')
        seen, mapped = set(), []
        for row in requested:
            if (not isinstance(row, dict) or set(row) != {'aps_operation_id', 'operation_id', 'workcenter_id',
                    'quantity', 'start_at', 'end_at', 'predecessor_operation_ids'}
                    or not isinstance(row.get('aps_operation_id'), str) or not row['aps_operation_id']):
                raise ValueError('EXTERNAL_OPERATION_PAYLOAD_INVALID:Only the explicit operation contract is writable')
            identifier = row['operation_id']
            if type(identifier) is not int or identifier not in by_operation or identifier in seen:
                raise ValueError('EXTERNAL_OPERATION_IDENTITY_MISMATCH:Foreign or duplicate operation identity')
            seen.add(identifier); work = by_operation[identifier]
            if Decimal(str(row['quantity'])) != Decimal(str(work.qty_production)):
                raise ValueError('EXTERNAL_OPERATION_QUANTITY_MISMATCH:Operation quantity cannot be changed by scheduling')
            legal = work.operation_id.workcenter_id | work.operation_id.workcenter_id.alternative_workcenter_ids
            if type(row['workcenter_id']) is not int or row['workcenter_id'] not in legal.ids:
                raise ValueError('EXTERNAL_WORKCENTER_NOT_ALLOWED:Workcenter is not an actual route alternative')
            center = legal.filtered(lambda r: r.id == row['workcenter_id'])
            if not center.active or center.company_id and center.company_id != production.company_id or not center.resource_calendar_id:
                raise ValueError('EXTERNAL_WORKCENTER_NOT_ALLOWED:An active same-company calendar-backed resource is required')
            if set(row['predecessor_operation_ids']) != set(work.blocked_by_workorder_ids.operation_id.ids):
                raise ValueError('EXTERNAL_DEPENDENCY_MISMATCH:Native route dependencies cannot be changed')
            start, finish = _timestamp(row['start_at']), _timestamp(row['end_at'])
            if start >= finish:
                raise ValueError('EXTERNAL_WINDOW_INVALID:Each operation needs a positive explicit window')
            mapped.append((work, center, start, finish))
        # Serialize this adapter's contenders on the actual native resource rows.
        centers = sorted({center.id for _, center, _, _ in mapped})
        self.env.cr.execute('SELECT id FROM mrp_workcenter WHERE id IN %s ORDER BY id FOR UPDATE', (tuple(centers),))
        production.button_unplan()
        # Standard write invokes Odoo's native duration/date inverse and creates
        # real calendar reservations. Requested end is not forced as target truth.
        for work, center, start, finish in sorted(mapped, key=lambda item: item[0].sequence):
            work.write({'workcenter_id': center.id, 'date_start': start, 'date_finished': finish})
        checks = self._native_checks(production)
        if not all(c['calendar_valid'] and c['capacity_valid'] and c['duration_valid'] and c['dependency_valid'] for c in checks):
            raise ValueError('EXTERNAL_SCHEDULE_CONFLICT:Native calendar, capacity, duration or dependency validation failed')
        return production
