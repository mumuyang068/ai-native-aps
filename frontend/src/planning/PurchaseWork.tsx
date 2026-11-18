import {useMemo} from 'react';
import type {Dict} from '../api/contracts';
import {Button,Definition} from '../components/Controls';
import {DataTable,type TableColumn} from '../components/DataTable';
import type {ReportScope} from './reportModel';
import {SavedWorkViews} from './SavedWorkViews';
import {ExportCsvButton} from './ExportCsvButton';
import {workColumns} from './workViewModel';
import {supplyScopeKey} from './supplyWorkModel';
import {formatSiteTime} from './timeRange';
import {qty} from './planModel';
import {purchaseDocuments,purchaseGroups,purchasePeriods,filterPurchases,selectPurchase,purchaseExportFields,type PurchaseDocument,type PurchaseGroup} from './purchaseWorkModel';
type Props={report:Dict;input:Dict;scope:ReportScope;search:URLSearchParams;onSearch:(search:URLSearchParams)=>void;onMaterial:(row:PurchaseDocument)=>void};
const quantity=(n:number|null,uom:string)=>n===null?'未知':qty(n)+' '+(uom||'单位未知');
const origin=(value:string)=>value==='EXISTING'?'原有采购':value==='PROPOSED'?'规划建议':'性质未知';
export function PurchaseWork({report,input,scope,search,onSearch,onMaterial}:Props){
 const all=useMemo(()=>purchaseDocuments(report,input),[report,input]),basis=search.get('purchase_basis')==='available'?'available':'physical',period=search.get('purchase_period')??'';
 const filter={query:search.get('purchase_query')??'',supplier:search.get('purchase_supplier')??'',item:search.get('purchase_item')??'',location:search.get('purchase_location')??'',status:search.get('purchase_status')??'',origin:search.get('purchase_origin')??''};
 const buckets=purchasePeriods([...(report.inventory?.rows??[]),...(report.documents?.summary??[])]),filtered=filterPurchases(all,filter),groups=purchaseGroups(filtered,buckets,basis).filter(g=>!period||g.startAt===period||period==='__UNKNOWN__'&&g.periodState==='UNKNOWN'||period==='__OUTSIDE__'&&g.periodState==='OUTSIDE');
 const groupId=search.get('purchase_group'),selectedGroup=search.get('supply_scope')===supplyScopeKey(scope)?groups.find(g=>g.id===groupId):undefined;
 const docs=selectedGroup?selectedGroup.documents:groupId?[]:groups.flatMap(g=>g.documents),selected=selectPurchase(docs,search,scope);
 const timezone=report.timezone??input.site?.timezone??'Asia/Shanghai',time=(s:string)=>s&&Number.isFinite(Date.parse(s))?formatSiteTime(s,timezone):'未知';
 function change(values:Record<string,string>,clear=true){const next=new URLSearchParams(search);for(const [key,value]of Object.entries(values)){if(value)next.set(key,value);else next.delete(key)}if(clear){next.delete('purchase_group');next.delete('purchase_doc')}next.set('supply_scope',supplyScopeKey(scope));onSearch(next)}
 const options=(field:'supplierId'|'itemId'|'locationId'|'status')=>[...new Set(all.map(d=>d[field]).filter(Boolean))].sort();
 const groupColumns:TableColumn<PurchaseGroup>[]=[
  {id:'supplier',title:'供应商 / 物料 / 地点',sortValue:r=>r.supplierId+' '+r.itemId,value:r=><>{r.documents[0].supplierName}<small>{r.itemName} · {r.itemId} @ {r.locationId||'地点未知'}</small></>},
  {id:'period',title:basis==='available'?'计划QC可用期间':'计划物理到货期间',sortValue:r=>r.startAt,value:r=>r.startAt?<>{time(r.startAt)}<small>至 {time(r.endAt)} 前</small></>:r.periodState==='OUTSIDE'?'规划期外 / 期间未取得':'时点未知'},
  {id:'status',title:'性质 / 状态',value:r=><>{origin(r.origin)}<small>{r.status||'状态未知'}</small></>},
  {id:'quantity',title:'期间单据量',numeric:true,sortValue:r=>r.quantity??Number.NEGATIVE_INFINITY,value:r=><>{quantity(r.quantity,r.uom)}{r.unknownQuantityCount>0&&<small>{r.unknownQuantityCount} 笔数量未知</small>}</>},
  {id:'count',title:'采购单数',numeric:true,sortValue:r=>r.documents.length,value:r=>r.documents.length},
  {id:'value',title:'模型采购价值',value:r=>r.unknownCostCount?<><span>未知</span><small>{r.unknownCostCount} 笔价格或条件未匹配</small></>:Object.entries(r.money).map(([currency,value])=><div key={currency}>{qty(value)} {currency}</div>)}
 ];
 const docColumns:TableColumn<PurchaseDocument>[]=[
  {id:'reference',title:'采购单 / 来源',sortValue:r=>r.reference,value:r=><>{r.reference}<small>{r.sourceId||'来源未知'} · {r.supplierName}</small></>},
  {id:'item',title:'物料 / 地点',sortValue:r=>r.itemId+' '+r.locationId,value:r=><>{r.itemName}<small>{r.itemId} @ {r.locationId||'未知'}</small></>},
  {id:'status',title:'性质 / 状态',value:r=><>{origin(r.origin)}<small>{r.status||'未知'}</small></>},
  {id:'quantity',title:'单据全量',numeric:true,sortValue:r=>r.quantity??Number.NEGATIVE_INFINITY,value:r=>quantity(r.quantity,r.uom)},
  {id:'physical',title:'计划物理到货',sortValue:r=>r.physicalAt,value:r=>time(r.physicalAt)},
  {id:'available',title:'计划QC后可用',sortValue:r=>r.availableAt,value:r=>time(r.availableAt)},
  {id:'receipt',title:'实际已收 / 未收',value:r=><>{quantity(r.received,r.uom)} / {quantity(r.remaining,r.uom)}</>},
  {id:'quality',title:'实收待检 / 冻结',value:r=><>{quantity(r.pending,r.uom)} / {quantity(r.frozen,r.uom)}</>},
  {id:'value',title:'模型采购价值',value:r=>r.value===null?'未知':qty(r.value)+' '+r.currency}
 ];
 const visibleColumns=workColumns(docColumns,search.get('purchase_columns'));
 return <section className="purchase-work" aria-label="采购期间工作清单">
  <form className="supply-work-filters purchase-filters" onSubmit={e=>e.preventDefault()}>
   <label>采购查询<input type="search" aria-label="采购查询" value={filter.query} placeholder="单号、物料、供应商或来源" onChange={e=>change({purchase_query:e.target.value})}/></label>
   <label>供应商<select aria-label="采购供应商" value={filter.supplier} onChange={e=>change({purchase_supplier:e.target.value})}><option value="">全部供应商</option>{options('supplierId').map(v=><option key={v} value={v}>{all.find(d=>d.supplierId===v)?.supplierName} · {v}</option>)}</select></label>
   <label>物料<select aria-label="采购物料" value={filter.item} onChange={e=>change({purchase_item:e.target.value})}><option value="">全部物料</option>{options('itemId').map(v=><option key={v} value={v}>{all.find(d=>d.itemId===v)?.itemName} · {v}</option>)}</select></label>
   <label>地点<select aria-label="采购地点" value={filter.location} onChange={e=>change({purchase_location:e.target.value})}><option value="">全部地点</option>{options('locationId').map(v=><option key={v}>{v}</option>)}</select></label>
   <label>单据性质<select aria-label="采购性质" value={filter.origin} onChange={e=>change({purchase_origin:e.target.value})}><option value="">全部性质</option><option value="EXISTING">原有采购</option><option value="PROPOSED">规划建议</option></select></label>
   <label>状态<select aria-label="采购状态" value={filter.status} onChange={e=>change({purchase_status:e.target.value})}><option value="">全部状态</option>{options('status').map(v=><option key={v}>{v}</option>)}</select></label>
   <label>归期时点<select aria-label="采购归期时点" value={basis} onChange={e=>change({purchase_basis:e.target.value})}><option value="physical">计划物理到货</option><option value="available">计划QC后可用</option></select></label>
   <label>期间<select aria-label="采购期间" value={period} onChange={e=>change({purchase_period:e.target.value})}><option value="">全规划期</option>{buckets.map(p=><option key={p.start_at} value={p.start_at}>{time(p.start_at)} 起</option>)}<option value="__UNKNOWN__">时点未知</option><option value="__OUTSIDE__">规划期外 / 期间未取得</option></select></label>
   <Button onClick={()=>change(Object.fromEntries(['query','supplier','item','location','origin','status','period'].map(k=>['purchase_'+k,''])))}>清除采购筛选</Button>
  </form>
  <div className="supply-list-scroll purchase-group-scroll" tabIndex={0} aria-label="采购期间汇总，可横向滚动"><DataTable rows={groups} columns={groupColumns} rowId={r=>r.id} selected={selectedGroup?.id} onSelect={r=>change({purchase_group:r.id,purchase_doc:''},false)} empty={['PRESENT','EMPTY_CHECKED'].includes(report.documents?.status)?'当前筛选没有采购单据。':'采购单据尚未取得，不能视为零采购。'}/></div>
  <div className="purchase-detail-heading"><h3>{selectedGroup?'所选期间的采购单据':'当前筛选的采购单据'} · {docs.length} 笔</h3>{groupId&&<Button onClick={()=>change({purchase_group:'',purchase_doc:''},false)}>返回全部筛选单据</Button>}<details className="purchase-columns"><summary>单据显示列</summary>{docColumns.map((column,i)=><label key={column.id}><input type="checkbox" disabled={i===0} checked={visibleColumns.some(c=>c.id===column.id)} onChange={e=>change({purchase_columns:(e.target.checked?[...visibleColumns.map(c=>c.id),column.id]:visibleColumns.filter(c=>c.id!==column.id).map(c=>c.id)).join(',')},false)}/>{column.title}</label>)}</details></div>
  {groupId&&!selectedGroup&&<p className="supply-pending">原期间分组不在当前筛选或运行中，请重新选择汇总行。</p>}
  <div className="supply-detail-scroll purchase-document-scroll" tabIndex={0} aria-label="采购单据工作清单，可横向滚动"><DataTable rows={docs} columns={visibleColumns} rowId={r=>r.id} selected={selected?.id} onSelect={r=>change({purchase_doc:r.id},false)} empty="没有可定位的采购单据。"/></div>
  {search.has('purchase_doc')&&!selected&&<p className="supply-pending">原采购单不在当前筛选或运行中。请选择真实单据，未自动替换为其他对象。</p>}
  {selected&&<section className="purchase-selected" aria-label="采购单据详情"><header className="purchase-detail-heading"><h3>{selected.reference} · {selected.itemName}</h3><Button onClick={()=>onMaterial(selected)}>返回此物料、来源与事件</Button></header><Definition rows={[["供应商 / 来源",selected.supplierName+' / '+(selected.sourceId||'未知')],["单据性质 / 状态",origin(selected.origin)+' / '+(selected.status||'未知')],["计划物理到货",time(selected.physicalAt)],["计划QC后可用",time(selected.availableAt)],["模型单价",selected.priceSource?quantity(Number(selected.priceSource.unit_price),selected.currency+' / '+selected.uom):'未知'],["模型采购价值",selected.value===null?'未知':qty(selected.value)+' '+selected.currency]]}/><details><summary>采购单据、价格来源与原始引用</summary><pre className="raw-json">{JSON.stringify({document:selected.raw,price_source:selected.priceSource},null,2)}</pre></details></section>}
  <p className="supply-work-note">期间为 [开始, 结束)，同供应商、物料、地点、单位、性质和状态分别汇总。单据全量与计划到货/QC日期不证明实际收货；没有实收凭据时，已收、未收、待检、冻结均为未知。模型价值按匹配的来源单价估计，不含税、运费和持有成本；币种分别列示。</p>
  <div className="purchase-tools"><details><summary>保存采购筛选与列</summary><SavedWorkViews scope={scope} search={search} onRestore={onSearch}/></details><ExportCsvButton input={{scope,data:report,status:report.documents?.status??'NOT_COLLECTED',kind:'documents',rows:docs,fields:purchaseExportFields,filters:{...filter,period,basis,grain:report.grain,group_id:selectedGroup?.id??null,columns:visibleColumns.map(c=>c.id)},sourceUrl:'/v1/enterprise-plans/'+scope.planId+'/reports?'+new URLSearchParams({data_version_id:scope.versionId,run_id:scope.runId,target:scope.target,grain:report.grain??'day'})}}/></div>
 </section>;
}
