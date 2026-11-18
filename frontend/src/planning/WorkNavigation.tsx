import {useEffect,useId,useRef,useState} from 'react';
import {IxButton} from '@siemens/ix-react';
import {iconCheck} from '@siemens/ix-icons/icons';
import {Button,Icon,Select} from '../components/Controls';
import {activeWorkPage,planWorkGroups,networkWorkGroups} from './workspaceNavigationModel';
import './workspace-navigation.css';
const primary=[['schedule','制造排程'],['supply-work','物料与采购'],['manufacturing','制造工单作业'],['composite','工序与资源'],['review','方案评审'],['exceptions','异常与约束']];
const other=[['reports','期间专业报表'],['demand-planning','预测需求规划'],['inventory-policy','库存目标调查'],['availability','质量与可用性'],['resources','资源日历与负荷'],['inventory','库存时间账']];
const options=(rows:string[][])=>rows.map(([value,label])=>({value,label}));
export function WorkNavigation({value,onChange,compact=false,network=false}:{value:string;onChange:(mode:string)=>void;compact?:boolean;network?:boolean}){
 if(network)return <nav className="work-navigation net-work-navigation" aria-label="网络规划工作视图"><span>工作视图</span><Select label="网络关联工作视图" value={['schedule','network-supply'].includes(value)?'network-supply':value} onChange={onChange} options={options([['network-supply','跨地点供需'],['review','方案评审'],['inventory','库存时间账'],['reports','专业资料与报表'],['exceptions','异常与约束']])}/></nav>;
 if(compact)return <nav className="rc-work-navigation" aria-label="规划工作视图"><strong>资源排程</strong><Select label="当前工作视图" value={value} onChange={onChange} options={options([...primary,...other])}/></nav>;
 return <nav className="work-navigation" aria-label="规划工作视图">{primary.map(([id,label])=><Button key={id} aria-current={value===id?'page':undefined} className={value===id?'work-nav-current':'quiet'} onClick={()=>onChange(id)}>{label}</Button>)}<Select label="更多专业视图" value={other.some(([id])=>id===value)?value:''} onChange={id=>id&&onChange(id)} options={[{value:'',label:'更多专业视图'},...options(other)]}/><div className="professional-mobile-navigation"><Select label="当前工作视图" value={value} onChange={onChange} options={options([...primary,...other])}/></div></nav>
}

export function PlanWorkNavigation({value,report,onChange,network=false,disabled=false,professional=false}:{value:string;report:string;onChange:(mode:string)=>void;network?:boolean;disabled?:boolean;professional?:boolean}){
 const[expanded,setExpanded]=useState(false),trigger=useRef<HTMLButtonElement>(null),menu=useRef<HTMLDivElement>(null),menuId=useId();
 const groups=network?networkWorkGroups:planWorkGroups.map(g=>({...g,pages:g.pages.filter(p=>professional||!['forecast-netting','order-inquiry','stock-replenishment'].includes(p.id))})),active=activeWorkPage(value,report),current=groups.flatMap(g=>g.pages).find(p=>p.id===active);
 useEffect(()=>{const realign=()=>{if(menu.current?.matches(':popover-open'))position()};window.addEventListener('resize',realign);return()=>window.removeEventListener('resize',realign)},[]);
 function position(){if(!trigger.current||!menu.current)return;const r=trigger.current.getBoundingClientRect(),width=Math.min(660,window.innerWidth-24),left=Math.max(12,Math.min(r.left,window.innerWidth-width-12));menu.current.style.setProperty('--work-menu-left',left+'px');menu.current.style.setProperty('--work-menu-top',(r.bottom+8)+'px')}
 function choose(id:string){menu.current?.hidePopover();onChange(id);requestAnimationFrame(()=>trigger.current?.focus())}
 return <div className="plan-view-switcher">
  <h1><button ref={trigger} type="button" className="plan-view-trigger" popoverTarget={menuId} aria-label={'切换工作页面，当前：'+(current?.label??'计划工作区')} aria-expanded={expanded} aria-controls={menuId} disabled={disabled} onClick={position}><span>{current?.label??'计划工作区'}</span><Icon name={expanded?'up':'down'}/></button></h1>
  <div ref={menu} id={menuId} popover="auto" className="plan-view-popover" onToggle={event=>setExpanded(event.currentTarget.matches(':popover-open'))}>
   <nav className={'plan-page-groups'+(network?' network-page-groups':'')} aria-label="计划工作页面">{groups.map(group=><section key={group.label} className="plan-page-group" aria-label={group.label}><h2>{group.label}</h2><div className="plan-page-links">{group.pages.map(page=><IxButton key={page.id} alignment="start" variant="tertiary" iconRight={page.id===active?iconCheck:undefined} className={'plan-page-link'+(page.id===active?' is-current':'')} aria-current={page.id===active?'page':undefined} onClick={()=>choose(page.id)}><span className="plan-page-label">{page.label}</span></IxButton>)}</div></section>)}</nav>
  </div>
 </div>
}
