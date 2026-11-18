import type {HistoryMessage} from '../api/contracts';
export function completedTurn(messages:HistoryMessage[],afterId:string|null,message:string){
 const index=afterId===null?-1:messages.findIndex(m=>m.id===afterId);
 if(afterId!==null&&index<0)return false;
 const tail=messages.slice(index+1),user=tail.findIndex(m=>m.role==='user'&&m.text===message);
 return user>=0&&tail.slice(user+1).some(m=>m.role==='assistant');
}
