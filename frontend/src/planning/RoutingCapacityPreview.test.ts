import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {it,expect} from 'vitest';
import {RoutingCapacityPreview} from './RoutingCapacityPreview';
const a='2026-11-05T08:00:00+08:00',b='2026-11-05T18:00:00+08:00';
const task:any={id:'run:OP:3',quantity:'20',uom:'piece',operation_id:'SEW'};
const props:any={change:{resource_id:'M1',start_at:a,end_at:b,before_intervals:[{start_at:a,end_at:'2026-11-05T12:00:00+08:00'},{start_at:'2026-11-05T13:00:00+08:00',end_at:b}],after_intervals:[]},input:{site:{timezone:'Asia/Shanghai'}},tasks:[task],loads:[{id:'l',operation_task_id:task.id,resource_id:'M1',start_at:'2026-11-05T13:00:00+08:00',end_at:'2026-11-05T16:20:00+08:00'}],assignments:[{native_reference:'3',operator_id:'P1'}],eventStatus:'PRESENT',onTask:()=>{}};
it('shows the exact source calendar and baseline overlap without predicting candidate delivery',()=>{const html=renderToStaticMarkup(createElement(RoutingCapacityPreview,props));for(const text of ['原基线','候选','该期间无声明可用时段','20 件','M1 / P1','2026-11-05 16:20','不是候选排程'])expect(html).toContain(text);expect(html).not.toContain('满足30')});
it('does not match equal-time or other-machine loads and does not turn missing proof into empty',()=>{const noOverlap={...props,loads:[{...props.loads[0],resource_id:'M2'}]};expect(renderToStaticMarkup(createElement(RoutingCapacityPreview,noOverlap))).toContain('无重叠占用');const missing=renderToStaticMarkup(createElement(RoutingCapacityPreview,{...noOverlap,eventStatus:'NOT_COLLECTED'}));expect(missing).toContain('基线负荷证据不足');expect(missing).not.toContain('已查询，无重叠')});
