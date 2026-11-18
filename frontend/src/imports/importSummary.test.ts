import {it,expect} from 'vitest';
import {reviewSections,currentDataVersion} from './importSummary';

it('groups by explicit confirmed target mapping and reports original row counts only',()=>{
  const task={source:{sheets:{客户订单:{rows:[[1],[2]]},产线:{rows:[[1]]},待说明:{rows:[[1],[2],[3]]}}},mappings:{客户订单:{target_sheet:'订单'},产线:{target_sheet:'生产资源'},待说明:{target_sheet:null}},confirmations:{orders:{reviewer:'核对人'}}} as any;
  const rows=reviewSections(task,{orders:'订单',production:'生产',mappings:'对应关系',unknown:'其他'});
  expect(rows[0]).toMatchObject({confirmed:true,rawRows:2,sheets:[{name:'客户订单',target:'订单'}]});
  expect(rows[1]).toMatchObject({confirmed:false,rawRows:1});
  expect(rows[2].sheets).toHaveLength(3);
  expect(rows[3].sheets).toEqual([]);
  expect(task.mappings.待说明.target_sheet).toBeNull();
});

it('does not confuse a saved older revision or another import with the current version',()=>{
  const task={id:'IMP-a',revision:3,version_ids:['DATA-old','DATA-other','DATA-current']} as any;
  const versions=[{id:'DATA-old',import_id:'IMP-a',import_revision:2},{id:'DATA-other',import_id:'IMP-b',import_revision:3}] as any;
  expect(currentDataVersion(task,versions)).toBeUndefined();
  expect(currentDataVersion(task,[...versions,{id:'DATA-current',import_id:'IMP-a',import_revision:3}] as any)?.id).toBe('DATA-current');
  expect(currentDataVersion({...task,version_ids:[]},[{id:'DATA-current',import_id:'IMP-a',import_revision:3}] as any)).toBeUndefined();
});

it('uses the server-verified saved business version after administrative revision increments',()=>{
 const task={id:'IMP-a',revision:3,current_version_id:'DATA-saved',version_ids:['DATA-saved']} as any;
 const versions=[{id:'DATA-saved',import_id:'IMP-a',import_revision:2}] as any;
 expect(currentDataVersion(task,versions)?.id).toBe('DATA-saved');
 expect(currentDataVersion({...task,current_version_id:null},versions)).toBeUndefined();
 expect(currentDataVersion({...task,id:'IMP-other'},versions)).toBeUndefined();
});
