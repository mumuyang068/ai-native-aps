import {describe,it,expect} from 'vitest';
import {createHash} from 'node:crypto';
import {csvCell,exportCsv,prepareExport,verifyExport,reportExportFields,demandKindLabel,type ExportInput} from './reportExport';
import {reportRows} from './reportModel';

const scope={planId:'P',versionId:'V',runId:'R',target:'baseline'};
function input(changes:Partial<ExportInput>={}):ExportInput{return {scope,data:{identity:{plan_id:'P',data_version_id:'V',run_id:'R',target:'baseline',profile:'enterprise-accessory-supply-v1'},timezone:'Asia/Shanghai',evidence_files:[{artifact:'raw/deliveryorder.json',sha256:'a'.repeat(64)}]},status:'PRESENT',kind:'demand',rows:[],fields:reportExportFields.demand,filters:{query:'FG',active:true},sourceUrl:'/reports?run_id=R',...changes}}
describe('current filtered CSV with authenticated sidecar',()=>{
 it('exports the displayed rows, rather than all rows or a selected inspector row',async()=>{
  const rows=[{item_id:'FG',location_id:'A',due_quantity:'12',planned_quantity:'8',overdue_unfulfilled:'4'},{item_id:'WIP',due_quantity:'20'},{item_id:'FG',due_quantity:'0',planned_quantity:'0'}];
  const displayed=reportRows(rows,'demand','FG',true),bundle=await prepareExport(input({rows:displayed}),'2026-10-21T01:00:00Z');
  expect(bundle.metadata.row_count).toBe(1);expect(bundle.csv).toContain('FG');expect(bundle.csv).not.toContain('WIP');expect(bundle.metadata.current_filters).toEqual({query:'FG',active:true});expect(bundle.metadata.generated_at).toBe('2026-10-21T01:00:00Z');
  expect(bundle.metadata.csv.sha256).toBe(createHash('sha256').update(bundle.csv).digest('hex'));expect(bundle.metadata.evidence_files).toEqual(input().data.evidence_files);
 });
 it('preserves UTF-8 BOM, CRLF and RFC4180 commas, quotes and embedded newlines',()=>{
  const text=exportCsv([{name:'中文, "引号"\n第二行'}],[{key:'name',label:'名称',meaning:'原文'}]);
  expect(text.charCodeAt(0)).toBe(0xfeff);expect(text).toBe('\uFEFF名称\r\n"中文, ""引号""\n第二行"\r\n');
 });
 it('prevents spreadsheet formulas including leading whitespace, while preserving numeric values',()=>{
  for(const s of ['=1+1','+cmd','-cmd','@SUM(A1)','\t=HYPERLINK("x")',' \r+1'])expect(csvCell(s)).toContain("'");
  expect(csvCell(-2)).toBe('-2');expect(csvCell('safe')).toBe('safe');
 });
 it('keeps null and unknown empty instead of turning them into zero',()=>{
  expect(csvCell(null)).toBe('');expect(csvCell(undefined)).toBe('');expect(csvCell('0')).toBe('0');expect(csvCell(0)).toBe('0');
 });
 it.each(['plan_id','data_version_id','run_id','target'])('refuses mismatched %s before creating any CSV bundle',key=>{
  const v=input();v.data.identity[key]='other';expect(()=>verifyExport(v)).toThrow('不一致');
 });
 it.each(['UNSUPPORTED','NOT_COLLECTED','INVALID_EVIDENCE','UNREADABLE'])('refuses %s evidence',status=>expect(()=>verifyExport(input({status}))).toThrow());
 it('refuses invented or absent source SHA and an uncertified resource model',()=>{
  for(const files of [undefined,[],[{artifact:'PlanResult.json',sha256:null}],[{artifact:'x',sha256:'not-a-sha'}]]){const v=input();v.data.evidence_files=files;expect(()=>verifyExport(v)).toThrow('来源');}
  const v=input({kind:'resource'});v.data.identity.profile='fixed-batch-window-v1';expect(()=>verifyExport(v)).toThrow('未认证');
 });
 it('refuses incomplete component evidence even when there are some present rows',()=>{
  const v=input();v.data.demand={validation:{status:'NOT_CHECKED'}};expect(()=>verifyExport(v)).toThrow('未完成');v.data.demand.validation={status:'FAIL',missing_evidence:['deliveryorder']};expect(()=>verifyExport(v)).toThrow('必要证据');
 });
 it('allows EMPTY_CHECKED with a header and explicit empty state metadata',async()=>{
  const bundle=await prepareExport(input({status:'EMPTY_CHECKED'}));expect(bundle.metadata.source_status).toBe('EMPTY_CHECKED');expect(bundle.metadata.row_count).toBe(0);expect(bundle.csv.split('\r\n')).toHaveLength(2);
  expect(()=>verifyExport(input({status:'EMPTY_CHECKED',rows:[{id:'inconsistent'}]}))).toThrow('空结果');
 });
 it('retains investigation failure and unknown field semantics in the sidecar',async()=>{
  const v=input();v.data.known_business_fail=true;v.data.demand={validation:{status:'FAIL'}};const bundle=await prepareExport(v);expect(bundle.metadata.validation.status).toBe('FAIL');expect(bundle.metadata.known_business_fail).toBe(true);expect(bundle.metadata.fields.find(f=>f.key==='planned_quantity')?.meaning).toContain('非实际收货');expect(bundle.metadata.csv.null_semantics).toContain('never converted to zero');
 });
 it('exports explicit Firm/Net bucket components and only the displayed demand line details',async()=>{
  const source={artifact:'canonical_input.json',pointer:'/demand_lines/0',sha256:'b'.repeat(64)},components=[{demand_id:'NET-looking-id',demand_kind:'FIRM_ORDER',due_quantity:'10',planned_quantity:'4',overdue_unfulfilled:'6'},{demand_id:'FIRM-looking-id',demand_kind:'NET_FORECAST',due_quantity:'8',planned_quantity:'3',overdue_unfulfilled:'5'}];
  const v=input({rows:[{item_id:'FG',endpoint:'已声明缝制阶段',demand_ids:['NET-looking-id','FIRM-looking-id','FIRM-unknown'],demand_kinds:['FIRM_ORDER','NET_FORECAST',null],demand_components:components}]});
  v.data.demand={lines:[{demand_id:'NET-looking-id',demand_kind:'FIRM_ORDER',planned_quantity:'4',unfulfilled_quantity:'6',planned_delivery_at:'2026-11-05T04:00:00Z',unfulfilled_delivery_at:null,sources:[source]},{demand_id:'FIRM-looking-id',demand_kind:'NET_FORECAST',planned_quantity:'3',unfulfilled_quantity:'5',planned_delivery_at:'2026-11-05T05:00:00Z',unfulfilled_delivery_at:null,sources:[source]},{demand_id:'FIRM-unknown'},{demand_id:'HIDDEN',demand_kind:'FIRM_ORDER'}]};
  const bundle=await prepareExport(v),lines=bundle.metadata.demand_lines!;
  expect(bundle.csv).toContain('已声明缝制阶段');expect(bundle.csv).toContain('期间逐需求明细');expect(bundle.csv).toContain('NET_FORECAST');expect(bundle.csv).not.toContain('HIDDEN');
  expect(lines.map((line:Record<string,unknown>)=>[line.demand_id,line.demand_kind])).toEqual([['NET-looking-id','FIRM_ORDER'],['FIRM-looking-id','NET_FORECAST'],['FIRM-unknown',null]]);
  expect(lines[0]).toMatchObject({planned_quantity:'4',unfulfilled_quantity:'6',planned_delivery_at:'2026-11-05T04:00:00Z',unfulfilled_delivery_at:null,sources:[source]});expect(lines[2].planned_delivery_at).toBeNull();
  expect(bundle.metadata.demand_line_semantics).toContain('per-bucket');expect(v.rows[0].demand_components).toEqual(components);
 });
 it('labels only canonical demand declarations and never guesses a missing kind from an ID',()=>{
  expect(demandKindLabel('FIRM_ORDER')).toBe('Firm · 订单 / 研究假设');expect(demandKindLabel('NET_FORECAST')).toBe('Net · 净预测（非客户订单）');expect(demandKindLabel(undefined)).toBe('未声明');expect(demandKindLabel(null)).toBe('未声明');expect(demandKindLabel('FIRM-like-custom-value')).toContain('类型未识别');
 });
 it('keeps independently generated CSV and source downloads paired by identical bytes and SHA',async()=>{
  const v=input({rows:[{item_id:'FG',due_quantity:'10',planned_quantity:'4',overdue_unfulfilled:'6'}]});
  const csvAction=await prepareExport(v,'2026-10-21T03:00:00Z'),sourceAction=await prepareExport(v,'2026-10-21T03:01:00Z');
  expect(csvAction.csv).toBe(sourceAction.csv);expect(sourceAction.metadata.csv.sha256).toBe(createHash('sha256').update(csvAction.csv).digest('hex'));expect(sourceAction.metadata.current_filters).toEqual(csvAction.metadata.current_filters);expect(sourceAction.metadata.scope).toEqual(csvAction.metadata.scope);expect(sourceAction.metadata.generated_at).not.toBe(csvAction.metadata.generated_at);
 });
});

it('labels only explicit research provenance and keeps unknown Firm sources neutral',()=>{expect(demandKindLabel('FIRM_ORDER',{source_system:'SYNTHETIC_RESEARCH_ASSUMPTION'})).toBe('Firm · 研究假设');expect(demandKindLabel('FIRM_ORDER',{id:'P409-NEW-FIRM-1-L1'})).toBe('Firm · 订单 / 研究假设');expect(demandKindLabel('NET_FORECAST',{source_system:'SYNTHETIC_RESEARCH_ASSUMPTION'})).toContain('净预测');});

it('preserves top-level exception validation and separate native evidence statuses',async()=>{const v=input({kind:'exceptions'});v.data.validation={known_fail:true,business_status:'INVALID_SCHEDULE'};v.data.evidence={constraints:{status:'PRESENT'},problems:{status:'NOT_CHECKED'}};const b=await prepareExport(v);expect(b.metadata.known_business_fail).toBe(true);expect(b.metadata.validation).toEqual(v.data.validation);expect(b.metadata.native_evidence).toEqual(v.data.evidence)});

it('exports only four business scope keys even if a caller carries presentation props',async()=>{const v=input();v.scope={...scope,priorityEditor:null,profile:'same'} as typeof scope;const b=await prepareExport(v);expect(b.metadata.scope).toEqual(scope)});
