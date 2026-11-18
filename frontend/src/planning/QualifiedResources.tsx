import type {Dict} from '../api/contracts';
import {Badge} from '../components/Controls';
import {DataTable,type TableColumn} from '../components/DataTable';
import {qualifiedResourceRows} from './presentationModel';
import {formatSiteTime} from './timeRange';
import {qty,unit} from './planModel';
import './qualified-resources.css';

export function QualifiedResources({report,input,timezone,reference}:{report?:Dict;input:Dict;timezone:string;reference?:string}){
 const rows=qualifiedResourceRows(report,reference),passed=report?.status==='PASS';
 const amount=(r:Dict)=>{const route=(input.routing_revisions??[]).find((x:Dict)=>x.operations.some((o:Dict)=>o.id===r.operation_id));const uom=(input.items??[]).find((x:Dict)=>x.id===route?.output_item_id)?.base_uom;return qty(Number.isSafeInteger(Number(r.quantity))?Number(r.quantity):r.quantity)+(uom?' '+unit(uom):'（单位未提供）')};
 const columns:TableColumn<Dict>[]=[{id:'op',title:'工序',value:r=>r.operation_id},{id:'machine',title:'设备',value:r=>r.machine_id},
  {id:'person',title:'具名操作员',value:r=>r.operator_id??'身份未核验'},{id:'skill',title:'必需技能',value:r=>r.required_skill_id},
  {id:'quantity',title:'制造数量',value:amount},{id:'start',title:'同时占用开始',value:r=>formatSiteTime(r.start_at,timezone)},
  {id:'end',title:'同时占用结束',value:r=>formatSiteTime(r.end_at,timezone)}];
 return <section className="qualified-resources" aria-label="设备与人员同时占用">
  <h2>设备与人员同时占用</h2>
  <Badge kind={passed?'good':report?.status==='FAIL'?'bad':'risk'}>{passed?'本次联合资源核验通过':report?.status==='FAIL'?'安排存在资源违规':'人员或原生占用证据不足'}</Badge>
  <p className="detail-note">主排程按设备显示工序；以下为同一工序的人员安排。计算结果不代表已下达生产。</p>
  {input.rules?.manufacturing_profile==='mixed-fixed-batch-confirmed-qualified-v1'&&<p>本联合模型只核验 SEW 的具名人员需求；CUT 保留数量窗口，未声明 CUT 人员安排。</p>}
  {input.rules?.manufacturing_profile==='mixed-fixed-batch-confirmed-cut-qualified-v1'&&<p>CUT 设备按整批数量窗口核验，CUT 与 SEW 人员分别按具名资格及同步占用时间核验。20套批量不等于20人。</p>}
  {rows.length?<><p>人员候选池：{rows[0].operator_pool_id}</p><DataTable rows={rows} columns={columns} rowId={r=>r.native_reference}/></>:<p>{passed?'本次没有排定工序。设备空闲不代表有合格人员可用。':'缺少可确认的同时占用记录，不能据此认可人员安排。'}</p>}
 </section>;
}
