import {describe,expect,it} from 'vitest';
import {activeWorkPage,planWorkGroups,networkWorkGroups,reportWorkPage,professionalDemandReportSearch} from './workspaceNavigationModel';

describe('unified plan work navigation',()=>{
 it('opens the computed professional candidate in demand reports without changing its identity',()=>{
  const original=new URLSearchParams('target=DRAFT-forecast&run_id=API-forecast&data_version_id=DATA-sim&analysis=forecast-netting&report=inventory&report_row=old&review_drafts=DRAFT-forecast&professional_return=order-inquiry');
  const next=professionalDemandReportSearch(original,'forecast-netting');
  expect(next.get('analysis')).toBe('reports');expect(next.get('report')).toBe('demand');
  expect(next.get('professional_return')).toBe('forecast-netting');expect(next.has('report_row')).toBe(false);
  for(const key of ['target','run_id','data_version_id','review_drafts'])expect(next.get(key)).toBe(original.get(key));
  expect(original.get('analysis')).toBe('forecast-netting');expect(original.get('report')).toBe('inventory');
 });
 it('keeps every work page uniquely addressable, including each report',()=>{
  const ids=planWorkGroups.flatMap(g=>g.pages.map(p=>p.id));
  expect(new Set(ids).size).toBe(ids.length);
  expect(ids).toEqual(expect.arrayContaining(['schedule','supply-work','manufacturing','composite','review','exceptions','demand-planning','inventory-policy','availability','resources','inventory','reports:demand','reports:resource','reports:inventory','reports:documents']));
 });
 it('highlights a report itself instead of two competing navigation levels',()=>{
  expect(activeWorkPage('reports','inventory')).toBe('reports:inventory');
  expect(activeWorkPage('resources')).toBe('resources');
 });
 it('preserves plan scope and return context when switching reports and clears the old row',()=>{
  const original=new URLSearchParams('target=DRAFT-one&run_id=API-one&data_version_id=DATA-one&report=demand&report_row=old&return_report=demand&grain=week&report_compare=1');
  const next=reportWorkPage(original,'resource');
  expect(next.get('analysis')).toBe('reports');expect(next.get('report')).toBe('resource');expect(next.has('report_row')).toBe(false);
  for(const name of ['target','run_id','data_version_id','return_report','grain','report_compare'])expect(next.get(name)).toBe(original.get(name));
  expect(original.get('report_row')).toBe('old');
 });
 it('does not expose manufacturing navigation inside network planning',()=>{
  const ids=networkWorkGroups.flatMap(g=>g.pages.map(p=>p.id));
  expect(ids).not.toContain('manufacturing');expect(ids).toContain('network-supply');
 });
 it('does not carry resource-only filters and hidden columns into another report',()=>{
  const p=reportWorkPage(new URLSearchParams('report=resource&report_query=SEW-01&report_chart_group=SEW-01&work_columns=load,ratio&report_issue_owner=M1&report_from=A&grain=month'),'inventory');
  for(const key of ['report_query','report_chart_group','work_columns','report_issue_owner'])expect(p.has(key)).toBe(false);
  expect(p.get('report_from')).toBe('A');expect(p.get('grain')).toBe('month');
 });
});
