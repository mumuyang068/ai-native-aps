import type {Dict} from '../api/contracts';
import {decisionQuantity} from './demandDecisionModel';
export function forecastGrossPresentation(rows:Dict[],target:string){
 if(target==='baseline')return {overridden:false,column:'原始预测 Gross',note:'未结订单 + 净预测构成有效需求；已抵扣的订单不再与 Gross 重复相加。'};
 const overridden=rows.some(row=>row.gross_overridden===true),unchanged=rows.length>0&&rows.every(row=>row.gross_overridden===false);
 return {overridden,column:overridden?'情景 Gross（假设）':unchanged?'来源预测 Gross（未改）':'当前 Gross（覆盖状态未核实）',note:overridden?'标记为覆盖的桶使用隔离 Gross 假设；来源确认 Gross 与原版本保持不变。覆盖桶的 Consumed / Net 按情景 Gross 抵扣，其余桶保持来源 Gross。':unchanged?'来源 Gross 未改；当前 Firm 与 Net 按原来源 Gross 重新抵扣。新增 Firm 属于隔离需求假设，不是 Gross 预测覆盖。':'Gross 覆盖状态未核实，请核对原始来源和草案，不能仅因当前是候选而判定预测被修改。'};
}
export function forecastGrossDetails(row:Dict,scenario:boolean):[string,string][]{
 if(!scenario)return [['原始预测 Gross',decisionQuantity(row.gross_forecast,row.uom)]];
 if(row.gross_overridden===true)return [['来源确认 → 情景假设 Gross',decisionQuantity(row.source_confirmed_gross_quantity,row.uom)+' → '+decisionQuantity(row.scenario_gross_quantity,row.uom)],['预测假设理由',typeof row.forecast_assumption_reason==='string'?row.forecast_assumption_reason:'未提供']];
 return [[row.gross_overridden===false?'来源预测 Gross（未改）':'当前 Gross（覆盖状态未核实）',decisionQuantity(row.gross_forecast,row.uom)]];
}
