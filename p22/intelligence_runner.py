"""Optional forecasting runtime: exact JSON I/O, no business stores or old SQL."""
import json,sys,time
from pathlib import Path

def main():
    source,target=map(lambda s:Path(s).resolve(),sys.argv[1:])
    if source.parent!=target.parent or source.suffix!='.json' or target.suffix!='.json':raise ValueError('ALGORITHM_RUNNER_PATH')
    from scripts.data_link_guard import install_sql_audit
    install_sql_audit(source.parent)
    body=json.loads(source.read_text('utf8'));p=body['parameters'];started=time.perf_counter()
    from demand_intelligence import forecast_history
    result=forecast_history(body['rows'],model=p['model'],training_cutoff=p['training_cutoff'],issue_time=p['issue_time'],
        horizon=p['horizon'],freq=p.get('freq','W-MON'),observation_kind=p.get('observation_kind','OBSERVED_DEMAND'),season_length=13,policy_version='EXP06-RESEARCH-STATISTICAL',censored_policy='exclude_series',interval_level=80)
    result['elapsed_seconds']=time.perf_counter()-started
    target.write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf8')

if __name__=='__main__':main()
