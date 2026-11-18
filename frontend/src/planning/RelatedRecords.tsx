import {useState} from 'react';
import type {NativeEvidence,OrderTrace} from '../api/contracts';
import {Button,Dialog} from '../components/Controls';
import {DataTable,type TableColumn} from '../components/DataTable';
import {NativeSource,relatedLabels,type RelatedKind} from './OrderDetails';
import {formatSiteTime} from './timeRange';
import {relatedQuantity,chronologicalEntries} from './presentationModel';
export function RelatedRecords({order,kind,timezone,onLocate,compact,selected}:{order:OrderTrace;kind:RelatedKind;timezone:string;onLocate:(id:string)=>void;compact:boolean;selected:string|null}){
 const [record,setRecord]=useState<NativeEvidence|null>(null),rows=kind==='operations'?chronologicalEntries(order[kind]):order[kind],time=(s:string|undefined)=>formatSiteTime(s,timezone);
 const columns:TableColumn<NativeEvidence>[]=[{id:'object',title:relatedLabels[kind],value:r=>kind==='materials'?r.record.item:kind==='resources'?r.record.resource:kind==='purchases'?r.record.item:r.record.operation?.split(':').at(-1),sortValue:r=>String(r.record.item??r.record.resource??r.record.operation??'')},
 {id:'ref',title:'任务 / 上级',value:r=><>{r.engine_ref??r.record.operationplan}<small>{r.owner_ref?'上级 '+r.owner_ref:''}</small></>},
 {id:'qty',title:kind==='resources'?'资源单位':'完整任务数量',numeric:true,value:r=>relatedQuantity(order,kind,r)},
 {id:'start',title:kind==='purchases'?'建议下单':kind==='materials'?'流转时间':'预计开始',value:r=>time(r.task?.order_at??r.task?.start_at??r.times?.flowdate??r.times?.startdate),sortValue:r=>String(r.task?.order_at??r.task?.start_at??r.times?.flowdate??r.times?.startdate??'')},
 {id:'end',title:kind==='purchases'?'预计到料':'预计结束',value:r=>kind==='materials'?'—':time(r.task?.receipt_at??r.task?.end_at??r.times?.enddate)},
 {id:'source',title:'操作',value:r=><Button className="quiet" onClick={e=>{e.stopPropagation();setRecord(r)}}>查看来源</Button>}];
 if(kind==='purchases')columns.splice(1,0,{id:'supplier',title:'供应商',value:r=>r.record.supplier});
 return <><div className="related-record-heading"><strong>订单 {order.order_ref} · {relatedLabels[kind]} {rows.length} 项</strong><span>{kind==='operations'?'选择工序可定位时间轴；':' '}共享任务的数量不按订单分摊{kind==='materials'?'，负数为消耗、正数为产出':''}。</span></div><DataTable rows={rows} columns={columns} rowId={r=>r.id??r.source.evidence_id} selected={selected??undefined} compact={compact} onSelect={kind==='operations'?r=>onLocate(r.id):undefined}/>{record&&<Dialog title={'原始记录 · '+relatedLabels[kind]} onClose={()=>setRecord(null)}><NativeSource entry={record}/></Dialog>}</>;
}
