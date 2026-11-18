import {useEffect,useState,type ReactNode} from 'react';
import {ConfigProvider,theme,type ThemeConfig} from 'antd';
import zhCN from 'antd/locale/zh_CN';
import {IxPill,IxWorkflowSteps,IxWorkflowStep} from '@siemens/ix-react';
/** Temporary bridge for unmigrated Ant input consumers; all colours come from iX. */
export function WorkspaceUi({children}:{children:ReactNode}){
 const [bridge,setBridge]=useState<ThemeConfig>({algorithm:theme.compactAlgorithm,token:{motion:false}});
 useEffect(()=>{const s=getComputedStyle(document.documentElement),v=(name:string)=>s.getPropertyValue(name).trim();setBridge({algorithm:theme.compactAlgorithm,token:{colorPrimary:v('--theme-color-primary'),colorInfo:v('--theme-color-info'),colorSuccess:v('--theme-color-success'),colorWarning:v('--theme-color-warning-text'),colorError:v('--theme-color-alarm'),colorText:v('--theme-color-std-text'),colorTextSecondary:v('--theme-color-soft-text'),colorTextPlaceholder:v('--theme-color-soft-text'),colorBorder:v('--theme-color-hard-bdr'),colorBgContainer:v('--theme-color-1'),fontFamily:v('--theme-font-sans'),fontSize:14,borderRadius:2,controlHeight:34,motion:false,boxShadow:'none',boxShadowSecondary:'none'}})},[]);
 return <ConfigProvider locale={zhCN} theme={bridge} button={{autoInsertSpace:false}}>{children}</ConfigProvider>;
}
export function Status({children,kind='default'}:{children:ReactNode;kind?:'success'|'warning'|'error'|'default'}){return <IxPill className="workspace-status" variant={kind==='error'?'alarm':kind==='default'?'neutral':kind} outline>{children}</IxPill>}
export function ReviewProgress({sheetCount,confirmed,total,blocking,canSave,saved}:{sheetCount:number;confirmed:number;total:number;blocking:number;canSave:boolean;saved:boolean}){
 return <div className="import-progress" role="group" aria-label="资料建立进度"><IxWorkflowSteps selectedIndex={saved?2:blocking||!canSave?1:2}>
 <IxWorkflowStep status="done">资料已读取<small>{sheetCount} 张原表已保留</small></IxWorkflowStep>
 <IxWorkflowStep status={blocking?'error':canSave?'done':'open'}>核对业务资料<small>{confirmed} / {total} 类已确认{blocking?' · '+blocking+' 项阻断':''}</small></IxWorkflowStep>
 <IxWorkflowStep status={saved?'done':'open'}>保存数据版本<small>{saved?'当前修订已有版本':blocking?'等待阻断问题处理':canSave?'可以保存当前修订':'等待业务核对'}</small></IxWorkflowStep>
 </IxWorkflowSteps></div>;
}
