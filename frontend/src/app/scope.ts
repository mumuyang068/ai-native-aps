export type PlanScope={planId:string;target:string;sessionId?:string};
export function acceptResponse(active:PlanScope,generation:number,incoming:{scope:PlanScope;generation:number}):boolean {
 return active.planId===incoming.scope.planId&&active.target===incoming.scope.target&&active.sessionId===incoming.scope.sessionId&&generation===incoming.generation;
}
export function ensureSessionId(planId:string,storage:Pick<Storage,'getItem'|'setItem'>=localStorage):string {
 const key='planning-ui-session:'+planId,previous=storage.getItem(key);
 if(previous&&/^[a-f0-9]{32}$/.test(previous))return previous;
 const value=crypto.randomUUID().replaceAll('-','');storage.setItem(key,value);return value;
}
