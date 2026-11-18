from pathlib import Path
import threading
from fastapi import Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from agent import AgentError, redact
from diagnosis.store import TaskStore


class StartInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_id: str = Field(pattern='^[a-f0-9]{32}$')
    question: str = Field(min_length=1,max_length=4000)
    target: str = 'baseline'
    expected_run_id: str = Field(pattern='^API-[a-f0-9]{16}$')
    selection: dict | None = None


class ResumeInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    order_id: str | None = None


def register_diagnosis(app, root, scoped, authorize):
    initialization_lock=threading.RLock()
    def manager():
        with initialization_lock:
            if not hasattr(app.state,'diagnosis_runtime'):
                try:
                    from diagnosis.runtime import DiagnosisRuntime
                except ImportError:
                    raise HTTPException(503,detail={'code':'DIAGNOSIS_RUNTIME_MISSING','message':'调查运行环境未就绪，请使用项目诊断环境启动；原计划操作仍可使用。'}) from None
                store=TaskStore(Path(root)/'diagnosis');store.recover_interrupted()
                app.state.diagnosis_runtime=DiagnosisRuntime(store)
        return app.state.diagnosis_runtime

    def guarded(action):
        try:return action()
        except KeyError:raise HTTPException(404,detail={'code':'DIAGNOSIS_NOT_FOUND','message':'当前计划没有这份调查。'}) from None
        except (ValueError,AgentError) as error:
            code=str(error).split(':')[0]
            raise HTTPException(503 if code.startswith(('MODEL_','COMPUTE_')) else 422,
                                detail={'code':code,'message':redact(error)}) from None

    def owned(tid,plan_id):
        task=manager().store.get(tid)
        if task['bound']['scope']['plan_id']!=plan_id:raise KeyError(tid)
        return task

    @app.get('/v1/diagnoses')
    def list_tasks(plan_id:str):
        return guarded(lambda:manager().store.list(plan_id))

    @app.post('/v1/diagnoses',dependencies=[Depends(authorize)])
    def create(body:StartInput,plan_id:str):
        raise HTTPException(410,detail={'code':'WEB_MODEL_REMOVED','message':'本项目已移除Web模型调查；已有业务查询及历史记录保留。'})

    @app.get('/v1/diagnoses/{tid}')
    def get_task(tid:str,plan_id:str):
        return guarded(lambda:manager().store.public(owned(tid,plan_id)))

    @app.get('/v1/diagnoses/{tid}/evidence/{eid}')
    def get_evidence(tid:str,eid:str,plan_id:str):
        def action():
            matches=[e for e in owned(tid,plan_id)['ledger'] if e['id']==eid]
            if not matches:raise KeyError(eid)
            return matches[0]
        return guarded(action)

    @app.post('/v1/diagnoses/{tid}/resume',dependencies=[Depends(authorize)])
    def resume(tid:str,body:ResumeInput,plan_id:str):
        raise HTTPException(410,detail={'code':'WEB_MODEL_REMOVED','message':'本项目已移除Web模型调查，不再恢复模型调用。'})

    @app.post('/v1/diagnoses/{tid}/pause',dependencies=[Depends(authorize)])
    def pause(tid:str,plan_id:str):
        return guarded(lambda:manager().store.public(manager().pause(owned(tid,plan_id)['id'])))

    @app.post('/v1/diagnoses/{tid}/cancel',dependencies=[Depends(authorize)])
    def cancel(tid:str,plan_id:str):
        return guarded(lambda:manager().store.public(manager().cancel(owned(tid,plan_id)['id'])))

    @app.on_event('shutdown')
    def close_runtime():
        if hasattr(app.state,'diagnosis_runtime'):
            runtime=app.state.diagnosis_runtime
            runtime.pool.shutdown(wait=True,cancel_futures=True)
            runtime.connection.close()
