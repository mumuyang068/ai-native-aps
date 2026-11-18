import type {PlanScope} from '../app/scope';
export class ApiError extends Error {
 constructor(public status:number,public code:string,message:string){super(message);this.name='ApiError'}
}
type Options={method?:string;body?:unknown;scope?:PlanScope;signal?:AbortSignal;fetcher?:typeof fetch};
export async function request<T=unknown>(path:string,options:Options={}):Promise<T> {
 if(!path.startsWith('/v1/')||path.includes('\\')||path.includes('#'))throw new ApiError(0,'INVALID_API_PATH','请求地址无效。');
 const url=new URL(path,'http://local.invalid'),scope=options.scope;
 if(!url.pathname.startsWith('/v1/'))throw new ApiError(0,'INVALID_API_PATH','请求地址无效。');
 const required=/^\/v1\/(state|plan-context|order-evidence|chat|session-history|drafts|diagnoses)(\/|$)/.test(url.pathname);
 if(required&&(!scope||!/^PLAN-[a-f0-9]{16}$/.test(scope.planId)))throw new ApiError(0,'PLAN_SCOPE_REQUIRED','请先选择一份企业计划。');
 if(scope){
  if(url.searchParams.has('plan_id')&&url.searchParams.get('plan_id')!==scope.planId)throw new ApiError(0,'PLAN_SCOPE_MISMATCH','计划身份不一致，请重新选择。');
  url.searchParams.set('plan_id',scope.planId);
  if(scope.sessionId&&['/v1/state','/v1/session-history'].includes(url.pathname))url.searchParams.set('session_id',scope.sessionId);
 }
 const method=options.method??'GET',headers=new Headers({'X-Planning-Client':'p1'});
 let body:BodyInit|undefined;
 if(options.body!==undefined){if(options.body instanceof Blob){body=options.body;headers.set('Content-Type','application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')}else{body=JSON.stringify(options.body);headers.set('Content-Type','application/json')}}
 let response:Response;
 try{response=await(options.fetcher??fetch)(url.pathname+url.search,{method,headers,body,signal:options.signal})}
 catch(error){if(error instanceof DOMException&&error.name==='AbortError')throw error;throw new ApiError(0,method==='GET'?'NETWORK_UNAVAILABLE':'WRITE_STATUS_UNKNOWN',method==='GET'?'暂时无法读取服务，请重试。':'连接中断，操作状态待核实；请先刷新核对，勿重复提交。')}
 let data:any;try{data=await response.json()}catch{throw new ApiError(response.status,method==='GET'?'INVALID_RESPONSE':'WRITE_STATUS_UNKNOWN',method==='GET'?'服务响应不完整，请刷新核对当前状态。':'操作响应不完整，状态待核实；请先刷新核对，勿重复提交。')}
 if(!response.ok){const code=data.detail?.code??'REQUEST_FAILED',raw=data.detail?.message??(code==='TASK_LOCAL_AUTHENTICATION_REQUIRED'?'研究确认会话已到期，请刷新页面重新验证身份；原操作记录保留。':'请求未完成，请核对当前状态。'),message=code==='COMPUTE_UNAVAILABLE'?'计算服务暂不可用，请保留当前草案并刷新核对运行状态。':raw.startsWith(code+':')?raw.slice(code.length+1).trim():raw;throw new ApiError(response.status,code,message)}
 return data as T;
}
