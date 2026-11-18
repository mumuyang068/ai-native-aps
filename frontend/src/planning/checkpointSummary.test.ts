import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {describe,it,expect} from 'vitest';
import {CheckpointSummary} from './CheckpointSummary';
const summary={checkpoint_id:'CP-1',parent_plan_id:'PLAN-source',parent_data_version_id:'DATA-source',cutoff_at:'2026-11-02T09:50:00+08:00',cutoff_sequence:4,completed_task:{reference:'EXEC-CUT-10',operation_id:'CUT',quantity:'10',uom:'set'},remaining_demands:[{order_line_id:'LINE-1',original_quantity:'10',actually_delivered:'2',remaining_quantity:'8',uom:'piece'}]};
describe('execution prefix and future plan remain distinct',()=>{
 it('links the source plan and shows actual delivery deducted from original demand',()=>{
  const html=renderToStaticMarkup(createElement(CheckpointSummary,{summary,validation:{status:'PASS'},timezone:'Asia/Shanghai'}));
  expect(html).toContain('#/plans/PLAN-source?target=baseline');expect(html).toContain('DATA-source');
  expect(html).toContain('EXEC-CUT-10');expect(html).toContain('10 − 2 = 8');
  expect(html).toContain('历史领料和入账已包含在新期初中');
 });
 it('never certifies missing future evidence as a complete two-period check',()=>{
  const html=renderToStaticMarkup(createElement(CheckpointSummary,{summary,validation:{status:'NOT_CHECKED'},timezone:'Asia/Shanghai'}));
  expect(html).toContain('尚未完成核验');expect(html).not.toContain('已核对执行前缀');
 });
});
