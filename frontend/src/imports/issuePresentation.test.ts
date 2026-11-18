import {expect,it} from 'vitest';
import {presentIssue} from './issuePresentation';
it('explains capability blocks without exposing implementation names or permitting bypass',()=>{
 for(const code of ['UNSUPPORTED_RESERVATION','UNSUPPORTED_OPEN_PURCHASE']){
  const issue={code,severity:'CAPABILITY',message:'internal existing_orders',resolution:'adapter',source_locations:[{sheet:'库存',cell:'J6'}]};
  const p=presentIssue(issue);expect(p.title).not.toContain('existing_orders');expect(p.message).not.toContain('adapter');expect(p.resolution).toContain('保留');expect(p.resolution).toContain('不能');expect(p.blocked).toBe(true);expect(issue.source_locations[0].cell).toBe('J6');
 }
});
it('preserves unknown error messages rather than inventing a resolution',()=>{const p=presentIssue({code:'NEW_CODE',severity:'ERROR',message:'准确的业务原因',resolution:'请核对原表',source_locations:[]});expect(p.title).toBe('准确的业务原因');expect(p.resolution).toBe('请核对原表')});
