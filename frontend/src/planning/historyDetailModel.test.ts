import {expect,it} from 'vitest';
import {historyRecordReference,historyQueryRange} from './historyDetailModel';

it('keeps an observed event visible when unrelated object ids are empty strings',()=>{
 expect(historyRecordReference({object_id:'',order_id:'',demand_id:'',lot_id:'LOT-1',event_id:'EV-1'})).toBe('LOT-1');
 expect(historyRecordReference({order_id:'',lot_id:'',event_id:'EV-2'})).toBe('EV-2');
 expect(historyRecordReference({object_id:'ORDER-1',event_id:'EV-1'})).toBe('ORDER-1');
 expect(historyRecordReference({})).toBe('未提供引用');
});

it('uses the Owner calendar end by grain so the inclusive known endpoint is visible',()=>{
 const context={scope:{origin:'2026-11-02T00:00:00+08:00',end:'2026-11-18T09:02:00+08:00'},query_end_by_grain:{
  day:'2026-11-19T00:00:00+08:00',week:'2026-11-23T00:00:00+08:00',month:'2026-12-01T00:00:00+08:00'}};
 for(const grain of ['day','week','month'])expect(historyQueryRange(context,new URLSearchParams({history_grain:grain})))
  .toEqual({start:context.scope.origin,end:context.query_end_by_grain[grain as keyof typeof context.query_end_by_grain]});
 expect(historyQueryRange(context,new URLSearchParams({history_end:'2026-11-10T00:00:00+08:00'})).end).toBe('2026-11-10T00:00:00+08:00');
 expect(historyQueryRange({scope:context.scope},new URLSearchParams()).end).toBe(context.scope.end);
 expect(context.scope.end).toBe('2026-11-18T09:02:00+08:00');
});
