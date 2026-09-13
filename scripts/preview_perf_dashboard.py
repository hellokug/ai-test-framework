"""压测大屏预览：不接前端，本地生成与 F1 大屏同源的 ECharts 页面。

数据契约与 `GET /runs/{id}/timeseries` + `/runs/{id}` 完全一致——本脚本的
HTML 就是 F1 前端大屏的参考实现（Vue/ECharts 同源）。

用法：
  python scripts/preview_perf_dashboard.py                 # 自动找最新真实数据
  python scripts/preview_perf_dashboard.py --data <timeseries.json>
  python scripts/preview_perf_dashboard.py --demo          # 合成演示数据（含故障窗口）
  python scripts/preview_perf_dashboard.py --run-id <id>   # 从库读（summaries 全量）
输出：perf_dashboard_preview.html（浏览器打开）
"""

from __future__ import annotations

import argparse
import json
import random
from datetime import datetime
from pathlib import Path

OUT_DEFAULT = "perf_dashboard_preview.html"
ERROR_KEYS_SKIP = ("engine", "bucket_seconds", "series", "combined", "errors")


# ---------------------------------------------------------------- data loading

def find_all_timeseries() -> list[Path]:
    """全部压测时序产物，按生成时间倒序（最新在前）。"""
    return sorted(Path("reports").glob("**/timeseries.json"),
                  key=lambda p: p.stat().st_mtime, reverse=True)


def describe(path: Path) -> str:
    """一行摘要：时间 / engine / run_name / 档数·点数。"""
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        n_pts = sum(len(s.get("points", [])) for s in d.get("series", []))
        stamp = datetime.fromtimestamp(path.stat().st_mtime).strftime("%m-%d %H:%M")
        # run_name：新数据在 JSON 里；旧文件从路径 reports/tool/<engine>/<run>/<stamp> 解析
        run_name = d.get("run_name") or (path.parts[-3] if len(path.parts) >= 3 else "?")
        return (f"{stamp}  {d.get('engine', '?'):<16} "
                f"{run_name:<20} {n_pts:>4} 点")
    except (ValueError, OSError):
        return f"{path}（不可读）"


def load_from_file(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    data.setdefault("run_name", path.parent.parent.name)
    data.setdefault("run_id", "")
    data.setdefault("status", "passed")
    data.setdefault("chaos_events", [])
    data.setdefault("summaries", [])
    # 旧格式回填墙钟：以文件 mtime 为曲线终点，按相对 t 反推各点 epoch
    pts = (data.get("combined") or (data.get("series") or [{}])[0].get("points", []))
    if pts and all("ts" not in p for p in pts):
        end = path.stat().st_mtime
        span = max(p["t"] for p in pts) or 1
        for p in pts:
            p["ts"] = round(end - (span - p["t"]), 3)
    return data


def make_demo_data() -> dict:
    """合成四档阶梯 + 故障注入窗口的演示数据（结构 = 真实契约）。

    墙钟时间：以当前时刻为曲线终点，按相对 t 回铺各点的 ts（epoch 秒）。
    """
    import time as _time

    rng = random.Random(42)
    end_epoch = _time.time()
    series, combined, summaries = [], [], []
    offset = 0
    for users in (8, 16, 32, 64):
        points = []
        ttft_base = 300 + users * 28
        for i in range(12):
            t = offset + i * 5
            chaos = 22 <= t <= 34                     # 第 3 档中段注入窗口
            ttft = ttft_base + rng.uniform(-80, 120) + (2500 if chaos else 0)
            ts = round(end_epoch - (260 - t), 1)
            points.append({
                "t": round(t - offset, 1), "users": users, "ts": ts,
                "ttft_avg": round(ttft, 1), "ttft_p95": round(ttft * 1.45, 1),
                "latency_avg": round(ttft * 2.1, 1),
                "latency_p95": round(ttft * 3.0, 1),
                "tpot_avg": round(18 + users * 0.7 + rng.uniform(-4, 6), 1),
                "tpot_p95": round(28 + users * 1.1, 1),
                "itl_avg": round(12 + users * 0.5 + rng.uniform(-3, 5), 1),
                "rps": round(users * 2.2 + rng.uniform(-4, 6), 2),
                "tok_s": round(users * 44 + rng.uniform(-60, 90), 1),
                "errors": rng.randint(0, 3) if users == 64 else 0,
            })
            combined.append({**points[-1], "t": round(t, 1)})
        offset += 65
        label = f"locust:u{users}"
        series.append({"users": users, "run": 0, "label": label, "points": points})
        summaries.append({"dataset": label, "metric": "qps",
                          "value": round(users * 2.2, 2), "count": users * 26,
                          "threshold": None, "passed": None})
        summaries.append({"dataset": label, "metric": "ttft_ms",
                          "value": round(ttft_base, 1), "count": users * 26,
                          "threshold": 3000, "passed": ttft_base < 3000})
    return {
        "engine": "evalscope-perf", "run_name": "demo-ramp", "run_id": "demo",
        "status": "passed", "bucket_seconds": 5.0,
        "series": series, "combined": combined, "summaries": summaries,
        "errors": [{"key": "HTTP 429 Too Many Requests", "count": 9},
                   {"key": "ConnectionTimeout", "count": 3}],
        "chaos_events": [
            {"fault": "toxiproxy:latency", "phase": "injected", "at": 22.0},
            {"fault": "toxiproxy:latency", "phase": "verified", "at": 22.4},
            {"fault": "toxiproxy:latency", "phase": "recovered", "at": 34.0},
        ],
    }


def load_from_db(run_id: str, storage_url: str) -> dict:
    import asyncio

    from aite.storage import repo_session

    async def _load():
        async with repo_session(storage_url) as repo:
            run = await repo.get_run(run_id)
            if not run:
                raise SystemExit(f"run not found: {run_id}")
            return run

    run = asyncio.run(_load())
    from aite.service.web_service import load_run_timeseries

    ts = load_run_timeseries(run) or {}
    ts.update({"run_id": run_id, "run_name": run["run_name"],
               "status": run["status"], "summaries": run.get("summaries", []),
               "chaos_events": (run.get("details") or {}).get("failures", [])
               and [] or []})
    return ts


# ---------------------------------------------------------------- html render

_HTML = """<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8">
<title>压测大屏预览 — __RUN_NAME__</title>
<script src="https://cdn.jsdelivr.net/npm/echarts@5.5.1/dist/echarts.min.js"></script>
<style>
body{font-family:system-ui,sans-serif;background:#f0f2f5;margin:16px}
h1{font-size:18px} h3{font-size:14px;margin:12px 0 4px}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.card{background:#fff;border-radius:8px;padding:8px;box-shadow:0 1px 2px #0001}
.chart{width:100%;height:300px}.full{grid-column:1/3}
.badge{display:inline-block;padding:2px 10px;border-radius:10px;color:#fff;font-size:12px}
table{border-collapse:collapse;width:100%;font-size:12px}
td,th{border:1px solid #e8eaee;padding:4px 8px}
</style></head><body>
<h1>压测大屏预览 <span id="meta"></span></h1>
<div class="grid">
 <div class="card"><h3>TTFT（首 token 延迟）<span style="color:#999">ms</span></h3><div id="ttft" class="chart"></div></div>
 <div class="card"><h3>TPOT / ITL（token 生成）<span style="color:#999">ms</span></h3><div id="tpot" class="chart"></div></div>
 <div class="card"><h3>延迟 p95 + RPS</h3><div id="lat" class="chart"></div></div>
 <div class="card"><h3>token 吞吐 + 并发阶梯</h3><div id="tps" class="chart"></div></div>
 <div class="card"><h3>错误统计</h3><div id="err" class="chart" style="height:200px"></div></div>
 <div class="card"><h3>档级汇总</h3><div id="tbl"></div></div>
</div>
<script>window.__D__ = __DATA__;</script>
<script>
const D = window.__D__;
const pts = D.combined && D.combined.length ? D.combined
          : (D.series[0] ? D.series[0].points : []);
document.getElementById("meta").innerHTML =
  `<span class="badge" style="background:${D.status === "passed" ? "#16a34a" : "#d97706"}">${D.status}</span>` +
  ` <b>${D.run_name}</b> <span style="color:#8a919f">${D.engine} · ${D.series.length} 档 · ${pts.length} 点</span>`;
// x 轴：优先墙钟时间戳（ts epoch 秒 → ms），无 ts 的旧数据回落相对秒
const USE_WALL = pts.length && pts[0].ts !== undefined;
const x = pts.map(p => USE_WALL ? p.ts * 1000 : p.t);
const chaosWindows = [];
if (D.chaos_events && D.chaos_events.length) {
  let start = null;
  for (const e of [...D.chaos_events].sort((a, b) => a.at - b.at)) {
    if (e.phase === "injected" && start === null) start = e.at;
    if (e.phase === "recovered" && start !== null) {
      chaosWindows.push([{xAxis: start, itemStyle: {color: "rgba(217,119,6,0.12)"}},
                         {xAxis: e.at}]);
      start = null;
    }
  }
}
const mk = (id, opt) => echarts.init(document.getElementById(id)).setOption(opt);
// 时间轴：墙钟模式 type=time（HH:MM，1/5min 自动刻度）；旧数据相对秒
const xaxis = USE_WALL
  ? {type: "time",
     axisLabel: {formatter: "{HH}:{mm}"},
     splitLine: {show: true}}
  : {type: "value", name: "s"};
const axis = (name) => ({type: "value", name});
function lineChart(id, title, seriesDef, markChaos) {
  const opt = {tooltip: {trigger: "axis"}, legend: {top: 0},
    grid: {left: 50, right: 20, top: 30, bottom: 25},
    xAxis: xaxis, series: seriesDef};
  if (chaosWindows.length) {
    opt.series = opt.series.map(s => ({...s, markArea: {silent: true, data: chaosWindows}}));
  }
  mk(id, {tooltip: {trigger: "axis"}, legend: {top: 0},
          grid: {left: 50, right: 20, top: 30, bottom: 25},
          xAxis: xaxis, yAxis: seriesDef.length > 1 && seriesDef[1].yAxisIndex ? [axis(title), axis(seriesDef[1].name)] : axis(title),
          series: seriesDef});
}
lineChart("ttft", "ms", [
  {name: "TTFT avg", type: "line", showSymbol: false, data: x.map((t, i) => [t, pts[i].ttft_avg])},
  {name: "TTFT p95", type: "line", showSymbol: false, lineStyle: {type: "dashed"},
   data: x.map((t, i) => [t, pts[i].ttft_p95])}], true);
lineChart("tpot", "ms", [
  {name: "TPOT avg", type: "line", showSymbol: false, data: x.map((t, i) => [t, pts[i].tpot_avg])},
  {name: "TPOT p95", type: "line", showSymbol: false, lineStyle: {type: "dashed"},
   data: x.map((t, i) => [t, pts[i].tpot_p95])},
  {name: "ITL avg", type: "line", showSymbol: false, data: x.map((t, i) => [t, pts[i].itl_avg])}], true);
mk("lat", {tooltip: {trigger: "axis", formatter: USE_WALL ? (ps => new Date(ps[0].value).toTimeString().slice(0, 5) + " " + ps[0].value[1]) : undefined}, legend: {top: 0},
  grid: {left: 50, right: 55, top: 30, bottom: 25},
  xAxis: xaxis,
  yAxis: [axis("ms"), axis("rps")],
  series: [
    {name: "Latency p95", type: "line", showSymbol: false,
     data: x.map((t, i) => [t, pts[i].latency_p95])},
    {name: "RPS", type: "line", showSymbol: false, yAxisIndex: 1,
     data: x.map((t, i) => [t, pts[i].rps])}]});
mk("tps", {tooltip: {trigger: "axis"}, legend: {top: 0},
  grid: {left: 55, right: 55, top: 30, bottom: 25},
  xAxis: xaxis,
  yAxis: [axis("tok/s"), axis("users")],
  series: [
    {name: "tok/s", type: "line", showSymbol: false,
     data: x.map((t, i) => [t, pts[i].tok_s])},
    {name: "users", type: "line", showSymbol: false, step: "end", yAxisIndex: 1,
     lineStyle: {type: "dotted"}, data: x.map((t, i) => [t, pts[i].users])}]});
const errs = D.errors || [];
mk("err", {grid: {left: 160, right: 20, top: 10, bottom: 25},
  xAxis: {type: "value"}, yAxis: {type: "category", data: errs.map(e => e.key)},
  series: [{type: "bar", data: errs.map(e => e.count), itemStyle: {color: "#dc2626"}}]});
const tbl = document.getElementById("tbl");
const rows = (D.summaries || []).filter(s => s.dataset);
tbl.innerHTML = rows.length ? `<table><tr><th>档位</th><th>指标</th><th>值</th><th>阈值</th><th>结果</th></tr>` +
  rows.map(s => `<tr><td>${s.dataset}</td><td>${s.metric}</td><td>${s.value}</td>` +
    `<td>${s.threshold ?? "—"}</td><td>${s.passed === null || s.passed === undefined ? "-" :
    (s.passed ? "PASS" : "FAIL")}</td></tr>`).join("") + "</table>"
  : '<span style="color:#8a919f">无档级汇总</span>';
</script></body></html>
"""


def render(data: dict) -> str:
    payload = json.dumps(data, ensure_ascii=False, default=str)
    run_name = data.get("run_name", "-")
    return _HTML.replace("__RUN_NAME__", run_name).replace("__DATA__", payload)


def main() -> None:
    ap = argparse.ArgumentParser(description="压测大屏预览（F1 同源 ECharts）")
    ap.add_argument("--data", help="timeseries.json 路径（直接指定）")
    ap.add_argument("--run-id", help="从 aite 库读取指定 run（summaries 全量）")
    ap.add_argument("--demo", action="store_true", help="合成演示数据（四档阶梯+故障窗口）")
    ap.add_argument("--list", action="store_true", help="仅列出可用数据源")
    ap.add_argument("--pick", type=int, help="选择清单序号（1=最新，配合无 --data 使用）")
    ap.add_argument("--db", default="sqlite+aiosqlite:///./aite.db", help="storage url")
    ap.add_argument("--out", help="输出 HTML 路径（缺省 perf_dashboard_<run名>.html）")
    args = ap.parse_args()

    if args.demo:
        data = make_demo_data()
        out = args.out or "perf_dashboard_demo.html"
    elif args.run_id:
        data = load_from_db(args.run_id, args.db)
        out = args.out or f"perf_dashboard_{data.get('run_name', 'run')}.html"
    elif args.data:
        data = load_from_file(Path(args.data))
        out = args.out or f"perf_dashboard_{data.get('run_name', 'run')}.html"
    else:
        hits = find_all_timeseries()
        if not hits:
            raise SystemExit("未找到任何 timeseries.json——先跑一次压测，或用 --demo")
        print("可用的压测数据（最新在前）：")
        for i, p in enumerate(hits, 1):
            print(f"  [{i}] {describe(p)}")
        if args.list:
            return
        if args.pick:
            idx = args.pick
        else:
            raw = input(f"选择序号 [1-{len(hits)}，回车=1]: ").strip()
            idx = int(raw) if raw.isdigit() and 1 <= int(raw) <= len(hits) else 1
        path = hits[idx - 1]
        data = load_from_file(path)
        out = args.out or f"perf_dashboard_{data.get('run_name', 'run')}.html"

    out = Path(args.out or out)
    out.write_text(render(data), encoding="utf-8")
    print(f"已生成: {out}（浏览器打开查看）")


if __name__ == "__main__":
    main()
