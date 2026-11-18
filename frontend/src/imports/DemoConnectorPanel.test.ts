import {describe,expect,it} from 'vitest';
import {connectorCommitBody,verifyConnectorPreview,findCommittedPreview} from './DemoConnectorPanel';

const preview={id:'PREVIEW-1',status:'READY',checkpoint:'T1',as_of:'2026-10-26T10:00:00+08:00',enterprise_id:'DEMO-GARMENT',parent_version_id:'DATA-parent',import_id:'IMP-1',revision:4,groups:['demand_lines','stock_positions'],summary:{added:2,updated:1,unchanged:4,late:1,conflicts:0},sources:[],anomalies:[],readiness:{ready:true}};

describe('demo connector confirmation contract',()=>{
 it('binds preview identity to the selected checkpoint and enterprise',()=>{
  expect(verifyConnectorPreview(preview,'T1','DEMO-GARMENT')).toBe(preview);
  expect(()=>verifyConnectorPreview(preview,'T2','DEMO-GARMENT')).toThrow();
  expect(()=>verifyConnectorPreview(preview,'T1','other-enterprise')).toThrow();
  expect(()=>verifyConnectorPreview({...preview,id:''},'T1','DEMO-GARMENT')).toThrow();
  expect(()=>verifyConnectorPreview({...preview,status:'COMMITTED'},'T1','DEMO-GARMENT')).toThrow();
 });
 it('keeps a blocked source preview inspectable before an import revision exists',()=>{
  const blocked={...preview,status:'BLOCKED',revision:null,import_id:null,groups:[],anomalies:[{code:'SOURCE_UNAVAILABLE',system:'WMS'}]};
  expect(verifyConnectorPreview(blocked,'T1','DEMO-GARMENT')).toBe(blocked);
  expect(()=>connectorCommitBody(blocked,'T1','DEMO-GARMENT','王计划',[])).toThrow();
  expect(()=>connectorCommitBody({...preview,revision:null},'T1','DEMO-GARMENT','王计划',preview.groups)).toThrow();
  expect(verifyConnectorPreview({...blocked,groups:undefined},'T1','DEMO-GARMENT').groups).toEqual([]);
 });
 it('requires a named reviewer and explicit confirmation of every preview group',()=>{
  expect(connectorCommitBody(preview,'T1','DEMO-GARMENT','  王计划  ',['stock_positions','demand_lines'])).toEqual({preview_id:'PREVIEW-1',revision:4,reviewer:'王计划',groups:['stock_positions','demand_lines']});
  for(const [reviewer,groups] of [['',preview.groups],['  ',preview.groups],['王计划',[]],['王计划',['demand_lines']],['王计划',['demand_lines','unknown']]]){
   expect(()=>connectorCommitBody(preview,'T1','DEMO-GARMENT',reviewer as string,groups as string[])).toThrow();
  }
 });
 it('blocks stale, blocked or unchanged previews from producing another version',()=>{
  for(const status of ['BLOCKED','UNCHANGED'])expect(()=>connectorCommitBody({...preview,status},'T1','DEMO-GARMENT','王计划',preview.groups)).toThrow();
  expect(()=>connectorCommitBody(preview,'T2','DEMO-GARMENT','王计划',preview.groups)).toThrow();
  expect(()=>connectorCommitBody(preview,'T1','other-enterprise','王计划',preview.groups)).toThrow();
 });
 it('recovers only the exact committed preview with a data version after an uncertain write',()=>{
  const committed={...preview,status:'COMMITTED',version_id:'DATA-current',reviewer:'王计划'};
  expect(findCommittedPreview([{...committed,id:'PREVIEW-other'},committed],'PREVIEW-1')).toBe(committed);
  expect(findCommittedPreview([{...committed,status:'READY'}],'PREVIEW-1')).toBeUndefined();
  expect(findCommittedPreview([{...committed,version_id:null}],'PREVIEW-1')).toBeUndefined();
 });
});
