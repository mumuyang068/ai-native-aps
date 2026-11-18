import {describe,it,expect} from 'vitest';
import {acceptResponse,ensureSessionId} from './scope';
import {request} from '../api/client';
const a={planId:'PLAN-aaaaaaaaaaaaaaaa',target:'baseline'},b={planId:'PLAN-bbbbbbbbbbbbbbbb',target:'baseline'};
describe('enterprise request ownership',()=>{
 it('refuses unscoped plan reads and writes without fetching',async()=>{
  for(const path of ['/v1/state','/v1/chat','/v1/plan-context','/v1/drafts/DRAFT-a/result','/v1/session-history'])
   await expect(request(path,{fetcher:async()=>{throw Error('must not fetch')}})).rejects.toMatchObject({code:'PLAN_SCOPE_REQUIRED'});
 });
 it('allows imports without inventing a plan identifier',async()=>{
  let url='';const value=await request('/v1/imports',{fetcher:async(input)=>{url=String(input);return Response.json([])}});
  expect(value).toEqual([]);expect(url).toBe('/v1/imports');
 });
 it('pins plan and session and keeps same-origin client header',async()=>{
  let url='',headers:Headers|undefined;
  await request('/v1/state',{scope:{...a,sessionId:'a'.repeat(32)},fetcher:async(input,init)=>{url=String(input);headers=new Headers(init?.headers);return Response.json({})}});
  expect(url).toContain('plan_id='+a.planId);expect(url).toContain('session_id='+'a'.repeat(32));expect(headers?.get('X-Planning-Client')).toBe('p1');
 });
 it('rejects a caller-provided plan query conflicting with scope',async()=>{
  await expect(request('/v1/state?plan_id='+b.planId,{scope:a})).rejects.toMatchObject({code:'PLAN_SCOPE_MISMATCH'});
 });
 it('does not retry an ambiguous write',async()=>{
  let calls=0;await expect(request('/v1/chat',{method:'POST',scope:a,body:{message:'查询'},fetcher:async()=>{calls++;throw new TypeError('offline')}})).rejects.toMatchObject({code:'WRITE_STATUS_UNKNOWN'});expect(calls).toBe(1);
 });
 it('explains an unavailable worker without retrying or claiming computation',async()=>{
  let calls=0;
  await expect(request('/v1/drafts/DRAFT-a/run',{method:'POST',scope:a,body:{},fetcher:async()=>{calls++;return Response.json({detail:{code:'COMPUTE_UNAVAILABLE',message:'COMPUTE_UNAVAILABLE'}},{status:503})}})).rejects.toMatchObject({status:503,code:'COMPUTE_UNAVAILABLE',message:'计算服务暂不可用，请保留当前草案并刷新核对运行状态。'});
  expect(calls).toBe(1);
 });
 it('rejects normalized paths outside the API boundary before fetching',async()=>{
  let calls=0;for(const path of ['/v1/../health','/v1/%2e%2e/health'])await expect(request(path,{fetcher:async()=>{calls++;return Response.json({})}})).rejects.toMatchObject({code:'INVALID_API_PATH'});
  expect(calls).toBe(0);
 });
 it('treats a lost write response body as unknown without resending',async()=>{
  let calls=0;await expect(request('/v1/chat',{method:'POST',scope:a,fetcher:async()=>{calls++;return new Response('incomplete',{status:200})}})).rejects.toMatchObject({code:'WRITE_STATUS_UNKNOWN'});expect(calls).toBe(1);
 });
 it('preserves typed server error and does not accept cross-origin endpoints',async()=>{
  await expect(request('/v1/imports',{fetcher:async()=>Response.json({detail:{code:'REVISION_CONFLICT',message:'请刷新核对'}},{status:409})})).rejects.toMatchObject({status:409,code:'REVISION_CONFLICT'});
  await expect(request('https://example.org/v1/state',{scope:a})).rejects.toMatchObject({code:'INVALID_API_PATH'});
 });
 it('rejects old plan, old target and old generation replies',()=>{
  expect(acceptResponse(b,2,{scope:a,generation:1})).toBe(false);
  expect(acceptResponse(a,2,{scope:{...a,target:'DRAFT-a'},generation:2})).toBe(false);
  expect(acceptResponse(a,2,{scope:a,generation:1})).toBe(false);
  expect(acceptResponse(a,2,{scope:a,generation:2})).toBe(true);
 });
 it('persists one session per plan and repairs invalid saved IDs',()=>{
  const map=new Map<string,string>(),store={getItem:(k:string)=>map.get(k)??null,setItem:(k:string,v:string)=>{map.set(k,v)}};
  const first=ensureSessionId(a.planId,store);expect(first).toMatch(/^[a-f0-9]{32}$/);
  expect(ensureSessionId(a.planId,store)).toBe(first);expect(ensureSessionId(b.planId,store)).not.toBe(first);
  map.set('planning-ui-session:'+a.planId,'broken');expect(ensureSessionId(a.planId,store)).toMatch(/^[a-f0-9]{32}$/);
 });
});
