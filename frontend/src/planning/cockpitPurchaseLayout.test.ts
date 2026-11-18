import {expect,it,vi} from 'vitest';
import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import * as echarts from 'echarts/core';
import {LineChart} from 'echarts/charts';
import {GridComponent,TooltipComponent,AriaComponent} from 'echarts/components';
import {SVGRenderer} from 'echarts/renderers';
import type {Dict} from '../api/contracts';
const captured=vi.hoisted(()=>[] as Dict[]);
vi.mock('../components/PlanningChart',()=>({PlanningChart:({option}:{option:Dict})=>{captured.push(option);return null}}));
import {CockpitSupplyPanel} from './CockpitPanels';
echarts.use([LineChart,GridComponent,TooltipComponent,AriaComponent,SVGRenderer]);

function purchaseOptions(){
 captured.length=0;
 const profile='multi-receipt-protected-manufacturing-v1';
 const scope={planId:'P',versionId:'V',runId:'R',target:'baseline',profile};
 const po=(id:string,physical:string,available:string)=>({id,item_id:id,location_id:'FACTORY',uom:'米',source_status:'confirmed',supply_source_id:'S',provenance:{record:id},remaining_quantity:'40',physical_receipt_at:physical,available_at:available});
 const orders=[po('EARLY','2026-11-03T09:00:00+08:00','2026-11-04T09:00:00+08:00'),po('MIDDLE','2026-11-05T09:00:00+08:00','2026-11-05T21:00:00+08:00'),po('LATE','2026-11-09T09:00:00+08:00','2026-11-10T09:00:00+08:00')];
 const context={enterprise_source:{plan_id:'P',data_version_id:'V'},plan_version:{solve_run_id:'R'},manufacturing_profile:profile,receipt_protection:{profile,purchase_orders:orders}};
 renderToStaticMarkup(createElement(CockpitSupplyPanel,{data:{timezone:'Asia/Shanghai'},context,scope,open:()=>{}}));
 expect(captured).toHaveLength(3);
 return {options:[...captured],orders};
}

it.each([156,266])('keeps early and late receipt/QC labels inside a %ipx ECharts canvas',width=>{
 const{options,orders}=purchaseOptions();
 options.forEach((option,i)=>{
  const chart=echarts.init(null,null,{renderer:'svg',ssr:true,width,height:84});
  try{
   chart.setOption({...option,animation:false});
   expect(option.series[0].data.map((p:Dict)=>p.value[0])).toEqual([Date.parse(orders[i].physical_receipt_at),Date.parse(orders[i].available_at)]);
   const text=(chart.getZr() as any).storage.getDisplayList().filter((e:any)=>e.type==='tspan'&&/到货|可用|\d\d-\d\d \d\d:\d\d/.test(e.style.text));
   expect(text.length).toBeGreaterThanOrEqual(2);
   for(const label of text){
    const rect=label.getBoundingRect().clone();rect.applyTransform(label.getComputedTransform());
    expect(rect.x,`${orders[i].id}: ${label.style.text} begins outside canvas`).toBeGreaterThanOrEqual(0);
    expect(rect.x+rect.width,`${orders[i].id}: ${label.style.text} ends outside canvas`).toBeLessThanOrEqual(width);
    expect(rect.y).toBeGreaterThanOrEqual(0);expect(rect.y+rect.height).toBeLessThanOrEqual(84);
   }
  }finally{chart.dispose()}
 });
});
