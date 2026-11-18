"""Independent, read-only business audit of one combined enterprise/native run.

No solver imports, legacy profile PASS reuse, file access, repairs, or inferred
assignments. Missing capture is NOT_CHECKED; an observed violation always wins.
The source tables, not a normalized PlanResult, are the business authority.
"""
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from decimal import Decimal
import hashlib
import json
import xml.etree.ElementTree as ET
from zoneinfo import ZoneInfo

EPS = Decimal('0.000001')


def is_priority(value):
    # Pinned 9.19 model.h SearchMode enum: PRIORITY=0. bool is not an enum.
    return value == 'PRIORITY' or (type(value) is int and value == 0)


def quantity(value):
    value = Decimal(str(value))
    if not value.is_finite():
        raise ValueError('nonfinite quantity')
    return value


def numeric(value):
    return format(quantity(value).normalize(), 'f')


def duration_seconds(value):
    text = str(value).strip().replace(' days, ', ' ').replace(' day, ', ' ')
    days = Decimal(0)
    if ' ' in text:
        day, text = text.split(' ',1);days = quantity(day)
    if ':' not in text:
        return quantity(text)
    hours,minutes,seconds = (quantity(v) for v in text.split(':'))
    return days*86400 + hours*3600 + minutes*60 + seconds


def table(value):
    if isinstance(value, dict):
        value = value.get('rows', value.get('results'))
    return value if isinstance(value, list) and all(isinstance(r, dict) for r in value) else None


def resource_names(value):
    """Native captures use names; the actual MO API uses resource objects."""
    if not isinstance(value, list):
        raise ValueError('resource assignments must be a list')
    result=[]
    for item in value:
        name=item.get('resource') if isinstance(item,dict) else item[0] if isinstance(item,(list,tuple)) and item else item
        if not isinstance(name,str) or not name:
            raise ValueError('invalid resource assignment object')
        result.append(name)
    return result


def status(issues, missing):
    return 'FAIL' if issues else 'NOT_CHECKED' if missing else 'PASS'


def working_seconds(start, end, intervals):
    """Union source intervals, so overlapping calendar rows never double count."""
    spans = sorted((max(start,a),min(end,b)) for a,b in intervals if max(start,a) < min(end,b))
    merged = []
    for a,b in spans:
        if merged and a <= merged[-1][1]:
            merged[-1] = (merged[-1][0],max(b,merged[-1][1]))
        else:
            merged.append((a,b))
    return sum(((b-a).total_seconds() for a,b in merged),0.0)


class _Audit:
    def __init__(self, data, raw, run_id):
        self.data, self.raw, self.run_id = data, raw, run_id
        self.meta = data.get('foundation_projection', {})
        self.source = self.meta.get('source_tables', {})
        self.zone = ZoneInfo(data.get('site', {}).get('timezone', 'Asia/Shanghai'))
        self.checks = {}

    def at(self, value):
        value = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return value.replace(tzinfo=self.zone) if value.tzinfo is None else value.astimezone(self.zone)

    def check(self, ok, code, ref=None, **detail):
        if not ok:
            self.current['issues'].append(dict(code=code, reference=ref, **detail))

    def missing(self, value):
        self.current['missing_evidence'].append(value)

    def rows(self, key):
        rows = table(self.raw.get(key))
        if rows is None:
            self.missing('raw.' + key)
        return rows

    def facts(self, key):
        rows = table(self.source.get(key))
        if rows is None:
            self.missing('foundation_projection.source_tables.' + key)
        return rows or []

    def capture(self, phase, field=None):
        value = self.raw.get('fixed_batch_' + phase)
        if not isinstance(value, dict):
            self.missing('raw.fixed_batch_' + phase + ('.' + field if field else ''))
            return None
        if field is not None:
            value = value.get(field)
            if value is None:
                self.missing('raw.fixed_batch_' + phase + '.' + field)
        return value

    def run(self, name, operation):
        self.current = dict(issues=[], missing_evidence=[])
        try:
            operation()
        except KeyError as exc:
            self.missing(name + '.required_field:' + str(exc))
        except (TypeError, ValueError, ArithmeticError) as exc:
            self.check(False, 'MALFORMED_EVIDENCE', name, reason=str(exc))
        self.current['missing_evidence'] = sorted(set(self.current['missing_evidence']))
        self.current['status'] = status(self.current['issues'], self.current['missing_evidence'])
        self.checks[name] = self.current

    def operation_rows(self):
        result = {}
        for key in ('manufacturingorder', 'workorder'):
            rows = self.rows(key)
            for row in rows or []:
                ref = str(row['reference'])
                if ref in result:
                    for k in ('operation', 'quantity', 'status', 'startdate', 'enddate', 'owner'):
                        if k in row and k in result[ref]:
                            self.check(str(row[k]) == str(result[ref][k]), 'NATIVE_ORDER_CONFLICT', ref, field=k)
                else:
                    result[ref] = row
        return result

    def complete_orders(self):
        return all(table(self.raw.get(k)) is not None for k in ('manufacturingorder','workorder'))

    def leaf_rows(self):
        mapping = self.meta.get('native_operation_map')
        if not isinstance(mapping, dict):
            self.missing('foundation_projection.native_operation_map')
            return []
        return [r for r in self.operation_rows().values() if r.get('operation') in mapping]

    def duration(self, row):
        return self.at(row['enddate']) - self.at(row['startdate'])

    def effective_calendar_matches(self,native,source_calendar,native_calendars,limit,ref):
        """Compare the complete availability AND maximum-calendar function.

        No calendar-name exception. Overlapping conflicting buckets require
        captured priorities; unsupported weekday/time masks remain unverified.
        """
        calendars={c['name']:c for c in native_calendars}
        values=[]
        for field in ('available','maximum_calendar'):
            name=native.get(field)
            if name is None:
                values.append(None);continue
            c=calendars.get(name)
            if c is None:
                self.missing(ref+'.'+field+'.'+name);return None
            if table(c.get('buckets')) is None:
                self.missing(ref+'.'+field+'.buckets');return None
            for bucket in c['buckets']:
                if bucket.get('days',127) != 127 or quantity(bucket.get('starttime',0)) != 0 or quantity(bucket.get('endtime',86400)) < 86399:
                    self.missing(ref+'.'+field+'.weekday_or_time_mask');return None
            values.append(c)
        start,end=self.at(self.data['horizon']['start_at']),self.at(self.data['horizon']['end_at'])
        expected=[(self.at(i['start_at']),self.at(i['end_at'])) for i in source_calendar['available_intervals']]
        edges={start,end}
        for a,b in expected:
            edges.update([max(start,min(end,a)),max(start,min(end,b))])
        for c in values:
            for bucket in c['buckets'] if c else []:
                edges.update([max(start,min(end,self.at(bucket['start']))),max(start,min(end,self.at(bucket['end'])))])
        def value(c,when,default):
            if c is None:return default
            active=[b for b in c['buckets'] if self.at(b['start']) <= when < self.at(b['end'])]
            if not active:return quantity(c['default'])
            distinct={quantity(b['value']) for b in active}
            if len(distinct) == 1:return next(iter(distinct))
            if not all(b.get('priority') is not None for b in active):return None
            priority=min(b['priority'] for b in active)
            distinct={quantity(b['value']) for b in active if b['priority'] == priority}
            return next(iter(distinct)) if len(distinct) == 1 else None
        ordered=sorted(edges)
        matches=True
        for a,b in zip(ordered,ordered[1:]):
            when=a+(b-a)/2
            available=value(values[0],when,Decimal(1));maximum=value(values[1],when,quantity(native['maximum']))
            if available is None or maximum is None:
                self.missing(ref+'.overlapping_bucket_priorities');return None
            self.check(available >= 0 and maximum >= 0,'NATIVE_NEGATIVE_CALENDAR_CAPACITY',ref)
            actual=maximum if available > 0 else Decimal(0)
            wanted=limit if any(x <= when < y for x,y in expected) else Decimal(0)
            if abs(actual-wanted) > EPS:matches=False
        return matches

    def source_scope(self):
        demands = self.facts('demand_lines')
        if not demands:
            self.missing('source.demand_lines.nonempty')
            return
        projected = self.data.get('demand_lines')
        if not isinstance(projected, list):
            self.missing('data.demand_lines')
            return
        expected = {r['id']: r for r in demands}
        actual = {r['id']: r for r in projected}
        effective=set(expected)
        if 'professional_overlay' in self.meta:
            from p22.professional_policy import effective_demands
            effective={r['id'] for r in effective_demands(self.data)}
        self.check(len(expected) == len(demands), 'DUPLICATE_SOURCE_DEMAND')
        self.check(Counter(r['id'] for r in projected) == Counter(effective), 'DEMAND_PROJECTION_SCOPE')
        profile=self.meta.get('policy',{}).get('profile')
        if profile=='odoo-verified-manufacturing-v1':
            # Independent sales arithmetic from retained ERP originals. The
            # historic SIM 24/480 gate continues to apply to that SIM profile.
            bundle=self.facts('locations')[0].get('odoo_bundle',{})
            capture=bundle.get('capture',{});documents=capture.get('documents',{})
            from interop.odoo_planning import ident,key
            orders={r['id']:r for r in documents.get('sale.order',[]) if r['state']=='sale'}
            units={r['id']:r['name'] for r in documents.get('uom.uom',[])}
            originals={key('sale.order.line',r['id']):r for r in documents.get('sale.order.line',[])
                if not r['display_type'] and quantity(r['product_uom_qty'])>quantity(r['qty_delivered'])}
            self.check(set(expected)==set(originals),'ODOO_OPEN_SALES_SCOPE')
            for identifier,r in originals.items():
                order=orders.get(ident(r['order_id']));out=expected.get(identifier,{})
                self.check(order is not None and out.get('item_id')==key('product.product',ident(r['product_id']))
                    and out.get('order_ref')==key('sale.order',ident(r['order_id']))
                    and quantity(out.get('quantity',-1))==quantity(r['product_uom_qty'])-quantity(r['qty_delivered'])
                    and out.get('uom')==units.get(ident(r['product_uom'])),'ODOO_OPEN_SALES_BALANCE',identifier)
        elif profile!='continuity-current-state-v1':
            from p22.foundation_editor import research_base_tables
            research_base=research_base_tables(self.data)
            original_demands=research_base['demand_lines'] if research_base is not None else demands
            self.check(len(original_demands) == 24 and sum(quantity(r['quantity']) for r in original_demands) == 480,
                       'COMPLETE_SIM_DEMAND_SCOPE')
        overlay=None
        if 'priority_overlay' in self.meta:
            from p22.foundation_projection import validate_priority_overlay
            from planning_adapter import InputError
            try:
                overlay=validate_priority_overlay(self.data)
                self.current['priority_overlay']=dict(overlay)
            except (InputError,KeyError,TypeError,ValueError,ArithmeticError):
                self.check(False,'FOUNDATION_PRIORITY_OVERLAY_INVALID')
        editor_overlay=None
        if self.meta.get('plan_editor_overlay'):
            from p22.foundation_editor import validate_overlay
            editor_overlay=validate_overlay(self.data)
        for ref in expected.keys() & actual.keys():
            for k in ('item_id', 'location_id', 'quantity', 'uom', 'due_at', 'priority',
                      'minimum_shipment', 'maximum_lateness_seconds'):
                if k not in expected[ref]:
                    continue
                wanted=overlay['after_priority'] if overlay and k=='priority' and ref==overlay['demand_id'] else expected[ref][k]
                if k=='priority' and editor_overlay is not None:
                    wanted=editor_overlay['after_priorities'][ref]
                same = (quantity(wanted) == quantity(actual[ref][k]) if k in
                        ('quantity', 'minimum_shipment', 'maximum_lateness_seconds') else
                        self.at(wanted) == self.at(actual[ref][k]) if k == 'due_at' else
                        wanted == actual[ref].get(k))
                self.check(same, 'DEMAND_PROJECTION_FACT_CHANGED', ref, field=k)
        if self.meta.get('policy', {}).get('enterprise_id') == 'SIM-GARMENT-01':
            from p22.foundation_editor import research_base_tables
            original_source=research_base_tables(self.data)
            original_source=self.source if original_source is None else original_source
            self.check(len(original_source) == 28 and sum(len(v) for v in original_source.values()) == 419,
                       'COMPLETE_SIM_SOURCE_ROWS')
        if self.meta.get('policy', {}).get('enterprise_id') == 'SIM-GARMENT-01' or self.meta.get('joint_supply_scope'):
            receipts = self.meta.get('row_receipts')
            if not isinstance(receipts, list):
                self.missing('foundation_projection.row_receipts')
            else:
                self.check(Counter((k, r['id']) for k, rows in self.source.items() for r in rows) ==
                           Counter((r['table'], r['id']) for r in receipts), 'SOURCE_ROW_RECEIPT_SCOPE')

    def native_identity(self):
        self.native_feasibility()
        expected = hashlib.sha256(json.dumps(self.data, sort_keys=True, separators=(',', ':'),
                                             ensure_ascii=False).encode()).hexdigest()
        if not self.run_id:
            self.missing('expected_run_id')
        for phase in ('pre', 'post'):
            snap = self.capture(phase)
            if snap is None:
                continue
            for key, wanted in [('run_id', self.run_id), ('input_hash', expected)]:
                if key not in snap:
                    self.missing('raw.fixed_batch_' + phase + '.' + key)
                elif wanted is not None:
                    self.check(snap[key] == wanted, 'NATIVE_RUN_MISMATCH' if key == 'run_id' else
                               'NATIVE_INPUT_MISMATCH', phase)
            if 'move_approved_early' not in snap:
                self.missing('raw.fixed_batch_' + phase + '.move_approved_early')
            else:
                self.check(snap['move_approved_early'] == 0, 'APPROVED_POLICY_DRIFT', phase)
            for model in ('qualified_model', 'wip_model'):
                captured = snap.get(model)
                if not isinstance(captured, dict) or 'fix_broken_supply_path' not in captured:
                    self.missing('raw.fixed_batch_' + phase + '.' + model + '.fix_broken_supply_path')
                else:
                    self.check(captured['fix_broken_supply_path'] in (False, 'false', 'False'),
                               'BROKEN_SUPPLY_PATH_POLICY_DRIFT', phase)

    def native_feasibility(self):
        """Validate final native diagnostics, never infer or repair their flags.

        Old archives without the post-refresh capture are NOT_CHECKED. The
        official export omits a true API flag, so omission is not independent
        proof of a completed refresh. A pre-loading false flag can be pending;
        only final API/post evidence is assessed here.
        """
        def flag(row,key,ref,path,*,final=True):
            if key not in row:
                self.missing(path+'.'+key);return None
            value=row[key]
            self.check(type(value) is bool,'NATIVE_FEASIBILITY_FLAG_TYPE',ref,evidence=path+'.'+key)
            if type(value) is not bool:return None
            if final:self.check(value,'NATIVE_ORDER_INFEASIBLE',ref,evidence=path+'.'+key)
            return value

        api_refs=set()
        for key in ('manufacturingorder','workorder'):
            for row in self.rows(key) or []:
                ref=str(row['reference']);api_refs.add(ref)
                plan=row.get('plan')
                if isinstance(plan,dict) and 'feasible' in plan:
                    flag(plan,'feasible',ref,'raw.'+key+'.'+ref+'.plan')
        post=self.raw.get('fixed_batch_post')
        if not isinstance(post,dict):return
        records=table(post.get('operationplans'))
        final_flags={}
        if records is None:
            self.missing('raw.fixed_batch_post.operationplans')
        else:
            for row in records:
                ref=str(row['reference'])
                final_flags[ref]=flag(row,'feasible',ref,'raw.fixed_batch_post.operationplans.'+ref)
            self.check(len(final_flags)==len(records),'NATIVE_FEASIBILITY_REFRESH_SCOPE','post.operationplans')
            if self.complete_orders():
                self.check(set(final_flags)==api_refs,'NATIVE_FEASIBILITY_REFRESH_SCOPE','post.operationplans')

        root=self.xml_root('post')
        xml_refs=[]
        if root is not None:
            for node in root.findall('operationplans/operationplan'):
                ref=node.get('reference',node.findtext('reference'))
                if ref is None:
                    self.missing('native_post.xml.operationplan.reference');continue
                xml_refs.append(ref)
                value=node.findtext('feasible')
                if value is not None:
                    self.check(value in ('true','false','1','0'),'NATIVE_FEASIBILITY_FLAG_TYPE',ref,evidence='native_post.xml')
                    self.check(value not in ('false','0'),'NATIVE_ORDER_INFEASIBLE',ref,evidence='native_post.xml')
                    if ref in final_flags and final_flags[ref] is not None and value in ('true','false','1','0'):
                        self.check(final_flags[ref] == (value in ('true','1')),
                                   'NATIVE_FEASIBILITY_REFRESH_CONTRACT',ref,evidence='native_post.xml')

        path='raw.fixed_batch_post.native_feasibility_refresh'
        receipt=post.get('native_feasibility_refresh')
        if receipt is None:
            self.missing(path);return
        if not isinstance(receipt,dict):
            self.check(False,'NATIVE_FEASIBILITY_REFRESH_CONTRACT',path);return
        for key,wanted in [('method','OFFICIAL_OPERATIONPLAN_UPDATE_FEASIBLE'),('business_fields_unchanged',True)]:
            if key not in receipt:self.missing(path+'.'+key)
            else:self.check(receipt[key] is True if key=='business_fields_unchanged' else receipt[key]==wanted,
                            'NATIVE_FEASIBILITY_REFRESH_CONTRACT',path,field=key)
        if root is None:self.missing(path+'.authenticated_post_xml')
        rows=table(receipt.get('rows'))
        if rows is None:
            if 'rows' not in receipt:self.missing(path+'.rows')
            else:self.check(False,'NATIVE_FEASIBILITY_REFRESH_CONTRACT',path,field='rows')
            return
        if 'count' not in receipt:self.missing(path+'.count')
        else:self.check(type(receipt['count']) is int and receipt['count']==len(rows),
                        'NATIVE_FEASIBILITY_REFRESH_CONTRACT',path,field='count')
        refs=[];before_flags={};after_flags={}
        for index,row in enumerate(rows):
            ref=row.get('reference')
            if ref is None:
                self.missing(path+'.rows.'+str(index)+'.reference');continue
            self.check(isinstance(ref,str) and bool(ref),'NATIVE_FEASIBILITY_REFRESH_SCOPE',str(ref))
            if not isinstance(ref,str) or not ref:continue
            refs.append(ref)
            before_flags[ref]=flag(row,'before',ref,path+'.rows.'+ref,final=False)
            after_flags[ref]=flag(row,'after',ref,path+'.rows.'+ref)
            if ref in final_flags and final_flags[ref] is not None and after_flags[ref] is not None:
                self.check(final_flags[ref] == after_flags[ref],'NATIVE_FEASIBILITY_REFRESH_CONTRACT',ref)
        self.check(len(set(refs))==len(refs),'NATIVE_FEASIBILITY_REFRESH_SCOPE',path)
        if len(refs)==len(rows):
            if records is not None:
                self.check(set(final_flags)<=set(refs),'NATIVE_FEASIBILITY_REFRESH_SCOPE',path)
            if root is not None:
                # Pinned model.h documents OperationInventory/STCK as hidden
                # operationplans. XML stores their buffer onhand, not a root
                # order. They still occur in the official all-operation loop.
                stock_refs=self.xml_stock_references(root,post)
                self.check(Counter(refs)==Counter(xml_refs)+Counter(stock_refs),
                           'NATIVE_FEASIBILITY_REFRESH_SCOPE',path)
        for key,values in [('false_before',before_flags),('false_after',after_flags)]:
            if key not in receipt:self.missing(path+'.'+key);continue
            listed=receipt[key]
            valid=isinstance(listed,list) and all(isinstance(ref,str) for ref in listed)
            self.check(valid,'NATIVE_FEASIBILITY_REFRESH_CONTRACT',path,field=key)
            if valid and len(values)==len(rows) and all(type(value) is bool for value in values.values()):
                self.check(Counter(listed)==Counter(ref for ref,value in values.items() if value is False),
                           'NATIVE_FEASIBILITY_REFRESH_CONTRACT',path,field=key)

    def xml_stock_references(self,root,post):
        """Account only for authenticated nonzero inventory-opening orders.

        This is not a generic exception for hidden orders or extra references:
        the XML buffer, native opening quantity/balance and epoch must agree.
        The material check independently reconciles these with source and API.
        """
        native=table(post.get('buffers'))
        result=[]
        def name(node,key):
            child=node.find(key)
            return None if child is None else child.get('name',child.findtext('name'))
        for node in root.findall('buffers/buffer'):
            opening=quantity(node.findtext('onhand','0'))
            if abs(opening)<=EPS:continue
            ref=node.get('name',node.findtext('name'))
            if not ref:
                self.missing('native_post.xml.buffer.name');continue
            result.append(ref)
            if native is None:
                self.missing('raw.fixed_batch_post.buffers');continue
            rows=[b for b in native if b.get('name')==ref and
                  (b.get('item'),b.get('location'))==(name(node,'item'),name(node,'location'))]
            self.check(len(rows)==1,'NATIVE_FEASIBILITY_REFRESH_SCOPE',ref,evidence='stock_buffer')
            if len(rows)!=1:continue
            buffer=rows[0]
            if buffer.get('onhand') is None:
                self.missing('native_post.buffer.'+ref+'.onhand');continue
            self.check(abs(quantity(buffer['onhand'])-opening)<=EPS,
                       'NATIVE_FEASIBILITY_REFRESH_SCOPE',ref,evidence='stock_onhand')
            events=table(buffer.get('events'))
            if events is None:
                self.missing('native_post.buffer.'+ref+'.events');continue
            matched=[event for event in events if event.get('reference')==ref]
            valid=len(matched)==1 and all(key in matched[0] for key in ('at','quantity','onhand'))
            if valid:
                event=matched[0]
                valid=(self.at(event['at']).replace(tzinfo=None)==datetime(1971,1,1) and
                       abs(quantity(event['quantity'])-opening)<=EPS and
                       abs(quantity(event['onhand'])-opening)<=EPS)
            self.check(valid,'NATIVE_FEASIBILITY_REFRESH_SCOPE',ref,evidence='stock_opening_event')
        return result

    def routings(self):
        mapping = self.meta.get('native_operation_map', {})
        recipes={r['id']:r for r in self.facts('recipes')}
        by_recipe = defaultdict(list)
        for operation in self.facts('operations'):
            by_recipe[operation['recipe_id']].append(operation)
        expected_map = {}
        for rid, operations in by_recipe.items():
            for operation in operations:
                name = rid if len(operations) == 1 else rid + ':' + operation['id']
                expected_map[name] = dict(recipe_id=rid, operation_id=operation['id'])
        self.check(mapping == expected_map, 'NATIVE_OPERATION_MAP_SCOPE')
        rows = self.operation_rows()
        for ref, row in rows.items():
            operation = row.get('operation')
            receipt=next((r for r in self.data.get('purchase_receipts',[]) if r['quality_status']=='PENDING_QC' and r['release_at'] is not None and ref=='QC:'+r['receipt_reference']),None)
            if receipt is not None:
                self.check(operation=='RECEIPT-RELEASE:'+receipt['receipt_reference'] and row['status']=='confirmed' and row.get('owner') is None and
                    quantity(row['quantity'])==quantity(receipt['quantity']) and self.at(row['startdate'])==self.at(row['enddate'])==self.at(receipt['release_at']),
                    'QC_RELEASE_IS_NOT_MANUFACTURING',ref)
                continue
            self.check(operation in mapping or (operation in by_recipe and len(by_recipe[operation]) > 1),
                       'UNKNOWN_NATIVE_MANUFACTURING_OPERATION', ref)
            if row.get('status')=='proposed' and operation in recipes:
                recipe=recipes[operation]
                if not {'minimum_quantity','multiple'}<=set(recipe):
                    self.missing('source.recipe.'+operation+'.manufacturing_sizes')
                else:
                    q=quantity(row['quantity']);minimum=quantity(recipe['minimum_quantity']);multiple=quantity(recipe['multiple'])
                    maximum=quantity(recipe['maximum_quantity']) if recipe.get('maximum_quantity') is not None else None
                    self.check(q>0 and q>=minimum and (maximum is None or q<=maximum) and (multiple==0 or q%multiple==0),
                               'MANUFACTURING_LOT_SIZE',ref)
            if operation in mapping and len(by_recipe[mapping[operation]['recipe_id']]) > 1:
                self.check(str(row.get('owner')) in rows, 'ORPHAN_ROUTING_CHILD', ref)
            if operation not in by_recipe or len(by_recipe[operation]) < 2:
                continue
            children = [r for r in rows.values() if str(r.get('owner')) == ref]
            specs = by_recipe[operation]
            if self.complete_orders():
                self.check(Counter(r.get('operation') for r in children) ==
                           Counter(operation + ':' + spec['id'] for spec in specs), 'ROUTING_CHILD_SCOPE', ref)
            child_by_op = {mapping[r['operation']]['operation_id']:r for r in children if r.get('operation') in mapping}
            for spec in specs:
                child = child_by_op.get(spec['id'])
                if child is None:
                    continue
                self.check(quantity(child['quantity']) == quantity(row['quantity']), 'ROUTING_CHILD_QUANTITY', str(child['reference']))
                self.check(self.at(row['startdate']) <= self.at(child['startdate']) <= self.at(child['enddate']) <= self.at(row['enddate']) if spec['duration_model']=='AVAILABILITY_TRANSFER' else self.at(row['startdate']) <= self.at(child['startdate']) < self.at(child['enddate']) <= self.at(row['enddate']),
                           'ROUTING_CHILD_TIME', str(child['reference']))
                previous = child_by_op.get(spec.get('predecessor_id'))
                if previous:
                    self.check(self.at(previous['enddate']) <= self.at(child['startdate']), 'ROUTING_PRECEDENCE', str(child['reference']))

    def demand_results(self):
        rows = self.rows('demand')
        deliveries = self.rows('deliveryorder')
        if rows is None:
            return
        if 'professional_overlay' in self.meta:
            from p22.professional_policy import effective_demands
            expected={d['id']:d for d in effective_demands(self.data)}
        else:expected = {d['id']: d for d in self.facts('demand_lines')}
        self.check(Counter(r['name'] for r in rows) == Counter(expected.keys()), 'NATIVE_DEMAND_SCOPE')
        actual_deliveries = defaultdict(Decimal)
        for row in deliveries or []:
            ref = row.get('demand')
            self.check(ref in expected, 'DELIVERY_UNKNOWN_DEMAND', str(row.get('reference')))
            qty = quantity(row['quantity'])
            self.check(qty >= 0, 'DELIVERY_NEGATIVE_QUANTITY', str(row.get('reference')))
            actual_deliveries[ref] += qty
            if ref in expected:
                self.check((row.get('item'), row.get('location')) ==
                           (expected[ref]['item_id'], expected[ref]['location_id']), 'DELIVERY_WRONG_OBJECT', ref)
                if row.get('enddate') is None:
                    self.missing('deliveryorder.' + str(row.get('reference')) + '.enddate')
                else:
                    due = self.at(expected[ref]['due_at'])
                    limit = expected[ref].get('maximum_lateness_seconds')
                    if limit is not None:
                        self.check(self.at(row['enddate']) <= due + timedelta(seconds=float(limit)),
                                   'DELIVERY_MAXIMUM_LATENESS', ref)
        for row in rows:
            ref = row['name']
            if ref not in expected:
                continue
            self.check(quantity(row['quantity']) == quantity(expected[ref]['quantity']),
                       'NATIVE_DEMAND_QUANTITY_CHANGED', ref)
            for native, source in [('item','item_id'),('location','location_id'),('customer','customer_id')]:
                if source not in expected[ref]:
                    continue
                if native not in row:
                    self.missing('demand.' + ref + '.' + native)
                else:
                    self.check(row[native] == expected[ref][source], 'NATIVE_DEMAND_IDENTITY_CHANGED', ref, field=native)
            if row.get('plannedquantity') is None:
                self.missing('demand.' + ref + '.plannedquantity')
                continue
            planned = quantity(row['plannedquantity'])
            self.check(0 <= planned <= quantity(expected[ref]['quantity']), 'DEMAND_PLANNED_QUANTITY', ref)
            if deliveries is not None:
                self.check(abs(actual_deliveries[ref] - planned) <= EPS, 'DEMAND_DELIVERY_RECONCILIATION', ref)

    def operation_models(self):
        operations = {o['id']:o for o in self.facts('operations')}
        recipes = {r['id']:r for r in self.facts('recipes')}
        boms = self.facts('bom_lines')
        mapping = self.meta.get('native_operation_map',{})
        for phase in ('pre','post'):
            model = self.capture(phase,'wip_model')
            if not isinstance(model,dict):
                continue
            xml_flows = self.xml_flows(phase)
            rows = table(model.get('operations'))
            if rows is None:
                self.missing('native_' + phase + '.wip_model.operations');continue
            for name,identity in mapping.items():
                spec = operations.get(identity['operation_id']);recipe = recipes.get(identity['recipe_id'])
                if spec is None or recipe is None:
                    self.missing('source.operation_model.' + name);continue
                actual = next((r for r in rows if r.get('name') == name),None)
                self.check(actual is not None,'NATIVE_PROCESSING_OPERATION_MISSING',name,phase=phase)
                if actual is None:
                    continue
                expected_type = 'operation_fixed_time' if spec['duration_model'] in {'FIXED_BATCH','AVAILABILITY_TRANSFER'} else 'operation_time_per'
                self.check(actual.get('type') == expected_type,'NATIVE_PROCESSING_OPERATION_TYPE',name,phase=phase)
                for field,business in [('size_minimum','minimum_quantity'),('size_multiple','multiple'),('size_maximum','maximum_quantity')]:
                    if recipe.get(business) is None:continue
                    if actual.get(field) is None:self.missing('native_'+phase+'.operation.'+name+'.'+field)
                    else:self.check(abs(quantity(actual[field])-quantity(recipe[business]))<=EPS,'NATIVE_MANUFACTURING_SIZE_CHANGED',name,phase=phase,field=field)
                for native,business in [('fixed_seconds','fixed_seconds'),('seconds_per_unit','seconds_per_unit')]:
                    if business == 'seconds_per_unit' and spec['duration_model'] in {'FIXED_BATCH','AVAILABILITY_TRANSFER'}:
                        continue
                    if actual.get(native) is None:
                        self.missing('native_' + phase + '.operation.' + name + '.' + native)
                    else:
                        self.check(abs(quantity(actual[native])-quantity(spec.get(business) or 0)) <= EPS,
                                   'NATIVE_PROCESSING_DURATION_CHANGED',name,phase=phase,field=native)
                flows = table(actual.get('flows'))
                if flows is None:
                    self.missing('native_' + phase + '.operation.' + name + '.flows');continue
                expected = Counter()
                for line in boms:
                    if line['recipe_id'] == recipe['id'] and line['consume_operation_id'] == spec['id']:
                        q = -quantity(line['quantity_per_output']) / (1-quantity(line['scrap_fraction']))
                        expected[(line['input_item_id'],numeric(q),line['consume_at'])] += 1
                if spec.get('produces_output') is True:
                    expected[(recipe['output_item_id'],'1','END')] += 1
                self.check(Counter((a,b) for a,b,_ in expected.elements()) ==
                           Counter((f['item'],numeric(f['quantity'])) for f in flows),
                           'NATIVE_BOM_MODEL_CHANGED',name,phase=phase)
                typed = xml_flows.get(name) if xml_flows is not None else flows
                if typed is None or any(f.get('type') not in ('flow_start','flow_end') for f in typed):
                    self.missing('native_' + phase + '.operation.' + name + '.flow_type')
                else:
                    observed=Counter((f['item'],numeric(f['quantity']), 'START' if f['type'] == 'flow_start' else 'END') for f in typed)
                    self.check(expected == observed,'NATIVE_BOM_MODEL_CHANGED',name,phase=phase)

    def xml_root(self,phase):
        """Inspect actual XML; a snapshot hash alone proves no model fields."""
        value=self.raw.get('fixed_batch_'+phase+'_xml')
        if value is None:return None
        encoded=value if isinstance(value,bytes) else value.encode('utf-8')
        capture=self.capture(phase)
        if capture is None or not capture.get('native_xml_sha256'):
            self.missing('native_'+phase+'.native_xml_sha256');return None
        matches=hashlib.sha256(encoded).hexdigest() == capture['native_xml_sha256']
        self.check(matches,'NATIVE_XML_IDENTITY',phase)
        if not matches:return None
        try:root=ET.fromstring(encoded)
        except ET.ParseError as exc:raise ValueError('invalid native XML: '+str(exc)) from exc
        return root

    def xml_flows(self,phase):
        root=self.xml_root(phase)
        if root is None:return None
        result={}
        for operation in root.findall('operations/operation'):
            name=operation.get('name',operation.findtext('name'))
            flows=[]
            for flow in operation.findall('flows/flow'):
                item=flow.find('item')
                if item is None or flow.findtext('quantity') is None:
                    self.missing('native_'+phase+'.xml.'+str(name)+'.flow_item_or_quantity');continue
                # Pinned 9.19 flow.cpp:37-40 registers flow_start as the XML
                # default class. The Python class name "flow" is not this proof.
                flows.append(dict(item=item.get('name',item.findtext('name')),quantity=flow.findtext('quantity'),
                    type=flow.get('{http://www.w3.org/2001/XMLSchema-instance}type','flow_start')))
            result[name]=flows
        return result

    def xml_supplies(self,phase):
        root=self.xml_root(phase)
        if root is None:return None
        result=[]
        for item in root.findall('items/item'):
            for path in item.findall('itemsuppliers/itemsupplier'):
                row=dict(item=item.get('name',item.findtext('name')),
                         effective_start=path.findtext('effective_start'),effective_end=path.findtext('effective_end'))
                for key in ('supplier','location','resource'):
                    node=path.find(key)
                    row[key]=node.get('name',node.findtext('name')) if node is not None else None
                result.append(row)
        return result

    def protected_processing_facts(self):
        """Independent expansion of source commitments, never extra supply."""
        rows=[]
        for original in self.facts('existing_manufacturing_orders'):
            operations=original.get('plan_operations',[])
            if len(operations)<=1:
                rows.append(original);continue
            for operation in operations:
                rows.append(dict(original,**operation,id=original['id']+'::WO::'+operation['operation_id'],
                    root_id=original['id']))
        return rows

    def protected_manufacturing(self):
        post=self.raw.get('fixed_batch_post',{})
        resolutions=post.get('protected_setup_resolution',[]) if isinstance(post,dict) else []
        if any(r.get('exchange_before') for r in resolutions if isinstance(r,dict)):
            proof=audit_rebinding_receipts(self.data,post)
            self.current['machine_exchange_proof']=proof
            self.current['issues'].extend(proof['issues'])
            for name in proof['missing_evidence']:self.missing(name)
        originals = self.protected_processing_facts()
        orders = self.operation_rows()
        for original in self.facts('existing_manufacturing_orders'):
            if len(original.get('plan_operations',[]))<=1:continue
            root_id=original['id']
            views=[('api',orders.get(root_id))]
            for phase in ('pre','post'):
                captured=self.capture(phase,'operationplans')
                if not isinstance(captured,list):continue
                matches=[r for r in captured if str(r.get('reference'))==root_id]
                self.check(len(matches)==1,'EXTERNAL_MO_ROOT_MISSING',root_id,phase=phase)
                if matches:views.append((phase,matches[0]))
            for phase,row in views:
                if row is None:
                    if self.complete_orders():self.check(False,'EXTERNAL_MO_ROOT_MISSING',root_id,phase=phase)
                    continue
                self.check(row.get('owner') is None and row.get('operation')==original['recipe_id'] and
                    row.get('status')==original['status'] and quantity(row.get('quantity',0))==quantity(original['quantity']),
                    'EXTERNAL_MO_ROOT_CHANGED',root_id,phase=phase)
                for key,api,native in [('start_at','startdate','start'),('end_at','enddate','end')]:
                    value=row.get(api) if phase=='api' else row.get(native)
                    self.check(value is not None and self.at(value)==self.at(original[key]),'EXTERNAL_MO_ROOT_CHANGED',root_id,phase=phase,field=key)
        pre = self.raw.get('fixed_batch_pre')
        pending_rows = pre.get('protected_setup_pending', []) if isinstance(pre, dict) else []
        if table(pending_rows) is None:
            self.check(False, 'PROTECTED_SETUP_PENDING_INVALID', field='list')
            pending_rows = []
        source_ids = {o['id'] for o in originals}
        refs = [str(p.get('reference')) for p in pending_rows]
        self.check(len(refs) == len(set(refs)) and set(refs) <= source_ids,
                   'PROTECTED_SETUP_PENDING_INVALID', field='scope')
        if pending_rows:
            self.check(pre.get('protected_window_status') == 'PENDING_SETUP_NOT_VALIDATED',
                       'PROTECTED_SETUP_PENDING_INVALID', field='status')
        self.current['pre_setup_pending'] = []
        for original in originals:
            ref = original['id']
            expected_operation = next((name for name, value in self.meta.get('native_operation_map', {}).items()
                                       if value['recipe_id'] == original['recipe_id'] and
                                       value['operation_id'] == original['operation_id']), None)
            views = [('api', orders.get(ref))]
            for phase in ('pre', 'post'):
                snaprows = self.capture(phase, 'operationplans')
                if not isinstance(snaprows, list):
                    if snaprows is not None:
                        self.missing('raw.fixed_batch_' + phase + '.operationplans.list')
                    continue
                matches = [r for r in snaprows if str(r.get('reference')) == ref]
                self.check(len(matches) == 1, 'PROTECTED_MO_MISSING', ref, phase=phase)
                if matches:
                    views.append((phase, matches[0]))
            if self.complete_orders():
                self.check(ref in orders, 'PROTECTED_MO_MISSING', ref, phase='api')
            for phase, row in views:
                if row is None:
                    continue
                if original.get('root_id'):
                    self.check(row.get('owner')==original['root_id'],'EXTERNAL_MO_WORKORDER_OWNER_CHANGED',ref,phase=phase)
                pending = [p for p in pending_rows if p.get('reference') == ref] if phase == 'pre' else []
                defer_end = len(pending) == 1 and self.valid_pre_setup_pending(original, expected_operation, row, pending[0])
                if pending:
                    self.check(defer_end, 'PROTECTED_SETUP_PENDING_INVALID', ref)
                    if defer_end:
                        self.current['pre_setup_pending'].append(dict(reference=ref,
                            status='FINAL_WINDOW_MATCH_REQUIRED_BY_COMBINED_AUDIT'))
                wanted = [('quantity', original['quantity']), ('status', original['status']),
                          ('operation', expected_operation)]
                for field, expected in wanted:
                    if row.get(field) is None:
                        self.missing('protected.' + phase + '.' + ref + '.' + field)
                        continue
                    same = quantity(row[field]) == quantity(expected) if field == 'quantity' else row[field] == expected
                    self.check(same, 'PROTECTED_MO_CHANGED', ref, phase=phase, field=field)
                for source_key, api_key, capture_key in [('start_at', 'startdate', 'start'), ('end_at', 'enddate', 'end')]:
                    actual = row.get(api_key if phase == 'api' else capture_key)
                    if actual is None:
                        self.missing('protected.' + phase + '.' + ref + '.' + source_key)
                    elif not (defer_end and source_key == 'end_at'):
                        self.check(self.at(actual) == self.at(original[source_key]), 'PROTECTED_MO_CHANGED',
                                   ref, phase=phase, field=source_key)
                assigned = row.get('resources')
                if assigned is None and phase != 'api':
                    self.missing('protected.' + phase + '.' + ref + '.resources')
                elif assigned is not None:
                    assigned = resource_names(assigned)
                    machines = {r['id'] for r in self.facts('resources')}
                    self.check(set(assigned) & machines == {original['resource_id']},
                               'PROTECTED_MO_MACHINE_CHANGED', ref, phase=phase)
                    people = {r['id'] for r in self.facts('operators')}
                    if original.get('operator_id'):
                        self.check(set(assigned)&people=={original['operator_id']},'EXTERNAL_MO_NAMED_PERSON_CHANGED',ref,phase=phase)
                    source_operation = next((o for o in self.facts('operations') if o['id'] == original['operation_id']),None)
                    if source_operation and source_operation.get('machine_and_person_simultaneously_required') is True:
                        self.check(len(set(assigned) & people) == 1,'NATIVE_REQUIRED_PERSON_LOAD_MISSING',ref,phase=phase)

    def valid_pre_setup_pending(self, original, expected_operation, row, receipt):
        """Independently recognize one unvalidated native setup expansion.

        This is not a relaxed final commitment. Only the pre end may differ;
        the final post/API window, setup sequence and all constraints remain
        independently mandatory. The receipt cannot redefine source facts.
        """
        try:
            expected = receipt['expected']
            identity = dict(reference=original['id'], operation=expected_operation,
                            status=original['status'], resource=original['resource_id'])
            if any(expected.get(k) != v for k,v in identity.items()):
                return False
            if any(row.get(k) != identity[k] for k in ('reference','operation','status')):
                return False
            if receipt['observed'] != row or quantity(expected['quantity']) != quantity(original['quantity']) or quantity(row['quantity']) != quantity(original['quantity']):
                return False
            source_start,source_end = self.at(original['start_at']),self.at(original['end_at'])
            if (self.at(expected['start']),self.at(expected['end'])) != (source_start,source_end):
                return False
            start,end,setup_start,setup_end = (self.at(row[k]) for k in ('start','end','setup_start','setup_end'))
            duration = quantity(row['setup_duration_seconds'])
            if not (duration > 0 and start == setup_start == source_start and
                    quantity((setup_end-start).total_seconds()) == duration and
                    quantity((end-source_end).total_seconds()) == duration and
                    end-setup_end == source_end-source_start):
                return False
            operation = next(o for o in self.facts('operations') if o['id'] == original['operation_id'])
            work = quantity(operation.get('fixed_seconds') or 0)
            if operation['duration_model'] != 'FIXED_BATCH':
                work += quantity(operation.get('seconds_per_unit') or 0) * quantity(original['quantity'])
            if work != quantity((source_end-source_start).total_seconds()):
                return False
            required = self.meta.get('operation_setups', {}).get(original['operation_id'])
            initial = self.meta.get('resource_initial_setups', {}).get(original['resource_id'])
            rules = [r for r in self.meta.get('setup_transitions', [])
                     if (r['from_state'],r['to_state']) == (initial,required)]
            if len(rules) != 1 or quantity(rules[0]['duration_seconds']) != duration:
                return False
            resources = {r['id']:r for r in self.facts('resources') + self.facts('operators')}
            machine_ids = {r['id'] for r in self.facts('resources')}
            people = {r['id'] for r in self.facts('operators')}
            assigned = resource_names(row['resources'])
            if len(assigned) != 2 or set(assigned) & machine_ids != {original['resource_id']} or len(set(assigned) & people) != 1:
                return False
            calendars = {c['id']:c for c in self.facts('calendars')}
            for rid in assigned:
                intervals = [(self.at(i['start_at']),self.at(i['end_at']))
                             for i in calendars[resources[rid]['calendar_id']]['available_intervals']]
                if quantity(working_seconds(start,end,intervals)) != quantity((end-start).total_seconds()):
                    return False
            return True
        except (KeyError,TypeError,ValueError,ArithmeticError,StopIteration):
            return False

    def purchases(self):
        rows, flows = self.rows('purchaseorder'), self.rows('operationplanmaterial')
        sources = {s['id']: s for s in self.facts('supply_sources')}
        originals = {p['id']: p for p in self.facts('purchase_orders')}
        change=self.meta.get('professional_overlay',{}).get('changes',{}).get('supply',{}).get('purchase')
        if change:
            import copy
            originals=copy.deepcopy(originals)
            originals[change['reference']].update(physical_receipt_at=change['after']['expected_receipt_at'],available_at=change['after']['available_at'])
        if rows is None:
            return
        self.check(len({str(r['reference']) for r in rows}) == len(rows), 'DUPLICATE_PURCHASE_REFERENCE')
        seen, totals, allocations = set(), defaultdict(Decimal), defaultdict(list)
        for row in rows:
            ref = str(row['reference']); q = quantity(row['quantity']); original = originals.get(ref)
            start, end = self.at(row['startdate']), self.at(row['enddate'])
            candidates = [s for s in sources.values() if (s['item_id'], s['location_id'], s['supplier_id']) ==
                          (row['item'], row['location'], row['supplier'])]
            if original is not None:
                seen.add(ref)
                source = sources.get(original['source_id'])
                self.check(q == quantity(original['unreceived_quantity']) and row['status'] == original['status'] and
                           (row['item'], row['location']) == (original['item_id'], original['location_id']) and
                           end == self.at(original['physical_receipt_at']), 'CONFIRMED_PO_CHANGED', ref)
                if original.get('ordered_at') is not None:
                    self.check(start == self.at(original['ordered_at']), 'CONFIRMED_PO_CHANGED',ref,field='ordered_at')
                available = self.at(original['available_at'])
            elif len(candidates) == 1:
                source = candidates[0]
                available = end + timedelta(seconds=float(source['qc_wait_seconds']))
            else:
                source = None
                self.check(bool(candidates), 'UNKNOWN_PURCHASE_SOURCE', ref)
                if candidates:
                    self.missing('purchase.' + ref + '.unambiguous_source_identity')
                continue
            if source is None:
                self.check(False, 'UNKNOWN_PURCHASE_SOURCE', ref)
                continue
            self.check(row['supplier'] == source['supplier_id'], 'CONFIRMED_PO_CHANGED', ref, field='supplier')
            self.check(available == end + timedelta(seconds=float(source['qc_wait_seconds'])), 'SOURCE_PO_QC_CONTRACT',ref)
            self.check(q >= 0 and start <= end, 'PURCHASE_QUANTITY_OR_TIME', ref)
            if original is None:
                self.check(row['status'] == 'proposed', 'UNKNOWN_PURCHASE_STATUS', ref)
                self.check(q >= quantity(source['minimum_quantity']), 'PURCHASE_MINIMUM', ref)
                multiple = quantity(source['order_multiple'])
                self.check(multiple > 0 and q % multiple <= EPS, 'PURCHASE_MULTIPLE', ref)
                self.check((end - start).total_seconds() + float(EPS) >= float(source['lead_time_seconds']),
                           'PURCHASE_LEAD_TIME', ref)
                if source.get('effective_from') is not None and source.get('effective_to') is not None:
                    self.check(self.at(source['effective_from']) <= start <= self.at(source['effective_to']),
                               'PURCHASE_SOURCE_VALIDITY', ref)
            if self.at(self.data['horizon']['start_at']) <= end <= self.at(self.data['horizon']['end_at']):
                totals[source['id']] += q
                allocations[source['id']].append((ref,q,start,original is not None))
            else:
                self.check(False,'PURCHASE_OUTSIDE_CAPACITY_HORIZON',ref)
            if flows is not None:
                observed = [f for f in flows if str(f['operationplan']) == ref and quantity(f['quantity']) > 0]
                self.check(len(observed) == 1 and all(self.at(f['flowdate']) == available and
                           (f['item'], f['location']) == (row['item'], row['location']) and
                           abs(quantity(f['quantity']) - q) <= EPS for f in observed), 'PO_QC_AVAILABILITY', ref)
        self.check(seen == set(originals), 'CONFIRMED_PO_MISSING')
        if rows:
            bindings=self.purchase_capacity_bindings()
            loads=self.rows('operationplanresource')
            for row in rows:
                if str(row['reference']) not in bindings:
                    self.missing('purchase.'+str(row['reference'])+'.native_capacity_owner')
            if loads is not None:
                expected_loads=Counter((str(p['reference']),bindings[str(p['reference'])],numeric(p['quantity']),
                    self.at(p['startdate']),self.at(p['enddate'])) for p in rows if str(p['reference']) in bindings)
                actual_loads=Counter((str(l['operationplan']),l['resource'],numeric(l['quantity']),
                    self.at(l['startdate']),self.at(l['enddate'])) for l in loads if str(l['operationplan']) in bindings)
                self.check(expected_loads==actual_loads,'PURCHASE_RESOURCE_LOAD_RECONCILIATION')
        for ref, source in sources.items():
            self.check(source.get('capacity_includes_confirmed') is True, 'SUPPLY_CAPACITY_ACCOUNTING', ref)
            self.check(totals[ref] <= quantity(source['horizon_capacity']) + EPS, 'SUPPLY_HORIZON_CAPACITY', ref)
        for phase in ('pre', 'post'):
            model = self.capture(phase, 'foundation_model')
            if not isinstance(model, dict):
                continue
            for value in model.get('missing_evidence',[]):
                self.missing('native_' + phase + '.foundation_model.' + str(value))
            paths = model.get('supply_paths')
            alternates = model.get('supply_alternates')
            if sources and (not isinstance(paths, list) or not isinstance(alternates, list)):
                self.missing('raw.fixed_batch_' + phase + '.foundation_model.supply_paths_and_alternates')
                continue
            xml_paths=self.xml_supplies(phase) if sources else None
            for ref, source in sources.items():
                matches = [r for r in paths if r.get('source_id') == ref]
                self.check(len(matches) == 1, 'NATIVE_SUPPLY_PATH_SCOPE', ref, phase=phase)
                if not matches:
                    continue
                path = matches[0]
                for native, business in [('minimum', 'minimum_quantity'), ('multiple', 'order_multiple'),
                                         ('lead_time_seconds', 'lead_time_seconds'), ('qc_seconds', 'qc_wait_seconds'),
                                         ('priority', 'priority')]:
                    if path.get(native) is None:
                        self.missing('native_supply.' + phase + '.' + ref + '.' + native)
                    else:
                        self.check(quantity(path[native]) == quantity(source[business]),
                                   'NATIVE_SUPPLY_POLICY_CHANGED', ref, phase=phase, field=native)
                choice = [r for r in alternates if (r.get('item'), r.get('location')) ==
                          (source['item_id'], source['location_id'])]
                candidate_count = sum((s['item_id'],s['location_id']) == (source['item_id'],source['location_id'])
                                      for s in sources.values())
                if candidate_count > 1:
                    self.check(len(choice) == 1 and is_priority(choice[0].get('search')),
                               'NATIVE_SUPPLY_PRIORITY_STRATEGY', ref, phase=phase)
                if xml_paths is None:
                    self.missing('native_supply.' + phase + '.' + ref + '.effective_dates_xml')
                else:
                    matches=[p for p in xml_paths if (p['item'],p['supplier'],p['location'],p['resource']) ==
                             (source['item_id'],source['supplier_id'],source['location_id'],path.get('capacity_resource'))]
                    self.check(len(matches)==1,'NATIVE_SUPPLY_XML_PATH_SCOPE',ref,phase=phase)
                    if len(matches)==1:
                        for native,business in [('effective_start','effective_from'),('effective_end','effective_to')]:
                            value=matches[0].get(native)
                            if value is None or source.get(business) is None:
                                self.missing('native_supply.' + phase + '.' + ref + '.' + native)
                            else:
                                self.check(self.at(value) == self.at(source[business]) and
                                           (path.get(native) is None or self.at(path[native]) == self.at(value)),
                                           'NATIVE_SUPPLY_EFFECTIVITY_CHANGED',ref,phase=phase,field=native)
                native_resources = self.capture(phase,'resources')
                native_calendars = self.capture(phase,'calendars')
                if not isinstance(native_resources,list) or not isinstance(native_calendars,list):
                    continue
                resource = next((r for r in native_resources if r.get('name') == path.get('capacity_resource')),None)
                self.check(resource is not None and resource.get('type') == 'resource_buckets' and
                           quantity(path.get('capacity_per_unit',0)) == 1,'NATIVE_SUPPLY_CAPACITY_LINK',ref,phase=phase)
                if resource is None:
                    continue
                if not isinstance(resource.get('loadplans'),list):
                    self.missing('native_supply.' + phase + '.' + ref + '.loadplans')
                else:
                    expected_loads = Counter((pid,numeric(q),start) for pid,q,start,confirmed in allocations[ref]
                                             if phase == 'post' or confirmed)
                    actual_loads = Counter((str(l['reference']),numeric(abs(quantity(l['quantity']))),self.at(l['at']))
                                           for l in resource['loadplans'] if quantity(l['quantity']) != 0)
                    self.check(expected_loads == actual_loads and all(quantity(l['quantity']) <= 0 for l in resource['loadplans']),
                               'NATIVE_SUPPLY_CAPACITY_LOADS',ref,phase=phase)
                calendar = next((c for c in native_calendars if c.get('name') == resource.get('maximum_calendar')),None)
                if calendar is None:
                    self.missing('native_supply.' + phase + '.' + ref + '.capacity_calendar')
                    continue
                buckets = [b for b in calendar.get('buckets',[]) if quantity(b['value']) != 0]
                first = min([self.at(self.data['planning_at'])] + [start for _,_,start,confirmed in allocations[ref] if confirmed])
                self.check(quantity(calendar['default']) == 0 and len(buckets) == 1 and
                           self.at(buckets[0]['start']) == first and self.at(buckets[0]['end']) == self.at(self.data['horizon']['end_at']) and
                           quantity(buckets[0]['value']) == quantity(source['horizon_capacity']),
                           'NATIVE_SUPPLY_CAPACITY_CALENDAR',ref,phase=phase)

    def purchase_capacity_bindings(self):
        """Classify only actual PO identities and their captured source path.

        The procurement audit still verifies the XML link, quantity, dates,
        native bucket events and capacity. A resource-name prefix grants no
        exception to ownership checks.
        """
        purchases=table(self.raw.get('purchaseorder'))
        if not purchases:
            return {}
        model=self.capture('post','foundation_model')
        paths=table(model.get('supply_paths')) if isinstance(model,dict) else None
        if paths is None:
            self.missing('raw.fixed_batch_post.foundation_model.supply_paths');return {}
        sources=self.facts('supply_sources')
        originals={p['id']:p for p in self.facts('purchase_orders')}
        result={}
        for row in purchases:
            ref=str(row['reference']);original=originals.get(ref)
            candidates=[s for s in sources if s['id']==original['source_id']] if original else [s for s in sources
                if (s['item_id'],s['supplier_id'],s['location_id'])==(row['item'],row['supplier'],row['location'])]
            if len(candidates)!=1:
                continue
            matching=[p for p in paths if p.get('source_id')==candidates[0]['id']]
            if len(matching)==1 and isinstance(matching[0].get('capacity_resource'),str):
                result[ref]=matching[0]['capacity_resource']
        return result

    def fixed_batches(self):
        operations = {o['id']: o for o in self.facts('operations')}
        windows = self.facts('capacity_windows')
        usage = defaultdict(Decimal)
        mapping = self.meta.get('native_operation_map', {})
        leaves = self.leaf_rows()
        api_loads = self.rows('operationplanresource')
        for row in leaves:
            spec = operations.get(mapping[row['operation']]['operation_id'])
            if spec is None or spec['duration_model'] != 'FIXED_BATCH':
                continue
            ref = str(row['reference']); q = quantity(row['quantity']); batch = quantity(spec['batch_quantity'])
            self.check(q == batch, 'FIXED_BATCH_QUANTITY', ref)
            self.check(abs(Decimal(str(self.duration(row).total_seconds())) - quantity(spec['fixed_seconds'])) <= EPS,
                       'FIXED_BATCH_DURATION', ref)
            start = self.at(row['startdate']); end = self.at(row['enddate'])
            matching = [w for w in windows if w['resource_id'] in spec['resource_ids'] and
                        self.at(w['start_at']) <= start < end <= self.at(w['end_at'])]
            self.check(len(matching) == 1 and start == self.at(matching[0]['start_at']), 'FIXED_BATCH_WINDOW', ref)
            if matching:
                usage[matching[0]['id']] += q
        for window in windows:
            self.check(usage[window['id']] <= quantity(window['capacity_quantity']) + EPS,
                       'WINDOW_QUANTITY_CAPACITY', window['id'])
        if self.meta.get('policy', {}).get('enterprise_id') == 'SIM-GARMENT-01':
            self.check(len(windows) == 96 and all(quantity(w['capacity_quantity']) == 20 for w in windows),
                       'COMPLETE_SIM_WINDOW_SCOPE')
        for phase in ('pre', 'post'):
            native_ops = self.capture(phase, 'operations')
            native_cal = self.capture(phase, 'calendars')
            native_resources = self.capture(phase, 'resources')
            if isinstance(native_resources, list):
                self.bucket_loadplans(phase, native_resources, windows, operations, mapping, leaves, api_loads)
            if not all(isinstance(v, list) for v in (native_ops, native_cal, native_resources)):
                continue
            for name, identity in mapping.items():
                spec = operations.get(identity['operation_id'])
                if not spec or spec['duration_model'] != 'FIXED_BATCH':
                    continue
                actual = next((o for o in native_ops if o.get('name') == name), None)
                self.check(actual is not None, 'NATIVE_FIXED_OPERATION_MISSING', name, phase=phase)
                if actual is None:
                    continue
                if actual.get('duration') is None:
                    self.missing('native_fixed.' + phase + '.' + name + '.duration')
                else:
                    self.check(actual.get('type') == 'operation_fixed_time' and
                               abs(duration_seconds(actual['duration']) - quantity(spec['fixed_seconds'])) <= EPS,
                               'NATIVE_FIXED_BATCH_DURATION',name,phase=phase)
                for field in ('size_minimum', 'size_maximum', 'size_multiple'):
                    self.check(quantity(actual[field]) == quantity(spec['batch_quantity']),
                               'NATIVE_FIXED_BATCH_SIZE', name, phase=phase, field=field)
            for rid in {w['resource_id'] for w in windows}:
                resource = next((r for r in native_resources if r.get('name') == rid), None)
                self.check(resource is not None and resource.get('type') == 'resource_buckets',
                           'NATIVE_WINDOW_RESOURCE_TYPE', rid, phase=phase)
                if resource is None:
                    continue
                calendar = next((c for c in native_cal if c.get('name') == resource.get('maximum_calendar')), None)
                if calendar is None:
                    self.missing('native_window.' + phase + '.' + rid + '.maximum_calendar')
                    continue
                expected = Counter((self.at(w['start_at']), self.at(w['start_at']) + timedelta(seconds=1),
                                    numeric(w['capacity_quantity'])) for w in windows if w['resource_id'] == rid)
                actual = Counter((self.at(b['start']), self.at(b['end']), numeric(b['value']))
                                 for b in calendar['buckets'] if quantity(b['value']) != 0)
                self.check(quantity(calendar['default']) == 0 and expected == actual,
                           'NATIVE_WINDOW_CALENDAR', rid, phase=phase)

    def bucket_loadplans(self, phase, resources, windows, operations, mapping, leaves, api_loads):
        """Reconcile each actual quantity-bucket event, not calendar counts.

        Pre has only imported source commitments; post must agree with the
        same-run MO API and resource-load API. Bucket loads consume quantity
        once at the operation start, not continuously or at operation end.
        """
        if phase == 'post' and (not self.complete_orders() or api_loads is None):
            self.missing('native_window.post.complete_api_orders_and_loads');return
        fixed = {str(r['reference']):r for r in leaves
                 if operations[mapping[r['operation']]['operation_id']]['duration_model'] == 'FIXED_BATCH'}
        for rid in sorted({w['resource_id'] for w in windows}):
            matches = [r for r in resources if r.get('name') == rid]
            self.check(len(matches) == 1, 'NATIVE_BUCKET_RESOURCE_SCOPE', rid, phase=phase)
            if len(matches) != 1:
                continue
            events = table(matches[0].get('loadplans'))
            if events is None:
                self.missing('native_window.' + phase + '.' + rid + '.loadplans');continue
            expected = Counter()
            if phase == 'pre':
                for row in self.protected_processing_facts():
                    spec = operations.get(row['operation_id'])
                    if row['resource_id'] != rid or spec is None or spec['duration_model'] != 'FIXED_BATCH':
                        continue
                    start,end = self.at(row['start_at']),self.at(row['end_at'])
                    expected[(str(row['id']),numeric(-quantity(row['quantity'])),start,start,end)] += 1
            else:
                assigned = [l for l in api_loads if l.get('resource') == rid]
                refs = {str(l['operationplan']) for l in assigned}
                for ref,row in fixed.items():
                    if ref in refs:
                        start,end = self.at(row['startdate']),self.at(row['enddate'])
                        expected[(ref,numeric(-quantity(row['quantity'])),start,start,end)] += 1
                api = Counter((str(l['operationplan']),numeric(-quantity(l['quantity'])),
                    self.at(l['startdate']),self.at(l['startdate']),self.at(l['enddate'])) for l in assigned)
                self.check(api == expected, 'NATIVE_BUCKET_LOADPLAN_RECONCILIATION', rid,
                           phase=phase, view='api_resource_loads', expected_count=sum(expected.values()),
                           observed_count=sum(api.values()))
            observed = Counter((str(l['reference']),numeric(l['quantity']),self.at(l['at']),
                self.at(l['start']),self.at(l['end'])) for l in events)
            self.check(observed == expected, 'NATIVE_BUCKET_LOADPLAN_RECONCILIATION', rid,
                       phase=phase, view='native_bucket_events', expected_count=sum(expected.values()),
                       observed_count=sum(observed.values()))

    def resources(self):
        loads = self.rows('operationplanresource')
        operations = {o['id']: o for o in self.facts('operations')}
        machines = {r['id']: r for r in self.facts('resources')}
        people = {r['id']: r for r in self.facts('operators')}
        calendars = {c['id']: c for c in self.facts('calendars')}
        skills = self.facts('resource_skills')
        qualifications = self.facts('machine_qualifications')
        mapping = self.meta.get('native_operation_map', {})
        leaves = self.leaf_rows()
        if loads is None:
            loads = []  # pre model evidence is still independently inspectable
        leaf_ids = {str(r['reference']) for r in leaves}
        resource_events, expected_native = defaultdict(list), Counter()
        purchases=table(self.raw.get('purchaseorder'))
        purchase_refs={str(p['reference']) for p in purchases or []}
        purchase_bindings=self.purchase_capacity_bindings()
        if self.complete_orders():
            for load in loads:
                ref=str(load['operationplan'])
                if ref in leaf_ids:
                    continue
                if purchases is None:
                    self.missing('raw.purchaseorder.resource_ownership');continue
                if ref in purchase_refs and ref not in purchase_bindings:
                    self.missing('purchase.'+ref+'.native_capacity_owner');continue
                self.check(ref in purchase_bindings and purchase_bindings[ref]==load['resource'], 'ORPHAN_RESOURCE_LOAD', ref)
        for row in leaves:
            spec = operations.get(mapping[row['operation']]['operation_id'])
            if spec is None:
                self.missing('source.operation:' + row['operation']);continue
            ref = str(row['reference']); start = self.at(row['startdate']); end = self.at(row['enddate'])
            assigned = [l for l in loads if str(l['operationplan']) == ref]
            if spec['duration_model']=='AVAILABILITY_TRANSFER':
                receiving=self.meta.get('policy',{}).get('wip_progress_policy',{}).get('receiving',{})
                self.check(spec.get('continuation_kind')=='AVAILABILITY_TRANSFER' and not spec.get('resource_ids') and not assigned and start==end and receiving.get('basis')=='DECLARED_RESEARCH_ZERO_TIME_RECEIPT' and receiving.get('duration_seconds')==0,'AVAILABILITY_TRANSFER_CONTRACT',ref)
                calendar=calendars.get(receiving.get('calendar_id'))
                self.check(calendar is not None and any(self.at(w['start_at'])<=start<=self.at(w['end_at']) for w in (calendar or {}).get('available_intervals',[])),'AVAILABILITY_TRANSFER_CALENDAR',ref)
                continue
            machine = [l for l in assigned if l['resource'] in machines]
            person = [l for l in assigned if l['resource'] in people]
            self.check(len(machine) == 1, 'MACHINE_ASSIGNMENT', ref)
            self.check(len(person) == 1 and len(assigned) == 2, 'NAMED_PERSON_ASSIGNMENT', ref)
            self.check(start < end, 'OPERATION_TIME_RANGE', ref)
            if len(machine) == 1:
                rid = machine[0]['resource']
                self.check(rid in spec['resource_ids'], 'MACHINE_NOT_ELIGIBLE', ref)
                if spec.get('resources_are_alternatives'):
                    self.check(any(q['resource_id'] == rid and q['skill_id'] == spec['required_operator_skill'] and
                        self.at(q['effective_from']) <= start < end <= self.at(q['effective_to'])
                        for q in qualifications), 'MACHINE_QUALIFICATION', ref)
            if len(person) == 1:
                rid = person[0]['resource']
                self.check(any(q['resource_id'] == rid and q['skill_id'] == spec['required_operator_skill'] and
                    self.at(q['effective_from']) <= start < end <= self.at(q['effective_to']) for q in skills),
                    'PERSON_QUALIFICATION', ref)
            for load in assigned:
                rid = load['resource']; ls = self.at(load['startdate']); le = self.at(load['enddate']); units = quantity(load['quantity'])
                self.check((ls, le) == (start, end), 'RESOURCE_SYNCHRONIZATION', ref, resource=rid)
                r = machines.get(rid, people.get(rid))
                if r is None:
                    continue
                bucket = r.get('capacity_model') == 'WINDOW_QUANTITY'
                expected_units = quantity(row['quantity']) if bucket else Decimal(1)
                self.check(abs(units - expected_units) <= EPS, 'RESOURCE_LOAD_QUANTITY', ref, resource=rid)
                calendar = calendars.get(r['calendar_id'])
                if calendar is None:
                    self.missing('source.calendar:' + r['calendar_id']);continue
                intervals = [(self.at(i['start_at']),self.at(i['end_at'])) for i in calendar['available_intervals']]
                # A native processing plan may bridge a calendar break. Its
                # start/end must be available and its required work must fit;
                # the unavailable gap is not counted as processing or overtime.
                processing = quantity(spec.get('fixed_seconds') or 0)
                if spec['duration_model'] != 'FIXED_BATCH':
                    processing += quantity(spec.get('seconds_per_unit') or 0) * quantity(row['quantity'])
                self.check(any(a <= start < b for a,b in intervals) and any(a < end <= b for a,b in intervals) and
                           working_seconds(start,end,intervals) + float(EPS) >= float(processing),
                           'RESOURCE_OFF_CALENDAR', ref, resource=rid)
                if not bucket:
                    resource_events[rid].extend([(ls, 1, units), (le, 0, -units)])
                    for when, amount in [(ls, units), (le, -units)]:
                        expected_native[(ref, rid, ls, le, when, numeric(amount))] += 1
            if spec['duration_model'] != 'FIXED_BATCH':
                work = quantity(spec.get('fixed_seconds') or 0) + quantity(spec.get('seconds_per_unit') or 0) * quantity(row['quantity'])
                self.check(Decimal(str((end-start).total_seconds())) + EPS >= work, 'PROCESSING_TIME_TOO_SHORT', ref)
        for rid, events in resource_events.items():
            resource = machines.get(rid, people.get(rid)); used = Decimal(0)
            limit = quantity(resource.get('capacity', resource.get('parallel_units', 1)))
            for _, _, change in sorted(events):
                used += change
                self.check(-EPS <= used <= limit + EPS, 'CONTINUOUS_RESOURCE_CAPACITY', rid)
        post = self.capture('post', 'resources')
        if isinstance(post, list):
            actual_native = Counter()
            for r in post:
                rid = r['name']; source = machines.get(rid, people.get(rid))
                if source is None or source.get('capacity_model') == 'WINDOW_QUANTITY':
                    continue
                if not isinstance(r.get('loadplans'), list):
                    self.missing('native_post.resources.' + rid + '.loadplans');continue
                for l in r['loadplans']:
                    if quantity(l['quantity']) != 0:
                        actual_native[(str(l['reference']), rid, self.at(l['start']), self.at(l['end']),
                                       self.at(l['at']), numeric(l['quantity']))] += 1
            if self.complete_orders():
                self.check(actual_native == expected_native, 'NATIVE_LOADPLAN_RECONCILIATION')
        # Canonical annual phase grants remain separate source facts. Native
        # 9.19 permits one interval per resource/skill; independently derive
        # their exact contiguous union, never fill an undeclared gap.
        native_qualifications = skills + qualifications
        if self.meta.get('policy', {}).get('year_policy', {}).get('id') == 'APS-SIM-YEAR-01-POLICY-V1':
            grouped = defaultdict(list)
            for grant in native_qualifications:
                grouped[(grant['resource_id'], grant['skill_id'])].append(grant)
            native_qualifications = []
            for (resource, skill), grants in grouped.items():
                ordered = sorted(grants, key=lambda q: (self.at(q['effective_from']), self.at(q['effective_to'])))
                begin, finish = self.at(ordered[0]['effective_from']), self.at(ordered[0]['effective_to'])
                for grant in ordered[1:]:
                    self.check(self.at(grant['effective_from']) <= finish, 'YEAR_NATIVE_QUALIFICATION_GAP', resource, skill_id=skill)
                    finish = max(finish, self.at(grant['effective_to']))
                native_qualifications.append(dict(resource_id=resource, skill_id=skill,
                    effective_from=begin.isoformat(), effective_to=finish.isoformat()))
        # Check the actual native skills and calendars, not only the source claim.
        for phase in ('pre', 'post'):
            model = self.capture(phase, 'qualified_model')
            if not isinstance(model, dict):
                continue
            native_skills = table(model.get('resource_skills'))
            if native_skills is None:
                self.missing('native_' + phase + '.qualified_model.resource_skills');continue
            for q in native_qualifications:
                matches = [x for x in native_skills if (x.get('resource'), x.get('skill')) == (q['resource_id'], q['skill_id'])]
                self.check(len(matches) == 1 and self.at(matches[0]['effective_start']) == self.at(q['effective_from']) and
                           self.at(matches[0]['effective_end']) == self.at(q['effective_to']), 'NATIVE_QUALIFICATION_CHANGED',
                           q['resource_id'], phase=phase)
            native_resources = table(model.get('resources'))
            native_calendars = self.capture(phase,'calendars')
            if native_resources is None or not isinstance(native_calendars,list):
                self.missing('native_' + phase + '.qualified_model.resources_and_calendars');continue
            for rid,r in {**machines,**people}.items():
                native = next((v for v in native_resources if v.get('name') == rid),None)
                self.check(native is not None, 'NATIVE_RESOURCE_MISSING',rid,phase=phase)
                if native is None:
                    continue
                if r.get('capacity_model') == 'WINDOW_QUANTITY':
                    continue  # one-second quantity windows have a separate audit
                self.check(quantity(native['maximum']) == quantity(r.get('capacity',r.get('parallel_units',1))),
                           'NATIVE_RESOURCE_CAPACITY_CHANGED',rid,phase=phase)
                original_calendar = calendars.get(r['calendar_id'])
                if original_calendar is None:
                    self.missing('native_' + phase + '.resource.' + rid + '.availability_calendar');continue
                matched=self.effective_calendar_matches(native,original_calendar,native_calendars,
                    quantity(r.get('capacity',r.get('parallel_units',1))),'native_'+phase+'.resource.'+rid)
                if matched is not None:
                    self.check(matched,'NATIVE_RESOURCE_CALENDAR_CHANGED',rid,phase=phase)

    def materials(self):
        flows = self.rows('operationplanmaterial')
        operations = {o['id']: o for o in self.facts('operations')}
        recipes = {r['id']: r for r in self.facts('recipes')}
        boms = self.facts('bom_lines')
        mapping = self.meta.get('native_operation_map', {})
        expected, actual = defaultdict(list), defaultdict(list)
        uncertain = set()
        if flows is not None:
            for row in self.leaf_rows():
                ref = str(row['reference']); identity = mapping[row['operation']]
                op = operations[identity['operation_id']]; recipe = recipes[identity['recipe_id']]
                qty = quantity(row['quantity']); site = recipe['location_id']
                processing_start = self.at(row['startdate'])
                inputs = [line for line in boms if line['recipe_id'] == recipe['id'] and
                          line['consume_operation_id'] == op['id']]
                if op['id'] in self.meta.get('operation_setups', {}) and any(line['consume_at'] == 'START' for line in inputs):
                    plans = self.capture('post', 'operationplans')
                    matches = [p for p in plans or [] if str(p.get('reference')) == ref] if isinstance(plans, list) else []
                    if len(matches) != 1 or matches[0].get('setup_end') is None:
                        self.missing('native_post.operationplans.' + ref + '.setup_end')
                        uncertain.add(ref);continue
                    native = matches[0]
                    same = (native.get('operation') == row['operation'] and
                            quantity(native['quantity']) == qty and
                            self.at(native['start']) == self.at(row['startdate']) and
                            self.at(native['end']) == self.at(row['enddate']))
                    self.check(same, 'NATIVE_ORDER_CONFLICT', ref, phase='post', field='material_timing')
                    # Pinned FlowStart consumes at getSetupEnd(), after setup;
                    # it does not consume at the resource envelope's start.
                    processing_start = self.at(native['setup_end'])
                    self.check(self.at(row['startdate']) <= processing_start <= self.at(row['enddate']),
                               'SETUP_TIME_RANGE', ref)
                for line in boms:
                    if line['recipe_id'] != recipe['id'] or line['consume_operation_id'] != op['id']:
                        continue
                    when = processing_start if line['consume_at'] == 'START' else self.at(row['enddate'])
                    used = -qty * quantity(line['quantity_per_output']) / (1 - quantity(line['scrap_fraction']))
                    expected[(ref, line['input_item_id'], site, when)].append(used)
                if op.get('produces_output') is True:
                    expected[(ref, recipe['output_item_id'], site, self.at(row['enddate']))].append(qty)
            leaves = {str(r['reference']) for r in self.leaf_rows()}
            purchases,deliveries = self.rows('purchaseorder'),self.rows('deliveryorder')
            known = leaves | {str(p['reference']) for p in purchases or []} | {str(d['reference']) for d in deliveries or []}
            if self.data.get('distribution_lanes'):
                dos=self.rows('distributionorder')
                known|={str(d['reference']) for d in dos or []}
            known|={'QC:'+r['receipt_reference'] for r in self.data.get('purchase_receipts',[]) if r['quality_status']=='PENDING_QC' and r['release_at'] is not None}
            for flow in flows:
                if self.at(flow['flowdate']) >= self.at(self.data['planning_at']) and self.complete_orders() and purchases is not None and deliveries is not None:
                    self.check(str(flow['operationplan']) in known, 'UNCLASSIFIED_MATERIAL_EVENT',str(flow['operationplan']))
                if str(flow['operationplan']) in leaves - uncertain and quantity(flow['quantity']) != 0:
                    actual[(str(flow['operationplan']), flow['item'], flow['location'],self.at(flow['flowdate']))].append(quantity(flow['quantity']))
            same = expected.keys() == actual.keys() and all(len(expected[k]) == len(actual[k]) and
                    all(abs(a-b) <= EPS for a,b in zip(sorted(expected[k]), sorted(actual[k]))) for k in expected)
            self.check(same, 'BOM_NATIVE_EVENT_RECONCILIATION')
            for delivery in deliveries or []:
                ref=str(delivery['reference'])
                observed=[f for f in flows if str(f['operationplan']) == ref and quantity(f['quantity']) != 0]
                self.check(len(observed) == 1 and all((f['item'],f['location']) ==
                           (delivery['item'],delivery['location']) and self.at(f['flowdate']) == self.at(delivery['enddate']) and
                           abs(quantity(f['quantity']) + quantity(delivery['quantity'])) <= EPS for f in observed),
                           'DELIVERY_MATERIAL_EVENT',ref)
        buffers = self.capture('post', 'buffers')
        if not isinstance(buffers, list):
            return
        openings = defaultdict(Decimal)
        reachable=({r['location_id'] for r in self.data.get('inventory_positions',[])} if self.data.get('distribution_lanes') else {self.data.get('site',{}).get('id')})
        for r in self.facts('inventory_positions'):
            if r.get('location_id') not in reachable:
                continue
            if r.get('net_available') is None:
                self.missing('opening.' + r['id'] + '.net_available');continue
            self.check(quantity(r['physical']) - quantity(r['quality_blocked']) - quantity(r['other_unavailable']) ==
                       quantity(r['net_available']), 'OPENING_STOCK_ACCOUNTING', r['id'])
            openings[(r['item_id'],r['location_id'])] += quantity(r['net_available'])
        for r in self.data.get('purchase_receipts',[]):
            if r['quality_status']=='RELEASED':openings[(r['item_id'],r['location_id'])]+=quantity(r['quantity'])
        self.check(set(openings) <= {(b['item'],b['location']) for b in buffers}, 'NATIVE_BUFFER_SCOPE')
        for buffer in buffers:
            key = (buffer['item'],buffer['location']); balance = openings[key]
            self.check(abs(quantity(buffer['onhand']) - balance) <= EPS, 'NATIVE_OPENING_BALANCE', str(key))
            if not isinstance(buffer.get('events'), list):
                self.missing('native_post.buffers.' + str(key) + '.events');continue
            previous = None
            # Preserve the native iterator order at equal timestamps. The 1971
            # inventory opening and pre-as_of history are not replayed at T0.
            for index,event in enumerate(buffer['events']):
                when = self.at(event['at'])
                if previous is not None:
                    self.check(when >= previous, 'NATIVE_EVENT_ORDER', str(key), index=index)
                previous = when
                if when < self.at(self.data['planning_at']):
                    continue
                balance += quantity(event['quantity'])
                self.check(balance >= -EPS, 'INVENTORY_NEGATIVE', str(key), index=index,
                           event_reference=str(event.get('reference')))
                self.check(abs(balance - quantity(event['onhand'])) <= EPS, 'INVENTORY_REPORTED_BALANCE',
                           str(key), index=index)
            if flows is not None:
                api_events = [f for f in flows if (f['item'],f['location']) == key]
                self.buffer_api_balances(key,buffer['events'],api_events)
        histories = {h['id'] for h in self.facts('historical_manufacturing_orders') if h.get('included_in_opening')}
        self.check(not histories.intersection(self.operation_rows()), 'HISTORICAL_MO_REPLAYED')

    def buffer_api_balances(self, key, native_events, api_events):
        """Match event-after balances without replaying API order or openings.

        Native iterator order owns same-time accounting above. API export order
        can differ; identity/time/quantity match the events, including captured
        openings and zero-quantity records. Equal identities retain multiplicity.
        """
        native, api = defaultdict(list),defaultdict(list)
        for event in native_events:
            identity=(str(event['reference']),self.at(event['at']),numeric(event['quantity']))
            native[identity].append(event.get('onhand'))
        for event in api_events:
            identity=(str(event['operationplan']),self.at(event['flowdate']),numeric(event['quantity']))
            api[identity].append(event.get('onhand'))
        self.check(Counter({k:len(v) for k,v in api.items()}) == Counter({k:len(v) for k,v in native.items()}),
                   'NATIVE_BUFFER_API_RECONCILIATION',str(key))
        for identity in native.keys() & api.keys():
            left,right=native[identity],api[identity]
            if any(v is None for v in left):
                self.missing('native_post.buffers.'+str(key)+'.event.'+str(identity)+'.onhand')
            if any(v is None for v in right):
                self.missing('raw.operationplanmaterial.'+str(key)+'.event.'+str(identity)+'.onhand')
            if len(left) != len(right) or any(v is None for v in left+right):
                continue
            wanted,actual=sorted(quantity(v) for v in left),sorted(quantity(v) for v in right)
            self.check(all(abs(a-b) <= EPS for a,b in zip(wanted,actual)),
                       'NATIVE_BUFFER_API_ONHAND',str(key),event_reference=identity[0],
                       at=identity[1].isoformat(),quantity=identity[2],
                       native_onhand=[numeric(v) for v in wanted],api_onhand=[numeric(v) for v in actual])

    def setup(self):
        expected_initial = self.meta.get('resource_initial_setups')
        expected_loads = self.meta.get('operation_setups')
        expected_rules = self.meta.get('setup_transitions')
        if not isinstance(expected_initial, dict) or not isinstance(expected_loads, dict) or not isinstance(expected_rules, list):
            self.missing('foundation_projection.setup_contract');return
        mapping = self.meta.get('native_operation_map', {})
        source_operations = {o['id']: o for o in self.facts('operations')}
        source_resources = {r['id']: r for r in self.facts('resources') + self.facts('operators')}
        source_calendars = {c['id']: c for c in self.facts('calendars')}
        neutral_buckets=set();neutral_operations=set()
        if self.meta.get('policy',{}).get('profile')=='continuity-current-state-v1':
            neutral_buckets={r['id'] for r in self.facts('resources') if r.get('capacity_model')=='WINDOW_QUANTITY'}
            self.check(self.meta['policy'].get('setup_policy')=='NO_ADDITIONAL_SETUP_IN_DECLARED_SIMULATION_NEUTRAL_STATE'
                       and all(quantity(r['duration_seconds'])==0 and r['from_state']==r['to_state']=='CONT:NEUTRAL' for r in expected_rules)
                       and all(expected_initial.get(rid)=='CONT:NEUTRAL' for rid in neutral_buckets),
                       'CONTINUITY_BUCKET_SETUP_OMISSION_CONTRACT')
            neutral_operations={o['id'] for o in source_operations.values() if set(o['resource_ids'])<=neutral_buckets}
            expected_initial={k:v for k,v in expected_initial.items() if k not in neutral_buckets}
            expected_loads={k:v for k,v in expected_loads.items() if k not in neutral_operations}
        for phase in ('pre', 'post'):
            model = self.capture(phase, 'foundation_model')
            if not isinstance(model, dict):
                continue
            resources = table(model.get('resources')); matrices = table(model.get('setup_matrices')); loads = table(model.get('operation_loads'))
            for key,value in [('resources',resources),('setup_matrices',matrices),('operation_loads',loads)]:
                if value is None:
                    self.missing('raw.fixed_batch_' + phase + '.foundation_model.' + key)
            if resources is None or matrices is None or loads is None:
                continue
            for rid in neutral_buckets:
                actual=next((r for r in resources if r.get('name')==rid),None)
                self.check(actual is not None and not actual.get('setupmatrix') and not actual.get('setup'),
                           'CONTINUITY_NEUTRAL_BUCKET_HAS_NATIVE_SETUP',rid,phase=phase)
            for name,identity in mapping.items():
                if identity['operation_id'] in neutral_operations:
                    self.check(all(not r.get('setup') for r in loads if r.get('operation')==name),
                               'CONTINUITY_NEUTRAL_BUCKET_LOAD_HAS_NATIVE_SETUP',name,phase=phase)
            for rid, initial in expected_initial.items():
                actual = next((r for r in resources if r.get('name') == rid),None)
                self.check(actual is not None, 'NATIVE_SETUP_RESOURCE_MISSING', rid, phase=phase)
                if actual is None:
                    continue
                if phase == 'pre':
                    self.check(actual.get('setup') == initial, 'NATIVE_INITIAL_SETUP_CHANGED', rid)
                matrix = next((m for m in matrices if m.get('name') == actual.get('setupmatrix')),None)
                self.check(matrix is not None, 'NATIVE_SETUP_MATRIX_MISSING', rid, phase=phase)
                if matrix is None:
                    continue
                for rule in expected_rules:
                    matches = [r for r in matrix.get('rules',[]) if (r.get('from_state'),r.get('to_state')) ==
                               (rule['from_state'],rule['to_state'])]
                    self.check(len(matches) == 1 and quantity(matches[0]['duration_seconds']) == quantity(rule['duration_seconds']),
                               'NATIVE_SETUP_RULE_CHANGED', rid, phase=phase,
                               transition=rule['from_state'] + '->' + rule['to_state'])
            for name, identity in mapping.items():
                required = expected_loads.get(identity['operation_id'])
                if required is None:
                    continue
                matching = [l for l in loads if l.get('operation') == name and l.get('setup') == required]
                self.check(len(matching) == 1, 'NATIVE_OPERATION_SETUP_MISSING', name, phase=phase)
        if expected_loads:
            plans = self.capture('post', 'operationplans')
            if isinstance(plans, list):
                sequences = defaultdict(list)
                for row in plans:
                    identity = mapping.get(row.get('operation'),{})
                    if identity.get('operation_id') not in expected_loads:
                        continue
                    if row.get('setup_duration_seconds') is None:
                        self.missing('native_post.operationplans.' + str(row['reference']) + '.setup_duration_seconds')
                    assigned = row.get('resources')
                    if not isinstance(assigned,list):
                        self.missing('native_post.operationplans.' + str(row['reference']) + '.resources');continue
                    assigned = resource_names(assigned)
                    machines = [rid for rid in assigned if rid in expected_initial]
                    self.check(len(machines) == 1, 'SETUP_MACHINE_IDENTITY', str(row['reference']))
                    if machines:
                        sequences[machines[0]].append(row)
                for rid, rows in sequences.items():
                    current = expected_initial[rid]
                    for row in sorted(rows,key=lambda r:(self.at(r['start']),self.at(r['end']),str(r['reference']))):
                        required = expected_loads[mapping[row['operation']]['operation_id']]
                        matches = [r for r in expected_rules if (r['from_state'],r['to_state']) == (current,required)]
                        if len(matches) != 1:
                            self.missing('setup_transition.' + current + '->' + required)
                        elif row.get('setup_duration_seconds') is not None:
                            duration = quantity(row['setup_duration_seconds'])
                            # Native getter -1 is the no-SetupEvent sentinel,
                            # only valid for a zero rule and an empty interval.
                            if duration == -1 and quantity(matches[0]['duration_seconds']) == 0 and all(
                                row.get(k) is not None and self.at(row[k]) == self.at(row['start'])
                                for k in ('setup_start', 'setup_end')):
                                duration = Decimal(0)
                            self.check(abs(duration - quantity(matches[0]['duration_seconds'])) <= EPS,
                                       'SETUP_DURATION_NOT_APPLIED', str(row['reference']), resource=rid,
                                       transition=current+'->'+required)
                            self.setup_work(row, duration, source_operations[mapping[row['operation']]['operation_id']],
                                            source_resources, source_calendars)
                        current = required

    def setup_work(self, row, duration, operation, resources, calendars):
        ref = str(row['reference'])
        if row.get('setup_start') is None or row.get('setup_end') is None:
            self.missing('native_post.operationplans.' + ref + '.setup_interval');return
        start, setup_start, setup_end, end = (self.at(row[k]) for k in ('start','setup_start','setup_end','end'))
        valid = start == setup_start <= setup_end <= end and (setup_start < setup_end if duration > 0 else setup_start == setup_end)
        self.check(valid, 'SETUP_TIME_RANGE', ref)
        if not valid:
            return
        shared = [(start,end)]
        for rid in resource_names(row['resources']):
            resource = resources.get(rid)
            calendar = calendars.get(resource.get('calendar_id')) if resource else None
            if calendar is None:
                self.missing('source.setup_calendar.' + str(rid));return
            intervals = [(self.at(i['start_at']),self.at(i['end_at'])) for i in calendar['available_intervals']]
            shared = [(max(a,x),min(b,y)) for a,b in shared for x,y in intervals if max(a,x) < min(b,y)]
        setup_seconds = quantity(working_seconds(setup_start,setup_end,shared))
        work = quantity(operation.get('fixed_seconds') or 0)
        if operation['duration_model'] != 'FIXED_BATCH':
            work += quantity(operation.get('seconds_per_unit') or 0) * quantity(row['quantity'])
        actual_work = quantity(working_seconds(setup_end,end,shared))
        self.check(abs(setup_seconds-duration) <= EPS, 'SETUP_WORK_SECONDS', ref,
                   expected=numeric(duration), observed=numeric(setup_seconds))
        self.check(abs(actual_work-work) <= EPS, 'PROCESSING_WORK_SECONDS', ref,
                   expected=numeric(work), observed=numeric(actual_work))

    def soft_targets(self):
        targets = self.facts('inventory_targets')
        self.current['policy'] = 'Soft stock objectives are reported separately; below-target is not a hard violation.'
        for target in targets:
            if target.get('hard_limit') is not False:
                self.missing('inventory_target.' + target['id'] + '.hard_target_native_evidence')
        receipt=self.meta.get('professional_overlay')
        if receipt:
            from inventory_decision import audit_policy
            selected=receipt['changes'].get('replenishment')
            policies=selected['rows'] if selected else []
            self.current['research_policies']=[]
            for policy in policies:
                result=audit_policy(self.data,self.raw,run_id=self.run_id,policy=policy)
                self.current['research_policies'].append(result)
                for issue in result['issues']:self.current['issues'].append(issue)
                for missing in result['missing_evidence']:self.missing(missing)


def audit_rebinding_receipts(data, post):
    """Independent before/after audit of a bounded exchange of two proposals.

    No resolver imports. Actual captures corroborate both receipts; native
    resource, setup, material and protected-source audits remain mandatory.
    """
    issues=[];missing=[]
    before=table(post.get('protected_setup_native_before'));after=table(post.get('operationplans'))
    if before is None or after is None:
        return dict(status='NOT_CHECKED',issues=[],missing_evidence=['native before/after machine exchange capture'])
    source=data.get('foundation_projection',{}).get('source_tables',{})
    machines={r['id'] for r in source.get('resources',[])}
    people={r['id'] for r in source.get('operators',[])}
    mapping=data['foundation_projection'].get('native_operation_map',{})
    operations={r['id']:r for r in source.get('operations',[])}
    fields=('reference','operation','quantity','status','start','end','resources')
    def equivalent(a,b):
        return all(quantity(a[k])==quantity(b[k]) if k=='quantity' else set(resource_names(a[k]))==set(resource_names(b[k])) if k=='resources' else a[k]==b[k] for k in fields)
    for resolution in post.get('protected_setup_resolution',[]):
        if not resolution.get('exchange_before'):continue
        try:
            if resolution.get('method')!='EXISTING_TWO_PROPOSALS_MACHINE_EXCHANGE_THEN_SOURCE_START_RECALCULATION':raise ValueError('method')
            pairs=[(resolution['predecessor_before'],resolution['predecessor_after']),
                   (resolution['exchange_before'],resolution['exchange_after'])]
            old_machines=[];new_machines=[]
            for first,last in pairs:
                ref=first['reference'];bs=[r for r in before if r.get('reference')==ref];ps=[r for r in after if r.get('reference')==ref]
                if len(bs)!=1 or len(ps)!=1 or not equivalent(first,bs[0]) or not equivalent(last,ps[0]):raise ValueError('capture')
                if first['status']!='proposed' or last['status']!='proposed':raise ValueError('approval')
                if any(first[k]!=last[k] for k in ('reference','operation','quantity','status','start','end')):raise ValueError('native_window')
                old=set(resource_names(first['resources']));new=set(resource_names(last['resources']))
                if old- machines!=new-machines or len(old&people)!=1:raise ValueError('person')
                a,b=old&machines,new&machines
                if len(a)!=1 or len(b)!=1 or a==b:raise ValueError('machine_scope')
                operation=operations[mapping[first['operation']]['operation_id']]
                if not a|b<=set(operation['resource_ids']):raise ValueError('qualification_scope')
                old_machines.append(a);new_machines.append(b)
            if old_machines!=list(reversed(new_machines)):raise ValueError('not_exchange')
        except (KeyError,TypeError,ValueError,ArithmeticError) as error:
            issues.append(dict(code='NATIVE_MACHINE_EXCHANGE_NOT_CORROBORATED',reference=resolution.get('reference'),reason=str(error)))
    return dict(status='FAIL' if issues else 'PASS',issues=issues,missing_evidence=missing)


def audit(data, raw, run_id=None):
    """Return combined PASS/FAIL/NOT_CHECKED without altering either argument."""
    check = _Audit(data, raw, run_id)
    for name, method in [('demand_scope',check.source_scope),('native_identity',check.native_identity),('routing',check.routings),
                         ('demand_delivery',check.demand_results),('native_operation_model',check.operation_models),
                         ('protected_manufacturing',check.protected_manufacturing),
                         ('procurement',check.purchases),('fixed_batch_windows',check.fixed_batches),
                         ('resources',check.resources),('material_balance',check.materials),
                         ('setup',check.setup),('soft_targets',check.soft_targets)]:
        check.run(name,method)
    if data.get('purchase_receipts') or data.get('distribution_lanes'):
        check.checks['joint_supply']=audit_joint_supply(data,raw,run_id)
    if data.get('foundation_projection',{}).get('policy',{}).get('profile')=='continuity-current-state-v1':
        from p22.continuity_audit import audit as audit_continuity
        check.checks['continuity']=audit_continuity(data,raw,run_id)
    issues = [dict(check=name,**issue) for name,result in check.checks.items() for issue in result['issues']]
    missing = sorted({value for result in check.checks.values() for value in result['missing_evidence']})
    return dict(status=status(issues,missing),scope='combined_enterprise_native_business_audit',
                run_id=run_id,simulation_only=True,execution_authorized=False,
                issues=issues,missing_evidence=missing,checks=check.checks,
                limits=['This audits planning evidence, never physical execution or production authorization.',
                        'Research setup overlays remain assumptions, not frozen enterprise facts.',
                        'A missing capture is not supplied from projected input or old profile certification.'])


audit_native = audit


def audit_joint_supply(data,raw,run_id=None):
    try:
        return _audit_joint_supply(data,raw,run_id)
    except KeyError as error:
        return dict(status='NOT_CHECKED',issues=[],missing_evidence=['joint.required_field:'+str(error)],
            scope='same_run_receipt_qc_distribution_conservation',run_id=run_id,execution_authorized=False)
    except (TypeError,ValueError,ArithmeticError) as error:
        return dict(status='FAIL',issues=[dict(code='MALFORMED_JOINT_EVIDENCE',reason=str(error))],missing_evidence=[],
            scope='same_run_receipt_qc_distribution_conservation',run_id=run_id,execution_authorized=False)


def _audit_joint_supply(data,raw,run_id=None):
    """Corroborate receipt availability and two-ended DOs with native captures."""
    issues=[];missing=[];zone=ZoneInfo(data['site']['timezone'])
    def at(v):
        v=datetime.fromisoformat(str(v).replace('Z','+00:00'))
        return v.replace(tzinfo=zone) if v.tzinfo is None else v.astimezone(zone)
    def ensure(ok,code,ref=None):
        if not ok:issues.append(dict(code=code,reference=ref))
    def rows(name):
        value=table(raw.get(name))
        if value is None:missing.append('raw.'+name)
        return value
    source=data['foundation_projection']['source_tables']
    # Frozen facts remain the authority. Native-vs-projected agreement alone
    # must not certify a changed quality cohort or route.
    for name,code,numerics in [('purchase_receipts','JOINT_RECEIPT_SOURCE_MISMATCH',{'quantity'}),
            ('distribution_lanes','JOINT_LANE_SOURCE_MISMATCH',{'minimum_transfer_quantity','transfer_multiple'})]:
        originals=source.get(name,[]);projected=data.get(name,[])
        ensure(len(originals)==len(projected),code)
        delta=data['foundation_projection'].get('professional_overlay',{}).get('changes',{}).get('supply',{}).get('distribution') if name=='distribution_lanes' else None
        for original in originals:
            matches=[r for r in projected if r['id']==original['id']]
            ensure(len(matches)==1,code,original['id'])
            if len(matches)!=1:continue
            expected=dict(original)
            if delta and delta['reference']==original['id']:
                ensure(delta['before']=={'lead_time_seconds':original['lead_time_seconds']} and
                    set(delta['after'])=={'lead_time_seconds'} and type(delta['after']['lead_time_seconds']) is int and
                    delta['after']['lead_time_seconds']>original['lead_time_seconds'],code,original['id'])
                expected.update(delta['after'])
            ensure(all(quantity(matches[0][k])==quantity(v) if k in numerics else matches[0][k]==v
                for k,v in expected.items()),code,original['id'])
    flows=rows('operationplanmaterial');mos=rows('manufacturingorder')
    dos=rows('distributionorder') if data.get('distribution_lanes') else []
    if dos is not None:
        refs=[str(d['reference']) for d in dos]
        ensure(len(refs)==len(set(refs)),'JOINT_DUPLICATE_DO')
    receipt_refs={'QC:'+r['receipt_reference']:r for r in data.get('purchase_receipts',[])}
    for ref,r in receipt_refs.items():
        expected=r['quality_status']=='PENDING_QC' and r['release_at'] is not None
        records=[m for m in mos or [] if str(m['reference'])==ref]
        fs=[f for f in flows or [] if str(f['operationplan'])==ref and quantity(f['quantity'])!=0]
        if mos is not None:ensure(len(records)==(1 if expected else 0),'RECEIPT_RELEASE_ORDER',ref)
        if flows is not None:
            ensure((len(fs)==1 and quantity(fs[0]['quantity'])==quantity(r['quantity']) and
                (fs[0]['item'],fs[0]['location'])==(r['item_id'],r['location_id']) and at(fs[0]['flowdate'])==at(r['release_at'])) if expected else not fs,'RECEIPT_RELEASE_EVENT',ref)
    lanes={(r['item_id'],r['origin_location_id'],r['destination_location_id']):r for r in data.get('distribution_lanes',[])}
    for phase in ('pre','post'):
        snap=raw.get('fixed_batch_'+phase)
        if not isinstance(snap,dict):missing.append('native_'+phase);continue
        ensure(snap.get('run_id')==run_id,'JOINT_NATIVE_RUN_MISMATCH',phase)
        buffers=table(snap.get('buffers'))
        if buffers is None:missing.append('native_'+phase+'.buffers')
        else:
            from multi_receipt_supply import receipt_openings
            from inventory_availability import net_available
            opening=receipt_openings(data)
            for stock in data['inventory_positions']:
                key=stock['item_id'],stock['location_id'];bs=[b for b in buffers if (b['item'],b['location'])==key]
                ensure(len(bs)==1 and quantity(bs[0]['onhand'])==net_available(stock)+opening[key],'RECEIPT_OPENING_RECONCILIATION',str(key))
        if lanes:
            model=snap.get('distribution_model',{});routes=table(model.get('routes'))
            if routes is None:missing.append('native_'+phase+'.distribution_model.routes');continue
            ensure(len(routes)==len(lanes),'JOINT_NATIVE_LANE_COUNT',phase)
            for key,lane in lanes.items():
                rs=[r for r in routes if (r['item'],r['origin'],r['destination'])==key]
                ensure(len(rs)==1,'JOINT_NATIVE_LANE_MISSING',lane['id'])
                if len(rs)==1:
                    r=rs[0]
                    ensure(quantity(r['leadtime'])==quantity(lane['lead_time_seconds']) and quantity(r['size_minimum'])==quantity(lane['minimum_transfer_quantity']) and quantity(r['size_multiple'])==quantity(lane['transfer_multiple']) and at(r['effective_start'])==at(lane['effective_from']) and at(r['effective_end'])==at(lane['effective_to']) and r.get('source')==lane['id'],'JOINT_NATIVE_LANE_TERMS',lane['id'])
    if lanes:
        native_orders=table((raw.get('fixed_batch_post') or {}).get('distribution_model',{}).get('orders'))
        if native_orders is None:missing.append('native_post.distribution_model.orders')
        elif dos is not None:
            native=Counter((str(r['reference']),r['item'],r['origin'],r['location'],quantity(r['quantity']),at(r['start']),at(r['end']),r['status']) for r in native_orders if r['order_type']=='DO')
            api=Counter((str(r['reference']),r['item'],r['origin'],r['destination'],quantity(r['quantity']),at(r['startdate']),at(r['enddate']),r['status']) for r in dos)
            ensure(native==api,'JOINT_NATIVE_DO_READBACK')
    for d in dos or []:
        ref=str(d['reference']);key=d['item'],d['origin'],d['destination'];lane=lanes.get(key)
        ensure(lane is not None,'JOINT_UNKNOWN_DO_LANE',ref)
        if lane is None:continue
        start,end=at(d['startdate']),at(d['enddate']);q=quantity(d['quantity'])
        ensure(d['status']=='proposed' and q>0 and q>=quantity(lane['minimum_transfer_quantity']) and q%quantity(lane['transfer_multiple'])==0,'JOINT_DO_STATUS_OR_BATCH',ref)
        ensure(at(data['planning_at'])<=start<end<=at(data['horizon']['end_at']) and (end-start).total_seconds()==quantity(lane['lead_time_seconds']),'JOINT_DO_LEADTIME',ref)
        ensure(at(lane['effective_from'])<=start<end<=at(lane['effective_to']),'JOINT_DO_EFFECTIVITY',ref)
        if flows is not None:
            expected=Counter([(key[0],key[1],start,-q),(key[0],key[2],end,q)])
            actual=Counter((f['item'],f['location'],at(f['flowdate']),quantity(f['quantity'])) for f in flows if str(f['operationplan'])==ref and quantity(f['quantity'])!=0)
            ensure(actual==expected,'JOINT_DO_TWO_ENDED_CONSERVATION',ref)
    return dict(status=status(issues,missing),issues=issues,missing_evidence=sorted(set(missing)),scope='same_run_receipt_qc_distribution_conservation',
        run_id=run_id,receipt_count=len(receipt_refs),distribution_order_count=len(dos) if dos is not None else None,execution_authorized=False)
