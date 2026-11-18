import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {describe,it,expect} from 'vitest';
import {ExternalSchedule} from './ExternalSchedule';
describe('native target schedule observation',()=>{
 it('keeps schedule, unsupported people and stock actuals separate',()=>{
  const html=renderToStaticMarkup(createElement(ExternalSchedule,{value:{observed_at:'2026-10-29T09:00:00Z',assessment:{status:'NOT_REPRESENTABLE',document_status:'DOCUMENT_ACCEPTED',operation_status:'OPERATION_ACCEPTED',schedule_status:'TARGET_ADJUSTED',actual_execution:'NOT_EXECUTED',finished_received_quantity:'0',operations:[],differences:[{field:'end_at',aps:'17:00',odoo:'09:00'}],not_representable:[{kind:'NAMED_PERSON_TIME',operator_id:'P1',aps_operation_id:'SEW'}]}}}));
  expect(html).toContain('目标已调整计划');expect(html).toContain('尚未执行');expect(html).toContain('P1');expect(html).toContain('17:00');expect(html).not.toContain('发布成功');
 });
});
