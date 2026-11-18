"""Versioned field projection into existing Canonical domains, never policies.

Only declarative, bounded transformations are supported. Unresolved values are
business review issues; they are not guessed, dropped or converted to zero.
"""
import copy
from datetime import datetime
from decimal import Decimal,localcontext
from zoneinfo import ZoneInfo

from p21.canonical import DOMAIN, digest


def escape(value):return str(value).replace('~','~0').replace('/','~1')


def enterprise_identity(source,mapping):
    aliases=(mapping or {}).get('enterprise_ids')
    if aliases is None:return source
    if (not isinstance(aliases,dict) or not aliases or source not in aliases or
        any(not isinstance(k,str) or not k.strip() or not isinstance(v,str) or not v.strip() for k,v in aliases.items())):
        raise ValueError('ENTERPRISE_MAPPING_UNRESOLVED')
    return aliases[source]


def pointer(record,path):
    if not isinstance(path,str) or not path.startswith('/') or len(path)>2048:
        raise ValueError('MAPPING_POINTER_REQUIRED')
    value=record
    for part in path[1:].split('/'):
        key=part.replace('~1','/').replace('~0','~')
        value=value[int(key)] if isinstance(value,list) else value[key]
    return copy.deepcopy(value)


def transform(row,rule):
    allowed={'path','constant','cast','prefix','values','scale','timezone','format'}
    if not isinstance(rule,dict) or set(rule)-allowed or ('path' in rule)==('constant' in rule):
        raise ValueError('MAPPING_RULE_INVALID')
    value=copy.deepcopy(rule['constant']) if 'constant' in rule else pointer(row,rule['path'])
    # An explicitly unknown source value remains unknown.
    if value is None:return None
    if 'values' in rule:
        if not isinstance(rule['values'],dict) or str(value) not in rule['values']:raise ValueError('MAPPING_ENUM_UNRESOLVED')
        value=copy.deepcopy(rule['values'][str(value)])
    cast=rule.get('cast')
    if cast not in {None,'string','decimal','integer','datetime','boolean'}:raise ValueError('MAPPING_CAST_UNSUPPORTED')
    if cast=='string':
        if isinstance(value,(list,dict)):raise ValueError('MAPPING_SCALAR_REQUIRED')
        value=str(value)
    if cast in {'decimal','integer'} or 'scale' in rule:
        if isinstance(value,bool):raise ValueError('MAPPING_QUANTITY_INVALID')
        source=Decimal(str(value));scale=Decimal(str(rule.get('scale',1)))
        if any(not q.is_finite() or len(q.as_tuple().digits)>512 or abs(q.as_tuple().exponent)>1000 for q in (source,scale)):
            raise ValueError('MAPPING_QUANTITY_PRECISION_LIMIT')
        with localcontext() as context:
            context.prec=len(source.as_tuple().digits)+len(scale.as_tuple().digits)+2
            number=source*scale
        if not number.is_finite() or (cast=='integer' and number!=number.to_integral_value()):raise ValueError('MAPPING_QUANTITY_INVALID')
        # Canonical Quantity accepts an exact decimal string. Never round a
        # source amount through float (or through Decimal's default context).
        value=int(number) if number==number.to_integral_value() else format(number,'f')
    if cast=='boolean':
        if type(value) is not bool:raise ValueError('MAPPING_BOOLEAN_REQUIRES_EXPLICIT_ENUM')
    if cast=='datetime':
        if not isinstance(value,str):raise ValueError('MAPPING_TIMESTAMP_TEXT_REQUIRED')
        parsed=datetime.strptime(value,rule['format']) if rule.get('format') else datetime.fromisoformat(value.replace('Z','+00:00'))
        if parsed.tzinfo is None:
            if not rule.get('timezone'):raise ValueError('MAPPING_TIMEZONE_REQUIRED')
            zone=ZoneInfo(rule['timezone'])
            a,b=parsed.replace(tzinfo=zone,fold=0),parsed.replace(tzinfo=zone,fold=1)
            if a.utcoffset()!=b.utcoffset():raise ValueError('MAPPING_LOCAL_TIME_AMBIGUOUS')
            parsed=a
        value=parsed.isoformat()
    if 'prefix' in rule:
        if not isinstance(value,str) or not isinstance(rule['prefix'],str):raise ValueError('MAPPING_ID_INVALID')
        value=rule['prefix']+value
    return value



# Versioned, source-neutral field rules. A template supplies only paths already
# supported by transform(); differences must be explicit overrides. In particular
# total/received/open/physical quantities are separate required business fields.
TEMPLATES = {
    'sales-orders/1': ('sales_orders', 'id reference customer_id status'),
    'demand-lines/1': ('demand_lines', 'id customer_id order_ref item_id location_id quantity uom due_at priority minimum_shipment maximum_lateness_seconds status'),
    'purchase-orders/1': ('purchase_orders', 'id reference item_id location_id source_id quantity received_quantity cancelled_quantity unreceived_quantity uom physical_receipt_at available_at status included_in_opening'),
    'purchase-receipts/1': ('purchase_receipts', 'id po_id receipt_reference item_id location_id quantity uom physical_receipt_at quality_status release_at inventory_includes_receipt'),
    'inventory-positions/1': ('inventory_positions', 'id item_id location_id physical quality_blocked other_unavailable net_available uom as_of knowledge'),
    'manufacturing-orders/1': ('existing_manufacturing_orders', 'id recipe_id operation_id resource_id quantity completed_quantity start_at end_at status protected'),
    'resources/1': ('resources', 'id location_id calendar_id role capacity_model parallel_units initial_setup'),
    'resource-skills/1': ('resource_skills', 'id resource_id skill_id effective_from effective_to'),
}


def template_fields(spec):
    name=spec.get('template')
    if name is None:return spec.get('fields')
    if not isinstance(name,str) or name not in TEMPLATES:raise ValueError('MAPPING_TEMPLATE_NOT_FOUND')
    domain,names=TEMPLATES[name]
    if spec.get('domain')!=domain:raise ValueError('MAPPING_TEMPLATE_DOMAIN_MISMATCH')
    overrides=spec.get('fields',{})
    if not isinstance(overrides,dict):raise ValueError('MAPPING_FIELDS_INVALID')
    return {**{key:{'path':'/'+key} for key in names.split()},**copy.deepcopy(overrides)}


def project(tables,mapping,sha):
    """Return typed tables + raw JSON pointers. No IO, state commit or solve."""
    if mapping is None:
        mapping={'schema':'business-record-mapping/1','id':'CANONICAL_FIELDS','version':'1',
            'tables':{k:{'domain':k} for k in tables}}
    if (not isinstance(mapping,dict) or not {'schema','id','version','tables'}<=set(mapping) or
        set(mapping)-{'schema','id','version','tables','enterprise_ids'} or
        mapping['schema']!='business-record-mapping/1' or
        any(not isinstance(mapping[k],str) or not mapping[k].strip() or len(mapping[k])>200 for k in ('id','version')) or
        not isinstance(mapping['tables'],dict) or set(mapping['tables'])!=set(tables)):
        raise ValueError('MAPPING_SCHEMA_INVALID')
    output={};refs={};issues=[]
    for source,rows in tables.items():
        spec=mapping['tables'][source]
        if (not isinstance(spec,dict) or set(spec)-{'domain','fields','template'} or spec.get('domain') not in DOMAIN or spec['domain'] in output):
            raise ValueError('INVALID_DOMAIN_MAPPING')
        kind=spec['domain'];output[kind]=[];refs[kind]=[]
        fields=template_fields(spec)
        if fields is not None and (not isinstance(fields,dict) or len(fields)>512):raise ValueError('MAPPING_FIELDS_INVALID')
        for n,row in enumerate(rows):
            base='/tables/'+escape(source)+'/'+str(n)
            locations={};value={}
            if fields is None:
                value=copy.deepcopy(row);locations={k:base+'/'+escape(k) for k in row}
            else:
                for target,rule in fields.items():
                    try:
                        value[target]=transform(row,rule)
                        locations[target]=base+rule['path'] if 'path' in rule else 'mapping:/tables/'+escape(source)+'/fields/'+escape(target)+'/constant'
                    except (KeyError,ValueError,TypeError,IndexError,ArithmeticError) as exc:
                        issues.append(dict(code='MAPPING_FIELD_UNRESOLVED',table=kind,row=n+1,field=target,
                            source_pointer=base,reason=str(exc).split(':')[0][:120],severity='ERROR',
                            requires_confirmation=True,requires_source=True))
            output[kind].append(value)
            refs[kind].append(dict(file_sha256=sha,sheet=source,row=n+1,pointer=base,fields=locations,
                mapping_id=mapping['id'],mapping_version=mapping['version'],mapping_sha256=digest(mapping)))
    return output,refs,issues,mapping
