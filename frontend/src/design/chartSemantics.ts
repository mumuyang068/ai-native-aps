/** Chart paint only. Domain Owners decide what is actual, planned or proposed. */
export const chartDefaults={demand:'#004f6b',actual:'#007d73',planned:'#0067a3',proposed:'#7652a0',baseline:'#65727c',success:'#007d73',warning:'#a86300',danger:'#b73535',unavailable:'#65727c'} as const;
export type ChartSemantic=keyof typeof chartDefaults;
export function chartColors():Record<ChartSemantic,string>{
 const css=typeof document!=='undefined'&&typeof getComputedStyle!=='undefined'?getComputedStyle(document.documentElement):null;
 return Object.fromEntries(Object.entries(chartDefaults).map(([key,value])=>[key,css?.getPropertyValue('--chart-'+key).trim()||value])) as Record<ChartSemantic,string>;
}
// Categories are stable across filtering/reordering and never borrow risk red or success green.
const categories=['#365b86','#7751a6','#536f96','#815b88','#496879','#6b638a'] as const;
export function chartCategory(key:string){let n=2166136261;for(const char of key)n=Math.imul(n^char.charCodeAt(0),16777619);return categories[(n>>>0)%categories.length]}
export function chartSeriesStyle(semantic:ChartSemantic,variant:'normal'|'baseline'|'candidate'='normal'){
 const color=chartColors()[semantic],baseline=variant==='baseline',candidate=variant==='candidate';
 return {itemStyle:{color:baseline?'#ffffff':color,borderColor:color,borderWidth:baseline||candidate?1.5:0,...(baseline?{decal:{symbol:'rect',color,rotation:-Math.PI/4,dashArrayX:[1,0],dashArrayY:[2,4]}}: {})},lineStyle:{color,width:candidate?2.5:2,type:baseline||semantic==='planned'?'dashed' as const:'solid' as const},symbol:baseline?'emptyCircle':candidate?'diamond':'circle'};
}
/** Numeric metric identity, not its display label or position, owns paint. */
export function reportMetricSemantic(id:string):ChartSemantic|undefined{
 if(['due_quantity','new_quantity','effective_demand','net','gross'].includes(id))return 'demand';
 if(['shipped','physical_in','physical_out','ending_physical','ending_available'].includes(id))return 'actual';
 if(['planned_quantity','cumulative_planned_quantity','load_hours','ending','inflow','outflow'].includes(id))return 'planned';
 if(['baseline','forecast_baseline'].includes(id))return 'baseline';
 if(['proposed','forecast_override','forecast_total'].includes(id))return 'proposed';
 if(['overdue_unfulfilled','overdue'].includes(id))return 'danger';
 if(['ending_open','ending_pending','ending_held','qc_waiting_end','receipt_qc_waiting_end'].includes(id))return 'warning';
 return undefined;
}
export function reportMetricChartStyle(id:string,outgoing=false){
 const semantic=reportMetricSemantic(id),color=semantic?chartColors()[semantic]:chartCategory(id),style=semantic?chartSeriesStyle(semantic):{itemStyle:{color},lineStyle:{color,width:2,type:'solid' as const},symbol:'circle'};
 // In/out are separate flows of the same nature. Pattern and direction distinguish them.
 return {...style,itemStyle:{...style.itemStyle,...(outgoing?{decal:{symbol:'rect',color:'#ffffff',rotation:-Math.PI/4,dashArrayX:[1,0],dashArrayY:[1,4]}}:{})},symbol:outgoing?'triangle':style.symbol};
}
