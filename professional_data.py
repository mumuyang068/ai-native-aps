"""Read-only adapter for an explicitly configured, hash-pinned data release."""
import hashlib,json,os,types,re
from pathlib import Path
from fastapi import HTTPException,Request
from professional_calendar import project_calendar,horizon,attributes

RELEASE='APS-PRO-DATA-V1.0.0'
MANIFEST_SHA='9470f061313fa77d593737c2e5d4b79251b20c4bf0a7e19b7f008f57cf4b931d'
ALLOWED={
 'FREPPLE-MFG-9.19.0':('items','operations','operation_materials','operation_resources','resources','resource_skills','skills','calendars','calendar_buckets','locations','buffers','item_suppliers','suppliers','item_distributions','operation_plans'),
 'SIM-GARMENT-01-V1':('items','recipes','bom_lines','routings','operations','resources','operators','resource_skills','machine_qualifications','skills','calendars','capacity_windows','inventory_positions','locations','supply_sources','suppliers','purchase_orders')}

class AssetAccessError(ValueError):
 def __init__(self,code):self.code=code;super().__init__(code)

class ProfessionalAssets:
 def __init__(self,root):
  if not root:raise AssetAccessError('DATA_RELEASE_NOT_CONFIGURED')
  self.root=Path(root).resolve()
  try:
   raw=(self.root/'release-manifest.json').read_bytes()
   if hashlib.sha256(raw).hexdigest()!=MANIFEST_SHA:raise AssetAccessError('MANIFEST_HASH_MISMATCH')
   self.manifest=json.loads(raw)
   if self.manifest['release_id']!=RELEASE or self.manifest['status']!='FROZEN':raise AssetAccessError('RELEASE_NOT_FROZEN')
   p=self.root/'tools/read_asset.py';entry=self.manifest['files']['tools/read_asset.py']
   if not p.resolve().is_relative_to(self.root):raise AssetAccessError('OUTSIDE_RELEASE')
   source=p.read_bytes()
   if hashlib.sha256(source).hexdigest()!=entry['sha256']:raise AssetAccessError('FILE_HASH_MISMATCH')
   # Reuse the exact verified release reader without importing a path or writing __pycache__ to frozen data.
   self.reader=types.ModuleType('verified_aps_release_reader')
   exec(compile(source,str(p),'exec'),self.reader.__dict__)
  except (OSError,KeyError,json.JSONDecodeError):raise AssetAccessError('FILE_UNREADABLE') from None
 def read(self,name):
  return json.loads(self.reader.checked(self.root,self.manifest,name).read_text('utf-8'))
 def dataset(self,ds,version):
  known=next((v for v in self.manifest['datasets'] if v['dataset_id']==ds),None)
  if not known:raise AssetAccessError('DATASET_NOT_FOUND')
  if ds not in ALLOWED:raise AssetAccessError('DATASET_NOT_SUPPORTED')
  if version!=known['dataset_version']:raise AssetAccessError('DATASET_VERSION_MISMATCH')
  meta=self.read(known['root']+'/manifest.json')
  return known,meta
 def catalog(self):
  rows=[]
  for d in self.manifest['datasets']:
   if d['dataset_id'] not in ALLOWED:continue
   _,meta=self.dataset(d['dataset_id'],d['dataset_version'])
   rows.append({'dataset_id':d['dataset_id'],'dataset_version':d['dataset_version'],'fact_kind':meta['fact_kind'],
    'time_semantics':meta['time_semantics'],'units':meta['units'],'object_types':list(ALLOWED[d['dataset_id']]),'normalized_tables':meta['normalized_tables']})
  return {'availability':'PRESENT','release_id':RELEASE,'manifest_sha256':MANIFEST_SHA,'datasets':rows,'readonly':True}
 def bundle(self,ds,version):
  d,meta=self.dataset(ds,version);tables={}
  for name in ALLOWED[ds]:
   rows=self.read(d['root']+'/normalized/'+name+'.json')
   if len(rows)!=meta['normalized_tables'][name] or len({v['entity_id'] for v in rows})!=len(rows) or any(v['dataset_id']!=ds for v in rows):raise AssetAccessError('TABLE_IDENTITY_MISMATCH')
   tables[name]=rows
  for name in meta['original_files']:self.reader.checked(self.root,self.manifest,d['root']+'/'+name)
  return {'release_id':RELEASE,'manifest_sha256':MANIFEST_SHA,'dataset':{k:v for k,v in meta.items() if k in ['dataset_id','version','fact_kind','time_semantics','units','known_gaps']},
   'tables':tables,'source_status':'PRESENT','readonly':True,'plan_context':None,'native_result':{'status':'NOT_AVAILABLE','collection_status':'NOT_COLLECTED','records':None}}
 def object(self,ds,version,typ,obj):
  self.dataset(ds,version)
  if typ not in ALLOWED[ds]:raise AssetAccessError('OBJECT_TYPE_NOT_SUPPORTED')
  return self.reader.lookup(self.root,ds,version,typ,obj)
 def calendar(self,ds,version,typ,obj,start,end):
  requested_start,requested_end=horizon(start,end)
  _,meta=self.dataset(ds,version)
  if ds=='SIM-GARMENT-01-V1':
   # The exported interval list is complete only inside its explicitly frozen horizon.
   match=re.search(r'horizon \[([^,]+),([^\)]+)\)',meta['time_semantics'])
   if not match:raise AssetAccessError('CALENDAR_HORIZON_NOT_CHECKED')
   if start<match[1][:10] or end>match[2][:10]:raise AssetAccessError('CALENDAR_OUTSIDE_SOURCE_HORIZON')
  row=self.object(ds,version,typ,obj)['record']
  if typ not in ('resources','operators'):raise AssetAccessError('OBJECT_TYPE_NOT_SUPPORTED')
  bundle=self.bundle(ds,version);t=bundle['tables'];synthetic=ds=='SIM-GARMENT-01-V1';a=attributes(row)
  ids=[a.get('maximum_calendar_id'),a.get('available_id'),a.get('calendar_id')]
  loc=next((v for v in t['locations'] if (attributes(v).get('id') if synthetic else attributes(v).get('name'))==a.get('location_id')),None)
  if loc:ids.append(attributes(loc).get('available_id'))
  calendars=[project_calendar(c,t.get('calendar_buckets',[]),start,end,synthetic) for c in t['calendars'] if attributes(c).get('name',attributes(c).get('id')) in ids]
  windows=[v for v in t.get('capacity_windows',[]) if v['resource_id']==a.get('id') and v['start_at'][:10]>=start and v['start_at'][:10]<end]
  missing=sorted(set(x for x in ids if x)-set(v['calendar_id'] for v in calendars))
  return {'dataset_id':ds,'dataset_version':version,'object_id':obj,'start':start,'end':end,
   'timezone':'Asia/Shanghai' if synthetic else 'UNSPECIFIED','capacity_model':a.get('capacity_model','DECLARED_RESOURCE_SIZE'),
   'declared_size':a.get('maximum',a.get('parallel_units',a.get('capacity'))),'calendars':calendars,'missing_calendar_ids':missing,
   'capacity_windows':windows,'is_actual_load':False,'combination':'Separate capacity/availability calendars; no utilization or assigned capacity calculated.'}

def register_professional_data(app):
 def access(request,allowed,action):
  if set(request.query_params)-set(allowed):raise HTTPException(422,detail={'code':'UNKNOWN_QUERY_FIELD','message':'专业数据查询含有不支持的字段。'})
  try:return action(ProfessionalAssets(os.environ.get('APS_PRO_DATA_ROOT')))
  except Exception as exc:
   code=getattr(exc,'code',str(exc) if isinstance(exc,ValueError) else 'ASSET_READ_ERROR')
   status=404 if code in ['DATASET_NOT_FOUND','OBJECT_NOT_FOUND'] else 422 if code in ['DATASET_NOT_SUPPORTED','DATASET_VERSION_MISMATCH','OBJECT_TYPE_NOT_SUPPORTED','INVALID_CALENDAR_RANGE','UNSUPPORTED_CALENDAR_OFFSET','CALENDAR_OUTSIDE_SOURCE_HORIZON'] else 503
   messages={'DATA_RELEASE_NOT_CONFIGURED':'尚未配置专业数据发布目录。','FILE_UNREADABLE':'数据发布文件不可读取，请联系管理员核对只读目录和文件完整性。','FILE_HASH_MISMATCH':'数据文件摘要不符，已停止读取，请核对固定发布版本。','DATASET_NOT_SUPPORTED':'本视图尚未接入该数据集。','OBJECT_TYPE_NOT_SUPPORTED':'当前数据集不支持此类对象查询。','INVALID_CALENDAR_RANGE':'请选择有效起始日期，查询范围不超过14天。','CALENDAR_OUTSIDE_SOURCE_HORIZON':'查询日期超出本数据发布的规划期，请在已声明范围内查看。','OUTSIDE_RELEASE':'文件超出已登记的发布范围，已拒绝读取。','MANIFEST_HASH_MISMATCH':'数据发布版本或摘要不符，请核对配置。','OBJECT_NOT_FOUND':'当前数据集中不存在该对象，请重新选择。','DATASET_VERSION_MISMATCH':'数据集版本不符，请重新选择固定版本。'}
   raise HTTPException(status,detail={'code':code,'message':messages.get(code,'专业数据无法读取或尚不支持该查询，请核对数据版本与来源。')}) from None
 @app.get('/v1/professional-data')
 def catalog(request:Request):
  if not os.environ.get('APS_PRO_DATA_ROOT'):return {'availability':'NOT_CONFIGURED','release_id':RELEASE,'datasets':[],'readonly':True}
  return access(request,[],lambda a:a.catalog())
 @app.get('/v1/professional-data/dataset')
 def dataset(request:Request,dataset_id:str,dataset_version:str):return access(request,['dataset_id','dataset_version'],lambda a:a.bundle(dataset_id,dataset_version))
 @app.get('/v1/professional-data/object')
 def object(request:Request,dataset_id:str,dataset_version:str,object_type:str,object_id:str):
  return access(request,['dataset_id','dataset_version','object_type','object_id'],lambda a:a.object(dataset_id,dataset_version,object_type,object_id))
 @app.get('/v1/professional-data/calendar')
 def calendar(request:Request,dataset_id:str,dataset_version:str,object_type:str,object_id:str,start:str,end:str):
  return access(request,['dataset_id','dataset_version','object_type','object_id','start','end'],lambda a:a.calendar(dataset_id,dataset_version,object_type,object_id,start,end))
