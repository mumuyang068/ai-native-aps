"""Local Codex app-server adapter, with a fail-closed native-tool boundary.

This module never reads credentials or calls a model HTTP endpoint. Codex owns
its normal authentication. Domain tools are supplied by the APS service.

Protocol fixtures test the driver; they do not certify a model or sandbox.
The installed Windows 0.142.5 sandbox failed the restricted-read probe (an
outside sentinel was readable). The public gateway therefore refuses turns
until that platform boundary is qualified. There is no environment/config
switch that bypasses qualification.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import re
import shutil
import subprocess
import threading
import time
from typing import Callable, Sequence

from jsonschema import Draft202012Validator


PROVIDER = "local_codex_stdio"
PROFILE = "aps-product-isolated"
_MAX_LINE = 2_000_000


def isolation_status() -> dict:
    """Current qualification, deliberately separate from CLI login status.

    EXPAND-07 evidence/model-probe/sandbox-isolation.json records the actual
    Windows probe: OUTSIDE_READ_ALLOWED and WRITE_DENIED. A new CLI/platform
    needs another qualification; read-only mode alone is not a read boundary.
    """
    return {
        "certified": False,
        "code": "RESTRICTED_READ_ISOLATION_NOT_CERTIFIED",
        "message": "当前本机 Codex 沙箱未通过工作目录外文件读取隔离核验；暂不能用于产品内计划助手。",
        "tested_cli": "0.142.5",
        "tested_platform": "windows",
        "observed_outside_read": "ALLOWED",
        "observed_write": "DENIED",
    }


def _redact(value: str) -> str:
    # Redact only bounded diagnostics; never forward raw stderr to the UI.
    value = re.sub(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+", r"\1[REDACTED]", str(value))
    value = re.sub(r"\bsk-[A-Za-z0-9_-]{8,}", "[REDACTED]", value)
    value = re.sub(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", "[REDACTED]", value)
    value = re.sub(r"(https?://)[^/@\s]+:[^/@\s]+@", r"\1[REDACTED]@", value)
    for name, secret in os.environ.items():
        if name.upper().endswith(("_KEY", "_TOKEN", "_PASSWORD")) and len(secret) >= 8:
            value = value.replace(secret, "[REDACTED]")
    return value[:1500]


def _default_command() -> list[str]:
    """Resolve npm's installed executable, without piping through a shell shim.

    The npm PowerShell shim enumerates piped input before launching Node and
    consequently cannot proxy the app-server's bidirectional JSONL transport.
    This follows the same platform-package layout as the installed codex.js.
    """
    executable = shutil.which("codex") or shutil.which("codex.ps1")
    if executable and os.name == "nt":
        base = Path(executable).parent / "node_modules" / "@openai" / "codex"
        target = "aarch64-pc-windows-msvc" if os.environ.get("PROCESSOR_ARCHITECTURE", "").upper() == "ARM64" else "x86_64-pc-windows-msvc"
        package = "codex-win32-arm64" if target.startswith("aarch64") else "codex-win32-x64"
        candidates = [base / "node_modules" / "@openai" / package / "vendor" / target / "bin" / "codex.exe", base / "vendor" / target / "bin" / "codex.exe"]
        for candidate in candidates:
            if candidate.is_file():
                return [str(candidate)]
    return [executable] if executable else ["codex"]


class _Failure(Exception):
    def __init__(self, code: str, message: str, *, outcome_unknown=False):
        self.code, self.message, self.outcome_unknown = code, message, outcome_unknown
        super().__init__(message)


class CodexGateway:
    def __init__(self, command: Sequence[str] | None = None, timeout_seconds=90, max_tool_calls=32, capabilities_path: Path | None = None):
        self.command = list(command or _default_command())
        if not self.command or any(not isinstance(v, str) or not v for v in self.command):
            raise ValueError("Invalid Codex command")
        self.timeout_seconds = min(max(float(timeout_seconds), .1), 300)
        self.max_tool_calls = min(max(int(max_tool_calls), 1), 100)
        # Only the hosting service chooses this local qualification receipt.
        # It is not an argument accepted from a model or browser request.
        self.capabilities_path = Path(capabilities_path) if capabilities_path else None

    def capabilities(self, cwd: Path) -> dict:
        """Cheap local qualification lookup; does not start a model or login."""
        status = isolation_status()
        certification, message = "NOT_CERTIFIED", status.get("message")
        if self.capabilities_path is not None:
            try:
                receipt = json.loads(self.capabilities_path.read_text(encoding="utf-8-sig"))
                if not isinstance(receipt, dict) or receipt.get("provider") != PROVIDER:
                    raise ValueError("Invalid capability receipt")
                status = receipt.get("isolation") or status
                if not isinstance(status, dict):
                    raise ValueError("Invalid isolation receipt")
                # Missing/partial receipts cannot grant capability.
                status = {**status, "certified": receipt.get("available") is True and status.get("certified") is True}
                certification = receipt.get("certification") if receipt.get("certification") in ("NOT_CERTIFIED", "ENVIRONMENT_BLOCKED") else "NOT_CERTIFIED"
                message = receipt.get("message") or status.get("message")
            except (OSError, ValueError, TypeError):
                status = {"certified": False, "message": "本机 Codex 隔离核验记录不可用。"}
                message = status["message"]
        if not Path(cwd).is_dir():
            return {"available": False, "certification": "ENVIRONMENT_BLOCKED", "code": "MODEL_WORKSPACE_UNAVAILABLE", "message": "计划助手的独立工作目录不可用。", "provider": PROVIDER}
        return {"available": bool(status.get("certified")), "certification": certification, "code": "MODEL_CAPABILITY_UNAVAILABLE" if not status.get("certified") else None, "message": message or "本机协议已核验；真实模型操作仍需验收。", "provider": PROVIDER, "isolation": status}

    @staticmethod
    def _settings(cwd: Path) -> dict:
        return {
            "features": {"shell_tool": False, "apps": False, "plugins": False, "hooks": False, "multi_agent": False, "view_image": False, "code_mode": {"enabled": False}},
            "web_search": "disabled", "tools": {"view_image": False},
            "approval_policy": "never", "project_root_markers": [], "project_doc_max_bytes": 0,
            "permissions": {PROFILE: {"filesystem": {":root": "deny", ":minimal": "read", str(cwd): "read"}, "network": {"enabled": False}}},
        }

    def run_turn(self, prompt: str, *, tool_specs: list[dict], execute_tool: Callable, cwd: Path, thread_id=None, on_event=None, cancel_event=None) -> dict:
        result = {"provider": PROVIDER, "thread_id": thread_id, "turn_id": None, "model": None, "status": "failed", "text": "", "usage": {"status": "NOT_COLLECTED"}, "tools": [], "error": None}
        if cancel_event is not None and cancel_event.is_set():
            result["status"] = "cancelled"
            result["error"] = {"code": "MODEL_CANCELLED", "message": "任务已取消。"}
            return result
        capability = self.capabilities(Path(cwd))
        if not capability["available"]:
            result["error"] = {"code": capability["code"], "message": capability["message"]}
            return result
        workspace = Path(cwd).resolve()
        # Never silently use a repository/business-data folder as model cwd.
        if any(workspace.iterdir()):
            result["error"] = {"code": "MODEL_WORKSPACE_NOT_EMPTY", "message": "计划助手需要独立的空工作目录。"}
            return result
        validators = {}
        for spec in tool_specs:
            name = spec.get("name", "")
            if spec.get("type") != "function" or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name) or name in validators:
                result["error"] = {"code": "INVALID_TOOL_SPEC", "message": "专业工具合同无效。"}
                return result
            try:
                Draft202012Validator.check_schema(spec["inputSchema"])
                validators[name] = Draft202012Validator(spec["inputSchema"])
            except Exception:
                result["error"] = {"code": "INVALID_TOOL_SPEC", "message": "专业工具参数合同无效。"}
                return result
        process = None
        deadline = time.monotonic() + self.timeout_seconds
        incoming = queue.Queue()
        stderr = []
        final_messages = []

        def emit(kind, **payload):
            if on_event:
                on_event({"type": kind, **payload})

        def check_deadline():
            if cancel_event is not None and cancel_event.is_set():
                raise _Failure("MODEL_CANCELLED", "任务已取消。", outcome_unknown=bool(result["tools"]))
            if time.monotonic() >= deadline:
                raise _Failure("MODEL_TIMEOUT", "模型会话超过等待上限。", outcome_unknown=bool(result["tools"]))

        def send(value):
            try:
                process.stdin.write(json.dumps(value, ensure_ascii=False) + "\n")
                process.stdin.flush()
            except (BrokenPipeError, OSError, ValueError):
                raise _Failure("MODEL_STREAM_CLOSED", "模型连接已断开。", outcome_unknown=bool(result["tools"])) from None

        def read_stdout():
            try:
                for line in process.stdout:
                    if len(line) > _MAX_LINE:
                        incoming.put(_Failure("MODEL_PROTOCOL_ERROR", "模型事件超过大小限制。")); return
                    try:
                        value = json.loads(line)
                        if not isinstance(value, dict):
                            raise ValueError()
                        incoming.put(value)
                    except ValueError:
                        incoming.put(_Failure("MODEL_PROTOCOL_ERROR", "模型事件格式无效。")); return
            finally:
                incoming.put(None)

        def read_stderr():
            for line in process.stderr:
                if len(stderr) < 12:
                    stderr.append(_redact(line))

        def next_message():
            while True:
                check_deadline()
                try:
                    value = incoming.get(timeout=.05)
                except queue.Empty:
                    continue
                if value is None:
                    raise _Failure("MODEL_STREAM_CLOSED", "模型连接在完成前断开。", outcome_unknown=bool(result["tools"]))
                if isinstance(value, _Failure):
                    raise value
                return value

        def dispatch(message):
            method, params = message.get("method"), message.get("params") or {}
            if "id" in message and method:
                if method != "item/tool/call":
                    send({"id": message["id"], "error": {"code": -32601, "message": "Capability unavailable"}})
                    raise _Failure("MODEL_CAPABILITY_UNAVAILABLE", "模型请求了未获准的本机能力。")
                if params.get("threadId") != result["thread_id"] or params.get("turnId") != result["turn_id"]:
                    raise _Failure("MODEL_CONTEXT_MISMATCH", "模型工具请求的会话身份不匹配。")
                name, arguments = params.get("tool"), params.get("arguments")
                if name not in validators or params.get("namespace"):
                    raise _Failure("FORBIDDEN_TOOL", "模型请求了未获准的专业工具。")
                if not isinstance(arguments, dict) or not validators[name].is_valid(arguments):
                    raise _Failure("INVALID_TOOL_ARGUMENTS", "模型提交的专业工具参数无效。")
                if len(result["tools"]) >= self.max_tool_calls:
                    raise _Failure("MODEL_TOOL_BUDGET_EXHAUSTED", "本轮专业工具调用已达到上限。")
                record = {"name": name, "call_id": params.get("callId"), "status": "started"}
                result["tools"].append(record)
                emit("tool_started", **record)
                callback_result = queue.Queue(maxsize=1)
                def invoke():
                    try:
                        callback_result.put((True, execute_tool(name, arguments)))
                    except Exception:
                        callback_result.put((False, {"error": {"code": "DOMAIN_TOOL_FAILED", "message": "专业工具执行失败，请核对服务端任务状态。"}}))
                threading.Thread(target=invoke, daemon=True).start()
                while True:
                    check_deadline()
                    try:
                        success, output = callback_result.get(timeout=.05); break
                    except queue.Empty:
                        continue
                record["status"] = "completed" if success else "failed"
                emit("tool_completed", **record)
                send({"id": message["id"], "result": {"success": success, "contentItems": [{"type": "inputText", "text": json.dumps(output, ensure_ascii=False)}]}})
                return
            if method == "item/completed":
                item = params.get("item") or {}
                if item.get("type") == "agentMessage" and item.get("phase") in (None, "final_answer"):
                    final_messages.append(item.get("text", ""))
                elif item.get("type") in ("commandExecution", "fileChange", "webSearch", "mcpToolCall"):
                    raise _Failure("MODEL_CAPABILITY_UNAVAILABLE", "检测到未获准的本机能力，已停止模型会话。", outcome_unknown=True)
            elif method == "thread/tokenUsage/updated":
                result["usage"] = {"status": "PRESENT", "value": params.get("tokenUsage")}
            elif method == "turn/completed":
                turn = params.get("turn") or {}
                if turn.get("id") != result["turn_id"]:
                    raise _Failure("MODEL_CONTEXT_MISMATCH", "模型返回了错误的轮次。")
                if turn.get("status") != "completed":
                    raise _Failure("MODEL_TURN_FAILED", "模型轮次未正常完成。", outcome_unknown=bool(result["tools"]))
                result["status"] = "completed"
                result["text"] = final_messages[-1] if final_messages else ""
                emit("model_completed", thread_id=result["thread_id"], turn_id=result["turn_id"])

        def rpc(identifier, method, params):
            send({"id": identifier, "method": method, "params": params})
            while True:
                message = next_message()
                if message.get("id") == identifier and not message.get("method"):
                    if "error" in message:
                        raise _Failure("MODEL_PROTOCOL_REJECTED", "本机 Codex 拒绝了所需协议或隔离配置。")
                    return message.get("result") or {}
                dispatch(message)

        try:
            settings = self._settings(workspace)
            args = self.command + ["-c", "features.shell_tool=false", "-c", "features.plugins=false", "-c", "features.apps=false", "-c", "features.hooks=false", "-c", 'web_search="disabled"', "app-server"]
            process = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", shell=False, cwd=str(workspace), creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            threading.Thread(target=read_stdout, daemon=True).start()
            threading.Thread(target=read_stderr, daemon=True).start()
            rpc(1, "initialize", {"clientInfo": {"name": "aps_local_assistant", "version": "0.1.0"}, "capabilities": {"experimentalApi": True}})
            send({"method": "initialized", "params": {}})
            # Discover names through public metadata, never by reading the
            # user's config. Disable every inherited MCP server by exact name
            # before creating/resuming the business thread.
            servers, cursor = {}, None
            for page in range(10):
                metadata = rpc(10 + page, "mcpServerStatus/list", {"limit": 100, "detail": "toolsAndAuthOnly", **({"cursor": cursor} if cursor else {})})
                for entry in metadata.get("data", []):
                    if not isinstance(entry.get("name"), str):
                        raise _Failure("MODEL_CAPABILITY_UNAVAILABLE", "无法核对继承的工具目录。")
                    servers[entry["name"]] = {"enabled": False}
                cursor = metadata.get("nextCursor")
                if not cursor:
                    break
            if cursor:
                raise _Failure("MODEL_CAPABILITY_UNAVAILABLE", "继承工具目录超过核对范围。")
            settings["mcp_servers"] = servers
            params = {"cwd": str(workspace), "approvalPolicy": "never", "environments": [], "config": settings, "permissions": PROFILE, "runtimeWorkspaceRoots": [str(workspace)]}
            if thread_id:
                params["threadId"] = thread_id
            else:
                params["dynamicTools"] = tool_specs
            thread = rpc(2, "thread/resume" if thread_id else "thread/start", params)
            returned_id = (thread.get("thread") or {}).get("id")
            if not returned_id or (thread_id and returned_id != thread_id):
                raise _Failure("MODEL_CONTEXT_MISMATCH", "模型返回了错误的会话。")
            result["thread_id"], result["model"] = returned_id, thread.get("model")
            metadata = rpc(30, "mcpServerStatus/list", {"limit": 100, "detail": "toolsAndAuthOnly", "threadId": returned_id})
            if metadata.get("nextCursor") or any(entry.get("tools") or entry.get("resources") or entry.get("resourceTemplates") for entry in metadata.get("data", [])):
                raise _Failure("MODEL_CAPABILITY_UNAVAILABLE", "继承的外部工具未能完全关闭。")
            emit("model_thread", thread_id=returned_id, model=result["model"])
            turn = rpc(3, "turn/start", {"threadId": returned_id, "input": [{"type": "text", "text": prompt}], "cwd": str(workspace), "approvalPolicy": "never", "environments": [], "permissions": PROFILE, "runtimeWorkspaceRoots": [str(workspace)]})
            result["turn_id"] = (turn.get("turn") or {}).get("id")
            if not result["turn_id"]:
                raise _Failure("MODEL_PROTOCOL_ERROR", "模型未返回轮次标识。")
            emit("model_started", thread_id=returned_id, turn_id=result["turn_id"])
            while result["status"] != "completed":
                dispatch(next_message())
        except _Failure as exc:
            result["text"] = ""
            result["status"] = "cancelled" if exc.code == "MODEL_CANCELLED" else "failed"
            result["error"] = {"code": exc.code, "message": exc.message, "outcome_unknown": exc.outcome_unknown}
            if process is not None and result["thread_id"] and result["turn_id"]:
                try:
                    send({"id": 99, "method": "turn/interrupt", "params": {"threadId": result["thread_id"], "turnId": result["turn_id"]}})
                except _Failure:
                    pass
        except Exception:
            result["text"] = ""
            result["error"] = {"code": "MODEL_UNAVAILABLE", "message": "本机 Codex 会话无法建立。", "outcome_unknown": bool(result["tools"])}
        finally:
            if process is not None:
                if process.poll() is None:
                    if os.name == "nt":
                        # Only this adapter's newly spawned process tree is stopped.
                        try:
                            subprocess.run(["taskkill.exe", "/PID", str(process.pid), "/T", "/F"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                        except (OSError, subprocess.TimeoutExpired):
                            process.terminate()
                    else:
                        process.terminate()
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        process.kill()
                if stderr:
                    result["diagnostics"] = stderr[:12]
            if result["error"]:
                emit("model_failed", error=result["error"])
        return result
