import {useRef,useState} from 'react';
import type {Dict} from '../api/contracts';
import {request} from '../api/client';
import {useActionGuard} from '../app/useActionGuard';
import {Badge,Button,Definition,ErrorNote} from '../components/Controls';
import {supports} from './capabilityModel';
import {decisionQuantity,type DemandDecisionScope,type DemandPlanningRow} from './demandDecisionModel';

const sourceKeys=['source_system','source_record_id','source_version','observed_at','import_batch_id','source_locator'] as const;
const bucketKeys=['forecast_id','forecast_version_id','item_id','location_id','customer_id','uom','bucket_start','bucket_end'] as const;
export type ForecastBucket=Record<typeof bucketKeys[number],string>&{provenance:Record<typeof sourceKeys[number],string>};

export function forecastBucket(row:DemandPlanningRow):ForecastBucket|null{
 const fields={forecast_id:row.forecast_id,forecast_version_id:row.version_id,item_id:row.item_id,location_id:row.location_id,customer_id:row.customer_id,uom:row.uom,bucket_start:row.start_at,bucket_end:row.end_at};
 const provenance:Dict|undefined=row.provenance&&typeof row.provenance==='object'?row.provenance as Dict:undefined;
 if(Object.values(fields).some(value=>typeof value!=='string'||!value.trim())||!provenance||typeof provenance!=='object'||Object.keys(provenance).length!==sourceKeys.length||sourceKeys.some(key=>typeof provenance[key]!=='string'||!provenance[key].trim()))return null;
 return {...fields,provenance:Object.fromEntries(sourceKeys.map(key=>[key,provenance[key]]))} as ForecastBucket;
}
function normalized(value:string){const [whole,fraction='']=value.split('.');const tail=fraction.replace(/0+$/,'');return whole+(tail?'.'+tail:'')}
export function forecastGross(value:string,before:unknown):string|null{
 if(!/^(0|[1-9][0-9]{0,11})(\.[0-9]{1,8})?$/.test(value)||typeof before!=='string'||normalized(value)===normalized(before))return null;
 return normalized(value);
}
function sameBucket(actual:Dict|undefined,expected:ForecastBucket){
 return !!actual&&bucketKeys.every(key=>actual[key]===expected[key])&&sourceKeys.every(key=>actual.provenance?.[key]===expected.provenance[key]);
}
function sameScope(identity:Dict|undefined,scope:DemandDecisionScope){
 return !!identity&&identity.plan_id===scope.planId&&identity.data_version_id===scope.versionId&&identity.baseline_run_id===scope.runId&&identity.run_id===scope.runId&&identity.target===scope.target;
}
export function verifyForecastPreview(value:Dict,scope:DemandDecisionScope,bucket:ForecastBucket,gross:string,reason:string):Dict{
 if(!sameScope(value?.identity,scope)||!sameBucket(value.bucket,bucket)||value.engine_executed!==false||value.calculation_submitted!==false||value.after?.gross_quantity!==normalized(gross)||value.reason!==reason.trim())throw Error('预测预览与当前计划、运行、桶或输入不一致，请重新预览。');
 return value;
}
export function verifyForecastDraft(value:Dict,scope:DemandDecisionScope,bucket:ForecastBucket,gross:string,reason:string):string{
 const draft=value?.draft,change=draft?.changes;
 if(!sameScope(value?.identity,scope)||value.calculation_submitted!==false||value.run?.state!=='NOT_RUN'||!draft||typeof draft.id!=='string'||!draft.id||draft.state!=='DRAFT'||draft.run_id!==null||change?.action!=='set_forecast_gross_override'||!sameBucket(change.bucket,bucket)||change.gross_quantity!==normalized(gross)||change.reason!==reason.trim())throw Error('响应未核实为当前预测桶的 NOT_RUN 独立草案，请刷新计划核对。');
 return draft.id;
}

type Props={scope:DemandDecisionScope;profile?:string;row:DemandPlanningRow;onDraft?:(id:string)=>void};
export function ForecastOverride({scope,profile,row,onDraft}:Props){
 const bucket=forecastBucket(row),key=JSON.stringify([scope.planId,scope.versionId,scope.runId,scope.target,bucket]);
 const[editor,setEditor]=useState<{key:string;gross:string;reason:string}|null>(null),[preview,setPreview]=useState<{key:string;input:string;data:Dict}|null>(null),[failure,setFailure]=useState<{key:string;message:string}|null>(null),[pending,setPending]=useState('');
 const capture=useActionGuard(key),receipt=useRef<{input:string;id:string}|null>(null),open=editor?.key===key,busy=pending===key;
 const gross=open?forecastGross(editor.gross,row.gross_forecast):null,reason=open?editor.reason.trim():'',input=JSON.stringify([key,gross,reason]);
 const available=scope.target==='baseline'&&supports(profile,'actions','forecast_override')&&!!bucket&&!!onDraft,valid=available&&gross!==null&&reason.length>0&&reason.length<=1000;
 const currentPreview=preview?.key===key&&preview.input===input?preview.data:null;
 function edit(field:'gross'|'reason',value:string){if(editor){setEditor({...editor,[field]:value});setPreview(null);setFailure(null)}}
 function close(){setEditor(null);setPreview(null);setFailure(null)}
 async function act(create:boolean){
  if(!valid||busy||!bucket||gross===null||(create&&!currentPreview))return;
  const owns=capture();setPending(key);setFailure(null);
  const body={data_version_id:scope.versionId,baseline_run_id:scope.runId,run_id:scope.runId,target:scope.target,bucket,gross_quantity:gross,reason};
  try{
   if(create){
    if(receipt.current?.input!==input)receipt.current={input,id:crypto.randomUUID().replaceAll('-','')};
    const value=await request<Dict>('/v1/enterprise-plans/'+encodeURIComponent(scope.planId)+'/forecast-override-drafts',{method:'POST',body:{...body,request_id:receipt.current.id}});
    if(owns()){const id=verifyForecastDraft(value,scope,bucket,gross,reason);close();onDraft?.(id)}
   }else{
    const value=await request<Dict>('/v1/enterprise-plans/'+encodeURIComponent(scope.planId)+'/forecast-override-preview',{method:'POST',body});
    if(owns())setPreview({key,input,data:verifyForecastPreview(value,scope,bucket,gross,reason)});
   }
  }catch(error:unknown){if(owns())setFailure({key,message:error instanceof Error?error.message:'预测操作状态未核实，请刷新当前计划。'})}
  finally{if(owns())setPending('')}
 }
 if(!supports(profile,'actions','forecast_override'))return null;
 return <div className="demand-priority">
  <Button disabled={!available||busy} aria-expanded={open} onClick={()=>{if(open)close();else{setEditor({key,gross:String(row.gross_forecast??''),reason:''});setPreview(null);setFailure(null)}}}>模拟预测 Gross</Button>
  {!available&&<p className="demand-decision-note">{scope.target!=='baseline'?'请返回原始基线，为此来源预测桶创建独立候选。':'当前预测桶缺少精确来源身份或草案入口，请刷新计划。'}</p>}
  {open&&<form className="demand-priority-editor" aria-label="预测 Gross 覆盖草案" onSubmit={event=>{event.preventDefault();void act(false)}}>
   <h3>预测 Gross 假设</h3>
   <Definition rows={[["预测桶",bucket?.forecast_id??'未知'],["预测版本",bucket?.forecast_version_id??'未知'],["客户 / 单位",(bucket?.customer_id??'未知')+' / '+row.uom],["数据版本",scope.versionId],["基线运行",scope.runId],["原始 Gross",decisionQuantity(row.gross_forecast,row.uom)],["未结 Firm",decisionQuantity(row.firm_open_orders,row.uom)],["原已消费 / Net",decisionQuantity(row.forecast_consumed,row.uom)+' / '+decisionQuantity(row.net_forecast,row.uom)]]}/>
   <label>假设 Gross<input aria-label="假设预测 Gross" type="text" inputMode="decimal" required disabled={busy} value={editor.gross} onChange={event=>edit('gross',event.target.value)}/></label>
   <label>假设理由<input aria-label="预测覆盖理由" type="text" maxLength={1000} required disabled={busy} value={editor.reason} onChange={event=>edit('reason',event.target.value)}/></label>
   <p className="demand-decision-note">同物料、地点、客户、单位及半开桶的未结订单按原规则消费 Gross。预览仅重新抵扣；实际履约、产能与库存变化需创建候选后提交计算。</p>
   {currentPreview&&<div aria-live="polite"><h4>重新抵扣预览</h4><Definition rows={[["Gross 原值 → 假设",decisionQuantity(currentPreview.before.gross_quantity,row.uom)+' → '+decisionQuantity(currentPreview.after.gross_quantity,row.uom)],["订单消费 原值 → 假设",decisionQuantity(currentPreview.before.consumed_quantity,row.uom)+' → '+decisionQuantity(currentPreview.after.consumed_quantity,row.uom)],["Net 原值 → 假设",decisionQuantity(currentPreview.before.net_quantity,row.uom)+' → '+decisionQuantity(currentPreview.after.net_quantity,row.uom)]]}/><Badge>规则推算 · 尚未求解</Badge></div>}
   <ErrorNote error={failure?.key===key?failure.message:''}/>
   <div className="demand-decision-actions"><Button type="submit" disabled={!valid||busy}>{busy?'正在核对':'预览重新抵扣'}</Button>{currentPreview&&<Button className="primary" disabled={!valid||busy} onClick={()=>void act(true)}>创建 NOT_RUN 草案</Button>}<Button disabled={busy} onClick={close}>取消</Button></div>
  </form>}
 </div>;
}
