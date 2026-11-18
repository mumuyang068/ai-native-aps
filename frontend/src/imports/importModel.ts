import type {ImportTask,Location,PlanRecord,Issue} from '../api/contracts';
export function prioritizeIssues(issues:Issue[]):Issue[]{return issues.slice().sort((a,b)=>Number(a.severity==='WARNING')-Number(b.severity==='WARNING'))}
export function cellCoordinates(cell:string,headerRow:number){const m=/^([A-Z]+)(\d+)$/.exec(cell);if(!m)return null;let n=0;for(const c of m[1])n=n*26+c.charCodeAt(0)-64;return {row:Number(m[2])-headerRow-1,column:n-1}}
export function locateIssue(task:ImportTask,location:Location):{sheet:string;page:number;cell:string|null}|null{const sheet=task.source.sheets[location.sheet];if(!sheet)return null;const xy=location.cell?cellCoordinates(location.cell,sheet.header_row):null;return {sheet:location.sheet,page:Math.max(0,Math.floor((xy?.row??0)/50)),cell:location.cell??null}}
export function resolveVersionAction(versionId:string,plans:PlanRecord[]):{kind:'OPEN';planId:string}|{kind:'CREATE';versionId:string}{if(!/^DATA-[a-f0-9]{12}$/.test(versionId))throw Error('请选择已保存的数据版本。');const plan=plans.find(p=>p.data_version_id===versionId);return plan?{kind:'OPEN',planId:plan.id}:{kind:'CREATE',versionId}}
export function canSave(task:ImportTask):boolean{return !!(task.readiness.ready&&task.readiness.confirmed)}
export const stateText=(state:string)=>({PENDING_REVIEW:'等待人工核对',CONFIRMED_BLOCKED:'已核对 · 仍有阻断',READY_FOR_PLANNING:'校验通过 · 可以保存版本'}[state]??state);
export function columnName(n:number){let name='';for(;n;n=Math.floor((n-1)/26))name=String.fromCharCode(65+(n-1)%26)+name;return name}
