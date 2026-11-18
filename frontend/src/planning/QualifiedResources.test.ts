import {it,expect} from 'vitest';
import * as presentation from './presentationModel';
import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {QualifiedResources} from './QualifiedResources';
it('uses explicit native operation identities and keeps absent person evidence unknown',()=>{
 const read=(presentation as Record<string,any>).qualifiedResourceRows;
 expect(typeof read).toBe('function');
 const report={status:'PASS',assignments:[{native_reference:'2',operation_id:'PROCESS-1',machine_id:'M1',operator_id:'P1',required_skill_id:'SAWING',quantity:'4',start_at:'2026-11-02T10:20:00+08:00',end_at:'2026-11-02T11:00:00+08:00'}]};
 expect(read(report,'2')).toHaveLength(1);expect(read(report,'foreign')).toHaveLength(0);
 expect(read(undefined)).toEqual([]);
 expect(read({...report,assignments:[{...report.assignments[0],operator_id:null}]} )[0].operator_id).toBeNull();
});
it('keeps the equipment schedule from falsely showing staff as idle while preserving old profiles',()=>{
 const select=(presentation as Record<string,any>).equipmentScheduleResources;
 expect(typeof select).toBe('function');
 const rows=[{id:'M1',role:'MACHINE'},{id:'P1',role:'OPERATOR'},{id:'POOL',role:'OPERATOR_POOL'}];
 expect(select(rows,{rules:{manufacturing_profile:'machine-qualified-operator-v1'}})).toEqual([rows[0]]);
 expect(select(rows,{rules:{manufacturing_profile:'finished-garment-confirmed-v1'}})).toEqual([rows[0]]);
 expect(presentation.hasQualifiedResourceView({rules:{manufacturing_profile:'finished-garment-confirmed-v1'}})).toBe(true);
 expect(presentation.hasQualifiedResourceView({rules:{manufacturing_profile:'two-level-wip-v1'}})).toBe(false);
 expect(select(rows,{rules:{manufacturing_profile:'two-level-wip-v1'}})).toBe(rows);
});
it('renders declared QC and PACK equipment, named people and skills for the FG profile',()=>{
 const report={status:'PASS',assignments:[
  {native_reference:'5',operation_id:'QC-TB-M',machine_id:'QC-01',operator_id:'OP-04',required_skill_id:'QC',operator_pool_id:'P403-POOL',quantity:'2E+1',start_at:'2026-11-06T16:20:00+08:00',end_at:'2026-11-06T17:20:00+08:00'},
  {native_reference:'4',operation_id:'PACK-TB-M',machine_id:'PACK-01',operator_id:'OP-05',required_skill_id:'PACK',operator_pool_id:'P403-POOL',quantity:'2E+1',start_at:'2026-11-06T17:20:00+08:00',end_at:'2026-11-06T18:00:00+08:00'}]};
 const input={rules:{manufacturing_profile:'finished-garment-confirmed-v1'},routing_revisions:[{output_item_id:'FG',operations:[{id:'QC-TB-M'},{id:'PACK-TB-M'}]}],items:[{id:'FG',base_uom:'piece'}]};
 const html=renderToStaticMarkup(createElement(QualifiedResources,{report,input,timezone:'Asia/Shanghai'}));
 for(const text of ['QC-01','PACK-01','OP-04','OP-05','P403-POOL','20 件','2026-11-06 16:20','2026-11-06 18:00'])expect(html).toContain(text);
 expect(html).not.toContain('身份未核验');
});
it('shows the same machine, named person, skill and simultaneous window with business units',()=>{
 const report={status:'PASS',assignments:[{native_reference:'2',operation_id:'PROCESS-1',machine_id:'M1',operator_id:'P1',required_skill_id:'SAWING',operator_pool_id:'OPERATORS',quantity:'4',start_at:'2026-11-02T10:20:00+08:00',end_at:'2026-11-02T11:00:00+08:00'}]};
 const input={routing_revisions:[{output_item_id:'FG',operations:[{id:'PROCESS-1'}]}],items:[{id:'FG',base_uom:'piece'}]};
 const html=renderToStaticMarkup(createElement(QualifiedResources,{report,input,timezone:'Asia/Shanghai'}));
 for(const text of ['M1','P1','SAWING','4 件','2026-11-02 10:20','2026-11-02 11:00','不代表已下达生产'])expect(html).toContain(text);
});
it('keeps an unplanned result without fabricating staff or completion dates',()=>{
 const html=renderToStaticMarkup(createElement(QualifiedResources,{report:{status:'PASS',assignments:[]},input:{},timezone:'Asia/Shanghai'}));
 expect(html).toContain('本次没有排定工序');expect(html).not.toContain('同时占用结束');
 const missing=renderToStaticMarkup(createElement(QualifiedResources,{input:{},timezone:'Asia/Shanghai'}));
 expect(missing).toContain('证据不足');expect(missing).not.toContain('本次联合资源核验通过');
});
