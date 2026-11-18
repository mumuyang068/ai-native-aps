import {describe,expect,it} from 'vitest';
import {renderToStaticMarkup} from 'react-dom/server';
import {createElement} from 'react';
import type {PlanView} from '../api/contracts';
import {EditorFailureDetails,editorFailureObservation} from './EditorFailureDetails';

const base:PlanView={run:{state:'FAILED',solve_run_id:'RUN-current',input_hash:'hash-current',failure_code:'EDITOR_NATIVE_INFEASIBLE'},failure_details:{schema:'native-editor-failure/1',scope:'NATIVE_FAILURE_OBSERVATION',capture_status:'OBSERVED',run_id:'RUN-current',input_hash:'hash-current',schedule_certified:false,infeasibility_proven:false,problems:[{code:'overload',entity:'capacity',description:'native capacity observation',owner_name:'SEW-01',start_at:'2026-11-06T13:20:00',end_at:'2026-11-06T13:45:00'}],native_false_refs:['247'],resources:[{resource_id:'SEW-01',loadplans:[{operation_reference:'124',quantity:'-1',start_at:'2026-11-06T13:20:00',end_at:'2026-11-06T13:45:00'}]}]}};
const html=(view:PlanView)=>renderToStaticMarkup(createElement(EditorFailureDetails,{view,timezone:'Asia/Shanghai'}));

describe('failed Editor native observation',()=>{
 it('shows same-run native reason, owner, exact windows and related refs while preserving failure',()=>{const text=html(base);for(const value of ['计算失败','原生问题快照可能含既有问题','overload','capacity','SEW-01','native capacity observation','2026-11-06 13:20:00','2026-11-06 13:45:00','247','124'])expect(text).toContain(value);expect(base.run?.state).toBe('FAILED')});
 it.each([{run_id:'RUN-other'},{input_hash:'hash-other'},{scope:'SCHEDULE_CERTIFICATION'},{schedule_certified:true},{infeasibility_proven:true}])('refuses mismatched or certified attachment %j',patch=>{const view={...base,failure_details:{...base.failure_details!,...patch}} as PlanView;expect(editorFailureObservation(view).status).toBe('MISMATCH');expect(html(view)).not.toContain('native capacity observation')});
 it('does not reuse failure observations on a succeeded run',()=>expect(html({...base,run:{...base.run!,state:'SUCCEEDED'}})).toBe(''));
 it('makes unavailable capture and a missing native reason explicit without a source explanation',()=>{const text=html({...base,failure_details:{...base.failure_details!,capture_status:'UNAVAILABLE',missing_evidence:['native_failure_capture']}});expect(text).toContain('原生失败诊断不可用');expect(text).toContain('未取得原生失败记录');expect(text).not.toContain('native capacity observation');const missing=html({...base,failure_details:{...base.failure_details!,problems:[],missing_evidence:['native_problem_for_infeasible_operation']}});expect(missing).toContain('未提供对应问题原因')});
 it('preserves the existing recovery flow when a queue timeout has no native attachment',()=>expect(html({run:{state:'FAILED',failure_code:'RECOVERY_REQUIRED'}})).toBe(''));
 it('explains the absence of a native Editor diagnostic',()=>expect(html({run:base.run})).toContain('本次运行未提供原生失败诊断'));
});
