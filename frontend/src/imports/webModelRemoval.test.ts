import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {MemoryRouter} from 'react-router';
import {expect,it} from 'vitest';
import {CanonicalReview} from './CanonicalFacts';

it('keeps manual fact confirmation and historical suggestions without an active model',()=>{
 const canonical={enterprise_id:'SIM',tables:{demand_lines:[]},objects:[],data_status:'DATA_ACCEPTED',timezone:'Asia/Shanghai',unresolved_policies:[],issues:[]};
 const task={id:'IMP-1',canonical,canonical_groups:['demand_lines'],confirmations:{},readiness:{can_save_facts:true},ai_events:[{scope:'CANONICAL_RELATION_REVIEW',id:'historical-only'}]} as any;
 const html=renderToStaticMarkup(createElement(MemoryRouter,null,createElement(CanonicalReview,{task,busy:false,error:'',note:'',onSave:()=>{},onConfirm:()=>{}})));
 expect(html).toContain('保存企业事实版本');expect(html).toContain('确认选定领域');expect(html).toContain('历史关联核对记录');
 expect(html).not.toContain('辅助核对关联');expect(html).not.toContain('提出关联建议');
});
