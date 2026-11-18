import {prepareExport,type ExportInput} from './reportExport';

export type ExportDownloadLinks={csv:{href:string;download:string};metadata:{href:string;download:string}};
type ExportBundle=Awaited<ReturnType<typeof prepareExport>>;
export type ExportLinkRuntime={prepare:(input:ExportInput)=>Promise<ExportBundle>;createUrl:(blob:Blob)=>string;revokeUrl:(url:string)=>void};
const browserRuntime:ExportLinkRuntime={prepare:prepareExport,createUrl:blob=>URL.createObjectURL(blob),revokeUrl:url=>URL.revokeObjectURL(url)};

/** Prepare one paired artifact before a user clicks. Cancellation revokes every owned URL. */
export function beginExportDownloadLinks(input:ExportInput,publish:(links:ExportDownloadLinks)=>void,fail:(error:unknown)=>void,runtime:ExportLinkRuntime=browserRuntime){
 let active=true;const urls:string[]=[];
 function release(){for(const url of urls.splice(0))runtime.revokeUrl(url)}
 const settled=(async()=>{try{
  const output=await runtime.prepare(input);if(!active)return;
  const name=[input.kind,input.scope.planId,input.scope.target,input.scope.runId].join('-').replace(/[^\p{L}\p{N}_.-]/gu,'_');
  const csvUrl=runtime.createUrl(new Blob([output.csv],{type:'text/csv;charset=utf-8'}));urls.push(csvUrl);
  const metadataUrl=runtime.createUrl(new Blob([JSON.stringify(output.metadata,null,2)+'\n'],{type:'application/json;charset=utf-8'}));urls.push(metadataUrl);
  if(active)publish({csv:{href:csvUrl,download:name+'.csv'},metadata:{href:metadataUrl,download:name+'.metadata.json'}});else release();
 }catch(error){release();if(active)fail(error)}})();
 return {settled,cancel:()=>{active=false;release()}};
}
