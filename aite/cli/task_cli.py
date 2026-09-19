"""域任务 CLI 工厂：四域同形任务命令（docs/domain-backends.md §7）。

C0 交付工厂本体；C1-C4 各域在 cli/main.py 段落化挂载自己的子应用：

    from aite.cli.task_cli import build_task_cli
    app.add_typer(build_task_cli(kit), name="perf")

kit 由各域 service 装配（executor 域内提供 + EventBus）；DB URL 走
AITE_DB 环境变量 / 缺省本地 SQLite（config 收口，原则 5）。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from aite import config
from aite.core.taskkit import DomainTaskKit, TaskNotFoundError, TaskStateError
from aite.storage import repo_session

console = Console()
err_console = Console(stderr=True)


def _load_spec(value: str) -> dict:
    """--spec 接受内联 JSON/YAML 或文件路径。"""
    text = value
    path = Path(value)
    if path.exists():
        text = path.read_text(encoding="utf-8")
    try:
        loaded = json.loads(text)
    except json.JSONDecodeError:
        import yaml

        try:
            loaded = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise typer.BadParameter(f"spec 不是合法 JSON/YAML: {exc}") from exc
    if not isinstance(loaded, dict):
        raise typer.BadParameter("spec 必须是 JSON/YAML 对象")
    return loaded


def build_task_cli(kit: DomainTaskKit) -> typer.Typer:
    """按域装配任务管理子应用：create / list / get / rm / run / stop / retry。"""
    app = typer.Typer(help=f"{kit.domain} 域任务管理", no_args_is_help=True)

    def _fail(exc: Exception) -> None:
        err_console.print(f"[red]{type(exc).__name__}:[/red] {exc}")
        raise SystemExit(1) from exc

    @app.command("create")
    def create(
        spec: str = typer.Option(..., "--spec", help="任务 spec：内联 JSON/YAML 或文件路径"),
        name: str = typer.Option("", "--name", help="任务名"),
    ) -> None:
        """创建任务（pending，不运行）。"""
        body = _load_spec(spec)

        async def _go() -> dict:
            async with repo_session(config.db_url_from_env()) as repo:
                return await kit.create(repo, body, name=name)

        try:
            task = asyncio.run(_go())
        except TaskStateError as exc:
            _fail(exc)
            return
        console.print(f"[green]created[/green] {task['id']} status={task['status']}")

    @app.command("list")
    def list_tasks(
        status: str | None = typer.Option(None, help="按状态过滤"),
        retry_of: str | None = typer.Option(None, help="按重试来源任务过滤"),
        limit: int = typer.Option(50, help="最多显示条数"),
    ) -> None:
        """列出任务。"""

        async def _go() -> list[dict]:
            async with repo_session(config.db_url_from_env()) as repo:
                return await kit.list(repo, status=status, retry_of=retry_of, limit=limit)

        tasks = asyncio.run(_go())
        table = Table(title=f"{kit.domain} tasks ({len(tasks)} shown)")
        for col in ("id", "name", "status", "retry", "created_at", "finished_at"):
            table.add_column(col, overflow="fold",
                             no_wrap=col in ("id", "status"))
        for t in tasks:
            chain = str(t["retry_count"]) + (f" ←{t['retry_of'][:8]}" if t["retry_of"] else "")
            table.add_row(t["id"], t["name"], t["status"], chain,
                          t["created_at"], t["finished_at"] or "")
        console.print(table)

    @app.command("get")
    def get(task_id: str = typer.Argument(...)) -> None:
        """查看任务详情（含 spec / result / error）。"""

        async def _go() -> dict | None:
            async with repo_session(config.db_url_from_env()) as repo:
                return await kit.get(repo, task_id)

        task = asyncio.run(_go())
        if task is None:
            _fail(TaskNotFoundError(task_id))
            return
        for key in ("id", "name", "status", "retry_of", "retry_count",
                    "created_at", "started_at", "finished_at"):
            console.print(f"[bold]{key}[/bold]: {task[key]}")
        console.print("[bold]spec[/bold]:")
        console.print_json(json.dumps(task["spec"], ensure_ascii=False, default=str))
        if task["result"] is not None:
            console.print("[bold]result[/bold]:")
            console.print_json(json.dumps(task["result"], ensure_ascii=False, default=str))
        if task["error"]:
            console.print(f"[bold]error[/bold]: {escape(task['error'])}")

    @app.command("rm")
    def rm(
        task_id: str = typer.Argument(...),
        force: bool = typer.Option(False, "--force", help="跳过确认"),
    ) -> None:
        """删除终态任务。"""
        if not force and not typer.confirm(f"删除任务 {task_id}?"):
            raise typer.Abort()

        async def _go() -> None:
            async with repo_session(config.db_url_from_env()) as repo:
                await kit.delete(repo, task_id)

        try:
            asyncio.run(_go())
        except (TaskNotFoundError, TaskStateError) as exc:
            _fail(exc)
            return
        console.print(f"[green]deleted[/green] {task_id}")

    @app.command("run")
    def run(task_id: str = typer.Argument(...)) -> None:
        """运行任务并等待终态（同步形态；exit code 0=succeeded，CI 友好）。"""

        async def _go() -> dict | None:
            async with repo_session(config.db_url_from_env()) as repo:
                await kit.run(repo, task_id)
                return await kit.wait(repo, task_id)

        try:
            task = asyncio.run(_go())
        except (TaskNotFoundError, TaskStateError) as exc:
            _fail(exc)
            return
        if task is None:
            _fail(TaskNotFoundError(task_id))
            return
        console.print(f"[bold]{task['id']}[/bold] status=[bold]{task['status']}[/bold]")
        if task["result"] is not None:
            console.print_json(json.dumps(task["result"], ensure_ascii=False, default=str))
        if task["error"]:
            console.print(f"[red]error:[/red] {escape(task['error'])}")
        if task["status"] != "succeeded":
            raise SystemExit(1)

    @app.command("stop")
    def stop(task_id: str = typer.Argument(...)) -> None:
        """停止运行中任务（协作式；超时强转 cancelled）。"""

        async def _go() -> dict:
            async with repo_session(config.db_url_from_env()) as repo:
                return await kit.stop(repo, task_id)

        try:
            task = asyncio.run(_go())
        except (TaskNotFoundError, TaskStateError) as exc:
            _fail(exc)
            return
        console.print(f"[yellow]stopped[/yellow] {task['id']} status={task['status']}")

    @app.command("retry")
    def retry(task_id: str = typer.Argument(...)) -> None:
        """重试终态任务：创建新任务（retry_of 指向原任务）。"""

        async def _go() -> dict:
            async with repo_session(config.db_url_from_env()) as repo:
                return await kit.retry(repo, task_id)

        try:
            task = asyncio.run(_go())
        except (TaskNotFoundError, TaskStateError) as exc:
            _fail(exc)
            return
        console.print(f"[green]retry created[/green] {task['id']} "
                      f"(retry_of={task['retry_of']}, count={task['retry_count']})")

    return app
