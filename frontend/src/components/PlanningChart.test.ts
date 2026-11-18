import {describe,expect,it} from 'vitest';
import {chartEventPosition,focusedChartOption,chartTimeWindow,timeZoomOption} from './PlanningChart';

describe('库存图表按原始事件身份定位',()=>{
 it('同刻同量事件仍按各自稳定ID定位，不借用相似记录',()=>{
  const option={series:[{type:'line',data:[
   {eventId:'API-A:FLOW:1',value:[100,8]},
   {eventId:'API-A:FLOW:2',value:[100,8]},
  ]}]};
  expect(chartEventPosition(option,'API-A:FLOW:2')).toEqual({seriesIndex:0,dataIndex:1});
  expect(chartEventPosition(option,'API-B:FLOW:2')).toBeNull();
 });
 it('可跨系列寻找确切事件，跳过期初、未知点和空系列',()=>{
  const option={series:[{type:'line'},{type:'line',data:[null,[100,12],{eventId:'event-2',value:[200,8]}]}]};
  expect(chartEventPosition(option,'event-2')).toEqual({seriesIndex:1,dataIndex:2});
  expect(chartEventPosition(option,'missing')).toBeNull();
 });
 it('兼容单系列对象；缺少证据不伪造定位点',()=>{
  expect(chartEventPosition({series:{type:'line',data:[{eventId:'event-1',value:[1,12]}]}},'event-1')).toEqual({seriesIndex:0,dataIndex:0});
  expect(chartEventPosition({},'event-1')).toBeNull();
  expect(chartEventPosition({series:[]},'event-1')).toBeNull();
 });
});

describe('时间缩放条的范围和操作反馈',()=>{
 const option={xAxis:{type:'time',min:1000,max:5000},dataZoom:[{type:'inside',zoomOnMouseWheel:'ctrl'},{type:'slider',labelFormatter:(v:number)=>'time:'+v}],series:[{type:'line',data:[{eventId:'original-event',value:[2000,8]}]}]};
 it('显示实际查看范围，并随缩放百分比更新而不改变物料事件',()=>{
  expect(chartTimeWindow(option)).toEqual({start:'time:1000',end:'time:5000'});
  expect(chartTimeWindow({...option,dataZoom:[{type:'slider',start:25,end:75,labelFormatter:(v:number)=>'time:'+v}]})).toEqual({start:'time:2000',end:'time:4000'});
  expect(option.series[0].data[0]).toEqual({eventId:'original-event',value:[2000,8]});
 });
 it('不把需求行分类轴或不明确的时间边界编造成日期',()=>{
  expect(chartTimeWindow({...option,xAxis:{type:'category',data:['D1']}})).toBeNull();
  expect(chartTimeWindow({...option,xAxis:{type:'time',min:null,max:5000}})).toBeNull();
  expect(chartTimeWindow({...option,dataZoom:[{type:'slider',start:80,end:20}]})).toBeNull();
 });
 it('用有效把手与现有主题强调拖动区域，保留原数据和Ctrl缩放',()=>{
  const colors={primary:'#007893',surface:'#fff',border:'#888',selection:'#e2f0f2',track:'#eee'};
  const styled=timeZoomOption(option,colors) as any;
  expect(styled.dataZoom[1]).toMatchObject({type:'slider',height:28,handleSize:32,brushSelect:false,filterMode:'none',handleStyle:{borderColor:colors.primary},fillerColor:colors.selection});
  expect(styled.series).toBe(option.series);
  expect(styled.dataZoom[0]).toMatchObject({...option.dataZoom[0],filterMode:'none'});
  expect((option.dataZoom[0] as any).filterMode).toBeUndefined();
  expect((option.dataZoom[1] as any).handleSize).toBeUndefined();
  expect(timeZoomOption({...option,xAxis:{type:'category'}},colors).dataZoom).toBe(option.dataZoom);
 });
 it('弹窗初次挂载缺少CSS令牌时，缩放轨道和把手仍有可见颜色',()=>{
  const colors={primary:'',surface:'',border:'',selection:'',track:''};
  const styled=timeZoomOption(option,colors) as any,slider=styled.dataZoom[1];
  expect(slider.handleStyle).toMatchObject({color:'#ffffff',borderColor:'#006e93'});
  expect(slider.backgroundColor).toBe('#eff0f1');
  expect(slider.fillerColor).toBe('rgba(0, 216, 236, .2)');
  expect(styled.series).toBe(option.series);
  expect(colors.primary).toBe('');
 });
});

describe('反向定位的持久事件反馈',()=>{
 it('只强调精确选中事件，并将原格式提示固定到该数据项',()=>{
  const formatter=(value:unknown)=>String(value);
  const option={tooltip:{trigger:'axis',formatter},series:[{type:'line',data:[
   {eventId:'API-A:FLOW:1',value:[100,12]},
   {eventId:'API-A:FLOW:2',value:[100,8]},
  ]}]};
  const focused=focusedChartOption(option,'API-A:FLOW:2') as any;
  expect(focused.tooltip).toMatchObject({trigger:'item',alwaysShowContent:true,formatter});
  expect(focused.series[0].data[0]).toEqual(option.series[0].data[0]);
  expect(focused.series[0].data[1]).toMatchObject({eventId:'API-A:FLOW:2',value:[100,8],symbolSize:14});
  expect((option.series[0].data[1] as any).symbolSize).toBeUndefined();
  expect(option.tooltip.trigger).toBe('axis');
 });
 it('缺事件或切走选择时不固定旧运行提示，不补造点',()=>{
  const option={tooltip:{trigger:'axis'},series:[{type:'line',data:[{eventId:'API-A:1',value:[1,12]}]}]};
  for(const id of [undefined,'API-B:1']){
   const value=focusedChartOption(option,id) as any;
   expect(value.tooltip.alwaysShowContent).not.toBe(true);
   expect(value.series[0].data).toEqual(option.series[0].data);
  }
 });
});
