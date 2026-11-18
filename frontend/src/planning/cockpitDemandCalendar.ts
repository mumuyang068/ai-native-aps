import type {Dict} from '../api/contracts';
import {demandCoverageMatrix} from './cockpitDemandMatrix';
import type {DemandMapKind,DemandMapLine} from './cockpitDemandMap';
import {addDays} from './timeRange';

export type CalendarScope={type:'all'|'item'|'location'|'group';value:string};
export type CalendarLine=DemandMapLine&{item:string;location:string;uom:string;endpoint:string;quantityValid:boolean};
export type DemandDay={id:string;lines:CalendarLine[];unplanned:number;late:number;unknown:number;attention:number};
/** Quantity encoding is allowed for one exact product/location/unit/endpoint group. */
export function calendarMetric(day:DemandDay|undefined,quantity=false):number{
 if(!day)return -2;
 if(!day.lines.length)return -1;
 if(day.unknown>0)return -3;
 if(!quantity)return day.attention;
 if(new Set(day.lines.map(l=>l.group)).size!==1||day.lines.some(l=>!l.quantityValid||l.short===null||l.short<0))return -3;
 return day.lines.reduce((sum,line)=>sum+line.short!,0);
}
/** Counts original rows, never quantities across products, units or endpoints. */
export function calendarDemand(report:Dict,kind:DemandMapKind,scope:CalendarScope){
 const matrix=demandCoverageMatrix(report,kind,'day');
 const products=matrix.products.filter(p=>scope.type==='all'||(scope.type==='item'?p.item===scope.value:scope.type==='location'?p.location===scope.value:p.id===scope.value));
 const identities=new Map(products.map(p=>[p.id,p]));
 const make=(id:string):DemandDay=>({id,lines:[],unplanned:0,late:0,unknown:0,attention:0});
 const days=new Map(matrix.periods.map(p=>[p.id,make(p.id)])),undated=make('undated');
 for(const cell of matrix.cells){const product=identities.get(cell.group);if(!product)continue;const day=cell.period?days.get(cell.period):undated;if(!day)continue;
  for(const line of cell.lines){
   const valid=cell.quantityValid,unknown=!valid||line.state==='UNKNOWN',unplanned=valid&&line.short!==null&&line.short>0,late=line.state==='FULL_LATE';
   day.lines.push({...line,item:product.item,location:product.location,uom:product.uom,endpoint:product.endpoint,quantityValid:valid});
   if(unplanned)day.unplanned++;if(late)day.late++;if(unknown)day.unknown++;
   if(unplanned||late||unknown)day.attention++;
  }
 }
 return {products,days:[...days.values()],undated};
}

const monthNumber=(date:string)=>Number(date.slice(0,4))*12+Number(date.slice(5,7))-1;
const monthId=(month:number)=>Math.floor(month/12)+'-'+String(month%12+1).padStart(2,'0');
/** Month context outside the actual report range remains outside, never an empty-demand observation. */
export function demandCalendarWindow(days:DemandDay[],width:number,requestedStart='',compact=false){
 if(!days.length)return null;
 const span=width>=700?12:width>=440?6:3,first=days[0].id,last=days.at(-1)!.id;
 const minimum=Number(first.slice(0,4))*12,maximum=Number(last.slice(0,4))*12+12-span;
 const desired=/^\d{4}-\d{2}$/.test(requestedStart)?monthNumber(requestedStart):monthNumber(first);
 const start=Math.max(minimum,Math.min(maximum,Math.floor(desired/span)*span)),end=start+span;
 const from=monthId(start)+'-01',to=addDays(monthId(end)+'-01',-1),lookup=new Map(days.map(day=>[day.id,day]));
 const dates:{id:string;day:DemandDay|undefined}[]=[];
 for(let date=from;date<=to;date=addDays(date,1))dates.push({id:date,day:lookup.get(date)});
 const weekday=(new Date(from+'T12:00:00Z').getUTCDay()+6)%7,weeks=Math.ceil((weekday+dates.length)/7);
 const cellSize=Math.max(12,Math.min(compact?16:22,Math.floor((width-52)/weeks)));
 const a=monthId(start),b=monthId(end-1),label=span===12?a.slice(0,4)+'年':a.slice(0,4)+'年'+Number(a.slice(5))+'—'+Number(b.slice(5))+'月';
 return {from,to,dates,span,cellSize,weeks,label,start:a,previous:start>minimum?monthId(start-span):null,next:start<maximum?monthId(start+span):null};
}
