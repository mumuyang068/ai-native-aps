import {it,expect} from 'vitest';
import {bucketExportInput} from './timeBucketExport';
import {planningBucketReport} from './reportMetricDefinitions';
import {prepareExport,type ExportInput} from './reportExport';
const scope={planId:'P',versionId:'V',runId:'R',target:'baseline'};
const input:ExportInput={scope,kind:'demand',status:'PRESENT',sourceUrl:'/reports',fields:[],filters:{},data:{identity:{plan_id:'P',data_version_id:'V',run_id:'R',target:'baseline',profile:'enterprise-accessory-supply-v1'},timezone:'Asia/Shanghai',evidence_files:[{artifact:'delivery.json',sha256:'a'.repeat(64)}],demand:{validation:{status:'PASS'}}},rows:[{item_id:'A',uom:'件',start_at:'2026-11-01',end_at:'2026-12-01',due_quantity:'10.125'},{item_id:'B',uom:'米',start_at:'2026-11-01',end_at:'2026-12-01',due_quantity:'20'}]};
it('exports the same owner rows and rejects missing evidence rather than bypassing the export gate',async()=>{
 const report=planningBucketReport('scope',[input.rows[0]],'demand');
 expect(bucketExportInput(input,report).rows).toEqual([input.rows[0]]);
 expect(bucketExportInput(input,report).rows[0].due_quantity).toBe('10.125');
 await expect(prepareExport(bucketExportInput({...input,data:{...input.data,demand:{validation:{status:'NOT_CHECKED'}}}},report))).rejects.toThrow();
 await expect(prepareExport(bucketExportInput({...input,data:{...input.data,evidence_files:[]}},report))).rejects.toThrow();
});
