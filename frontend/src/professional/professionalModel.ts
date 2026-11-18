export type Values=Record<string,any>;
export type AssetRow=Values&{entity_id:string;dataset_id:string;attributes?:Values};
export type Bundle={release_id:string;manifest_sha256:string;dataset:{dataset_id:string;version:string;fact_kind:string;time_semantics:string;units:string};tables:Record<string,AssetRow[]>;native_result:{status:string;records:null};plan_context:null};
export type Obj={id:string;key:string;type:string;name:string;row:AssetRow};
export type Op=Obj&{outputs:string[];owner?:string;sequence:number;kind:string;fixed:unknown;per:unknown;resourceIds:string[];skill?:string};
export type Flow={input:string;output:string;operation:string;quantity:unknown;uom?:string;row:AssetRow};
export type Stock=Obj&{item:string;location:string;quantity:unknown;physical:unknown;frozen:unknown;other:unknown;uom?:string;at?:string;knowledge?:string};
export const fields=(r:AssetRow):Values=>r.attributes??r;
export const number=(v:unknown):number|null=>v!==null&&v!==undefined&&v!==''&&Number.isFinite(Number(v))?Number(v):null;
export const show=(v:unknown)=>v===null||v===undefined||v===''?'未提供':typeof v==='object'?JSON.stringify(v):String(v);
export function buildProfessionalModel(bundle:Bundle){
 const t=bundle.tables,sim=bundle.dataset.fact_kind==='SYNTHETIC_FACT';
 const objects=Object.entries(t).flatMap(([type,rows])=>rows.map(row=>{const a=fields(row);return {id:row.entity_id,key:String((sim?a.id:a.name??a.id)??row.business_key??row.entity_id),name:String(type==='buffers'||type==='inventory_positions'?a.item_id+' @ '+a.location_id:sim&&a.id?(a.name&&a.name!==a.id?a.id+' · '+a.name:a.id):a.name??a.id??row.business_key??row.entity_id),type,row}}));
 const list=(type:string)=>objects.filter(v=>v.type===type),get=(type:string,key:string)=>list(type).find(v=>v.key===key);
 const items=list('items'),resources=[...list('resources'),...list('operators')],locations=list('locations');
 const material=t.operation_materials??[],requirements=t.operation_resources??[];
 const ops:Op[]=list('operations').map(o=>{const a=fields(o.row);let outputs=material.filter(v=>fields(v).operation_id===o.key&&(number(fields(v).quantity)??0)>0).map(v=>String(fields(v).item_id));
  if(a.item_id&&!outputs.includes(a.item_id))outputs.push(a.item_id);
  if(sim){const recipe=(t.recipes??[]).find(v=>v.id===a.recipe_id);if(a.produces_output&&recipe)outputs=[recipe.output_item_id];}
  return {...o,outputs,owner:a.owner_id,sequence:number(a.sequence??a.priority)??0,kind:a.type??a.duration_model??'未提供',fixed:a.duration??a.fixed_seconds,per:a.duration_per??a.seconds_per_unit,resourceIds:sim?a.resource_ids??[]:requirements.filter(v=>fields(v).operation_id===o.key).map(v=>fields(v).resource_id),skill:a.required_operator_skill};
 });
 const opByKey=(key:string)=>ops.find(v=>v.key===key);
 const flows:Flow[]=[];
 if(sim){for(const v of t.bom_lines??[]){flows.push({input:v.input_item_id,output:v.output_item_id,operation:v.consume_operation_id,quantity:v.quantity_per_output,uom:v.input_uom,row:v})}}
 else for(const v of material){const a=fields(v);if((number(a.quantity)??0)>=0)continue;const op=opByKey(a.operation_id);const outputs=op?.outputs.length?op.outputs:op?.owner?opByKey(op.owner)?.outputs??[]:[];for(const output of outputs)flows.push({input:a.item_id,output,operation:a.operation_id,quantity:Math.abs(number(a.quantity)!),row:v})}
 const stocks:Stock[]=list(sim?'inventory_positions':'buffers').map(o=>{const a=fields(o.row);return {...o,item:a.item_id,location:a.location_id,quantity:sim?a.net_available:a.onhand,physical:sim?a.physical:null,frozen:sim?a.quality_blocked:null,other:sim?a.other_unavailable:null,uom:a.uom,at:a.as_of,knowledge:a.knowledge}});
 const operationsFor=(item:string)=>{const direct=ops.filter(v=>v.outputs.includes(item)||flows.some(f=>f.output===item&&f.operation===v.key));return ops.filter(v=>direct.includes(v)||direct.some(p=>p.key===v.owner))};
 const uses=(item:string)=>flows.filter(v=>v.input===item),inputs=(item:string)=>flows.filter(v=>v.output===item);
 const requirementRows=(op:Op):Values[]=>sim?[...op.resourceIds.map(id=>({resource_id:id,skill_id:undefined,quantity:null,evidence:op.row,kind:'DECLARED_MACHINE_REQUIREMENT'})),...(op.skill?[{resource_id:null,skill_id:op.skill,quantity:null,evidence:op.row,kind:'QUALIFIED_PERSON_REQUIRED'}]:[])]:requirements.filter(v=>fields(v).operation_id===op.key).map(v=>({...fields(v),evidence:v,kind:'SOURCE_REQUIREMENT'}));
 const ancestors=(item:string)=>{const keys=[item];let v=get('items',item);while(v&&fields(v.row).owner_id&&!keys.includes(fields(v.row).owner_id)){keys.push(fields(v.row).owner_id);v=get('items',fields(v.row).owner_id)}return keys};
 const paths=(item:string)=>(sim?list('supply_sources'):[...list('item_suppliers'),...list('item_distributions')]).filter(v=>ancestors(item).includes(fields(v.row).item_id));
 const members=(resource:string)=>resources.filter(v=>fields(v.row).owner_id===resource);
 const skillsFor=(resource:string)=>(t.resource_skills??[]).filter(v=>fields(v).resource_id===resource);
 const qualified=(skill:string)=>(t.resource_skills??[]).filter(v=>fields(v).skill_id===skill).flatMap(v=>resources.filter(r=>r.key===fields(v).resource_id));
 return {objects,list,get,items,resources,locations,ops,opByKey,flows,stocks,operationsFor,uses,inputs,requirementRows,paths,members,skillsFor,qualified,sim};
}
export type ProfessionalModel=ReturnType<typeof buildProfessionalModel>;
export function calendarStart(bundle:Bundle){
 const dates=(bundle.tables.calendar_buckets??[]).map(v=>fields(v).startdate as string).filter(v=>v&&v.slice(0,4)>='2020').sort();
 return bundle.dataset.fact_kind==='SYNTHETIC_FACT'?(bundle.tables.calendars??[]).flatMap(v=>v.available_intervals??[]).map(v=>String(v.start_at).slice(0,10)).sort()[0]??null:dates[0]?.slice(0,10)??null;
}
export function nextWeek(start:string){const d=new Date(start+'T00:00:00Z');d.setUTCDate(d.getUTCDate()+7);return d.toISOString().slice(0,10)}
