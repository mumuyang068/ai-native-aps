export type SupplyWorkScope={planId:string;versionId:string;runId:string;target:string};
const transient=['purchase_doc','purchase_group','supply_doc_period','supply_bucket','analysis_calendar_window','supply_doc','supply_event','supply_scope','analysis_event','analysis_task','run_id','data_version_id'];
/** A deliberate main-navigation entry starts a new investigation origin. */
export function enterSupplyWork(previous:URLSearchParams){
 const next=new URLSearchParams(previous);next.set('analysis','supply-work');
 next.delete('supply_return');next.delete('supply_return_scope');return next;
}
/** Store only route context. A new scenario must read its own native objects. */
export function rememberSupplyWork(previous:URLSearchParams,scope:SupplyWorkScope){
 const next=new URLSearchParams(previous);
 if(!next.has('supply_return')){
  next.set('supply_return',previous.toString());
  next.set('supply_return_scope',JSON.stringify([scope.planId,scope.versionId,scope.runId,scope.target]));
 }
 return next;
}
export function returnSupplyWork(previous:URLSearchParams,scope:SupplyWorkScope):URLSearchParams|null{
 let identity:unknown;
 try{identity=JSON.parse(previous.get('supply_return_scope')??'null')}catch{return null}
 if(!Array.isArray(identity)||identity.length!==4||identity[0]!==scope.planId||identity[1]!==scope.versionId)return null;
 const next=new URLSearchParams(previous.get('supply_return')??'');
 if(identity[2]!==scope.runId||identity[3]!==scope.target)for(const key of transient)next.delete(key);
 next.set('target',scope.target);next.set('analysis','supply-work');next.delete('supply_return');next.delete('supply_return_scope');return next;
}
