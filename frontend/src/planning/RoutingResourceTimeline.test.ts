import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {it,expect} from 'vitest';
import input from '../../../fixtures/p416-routing-capacity.json';
import {RoutingResourceTimeline} from './RoutingResourceTimeline';
import {routingProjection} from './ganttProjection';
const a='2026-11-04T08:00:00+08:00',b='2026-11-04T12:00:00+08:00';
const task:any={id:'run:OP:1',manufacture_id:'run:MO:1',operation_id:'P416-SEW',resource_id:'P416-SEW-M1',start_at:a,end_at:b,quantity:'4',uom:'piece'};
const resource=(id:string,status='EMPTY_CHECKED')=>({id,role:id==='P416-P1'?'OPERATOR':'MACHINE',capacity:'1',event_status:status,availability:[{start_at:a,end_at:b}]});
const props:any={input,data:{resource_analysis:{resources:[resource('P416-SEW-M2','NOT_COLLECTED')],intervals:[]}},tasks:[task],groupId:'run:MO:1',start:a,end:b,onlyGroup:false,onTask:()=>{},onResource:()=>{}};
it('does not turn absent native load evidence into an unused qualified machine',()=>{
 const result=routingProjection(input,props.data,props.tasks,a,b,false,props.groupId);
 expect(result.rows[0].detail).toContain('占用未核验');expect(result.rows[0].empty).toBe('占用证据不足');expect(result.intervals).toEqual([]);
 const html=renderToStaticMarkup(createElement(RoutingResourceTimeline,props));expect(html).toContain('占用证据不足');expect(html).not.toContain('合格但本次未采用');
});
it('draws named-person occupancy only from actual load records',()=>{
 const data={resource_analysis:{resources:[resource('P416-SEW-M1'),resource('P416-P1')],intervals:[{id:'load1',operation_task_id:task.id,resource_id:'P416-SEW-M1',start_at:a,end_at:b}]}};
 const result=routingProjection(input,data,[task],a,b,false);expect(result.intervals).toHaveLength(1);expect(result.intervals[0].rowId).toBe('P416-SEW-M1');expect(result.rows.find(r=>r.id==='P416-P1')?.empty).toBe('本运行已查询，无实际占用');
});
it('keeps same-parent sequence edges separate from order allocation',()=>{
 const next={...task,id:'run:OP:2',operation_id:'P416-PACK',resource_id:'P416-PACK-LINE',start_at:'2026-11-04T10:00:00+08:00'},tasks=[{...task,end_at:next.start_at},next,{...task,id:'run:other',manufacture_id:'run:other-parent'}];
 const data={resource_analysis:{resources:[resource('P416-SEW-M1'),resource('P416-PACK-LINE')],intervals:[]}};
 const result=routingProjection(input,data,tasks,a,b,false,props.groupId);expect(result.links).toHaveLength(1);expect(result.links[0]).toMatchObject({fromTask:task.id,toTask:next.id});
 expect(renderToStaticMarkup(createElement(RoutingResourceTimeline,{...props,tasks,data}))).toContain('不是订单分配');
});
