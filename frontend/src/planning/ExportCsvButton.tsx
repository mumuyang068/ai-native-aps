import {useEffect,useRef,useState,type MouseEvent} from 'react';
import {Button,ErrorNote} from '../components/Controls';
import {verifyExport,type ExportInput} from './reportExport';
import {beginExportDownloadLinks,type ExportDownloadLinks} from './exportDownloadLinks';
export function ExportCsvButton({input}:{input:ExportInput}){
 const [artifact,setArtifact]=useState<{key:string;links:ExportDownloadLinks}|null>(null),[failure,setFailure]=useState<{key:string;message:string}|null>(null),[retry,setRetry]=useState(0),owner=useRef('');
 const key=JSON.stringify(input);owner.current=key;
 let refusal='';try{verifyExport(input)}catch(e){refusal=e instanceof Error?e.message:'当前来源不能导出。'}
 useEffect(()=>{
  owner.current=key;setArtifact(null);setFailure(null);
  const generation=!refusal?beginExportDownloadLinks(input,links=>{if(owner.current===key)setArtifact({key,links})},error=>{if(owner.current===key)setFailure({key,message:error instanceof Error?error.message:'下载文件生成失败，请重试。'})}):null;
  return()=>{generation?.cancel();if(owner.current===key)owner.current=''};
 },[key,retry]);
 const links=!refusal&&artifact?.key===key?artifact.links:null,error=failure?.key===key?failure.message:'',loading=!refusal&&!links&&!error;
 function guard(event:MouseEvent<HTMLAnchorElement>){if(owner.current!==key||artifact?.key!==key||refusal)event.preventDefault()}
 return <div className="csv-export" aria-busy={loading}>
  {links?<><a className="button-link" href={links.csv.href} download={links.csv.download} onClick={guard}>导出当前筛选 CSV · {input.rows.length} 行</a><a className="button-link quiet" href={links.metadata.href} download={links.metadata.download} onClick={guard}>下载来源清单</a></>:<><Button disabled title={refusal||error||'正在生成当前筛选的下载文件'}>{loading?'正在生成 CSV':'导出当前筛选 CSV'} · {input.rows.length} 行</Button><Button className="quiet" disabled title={refusal||error||'正在生成来源清单'}>{loading?'正在生成来源清单':'下载来源清单'}</Button></>}
  {refusal&&<span className="analysis-note">{refusal}</span>}<ErrorNote error={error}/>{error&&<Button onClick={()=>setRetry(n=>n+1)}>重新生成下载文件</Button>}
 </div>;
}
