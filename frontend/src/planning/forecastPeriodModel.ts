import type {Dict} from '../api/contracts';
import {forecastSeries} from './intelligenceWorkbenchModel';
export function forecastPeriodMatrix(rows:Dict[],selectedId:string){
 const series=forecastSeries(rows,[],selectedId);if(!series)return null;
 return {...series,buckets:series.forecast.map(r=>({id:r.id,label:r.bucket_start.slice(0,10),end:r.bucket_end})),values:Object.fromEntries(['baseline','override','gross','consumed','net'].map(metric=>[metric,Object.fromEntries(series.forecast.map(r=>[r.id,metric==='baseline'?r.baseline_quantity??r.statistical_quantity??(r.forecast_origin==='STATISTICAL_BASELINE'?r.quantity??r.gross_quantity:null):metric==='override'?r.override_quantity??(r.forecast_origin==='MANUAL_OVERRIDE'?r.gross_quantity:null):metric==='gross'?r.gross_quantity??null:r.netting?.[metric+'_quantity']??null]))]))};
}
export function forecastEditRequest(currentVersion:string,editorVersion:string,rows:Dict[],rowId:string,quantity:string){
 if(!currentVersion||currentVersion!==editorVersion)throw Error('预测版本已变化，请重新选择原始期间。');
 if(!rows.some(r=>r.id===rowId))throw Error('只能覆盖本版本的原始期间，不能写入聚合期间。');
 if(!/^\d+(?:\.\d+)?$/.test(quantity))throw Error('覆盖数量必须为非负十进制数。');
 return {row_id:rowId,quantity};
}
