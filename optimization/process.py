"""A single registered solver child; every other process remains denied."""
import subprocess,threading
from pathlib import Path
_permit=threading.local()

def authorized(event_args):
 expected=getattr(_permit,'expected',None)
 return bool(expected and tuple(event_args)==expected)

def run(command,cwd,env,timeout):
 if len(command)!=10 or command[1:4]!=['-B','-m','optimization.runner'] or command[6]!='--algorithm' or command[8]!='--budget':raise ValueError('OPTIMIZER_PROCESS_PROTOCOL')
 if command[7] not in {'cp_sat','vns','late_acceptance','tabu','pyjobshop'} or not command[9].isdigit() or not 5<=int(command[9])<=600:raise ValueError('OPTIMIZER_PROCESS_SETTINGS')
 if Path(command[4]).resolve().parent!=Path(command[5]).resolve().parent:raise ValueError('OPTIMIZER_PROCESS_PATH')
 if set(env)-{'SystemRoot','SYSTEMROOT','WINDIR','PATH','TEMP','TMP','PYTHONDONTWRITEBYTECODE'}:raise ValueError('OPTIMIZER_PROCESS_ENV')
 # CPython audits the Windows command line after list2cmdline conversion.
 _permit.expected=(command[0],subprocess.list2cmdline(command),str(cwd),env)
 try:return subprocess.run(command,executable=command[0],cwd=str(cwd),env=env,capture_output=True,text=True,encoding='utf8',timeout=timeout)
 finally:_permit.expected=None
