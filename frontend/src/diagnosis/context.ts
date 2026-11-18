export type SelectionContext={type:'order'|'operation'|'resource';id:string;order_id?:string};
export type DiagnosisScope={plan_id:string;target:string;run_id:string;result_hash:string;data_version_id?:string};
export function diagnosisMatches(task:{scope:DiagnosisScope},scope:DiagnosisScope){return task.scope.plan_id===scope.plan_id&&task.scope.target===scope.target&&task.scope.run_id===scope.run_id&&task.scope.result_hash===scope.result_hash&&task.scope.data_version_id===scope.data_version_id}
export function selectionContext(key:string,object:{kind:string;id:string}|null,origin:{key:string;id:string}|null):SelectionContext|null{
 if(!object)return null;
 if(object.kind==='order')return {type:'order',id:object.id,order_id:object.id};
 if(object.kind==='task')return {type:'operation',id:object.id,...(origin?.key===key?{order_id:origin.id}:{})};
 return null;
}
