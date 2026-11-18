from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles


def mount_workspace(app: FastAPI, dist: Path) -> None:
    if (dist / 'index.html').is_file():
        app.mount('/workspace', StaticFiles(directory=dist, html=True), name='workspace')
    else:
        @app.get('/workspace/')
        def unavailable():
            raise HTTPException(503, detail={'code': 'WORKSPACE_NOT_BUILT', 'message': '工作区暂不可用，请完成前端构建后重启本服务。'})
