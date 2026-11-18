import type {Dict} from '../api/contracts';
export function homeHistoryGroups(value:Dict,scope:{planId?:string;versionId:string},enterprise?:string){
 if(value.identity?.plan_id!==scope.planId||value.identity?.data_version_id!==scope.versionId||value.identity?.run_id!=null||enterprise&&value.identity?.enterprise_id!==enterprise)throw Error('历史趋势与当前企业或版本不一致，已停止展示。');
 return (value.groups??[]).filter((g:Dict)=>g.dimension?.uom&&g.cells?.length);
}
export function historyChartRows(group:Dict|undefined,buckets:Dict[],observedThrough?:string){
 return buckets.map((b,i)=>{const c=group?.cells?.[i];const partial=b.partial||observedThrough&&Date.parse(observedThrough)<Date.parse(b.end);const number=(name:string)=>c?.status==='KNOWN'&&c[name]!=null&&Number.isFinite(Number(c[name]))?Number(c[name]):null;return {id:b.id,label:(b.label??b.start.slice(0,7))+(partial?' *':''),created:number('new_quantity'),shipped:number('shipped'),balance:number('ending_open')};});
}
export function homePlanLink(planId:string,values:Record<string,string>){return '/plans/'+encodeURIComponent(planId)+'?'+new URLSearchParams({target:'baseline',...values});}
export function homeHistoryLink(planId:string,dimension?:Dict){return homePlanLink(planId,{analysis:'history-demand',...Object.fromEntries(['item_id','location_id','customer_id'].filter(k=>dimension?.[k]).map(k=>['history_'+k.replace('_id',''),dimension![k]]))});}
export function homeTaskLink(planId:string,task:Dict){return homePlanLink(planId,{analysis:'cockpit',target:task.identity.target,assistant:'1',assistant_task:task.id});}
