import type {Dict} from '../api/contracts';
export function replaceEdit(edits:Dict[],value:Dict):Dict[]{const i=edits.findIndex(e=>e.kind===value.kind&&e.reference===value.reference);return i<0?[...edits,value]:edits.map((e,n)=>n===i?value:e)}
export function removeEdit(edits:Dict[],kind:string,reference:string):Dict[]{return edits.filter(e=>e.kind!==kind||e.reference!==reference)}
export function editorIdentityMatches(actual:Dict,expected:Dict):boolean{return ['plan_id','data_version_id','baseline_run_id','run_id','target'].every(k=>actual?.[k]===expected[k])}
export function editorScopeRequest(identity:Dict):Record<string,string>{return Object.fromEntries(['data_version_id','baseline_run_id','run_id','target'].map(k=>[k,identity[k]]))}
