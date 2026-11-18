import type {Dict} from '../api/contracts';
import type {GanttInterval,GanttRow} from '../components/ganttModel';
export function verifySourceWorkspace(value:Dict,planId:string,versionId:string){
 if(value.schema_version!=='source-workspace/1'||value.basis!=='CONFIRMED_PLAN_INPUT'||value.identity?.plan_id!==planId||value.identity.data_version_id!==versionId||value.identity.run_id!==null)throw Error('来源资料与当前计划确认版本不一致。');
 return value;
}
export function sourceResources(input:Dict):Dict[]{
 const a=Date.parse(input.horizon.start_at),b=Date.parse(input.horizon.end_at);
 return (input.resources??[]).map((r:Dict)=>{
  const calendar=(input.calendars??[]).find((c:Dict)=>c.id===r.availability_calendar_id),work:number[][]=[];
  for(const w of (calendar?.available_intervals??[]).map((w:Dict)=>[Math.max(a,Date.parse(w.start_at)),Math.min(b,Date.parse(w.end_at))]).filter(([l,h]:number[])=>h>l).sort((x:number[],y:number[])=>x[0]-y[0])){
   const last=work.at(-1);if(last&&w[0]<=last[1])last[1]=Math.max(last[1],w[1]);else work.push([...w]);
  }
  const units=Number(r.parallel_units??r.capacity),continuous=r.capacity_model!=='WINDOW_QUANTITY'&&!String(r.role??'').endsWith('_POOL');
  return {...r,calendar,available_hours:continuous&&calendar&&Number.isFinite(units)?work.reduce((n,w)=>n+(w[1]-w[0])/3600000,0)*units:null,planned_load:null};
 });
}
/** Source MO windows are not reconstructed operation or personnel assignments. */
export function sourceManufacturing(input:Dict){
 return (input.existing_orders??[]).filter((r:Dict)=>r.type==='MO').map((r:Dict)=>({...r,uom:r.uom??(input.items??[]).find((i:Dict)=>i.id===r.item_id)?.base_uom,protected:r.protected===true||r.freeze_policy==='LOCKED',basis:'SOURCE_ORDER_WINDOW'}));
}
export function sourceGantt(input:Dict):{rows:GanttRow[];intervals:GanttInterval[]}{
 const resources=sourceResources(input),orders=sourceManufacturing(input);
 const rows:GanttRow[]=resources.map((r:Dict)=>({id:r.id,label:r.name??r.id,detail:r.role??'资源',calendar:r.calendar?r.calendar.available_intervals.map((w:Dict)=>({start:Date.parse(w.start_at),end:Date.parse(w.end_at)})):null,empty:'来源日历；计划占用尚未计算'}));
 const intervals:GanttInterval[]=orders.flatMap((r:Dict)=>(r.operations??(r.resource_id?[r]:[])).flatMap((operation:Dict,index:number)=>{
  const start=Date.parse(operation.start_at),end=Date.parse(operation.end_at),resource=operation.resource_id;
  if(!resource||!rows.some(x=>x.id===resource)||!Number.isFinite(start)||!(end>start))return [];
  return [{id:JSON.stringify([r.id,index]),taskId:r.id,rowId:resource,start,end,label:r.id+' · '+r.quantity+' '+r.uom,nature:'planned' as const,kind:'operation' as const,protected:r.protected,details:[r.item_id,'来源工单窗口 · '+r.status,'不是原生工序占用']}];
 }));
 return {rows,intervals};
}
