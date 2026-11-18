# 接入原生规划 Worker

工作台与计算服务分别运行。Worker 由 `planning_api:app` 提供服务，内部调用独立配置的 frePPLe Community 9.19 运行环境。本页说明两侧的接入合同。

1. 为 frePPLe 引擎配置独立运行环境、数据库、时区和访问账号。`config/engine-profile.json` 记录了研究时的引擎版本、镜像和时间口径。`scripts/start-engine.sh`、`scripts/bootstrap.py` 是引擎容器侧的启动和账号初始化代码。
2. 在 Worker 环境配置 `PLANNING_ENGINE_URL`、`PLANNING_ENGINE_CONFIG_FILE` 和 `PLANNING_ENGINE_EVIDENCE_DIR`。配置文件由维护人员在本机创建，提供 `P0_ENGINE_USER`、`P0_ENGINE_PASSWORD`、`P0_API_TOKEN`。证据目录需有与本次引擎一致的 `runtime.json`，且 Worker 能访问引擎用于导入的共享目录（默认位于 `engine-logs/data/default`）。
3. 以项目根目录为工作目录启动 Worker。

   ```powershell
   .\.venv\Scripts\python -m uvicorn planning_api:app --host 127.0.0.1 --port 9295
   ```

4. 在另一个终端为工作台设置 `PLANNING_COMPUTE_TOKEN`，使用与 Worker 一致的值。启动时显式选择 Worker。

   ```powershell
   .\.venv\Scripts\python -m scripts.local_workspace --worker-url http://127.0.0.1:9295
   ```

5. 从已确认、符合规划条件的数据版本建立计划，在页面检查计算状态、结果和约束。Canonical 企业版本需要与来源匹配、明确批准的研究政策。API 合同可从相应服务的 `/docs` 查看。

Git 仓库中的本地启动方式适用于开发和研究。引擎凭据、资料版本和运行归档应保存在各自配置的本机目录。
