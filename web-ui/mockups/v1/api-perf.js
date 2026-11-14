/* aite 静态稿 · 性能域原地对接层（api-perf.js）—— 仅 perf2.html 引用。
   模式与 accuracy2 的 api-acc3.js 同源：serve.py 同源反代 /api → 真数据渲染；
   后端不可达自动回退假数据（MOCK 徽标），写操作失败 toast 显式报错。
   覆盖：任务列表（GET /perf/tasks + 停止/重试）/任务详情（task+result+run
   summaries，运行中 5s 轮询，终态拉 /runs/{id}/timeseries 出 TTFT/RPS 图）/
   压测大屏（timeseries 全量：统计表档级 chips + 八图 + 错误条形）/
   性能数据集（GET/POST /perf/datasets + validate 干跑）/新建抽屉（自定义编排
   → suite raw 组装 POST /perf/tasks + run）。
   不对接（后端缺口，保持演示数据）：脚本库（无 /perf/scripts 端点）、
   数据集删除（无 DELETE 端点）、运行中逐秒进度（两压测引擎为子进程整跑，
   collect 才落盘——详情页运行中只轮询状态与耗时）。
   spec 键位契约 = aite/tools/perf_common.py / perf_adapter.py（perf 段）+
   parse_tool_suite（target/tools.metric_thresholds）；参数映射见本文件
   papiCollectSpec 注释。 */
"use strict";
const PAPI_INITIAL_HASH = location.hash || "";   // applyRoute 重写前捕获

const PAPI = {
  base: "/api/v1",
  live: false,
  async req(method, path, body) {
    const resp = await fetch(this.base + path, {
      method,
      headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) {
      const detail = data.detail !== undefined ? JSON.stringify(data.detail) : resp.statusText;
      throw new Error(`${resp.status} ${detail}`);
    }
    return data;
  },
  get(p) { return this.req("GET", p); },
  post(p, b) { return this.req("POST", p, b ?? {}); },
};

function papiBadge(live) {
  let el = document.getElementById("papiBadge");
  if (!el) {
    el = document.createElement("div");
    el.id = "papiBadge";
    el.style.cssText = "position:fixed;left:14px;bottom:14px;z-index:90;padding:6px 12px;" +
      "border-radius:9px;font-size:12px;font-weight:650;border:1px solid;backdrop-filter:blur(6px)";
    document.body.appendChild(el);
  }
  el.style.background = live ? "rgba(20,40,24,.85)" : "rgba(40,30,20,.85)";
  el.style.borderColor = live ? "rgba(74,222,128,.5)" : "rgba(251,191,36,.5)";
  el.style.color = live ? "#86efac" : "#fcd34d";
  el.textContent = live ? "LIVE · 性能域已对接真实后端" : "MOCK · 假数据（后端未启动，python serve.py + aite serve）";
}

function papiToast(msg, bad = false) {
  const el = document.createElement("div");
  el.style.cssText = "position:fixed;top:16px;right:16px;z-index:99;max-width:440px;padding:10px 14px;" +
    "border-radius:10px;font-size:12.5px;border:1px solid;backdrop-filter:blur(6px);" +
    "background:" + (bad ? "rgba(60,20,20,.92)" : "rgba(20,32,48,.92)") + ";" +
    "border-color:" + (bad ? "rgba(251,113,133,.5)" : "rgba(96,165,250,.5)") + ";color:#e8eefc";
  el.textContent = msg;
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 4600);
}

function papiEsc(s) { return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;"); }
function papiShortTime(iso) { return (iso || "").slice(5, 16).replace("T", " "); }
function papiFmtDur(s) {
  return s == null ? "—" : s >= 3600 ? `${(s / 3600).toFixed(1)} 小时`
    : s >= 60 ? `${Math.round(s / 60)} 分钟` : `${Math.round(s)} 秒`;
}
function papiNum(v) {
  if (v === null || v === undefined) return "—";
  if (typeof v !== "number") return String(v);
  const a = Math.abs(v);
  return a >= 1000 ? v.toFixed(0) : a >= 100 ? v.toFixed(1) : v.toFixed(2);
}

/* ---------------------------------------------------------- 任务列表（live） */
const PERF_ID8 = {};            // id 前 8 位 → 任务 id（行 onclick 路由）
let PERF_ITEMS = [];            // /perf/tasks items（papiRerenderTasks 渲染源）
let PERF_LAST_TASK = null;      // 大屏「← 任务详情」回跳目标
let PERF_DATASETS = [];         // /perf/datasets items（负载下拉 + 数据集页）
let PERF_MODEL_SOURCES = [];    // [{key, label, profile|target}]
let PERF_DETAIL_TIMER = null;
const PERF_TS = {};             // 当前大屏/详情 timeseries（图表 builder 全局读）

function papiModeLabel(spec) {
  const p = spec?.perf || {};
  const mode = p.mode || (p.rate != null ? "QPS 开环" : "直配");
  const grad = Array.isArray(p.parallel) ? `u${p.parallel.join("→u")}`
    : p.parallel != null ? `u${p.parallel}`
    : Array.isArray(p.rate) ? `q${p.rate.join("→q")}` : p.rate != null ? `q${p.rate}` : "";
  return grad ? `${mode} ${grad}` : mode;
}

function papiTargetLabel(spec) {
  return spec?.target?.profile || spec?.target?.model || "-";
}

function papiRowDuration(t) {
  if (t.started_at && t.finished_at)
    return papiFmtDur((new Date(t.finished_at) - new Date(t.started_at)) / 1000);
  if (t.status === "running" && t.started_at)
    return papiFmtDur((Date.now() - new Date(t.started_at)) / 1000);
  return "—";
}

/* 任务层 succeeded = 子进程跑完 ≠ 执行成功：run 级 error（工具缺失/端点拒绝）
   上浮为 failed 徽标（口径同精度域「succeeded=跑完」） */
function papiEffStatus(t) {
  if (t.status === "succeeded" && t.result?.status && t.result.status !== "succeeded")
    return t.result.status === "error" ? "failed" : t.result.status;
  return t.status;
}

/* live 行渲染：布局同静态 renderTasks，但行/按钮带真实 task id（内联 onclick），
   不复用静态版的 onclick="showPage('detail')"。筛选状态直接读 DOM（chips 双绑）。 */
function papiRerenderTasks() {
  const bar = document.getElementById("taskFilter");
  const status = bar?.querySelector(".chip[data-status].on")?.dataset.status || "all";
  const owner = bar?.querySelector("[data-owner]")?.value || "all";
  const kw = bar?.querySelector("[data-kw]")?.value?.trim().toLowerCase() || "";
  const hits = PERF_ITEMS.filter(t => (status === "all" || papiEffStatus(t) === status)
    && (owner === "all" || owner === "-")
    && (!kw || `${t.name} ${t.spec?.engine || ""} ${papiModeLabel(t.spec)}`.toLowerCase().includes(kw)));
  const tbl = document.getElementById("taskTbl");
  if (!tbl) return;
  tbl.innerHTML = `
  <thead><tr><th>任务</th><th>引擎</th><th>模式</th><th>状态</th><th>时长</th><th></th></tr></thead>
  <tbody>${hits.length ? hits.map(t => {
    const id8 = t.id.slice(0, 8);
    PERF_ID8[id8] = t.id;
    const eff = papiEffStatus(t);
    return `<tr class="clickable" onclick="showPerfTask('${t.id}')">
    <td class="rname"><b>${papiEsc(t.name || "(未命名)")}</b><div class="rid">${id8} · ${papiEsc(papiTargetLabel(t.spec))} · ${papiShortTime(t.started_at || t.created_at)}</div></td>
    <td><span class="tag mono">${papiEsc(t.spec?.engine || "-")}</span></td>
    <td class="dim">${papiEsc(papiModeLabel(t.spec))}</td>
    <td>${badge(eff)}</td>
    <td class="num dim">${papiRowDuration(t)}</td>
    <td>${t.status === "running" || t.status === "stopping"
      ? `<button class="btn sm danger" onclick="event.stopPropagation();stopPerfTask('${t.id}')">停止</button>`
      : t.status === "pending"
        ? `<button class="btn sm primary" onclick="event.stopPropagation();runPerfTask('${t.id}')">运行</button>`
        : `<button class="btn sm" onclick="event.stopPropagation();retryPerfTask('${t.id}')">重试</button>`}</td></tr>`;
  }).join("")
    : '<tr><td colspan="6" class="ds-empty">当前筛选无匹配任务 —— 调整状态 / 执行人或关键词</td></tr>'}</tbody>`;
}

/* 筛选条二次绑定：静态 bindTaskFilter 的闭包持有旧 renderTasks 引用，覆盖不掉，
   给同一批控件补挂 papiRerenderTasks（chip 点击会双渲染，静默无感）。 */
function papiBindTaskFilter() {
  const bar = document.getElementById("taskFilter");
  if (!bar || bar.dataset.papiBound) return;
  bar.dataset.papiBound = "1";
  bar.querySelectorAll(".chip[data-status]").forEach(c => c.addEventListener("click", () => papiRerenderTasks()));
  bar.querySelector("[data-owner]")?.addEventListener("change", () => papiRerenderTasks());
  bar.querySelector("[data-kw]")?.addEventListener("input", () => papiRerenderTasks());
}

async function wirePerfTasks() {
  const data = await PAPI.get("/perf/tasks");
  PERF_ITEMS = data.items;
  setNavCount("tasks", data.count);
  papiRerenderTasks();
}

async function runPerfTask(taskId) {
  try {
    await PAPI.post(`/perf/tasks/${taskId}/run`);
    papiToast(`已发起执行：${taskId.slice(0, 8)}（运行中）`);
    await wirePerfTasks();
    showPerfTask(taskId);
  } catch (err) { papiToast(`运行失败：${err.message}`, true); }
}

async function stopPerfTask(taskId) {
  if (!confirm("停止该压测任务？（running → stopping → cancelled，幂等）")) return;
  try {
    await PAPI.post(`/perf/tasks/${taskId}/stop`);
    papiToast("停止指令已下发");
    await wirePerfTasks();
    if (PERF_LAST_TASK === taskId) showPerfTask(taskId);
  } catch (err) { papiToast(`停止失败：${err.message}`, true); }
}

async function retryPerfTask(taskId) {
  try {
    await PAPI.post(`/perf/tasks/${taskId}/retry`);
    papiToast(`重试已发起（追加执行历史轮次）：${taskId.slice(0, 8)}`);
    await wirePerfTasks();
    showPerfTask(taskId);
  } catch (err) { papiToast(`重试失败：${err.message}`, true); }
}

/* ------------------------------------------------------- 任务详情（live） */
function papiClearDetailTimer() {
  if (PERF_DETAIL_TIMER) { clearTimeout(PERF_DETAIL_TIMER); PERF_DETAIL_TIMER = null; }
}

function papiSummariesTable(summaries, emptyTip) {
  return `<table class="sumtable">
    <thead><tr><th>档级</th><th>指标</th><th>值</th><th>阈值</th><th>N</th><th>结果</th></tr></thead>
    <tbody>${summaries.length ? summaries.map(s => `<tr class="${s.dataset ? "" : "overall"}">
      <td class="mono">${s.dataset || "overall"}</td>
      <td class="mono dim">${s.metric}</td>
      <td class="num mono">${papiNum(s.value)}</td>
      <td class="num dim">${s.threshold ?? "—"}</td>
      <td class="num">${s.count ?? "—"}</td>
      <td>${s.passed === null || s.passed === undefined
        ? '<span class="faint">—</span>' : badge(s.passed ? "passed" : "failed")}</td></tr>`).join("")
      : `<tr><td colspan="6" class="ds-empty">${emptyTip}</td></tr>`}</tbody>
  </table>`;
}

async function showPerfTask(taskId) {
  const seq = showPerfTask.seq = (showPerfTask.seq || 0) + 1;
  papiClearDetailTimer();
  PERF_LAST_TASK = taskId;
  const detail = document.querySelector('main.page-host [data-page="detail"]');
  sessionStorage.setItem("aite-perf-last-task", taskId);
  showPage("detail");
  detail.innerHTML = `<header class="topbar">
      <div><h1>加载中…</h1><div class="crumb">task ${taskId}</div></div>
      <div class="actions"><button class="btn" onclick="showPage('tasks')">← 任务列表</button></div>
    </header>
    <section class="card"><div class="ds-empty">正在加载任务详情…</div></section>`;
  let t;
  try {
    t = await PAPI.get(`/perf/tasks/${taskId}`);
  } catch (err) {
    if (seq !== showPerfTask.seq) return;
    if (/^404/.test(err.message)) {
      sessionStorage.removeItem("aite-perf-last-task");
      papiToast(`任务 ${taskId} 不存在（可能已被删除），已回到任务列表`, true);
      showPage("tasks");
      return;
    }
    detail.innerHTML = `<header class="topbar"><div><h1>${taskId}
      <span class="badge failed"><i></i>加载失败</span></h1></div>
      <div class="actions"><button class="btn" onclick="showPage('tasks')">← 任务列表</button></div></header>
      <section class="card"><div class="ds-empty">加载失败：${papiEsc(err.message)}</div></section>`;
    return;
  }
  if (seq !== showPerfTask.seq) return;
  let result = null;
  try { result = await PAPI.get(`/perf/tasks/${taskId}/result`); }
  catch (e) { /* 409 非终态 */ }
  const runId = t.result?.run_id || result?.result?.run_id || null;
  let run = null, ts = null;
  if (runId) {
    try { run = await PAPI.get(`/runs/${runId}`); } catch (e) { /* run 行可能尚未可见 */ }
    try { ts = await PAPI.get(`/runs/${runId}/timeseries`); } catch (e) { /* 非压测/未落盘 */ }
  }
  if (seq !== showPerfTask.seq) return;
  const spec = t.spec || {};
  const p = spec.perf || {};
  const eff = papiEffStatus(t);
  const running = t.status === "running" || t.status === "stopping" || t.status === "pending";
  const runError = (t.error || (eff === "failed" ? run?.error : "")) || "";
  const summaries = run?.summaries || (ts?.summaries || []);
  const overall = summaries.find(s => !s.dataset) || null;
  const byMetric = k => summaries.find(s => !s.dataset && s.metric === k);
  const reports = run?.details?.reports || result?.result?.reports || {};

  detail.innerHTML = `
    <header class="topbar">
      <div><h1>${papiEsc(t.name || "(未命名)")} ${badge(eff)}</h1>
      <div class="crumb">task ${t.id.slice(0, 8)} · engine=${papiEsc(spec.engine || "-")}
      · ${papiEsc(papiModeLabel(spec))} · ${papiEsc(papiTargetLabel(spec))}
      · ${runId ? `run #${runId.slice(0, 8)}${run?.status && run.status !== "succeeded" ? ` · run ${run.status}` : ""} · ` : ""}${t.started_at ? papiShortTime(t.started_at) : "未开始"}</div></div>
      <div class="actions">
        ${runId ? `<button class="btn" onclick="showPerfDashboard('${runId}')">压测大屏 ↗</button>` : ""}
        ${running ? `<button class="btn danger" onclick="stopPerfTask('${taskId}')">停止</button>` : ""}
        <button class="btn" onclick="showPerfTask('${taskId}')">刷新</button>
        <button class="btn" onclick="showPage('tasks')">← 任务列表</button></div>
    </header>
    ${runError ? `<section class="card" style="margin-bottom:14px;border-color:color-mix(in srgb,var(--bad) 40%,transparent)">
      <div class="card-head"><div><h3 style="color:var(--bad)">执行错误</h3>
        <div class="sub">run 级失败（任务层 succeeded 仅表示子进程跑完）</div></div></div>
      <pre style="white-space:pre-wrap;font-size:12.5px;margin:0">${papiEsc(runError)}</pre></section>` : ""}
    <section class="card" style="margin-bottom:14px">
      <div class="card-head"><div><h3>${running ? "执行状态" : "结果概览"}</h3>
        <div class="sub">${running
          ? "压测为子进程整跑（逐秒数据 collect 时落盘）· 本卡 5s 轮询任务状态与耗时，终态自动出图"
          : "overall 行 · 与 CLI 汇总同源（run 指标摘要）"}</div></div>
        <span class="tag mono">${papiEsc(spec.engine || "-")} · ${papiEsc(papiModeLabel(spec))}</span></div>
      ${running ? `<div class="mini-stats">
        <div class="mstat"><b id="perfElapsed">${papiRowDuration(t)}</b><span>已运行</span></div>
        <div class="mstat"><b>${papiEsc(p.duration != null ? `${p.duration}s 预算`
          : p.level_duration != null ? `${p.level_duration}s/档` : "时长未设")}</b><span>时长口径</span></div>
        <div class="mstat ac"><b>${papiEsc(Object.keys(p.probe?.sla || {}).length
          ? Object.entries(p.probe.sla).map(([k, v]) => `${k}≤${v}`).join(" & ") : "无 SLA")}</b><span>probe 判据</span></div>
        <div class="mstat"><b>${papiEsc(Array.isArray(p.parallel) ? p.parallel.join("→") : p.parallel ?? (p.rate ?? "—"))}</b><span>并发 / QPS 梯度</span></div>
      </div>` : `<div class="mini-stats">
        <div class="mstat"><b>${papiNum(byMetric("qps")?.value ?? overall?.count)}</b><span>${byMetric("qps") ? "QPS" : "总请求数"}</span></div>
        <div class="mstat"><b>${papiNum(byMetric("ttft_p95_ms")?.value ?? byMetric("ttft_ms")?.value)}</b><span>TTFT p95/avg（ms）</span></div>
        <div class="mstat ${Number(byMetric("error_rate")?.value) > 0 ? "bad" : "ok"}"><b>${papiNum(byMetric("error_rate")?.value ?? 0)}</b><span>错误率</span></div>
        <div class="mstat"><b>${papiFmtDur(t.started_at && t.finished_at
          ? (new Date(t.finished_at) - new Date(t.started_at)) / 1000 : null)}</b><span>总耗时</span></div>
      </div>`}
    </section>
    ${ts?.combined?.length ? `<div class="grid2" style="margin-bottom:14px">
      <div class="card"><div class="card-head"><div><h3>TTFT（ms）</h3>
        <div class="sub">${ts.bucket_seconds}s 桶 · avg/p95 · 全场阶梯（完整档级对比进「压测大屏」）</div></div></div>
        <div class="chart" data-chart="papiCttft"></div></div>
      <div class="card"><div class="card-head"><div><h3>RPS（请求/秒）</h3></div></div>
        <div class="chart" data-chart="papiCrps"></div></div>
    </div>` : (running ? "" : `<section class="card" style="margin-bottom:14px"><div class="ds-empty">暂无时序产物（旧版运行或引擎未落 timeseries.json）</div></section>`)}
    <div class="grid2" style="grid-template-columns:1.15fr 1fr;align-items:start">
      <section class="card">
        <div class="card-head"><div><h3>统计结果表</h3>
          <div class="sub">overall 与档级（perf:c&lt;并发&gt; / locust:u&lt;档&gt;）· 数据源 run 指标摘要（DB 权威）</div></div></div>
        ${papiSummariesTable(summaries, "暂无指标摘要（运行中或旧版本运行）")}
      </section>
      <section class="card">
        <div class="card-head"><div><h3>报告产物</h3>
          <div class="sub">落盘 work_dir（产物下载端点为后续缺口 §8.7，路径可服务器直取）</div></div></div>
        <div class="detail-stack">
          ${Object.entries(reports).map(([k, v]) =>
            `<div class="kv"><span>${papiEsc(k)}</span><div class="mono" style="font-size:12px;white-space:normal">${papiEsc(v)}</div></div>`).join("")
          || '<div class="ds-empty">暂无报告产物</div>'}
          ${run?.details?.work_dir ? `<div class="kv"><span>work_dir</span>
            <div class="mono" style="font-size:12px;white-space:normal">${papiEsc(run.details.work_dir)}</div></div>` : ""}
          ${run?.details?.work_dir ? `<div class="kv"><span>运行日志</span>
            <div class="mono" style="font-size:12px;white-space:normal">${papiEsc(run.details.work_dir)}/logs/${papiEsc(spec.engine === "locust" ? "locust" : "perf")}.log</div></div>` : ""}
        </div>
      </section>
    </div>`;
  if (ts) { Object.assign(PERF_TS, ts); autoCharts(); }
  if (running && t.status === "running") {
    PERF_DETAIL_TIMER = setTimeout(() => {
      if (seq === showPerfTask.seq) showPerfTask(taskId);
    }, 5000);
  }
}

/* ----------------------------------------------------------- 压测大屏（live） */
function papiLineOption(theme, key, color) {
  const pts = PERF_TS.combined || [];
  const c = theme.charts;
  const hasP95 = pts.some(p => p[`${key}_p95`] != null);
  const series = [{ name: "avg", type: "line", symbol: "none", lineStyle: { width: 2, color }, data: pts.map(p => p[key]) }];
  if (hasP95) series.push({ name: "p95", type: "line", symbol: "none", lineStyle: { width: 1.4, type: "dashed", color }, data: pts.map(p => p[`${key}_p95`]) });
  return {
    grid: { left: 6, right: 12, top: 20, bottom: 4, containLabel: true },
    legend: { top: 0, right: 6, textStyle: { color: c.axis, fontSize: 11 } },
    tooltip: { trigger: "axis", backgroundColor: c.tipBg, borderColor: c.tipBorder, textStyle: { color: c.tipTx, fontSize: 12 } },
    xAxis: { type: "category", data: pts.map(p => Math.round(p.t)), ...baseAxis(c), axisLabel: { color: c.axis, formatter: v => v + "s" } },
    yAxis: { type: "value", ...baseAxis(c), axisLabel: { color: c.axis } },
    series,
  };
}

/* 图表 builder（data-chart 寻址；PERF_TS 全局供数，autoCharts() 触发渲染） */
window.papiCttft = t => papiLineOption(t, "ttft", t.charts.pass);
window.papiClat = t => papiLineOption(t, "latency", t.charts.err);
window.papiCtpot = t => papiLineOption(t, "tpot", t.charts.radar[2]);
window.papiCitl = t => papiLineOption(t, "itl", t.charts.radar[1]);
window.papiCrps = t => papiLineOption(t, "rps", t.charts.pass);
window.papiCtok = t => papiLineOption(t, "tok_s", t.charts.radar[1]);
window.papiCusers = t => {
  const c = t.charts, pts = PERF_TS.combined || [];
  return {
    grid: { left: 6, right: 12, top: 12, bottom: 4, containLabel: true },
    tooltip: { trigger: "axis", backgroundColor: c.tipBg, borderColor: c.tipBorder, textStyle: { color: c.tipTx, fontSize: 12 } },
    xAxis: { type: "category", data: pts.map(p => Math.round(p.t)), ...baseAxis(c), splitLine: { show: false }, axisLabel: { color: c.axis, formatter: v => v + "s" } },
    yAxis: { type: "value", ...baseAxis(c), axisLabel: { color: c.axis } },
    series: [{ type: "line", step: "end", symbol: "none", lineStyle: { width: 2.2, color: c.ac2 || c.err },
      areaStyle: { color: vGrad(c.pass, 0.10) }, data: pts.map(p => p.users) }] };
};
window.papiCerr = t => {
  const c = t.charts, errs = PERF_TS.errors || [];
  return {
    grid: { left: 6, right: 48, top: 16, bottom: 4, containLabel: true },
    tooltip: { backgroundColor: c.tipBg, borderColor: c.tipBorder, textStyle: { color: c.tipTx, fontSize: 12 } },
    xAxis: { type: "value", ...baseAxis(c), splitLine: { lineStyle: { color: c.split } }, axisLabel: { color: c.axis } },
    yAxis: { type: "category", data: errs.map(e => e.key), ...baseAxis(c), axisLabel: { color: c.axis }, splitLine: { show: false } },
    series: [{ type: "bar", barWidth: 16, itemStyle: { color: c.err, borderRadius: 4 }, data: errs.map(e => e.count),
      label: { show: true, position: "right", color: c.axis, fontSize: 11 } }] };
};

function papiDashChips(labels) {
  return `<span class="chip on" data-dash="all">overall</span>` +
    labels.map(l => `<span class="chip" data-dash="${papiEsc(l)}">${papiEsc(l)}</span>`).join("");
}

async function showPerfDashboard(runId) {
  const sec = document.querySelector('main.page-host [data-page="dashboard"]');
  showPage("dashboard");
  sec.innerHTML = `<header class="topbar"><div><h1>压测大屏</h1><div class="crumb">run ${runId}</div></div>
    <div class="actions"><button class="btn" onclick="showPage('tasks')">← 任务列表</button></div></header>
    <section class="card"><div class="ds-empty">加载时序数据…</div></section>`;
  let run = null, ts;
  try {
    ts = await PAPI.get(`/runs/${runId}/timeseries`);
    try { run = await PAPI.get(`/runs/${runId}`); } catch (e) { /* run 行缺摘要不致命 */ }
  } catch (err) {
    sec.innerHTML = `<header class="topbar"><div><h1>压测大屏</h1>
      <div class="crumb">run ${runId} · 无时序产物</div></div>
      <div class="actions"><button class="btn" onclick="${PERF_LAST_TASK ? `showPerfTask('${PERF_LAST_TASK}')` : "showPage('tasks')"}">← 返回</button></div></header>
      <section class="card"><div class="ds-empty">该 run 无压测时序（timeseries.json 未落盘或非压测 run）：${papiEsc(err.message)}</div></section>`;
    return;
  }
  Object.keys(PERF_TS).forEach(k => delete PERF_TS[k]);
  Object.assign(PERF_TS, ts);
  const labels = [...new Set((ts.summaries || []).map(s => s.dataset).filter(Boolean))];
  const summaries = run?.summaries?.length ? run.summaries : (ts.summaries || []);
  const name = ts.run_name || run?.run_name || runId.slice(0, 8);
  sec.innerHTML = `
    <header class="topbar">
      <div><h1>${papiEsc(name)} <span class="badge ${ts.status === "succeeded" ? "passed" : ts.status === "error" ? "failed" : ts.status}" style="vertical-align:3px"><i></i>${ts.status || ""}</span></h1>
      <div class="crumb">run #${runId.slice(0, 8)} · engine=${papiEsc(ts.engine || "-")} · ${papiEsc(String(ts.bucket_seconds))}s 桶 · ${papiEsc((ts.combined || []).length)} 点</div></div>
      <div class="actions">
        ${PERF_LAST_TASK ? `<button class="btn" onclick="showPerfTask('${PERF_LAST_TASK}')">← 任务详情</button>` : ""}
        <button class="btn" onclick="showPage('tasks')">← 任务列表</button></div>
    </header>
    <section class="card" id="sumCard" style="margin-bottom:14px">
      <div class="card-head"><div><h3>统计结果表</h3>
        <div class="sub">overall 与档级 · 与 CLI 汇总同源（run 指标摘要 + timeseries.summaries）</div></div>
        <div class="chips">${papiDashChips(labels)}</div></div>
      <div id="sumTbl"></div>
    </section>
    <div class="grid2" style="margin-bottom:14px">
      <div class="card"><div class="card-head"><div><h3>TTFT（首 token 时延 ms）</h3></div></div><div class="chart" data-chart="papiCttft"></div></div>
      <div class="card"><div class="card-head"><div><h3>端到端延迟（ms）</h3></div></div><div class="chart" data-chart="papiClat"></div></div>
    </div>
    <div class="grid2" style="margin-bottom:14px">
      <div class="card"><div class="card-head"><div><h3>TPOT（每输出 token 时延 ms）</h3></div></div><div class="chart" data-chart="papiCtpot"></div></div>
      <div class="card"><div class="card-head"><div><h3>ITL（token 间隔 ms）</h3></div></div><div class="chart" data-chart="papiCitl"></div></div>
    </div>
    <div class="stats" style="grid-template-columns:repeat(3,1fr)">
      <div class="card"><div class="card-head"><div><h3>RPS（请求/秒）</h3></div></div><div class="chart" data-chart="papiCrps"></div></div>
      <div class="card"><div class="card-head"><div><h3>吞吐（tok/s）</h3></div></div><div class="chart" data-chart="papiCtok"></div></div>
      <div class="card"><div class="card-head"><div><h3>并发阶梯</h3></div></div><div class="chart" data-chart="papiCusers"></div></div>
    </div>
    ${(ts.errors || []).length ? `<section class="card" style="margin-top:14px">
      <div class="card-head"><div><h3>错误统计</h3><div class="sub">错误键 × 次数（evalscope 引擎仅计数，文本级分布见 locust）</div></div></div>
      <div class="chart" data-chart="papiCerr" style="height:200px"></div></section>` : ""}`;
  const sumTbl = document.getElementById("sumTbl");
  sumTbl.innerHTML = papiSummariesTable(summaries, "暂无指标摘要");
  sec.querySelectorAll(".chip[data-dash]").forEach(ch => ch.addEventListener("click", () => {
    sec.querySelectorAll(".chip[data-dash]").forEach(x => x.classList.remove("on"));
    ch.classList.add("on");
    const sel = ch.dataset.dash;
    const rows = summaries.filter(s => sel === "all" || !s.dataset || s.dataset === sel);
    sumTbl.innerHTML = papiSummariesTable(rows, "暂无指标摘要");
  }));
  autoCharts();
}

/* ------------------------------------------------------- 性能数据集（live） */
function papiSceneName(file) {
  return file.replace(/-\d{8}-\d{6}\.jsonl$/i, "");
}

function papiDsStatsText(st) {
  if (!st) return "—";
  const buckets = Object.entries(st.buckets || {}).map(([k, v]) => `${k}×${v}`).join("/");
  return `${st.count ?? "?"} 条 · 桶 ${buckets || "—"} · 共享 ${st.shared_ratio ?? 0}`
    + `${st.output_tokens ? ` · 输出 ${st.output_tokens}` : ""}`;
}

function papiRenderDsTable() {
  const tbl = document.getElementById("dsTbl");
  if (!tbl) return;
  tbl.innerHTML = `
    <thead><tr><th>场景</th><th>来源</th><th>版本</th><th>stats</th><th>产物</th><th>生成时间</th><th style="text-align:right">操作</th></tr></thead>
    <tbody>${PERF_DATASETS.length ? PERF_DATASETS.map(d => {
      const scene = papiSceneName(d.file);
      return `<tr>
      <td class="rname"><b class="mono">${papiEsc(scene)}</b></td>
      <td><span class="tag">generated</span></td>
      <td class="mono dim">seed${d.stats?.seed ?? "?"}</td>
      <td class="dim" style="white-space:normal">${papiEsc(papiDsStatsText(d.stats))}</td>
      <td class="mono faint" style="font-size:11px">${papiEsc(d.path || d.file)}</td>
      <td class="dim">${papiShortTime(d.created_at)}</td>
      <td style="text-align:right"><button class="btn sm" onclick="papiCloneGen('${papiEsc(d.file)}')">克隆配置</button></td></tr>`;
    }).join("")
      : '<tr><td colspan="7" class="ds-empty">还没有生成物 —— 点右上角「生成场景数据集」</td></tr>'}</tbody>`;
  setNavCount("datasets", PERF_DATASETS.length);
}

function papiCloneGen(file) {
  const d = PERF_DATASETS.find(x => x.file === file);
  if (!d) return;
  const st = d.stats || {};
  const set = (id, v) => { const el = document.getElementById(id); if (el && v != null) el.value = String(v); };
  set("genName", papiSceneName(file));
  set("genCount", st.count);
  const buckets = Object.keys(st.buckets || {});
  set("genLengths", buckets.join(", "));
  const total = Object.values(st.buckets || {}).reduce((a, b) => a + b, 0) || 1;
  set("genWeights", buckets.map(b => (st.buckets[b] / total).toFixed(3)).join(", "));
  set("genSharing", st.shared_ratio);
  set("genOutput", st.output_tokens);
  set("genSeed", st.seed);
  const drawer = document.getElementById("genDrawer");
  if (drawer?.hidden) drawer.hidden = false;
}

function papiCollectGen() {
  const num = (id, what, { int = true, min = 1 } = {}) => {
    const raw = document.getElementById(id)?.value?.trim();
    if (raw === "" || raw == null) return undefined;
    const v = Number(raw);
    if (Number.isNaN(v) || (int && !Number.isInteger(v)) || v < min)
      throw new Error(`${what} 不合法：${raw}`);
    return v;
  };
  const list = (id, what, cast) => {
    const raw = document.getElementById(id)?.value?.trim();
    if (!raw) return undefined;
    const arr = raw.split(/[,，\s]+/).filter(Boolean).map(cast);
    if (!arr.length || arr.some(v => Number.isNaN(v))) throw new Error(`${what} 不合法：${raw}`);
    return arr;
  };
  const corpus = document.getElementById("genCorpus")?.value?.trim();
  if (!corpus) throw new Error("语料池必填（目录或 .txt/.md/.jsonl 文件）");
  const count = num("genCount", "count 须为 ≥1 整数");
  if (count === undefined) throw new Error("count 必填（生成条数）");
  const lengths = list("genLengths", "输入长度分桶", Number);
  if (!lengths) throw new Error("input_lengths 必填（如 80, 320, 640）");
  const body = {
    corpus, count, input_lengths: lengths,
    name: document.getElementById("genName")?.value?.trim() || undefined,
  };
  const weights = list("genWeights", "权重", Number);
  if (weights) body.input_weights = weights;
  const seed = num("genSeed", "seed 须为整数", { min: -1e9 });
  if (seed !== undefined) body.seed = seed;
  const sharing = num("genSharing", "prefix_sharing 须为 0~1", { int: false, min: 0 });
  if (sharing !== undefined) {
    if (sharing > 1) throw new Error("prefix_sharing 须为 0~1");
    body.prefix_sharing = sharing;
  }
  const groups = num("genGroups", "prefix_groups 须为 ≥1 整数");
  if (groups !== undefined) body.prefix_groups = groups;
  const output = num("genOutput", "output_tokens 须为 ≥1 整数");
  if (output !== undefined) body.output_tokens = output;
  return body;
}

function papiBindGenDrawer() {
  const drawer = document.getElementById("genDrawer");
  if (!drawer || drawer.dataset.papiBound) return;
  drawer.dataset.papiBound = "1";
  const box = () => document.getElementById("genStatsBox");
  drawer.querySelector("#genValidateBtn")?.addEventListener("click", async () => {
    try {
      const body = papiCollectGen();
      const d = await PAPI.post("/perf/datasets/validate", body);
      box().innerHTML = `<div class="kv"><span>校验通过</span><pre style="white-space:pre-wrap;margin:0">${
        papiEsc(JSON.stringify(d.spec, null, 2))}</pre></div>`;
    } catch (err) {
      box().innerHTML = `<div class="kv"><span>校验失败</span><pre style="white-space:pre-wrap;margin:0;color:var(--bad)">${papiEsc(err.message)}</pre></div>`;
    }
  });
  drawer.querySelector("#genSubmitBtn")?.addEventListener("click", async () => {
    try {
      const body = papiCollectGen();
      const d = await PAPI.post("/perf/datasets", body);
      box().innerHTML = `
        <div class="kv"><span>count</span><pre style="white-space:pre-wrap;margin:0">${d.count} 条 → ${papiEsc(d.file)}</pre></div>
        <div class="kv"><span>长度分桶</span><pre style="white-space:pre-wrap;margin:0">${papiEsc(JSON.stringify(d.stats?.buckets))}（共享 ${d.stats?.shared_ratio}）</pre></div>
        <div class="kv"><span>输出长度</span><pre style="white-space:pre-wrap;margin:0">${papiEsc(String(d.stats?.output_tokens ?? "缺省"))}</pre></div>`;
      papiToast(`数据集已生成：${d.file}`);
      await wirePerfDatasets();
    } catch (err) {
      box().innerHTML = `<div class="kv"><span>生成失败</span><pre style="white-space:pre-wrap;margin:0;color:var(--bad)">${papiEsc(err.message)}</pre></div>`;
    }
  });
}

async function wirePerfDatasets() {
  const data = await PAPI.get("/perf/datasets");
  PERF_DATASETS = data.items;
  papiRenderDsTable();
  papiFillLoadDatasets();
  await papiFillGenCorpus();
}

/* 生成抽屉语料池下拉：已登记数据集（catalog，含 HF 导入的开源语料如 ShareGPT）
   + 性能产物 → 选中回填 genCorpus 路径框（仍可手改）。catalog 拉不到（mock/离线）时仅列产物。 */
async function papiFillGenCorpus() {
  const sel = document.getElementById("genCorpusSel");
  if (!sel) return;
  const cnt = s => (s && s.count != null) ? `${s.count} 条` : "—";
  const seen = new Set();
  const opt = (label, path) => {
    if (!path || seen.has(path)) return "";
    seen.add(path);
    return `<option value="${papiEsc(path)}">${papiEsc(label)}</option>`;
  };
  let rows = [];
  try { rows = (await PAPI.get("/datasets")).items || []; } catch { /* mock 容错 */ }
  let html = '<option value="">— 选择已登记数据集 / 性能产物，或下方手填路径 —</option>';
  html += '<optgroup label="性能产物（生成 / 真实负载导入）">';
  html += PERF_DATASETS.map(d => opt(`产物 ${d.file}（${cnt(d.stats)}）`, d.path)).join("");
  html += rows.filter(r => r.type === "perf")
    .map(r => opt(`perf:${r.name}（${cnt(r.stats)}）`, r.path)).join("");
  html += "</optgroup>";
  html += '<optgroup label="开源 / 题库数据集（accuracy：对话数组可直接当语料池）">';
  html += rows.filter(r => r.type === "accuracy")
    .map(r => opt(`accuracy:${r.name}（${cnt(r.stats)}）`, r.path)).join("");
  html += "</optgroup>";
  sel.innerHTML = html;
  sel.onchange = () => {
    const input = document.getElementById("genCorpus");
    if (sel.value && input) input.value = sel.value;
  };
}

/* ------------------------------------------------------------- 模型下拉 */
async function wirePerfModels() {
  const [common, db] = await Promise.all([
    PAPI.get("/common/models").catch(() => ({ items: [] })),
    PAPI.get("/models").catch(() => ({ items: [] })),
  ]);
  PERF_MODEL_SOURCES = [
    ...common.items.map(m => ({ key: `common:${m.name}`, profile: m.name,
      label: `${m.name}${m.model ? ` · ${m.model}` : ""}（预制）` })),
    ...db.items.map(m => ({ key: `db:${m.id}`,
      target: { type: "openai_compatible", base_url: m.base_url || "",
                model: m.model || "", ...(m.api_key_env ? { api_key_env: m.api_key_env } : {}) },
      label: `${m.name}${m.model ? ` · ${m.model}` : ""}（注册）` })),
  ];
  const opts = PERF_MODEL_SOURCES.map(s => `<option value="${s.key}">${papiEsc(s.label)}</option>`).join("");
  const custom = '<option value="__custom__">自定义（手填端点）</option>';
  for (const id of ["perfModelSel", "perfScriptModelSel"]) {
    const sel = document.getElementById(id);
    if (sel) { const prev = sel.value; sel.innerHTML = opts + custom; if ([...sel.options].some(o => o.value === prev)) sel.value = prev; }
  }
}

function papiTargetOf(selectValue, paneId) {
  if (selectValue === "__custom__") {
    const inputs = document.querySelectorAll(`#${paneId} input.in`);
    const target = { type: "openai_compatible", base_url: inputs[0]?.value.trim() || "",
                     model: inputs[1]?.value.trim() || "" };
    const keyEnv = inputs[2]?.value.trim();
    if (keyEnv) target.api_key_env = keyEnv;
    if (!target.base_url || !target.model) throw new Error("自定义端点需填写 模型访问 url 与 模型名称");
    return target;
  }
  const src = PERF_MODEL_SOURCES.find(s => s.key === selectValue);
  if (!src) throw new Error("目标模型已失效，请重新选择");
  return src.profile ? { profile: src.profile } : { ...src.target };
}

/* --------------------------------------------------- 新建抽屉（自定义编排） */
/* spec 组装（键位契约 = perf_common.resolve_* + perf_adapter._assemble_args）：
   负载 = perf.dataset（平台数据集）| perf.synthetic{count,prompt_length}（固定
   输入输出的临时数据集，输出长度另配 max_tokens 定长）二选一；mode
   baseline{parallel}/ramp{from,to,step}/probe{sla,max_parallel,from,runs}/
   soak{parallel}；rate 开环与 mode/parallel 互斥；duration|level_duration
   二选一；locust 必配时长；引擎附加 warmup_num / spawn_rate / arrival / jitter。
   生成/采样参数显式填写才透传：temperature/top_p/top_k/min_tokens/seed/stop/
   frequency_penalty/repetition_penalty/query_template/apply_chat_template/
   tokenizer_path。max_tokens 优先级：任务显式 > 数据集 meta.output_tokens
   （平台数据集未配 max_tokens 时后端按其定长兜底）。tools.metric_thresholds 判定。 */
function papiFieldVal(labelRe) {
  const f = [...document.querySelectorAll("#perfParams > .field")]
    .find(el => labelRe.test(el.querySelector("label")?.textContent || ""));
  const inp = f?.querySelector("input.in, select.in");
  return inp ? inp.value.trim() : "";
}

function papiNumIn(raw, what, { int = true, min = 1, exclusive = false } = {}) {
  if (raw === "" || raw == null) return undefined;
  const v = Number(raw);
  const ok = !Number.isNaN(v) && (!int || Number.isInteger(v))
    && (exclusive ? v > min : v >= min);
  if (!ok)
    throw new Error(`${what} 不合法：${raw}（须为${int ? "整数" : "数字"}且${exclusive ? " > " : " ≥ "}${min}）`);
  return v;
}

function papiRenderLoadExtra() {
  const kind = document.querySelector('#newTaskDrawer .radio-cards.three .radio-card.on')?.dataset.load || "dataset";
  const box = document.getElementById("perfLoadExtra");
  if (!box) return;
  if (kind === "dataset") {
    box.innerHTML = `<div class="field"><label>数据集（perf 型 · 平台生成物）</label>
      <div style="display:flex;gap:8px">
        <select class="in" id="perfDatasetSel" style="flex:1;min-width:0"></select>
        <button class="btn sm" id="perfGenBtn" type="button">＋ 生成新数据集</button></div>
      <span class="hint">由「性能数据集」生成功能产出（长度分布/前缀共享可控）；max_tokens 留空时按数据集 meta 的 output_tokens 定长兜底</span></div>`;
    papiFillLoadDatasets();
    box.querySelector("#perfGenBtn")?.addEventListener("click", () => {
      document.getElementById("genDrawer").hidden = false;
    });
  } else {   // 固定输入输出：临时数据集（确定性生成，跑完即弃，不选平台数据集）
    box.innerHTML = `<div class="form-grid">
      <div class="field"><label>数据条数 count</label><input class="in" id="perfSynCount" placeholder="如 500"></div>
      <div class="field"><label>输入长度 prompt_length（字符）</label><input class="in" id="perfSynLen" placeholder="如 512"></div>
      <div class="field"><label>输出长度 output_tokens（定长）</label><input class="in" id="perfSynOut" placeholder="如 256"></div></div>
      <span class="hint">运行时按 参数自动生成临时数据集（确定性可复现）；输出长度经 max_tokens 定长透传（vLLM 配 ignore_eos 为真定长）；本模式不选数据集，「模型参数」区的 max_tokens 不生效</span>`;
  }
}

function papiFillLoadDatasets() {   // live 数据集清单就绪后刷新负载下拉（保留当前选择）
  const sel = document.getElementById("perfDatasetSel");
  if (!sel) return;
  const prev = sel.value;
  sel.innerHTML = PERF_DATASETS.map(d =>
    `<option value="${papiEsc(d.path)}">${papiEsc(papiSceneName(d.file))}（${d.stats?.count ?? "?"} 条）</option>`).join("")
    || '<option value="">暂无生成物 —— 先到「性能数据集」页生成</option>';
  if (prev && [...sel.options].some(o => o.value === prev)) sel.value = prev;
}

function papiCollectSpec() {
  const $ = id => document.getElementById(id);
  const engine = perfCur("data-e", "evalscope-perf");
  const mode = perfCur("data-m", "baseline");
  const perf = {};
  // 负载数据集二选一（prompt 键后端仍兼容，但表单不再提供）
  const kind = document.querySelector('#newTaskDrawer .radio-cards.three .radio-card.on')?.dataset.load || "dataset";
  if (kind === "dataset") {
    perf.dataset = $("perfDatasetSel")?.value || "";
    if (!perf.dataset) throw new Error("请选择平台数据集（或先点「＋ 生成新数据集」）");
    // max_tokens（定长或 [min,max]）：任务显式填写 > 数据集 meta 兜底（后端规则）
    const mtRaw = $("perfMaxTokIn")?.value.trim();
    if (mtRaw) {
      const m = mtRaw.match(/^\[\s*(\d+)\s*,\s*(\d+)\s*\]$/);
      perf.max_tokens = m ? [Number(m[1]), Number(m[2])] : papiNumIn(mtRaw, "max_tokens");
    }
  } else {
    // 固定输入输出：条数 + 输入长度 + 输出长度（定长 max_tokens），运行时生成临时数据集
    const count = papiNumIn($("perfSynCount")?.value.trim(), "数据条数 count");
    const len = papiNumIn($("perfSynLen")?.value.trim(), "输入长度 prompt_length");
    const out = papiNumIn($("perfSynOut")?.value.trim(), "输出长度 output_tokens");
    if (count === undefined || len === undefined || out === undefined)
      throw new Error("固定输入输出需要 数据条数 / 输入长度 / 输出长度 三项");
    perf.synthetic = { count, prompt_length: len };
    perf.max_tokens = out;   // 本模式输出长度即定长 max_tokens，优先于模型参数区
  }
  // 生成/采样参数（显式填写才透传；temperature 等无数据集侧来源，仅任务侧）
  const temperature = papiNumIn($("perfTempIn")?.value.trim(), "temperature", { int: false, min: 0 });
  if (temperature !== undefined) perf.temperature = temperature;
  const topP = papiNumIn($("perfTopPIn")?.value.trim(), "top_p", { int: false, min: 0, exclusive: true });
  if (topP !== undefined) perf.top_p = topP;
  const topK = papiNumIn($("perfTopKIn")?.value.trim(), "top_k");
  if (topK !== undefined) perf.top_k = topK;
  const seed = papiNumIn($("perfSeedIn")?.value.trim(), "seed", { min: 0 });
  if (seed !== undefined) perf.seed = seed;
  const minTok = papiNumIn($("perfMinTokIn")?.value.trim(), "min_tokens");
  if (minTok !== undefined) perf.min_tokens = minTok;
  const freq = papiNumIn($("perfFreqIn")?.value.trim(), "frequency_penalty", { int: false, min: -2 });
  if (freq !== undefined) perf.frequency_penalty = freq;
  const rep = papiNumIn($("perfRepIn")?.value.trim(), "repetition_penalty", { int: false, min: 0, exclusive: true });
  if (rep !== undefined) perf.repetition_penalty = rep;
  const tpl = $("perfTplIn")?.value.trim();
  if (tpl) perf.query_template = tpl;
  const chatTpl = $("perfChatTplIn")?.value;
  if (chatTpl) perf.apply_chat_template = chatTpl === "true";
  const stopRaw = $("perfStopIn")?.value.trim();
  if (stopRaw) perf.stop = stopRaw.split(/[,，]/).map(s => s.trim()).filter(Boolean);
  const tokPath = $("perfTokIn")?.value.trim();
  if (tokPath) perf.tokenizer_path = tokPath;
  // 压力参数（动态字段按 label 采集）
  let qpsRaw = "";
  if (mode === "baseline") {
    const parallel = papiNumIn(papiFieldVal(/^并发数/), "baseline.parallel");
    if (parallel === undefined) throw new Error("baseline 需要并发数 parallel");
    perf.baseline = { parallel };
    qpsRaw = papiFieldVal(/^QPS/);
    const dur = papiNumIn(papiFieldVal(/^duration 总时长/), "duration", { min: 1 });
    if (dur !== undefined) perf.duration = dur;
  } else if (mode === "ramp") {
    const raw = papiFieldVal(/^并发梯度/);
    const levels = (raw || "").split(/[,，\s]+/).filter(Boolean).map(Number);
    if (!levels.length || levels.some(v => !Number.isInteger(v) || v < 1))
      throw new Error(`并发梯度不合法：${raw}（须为 ≥1 整数逗号序列）`);
    if (levels.length > 1) {
      const step = levels[1] - levels[0];
      if (levels.some((v, i) => i > 0 && v - levels[i - 1] !== step) || step < 1)
        throw new Error("并发梯度须等差（ramp from/to/step 语义）；非等差请分多次任务或改直配");
      perf.ramp = { from: levels[0], to: levels[levels.length - 1], step };
    } else {
      throw new Error("并发梯度至少两档（单档请用 baseline）");
    }
    const per = papiNumIn(papiFieldVal(/^level_duration/), "level_duration", { min: 1 });
    if (per !== undefined) perf.level_duration = per;
    const dur = papiNumIn(papiFieldVal(/^duration 总时长/), "duration", { min: 1 });
    if (dur !== undefined) perf.duration = dur;
  } else if (mode === "probe") {
    const metric = papiFieldVal(/^SLA 指标/);
    const slaVal = papiNumIn(papiFieldVal(/^SLA 上限/), "SLA 上限", { int: false, min: 0, exclusive: true });
    if (!metric || slaVal === undefined) throw new Error("probe 需要 SLA 指标与上限值");
    const maxp = papiNumIn(papiFieldVal(/^并发上界/), "probe.max_parallel");
    if (maxp === undefined) throw new Error("probe 需要并发搜索上界 max_parallel");
    perf.probe = { max_parallel, sla: { [metric]: slaVal } };
    const from = papiNumIn(papiFieldVal(/^并发搜索起点|^SLA 起点/), "probe.from");
    if (from !== undefined) perf.probe.from = from;
    const runs = papiNumIn(papiFieldVal(/^每档重复/), "probe.runs");
    if (runs !== undefined) perf.probe.runs = runs;
    const dur = papiNumIn(papiFieldVal(/^总预算|^duration/), "duration", { min: 1 });
    if (dur !== undefined) perf.duration = dur;
  } else if (mode === "soak") {
    const parallel = papiNumIn(papiFieldVal(/^并发数/), "soak.parallel");
    if (parallel === undefined) throw new Error("soak 需要并发数 parallel");
    perf.soak = { parallel };
    const dur = papiNumIn(papiFieldVal(/^duration 总时长|^duration/), "duration", { min: 1 });
    if (dur === undefined) throw new Error("soak 必配 duration 总时长（长稳窗口）");
    perf.duration = dur;
  }
  // QPS 开环：与 mode/parallel 互斥（_resolve_rate 契约）——rate 模式即梯度载体，
  // duration 保留合法（开环 + 时长终止）
  if (qpsRaw) {
    const qps = papiNumIn(qpsRaw, "QPS rate", { int: false, min: 0, exclusive: true });
    if (qps !== undefined) {
      if (mode !== "baseline") throw new Error("QPS 开环仅 baseline 直配支持；爬坡请用多档 rate 数组（高级 JSON）");
      delete perf.baseline;
      perf.rate = qps;
    }
  }
  // 引擎附加参数
  if (engine === "evalscope-perf") {
    const warm = papiNumIn(papiFieldVal(/^warmup/), "warmup_num");
    if (warm !== undefined) perf.warmup_num = warm;
  } else {
    const spawn = papiNumIn(papiFieldVal(/^spawn_rate|^起压速率/), "spawn_rate", { int: false, min: 0, exclusive: true });
    if (spawn !== undefined) perf.spawn_rate = spawn;
    const arrival = papiFieldVal(/^arrival|^到达模型/);
    if (arrival && !/^无/.test(arrival)) perf.arrival = arrival.split(/\s/)[0];
    const jitter = papiNumIn(papiFieldVal(/^jitter|^到达抖动/), "jitter", { int: false, min: 0 });
    if (jitter !== undefined) perf.jitter = jitter;
  }
  const spec = { module: "perf", engine, perf };
  const target = papiTargetOf($("perfModelSel").value, "perfModelCustom");
  spec.target = target;
  const name = $("perfTaskName2")?.value.trim();
  if (name) spec.run_name = name;
  const thrRaw = $("perfThrIn")?.value.trim();
  if (thrRaw) {
    let thr;
    try { thr = JSON.parse(thrRaw); }
    catch (e) { throw new Error(`阈值 JSON 不合法：${e.message}`); }
    spec.tools = { metric_thresholds: thr };
  }
  return { spec, name };
}

function bindPerfSubmit() {
  const drawer = document.getElementById("newTaskDrawer");
  if (!drawer || drawer.dataset.papiBound) return;
  drawer.dataset.papiBound = "1";
  drawer.querySelector(".drawer-foot .btn.primary")?.addEventListener("click", async () => {
    const fromScript = !!drawer.querySelector('.chip[data-src="script"].on');
    try {
      if (fromScript) throw new Error("脚本库暂未落库（后端缺口）：请切「自定义编排」发起压测");
      const { spec, name } = papiCollectSpec();
      const task = await PAPI.post("/perf/tasks", { name: name || undefined, spec });
      await PAPI.post(`/perf/tasks/${task.id}/run`);
      drawer.hidden = true;
      papiToast(`压测已发起：${task.id.slice(0, 8)}（运行中，任务列表可查）`);
      await wirePerfTasks();
      showPage("tasks");
    } catch (err) { papiToast(`发起失败：${err.message}`, true); }
  });
  document.getElementById("perfSaveScript")?.addEventListener("click", () => {
    if (PAPI.live) papiToast("保存为脚本暂未落库（无 /perf/scripts 端点）：编排可用「克隆上一次任务 spec」方式暂存");
  });
}

/* ------------------------------------------------------------------ 轮询 */
function startPerfPolling() {
  if (startPerfPolling.timer) return;
  startPerfPolling.timer = setInterval(async () => {
    if (!PAPI.live) return;
    const detailSec = document.querySelector('main.page-host [data-page="detail"]');
    const detailOn = detailSec && !detailSec.hidden && PERF_LAST_TASK;
    const tasksSec = document.querySelector('main.page-host [data-page="tasks"]');
    try {
      if (detailOn) {
        const t = await PAPI.get(`/perf/tasks/${PERF_LAST_TASK}`);
        const elapsed = detailSec.querySelector("#perfElapsed");
        if (t.status === "running") {
          if (elapsed) elapsed.textContent = papiRowDuration(t);
          return;   // 详情页自轮询已覆盖运行态，列表刷新跳过
        }
        showPerfTask(PERF_LAST_TASK);   // 终态：自动出结果
        return;
      }
      if (!tasksSec || tasksSec.hidden) return;
      const data = await PAPI.get("/perf/tasks");
      const sig = data.items.map(t => `${t.id}:${t.status}`).join("|");
      if (sig === startPerfPolling.lastSig) return;
      startPerfPolling.lastSig = sig;
      PERF_ITEMS = data.items;
      setNavCount("tasks", data.count);
      papiRerenderTasks();
    } catch (e) { /* 静默：下一轮再试 */ }
  }, 5000);
  startPerfPolling.timer.unref?.();
}

/* ------------------------------------------------------------------ boot */
/* 负载来源卡切换 + 初次条件渲染：顶层无条件执行（mock 模式不接后端也要出表单；
   数据集下拉随后由 wirePerfDatasets → papiFillLoadDatasets 回填清单） */
document.querySelectorAll('#newTaskDrawer .radio-cards.three .radio-card[data-load]')
  .forEach(c => c.addEventListener("click", () => setTimeout(papiRenderLoadExtra, 0)));
papiRenderLoadExtra();

async function papiBoot() {
  let live = false;
  try {
    await PAPI.get("/perf/tasks");
    live = true;
  } catch (e) { live = false; }
  PAPI.live = live;
  papiBadge(live);
  document.documentElement.dataset.apiReady = live ? "live" : "mock";
  if (!live) return;
  // probe 字段对齐真实契约（SLA 指标 ∈ _SLA_METRICS；上界 = max_parallel 并发而非 QPS）
  PERF_MODE_PARAMS.probe = [
    { label: "SLA 指标", el: '<select class="in"><option>avg_ttft</option><option>p90_ttft</option><option>p99_ttft</option><option>avg_latency</option><option>p99_latency</option><option>avg_tpot</option><option>p90_tpot</option><option>p99_tpot</option><option>rps</option><option>tps</option></select>' },
    { label: "SLA 上限", el: '<input class="in" placeholder="指标上限值（如 avg_ttft ≤ 500ms）">' },
    { label: "并发搜索起点 from", el: '<input class="in" placeholder="缺省 1">' },
    { label: "并发上界 max_parallel", el: '<input class="in" placeholder="如 64（搜索终点）">' },
    { label: "每档重复 runs", el: '<input class="in" placeholder="缺省 1">' },
    { label: "总预算 duration（秒 · 看门狗截断）", el: '<input class="in" placeholder="可选，如 1800">' },
  ];
  papiBindTaskFilter();
  papiBindGenDrawer();
  bindPerfSubmit();
  const steps = [
    ["任务列表", wirePerfTasks], ["性能数据集", wirePerfDatasets],
    ["模型下拉", wirePerfModels],
  ];
  for (const [label, fn] of steps) {
    try {
      await fn();
    } catch (err) {
      console.error(`[papi] ${label} 对接失败:`, err);
      papiToast(`${label}对接失败（该板块保持演示数据）：${err.message}`, true);
    }
  }
  // 脚本库页保持演示数据：脚注显式声明（避免被当成真实清单）
  const foot = document.querySelector('main.page-host [data-page="scripts"] .foot-note span');
  if (foot) foot.textContent = "脚本库暂未落库（无 /perf/scripts 端点）· 当前为演示数据 · 压测请从「任务列表 → 新建」自定义编排发起";
  const ribbon = document.querySelector(".ribbon");
  if (ribbon) ribbon.firstChild.textContent = "V1 静态稿 · 性能压测 · LIVE 真实后端（脚本库除外）";
  startPerfPolling();
  // 刷新后停在详情页：仅当 hash 以 #detail 开头才恢复任务视图
  if (PAPI_INITIAL_HASH.startsWith("#detail")) {
    const fromHash = PAPI_INITIAL_HASH.startsWith("#detail/")
      ? PAPI_INITIAL_HASH.slice(8) : "";
    const lastTask = fromHash || sessionStorage.getItem("aite-perf-last-task");
    if (lastTask) await showPerfTask(lastTask);
  }
  papiToast("性能域已对接真实后端（任务/详情/大屏/数据集/模型真读写）；脚本库与数据集删除暂为演示/缺口");
}

// 启动收尾（含 MOCK 提前返回）在 <html data-api-ready> 落标记：自动化验据此等待监听绑定完成
document.addEventListener("DOMContentLoaded", () => setTimeout(() => papiBoot().catch(err => {
  console.error("[papi] boot failed:", err);
  document.documentElement.dataset.apiReady = "mock";
}), 80));
