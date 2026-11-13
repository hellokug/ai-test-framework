"""aite CLI (Typer). Core API stays dependency-free of Typer/Rich/FastAPI."""

from __future__ import annotations

import asyncio
import json
import os
from contextlib import contextmanager
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
from aite.utils.logging import APP_LOG_NAME, SERVER_LOG_NAME, build_web_log_config, get_logger, setup_logging

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
    resume: str | None = typer.Option(
        None, "--resume", help="工具域断点续推档位：auto=没跑完就地续推/跑完全新起跑"
                              "（缺省，yaml 未写 resume 时同样 auto）；true=强制复用"
                              "最近目录（如改判官只重判）；false=永远全新"),
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

    bar = RichProgressReporter() if not quiet else None
    bar_state: dict = {}

    def _tool_progress(ev: dict) -> None:
        """工具引擎运行期进度：Rich 单行动态条（逐事件打印会刷屏——一行一案）；
        evalscope 原始日志行镜像为进度条上方的暗色行（等待时后台在干嘛可见）。"""
        if ev.get("type") == "log":
            if not quiet:
                from rich.text import Text

                console.print(Text(f"  │ {ev.get('line', '')}", style="dim"))
            return
        if quiet or bar is None:
            return
        phase = str(ev.get("phase") or "progress")
        done, total = ev.get("done"), ev.get("total")
        if done is None or not total:
            return  # 无 done/total 结构的事件不落条（避免噪音行）
        if bar_state.get("phase") != phase or bar_state.get("total") != total:
            bar.start(int(total), phase)
            bar_state.update(phase=phase, total=int(total), done=0)
        delta = int(done) - bar_state.get("done", 0)
        if delta > 0:
            bar.update(delta)
            bar_state["done"] = int(done)
        bar.update(0, note=str(ev.get("note") or "")[:60])

    async def _run() -> int:
        async with repo_session(storage_url) as repo:
            try:
                report = await execute_suite(
                    raw, repo, limit=limit, dataset_ids=dataset_ids,
                    concurrency=concurrency, max_cost_usd=max_cost_usd,
                    report_formats=report_formats, event_cb=_tool_progress,
                    resume=resume,
                    progress=None if quiet else RichProgressReporter())
            finally:
                if bar is not None:
                    bar.finish()
            _print_report_summary(report)
            return report.exit_code

    raise SystemExit(asyncio.run(_run()))


def _print_report_summary(report) -> None:
    from rich.markup import escape

    from aite.core.aggregator import execution_status

    table = Table(title=f"{report.run_name} [评估 {report.status} · 执行 {execution_status(report)}]",
                  show_lines=False)
    for col in ("metric", "value", "threshold", "result"):
        table.add_column(col)
    for s in report.summaries:
        if s.dataset is not None or s.group is not None:
            continue
        table.add_row(
            s.metric,
            "—" if s.value is None else f"{s.value:.4f}",
            "—" if s.threshold is None else f"{s.threshold:g}",
            {True: "[green]PASS[/green]", False: "[red]FAIL[/red]", None: "-"}[s.passed],
        )
    console.print(table)
    if any(s.passed is None for s in report.summaries
           if s.dataset is None and s.group is None):
        console.print("  [dim]说明: result 为 '-' 的指标未配置阈值，仅记录不参与判定"
                      "（如 score = judge 对回答质量的 1-10 评分归一化）[/dim]")
    failures = report.details.get("failures") or []
    if failures:
        console.print(f"  执行失败详情（{len(failures)} 例；完整信息见 junit/log 报告）:")
        for f in failures[:20]:
            console.print(f"    [red]✗[/red] {escape(f['dataset'])}::{escape(f['case'])}")
            for line in str(f.get("detail", "")).splitlines()[:5]:
                if line.strip():
                    console.print(f"      {escape(line)}")
        if len(failures) > 20:
            console.print(f"    …… 其余 {len(failures) - 20} 例见 junit 报告")
    # 评估未通过的 case：输入/输出/预期/原因（完整版见 HTML 报告 Failing cases）
    payloads = (report.details or {}).get("case_payloads") or {}
    thresholds = {s.metric: s.threshold for s in report.summaries}
    eval_failed = [r for r in report.case_metrics if r.passed is False]

    def flat(text: str, limit: int) -> str:
        """多行内容压成单行摘要（prompt 里的换行会让表格明细断行难读）。"""
        return escape(" ".join(text.split())[:limit])

    if eval_failed:
        console.print(f"  评估未通过明细（{len(eval_failed)} 条判定；"
                      f"完整判官证据见 HTML 报告 Failing cases）:")
        for r in eval_failed[:10]:
            p = payloads.get(r.case_id) or {}
            console.print(f"    [red]✗[/red] {escape(r.case_id)}::{escape(r.metric)} "
                          f"value={r.value} 阈值={thresholds.get(r.metric)}")
            if p.get("input"):
                console.print(f"      输入: {flat(p['input'], 120)}")
            if p.get("output"):
                console.print(f"      输出: {flat(p['output'], 160)}")
            if p.get("expected"):
                console.print(f"      预期: {flat(p['expected'], 120)}")
        if len(eval_failed) > 10:
            console.print(f"    …… 其余 {len(eval_failed) - 10} 条见 HTML 报告")
    reports = report.details.get("reports") or {}
    for fmt, path in reports.items():
        console.print(f"  report [{fmt}]: {path}")


tool_app = typer.Typer(help="external tool adapters (eval engines)")
app.add_typer(tool_app, name="tool")


def _benchmarks_via_common_python(adapter) -> list[dict]:
    """list_tasks 的主环境 ImportError 回退：用 common.yaml 的 python_exe 子进程列举。"""
    from aite.core.profiles import load_common

    python_exe = (((load_common().get("tools") or {}).get("extra_args")) or {}
                  ).get("python_exe")
    via = getattr(adapter, "list_tasks_via", None)
    if not via or not python_exe:
        err_console.print(
            "[red]该引擎无法在主环境导入，且 data/common.yaml 未配置 "
            "tools.extra_args.python_exe（独立环境解释器）[/red]")
        raise SystemExit(1)
    try:
        return via(str(python_exe))
    except Exception as exc:  # noqa: BLE001 - CLI 边界统一报错出口
        err_console.print(f"[red]{exc}[/red]")
        raise SystemExit(1) from exc


@tool_app.command("benchmarks")
def tool_benchmarks(
    engine: str = typer.Argument("evalscope", help="tool engine name"),
) -> None:
    """List benchmarks of an external tool: name / metric / tags / few-shot."""
    from aite.tools import get_tool

    adapter = get_tool(engine)
    try:
        tasks = adapter.list_tasks()
    except ImportError:
        # 主环境未装该工具（如 evalscope 仅在 .venv-evalscope/conda env）：
        # 回退 data/common.yaml 的 python_exe 子进程列举，两者皆无才报错
        tasks = _benchmarks_via_common_python(adapter)
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


@dataset_app.command("settings")
def dataset_settings() -> None:
    """查看数据集路径与在线源设置（生效值 + 环境变量覆盖标记）。"""
    from aite.datasets import settings as ds_settings

    snap = ds_settings.snapshot()
    env_checks = {
        "datasets_dir": aite_config.ENV_DATASETS_DIR,
        "cache_dir": "HF_HOME",
        "hf_endpoint": "HF_ENDPOINT",
        "remote_url": aite_config.ENV_DATASET_REMOTE_URL,
    }
    table = Table(title=f"dataset settings ({ds_settings.settings_path()})")
    for col in ("key", "设置值", "生效值", "来源", "说明"):
        table.add_column(col, overflow="fold")
    for key in ds_settings.FIELDS:
        env_name = env_checks[key]
        locked = bool(os.environ.get(env_name))
        resolved = snap["resolved"][key] or "（未配置）"
        source = f"env {env_name}" if locked else (
            "设置文件" if snap["settings"][key] else "默认")
        table.add_row(key, snap["settings"][key] or "—", resolved, source,
                      snap["help"][key])
    console.print(table)


@dataset_app.command("settings-set")
def dataset_settings_set(
    key: str = typer.Argument(
        ..., help="datasets_dir / cache_dir / hf_endpoint / remote_url"),
    value: str = typer.Argument(
        ..., help="设置值（hf_endpoint/remote_url 传空串恢复默认）"),
) -> None:
    """写一个数据集设置项（落 ./data/dataset_settings.json）。"""
    from aite.datasets import settings as ds_settings

    try:
        values = ds_settings.save_settings({key: value})
    except ValueError as exc:
        err_console.print(f"[red]settings failed:[/red] {exc}")
        raise SystemExit(1) from exc
    console.print(f"[green]saved[/green] {key}={values[key] or '（空=默认）'} "
                  f"-> {ds_settings.settings_path()}")


@dataset_app.command("upload")
def dataset_upload(
    file: Path = typer.Argument(..., help="数据集文件（.jsonl/.json/.csv 文本）"),
    type_: str = typer.Option("accuracy", "--type",
                              help="数据集类型（accuracy / functional / perf）"),
    name: str = typer.Option(..., "--name", help="数据集名（type 下唯一）"),
    notes: str = typer.Option("", "--notes"),
    force: bool = typer.Option(False, "--force", help="覆盖同名"),
) -> None:
    """上传本地数据集文件：校验 → 落位数据集目录 → 登记 catalog。"""
    from aite.service.dataset_files import upload_dataset

    async def _go():
        async with repo_session(aite_config.db_url_from_env()) as repo:
            return await upload_dataset(
                repo, dataset_type=type_, name=name,
                filename=file.name, content=file.read_bytes(),
                notes=notes, force=force)

    try:
        row = asyncio.run(_go())
    except (ValueError, FileNotFoundError) as exc:
        err_console.print(f"[red]upload failed:[/red] {exc}")
        raise SystemExit(1) from exc
    console.print(f"[green]uploaded[/green] {row['type']}/{row['name']} "
                  f"({(row.get('stats') or {}).get('count', '?')} records) -> {row['path']}")


@dataset_app.command("download")
def dataset_download(
    ref: str = typer.Argument(..., help="id / name / type:name"),
    out: Path | None = typer.Option(None, "--out",
                                    help="目标路径（缺省当前目录原文件名）"),
) -> None:
    """下载数据集文件到本地（登记文件优先，包内置精度样本回退）。"""
    import shutil

    from aite.service.dataset_files import resolve_download

    async def _go():
        async with repo_session(aite_config.db_url_from_env()) as repo:
            return await resolve_download(repo, ref)

    try:
        result = asyncio.run(_go())
    except ValueError as exc:
        err_console.print(f"[red]download failed:[/red] {exc}")
        raise SystemExit(1) from exc
    dest = out or Path(result["filename"])
    if result["kind"] == "file":
        shutil.copyfile(result["path"], dest)
    else:
        dest.write_bytes(result["content"])
    console.print(f"[green]downloaded[/green] {ref} -> {dest}")


@dataset_app.command("pull-url")
def dataset_pull_url(
    name: str = typer.Argument(..., help="数据集名"),
    type_: str = typer.Option("accuracy", "--type",
                              help="数据集类型（accuracy / functional / perf）"),
    url: str = typer.Option(
        "", "--url",
        help="直链地址（缺省用设置 remote_url；结尾带 / 按 <地址>/<名>.jsonl 拼接）"),
    notes: str = typer.Option("", "--notes"),
    force: bool = typer.Option(False, "--force", help="覆盖同名"),
) -> None:
    """从远程拉取地址导入数据集（OBS 桶/其他直链，内网 HF 不通场景）。"""
    from aite.service.dataset_files import remote_import

    async def _go():
        async with repo_session(aite_config.db_url_from_env()) as repo:
            return await remote_import(repo, dataset_type=type_, name=name,
                                       url=url, notes=notes, force=force)

    try:
        row = asyncio.run(_go())
    except ValueError as exc:
        err_console.print(f"[red]pull failed:[/red] {exc}")
        raise SystemExit(1) from exc
    console.print(f"[green]imported[/green] {row['type']}/{row['name']} "
                  f"({(row.get('stats') or {}).get('count', '?')} records) -> {row['path']}")


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
    log_dir = Path("logs")   # 跟随启动时 CWD（与 ./aite.db、./reports 同一约定）
    console.print(f"[bold]aite[/bold] v{__version__} serving on http://{host}:{port}，"
                  f"日志落盘 {log_dir}：{APP_LOG_NAME} 业务 / {SERVER_LOG_NAME} 访问·服务器")
    uvicorn.run(application, host=host, port=port, reload=reload,
                log_config=build_web_log_config(log_dir))


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


# ------------------------------------------------------- 全局 common 配置（模型预制/环境缺省）
# data/common.yaml：models 录入一次、suite 里 target: {profile: <名>} 一行引用；
# tools 段为工具适配器环境缺省（python_exe/timeout_s…），suite 内联同名键覆盖。
common_app = typer.Typer(help="全局 common 配置（data/common.yaml：模型预制 + 工具环境缺省）")
app.add_typer(common_app, name="common")


@common_app.command("show")
def common_show() -> None:
    """查看已录入的模型 profile 与工具环境缺省（suite 引用前先看这里）。"""
    from aite.core.profiles import common_path, load_common

    path = common_path()
    common = load_common()
    models = common.get("models") or {}
    tools_defaults = common.get("tools") or {}
    if not models and not tools_defaults:
        console.print(f"[yellow]无 common 配置[/yellow]（{path} 不存在或为空；"
                      "录入模板见 examples/common.example.yaml）")
        return
    if models:
        table = Table(title=f"模型预制 models（{path}）")
        for col in ("profile", "base_url", "model", "api_key_env"):
            table.add_column(col)
        for name, entry in sorted(models.items()):
            entry = entry if isinstance(entry, dict) else {}
            table.add_row(name, str(entry.get("base_url") or ""),
                          str(entry.get("model") or ""),
                          str(entry.get("api_key_env") or ""))
        console.print(table)
    if tools_defaults:
        console.print(f"[bold]工具环境缺省 tools[/bold]: "
                      f"{json.dumps(tools_defaults, ensure_ascii=False)}")


def main() -> None:  # console_scripts entrypoint
    app()


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


# ============================================ 功能域资产（V1-PLAN §10.4/§10.5）
@contextmanager
def _fasset_errors():
    """CLI 侧资产异常映射（与 routers/functional.py 的 HTTP 映射同口径）。"""
    try:
        yield
    except FileExistsError as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise SystemExit(1) from exc
    except FileNotFoundError as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise SystemExit(1) from exc
    except ValueError as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise SystemExit(1) from exc


fscen_app = typer.Typer(help="评估场景管理（功能域，builtin+用户根合并）")
app.add_typer(fscen_app, name="fscenario")
fscript_app = typer.Typer(help="功能脚本（用例原子）管理")
app.add_typer(fscript_app, name="fscript")
ftag_app = typer.Typer(help="维度标签库（功能域共享）")
app.add_typer(ftag_app, name="ftag")


@fscen_app.command("list")
def fscenario_list() -> None:
    """列出评估场景（含能力覆盖与用例数）。"""
    from aite.service import functional_assets as assets

    with _fasset_errors():
        items = assets.scan_scenarios()
    table = Table(title=f"functional scenarios ({len(items)})")
    for col in ("id", "display_name", "dimension", "forms",
                "scripts", "cases", "coverage"):
        table.add_column(col, overflow="fold")
    for i in items:
        table.add_row(i["id"], i["display_name"], i["dimension"] or "-",
                      i["forms"], str(i["scripts"]),
                      str(i["cases"]),
                      json.dumps(i["coverage"], ensure_ascii=False))
    console.print(table)


@fscen_app.command("create")
def fscenario_create(
    scenario_id: str = typer.Argument(..., help="场景英文编号（小写字母开头，允许数字/-/_）"),
    name: str = typer.Option("", "--name", help="显示名（中文名称，缺省 = 编号）"),
    dimension: str = typer.Option("", "--dimension", help="维度标签（须在标签库内）"),
    description: str = typer.Option("", "--description"),
) -> None:
    """新建评估场景（写 manifest；创建即注册）。"""
    from aite.service import functional_assets as assets

    async def _go():
        async with repo_session(aite_config.db_url_from_env()) as repo:
            return await assets.create_scenario(repo, scenario_id, name,
                                                dimension, description)

    with _fasset_errors():
        row = asyncio.run(_go())
    console.print(f"[green]created[/green] {row['id']}（{row['display_name']}，"
                  f"dimension={row['dimension'] or '-'}）")


@fscen_app.command("delete")
def fscenario_delete(
    scenario_id: str = typer.Argument(..., help="场景英文编号"),
    force: bool = typer.Option(False, "--force",
                               help="场景下仍有脚本/用例时级联删除（默认拒删）"),
) -> None:
    """删除评估场景（默认有内容拒删；--force 级联删除整目录）。"""
    from aite.service import functional_assets as assets

    with _fasset_errors():
        row = assets.delete_scenario(scenario_id, force)
    console.print(f"[green]deleted[/green] {row['id']}（{row['display_name']}，"
                  f"含 {row['scripts']} 脚本 / {row['cases']} 用例）")


@fscript_app.command("list")
def fscript_list(scenario: str = typer.Option(None, "--scenario")) -> None:
    """列出功能脚本（用例原子，跨场景）。"""
    from aite.service import functional_assets as assets

    items = assets.list_scripts(scenario or None)
    table = Table(title=f"functional scripts ({len(items)})")
    for col in ("id", "name", "scenario", "case_type", "row_count", "source",
                "external_ref"):
        table.add_column(col, overflow="fold")
    for i in items:
        table.add_row(i["id"], i["name"], i.get("scenario", "-"),
                      i.get("case_type", i.get("dimension", "-")),
                      str(i.get("row_count", 0)), i.get("source", "-"),
                      i.get("external_ref") or "-")
    console.print(table)


@fscript_app.command("show")
def fscript_show(script_id: str = typer.Argument(...)) -> None:
    """查看脚本：登记 + 全部数据行。"""
    from aite.service import functional_assets as assets

    with _fasset_errors():
        row = assets.get_script(script_id)
    console.print(f"[bold]{row['name']}[/bold]（{row['id']}）@ {row['scenario']} "
                  f"· {row['case_type']} · {row['source']}"
                  + (f" · 外部用例 {row['external_ref']}" if row["external_ref"] else ""))
    for r in row["rows"]:
        console.print(f"  - [dim]{r['id']}[/dim] {r.get('desc', '')} "
                      f"expect={json.dumps(r.get('expect'), ensure_ascii=False)}")


@fscript_app.command("import")
def fscript_import(
    script_id: str = typer.Argument(...),
    scenario: str = typer.Option(..., "--scenario", help="目标场景 id"),
    name: str = typer.Option(..., "--name", help="脚本名称（中文名称，测试意图）"),
    file: Path = typer.Option(None, "--file", help="cases.jsonl 文件（与 --dataset 二选一）"),
    dataset: str = typer.Option(None, "--dataset", help="functional 型数据集名"),
    case_type: str = typer.Option("基础", "--case-type"),
    external_ref: str = typer.Option("", "--external-ref", help="外部平台用例编号"),
) -> None:
    """导入生成脚本：jsonl 文件 / 数据集整体导入（1 导入 = 1 脚本 N 行）。"""
    from aite.service import functional_assets as assets

    if bool(file) == bool(dataset):
        err_console.print("[red]--file 与 --dataset 恰好提供其一[/red]")
        raise SystemExit(1)
    jsonl = file.read_text(encoding="utf-8") if file else ""

    async def _go():
        async with repo_session(aite_config.db_url_from_env()) as repo:
            return await assets.import_script(
                repo, scenario_id=scenario, script_id=script_id, name=name,
                case_type=case_type, external_ref=external_ref,
                jsonl=jsonl, dataset=dataset or "")

    with _fasset_errors():
        row = asyncio.run(_go())
    console.print(f"[green]imported[/green] {row['id']}（{row['row_count']} 行）"
                  f" -> {row['scenario']}")


@fscript_app.command("delete")
def fscript_delete(script_id: str = typer.Argument(...),
                   yes: bool = typer.Option(False, "--yes")) -> None:
    """删除脚本（登记 + 行文件 + 重派生 cases.jsonl）。"""
    from aite.service import functional_assets as assets

    if not yes and not typer.confirm(f"删除脚本 {script_id}?"):
        raise typer.Abort()
    with _fasset_errors():
        row = assets.delete_script(script_id)
    console.print(f"[green]deleted[/green] {row['id']}（{row['scenario']}）")


@ftag_app.command("list")
def ftag_list() -> None:
    """列出维度标签库（内置 + 自定义）。"""
    from aite.service import functional_assets as assets

    async def _go():
        async with repo_session(aite_config.db_url_from_env()) as repo:
            return await assets.list_dim_tags(repo)

    items = asyncio.run(_go())
    for t in items:
        console.print(f"{t['name']}  [dim]({t['source']})[/dim]")


@ftag_app.command("add")
def ftag_add(name: str = typer.Argument(...)) -> None:
    """新增维度标签（入库后所有场景可复用）。"""
    from aite.service import functional_assets as assets

    async def _go():
        async with repo_session(aite_config.db_url_from_env()) as repo:
            return await assets.create_dim_tag(repo, name)

    with _fasset_errors():
        row = asyncio.run(_go())
    console.print(f"[green]added[/green] {row['name']}")


# ============================================ 精度域资产（V1-PLAN §10.4 accuracy）
# 场景与脚本全部用户自管，无预置；维度标签复用共享标签库（ftag）。
ascen_app = typer.Typer(help="精度场景管理（用户自管，无预置）")
app.add_typer(ascen_app, name="ascenario")
ascript_app = typer.Typer(help="精度脚本（固化 spec）管理")
app.add_typer(ascript_app, name="ascript")


@ascen_app.command("list")
def ascenario_list() -> None:
    """列出精度场景（含脚本数与数据条数）。"""
    from aite.service import accuracy_assets as assets

    async def _go():
        async with repo_session(aite_config.db_url_from_env()) as repo:
            return await assets.scan_scenarios(repo)

    with _fasset_errors():
        items = asyncio.run(_go())
    table = Table(title=f"accuracy scenarios ({len(items)})")
    for col in ("id", "display_name", "dimension", "scripts", "cases", "description"):
        table.add_column(col, overflow="fold")
    for i in items:
        table.add_row(i["id"], i["display_name"], i["dimension"] or "-",
                      str(i["scripts"]),
                      "-" if i["cases"] is None else str(i["cases"]),
                      i["description"])
    console.print(table)


@ascen_app.command("create")
def ascenario_create(
    scenario_id: str = typer.Argument(..., help="场景英文编号（小写字母开头，允许数字/-/_）"),
    name: str = typer.Option("", "--name", help="显示名（中文名称，缺省 = 编号）"),
    dimension: str = typer.Option("", "--dimension", help="维度标签（须在共享标签库内）"),
    description: str = typer.Option("", "--description"),
) -> None:
    """新建精度场景（写 manifest；创建即注册）。"""
    from aite.service import accuracy_assets as assets

    async def _go():
        async with repo_session(aite_config.db_url_from_env()) as repo:
            return await assets.create_scenario(repo, scenario_id, name,
                                                dimension, description)

    with _fasset_errors():
        row = asyncio.run(_go())
    console.print(f"[green]created[/green] {row['id']}（{row['display_name']}，"
                  f"dimension={row['dimension'] or '-'}）")


@ascen_app.command("delete")
def ascenario_delete(
    scenario_id: str = typer.Argument(..., help="场景英文编号"),
    force: bool = typer.Option(False, "--force",
                               help="场景下仍有脚本时级联删除（默认拒删）"),
) -> None:
    """删除精度场景（默认有脚本拒删；--force 级联删除整目录）。"""
    from aite.service import accuracy_assets as assets

    with _fasset_errors():
        row = asyncio.run(assets.delete_scenario(scenario_id, force))
    console.print(f"[green]deleted[/green] {row['id']}（{row['display_name']}，"
                  f"含 {row['scripts']} 脚本）")


@ascript_app.command("list")
def ascript_list(scenario: str = typer.Option(None, "--scenario")) -> None:
    """列出精度脚本（跨场景；数据集 + 条数瘦身口径）。"""
    from aite.service import accuracy_assets as assets

    async def _go():
        async with repo_session(aite_config.db_url_from_env()) as repo:
            return await assets.list_scripts(repo, scenario or None)

    with _fasset_errors():
        items = asyncio.run(_go())
    table = Table(title=f"accuracy scripts ({len(items)})")
    for col in ("id", "name", "scenario", "datasets", "条数", "updated_at"):
        table.add_column(col, overflow="fold")
    for i in items:
        table.add_row(i["id"], i["name"], i["scenario"],
                      " ".join(i["datasets"]) or "-",
                      "-" if i["case_count"] is None else str(i["case_count"]),
                      (i.get("updated_at") or "")[:10])
    console.print(table)


@ascript_app.command("create")
def ascript_create(
    script_id: str = typer.Argument(..., help="脚本英文编号"),
    scenario: str = typer.Option(..., "--scenario", help="目标场景 id"),
    name: str = typer.Option("", "--name", help="脚本中文名称（缺省 = 编号）"),
    file: Path = typer.Option(..., "--file", help="suite raw yaml 文件（spec）"),
    description: str = typer.Option("", "--description"),
) -> None:
    """新建精度脚本：spec 经 resolve_suite 真实校验后落盘。"""
    import yaml as _yaml

    from aite.service import accuracy_assets as assets

    spec = _yaml.safe_load(file.read_text(encoding="utf-8"))

    async def _go():
        async with repo_session(aite_config.db_url_from_env()) as repo:
            return await assets.create_script(repo, scenario_id=scenario,
                                              script_id=script_id, name=name,
                                              description=description, spec=spec)

    with _fasset_errors():
        row = asyncio.run(_go())
    count = row["case_count"]
    console.print(f"[green]created[/green] {row['id']}（{row['name']}，"
                  f"{len(row['datasets'])} 数据集 · 条数 "
                  f"{'—' if count is None else count}） -> {row['scenario']}")


@ascript_app.command("show")
def ascript_show(script_id: str = typer.Argument(...)) -> None:
    """查看脚本：登记 + 完整 spec。"""
    import yaml as _yaml

    from aite.service import accuracy_assets as assets

    async def _go():
        async with repo_session(aite_config.db_url_from_env()) as repo:
            return await assets.get_script(repo, script_id)

    with _fasset_errors():
        row = asyncio.run(_go())
    console.print(f"[bold]{row['name']}[/bold]（{row['id']}）@ {row['scenario']}"
                  + (f" · {row['description']}" if row.get("description") else ""))
    count = row["case_count"]
    console.print(f"数据集: {' '.join(row['datasets']) or '-'} · 条数: "
                  f"{'—' if count is None else count}")
    console.print(_yaml.safe_dump(row["spec"], allow_unicode=True, sort_keys=False))


@ascript_app.command("delete")
def ascript_delete(script_id: str = typer.Argument(...),
                   yes: bool = typer.Option(False, "--yes")) -> None:
    """删除脚本（场景目录内 yaml）。"""
    from aite.service import accuracy_assets as assets

    if not yes and not typer.confirm(f"删除脚本 {script_id}?"):
        raise typer.Abort()

    with _fasset_errors():
        row = asyncio.run(assets.delete_script(script_id))
    console.print(f"[green]deleted[/green] {row['id']}（{row['scenario']}）")


# `python -m aite.cli.main` 入口必须排在全部 add_typer 之后：提前执行会在
# 后续命令组注册前退出（2026-11-08 效率复盘教训：ascenario/ascript 曾因此
# 「No such command」）。
if __name__ == "__main__":
    main()
