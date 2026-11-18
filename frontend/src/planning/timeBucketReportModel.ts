export type ReportMetric={id:string;label:string;kind:'flow'|'balance'|'snapshot';chart?:'bar'|'line';direction?:1|-1;unit?:string;default?:boolean;note?:string};
export type ReportBucket={id:string;label:string;start:string;end:string;partial?:boolean};
export type ReportObject={key:string;label:string;unit:string;dimensions?:Record<string,unknown>;cells:Record<string,Record<string,string|number|null|undefined>>;states?:Record<string,string>;sourceRows?:Record<string,unknown>};
export type TimeBucketReportData={scopeKey:string;buckets:ReportBucket[];objects:ReportObject[];metrics:ReportMetric[]};
export type ReportSelection={scopeKey:string;objectKey:string;bucketId:string;metricId:string};
export function reportSelectionSearch(previous:URLSearchParams,selection:ReportSelection){const next=new URLSearchParams(previous);next.set('bucket_selection',JSON.stringify(selection));return next}
export function reportObjectKey(d:Record<string,unknown>){return JSON.stringify(['item_id','location_id','customer_id','uom','endpoint','resource_id'].map(k=>d[k]??null))}
export function reportCellId(objectKey:string,bucketId:string,metricId:string){return JSON.stringify([objectKey,bucketId,metricId])}
export function readReportCell(object:ReportObject,bucket:string,metric:string):string|number|null{
 if(object.states?.[bucket]==='NOT_YET_KNOWN')return null;
 return object.cells[bucket]?.[metric]??null;
}
/** Pivot only. All balances, totals and classification belong to the source Owner. */
export function buildTimeBucketRows(report:TimeBucketReportData,metrics:ReportMetric[]){
 return report.objects.flatMap(object=>metrics.map(metric=>({id:reportCellId(object.key,'',metric.id),object,metric,values:Object.fromEntries(report.buckets.map(b=>[b.id,readReportCell(object,b.id,metric.id)]))})));
}
export function resolveReportSelection(selection:ReportSelection|null|undefined,report:TimeBucketReportData):ReportSelection|null{
 return selection&&selection.scopeKey===report.scopeKey&&report.objects.some(o=>o.key===selection.objectKey)&&report.buckets.some(b=>b.id===selection.bucketId)&&report.metrics.some(m=>m.id===selection.metricId)?selection:null;
}
export function reportValue(value:unknown){return value==null?'未知':String(value)}
/** Leave a complete first bucket beside pinned labels and the grid scrollbar. */
export function timeBucketColumnWidths(containerWidth?:number){
 if(containerWidth===undefined||!Number.isFinite(containerWidth)||containerWidth<=0||containerWidth>=580)return {object:245,metric:175,bucket:142};
 if(containerWidth>=480)return {object:190,metric:125,bucket:142};
 const available=containerWidth-18,object=containerWidth<360?Math.max(96,Math.min(128,Math.floor((available-90)*.54))):128;
 const metric=containerWidth<360?Math.max(84,available-90-object):108;
 return {object,metric,bucket:Math.max(90,Math.min(142,available-object-metric))};
}
export function timeBucketCellLabel(object:ReportObject,bucket:ReportBucket,metric:ReportMetric,value:unknown){
 return `${object.label} ${bucket.label} ${metric.label} ${reportValue(value)} ${metric.unit??object.unit} · ${bucket.start} — ${bucket.end}（不含结束）`;
}
