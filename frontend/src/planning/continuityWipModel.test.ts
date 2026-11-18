import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {describe,expect,it} from 'vitest';
import type {ContinuityWipContext,PlanContext} from '../api/contracts';
import {PlanConditions} from './PlanConditions';
import {continuityWipModel} from './continuityWipModel';

function source():ContinuityWipContext{return {schema_version:'continuity-wip-read/1',status:'PRESENT',audit_status:'PASS',foundation_audit_status:'PASS',run_id:'RUN-1',source_as_of:'2026-11-03T03:00:00+08:00',continuation_certification:'NOT_CERTIFIED',execution_authorized:false,
 execution_protection:{owner:'enterprise_sim.live.check_manufacture_release',status:'SERVER_ENFORCED',block_codes:['WIP_REQUIRES_CERTIFIED_CONTINUATION','NATIVE_MANUFACTURE_ALREADY_ADOPTED']},
 rows:[{job_id:'JOB-WIP-1',order_id:'SO-1',opening:20,used:0,remaining:20,evidence_status:'AUDITED',continuation_status:'NOT_USED',completed_prefix:[{operation_id:'CUT-TB-M',quantity:'20',status:'COMPLETED'}],issued_materials:[{id:'ISSUE-1',lot_id:'LOT-RAW-1',quantity:'20',unit:'米'}],source:{owner:'p22.continuity_audit.audit',run_id:'RUN-1',pointer:'/interpretation/foundation_validation/checks/continuity/wip/0',sha256:'a'.repeat(64)}}]}}

describe('same-run continuity WIP presentation',()=>{
 it('shows original balances and warns when the native plan did not use the WIP',()=>{
  const raw=source(),frozen=JSON.stringify(raw),model=continuityWipModel(raw);
  expect(model?.jobs[0]).toMatchObject({jobId:'JOB-WIP-1',orderId:'SO-1',opening:'20',used:'0',remaining:'20',status:'原生规划尚未采用该 WIP'});
  expect(model?.certification).toBe('WIP 续排执行仍未认证');expect(JSON.stringify(raw)).toBe(frozen);
 });
 it('does not convert absent quantities to zero or claim native usage is actual production',()=>{
  const raw=source();Object.assign(raw.rows[0],{used:null,evidence_status:'NOT_COLLECTED',continuation_status:'NOT_CHECKED'});
  expect(continuityWipModel(raw)?.jobs[0].used).toBe('未提供');
  Object.assign(raw.rows[0],{used:20,remaining:0,evidence_status:'AUDITED',continuation_status:'USED_IN_NATIVE_PLAN'});
  expect(continuityWipModel(raw)?.jobs[0].status).toBe('原生规划已使用该 WIP，实际续排完成尚未认证');
 });
 it('keeps missing and mismatched audit evidence distinct',()=>{
  expect(continuityWipModel(undefined)).toBeNull();
  const raw=source();Object.assign(raw,{status:'INVALID_EVIDENCE',rows:[]});
  expect(continuityWipModel(raw)?.evidence).toBe('当前运行的 WIP 审计证据不完整');
  expect(continuityWipModel(raw)?.countLabel).toBe('记录未提供');
 });
 it('renders the original work order, source prefix, issues and server protection in PlanConditions',()=>{
  const context={effective_constraints:[],resource_calendars:{resources:[],calendars:[]},overtime:{policy:'EXPLICITLY_FORBIDDEN'},timezone:'Asia/Shanghai',continuity_wip:source()} as unknown as PlanContext;
  const html=renderToStaticMarkup(createElement(PlanConditions,{context,input:{}}));
  for(const text of ['在制品续排','JOB-WIP-1','SO-1','期初 WIP','原生已使用','原生剩余','CUT-TB-M','LOT-RAW-1','20 米','原生规划尚未采用该 WIP','WIP 续排执行仍未认证','采纳和执行前','阻止重复领料或重复制造','RUN-1'])expect(html).toContain(text);
  expect(html).not.toContain('续排已完成');
 });
});
