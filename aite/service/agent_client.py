"""拉取式 agent 客户端（模块 G）：注册/心跳/执行/回传。

线程模型：心跳后台线程（保持在线 + 领任务入队）+ 主线程执行回传——
长任务执行期间心跳不中断，否则会被 master 判 stale 误杀。
agent 只做出站 HTTP（master URL 单向可达即可），执行复用 tools 层适配器。
"""

from __future__ import annotations

import queue
import threading
import time
from datetime import datetime
from pathlib import Path

import httpx

from aite import __version__, config
from aite.service.agent_service import AGENT_ENGINES
from aite.tools import execute_tool
from aite.utils.logging import get_logger

logger = get_logger(__name__)


class AgentClient:
    """拉取式 agent：心跳线程 + 执行主循环。"""

    def __init__(self, master: str, name: str, labels: list[str] | None = None,
                 token: str | None = None, interval: float = 5.0,
                 work_root: Path | None = None) -> None:
        self.master = master.rstrip("/")
        self.name = name
        self.labels = labels or []
        self.interval = interval
        self.work_root = work_root or Path(config.DEFAULT_REPORTS_DIR) / "agent"
        self.agent_id: str | None = None
        self._current_task_id: str | None = None
        self._headers = {"Authorization": f"Bearer {token}"} if token else {}

    # ------------------------------------------------------------------ loop

    def run_forever(self) -> None:
        """注册 → 启动心跳线程 → 主循环执行任务；Ctrl+C 退出。"""

        self._register()
        tasks: queue.Queue = queue.Queue()
        threading.Thread(target=self._heartbeat_loop, args=(tasks,),
                         daemon=True).start()
        logger.info("agent 循环启动（master=%s, interval=%.1fs）",
                    self.master, self.interval)
        while True:
            try:
                task = tasks.get(timeout=3600)
            except queue.Empty:
                continue
            except KeyboardInterrupt:
                logger.info("agent 退出（Ctrl+C）")
                return
            self._current_task_id = task["id"]
            try:
                self._run_task(task)
            finally:
                self._current_task_id = None

    def _heartbeat_loop(self, tasks: queue.Queue) -> None:
        """后台心跳：保持在线；心跳响应携带认领到的任务（派发即经此通道）。"""
        while True:
            try:
                data = self._post(f"/agents/{self.agent_id}/heartbeat",
                                  {"current_task_id": self._current_task_id})
                task = data.get("task")
                if task:
                    logger.info("认领任务 %s（%s / %s）",
                                task["id"], task["engine"], task["run_name"])
                    tasks.put(task)
            except Exception as exc:  # noqa: BLE001 - 常驻心跳：错误不崩线程
                logger.warning("心跳失败: %s", exc)
            time.sleep(self.interval)

    # ------------------------------------------------------------------ task

    def _run_task(self, task: dict) -> None:
        """执行并回传；任何失败回传 error payload（不崩循环）。"""
        import asyncio

        task_id = task["id"]
        engine = task.get("engine")
        if engine not in AGENT_ENGINES:
            self._post(f"/agent-tasks/{task_id}/result",
                       {"payload": {"status": "error",
                                    "error": f"agent 不支持引擎 {engine!r}"}})
            return
        work_dir = self.work_root / task["run_name"] / self._stamp()
        try:
            result, _snapshot, _req = asyncio.run(
                execute_tool(task["suite_raw"], work_dir=work_dir))
            from aite.service.agent_service import serialize_result

            payload = serialize_result(result, work_dir)
        except Exception as exc:  # noqa: BLE001 - 执行失败回传 error
            logger.exception("任务 %s 执行失败", task_id)
            payload = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
        resp = self._post(f"/agent-tasks/{task_id}/result", {"payload": payload})
        logger.info("任务 %s 回传完成: run_id=%s status=%s",
                    task_id, resp.get("run_id"), resp.get("status"))

    # ------------------------------------------------------------------ http

    def _register(self) -> None:
        body = {"name": self.name, "labels": self.labels, "version": __version__}
        data = self._post("/agents/register", body)
        self.agent_id = data["id"]
        logger.info("agent 已注册: %s (%s)", data["name"], self.agent_id)

    def _post(self, path: str, body: dict) -> dict:
        with httpx.Client(timeout=30, headers=self._headers) as client:
            resp = client.post(f"{self.master}/api/v1{path}", json=body)
            resp.raise_for_status()
            return resp.json()

    @staticmethod
    def _stamp() -> str:
        return datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
