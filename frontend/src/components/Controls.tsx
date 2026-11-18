import {useEffect,useId,useRef,type ReactNode,type ButtonHTMLAttributes,type ComponentProps} from 'react';
import {IxButton,IxIcon,IxMessageBar,IxModal,IxModalHeader,IxModalContent,IxModalFooter,IxSelect,IxSelectItem,IxSpinner,IxTabs,IxTabItem} from '@siemens/ix-react';
import {iconTasksAll,iconTable,iconCompare,iconDocumentAi,iconDocumentMapping,iconArrowLeft,iconArrowRight,iconClose,iconInfo,iconCheck,iconClock,iconFullScreen,iconSearch,iconUpload,iconRefresh,iconChevronUp,iconChevronDown} from '@siemens/ix-icons/icons';
import {Status} from './WorkspaceUi';
const icons:Record<string,string>={plan:iconTasksAll,table:iconTable,compare:iconCompare,ai:iconDocumentAi,recognize:iconDocumentMapping,left:iconArrowLeft,right:iconArrowRight,close:iconClose,info:iconInfo,check:iconCheck,clock:iconClock,fit:iconFullScreen,search:iconSearch,upload:iconUpload,reload:iconRefresh,up:iconChevronUp,down:iconChevronDown};
export function Icon({name='plan'}:{name?:string}){return <IxIcon className="workspace-icon" name={icons[name]??icons.plan} size="16" aria-hidden="true"/>}
/** Callers own business permissions and actions; iX owns the control and its states. */
export function Button({icon,children,loading=false,...props}:Omit<ButtonHTMLAttributes<HTMLButtonElement>,'color'>&{icon?:string;loading?:boolean}){
 const{type='button',className='',disabled,onClick,...rest}=props;
 const primary=className.split(/\s+/).includes('primary'),quiet=/\b(quiet|text-button|review-open)\b/.test(className),danger=className.split(/\s+/).includes('danger');
 const variant=danger?'danger-primary':primary?'primary':quiet?'tertiary':'secondary';
 return <IxButton {...rest as ComponentProps<typeof IxButton>} type={type==='reset'?'button':type} variant={variant} disabled={disabled||loading} loading={loading} className={className} icon={icon?icons[icon]??icons.plan:undefined} onClick={e=>{if(disabled||loading)return;if(type==='reset')e.currentTarget.closest('form')?.reset();onClick?.(e as unknown as React.MouseEvent<HTMLButtonElement>)}}><span className="button-content">{children}</span></IxButton>;
}
export function Badge({children,kind=''}:{children:ReactNode;kind?:string}){return <Status kind={kind==='good'?'success':kind==='risk'?'warning':kind==='bad'?'error':'default'}>{children}</Status>}
export function ErrorNote({error}:{error:string}){return error?<IxMessageBar className="error-note" type="alarm" persistent role="alert">{error}</IxMessageBar>:null}
export function Loading(){return <div className="ix-loading" role="status" aria-label="正在读取"><IxSpinner size="small"/>正在读取</div>}
export function Select({label,value,options,onChange,disabled=false,id,className=''}:{label:string;value:string;options:{value:string;label:string;disabled?:boolean}[];onChange:(value:string)=>void;disabled?:boolean;id?:string;className?:string}){
 return <IxSelect id={id} className={'workspace-select '+className} aria-label={label} value={value} mode="single" enableTopLayer disabled={disabled} onValueChange={e=>{const v=Array.isArray(e.detail)?e.detail[0]:e.detail;if(v!==undefined&&v!==value)onChange(v)}} i18nPlaceholder="请选择" i18nSelectListHeader={label} i18nNoMatches="没有匹配项">{options.map(o=><IxSelectItem key={o.value} value={o.value} label={o.label} disabled={o.disabled}/>)}</IxSelect>;
}
export function Dialog({title,children,onClose,animate=false,className='',size='360',centered=false}:{title:string;children:ReactNode;onClose:()=>void;animate?:boolean;className?:string;size?:ComponentProps<typeof IxModal>['size'];centered?:boolean}){
 const ref=useRef<HTMLIxModalElement>(null),titleId=useId(),close=useRef(onClose);close.current=onClose;
 useEffect(()=>{let active=true;const trigger=document.activeElement as HTMLElement|null;void customElements.whenDefined('ix-modal').then(()=>{if(active)void ref.current?.showModal()});return()=>{active=false;requestAnimationFrame(()=>{if(trigger?.isConnected)trigger.focus()})}},[]);
 return <IxModal ref={ref} className={className} size={size} centered={centered} aria-labelledby={titleId} closeOnBackdropClick={false} disableAnimation={!animate} onDialogDismiss={()=>close.current()} onDialogClose={()=>close.current()}><IxModalHeader id={titleId} hideClose><div className="dialog-title"><h2>{title}</h2><Button aria-label="关闭" className="quiet" icon="close" onClick={()=>void ref.current?.dismissModal()}/></div></IxModalHeader><IxModalContent><div className="dialog-body">{children}</div></IxModalContent><IxModalFooter><Button onClick={()=>void ref.current?.closeModal('closed')}>关闭</Button></IxModalFooter></IxModal>;
}
export function Definition({rows}:{rows:[string,ReactNode][]}){return <dl className="definition">{rows.map(([k,v])=><div key={k}><dt>{k}</dt><dd>{v}</dd></div>)}</dl>}
export function Tabs({tabs,value,onChange,label}:{tabs:[string,string][];value:string;onChange:(key:string)=>void;label:string}){return <IxTabs className="records-tabs" aria-label={label} ariaLabelMoreTabs="更多视图" activeTabKey={value} onTabChange={e=>{if(e.detail&&e.detail!==value)onChange(e.detail)}} keyboardNavigation="automatic">{tabs.map(([key,text])=><IxTabItem key={key} tabKey={key}>{text}</IxTabItem>)}</IxTabs>}
