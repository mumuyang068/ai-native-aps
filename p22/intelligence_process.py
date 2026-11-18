"""Only one exact registered optional algorithm invocation may cross SourceGuard."""
import json,os,subprocess,threading,uuid
from pathlib import Path
_permit=threading.local()

def authorized(event_args):
    return bool(getattr(_permit,'expected',None) and tuple(event_args)==_permit.expected)

def run(command,*,cwd,env,timeout):
    if len(command)!=6 or command[1:3]!=['-B','-m'] or command[3] not in {'p22.intelligence_runner','p22.intelligence_inventory_runner'}:raise ValueError('ALGORITHM_PROCESS_PROTOCOL')
    if Path(command[4]).resolve().parent!=Path(command[5]).resolve().parent or any(Path(p).suffix!='.json' for p in command[4:]):raise ValueError('ALGORITHM_PROCESS_PATH')
    if set(env)-{'SystemRoot','SYSTEMROOT','WINDIR','PATH','TEMP','TMP','PYTHONDONTWRITEBYTECODE'}:raise ValueError('ALGORITHM_PROCESS_ENV')
    _permit.expected=(command[0],subprocess.list2cmdline(command),str(cwd),env)
    # Windows child tracebacks may use the local code page. A diagnostic decode
    # error must not replace the actual algorithm failure with stderr=None.
    # The authoritative result remains a separately validated strict UTF-8 JSON file.
    try:return subprocess.run(command,executable=command[0],cwd=str(cwd),env=env,capture_output=True,text=True,encoding='utf8',errors='replace',timeout=timeout)
    finally:_permit.expected=None

def inventory_library(request,folder):
    """Actual pinned library execution; parent keeps the business policy Owner."""
    from p22.foundation_projection import digest,require
    python=os.environ.get('PLANNING_INVENTORY_PYTHON','')
    require(bool(python) and Path(python).is_file(),'INVENTORY_RUNTIME_UNAVAILABLE')
    root=Path(folder)/'inventory-algorithm';root.mkdir(exist_ok=True)
    key=uuid.uuid4().hex;source=root/(key+'-input.json');target=root/(key+'-result.json')
    source.write_text(json.dumps(dict(request=request),ensure_ascii=False,allow_nan=False),encoding='utf8')
    env={k:v for k,v in os.environ.items() if k in {'SystemRoot','SYSTEMROOT','WINDIR','PATH','TEMP','TMP'}}
    env['PYTHONDONTWRITEBYTECODE']='1'
    result=run([python,'-B','-m','p22.intelligence_inventory_runner',str(source),str(target)],
        cwd=Path(__file__).resolve().parent.parent,env=env,timeout=60)
    require(result.returncode==0,'INVENTORY_ALGORITHM_FAILED:'+result.stderr[-1000:])
    proof=json.loads(target.read_text('utf8'))
    require(proof.get('request_sha256')==digest(request),'INVENTORY_ALGORITHM_INPUT_CHANGED')
    require(proof.get('receipt',{}).get('pinned_version')=='1.0.2','INVENTORY_ALGORITHM_VERSION')
    require(proof['receipt'].get('check') in {'PASS','SKIPPED'},'INVENTORY_ALGORITHM_RECEIPT')
    return proof
