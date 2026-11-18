import {describe,it,expect,vi} from 'vitest';
import {createHash} from 'node:crypto';
import {beginExportDownloadLinks,type ExportDownloadLinks,type ExportLinkRuntime} from './exportDownloadLinks';
import {prepareExport,type ExportInput} from './reportExport';

function input():ExportInput{return {scope:{planId:'P',versionId:'V',runId:'R',target:'baseline'},data:{identity:{plan_id:'P',data_version_id:'V',run_id:'R',target:'baseline',profile:'enterprise-accessory-supply-v1'},timezone:'Asia/Shanghai',evidence_files:[{artifact:'PlanResult.json',sha256:'a'.repeat(64)}]},status:'PRESENT',kind:'demand',rows:[{item_id:'FG'}],fields:[{key:'item_id',label:'物料',meaning:'原始ID'}],filters:{query:'FG'},sourceUrl:'/reports'}}
function runtime(prepare:ExportLinkRuntime['prepare']=prepareExport){const blobs:Blob[]=[],revokeUrl=vi.fn();return {blobs,revokeUrl,prepare,createUrl:(blob:Blob)=>{blobs.push(blob);return 'blob:test-'+blobs.length}}}
describe('pre-generated native download link lifecycle',()=>{
 it('publishes two native URLs from one paired generation with the exact CSV SHA and revokes both on cleanup',async()=>{
  const services=runtime(),publish=vi.fn(),fail=vi.fn(),job=beginExportDownloadLinks(input(),publish,fail,services);await job.settled;
  expect(fail).not.toHaveBeenCalled();expect(publish).toHaveBeenCalledTimes(1);expect(services.blobs).toHaveLength(2);
  const links=publish.mock.calls[0][0] as ExportDownloadLinks;expect(links.csv).toEqual({href:'blob:test-1',download:'demand-P-baseline-R.csv'});expect(links.metadata.download).toBe('demand-P-baseline-R.metadata.json');
  const bytes=Buffer.from(await services.blobs[0].arrayBuffer()),metadata=JSON.parse(await services.blobs[1].text());expect(bytes.subarray(0,3)).toEqual(Buffer.from([0xef,0xbb,0xbf]));expect(metadata.csv.sha256).toBe(createHash('sha256').update(bytes).digest('hex'));
  job.cancel();job.cancel();expect(services.revokeUrl.mock.calls).toEqual([['blob:test-1'],['blob:test-2']]);
 });
 it('discards an old asynchronous generation without creating or publishing URLs after scope/filter cleanup',async()=>{
  let finish!:(output:Awaited<ReturnType<typeof prepareExport>>)=>void;const pending=new Promise<Awaited<ReturnType<typeof prepareExport>>>(resolve=>{finish=resolve}),services=runtime(()=>pending),publish=vi.fn(),fail=vi.fn();
  const job=beginExportDownloadLinks(input(),publish,fail,services);job.cancel();finish(await prepareExport(input()));await job.settled;
  expect(services.blobs).toHaveLength(0);expect(publish).not.toHaveBeenCalled();expect(fail).not.toHaveBeenCalled();
 });
 it('revokes a partially created pair and reports failure without a broken link',async()=>{
  const services=runtime(),publish=vi.fn(),fail=vi.fn();services.createUrl=()=>{if(!services.blobs.length){services.blobs.push(new Blob());return 'blob:first'}throw Error('blob allocation failed')};
  const job=beginExportDownloadLinks(input(),publish,fail,services);await job.settled;expect(publish).not.toHaveBeenCalled();expect(fail).toHaveBeenCalledTimes(1);expect(services.revokeUrl).toHaveBeenCalledWith('blob:first');job.cancel();expect(services.revokeUrl).toHaveBeenCalledTimes(1);
 });
 it('never allocates downloadable links for unverified evidence',async()=>{
  const value=input();value.status='NOT_COLLECTED';const services=runtime(),publish=vi.fn(),fail=vi.fn(),job=beginExportDownloadLinks(value,publish,fail,services);await job.settled;
  expect(services.blobs).toHaveLength(0);expect(publish).not.toHaveBeenCalled();expect(fail).toHaveBeenCalledTimes(1);
 });
});
