export type WorkPage = {id:string;label:string};
export type WorkPageGroup = {label:string;pages:WorkPage[]};
const pages=(rows:string[][]):WorkPage[]=>rows.map(([id,label])=>({id,label}));

export const planWorkGroups:WorkPageGroup[]=[
 {label:'经营与计划历史',pages:pages([['history-demand','需求与履约历史'],['history-inventory','库存与物料历史'],['history-plans','计划版本历史']])},
 {label:'需求与履约',pages:pages([['cockpit','计划驾驶舱'],['reports:demand','需求履约'],['demand-planning','需求与预测'],['forecast-netting','预测抵扣与规划'],['order-inquiry','新订单评估'],['exceptions','异常与约束']])},
 {label:'制造与资源',pages:pages([['schedule','制造排程'],['manufacturing','制造工单作业'],['reports:manufacturing','制造期间计划'],['composite','工序与资源'],['resources','资源日历与负荷'],['reports:resource','期间资源产能']])},
 {label:'物料与供应',pages:pages([['supply-work','物料与采购'],['inventory','库存时间账'],['reports:inventory','期间库存收支'],['inventory-policy','库存目标调查'],['stock-replenishment','库存与补货策略'],['availability','质量与可用性'],['reports:documents','采购 / 制造 / 配送'],['reports:transit','配送与在途计划']])},
 {label:'方案评审',pages:pages([['reports:constraints','原生约束报告'],['reports:problems','运行问题报告'],['review','方案评审']])}
];
export const networkWorkGroups:WorkPageGroup[]=[
 {label:'网络供需',pages:pages([['cockpit','计划驾驶舱'],['network-supply','跨地点供需'],['inventory','库存时间账'],['reports:demand','需求履约'],['reports:resource','期间资源产能'],['reports:inventory','期间库存收支'],['reports:documents','采购 / 制造 / 配送'],['exceptions','异常与约束'],['review','方案评审']])}
];
export function activeWorkPage(mode:string,report='demand'){
 return mode==='reports'?'reports:'+report:mode;
}
/** Navigation changes the view only; plan, candidate and run scope remain owned by Workspace. */
export function reportWorkPage(previous:URLSearchParams,report:string){
 const next=new URLSearchParams(previous);
 if(previous.get('report')!==report)for(const key of ['report_query','report_chart_group','report_issue_type','report_issue_owner','report_line_query','work_kind','work_status','work_sort','work_desc','work_columns'])next.delete(key);
 next.set('analysis','reports');next.set('report',report);next.delete('report_row');
 return next;
}
export function professionalDemandReportSearch(previous:URLSearchParams,origin:string){
 const next=reportWorkPage(previous,'demand');
 next.set('professional_return',origin);
 return next;
}
