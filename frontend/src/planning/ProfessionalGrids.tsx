import {useMemo} from 'react';
import type {Dict} from '../api/contracts';
import {ProfessionalGrid,type GridEvidence,type GridScope,type ProfessionalColumn} from '../components/ProfessionalGrid';
import type {TaskRecord} from './planModel';
import {manufacturingLabel,qty,unit} from './planModel';
import {routingOperationLabel} from './routingTimelineModel';
import {formatSiteTime} from './timeRange';

type OperationGridRow=TaskRecord&{machine_id:string|null;operator_id:string|null};
export function routingOperationRows(tasks:TaskRecord[],assignments:Dict[]):OperationGridRow[]{
 return tasks.map(task=>{const matches=assignments.filter(a=>String(a.native_reference)===task.id.split(':').at(-1));const actual=matches.length===1?matches[0]:null;
  return {...task,machine_id:typeof actual?.machine_id==='string'?actual.machine_id:null,operator_id:typeof actual?.operator_id==='string'?actual.operator_id:null};
 });
}
export function RoutingOperationsGrid({scope,input,tasks,assignments,selectedTaskId,onTask,evidenceStatus='PRESENT'}:{scope:GridScope;input:Dict;tasks:TaskRecord[];assignments:Dict[];selectedTaskId?:string;onTask:(id:string)=>void;evidenceStatus?:GridEvidence}){
 const rows=useMemo(()=>routingOperationRows(tasks,assignments),[tasks,assignments]),timezone=input.site?.timezone??'Asia/Shanghai';
 const columns=useMemo<ProfessionalColumn<OperationGridRow>[]>(()=>[
  {id:'operation',title:'工序',width:250,value:row=><>{routingOperationLabel(input,row.operation_id)}<small>{manufacturingLabel(row)}</small></>,sortValue:row=>routingOperationLabel(input,row.operation_id)},
  {id:'quantity',title:'制造数量',numeric:true,width:145,value:row=>qty(row.quantity)+' '+(row.uom==='set'?'套':unit(row.uom)),sortValue:row=>Number(row.quantity)},
  {id:'machine',title:'实际设备',value:row=>row.machine_id??'实际设备未核验',sortValue:row=>row.machine_id??''},
  {id:'operator',title:'具名人员',value:row=>row.operator_id??'实际人员未核验',sortValue:row=>row.operator_id??''},
  {id:'start',title:'计划开始',width:190,value:row=>formatSiteTime(row.start_at,timezone),sortValue:row=>Date.parse(row.start_at)},
  {id:'end',title:'计划结束',width:190,value:row=>formatSiteTime(row.end_at,timezone),sortValue:row=>Date.parse(row.end_at)},
 ],[input,timezone]);
 return <ProfessionalGrid scope={scope} rows={rows} columns={columns} rowId={row=>row.id} label="制造工序" selection={selectedTaskId?{scope,id:selectedTaskId}:null} onSelect={row=>onTask(row.id)} onOpen={row=>onTask(row.id)} evidenceStatus={evidenceStatus} empty="已查询，本范围没有制造工序。"/>;
}
