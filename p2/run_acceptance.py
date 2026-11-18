"""Reproduce static sample acceptance, recording source hashes and explicit gates."""
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from p2.preflight import load_workbook, inspect_tables, sample_review, PROFILE_SHA

ROOT=Path(__file__).resolve().parents[1]
HERE=ROOT/'p2'

def main():
    output=HERE/'artifacts/acceptance'
    output.mkdir(parents=True,exist_ok=True)
    records=[]
    for file in sorted((HERE/'samples').glob('*.xlsx')):
        tables=load_workbook(file)
        before=inspect_tables(tables)
        review=sample_review(tables)
        after=inspect_tables(tables,review)
        for name,value in [('before-review',before),('simulated-review',review),('after-review',after)]:
            (output/(file.stem+'-'+name+'.json')).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        if 'canonical' in after:
            for name in ('canonical','native'):
                (output/(file.stem+'-'+name+'.json')).write_text(json.dumps(after[name],ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        records.append({'file':file.name,'sha256':tables['sha256'],'before_review':before['status'],
            'after_simulated_review':after['status'],'mapped_cells':after['counts']['mapped_cells'],
            'blocking_issues':after['counts']['blocking_issues'],
            'issue_codes':sorted({x['code'] for x in after['issues']}),'engine_executed':False})
    assert records[0]['before_review']=='NEEDS_CONFIRMATION'
    assert records[0]['after_simulated_review']=='READY_FOR_STATIC_EXPORT'
    assert all(x['after_simulated_review']=='BLOCKED' for x in records[1:])
    baseline=ROOT/'fixtures/baseline.json'
    expected='bed143fe7cd13bdcdd3b524030ea0bbfb37e97f5fc0ba5f786d7c35fdc3fb646'
    assert hashlib.sha256(baseline.read_bytes()).hexdigest()==expected
    frozen='p1.1-verified-20261014-r1'
    runtime_files=['planning_adapter.py','planning_api.py','agent.py','service.py','plan_context.py','interaction_state.py','p1_api.py','semantics.py','drafts.py','run_store.py']
    result=subprocess.run(['git','diff','--exit-code',frozen,'--',*runtime_files,'web','fixtures/baseline.json'],cwd=ROOT,capture_output=True,text=True)
    if result.returncode:raise RuntimeError('Frozen runtime files differ: '+result.stdout)
    summary={'stage':'P2.0','scope':'OFFLINE_SYNTHETIC_ONLY','mapping_sha256':PROFILE_SHA,
             'frozen_p11_tag':frozen,'p0_baseline_sha256':expected,'frozen_runtime_unchanged':True,
             'engine_executed':False,'ai_model_called':False,'cases':records}
    (output/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
