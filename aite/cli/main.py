"""aite CLI (Typer). Core API stays dependency-free of Typer/Rich/FastAPI."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from aite import __version__
from aite.core.engine import ExecutionEngine, build_repository
from aite.core.loader import load_suite
from aite.core.registry import registry
from aite.reporting.rebuild import rebuild_report
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


def _db_url(config=None, db: str | None = None) -> str:
    if db:
        return db
    if os.environ.get("AITE_DB"):
        return os.environ["AITE_DB"]
    if config is not None:
        return config.storage.url
    return "sqlite+aiosqlite:///./aite.db"


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
        base / "data" / "sample_qa.jsonl": SAMPLE_JSONL,
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
    quiet: bool = typer.Option(False, "--quiet", help="no live progress, log only"),
    log_level: str = typer.Option("INFO"),
) -> None:
    """Run a test suite; exit code 0 = passed, 1 = failed/error (CI friendly)."""
    setup_logging(log_level, quiet=quiet)
    suite = load_suite(config)
    if concurrency:
        suite.concurrency = concurrency
    if max_cost_usd is not None:
        suite.max_cost_usd = max_cost_usd
    if report_formats:
        suite.report = [f.strip() for f in report_formats.split(",") if f.strip()]
    dataset_ids = [d.strip() for d in datasets.split(",")] if datasets else None

    from aite.cli.progress import RichProgressReporter

    async def _run() -> int:
        repo = build_repository(suite.storage.url)
        await repo.init()
        try:
            progress = None if quiet else RichProgressReporter()
            engine = ExecutionEngine(repo, progress=progress)
            report = await engine.run_suite(suite, limit=limit, dataset_ids=dataset_ids)
            _print_report_summary(report)
            return report.exit_code
        finally:
            await repo.close()

    raise SystemExit(asyncio.run(_run()))


def _print_report_summary(report) -> None:
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
    reports = report.details.get("reports") or {}
    for fmt, path in reports.items():
        console.print(f"  report [{fmt}]: {path}")


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
        repo = build_repository(_db_url(db=db))
        await repo.init()
        try:
            report = await rebuild_report(repo, run_id)
            if report is None:
                err_console.print(f"[red]run not found:[/red] {run_id}")
                raise SystemExit(1)
            from aite.core.registry import resolve_renderer

            out_dir = Path(out) / report.run_name / run_id[:8]
            for fmt in [f.strip() for f in formats.split(",") if f.strip()]:
                path = resolve_renderer(fmt)().render(report, out_dir)
                console.print(f"[green]wrote[/green] {path}")
        finally:
            await repo.close()

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
        repo = build_repository(_db_url(db=db))
        await repo.init()
        try:
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
        finally:
            await repo.close()

    asyncio.run(_go())


dataset_app = typer.Typer(help="builtin dataset catalog")
app.add_typer(dataset_app, name="dataset")


@dataset_app.command("list")
def dataset_list() -> None:
    """List builtin (packaged, sample-edition) benchmarks."""
    from aite.datasets import builtin_ids, builtin_info, try_register_hf

    table = Table(title="builtin datasets")
    for col in ("id", "task", "recommended metrics", "full-scale source"):
        table.add_column(col)
    for ds_id in builtin_ids():
        info = builtin_info(ds_id)
        table.add_row(ds_id, info.get("task", ""),
                      ", ".join(info.get("metrics", [])), info.get("upstream", ""))
    console.print(table)
    extra = try_register_hf()
    if not extra:
        console.print("[dim]hf_hub loader available with: pip install 'aite[bench]'[/dim]")


@dataset_app.command("show")
def dataset_show(builtin_id: str = typer.Argument(...)) -> None:
    from aite.datasets import builtin_info

    info = builtin_info(builtin_id)
    if not info:
        err_console.print(f"[red]unknown builtin id:[/red] {builtin_id}")
        raise SystemExit(1)
    for k, v in info.items():
        console.print(f"[bold]{k}[/bold]: {v}")


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
        repo = build_repository(_db_url(db=db))
        await repo.init()
        try:
            run_id = await import_results(repo, results, fmt=format, run_name=run_name)
            console.print(f"[green]imported[/green] run_id={run_id}")
        finally:
            await repo.close()

    asyncio.run(_go())


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1"),
    port: int = typer.Option(8300),
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
