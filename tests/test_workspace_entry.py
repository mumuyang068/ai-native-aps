from pathlib import Path
from fastapi import FastAPI
from fastapi.testclient import TestClient
from workspace_ui import mount_workspace


def test_built_workspace_and_assets_do_not_swallow_api(tmp_path):
    dist = tmp_path / 'dist'
    dist.mkdir()
    (dist / 'index.html').write_text('<h1>生产计划</h1>', encoding='utf-8')
    (dist / 'main.js').write_text('export const ready=true', encoding='utf-8')
    app = FastAPI()
    app.get('/health')(lambda: {'status': 'OK'})
    mount_workspace(app, dist)
    client = TestClient(app)
    assert client.get('/workspace/').status_code == 200
    assert '生产计划' in client.get('/workspace/').text
    assert client.get('/workspace/main.js').status_code == 200
    assert client.get('/health').json() == {'status': 'OK'}
    assert client.get('/workspace/missing.js').status_code == 404


def test_missing_build_only_disables_new_entry(tmp_path):
    app = FastAPI()
    app.get('/health')(lambda: {'status': 'OK'})
    mount_workspace(app, tmp_path / 'missing')
    client = TestClient(app)
    assert client.get('/health').status_code == 200
    response = client.get('/workspace/')
    assert response.status_code == 503
    assert response.json()['detail']['code'] == 'WORKSPACE_NOT_BUILT'


def test_existing_product_routes_survive_workspace_registration(tmp_path):
    from p1_api import create_app
    client = TestClient(create_app(tmp_path))
    assert client.get('/').status_code == 200
    assert client.get('/imports').status_code == 200
    assert client.get('/v1/imports').json() == []
    assert client.get('/health').json()['status'] == 'OK'
