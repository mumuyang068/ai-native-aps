import {describe,expect,it} from 'vitest';
import {createElement} from 'react';
import {gridEvidenceState,gridScopeKey,gridRowIds,gridSelectedId,gridColumnValue,gridSearchText,type GridScope} from './ProfessionalGrid';
import {routingOperationRows} from '../planning/ProfessionalGrids';

const scope:GridScope={planId:'PLAN-A',versionId:'DATA-A',runId:'API-A',target:'baseline'};
describe('专业网格身份与证据契约',()=>{
 it('计划、版本、运行或方案变化均隔离选择',()=>{
  const selection={scope,id:'OP-1'};
  expect(gridSelectedId(scope,selection)).toBe('OP-1');
  for(const field of ['planId','versionId','runId','target'] as const){
   expect(gridScopeKey({...scope,[field]:scope[field]+'-other'})).not.toBe(gridScopeKey(scope));
   expect(gridSelectedId({...scope,[field]:scope[field]+'-other'},selection)).toBeNull();
  }
 });
 it('稳定身份拒绝空值和重复，不使用相同显示名称凑行键',()=>{
  const sameNames=[{id:'PO-1',name:'采购'},{id:'PO-2',name:'采购'}];
  expect(gridRowIds(sameNames,r=>r.id)).toEqual(['PO-1','PO-2']);
  expect(()=>gridRowIds([{id:''}],r=>r.id)).toThrow(/身份/);
  expect(()=>gridRowIds([{id:'PO-1'},{id:'PO-1'}],r=>r.id)).toThrow(/重复/);
 });
 it('缺少运行身份时拒绝借用其他运行',()=>{
  expect(()=>gridScopeKey({...scope,runId:''})).toThrow(/运行/);
 });
 it('证据缺失、无效、已查为空与加载有独立状态，已有硬违规优先',()=>{
  expect(gridEvidenceState('PRESENT',0).kind).toBe('empty');
  expect(gridEvidenceState('EMPTY_CHECKED',0).kind).toBe('empty');
  expect(gridEvidenceState('NOT_COLLECTED',0).kind).toBe('unavailable');
  expect(gridEvidenceState('UNREADABLE',0).kind).toBe('unavailable');
  expect(gridEvidenceState('INVALID_EVIDENCE',3).kind).toBe('unavailable');
  expect(gridEvidenceState('NOT_CHECKED',3).kind).toBe('incomplete');
  expect(gridEvidenceState('LOADING',0).kind).toBe('loading');
  expect(gridEvidenceState('FAIL',0).kind).toBe('fail');
 });
 it('数值使用原始数量排序，未知不转为0；展示片段可保留但不猜数值',()=>{
  const column={id:'quantity',title:'数量',value:(r:{q:string|null})=>r.q===null?'未核验':r.q+' 米',sortValue:(r:{q:string|null})=>r.q===null?NaN:Number(r.q),numeric:true};
  expect(gridColumnValue(column,{q:'40'})).toBe(40);
  expect(gridColumnValue(column,{q:'8'})).toBe(8);
  expect(gridColumnValue(column,{q:null})).toBeNull();
  expect(gridColumnValue({id:'v',title:'值',value:()=>createElement('span',null,'未核验')},{})).toBeNull();
 });
 it('筛选可找到实际显示的中文、日期与零值，不用原始时间戳代替用户文字',()=>{
  expect(gridSearchText(createElement('span',null,'采购 A',createElement('small',null,'来源 X')))).toBe('采购 A 来源 X');
  expect(gridSearchText('2026-11-05 09:00')).toBe('2026-11-05 09:00');
  expect(gridSearchText(0)).toBe('0');
  expect(gridSearchText(null)).toBe('');
 });
});

describe('真实制造工序网格投影',()=>{
 it('保留原任务身份、数量和时刻，设备与具名人员来自精确原生assignment',()=>{
  const task={id:'API-A:OP:10',operation_id:'SEW',manufacture_id:'MO-A',quantity:'16',uom:'piece',start_at:'2026-11-04T07:20:00Z',end_at:'2026-11-04T10:00:00Z',resource_id:'POOL',name:'SEW',resourceName:'POOL',itemId:'FG',recipeId:'R',orderAllocation:null} as any;
  const rows=routingOperationRows([task],[{native_reference:'10',machine_id:'M1',operator_id:'P1'}]);
  expect(rows[0]).toMatchObject({id:task.id,quantity:'16',start_at:task.start_at,end_at:task.end_at,machine_id:'M1',operator_id:'P1'});
  expect(routingOperationRows([task],[])[0]).toMatchObject({machine_id:null,operator_id:null});
  expect(task.resource_id).toBe('POOL');
 });
});
