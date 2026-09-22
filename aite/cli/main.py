"""aite CLI (Typer). Core API stays dependency-free of Typer/Rich/FastAPI."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from aite import __version__
from aite import config as aite_config
from aite.core.registry import registry
from aite.reporting.rebuild import rebuild_report
from aite.reporting.renderers import report_relpath
from aite.storage import repo_session
from aite.utils.logging import get_logger, setup_logging

app = typer.Typer(
    name="aite",
    help="AI Test & Evaluation Framework - LLM / Agent / RAG + perf·reliability·safety",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()
err_console = Console(stderr=True)
logger = get_logger("cli")

QUICKSTART_YAML = """\
run_name: quickstart-openai
description: aite quickstart - builtin sample QA over an OpenAI-compatible API

target:
  type: openai_compatible
  # Works with OpenAI / DeepSeek / Qwen(DashScope compatible-mode) / vLLM / Ollama (:11434/v1)
  base_url: http://localhost:11434/v1
  model: qwen2.5:7b
  api_key_env: OPENAI_API_KEY      # optional for local servers
  params: {temperature: 0.2, max_tokens: 512}
  rate_limit: {rpm: 120, concurrency: 8}
  stream: true                     # capture TTFT

datasets:
  - type: builtin
    id: gsm8k                      # builtin sample set (offline)
    sample: {n: 10, seed: 42}

metrics:
  - exact_match: {threshold: 0.5, extract: number}
  - latency_p95_ms: {threshold: 30000}

pass_strategy: all_of
report: [rich, json, html]
output_dir: ./reports
storage: {url: sqlite+aiosqlite:///./aite.db}
"""

SAMPLE_JSONL = """\
{"id": "qa-1", "question": "What is the capital of France?", "answer": "Paris"}
{"id": "qa-2", "question": "Who wrote 'Pride and Prejudice'?", "answer": "Jane Austen"}
{"id": "qa-3", "question": "What is 15% of 200?", "answer": "30"}
{"id": "qa-4", "question": "Name the largest planet in the solar system.", "answer": "Jupiter"}
{"id": "qa-5", "question": "What does HTTP status 404 mean?", "answer": "Not Found"}
"""


def _db_url(suite_config=None, db: str | None = None) -> str:
    if db:
        return db
    if os.environ.get(aite_config.ENV_DB_URL):
        return os.environ[aite_config.ENV_DB_URL]
    if suite_config is not None:
        return suite_config.storage.url
    return aite_config.DEFAULT_DB_URL


@app.command()
def init(
    dir: Path = typer.Option(Path("."), help="directory to scaffold"),
    force: bool = typer.Option(False, help="overwrite existing files"),
) -> None:
    """Scaffold example config + sample dataset in the current directory."""
    base = Path(dir)
    base.mkdir(parents=True, exist_ok=True)
    targets = {
        base / "quickstart.yaml": QUICKSTART_YAML,
        base / "data" / "datasets" / "accuracy" / "sample_qa.jsonl": SAMPLE_JSONL,
    }
    for path, content in targets.items():
        if path.exists() and not force:
            console.print(f"[yellow]skip[/yellow] {path} (exists)")
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        console.print(f"[green]created[/green] {path}")
    console.print(
        "\nNext steps:\n"
        "  aite run -c quickstart.yaml\n"
        "  aite dataset list\n"
        "  aite serve            # web platform on http://127.0.0.1:8300"
    )


@app.command()
def run(
    config: Path = typer.Option(..., "-c", "--config", help="suite YAML file"),
    limit: int | None = typer.Option(None, help="max cases per dataset"),
    datasets: str | None = typer.Option(None, help="comma-separated dataset ids/paths"),
    concurrency: int | None = typer.Option(None, help="override concurrency"),
    max_cost_usd: float | None = typer.Option(None, help="cost circuit breaker (USD)"),
    report_formats: str | None = typer.Option(None, help="override report formats csv"),
    agent: str | None = typer.Option(None, "--agent", help="派发到指定执行机（名称）"),
    quiet: bool = typer.Option(False, "--quiet", help="no live progress, log only"),
    log_level: str = typer.Option("INFO"),
) -> None:
    """Run a test suite; exit code 0 = passed, 1 = failed/error (CI friendly)."""
    setup_logging(log_level, quiet=quiet)

    import yaml as _yaml

    from aite.cli.progress import RichProgressReporter
    from aite.service import execute_suite, resolve_suite, storage_url_of
    from aite.storage import repo_session

    raw = _yaml.safe_load(config.read_text(encoding="utf-8")) or {}
    sreq = resolve_suite(raw)   # engine 路由 + 校验（与 Web 同源）
    dataset_ids = [d.strip() for d in datasets.split(",")] if datasets else None

    if agent:
        # 派发到执行机（模块 G）：创建 agent_task → 轮询终态 → 打印结果
        _dispatch_to_agent(raw, agent, log_level)
        return

    storage_url = (sreq.config.storage.url if sreq.config
                   else storage_url_of(raw))

    async def _run() -> int:
        async with repo_session(storage_url) as repo:
            report = await execute_suite(
                raw, repo, limit=limit, dataset_ids=dataset_ids,
                concurrency=concurrency, max_cost_usd=max_cost_usd,
                report_formats=report_formats,
                progress=None if quiet else RichProgressReporter())
            _print_report_summary(report)
            return report.exit_code

    raise SystemExit(asyncio.run(_run()))


def _print_report_summary(report) -> None:
    from rich.markup import escape

    table = Table(title=f"{report.run_name} [{report.status}]", show_lines=False)
    for col in ("metric", "value", "threshold", "result", "reports"):
        table.add_column(col)
    for s in report.summaries:
        if s.dataset is not None or s.group is not None:
            continue
        table.add_row(
            s.metric,
            "—" if s.value is None else f"{s.value:.4f}",
            "—" if s.threshold is None else f"{s.threshold:g}",
            {True: "[green]PASS[/green]", False: "[red]FAIL[/red]", None: "-"}[s.passed],
            "",
        )
    console.print(table)
    failures = report.details.get("failures") or []
    if failures:
        console.print(f"  失败详情（{len(failures)} 例；完整信息见 junit/log 报告）:")
        for f in failures[:20]:
            console.print(f"    [red]✗[/red] {escape(f['dataset'])}::{escape(f['case'])}")
            for line in str(f.get("detail", "")).splitlines()[:5]:
                if line.strip():
                    console.print(f"      {escape(line)}")
        if len(failures) > 20:
            console.print(f"    …… 其余 {len(failures) - 20} 例见 junit 报告")
    reports = report.details.get("reports") or {}
    for fmt, path in reports.items():
        console.print(f"  report [{fmt}]: {path}")


tool_app = typer.Typer(help="external tool adapters (eval engines)")
app.add_typer(tool_app, name="tool")


@tool_app.command("benchmarks")
def tool_benchmarks(
    engine: str = typer.Argument("evalscope", help="tool engine name"),
) -> None:
    """List benchmarks of an external tool: name / metric / tags / few-shot."""
    from aite.tools import get_tool

    try:
        tasks = get_tool(engine).list_tasks()
    except ImportError as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise SystemExit(1) from exc
    table = Table(title=f"{engine} benchmarks ({len(tasks)})")
    for col in ("name", "pretty_name", "metrics", "tags", "few_shot"):
        table.add_column(col)
    for t in tasks:
        table.add_row(t["name"], t["pretty_name"], ", ".join(t["metrics"]),
                      ", ".join(t["tags"]), str(t.get("few_shot_num") or "-"))
    console.print(table)


report_app = typer.Typer(help="re-render / inspect stored run reports")
app.add_typer(report_app, name="report")


@report_app.command("show")
def report_show(
    run_id: str = typer.Argument(..., help="run id"),
    formats: str = typer.Option("html,json", help="formats to render"),
    out: Path = typer.Option(Path("./reports"), help="output directory"),
    db: str | None = typer.Option(None, "--db", help="storage url override"),
) -> None:
    """Re-render a stored run into html/json/junit files."""
    setup_logging("WARNING", quiet=True)

    async def _go() -> None:
        async with repo_session(_db_url(db=db)) as repo:
            report = await rebuild_report(repo, run_id)
            if report is None:
                err_console.print(f"[red]run not found:[/red] {run_id}")
                raise SystemExit(1)
            from aite.core.registry import resolve_renderer

            out_dir = Path(out) / report_relpath(report)
            for fmt in [f.strip() for f in formats.split(",") if f.strip()]:
                path = resolve_renderer(fmt)().render(report, out_dir)
                console.print(f"[green]wrote[/green] {path}")

    asyncio.run(_go())


case_app = typer.Typer(help="inspect stored test cases")
app.add_typer(case_app, name="case")


@case_app.command("list")
def case_list(
    suite: str = typer.Option(..., help="suite (run) name"),
    limit: int = typer.Option(20),
    db: str | None = typer.Option(None, "--db"),
) -> None:
    setup_logging("WARNING", quiet=True)

    async def _go() -> None:
        async with repo_session(_db_url(db=db)) as repo:
            found = await repo.get_suite_by_name(suite)
            if not found:
                err_console.print(f"[red]suite not found:[/red] {suite}")
                raise SystemExit(1)
            cases = await repo.list_test_cases(found["id"], limit=limit)
            table = Table(title=f"suite {suite} ({len(cases)} shown)")
            for col in ("id", "variant", "dataset", "input"):
                table.add_column(col, overflow="fold")
            for c in cases:
                table.add_row(c["external_key"], c["variant"], c["dataset"] or "",
                              str(c["input"])[:120])
            console.print(table)

    asyncio.run(_go())


dataset_app = typer.Typer(help="数据集管理（统一 catalog：functional/accuracy/perf）")
app.add_typer(dataset_app, name="dataset")


@dataset_app.command("list")
def dataset_list(
    type_: str | None = typer.Option(
        None, "--type", help="按类型过滤（functional/accuracy/perf）"),
) -> None:
    """列出受管数据集（catalog）+ 内置基准（builtin 合并显示）。"""
    from aite.datasets import builtin_ids, builtin_info, try_register_hf

    async def _go():
        async with repo_session(aite_config.db_url_from_env()) as repo:
            return await repo.list_dataset_catalog(type_=type_)

    rows = asyncio.run(_go())
    table = Table(title=f"datasets ({len(rows)} managed)")
    for col in ("id", "type", "name", "version", "source", "path"):
        table.add_column(col, overflow="fold")
    for r in rows:
        table.add_row(r["id"][:8], r["type"], r["name"], r["version"],
                      r["source"], r["path"])
    console.print(table)
    if type_ in (None, "accuracy"):
        table2 = Table(title="builtin benchmark datasets (accuracy, packaged)")
        for col in ("id", "task", "recommended metrics", "full-scale source"):
            table2.add_column(col)
        for ds_id in builtin_ids():
            info = builtin_info(ds_id)
            recs = ", ".join(
                f"{r.get('name')}"
                + (f" ({', '.join(f'{k}={v}' for k, v in r['params'].items())})" if r.get("params") else "")
                + (f" >={r.get('threshold')}" if r.get("threshold") is not None else "")
                for r in info.get("recommend") or [])
            table2.add_row(ds_id, info.get("task", ""), recs, info.get("upstream", ""))
        console.print(table2)
    extra = try_register_hf()
    if not extra:
        console.print("[dim]hf_hub loader available with: pip install 'aite[bench]'[/dim]")


@dataset_app.command("show")
def dataset_show(ref: str = typer.Argument(..., help="id / name / type:name")) -> None:
    """查看数据集：catalog 引用优先，回退 builtin。"""
    from aite.datasets import builtin_info
    from aite.datasets.catalog import resolve_dataset_ref

    async def _go():
        async with repo_session(aite_config.db_url_from_env()) as repo:
            try:
                return await resolve_dataset_ref(repo, ref)
            except ValueError:
                return None

    row = asyncio.run(_go())
    if row is not None:
        for k, v in row.items():
            console.print(f"[bold]{k}[/bold]: {v}")
        return
    info = builtin_info(ref)
    if not info:
        err_console.print(f"[red]unknown dataset:[/red] {ref}")
        raise SystemExit(1)
    for k, v in info.items():
        console.print(f"[bold]{k}[/bold]: {v}")


@dataset_app.command("import-functional")
def dataset_import_functional(
    file: Path = typer.Argument(..., help="cases.jsonl 文件"),
    name: str = typer.Option(..., "--name", help="数据集名（=场景名）"),
    notes: str = typer.Option("", "--notes"),
    force: bool = typer.Option(False, "--force", help="覆盖同名"),
) -> None:
    """导入功能数据集：逐行 harness.Case 校验 → 落盘 → 登记 catalog。"""
    from aite.functional.ingest import ingest_functional

    async def _go():
        async with repo_session(aite_config.db_url_from_env()) as repo:
            return await ingest_functional(repo, file, name, notes=notes, force=force)

    try:
        row = asyncio.run(_go())
    except (ValueError, FileNotFoundError) as exc:
        err_console.print(f"[red]import failed:[/red] {exc}")
        raise SystemExit(1) from exc
    console.print(f"[green]imported[/green] {row['type']}/{row['name']} "
                  f"({row['stats'].get('count')} cases) -> {row['path']}")


@dataset_app.command("rm")
def dataset_rm(
    ref: str = typer.Argument(..., help="id / name / type:name"),
    force: bool = typer.Option(False, "--force", help="跳过确认"),
) -> None:
    """删除 catalog 登记条目（数据文件保留）。"""
    from aite.datasets.catalog import resolve_dataset_ref

    async def _go() -> dict | None:
        async with repo_session(aite_config.db_url_from_env()) as repo:
            row = await resolve_dataset_ref(repo, ref)
            deleted = await repo.delete_dataset_catalog(row["id"])
            return row if deleted else None

    if not force and not typer.confirm(f"删除数据集登记 {ref}?（文件保留）"):
        raise typer.Abort()
    row = asyncio.run(_go())
    if row is None:
        err_console.print(f"[red]unknown dataset:[/red] {ref}")
        raise SystemExit(1)
    console.print(f"[green]removed[/green] {row['type']}/{row['name']}（文件保留: {row['path']}）")


@dataset_app.command("add-hf")
def dataset_add_hf(
    hf_id: str = typer.Argument(..., help="HuggingFace dataset id, e.g. openai/gsm8k"),
    name: str | None = typer.Option(None, help="builtin id (default: derived from hf_id)"),
    split: str = typer.Option("test", help="dataset split"),
    subset: str | None = typer.Option(None, help="config/subset name, e.g. 'main'"),
    n: int | None = typer.Option(None, "-n", help="pre-sample N records (recommended)"),
    seed: int = typer.Option(42, help="sample seed"),
    fields: str | None = typer.Option(
        None, help="field mapping, e.g. 'input=question,expected=answer'"),
) -> None:
    """Download a HF dataset, convert it and register as an offline builtin.

    Requires network + 'aite[bench]'; result is fully offline afterwards.
    """
    from aite.datasets.add_hf import add_hf_dataset
    from aite.datasets.catalog import register_entry

    fmap: dict[str, str] = {}
    if fields:
        for part in fields.split(","):
            if "=" in part:
                k, v = part.split("=", 1)
                fmap[k.strip()] = v.strip()
    try:
        path = add_hf_dataset(hf_id, name, split=split, subset=subset,
                              n=n, seed=seed, fields=fmap)
    except (RuntimeError, ValueError, FileNotFoundError) as exc:
        err_console.print(f"[red]import failed:[/red] {exc}")
        raise SystemExit(1) from exc
    console.print(f"[green]wrote[/green] {path}")
    console.print(f"usable offline as: {{type: builtin, id: {path.stem}}}")

    count = sum(1 for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip())

    async def _reg():
        async with repo_session(aite_config.db_url_from_env()) as repo:
            return await register_entry(
                repo, dataset_type="accuracy", name=path.stem, source="hf",
                path=str(path).replace("\\", "/"), version=f"{split}@{seed}",
                stats={"count": count}, notes=f"hf:{hf_id}")

    row = asyncio.run(_reg())
    console.print(f"[green]registered[/green] accuracy/{row['name']} ({count} records)")


@dataset_app.command("gen-domain")
def dataset_gen_domain(
    domain: str = typer.Argument(..., help="领域（finance / government / legal）"),
    name: str | None = typer.Option(None, help="数据集名（缺省 <domain>_private）"),
    n: int = typer.Option(12, "-n", help="生成条数（不超过领域语料池）"),
    seed: int = typer.Option(42, help="随机种子（=版本凭证，同种子可复现）"),
    distractors: int = typer.Option(2, help="每条附带的同域干扰文档数"),
    notes: str = typer.Option("", "--notes"),
) -> None:
    """生成私域 RAG 数据集（金融/政务/法律）：落盘 + 注册 userstore/catalog。"""
    from aite.datasets.gen_domain import generate_domain_dataset, list_domains

    async def _go():
        async with repo_session(aite_config.db_url_from_env()) as repo:
            return await generate_domain_dataset(
                repo, domain, name or f"{domain}_private",
                n=n, seed=seed, distractors=distractors, notes=notes)

    try:
        row = asyncio.run(_go())
    except ValueError as exc:
        err_console.print(f"[red]generate failed:[/red] {exc}（可用领域："
                          f"{', '.join(list_domains())}）")
        raise SystemExit(1) from exc
    console.print(f"[green]generated[/green] {row['type']}/{row['name']} "
                  f"(seed={seed}, {row['stats'].get('count')} records) -> {row['path']}")
    console.print(f"usable offline as: {{type: builtin, id: {row['name']}}} "
                  f"或 dataset ref: {row['name']}")


bench_app = typer.Typer(help="evalscope / lm-eval compatibility tools")
app.add_typer(bench_app, name="bench")


@bench_app.command("convert")
def bench_convert(
    task_yaml: Path = typer.Argument(..., help="upstream task yaml"),
    source: str = typer.Option("lm-eval", help="lm-eval | evalscope"),
    out: Path | None = typer.Option(None, help="write converted suite yaml here"),
    threshold: float | None = typer.Option(None, help="default metric threshold"),
) -> None:
    """Convert an lm-eval/evalscope task definition into an aite suite (layer 1)."""
    if source == "lm-eval":
        from aite.datasets.compat.lm_eval_adapter import convert_lm_eval_task as convert
    elif source == "evalscope":
        from aite.datasets.compat.evalscope_adapter import convert_evalscope_task as convert
    else:
        err_console.print(f"[red]unknown source:[/red] {source}")
        raise SystemExit(1)
    fragment = convert(task_yaml, threshold=threshold)
    import yaml as _yaml

    text = _yaml.safe_dump(fragment, allow_unicode=True, sort_keys=False)
    if out:
        out.write_text(text, encoding="utf-8")
        console.print(f"[green]wrote[/green] {out}")
    else:
        console.print(text)
    for warning in fragment.get("details", {}).get("warnings", []):
        console.print(f"[yellow]warning:[/yellow] {warning}")


@bench_app.command("import")
def bench_import(
    results: Path = typer.Argument(..., help="results json produced by lm-eval/evalscope"),
    format: str = typer.Option(..., "--format", help="lm-eval | evalscope"),
    run_name: str | None = typer.Option(None),
    db: str | None = typer.Option(None, "--db"),
) -> None:
    """Import external benchmark results into the aite DB (layer 2 bridge)."""
    setup_logging("WARNING", quiet=True)
    from aite.datasets.compat.bench_import import import_results

    async def _go() -> None:
        async with repo_session(_db_url(db=db)) as repo:
            run_id = await import_results(repo, results, fmt=format, run_name=run_name)
            console.print(f"[green]imported[/green] run_id={run_id}")

    asyncio.run(_go())


@app.command()
def serve(
    host: str = typer.Option(aite_config.WEB_HOST),
    port: int = typer.Option(aite_config.WEB_PORT),
    db: str | None = typer.Option(None, "--db", help="storage url override"),
    token: str | None = typer.Option(
        None, envvar="AITE_WEB_TOKEN", help="bearer token guard (empty = open)"),
    reload: bool = typer.Option(False, "--reload", help="dev autoreload"),
) -> None:
    """Start the web platform (FastAPI + SPA)."""
    import uvicorn

    from aite.web.app import create_app

    application = create_app(storage_url=_db_url(db=db), token=token)
    console.print(f"[bold]aite[/bold] v{__version__} serving on http://{host}:{port}")
    uvicorn.run(application, host=host, port=port, reload=reload, log_level="info")


@app.command()
def version() -> None:
    console.print(f"aite {__version__}")


@app.command(name="list")
def list_components() -> None:
    """List registered components (connectors/datasets/metrics/judges/renderers)."""
    import aite.connectors
    import aite.datasets
    import aite.metrics
    import aite.reporting  # noqa: F401 - registers renderers

    for label, comp in (("connectors", registry.connectors),
                        ("datasets", registry.datasets),
                        ("metrics", registry.metrics),
                        ("judges", registry.judges),
                        ("renderers", registry.renderers)):
        console.print(f"[bold]{label}[/bold] ({len(comp)}): " + ", ".join(sorted(comp)))


def main() -> None:  # console_scripts entrypoint
    app()


if __name__ == "__main__":
    main()


agent_app = typer.Typer(help="执行机 agent（模块 G：拉取式纳管）")
app.add_typer(agent_app, name="agent")


@agent_app.command("serve")
def agent_serve(
    master: str = typer.Option(..., "--master", help="master 地址（aite serve）"),
    name: str = typer.Option(..., "--name", help="执行机名称（注册标识）"),
    labels: str | None = typer.Option(None, help="逗号分隔标签"),
    token: str | None = typer.Option(None, help="bearer token（master 开启鉴权时）"),
    interval: float = typer.Option(5.0, help="心跳间隔（秒）"),
    log_level: str = typer.Option("INFO"),
) -> None:
    """常驻执行机 agent：注册 → 心跳领任务 → 执行 → 回传 master。"""
    setup_logging(log_level, quiet=False)
    from aite.service.agent_client import AgentClient

    client = AgentClient(master=master, name=name,
                         labels=[x.strip() for x in labels.split(",")] if labels else [],
                         token=token, interval=interval)
    client.run_forever()


def _dispatch_to_agent(raw: dict, node: str, log_level: str) -> None:
    """CLI 派发：创建 agent_task → 轮询终态 → 打印（同步桥接）。"""
    import time

    from aite import config as _cfg
    from aite.service.agent_service import dispatch_task
    from aite.storage import repo_session

    async def _create():
        async with repo_session(_cfg.DEFAULT_DB_URL) as repo:
            return await dispatch_task(repo, raw, node)

    task = asyncio.run(_create())
    console.print(f"[green]已派发[/green] task={task['id']} → {node}，"
                  f"等待执行机回传…")
    err_console.print("提示: agent 侧执行；本命令轮询 master 直至终态")

    deadline = time.time() + 86400
    while time.time() < deadline:
        time.sleep(3)

        async def _poll():
            async with repo_session(_cfg.DEFAULT_DB_URL) as repo:
                return await repo.get_agent_task(task["id"])

        current = asyncio.run(_poll())
        if current["status"] in ("done", "error", "stale"):
            if current["status"] == "done":
                result = current.get("result") or {}
                console.print(f"[green]完成[/green] run_id={result.get('run_id')} "
                              f"status={result.get('status')}")
                raise SystemExit(0 if result.get("status") == "passed" else 1)
            err_console.print(f"[red]派发失败[/red]: {current.get('error')}")
            raise SystemExit(1)
    err_console.print("[red]等待超时（24h）[/red]")
    raise SystemExit(1)


# ----------------------------------------------------- 四域独立任务管理（C0.5）
# docs/domain-backends.md：每域自带任务增删改查 / 运行·停止·重试 / 结果展示，
# 公共框架 core/taskkit.py 一份实现、四张任务表各自演进；CLI 与 Web 各自装配。
from aite.cli.task_cli import build_task_cli  # noqa: E402
from aite.service.accuracy_tasks import build_accuracy_task_kit  # noqa: E402
from aite.service.functional_tasks import build_functional_task_kit  # noqa: E402
from aite.service.perf_tasks import build_perf_task_kit  # noqa: E402
from aite.service.reliability_tasks import build_reliability_task_kit  # noqa: E402


def _mount_domain_tasks(label: str, kit_builder) -> None:
    """`aite {域} tasks …`：域组下挂 tasks 子组，给域内其他命令留空间。"""
    domain_app = typer.Typer(help=f"{label} 域（任务管理等）", no_args_is_help=True)
    domain_app.add_typer(build_task_cli(kit_builder()), name="tasks")
    app.add_typer(domain_app, name=label)


_mount_domain_tasks("functional", build_functional_task_kit)
_mount_domain_tasks("accuracy", build_accuracy_task_kit)
_mount_domain_tasks("perf", build_perf_task_kit)
_mount_domain_tasks("reliability", build_reliability_task_kit)


# ------------------------------------------------------- 用例管理（§15，T1）
# 文字用例库（测什么）与脚本绑定（怎么测）；执行回填/版本报告见 §15.3。
case_doc_app = typer.Typer(help="文字用例库（功能/性能/精度/可靠性）")
app.add_typer(case_doc_app, name="case-doc")


def _load_case_fields(file: Path | None, options: dict) -> dict:
    import yaml

    fields = {}
    if file is not None:
        loaded = yaml.safe_load(file.read_text(encoding="utf-8")) or {}
        if not isinstance(loaded, dict):
            raise typer.BadParameter(f"{file} 须为字段映射")
        fields.update(loaded)
    fields.update({k: v for k, v in options.items() if v})
    return fields


@case_doc_app.command("add")
def case_doc_add(
    file: Path = typer.Option(None, "--file", help="用例 YAML（全部字段）"),
    domain: str = typer.Option(None, "--domain",
                               help="functional/accuracy/perf/reliability"),
    title: str = typer.Option(None, "--title"),
    purpose: str = typer.Option(None, "--purpose"),
    expected: str = typer.Option(None, "--expected"),
    priority: str = typer.Option(None, "--priority", help="P0-P3"),
    risk: str = typer.Option(None, "--risk", help="高/中/低"),
    owner: str = typer.Option(None, "--owner"),
    case_no: str = typer.Option(None, "--case-no", help="人工编号（缺省自动）"),
    script: Path = typer.Option(None, "--script", help="脚本绑定 YAML/JSON 文件"),
) -> None:
    """新增文字用例（--file 整档或逐选项）。"""
    from aite.service.case_docs import create_case_doc

    options = {"domain": domain, "title": title, "purpose": purpose,
               "expected": expected, "priority": priority, "risk": risk,
               "owner": owner, "case_no": case_no}
    if script is not None:
        import yaml

        options["script"] = yaml.safe_load(script.read_text(encoding="utf-8"))
    fields = _load_case_fields(file, options)
    try:
        async def _go():
            async with repo_session(aite_config.db_url_from_env()) as repo:
                return await create_case_doc(repo, fields)

        row = asyncio.run(_go())
    except ValueError as exc:
        err_console.print(f"[red]add failed:[/red] {exc}")
        raise SystemExit(1) from exc
    console.print(f"[green]created[/green] {row['case_no']} "
                  f"({row['domain']}) {row['title']}")


@case_doc_app.command("list")
def case_doc_list(
    domain: str | None = typer.Option(None, "--domain"),
    status: str | None = typer.Option(None, "--status"),
    priority: str | None = typer.Option(None, "--priority"),
) -> None:
    """列出文字用例。"""
    from aite.service.case_docs import create_case_doc  # noqa: F401

    async def _go():
        async with repo_session(aite_config.db_url_from_env()) as repo:
            return await repo.list_case_docs(domain=domain, status=status,
                                             priority=priority)

    rows = asyncio.run(_go())
    table = Table(title=f"test cases ({len(rows)})")
    for col in ("case_no", "domain", "title", "priority", "risk", "status",
                "owner"):
        table.add_column(col, overflow="fold")
    for r in rows:
        table.add_row(r["case_no"], r["domain"], r["title"][:40],
                      r["priority"], r["risk"], r["status"], r["owner"])
    console.print(table)


@case_doc_app.command("show")
def case_doc_show(ref: str = typer.Argument(..., help="case_no 或 id")) -> None:
    """查看用例详情 + 最近执行回填。"""
    from aite.service.case_docs import resolve_case_ref

    async def _go():
        async with repo_session(aite_config.db_url_from_env()) as repo:
            row = await resolve_case_ref(repo, ref)
            runs = await repo.list_case_runs(case_id=row["id"], limit=5)
            return row, runs

    try:
        row, runs = asyncio.run(_go())
    except ValueError as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise SystemExit(1) from exc
    for key in ("case_no", "domain", "title", "priority", "risk", "status",
                "owner", "created_at"):
        console.print(f"[bold]{key}[/bold]: {row[key]}")
    for key in ("purpose", "steps", "expected"):
        if row[key]:
            console.print(f"[bold]{key}[/bold]: {row[key]}")
    if row["script"]:
        console.print("[bold]script[/bold]:")
        console.print_json(json.dumps(row["script"], ensure_ascii=False))
    for run in runs:
        console.print(f"  ↳ [{run['executed_at']}] v={run['version'] or '-'} "
                      f"verdict={run['verdict']} metrics={run['key_metrics']}")


@case_doc_app.command("link")
def case_doc_link(
    ref: str = typer.Argument(..., help="case_no 或 id"),
    script: Path = typer.Option(..., "--script", help="脚本绑定 YAML/JSON 文件"),
) -> None:
    """更新用例的脚本绑定（怎么测）。"""
    import yaml

    from aite.service.case_docs import update_case_doc

    binding = yaml.safe_load(Path(script).read_text(encoding="utf-8")) or {}

    async def _go():
        async with repo_session(aite_config.db_url_from_env()) as repo:
            return await update_case_doc(repo, ref, {"script": binding})

    try:
        row = asyncio.run(_go())
    except ValueError as exc:
        err_console.print(f"[red]link failed:[/red] {exc}")
        raise SystemExit(1) from exc
    console.print(f"[green]linked[/green] {row['case_no']} -> "
                  f"{json.dumps(row['script'], ensure_ascii=False)}")


@case_doc_app.command("rm")
def case_doc_rm(
    ref: str = typer.Argument(..., help="case_no 或 id"),
    force: bool = typer.Option(False, "--force", help="跳过确认"),
) -> None:
    """删除用例（级联删除执行回填记录）。"""
    from aite.service.case_docs import resolve_case_ref

    async def _go():
        async with repo_session(aite_config.db_url_from_env()) as repo:
            row = await resolve_case_ref(repo, ref)
            deleted = await repo.delete_case_doc(row["id"])
            return row, deleted

    if not force and not typer.confirm(f"删除用例 {ref}?（回填记录级联删除）"):
        raise typer.Abort()
    row, deleted = asyncio.run(_go())
    if not deleted:
        err_console.print(f"[red]unknown case:[/red] {ref}")
        raise SystemExit(1)
    console.print(f"[green]removed[/green] {row['case_no']}")
