import type {Dict} from '../api/contracts';

/** The query end is exclusive; the source's observed-through instant is not. */
export function historyQueryRange(context:Dict,search:URLSearchParams):{start:string;end:string}{
 const grain=search.get('history_grain')??'month';
 return {start:search.get('history_start')??context.scope.origin,
  end:search.get('history_end')??context.query_end_by_grain?.[grain]??context.scope.end};
}

/** An empty unrelated identity must not hide the actual lot or source event. */
export function historyRecordReference(row:Dict):string{
 return ['object_id','order_id','demand_id','lot_id','event_id','resource_id','operation_id','item_id']
  .map(key=>row[key]).find(value=>typeof value==='string'&&value.trim())??'未提供引用';
}
