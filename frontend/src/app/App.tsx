import {PlanDirectory} from '../planning/PlanDirectory';
import {BusinessFactsPage} from '../planning/BusinessFacts';
import {CockpitHome} from '../planning/CockpitHome';
import {useEffect,useState} from 'react';
import {Routes,Route,NavLink,Link,useLocation,useNavigate} from 'react-router';
import {IxApplication,IxApplicationHeader,IxMenu,IxMenuItem} from '@siemens/ix-react';
import {iconTasksAll,iconTable,iconRefresh} from '@siemens/ix-icons/icons';
import {request} from '../api/client';
import type {PlanRecord} from '../api/contracts';
import {Icon,ErrorNote,Loading} from '../components/Controls';
import {DataTable} from '../components/DataTable';
import {ImportPage} from '../imports/ImportPage';
import {DataVersionPage} from '../imports/DataVersionPage';
import {Workspace} from '../planning/Workspace';
import {ComparePage} from '../scenarios/ComparePage';
import {RecoveryPage} from '../operations/RecoveryPage';
import {ProfessionalPage} from '../professional/ProfessionalPage';

function PlanList(){return <PlanDirectory/>}
function PendingSurface(){return <div className="empty-surface"><h1>请选择企业资料或生产计划</h1><p>从已有资料开始，核对订单、库存和生产能力后，再生成第一份计划。</p><Link to="/imports">查看企业资料</Link></div>}
export function App(){const location=useLocation(),navigate=useNavigate(),P={ImportPage,DataVersionPage,Workspace,ComparePage};return <><a className="skip" href="#main">跳到主要内容</a><IxApplication className="aps-application" theme="classic" colorSchema="light"><IxApplicationHeader slot="application-header" name="AI Native APS" nameSuffix="供需与生产计划" ariaLabelMoreMenuIconButton="更多导航" enableTopLayer/><IxMenu slot="menu" i18nAriaLabelMenu="工作区导航" i18nNavigationHint="使用方向键选择，按Enter进入" i18nExpand="展开导航" i18nCollapse="收起导航" i18nLegal="关于与法律信息" i18nSettings="设置" i18nToggleTheme="切换主题"><IxMenuItem icon={iconTable} label="计划驾驶舱" active={location.pathname==='/'||new URLSearchParams(location.search).get('analysis')==='cockpit'} onClick={()=>navigate('/')}/><IxMenuItem icon={iconTasksAll} label="生产计划" active={location.pathname.startsWith('/plans')&&new URLSearchParams(location.search).get('analysis')!=='cockpit'} onClick={()=>navigate('/plans')}/><IxMenuItem icon={iconTable} label="企业资料" active={['/imports','/data','/professional-data'].some(p=>location.pathname.startsWith(p))} onClick={()=>navigate('/imports')}/><IxMenuItem icon={iconRefresh} label="任务核对" slot="bottom" active={location.pathname.startsWith('/operations')} onClick={()=>navigate('/operations/recovery')}/></IxMenu><main id="main" className="ix-app-main" tabIndex={-1}><div className="route-content"><Routes><Route path="/professional-data" element={<ProfessionalPage/>}/><Route path="/operations/recovery" element={<RecoveryPage/>}/><Route path="/" element={<CockpitHome/>}/><Route path="/imports" element={P.ImportPage?<P.ImportPage/>:<Loading/>}/><Route path="/imports/:taskId" element={P.ImportPage?<P.ImportPage/>:<Loading/>}/><Route path="/data/:versionId/business" element={<BusinessFactsPage/>}/><Route path="/data/:versionId" element={P.DataVersionPage?<P.DataVersionPage/>:<Loading/>}/><Route path="/plans" element={<PlanList/>}/><Route path="/plans/:planId" element={P.Workspace?<P.Workspace/>:<Loading/>}/><Route path="/plans/:planId/compare/:draftId" element={P.ComparePage?<P.ComparePage/>:<Loading/>}/><Route path="*" element={<PendingSurface/>}/></Routes></div><footer className="statusbar"><span>供需与生产计划</span><span>计划建议与实际执行分别管理</span></footer></main></IxApplication></>}
