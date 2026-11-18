import {forecastBucket,type ForecastBucket} from './ForecastOverride';
import {verifyDecisionIdentity,type DecisionReport,type DemandDecisionScope,type DemandPlanningRow} from './demandDecisionModel';

export type ForecastSelection={plan_id:string;data_version_id:string;row_id:string;bucket:ForecastBucket};

export function rememberForecastSelection(scope:DemandDecisionScope,row:DemandPlanningRow):ForecastSelection|null{
 const bucket=forecastBucket(row);
 if(!scope.planId||!scope.versionId||!bucket||row.id!==bucket.forecast_id)return null;
 return {plan_id:scope.planId,data_version_id:scope.versionId,row_id:row.id,bucket};
}

function exact(actual:unknown,expected:unknown):boolean{
 if(expected===null||typeof expected!=='object')return actual===expected;
 if(actual===null||typeof actual!=='object'||Array.isArray(actual))return false;
 const value=actual as Record<string,unknown>,fields=Object.entries(expected);
 return Object.keys(value).length===fields.length&&fields.every(([key,child])=>Object.hasOwn(value,key)&&exact(value[key],child));
}

/** A saved source selection is rebound only to a verified current report row. */
export function restoreForecastSelection(saved:unknown,scope:DemandDecisionScope,report:DecisionReport<DemandPlanningRow>):DemandPlanningRow|null{
 if(!saved||typeof saved!=='object'||Array.isArray(saved))return null;
 const value=saved as Record<string,unknown>;
 if(Object.keys(value).length!==4||value.plan_id!==scope.planId||value.data_version_id!==scope.versionId||typeof value.row_id!=='string')return null;
 try{verifyDecisionIdentity(report,scope)}catch{return null}
 if(!['PRESENT','EMPTY_CHECKED'].includes(report.status))return null;
 const matches=report.rows.filter(row=>row.id===value.row_id);
 if(matches.length!==1)return null;
 const bucket=forecastBucket(matches[0]);
 return bucket&&matches[0].id===bucket.forecast_id&&exact(value.bucket,bucket)?matches[0]:null;
}
