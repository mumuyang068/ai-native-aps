import type {ExportInput} from './reportExport';
import {reportObjectKey,type TimeBucketReportData} from './timeBucketReportModel';
/** Only selects original rows. Existing ExportCsvButton owns verification and sidecar. */
export function bucketExportInput(input:ExportInput,report:TimeBucketReportData):ExportInput{
 const keys=new Set(report.objects.map(o=>o.key));return {...input,rows:input.rows.filter(r=>keys.has(reportObjectKey(r))),filters:{...input.filters,object_keys:[...keys],metrics:report.metrics.map(m=>m.id)}};
}
