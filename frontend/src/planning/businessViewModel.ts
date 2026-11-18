import type {Dict} from '../api/contracts';
import {addDays} from './timeRange';
export type BusinessScope={versionId:string;planId?:string};
export type BusinessSelection={day?:string;overdue?:boolean;section?:string};
export function businessDetailQuery(filters:Record<string,string>,selection:BusinessSelection,offset:number,range?:{start:string;end:string}):Record<string,string|number>{
 return {...filters,...(!selection.overdue&&range?{start:range.start,end:range.end}:{}),section:selection.section??'orders',...(selection.day?{start:selection.day,end:addDays(selection.day,1)}:{}),...(selection.overdue?{status:'overdue'}:{}),offset,page_size:20};
}
export function businessViewUrl(scope:BusinessScope,query:Record<string,string|number>){
 const params=new URLSearchParams(Object.entries(query).map(([k,v])=>[k,String(v)]));
 if(scope.planId)params.set('data_version_id',scope.versionId);
 return (scope.planId?'/v1/enterprise-plans/'+encodeURIComponent(scope.planId):'/v1/data-versions/'+encodeURIComponent(scope.versionId))+'/business-view?'+params;
}
export function verifyBusinessView(value:Dict,scope:BusinessScope){
 if(value.schema_version!=='business-view/1'||value.fact_kind!=='CONFIRMED_BUSINESS_FACTS'||value.identity?.data_version_id!==scope.versionId||value.identity.run_id!==null||scope.planId&&value.identity.plan_id!==scope.planId)throw Error('业务资料响应与当前企业版本不一致，已停止展示。');
 return value;
}
export function businessCalendarWindow(range:{start:string;end:string},width:number,requested:string){
 const month=(s:string)=>Number(s.slice(0,4))*12+Number(s.slice(5,7))-1;
 const id=(n:number)=>Math.floor(n/12)+'-'+String(n%12+1).padStart(2,'0');
 const first=month(range.start),last=month(addDays(range.end,-1)),span=width>=700?12:width>=440?6:3;
 const start=Math.max(first,Math.min(last,requested?month(requested):first)),from=id(start)+'-01',to=addDays(id(Math.min(last+1,start+span))+'-01',-1);
 const dates:string[]=[];for(let d=from;d<=to;d=addDays(d,1))dates.push(d);
 const weeks=Math.ceil((dates.length+(new Date(from+'T12:00:00Z').getUTCDay()+6)%7)/7);
 return {from,to,span,dates,cellSize:Math.max(10,Math.min(20,Math.floor((width-48)/weeks))),previous:start>first?id(Math.max(first,start-span)):null,next:start+span<=last?id(start+span):null};
}
