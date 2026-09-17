"""双引擎压测对比工具：交替跑 N 轮取中位——云端对比的唯一可信方法。

背景（roadmap 2026-09-16 教训）：DeepSeek 云端存在显著时段性状态，同配置
相邻两轮行为可完全不同（尖峰 9 vs 0），**任何单轮跨引擎对比都不可靠**。

用法：
  # 交替驱动新跑（按给定顺序 L→E→L→E…，每轮结束即入库）
  python scripts/compare_perf.py --suites examples/locust_deepseek.yaml \
      examples/perf_evalscope_deepseek.yaml --rounds 2

  # 分析已有 run（按 run_id 或按 suite 名 + 起始日期聚合）
  python scripts/compare_perf.py --runs <id1> <id2> [<id3>...]
  python scripts/compare_perf.py --names perf-locust perf-evalscope-deepseek \
      --since 2026-09-17

输出：各 suite 指标中位数对照表（overall 行，与 CLI 汇总同源）。
"""

from __future__ import annotations

import argparse
import asyncio
import sqlite3
import statistics
from pathlib import Path

_METRIC_ORDER = ("ttft_ms", "ttft_p95_ms", "latency_avg_ms", "latency_p95_ms",
                 "tpot_ms", "itl_ms", "qps", "output_tps", "error_rate")


def fetch_metrics(db: str, run_ids: list[str]) -> dict[str, dict[str, float]]:
    """run_id → {metric: overall 值}（dataset IS NULL 行，CLI 汇总同源）。"""
    conn = sqlite3.connect(db)
    out: dict[str, dict[str, float]] = {}
    for rid in run_ids:
        rows = conn.execute(
            "SELECT metric, value FROM run_metric_summary "
            "WHERE run_id = ? AND dataset IS NULL AND value IS NOT NULL",
            (rid,)).fetchall()
        out[rid] = {m: v for m, v in rows}
    conn.close()
    return out


def resolve_run_ids(db: str, names: list[str], since: str | None) -> dict[str, str]:
    """按 suite 名（+起始日期）→ 该 suite 最新一次 passed run 的 id。"""
    conn = sqlite3.connect(db)
    out: dict[str, str] = {}
    for name in names:
        sql = ("SELECT id, started_at FROM test_run WHERE run_name = ? "
               "AND status = 'passed'")
        args: list = [name]
        if since:
            sql += " AND started_at >= ?"
            args.append(f"{since} 00:00:00" if " " not in since else since)
        sql += " ORDER BY started_at DESC LIMIT 1"
        row = conn.execute(sql, args).fetchone()
        if not row:
            raise SystemExit(f"未找到 passed run: {name}（since={since}）")
        out[name] = row[0]
        print(f"  {name} → {row[0][:8]}… ({row[1][:19]})")
    conn.close()
    return out


def median_table(groups: dict[str, list[dict[str, float]]]) -> None:
    """各 suite 跨轮取中位 → 对照表（含相对首列的偏差）。"""
    medians = {name: {m: statistics.median(vals[m] for vals in runs
                      if m in vals)
                      for m in _METRIC_ORDER
                      if any(m in vals for vals in runs)}
               for name, runs in groups.items()}
    base = next(iter(medians))
    metrics = [m for m in _METRIC_ORDER if m in medians[base]]
    names = list(medians)
    print(f"{'metric':<18}" + "".join(f"{n[:16]:>18}" for n in names))
    for m in metrics:
        row = f"{m:<18}"
        for name in names:
            row += f"{medians[name][m]:>18.4g}"
        base_v = medians[base][m]
        if base_v:
            worst = max(abs(medians[n][m] / base_v - 1) for n in names)
            row += f"   最大偏差 {worst * 100:.1f}%"
        print(row)


def drive(suites: list[Path], rounds: int, db: str) -> None:
    """交替驱动：每轮按给定顺序各跑一次（L→E→L→E…），单轮失败不中断。"""
    import yaml

    from aite import config
    from aite.storage import repo_session
    from aite.tools import run_tool_suite

    raws = []
    for p in suites:
        raw = yaml.safe_load(Path(p).read_text(encoding="utf-8"))
        raws.append((str(p), raw))
    for rnd in range(1, rounds + 1):
        for path, raw in raws:
            print(f"=== 第 {rnd}/{rounds} 轮：{raw.get('run_name')} ({path}) ===")

            async def _run(raw=raw):
                async with repo_session(db or config.db_url_from_env()) as repo:
                    report = await run_tool_suite(raw, repo)
                print(f"    → {report.status}")
                return report

            try:
                asyncio.run(_run())
            except Exception as exc:  # noqa: BLE001 - 单轮失败不中断交替序列
                print(f"    → FAILED: {exc}")


def main() -> None:
    ap = argparse.ArgumentParser(description="双引擎压测对比（交替 N 轮取中位）")
    ap.add_argument("--suites", nargs="+", help="suite yaml 列表（交替驱动模式）")
    ap.add_argument("--rounds", type=int, default=2, help="交替轮数（缺省 2）")
    ap.add_argument("--runs", nargs="+", help="分析模式：run_id 列表")
    ap.add_argument("--names", nargs="+", help="分析模式：suite 名列表（各取最新 passed）")
    ap.add_argument("--since", help="分析模式起始日期（YYYY-MM-DD）")
    ap.add_argument("--db", default="sqlite+aiosqlite:///./aite.db")
    args = ap.parse_args()

    db = args.db.replace("sqlite+aiosqlite:///", "").replace("sqlite:///", "")
    if args.suites:
        drive([Path(p) for p in args.suites], args.rounds, args.db)
        return
    if args.runs:
        ids = {rid: rid[:8] for rid in args.runs}
    elif args.names:
        print("解析最新 passed run：")
        ids = resolve_run_ids(db, args.names, args.since)
    else:
        ap.error("需要 --suites（驱动模式）或 --runs/--names（分析模式）")

    labels = ids                                   # {展示标签: run_id}
    metrics = fetch_metrics(db, list(labels.values()))
    groups = {label: [metrics[rid]] for label, rid in labels.items()}
    median_table(groups)


if __name__ == "__main__":
    main()
