import {isValidElement,useCallback,useEffect,useId,useMemo,useRef,useState,type ReactNode} from 'react';
import {IxButton,IxDropdown,IxDropdownItem,IxInput,IxMessageBar,IxSpinner} from '@siemens/ix-react';
import {AgGridReact} from 'ag-grid-react';
import * as ag from 'ag-grid-community';
import {getIxTheme} from '@siemens/ix-aggrid';
import type {TableColumn} from './DataTable';

ag.ModuleRegistry.registerModules([ag.AllCommunityModule]);
const ixGridTheme=getIxTheme(ag,{stripedRows:true});
const gridLocale:Record<string,string>={noRowsToShow:'没有对应记录',loadingOoo:'正在读取…',filterOoo:'筛选…',applyFilter:'应用筛选',resetFilter:'重置筛选',clearFilter:'清除筛选',cancelFilter:'取消',textFilter:'文本筛选',numberFilter:'数字筛选',dateFilter:'日期筛选',equals:'等于',notEqual:'不等于',contains:'包含',notContains:'不包含',startsWith:'开头为',endsWith:'结尾为',blank:'空值',notBlank:'非空',lessThan:'小于',lessThanOrEqual:'小于或等于',greaterThan:'大于',greaterThanOrEqual:'大于或等于',inRange:'区间',andCondition:'并且',orCondition:'或者',searchOoo:'查找…',columns:'列',pinColumn:'固定列',pinLeft:'固定左侧',pinRight:'固定右侧',noPin:'取消固定',autosizeThiscolumn:'适应此列宽度',autosizeAllColumns:'适应所有列宽度',sortAscending:'升序',sortDescending:'降序',sortUnSort:'取消排序',ariaColumnFilter:'列筛选',ariaFilterMenuOpen:'打开筛选',ariaRowSelect:'选择当前记录',ariaRowDeselect:'取消当前选择'};

export type GridScope={planId:string;versionId:string;runId:string;target:string;basis?:'CONFIRMED_SOURCE'};
export type GridSelection={scope:GridScope;id:string};
export type ProfessionalColumn<T>=TableColumn<T>&{width?:number;minWidth?:number;pinned?:boolean};
export type GridEvidence='PRESENT'|'EMPTY_CHECKED'|'NOT_CHECKED'|'NOT_COLLECTED'|'UNSUPPORTED'|'UNREADABLE'|'INVALID_EVIDENCE'|'FAIL'|'LOADING';
export function gridScopeKey(scope:GridScope){
 if([scope.planId,scope.versionId,scope.target,...(scope.basis==='CONFIRMED_SOURCE'?[]:[scope.runId])].some(v=>typeof v!=='string'||!v.trim()))throw Error('缺少计划、版本、运行或方案身份，不能展示专业清单。');
 if(scope.basis==='CONFIRMED_SOURCE'&&scope.runId!=='')throw Error('来源历史不能携带规划运行身份。');
 return JSON.stringify([scope.planId,scope.versionId,scope.runId,scope.target]);
}
export function gridSelectedId(scope:GridScope,selection?:GridSelection|null){return selection&&gridScopeKey(scope)===gridScopeKey(selection.scope)?selection.id:null}
export function gridRowIds<T>(rows:T[],rowId:(row:T)=>string){
 const ids=rows.map(rowId);
 if(ids.some(id=>typeof id!=='string'||!id.trim()))throw Error('记录缺少稳定身份，不能按显示名称补造行键。');
 if(new Set(ids).size!==ids.length)throw Error('本运行存在重复记录身份，已停止展示专业清单。');
 return ids;
}
export function gridColumnValue<T>(column:TableColumn<T>,row:T):string|number|null{
 const raw=column.sortValue?column.sortValue(row):column.value(row);
 return typeof raw==='number'?Number.isFinite(raw)?raw:null:typeof raw==='string'?raw:null;
}
export function gridSearchText(value:ReactNode):string{
 if(typeof value==='string'||typeof value==='number')return String(value);
 if(Array.isArray(value))return value.map(gridSearchText).filter(Boolean).join(' ');
 return isValidElement<{children?:ReactNode}>(value)?gridSearchText(value.props.children):'';
}
export function gridEvidenceState(status:GridEvidence,count:number){
 if(status==='FAIL')return {kind:'fail',message:'存在已知业务违规；保留原记录用于调查，不能作为合法执行方案。'};
 if(status==='LOADING')return {kind:'loading',message:'正在读取当前运行的记录。'};
 if(['NOT_COLLECTED','UNSUPPORTED','UNREADABLE','INVALID_EVIDENCE'].includes(status))return {kind:'unavailable',message:({NOT_COLLECTED:'必要记录未采集，不能显示为零。',UNSUPPORTED:'当前模型尚未认证此专业清单。',UNREADABLE:'记录不可读取，请检查当前运行来源。',INVALID_EVIDENCE:'来源证据不一致，已停止展示。'} as Record<string,string>)[status]};
 if(status==='EMPTY_CHECKED'&&count>0)return {kind:'unavailable',message:'已查为空的证据状态与记录不一致，已停止展示。'};
 if(status==='NOT_CHECKED')return {kind:'incomplete',message:'记录已取得，必要业务核验尚未完成。'};
 return count?{kind:'ready',message:''}:{kind:'empty',message:'已查询，当前范围没有对应记录。'};
}

export type ProfessionalGridProps<T>={scope:GridScope;rows:T[];columns:ProfessionalColumn<T>[];rowId:(row:T)=>string;label:string;selection?:GridSelection|null;onSelect?:(row:T)=>void;onOpen?:(row:T)=>void;evidenceStatus?:GridEvidence;empty?:string;height?:number;tools?:boolean};

/** A read-only grid; row identity and business permissions stay with the caller. */
export function ProfessionalGrid<T extends object>(p:ProfessionalGridProps<T>){
 let scopeKey='',identityError='';
 try{scopeKey=gridScopeKey(p.scope);gridRowIds(p.rows,p.rowId)}catch(error){identityError=error instanceof Error?error.message:'记录身份无法核对。'}
 if(identityError)return <IxMessageBar type="alarm" persistent>{identityError}</IxMessageBar>;
 return <ScopedProfessionalGrid key={scopeKey} {...p}/>;
}

function ScopedProfessionalGrid<T extends object>({scope,rows,columns,rowId,label,selection,onSelect,onOpen,evidenceStatus='PRESENT',empty,height,tools=true}:ProfessionalGridProps<T>){
 const [query,setQuery]=useState(''),[compact,setCompact]=useState(false),[hidden,setHidden]=useState<Set<string>>(()=>new Set()),[visibleCount,setVisibleCount]=useState(rows.length);
 const grid=useRef<AgGridReact<T>>(null),syncing=useRef(false),menuId='grid-columns-'+useId().replaceAll(':',''),selectedId=gridSelectedId(scope,selection),state=gridEvidenceState(evidenceStatus,rows.length);
 const columnDefs=useMemo<ag.ColDef<T>[]>(()=>columns.map((column,index)=>({
  colId:column.id,headerName:column.title,minWidth:column.minWidth??(index===0?190:125),width:column.width??(index===0?230:175),hide:index>0&&hidden.has(column.id),
  pinned:column.pinned?'left':undefined,
  valueGetter:params=>params.data?gridColumnValue(column,params.data):null,
  cellRenderer:({data}:{data?:T}):ReactNode=>data?column.value(data):null,
  cellClass:column.numeric?'ag-right-aligned-cell':undefined,headerClass:column.numeric?'ag-right-aligned-header':undefined,
  filter:column.numeric?'agNumberColumnFilter':'agTextColumnFilter',sortable:!!column.sortValue,
  getQuickFilterText:params=>params.data?gridSearchText(column.value(params.data)):'',
  tooltipValueGetter:params=>params.value==null?'未提供':String(params.value),
 })),[columns,hidden]);
 const syncSelection=useCallback(()=>{
  const api=grid.current?.api;if(!api)return;
  syncing.current=true;api.forEachNode(node=>node.setSelected(!!selectedId&&node.id===selectedId));syncing.current=false;
  api.redrawRows();
 },[selectedId]);
 useEffect(syncSelection,[syncSelection,rows]);
 function choose(row:T|undefined){if(row&&rowId(row)!==selectedId)onSelect?.(row)}
 if(state.kind==='loading')return <div role="status"><IxSpinner size="small"/> {state.message}</div>;
 if(state.kind==='unavailable')return <IxMessageBar type="warning" persistent>{state.message}</IxMessageBar>;
 return <section className="professional-grid" aria-label={label} style={{minWidth:0,width:'100%'}}>
  {(state.kind==='fail'||state.kind==='incomplete')&&<IxMessageBar type={state.kind==='fail'?'alarm':'warning'} persistent>{state.message}</IxMessageBar>}
  {state.kind==='empty'||!rows.length?<p role="status">{empty??state.message}</p>:<>
   {tools&&<div className="professional-grid-tools" style={{display:'flex',flexWrap:'wrap',gap:8,alignItems:'end',padding:'8px 0'}}>
    <IxInput label={'筛选'+label} aria-label={'筛选'+label} placeholder="查找当前清单" value={query} onValueChange={event=>setQuery(event.detail)} style={{width:240,maxWidth:'100%'}}/>
    <IxButton id={menuId} variant="secondary">显示列</IxButton>
    <IxDropdown trigger={menuId}>
     {columns.map((column,index)=><IxDropdownItem key={column.id} label={column.title+(index===0?'（身份列）':'')} checked={index===0||!hidden.has(column.id)} disabled={index===0} onClick={()=>setHidden(previous=>{const next=new Set(previous);next.has(column.id)?next.delete(column.id):next.add(column.id);return next})}/>)}
    </IxDropdown>
    <IxButton variant="tertiary" onClick={()=>{setQuery('');grid.current?.api.setFilterModel(null)}}>清除筛选</IxButton>
    <IxButton variant="tertiary" onClick={()=>setCompact(value=>!value)}>{compact?'标准行高':'紧凑行高'}</IxButton>
    <span role="status">{visibleCount} / {rows.length} 条</span>
   </div>}
   <div style={{height:height??Math.min(420,60+Math.max(3,Math.min(rows.length,7))*(compact?40:56)),width:'100%',minWidth:0}}>
    <AgGridReact<T> localeText={gridLocale} ref={grid} theme={ixGridTheme} rowData={rows} columnDefs={columnDefs} getRowId={params=>rowId(params.data)}
     defaultColDef={{resizable:true,suppressMovable:true}} rowHeight={compact?40:56} headerHeight={42} quickFilterText={query}
     rowSelection={{mode:'singleRow',enableClickSelection:true,checkboxes:false}} enableCellTextSelection
     getRowStyle={params=>params.node.id===selectedId?{backgroundColor:'var(--theme-color-ghost--selected)'}:undefined}
     onGridReady={syncSelection} onFirstDataRendered={syncSelection} onRowDataUpdated={syncSelection}
     onSelectionChanged={event=>{if(!syncing.current&&event.source!=='api'){const row=event.api.getSelectedRows()[0];if(row&&rowId(row)!==selectedId)choose(row)}}}
     onRowClicked={event=>choose(event.data)} onRowDoubleClicked={event=>{choose(event.data);if(event.data)onOpen?.(event.data)}}
     onCellKeyDown={event=>{const keyboard=event.event as KeyboardEvent|undefined;if(keyboard?.key==='Enter'&&event.data){choose(event.data);onOpen?.(event.data)}}}
     onFilterChanged={event=>setVisibleCount(event.api.getDisplayedRowCount())} onModelUpdated={event=>setVisibleCount(event.api.getDisplayedRowCount())}
     overlayNoRowsTemplate="<span role='status'>当前筛选没有匹配记录。</span>"/>
   </div>
  </>}
 </section>;
}
