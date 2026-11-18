"""One read-only ORM transaction for an explicitly bounded sales/MRP scope."""
import json
from odoo import api, fields, models
from odoo.exceptions import ValidationError
from .publication import _digest


class PlanningFacts(models.Model):
    _inherit = 'aps.interop.command'

    @api.model
    def capture_planning_facts(self, company_id, sale_ids):
        self._authorize()
        company = self._company(company_id)
        if (not isinstance(sale_ids, list) or not 0 < len(sale_ids) <= 20
                or any(type(x) is not int or x <= 0 for x in sale_ids)):
            raise ValidationError('APS_SOURCE_SCOPE_INVALID')
        sales = self.env['sale.order'].browse(sale_ids).exists()
        if len(sales) != len(set(sale_ids)) or any(s.company_id != company for s in sales):
            raise ValidationError('APS_SOURCE_COMPANY_MISMATCH')
        sales.check_access('read')
        productions = self.env['mrp.production'].search([
            ('company_id', '=', company.id), ('origin', 'in', sales.mapped('name'))])
        # Published suggestions retain original sales lineage through their origin.
        boms = productions.bom_id
        products = sales.order_line.product_id | boms.product_id | boms.product_tmpl_id.product_variant_ids | boms.bom_line_ids.product_id
        warehouses = sales.warehouse_id
        if len(warehouses) != 1 or len(products) > 50 or len(productions) > 100:
            raise ValidationError('APS_SOURCE_BOUNDED_SCOPE_REQUIRED')
        warehouse = warehouses.ensure_one()
        if any(m.picking_type_id.warehouse_id != warehouse or
               m.location_src_id != warehouse.pbm_loc_id or
               m.location_dest_id != warehouse.sam_loc_id for m in productions):
            raise ValidationError('APS_SOURCE_MANUFACTURING_WAREHOUSE_MISMATCH')
        operations = boms.operation_ids
        centers = operations.workcenter_id | operations.workcenter_id.alternative_workcenter_ids | productions.workorder_ids.workcenter_id
        calendars = centers.resource_calendar_id
        locations = self.env['stock.location'].search([('id','child_of',warehouses.view_location_id.ids)])
        moves = self.env['stock.move'].search([('company_id','=',company.id),('product_id','in',products.ids)])
        quants = self.env['stock.quant'].search([('company_id','=',company.id),('product_id','in',products.ids),('location_id','in',locations.ids)])
        leaves = self.env['resource.calendar.leaves'].search([('calendar_id','in',calendars.ids)])
        documents = {}
        revisions = {}
        def collect(records, names):
            records.check_access('read')
            records = records.sorted('id')
            documents[records._name] = records.read(['id'] + list(names))
            revisions[records._name] = {str(r.id):r.write_date.isoformat(timespec='microseconds') if r.write_date else False for r in records}
        collect(company, ['name'])
        collect(sales, ['name','company_id','partner_id','state','commitment_date','order_line','warehouse_id','procurement_group_id'])
        collect(sales.order_line, ['order_id','product_id','product_uom_qty','qty_delivered','qty_to_deliver','product_uom','display_type'])
        collect(products, ['name','company_id','default_code','uom_id','product_tmpl_id','is_storable','type'])
        collect(products.uom_id | boms.product_uom_id | boms.bom_line_ids.product_uom_id, ['name','category_id','factor','rounding'])
        collect(boms, ['company_id','product_id','product_tmpl_id','product_qty','product_uom_id','type','active','bom_line_ids','operation_ids','allow_operation_dependencies'])
        collect(boms.bom_line_ids, ['bom_id','product_id','product_qty','product_uom_id','operation_id'])
        collect(operations, ['bom_id','name','sequence','workcenter_id','time_mode','time_cycle_manual','time_cycle','blocked_by_operation_ids'])
        collect(centers, ['name','company_id','active','resource_id','resource_calendar_id','default_capacity','time_efficiency','time_start','time_stop','alternative_workcenter_ids','capacity_ids'])
        collect(centers.capacity_ids, ['workcenter_id','product_id','capacity'])
        collect(calendars, ['name','tz','two_weeks_calendar','attendance_ids'])
        collect(calendars.attendance_ids, ['calendar_id','dayofweek','hour_from','hour_to','date_from','date_to','week_type','day_period'])
        collect(leaves, ['calendar_id','resource_id','date_from','date_to','time_type'])
        collect(warehouses, ['company_id','name','view_location_id','lot_stock_id','manufacture_steps','pbm_loc_id','sam_loc_id'])
        collect(locations, ['company_id','name','usage','location_id'])
        collect(productions, ['company_id','name','origin','state','product_id','product_qty','qty_produced','qty_producing','product_uom_id','bom_id','date_start','date_finished','date_deadline','workorder_ids','move_raw_ids','move_finished_ids','procurement_group_id','backorder_sequence'])
        collect(productions.workorder_ids, ['production_id','operation_id','name','state','workcenter_id','qty_production','qty_produced','qty_remaining','date_start','date_finished','duration_expected','duration','blocked_by_workorder_ids','leave_id'])
        collect(moves, ['company_id','name','state','product_id','product_uom','product_uom_qty','quantity','picked','date','location_id','location_dest_id','picking_id','raw_material_production_id','production_id','move_line_ids','move_orig_ids','move_dest_ids'])
        collect(moves.move_line_ids, ['company_id','move_id','product_id','product_uom_id','quantity','picked','state','date','location_id','location_dest_id','lot_id'])
        collect(quants, ['company_id','product_id','location_id','quantity','reserved_quantity','lot_id','owner_id','package_id'])
        collect(moves.picking_id, ['company_id','name','state','picking_type_id','location_id','location_dest_id','date_done','move_ids','backorder_id','origin'])
        docs = json.loads(json.dumps(documents, default=lambda value:value.isoformat()))
        return dict(schema='odoo-planning-facts/1', company_id=company.id,
            enterprise_id=company.sudo().aps_interop_enterprise_id,
            database=self.env.cr.dbname, captured_at=fields.Datetime.now().isoformat()+'+00:00',
            scope={'sale_ids':sales.ids, 'production_ids':productions.ids, 'product_ids':products.ids},
            documents=docs, revisions=revisions, source_sha256=_digest(docs),
            transaction='SINGLE_ODOO_READ_ONLY_ORM_TRANSACTION',
            semantics={'quant':'CURRENT_PHYSICAL_SNAPSHOT_NOT_SECOND_MOVEMENT',
                       'reservation':'NOT_ACTUAL_CONSUMPTION', 'inspection':'WORK_OPERATION_NOT_QUALITY_RELEASE'})
