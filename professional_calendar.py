"""Bounded, read-only calendar projection. Never a LoadPlan or engine result."""
from datetime import datetime,timedelta

def attributes(row):return row.get('attributes',row)

def horizon(start,end):
 try:a=datetime.strptime(start,'%Y-%m-%d');b=datetime.strptime(end,'%Y-%m-%d')
 except (ValueError,TypeError):raise ValueError('INVALID_CALENDAR_RANGE') from None
 if not 0<(b-a).days<=14:raise ValueError('INVALID_CALENDAR_RANGE')
 return a,b

def local(value):
 d=datetime.fromisoformat(value)
 if d.tzinfo is not None and d.utcoffset()!=timedelta(hours=8):raise ValueError('UNSUPPORTED_CALENDAR_OFFSET')
 return d.replace(tzinfo=None)

def seconds(value,default):
 if value is None:return default
 h,m,s=map(int,value.split(':'));return h*3600+m*60+s

def project_calendar(calendar,buckets,start,end,synthetic):
 a,b=horizon(start,end);c=attributes(calendar);cid=c.get('name',c.get('id'));spans=[];limitations=[]
 if synthetic:
  default=0
  for v in c.get('available_intervals',[]):
   x,y=max(a,local(v['start_at'])),min(b,local(v['end_at']))
   if x<y:spans.append((x,y,0,1,calendar['entity_id']))
 else:
  default=c.get('defaultvalue')
  for row in buckets:
   v=attributes(row)
   if v.get('calendar_id')!=cid:continue
   try:
    valid_start=local(v['startdate']) if v.get('startdate') else a
    valid_end=local(v['enddate']) if v.get('enddate') else b
    st=seconds(v.get('starttime'),0);en=seconds(v.get('endtime'),86399)
    if not 0<=st<=en<=86400:raise ValueError('OVERNIGHT_BUCKET_NOT_CERTIFIED')
    day=a
    while day<b:
     label=day.strftime('%A').lower()
     enabled=v.get(label, bool(int(v.get('days',127)) & (1<<((day.weekday()+1)%7))))
     if enabled:
      x=max(a,valid_start,day+timedelta(seconds=st));y=min(b,valid_end,day+timedelta(seconds=en))
      if x<y:spans.append((x,y,int(v.get('priority',0)),v.get('value'),row['entity_id']))
     day+=timedelta(days=1)
   except (ValueError,TypeError):limitations.append({'object_id':row['entity_id'],'status':'NOT_CHECKED','reason':'UNSUPPORTED_BUCKET_DEFINITION'})
 boundaries={a,b};day=a
 while day<b:boundaries.add(day);day+=timedelta(days=1)
 for x,y,_,_,_ in spans:boundaries.update([x,y])
 times=sorted(boundaries);segments=[]
 for x,y in zip(times,times[1:]):
  live=[v for v in spans if v[0]<=x<v[1]]
  priority=min(v[2] for v in live) if live else None
  winners=[v for v in live if v[2]==priority]
  values={v[3] for v in winners};ambiguous=len(values)>1 or bool(limitations)
  value=None if ambiguous else next(iter(values)) if winners else default
  segments.append({'start':x.isoformat(),'end':y.isoformat(),'value':value,'priority':priority,
   'origin':'BUCKET' if winners else 'DEFAULT','source_object_ids':[v[4] for v in winners] or [calendar['entity_id']],
   'status':'NOT_CHECKED' if ambiguous or value is None else 'DECLARED_CALENDAR'})
 return {'calendar_id':cid,'object_id':calendar['entity_id'],'default_value':default,'segments':segments,'limitations':limitations,
  'boundary_semantics':'Half-open projected intervals; original endtime 23:59:59 retained literally, remaining second uses default. No engine-equivalence claim.',
  'status':'NOT_CHECKED' if limitations else 'DECLARED_CALENDAR','is_actual_load':False}
