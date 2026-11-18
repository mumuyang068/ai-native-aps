import {it,expect} from 'vitest';
import {selectionContext,diagnosisMatches} from './context';
it('preserves the actual originating order on operation navigation only within the same view',()=>{
 expect(selectionContext('P|base',{kind:'task',id:'RUN:OP:53'},{key:'P|base',id:'O-C301-L1'})).toEqual({type:'operation',id:'RUN:OP:53',order_id:'O-C301-L1'});
 expect(selectionContext('P|other',{kind:'task',id:'RUN:OP:53'},{key:'P|base',id:'O-C301-L1'})).toEqual({type:'operation',id:'RUN:OP:53'});
});
it('does not accept the previous run, scenario or data version as current',()=>{
 const scope={plan_id:'P',target:'baseline',run_id:'RUN1',result_hash:'h1',data_version_id:'V1'};
 expect(diagnosisMatches({scope},scope)).toBe(true);
 for(const change of [{run_id:'RUN2'},{target:'DRAFT'},{data_version_id:'V2'},{result_hash:'h2'}])expect(diagnosisMatches({scope},{...scope,...change})).toBe(false);
});
