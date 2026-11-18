import {expect,it} from 'vitest';
import {forecastPeriodMatrix,forecastEditRequest} from './forecastPeriodModel';
const a={id:'A',item_id:'I',location_id:'L',customer_id:'C',uom:'件',bucket_start:'2026-11-01',bucket_end:'2026-11-08',baseline_quantity:'10.125',gross_quantity:'12.125'};
it('keeps only the exact original series and leaves unnetted periods unknown',()=>{
 const m=forecastPeriodMatrix([a,{...a,id:'B',uom:'米'}],'A');
 expect(m?.buckets.map(b=>b.id)).toEqual(['A']);expect(m?.values.baseline.A).toBe('10.125');expect(m?.values.net.A).toBeNull();
});
it('rejects stale version and aggregate identity and preserves entered decimal',()=>{
 expect(()=>forecastEditRequest('V1','V2',[a],'A','12.500')).toThrow();
 expect(()=>forecastEditRequest('V1','V1',[a],'2026-11','12.500')).toThrow();
 expect(()=>forecastEditRequest('V1','V1',[a],'A','-1')).toThrow();
 expect(forecastEditRequest('V1','V1',[a],'A','12.500')).toEqual({row_id:'A',quantity:'12.500'});
 expect(a.baseline_quantity).toBe('10.125');
});
it('reads the original statistical Owner shape and never replaces a manual baseline with gross',()=>{
 const fresh={...a,baseline_quantity:undefined,quantity:'10.125',gross_quantity:'10.125',forecast_origin:'STATISTICAL_BASELINE'};
 expect(forecastPeriodMatrix([fresh],'A')?.values.baseline.A).toBe('10.125');
 expect(forecastPeriodMatrix([{...fresh,forecast_origin:'MANUAL_OVERRIDE',quantity:'50',gross_quantity:'50',statistical_quantity:'10.125'}],'A')?.values.baseline.A).toBe('10.125');
});
