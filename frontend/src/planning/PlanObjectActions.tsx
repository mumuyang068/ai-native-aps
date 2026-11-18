import {useState} from 'react';
import type {Dict} from '../api/contracts';
import type {PlanScope} from '../app/scope';
import {Button,Definition,Dialog} from '../components/Controls';
import {ApprovedManufacturingMove} from './ApprovedManufacturingMove';
import type {TaskRecord} from './planModel';
import {qty,unit} from './planModel';
import {planObjectContext} from './planEditorModel';
import {formatSiteTime} from './timeRange';
import './plan-editor.css';

export type PlanObjectActionsProps={scope:PlanScope;baselineRunId:string;dataVersion:string;input:Dict;result:Dict;task:TaskRecord;timezone:string;onTarget:(id:string)=>void;onResource:(id:string)=>void;onCapacity:(taskId:string,resourceId:string)=>void};

/** Entry from the actual selected operation. Existing workbenches own candidate creation and calculation. */
export function PlanObjectActions(props:PlanObjectActionsProps){
 const[opened,setOpened]=useState<string|null>(null),{scope,baselineRunId,dataVersion,input,result,task,timezone,onTarget,onResource,onCapacity}=props;
 const key=JSON.stringify([scope.planId,dataVersion,scope.target,task.id,baselineRunId]),context=planObjectContext(input,result,task.id,{target:scope.target,baselineRunId});
 const time=(value:unknown)=>typeof value==='string'?formatSiteTime(value,timezone):'未提供';
 const selected=context.task,parent=context.manufacture,source=context.sourceOrder;
 const resource=(id:string)=>{setOpened(null);onResource(id)};
 const capacity=(id:string)=>{if(!context.capacity.allowed||!selected||!context.alternatives.some(a=>a.resource.id===id&&a.qualified))return;setOpened(null);onCapacity(selected.id,id)};
 return <><Button className="wide plan-object-trigger" onClick={()=>setOpened(key)}>查看工单与调整</Button>{opened===key&&<Dialog title="工单、资源与计划调整" size="840" className="plan-object-dialog" onClose={()=>setOpened(null)}>
  <div className="plan-object-editor">
   {!selected||!parent?<p role="alert">{context.sourceReason}</p>:<>
    <Definition rows={[["工序",selected.operation_id],["制造对象",source?.id??parent.external_ref??parent.id],["产品",parent.item_id??task.itemId??'未提供'],["工序数量",qty(selected.quantity)+' '+unit(parent.uom??task.uom??'单位未提供')],["制造状态",parent.origin==='EXISTING'?'已有工单 · '+(parent.source_status??'状态未提供'):'制造建议 · '+(parent.source_status??'proposed')],["计划开始",time(selected.start_at)],["计划结束",time(selected.end_at)],["业务时区",timezone]]}/>
    {source&&<section aria-label="原始工单条件"><h3>原始工单条件</h3><Definition rows={[["原工单",source.id],["原始状态",source.source_status??source.status??'未提供'],["冻结策略",source.freeze_policy??'未提供'],["原工单数量",qty(source.quantity)+' '+unit(source.uom??parent.uom??task.uom??'单位未提供')],["已完成数量",source.completed_quantity===undefined?'未提供':qty(source.completed_quantity)]]}/><div className="plan-editor-records">{(source.operations??[]).map((o:Dict,index:number)=><div className="plan-editor-record" key={index}><strong>{o.operation_id}</strong><span>原资源 {o.resource_id}{o.operator_id?' · 原人员 '+o.operator_id:''}</span><span>{time(o.start_at)} → {time(o.end_at)}</span></div>)}</div></section>}
    {context.sourceReason&&<p className="plan-editor-reason" role="status">{context.sourceReason}</p>}
    <section aria-label="本次实际资源与人员"><h3>本次实际资源与人员</h3>{context.actualResources.length?<div className="plan-editor-records">{context.actualResources.map(({load,resource:actual,calendar,qualifications},index)=><div className="plan-editor-record" key={load.resource_id+':'+index}>
      <div className="plan-editor-resource-heading"><strong>{load.resource_id} · {actual?.role==='OPERATOR'?'具名人员':actual?.role==='MACHINE'?'设备':'资源'}</strong><Button disabled={!actual} onClick={()=>resource(load.resource_id)}>查看资源日历</Button></div>
      <span>实际占用 {time(load.start_at)} → {time(load.end_at)} · 占用单位 {qty(load.resource_units)}</span>
      <span>原日历 {actual?.availability_calendar_id??actual?.calendar_id??'未提供'}{actual?.capacity_model==='WINDOW_QUANTITY'?' · 数量窗口容量，不换算为工时':''}</span>
      {!actual&&<p role="status">当前输入没有唯一的该资源记录，保留原负荷记录，不代用其他资源。</p>}
      <details><summary>原日历、资格与负荷来源</summary><pre className="raw-json">{JSON.stringify({resource:actual??null,calendar:calendar??null,qualifications,load},null,2)}</pre></details>
     </div>)}</div>:<p>当前工序没有对应的实际资源负荷记录，无法确认设备或人员占用。</p>}</section>
    <section aria-label="允许的计划调整"><h3>计划调整</h3>
     <div className="plan-editor-action"><strong>工单时间窗口</strong><p>{context.move.reason}</p>{context.move.allowed&&source&&<ApprovedManufacturingMove key={key+source.id} scope={scope} baselineRunId={baselineRunId} dataVersion={dataVersion} order={source} timezone={timezone} triggerLabel="选择合法窗口并创建候选" onTarget={id=>{setOpened(null);onTarget(id)}}/>}</div>
     <div className="plan-editor-action"><strong>设备停机与人员缺勤</strong><p>{context.unavailability.reason}</p>{context.unavailability.allowed&&<p>从上方实际设备或具名人员进入资源日历，再输入不可用起止时间并创建独立草案。</p>}</div>
     <div className="plan-editor-action"><strong>可替代设备与容量条件</strong><p>{context.capacity.reason}</p>{context.alternatives.length>0&&<><p>以下为工艺声明的候选设备，实际采用情况见上方负荷记录。只允许减少已认证机器的可用时段，替代分配由原规划计算。</p><div className="plan-editor-records">{context.alternatives.map(a=><div className="plan-editor-record" key={a.resource.id}><div className="plan-editor-resource-heading"><strong>{a.resource.id}</strong>{context.capacity.allowed&&<Button disabled={!a.qualified} onClick={()=>capacity(a.resource.id)}>输入容量候选</Button>}</div><span>所需资格 {a.required} · {a.qualified?'声明资格覆盖当前规划期':'资格不满足，不能选择'}</span></div>)}</div></>}</div>
     <p>候选中的时间与设备变化均为假设。创建后需在独立方案中调用原规划计算，才能比较新的甘特、负荷与履约结果，并进入方案评审。</p>
    </section>
    <details><summary>对象身份与原始来源</summary><Definition rows={[["企业计划",scope.planId],["数据版本",dataVersion],["当前方案",scope.target],["原基线运行",baselineRunId],["工序记录",selected.id],["制造父记录",parent.id]]}/><pre className="raw-json">{JSON.stringify({operation:selected,manufacture:parent,source_order:source??null,source_provenance:source?.provenance??null,load_evidence:context.actualResources.map(r=>r.load)},null,2)}</pre></details>
   </>}
  </div>
 </Dialog>}</>;
}
