/* aite 静态稿 · 精度域原地对接层（api-acc3.js）—— 仅 accuracy2.html 引用。
   模式与 functional2 的 api32.js 同源：serve.py 同源反代 /api → 真数据渲染；
   后端不可达自动回退假数据（MOCK 徽标），写操作失败 toast 显式报错。
   覆盖：任务列表/任务详情/脚本库（场景维度 · 无预置：场景新建/删除 + 脚本
   发起/编辑/删除；清单瘦身口径 = 数据集 + 条数，spec 详情懒加载）/创建抽屉
   （从脚本发起 + 自定义六卡 → suite raw 组装，保存为脚本须选场景）/
   数据集选择器（benchmarks 真目录）/模型下拉。契约 = docs/domain-backends.md §17。
   趋势对比与人工评测页本轮保持演示数据（后端聚合缺口 V1-PLAN §8.6）。
   参数映射契约 = web-ui/TOOL-SUITE-PARAMS.md（表单控件 → suite raw 键位）。 */
"use strict";
const AAPI_INITIAL_HASH = location.hash || "";   // applyRoute 重写前捕获

const AAPI = {
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
  patch(p, b) { return this.req("PATCH", p, b); },
  del(p) { return this.req("DELETE", p); },
};

function aapiBadge(live) {
  let el = document.getElementById("aapiBadge");
  if (!el) {
    el = document.createElement("div");
    el.id = "aapiBadge";
    el.style.cssText = "position:fixed;left:14px;bottom:14px;z-index:90;padding:6px 12px;" +
      "border-radius:9px;font-size:12px;font-weight:650;border:1px solid;backdrop-filter:blur(6px)";
    document.body.appendChild(el);
  }
  el.style.background = live ? "rgba(20,40,24,.85)" : "rgba(40,30,20,.85)";
  el.style.borderColor = live ? "rgba(74,222,128,.5)" : "rgba(251,191,36,.5)";
  el.style.color = live ? "#86efac" : "#fcd34d";
  el.textContent = live ? "LIVE · 精度域已对接真实后端" : "MOCK · 假数据（后端未启动，python serve.py + aite serve）";
}

function aapiToast(msg, bad = false) {
  const el = document.createElement("div");
  el.style.cssText = "position:fixed;top:16px;right:16px;z-index:99;max-width:440px;padding:10px 14px;" +
    "border-radius:10px;font-size:12.5px;border:1px solid;backdrop-filter:blur(6px);" +
    "background:" + (bad ? "rgba(60,20,20,.92)" : "rgba(20,32,48,.92)") + ";" +
    "border-color:" + (bad ? "rgba(251,113,133,.5)" : "rgba(96,165,250,.5)") + ";color:#e8eefc";
  el.textContent = msg;
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 4600);
}

function aapiShortTime(iso) { return (iso || "").slice(5, 16).replace("T", " "); }
const aapiFirstLine = t => (t || "").split("\n").map(s => s.trim()).find(Boolean) || "";

/* 任务层状态（succeeded=跑完，≠样本全过；样本分数看指标汇总） */
const ACC_ID8 = {};            // id 前 8 位 → 任务 id（行点击路由）
let ACC_TASK_IDS = [];
let ACC_SCRIPT_DOCS = {};      // id → 脚本详情（含 spec；清单瘦身不带，懒加载缓存）
let ACC_SCENARIOS = [];        // 场景清单（/accuracy/scenarios：筛选/分组/保存落点）
let ACC_MODEL_SOURCES = [];    // [{key, label, profile|target}]
let accDetailTimer = null;

/* ------------------------------------------------------------ 任务列表 */
function aapiRerenderRuns() {
  const bar = document.getElementById("runsFilter");
  renderRuns(bar?.querySelector(".chip[data-status].on")?.dataset.status || "all",
             bar?.querySelector("[data-owner]")?.value || "all",
             bar?.querySelector("[data-kw]")?.value?.trim().toLowerCase() || "");
}

function rebuildAccRuns(items) {
  ACC_ID8 && Object.keys(ACC_ID8).forEach(k => delete ACC_ID8[k]);
  ACC_TASK_IDS = items.map(t => t.id);
  RUNS.splice(0, RUNS.length, ...items.map(t => {
    ACC_ID8[t.id.slice(0, 8)] = t.id;
    const ver = t.spec?.version ? `版本 ${t.spec.version}` : "未标注版本";
    return [
      t.name || "(未命名)",
      `${t.id.slice(0, 8)} · ${ver}`,
      t.spec?.target?.profile || t.spec?.target?.model || "-",
      t.status,                       // 原始任务层状态（badge 直显；chips 已对齐口径）
      null,                           // 分数：异步兜底（/runs/{id} overall 行）
      t.result ? `${t.result.total_cases ?? "—"}${t.result.error_cases ? ` / ${t.result.error_cases} err` : ""}` : "—",
      aapiShortTime(t.started_at || t.created_at),
      "-",
    ];
  }));
  items.forEach((t, i) => {
    const runId = t.result?.run_id;
    if (!runId || !RUNS[i]) return;
    AAPI.get(`/runs/${runId}`).then(run => {
      const overall = (run.summaries || []).find(s => !s.dataset);
      const score = overall?.value ?? (run.overall_score ?? null);
      if (!RUNS[i]) return;
      RUNS[i][4] = score === null || score === undefined ? null : score * 100;
      aapiRerenderRuns();
    }).catch(() => { /* 静默：下一轮轮询重试 */ });
  });
  // 状态 chips 计数对齐真实数据（全部/已完成/运行中/失败/排队）
  const counts = { all: items.length };
  items.forEach(t => { counts[t.status] = (counts[t.status] || 0) + 1; });
  document.querySelectorAll("#runsFilter .chip[data-status]").forEach(ch => {
    const small = ch.querySelector("small");
    if (small) small.textContent = String(counts[ch.dataset.status] || 0);
  });
  aapiRerenderRuns();
}

async function wireAccTasks() {
  const data = await AAPI.get("/accuracy/tasks");
  setNavCount("runs", data.count);
  rebuildAccRuns(data.items);
}

function startAccPolling() {
  if (startAccPolling.timer) return;
  startAccPolling.timer = setInterval(async () => {
    if (!AAPI.live) return;
    const runsSec = document.querySelector('main.page-host [data-page="runs"]');
    if (!runsSec || runsSec.hidden) return;   // 详情页有自己的 5s 自刷新（showAccTask）
    try {
      const data = await AAPI.get("/accuracy/tasks");
      const sig = data.items.map(t => `${t.id}:${t.status}`).join("|");
      if (sig === startAccPolling.lastSig) return;
      startAccPolling.lastSig = sig;
      setNavCount("runs", data.count);
      rebuildAccRuns(data.items);
    } catch (e) { /* 静默：下一轮再试 */ }
  }, 5000);
  startAccPolling.timer.unref?.();
}

/* ------------------------------------------------------- 任务详情（live） */
function backToRuns() { showPage("runs"); }

function aapiNum(v, digits = 4) {
  return v === null || v === undefined ? "—" :
    (Math.abs(v) <= 1.0001 ? (v * 100).toFixed(1) + "%" : String(v));
}

async function showAccTask(taskId) {
  const seq = showAccTask.seq = (showAccTask.seq || 0) + 1;
  if (accDetailTimer) { clearTimeout(accDetailTimer); accDetailTimer = null; }
  const detail = document.querySelector('main.page-host [data-page="detail"]');
  sessionStorage.setItem("aite-acc-last-task", taskId);
  showPage("detail");
  detail.innerHTML = `<header class="topbar">
      <div><h1>加载中…</h1><div class="crumb">task ${taskId}</div></div>
      <div class="actions"><button class="btn" onclick="backToRuns()">← 任务列表</button></div>
    </header>
    <section class="card"><div class="ds-empty">正在加载任务详情…</div></section>`;
  let t;
  try {
    t = await AAPI.get(`/accuracy/tasks/${taskId}`);
  } catch (err) {
    if (seq !== showAccTask.seq) return;
    if (/^404/.test(err.message)) {
      sessionStorage.removeItem("aite-acc-last-task");
      aapiToast(`任务 ${taskId} 不存在（可能已被删除），已回到任务列表`, true);
      backToRuns();
      return;
    }
    detail.innerHTML = `<header class="topbar"><div><h1>${taskId}
      <span class="badge failed"><i></i>加载失败</span></h1>
      <div class="crumb">任务详情加载失败</div></div>
      <div class="actions"><button class="btn" onclick="backToRuns()">← 任务列表</button></div></header>
      <section class="card"><div class="ds-empty">加载失败：${err.message}（后端未启动或网络异常？）</div></section>`;
    return;
  }
  if (seq !== showAccTask.seq) return;
  let result = null;
  try { result = await AAPI.get(`/accuracy/tasks/${taskId}/result`); }
  catch (e) { /* 409 非终态 */ }
  const runId = t.result?.run_id || result?.result?.run_id || null;
  let run = null;
  if (runId) {
    try { run = await AAPI.get(`/runs/${runId}`); }   // DB 唯一权威
    catch (e) { /* run 行可能尚未可见 */ }
  }
  const summaries = run?.summaries || [];
  const overalls = summaries.filter(s => !s.dataset);
  const overall = overalls[0] || null;
  const spec = t.spec || {};
  const dsNames = spec.tools?.datasets
    || (spec.datasets || []).map(d => d.name || d.type || "?");
  const datasets = [].concat(dsNames || []).join(" · ") || "—";
  const failures = run?.details?.failures || [];
  const reports = run?.details?.reports || {};
  const running = t.status === "running" || t.status === "stopping" || t.status === "pending";
  const fmtDur = s => s == null ? "—" :
    s >= 3600 ? `${(s / 3600).toFixed(1)} 小时` : s >= 60 ? `${Math.round(s / 60)} 分钟` : `${Math.round(s)} 秒`;
  const dur = (t.started_at && t.finished_at)
    ? fmtDur((new Date(t.finished_at) - new Date(t.started_at)) / 1000) : "—";

  const failBlock = (f, i) => {
    const cid = f.case_id || f.case || f.id || f.sample_id || `#${i + 1}`;
    const why = f.reason || f.error || f.why || "";
    const extra = { ...f };
    delete extra.case_id; delete extra.case; delete extra.id; delete extra.sample_id;
    delete extra.reason; delete extra.error; delete extra.why;
    return `<details style="border:1px solid color-mix(in srgb,var(--bad) 30%,transparent);
        border-radius:10px;padding:8px 12px;margin-top:8px">
      <summary style="cursor:pointer;font-size:12.5px;color:var(--bad)" class="mono">✕ ${cid}</summary>
      ${why ? `<div class="kv" style="margin-top:8px"><span>原因</span>
        <pre style="white-space:pre-wrap;margin:0">${String(why)}</pre></div>` : ""}
      ${Object.keys(extra).length ? `<div class="kv"><span>明细</span>
        <pre style="white-space:pre-wrap;margin:0;font-size:12px">${JSON.stringify(extra, null, 2)}</pre></div>` : ""}
    </details>`;
  };

  detail.innerHTML = `
    <header class="topbar">
      <div><h1>${t.name || "(未命名)"} ${badge(t.status)}</h1>
      <div class="crumb">task ${t.id} · 引擎 ${spec.engine || run?.details?.tool || "自研"}
      · 数据集 ${datasets} · ${spec.version ? `版本 ${spec.version} · ` : ""}模型
      ${spec.target?.profile || spec.target?.model || "-"}
      · ${t.started_at ? aapiShortTime(t.started_at) : "未开始"}</div></div>
      <div class="actions">
        ${running ? `<button class="btn" onclick="stopAccTask('${taskId}')">停止</button>` : ""}
        <button class="btn" onclick="showAccTask('${taskId}')">刷新</button>
        <button class="btn" onclick="backToRuns()">← 任务列表</button></div>
    </header>
    <section class="stats" style="grid-template-columns:repeat(4,1fr);margin-bottom:14px">
      <div class="stat"><div class="label"><i></i>总分（overall）</div>
        <div class="value">${overall?.value != null ? (overall.value * 100).toFixed(1) + "%" : "—"}</div>
        ${overall?.passed != null ? `<span class="delta ${overall.passed ? "up" : "down"}">${overall.passed ? "▲ 达标" : "▼ 未达标"}${overall.threshold != null ? `（阈值 ${overall.threshold}）` : ""}</span>` : ""}
      </div>
      <div class="stat"><div class="label"><i style="background:var(--ac2)"></i>样本数</div>
        <div class="value">${run?.total_cases ?? t.result?.total_cases ?? "—"}</div></div>
      <div class="stat"><div class="label"><i style="background:var(--bad)"></i>执行错误</div>
        <div class="value">${run?.error_cases ?? t.result?.error_cases ?? 0}</div></div>
      <div class="stat"><div class="label"><i style="background:var(--warn)"></i>耗时</div>
        <div class="value" style="font-size:22px">${dur}</div></div>
    </section>
    ${t.error ? `<section class="card" style="margin-bottom:14px;border-color:color-mix(in srgb,var(--bad) 40%,transparent)">
      <div class="card-head"><div><h3 style="color:var(--bad)">执行错误</h3></div></div>
      <pre style="white-space:pre-wrap;font-size:12.5px;margin:0">${t.error}</pre></section>` : ""}
    <section class="card" style="margin-bottom:14px">
      <div class="card-head"><div><h3>指标汇总</h3>
        <div class="sub">metric × 数据集/分组 · overall 行 = 全 dataset 按 N 加权 · 数据源 run 指标摘要（DB 权威）</div></div></div>
      <table class="sumtable">
        <thead><tr><th>指标</th><th>数据集 / 分组</th><th>值</th><th>阈值</th><th>N</th><th>结果</th></tr></thead>
        <tbody>${summaries.length ? summaries.map(s => `<tr class="${s.dataset ? "" : "overall"}">
          <td class="mono">${s.metric}</td>
          <td class="${s.dataset ? "dim" : ""}">${s.dataset || "overall"}${s.group ? ` · ${s.group}` : ""}</td>
          <td class="num mono">${s.value ?? "—"}</td>
          <td class="num dim">${s.threshold ?? "—"}</td>
          <td class="num">${s.count ?? "—"}</td>
          <td>${s.passed === null || s.passed === undefined
            ? '<span class="faint">—</span>' : badge(s.passed ? "passed" : "failed")}</td></tr>`).join("")
          : '<tr><td colspan="6" class="ds-empty">暂无指标摘要（运行中或旧版本运行）</td></tr>'}</tbody>
      </table>
    </section>
    <div style="display:flex;flex-direction:column;gap:14px">
      <section class="card">
        <div class="card-head"><div><h3>失败样本 <span class="dim">(全部 ${failures.length} 条)</span></h3>
          <div class="sub">完整可追溯 · 详情落盘 failures.jsonl（work_dir）</div></div></div>
        <div class="detail-stack">${failures.length ? failures.map(failBlock).join("")
          : '<div class="ds-empty">无失败样本记录</div>'}</div>
      </section>
      <section class="card">
        <div class="card-head"><div><h3>报告产物</h3>
          <div class="sub">落盘 work_dir（产物下载端点为后续缺口 §8.7，路径可服务器直取）</div></div></div>
        <div class="detail-stack">
          ${Object.entries(reports).map(([k, v]) =>
            `<div class="kv"><span>${k}</span><div class="mono" style="font-size:12px;white-space:normal">${v}</div></div>`).join("")
          || '<div class="ds-empty">暂无报告产物</div>'}
          ${run?.details?.work_dir ? `<div class="kv"><span>work_dir</span>
            <div class="mono" style="font-size:12px;white-space:normal">${run.details.work_dir}</div></div>` : ""}
        </div>
      </section>
    </div>`;
  if (running && t.status === "running") {
    accDetailTimer = setTimeout(() => {
      if (seq === showAccTask.seq) showAccTask(taskId);
    }, 5000);
  }
}

async function stopAccTask(taskId) {
  if (!confirm("停止该任务？（running → stopping → cancelled，幂等）")) return;
  try {
    await AAPI.post(`/accuracy/tasks/${taskId}/stop`);
    aapiToast("停止指令已下发");
    showAccTask(taskId);
  } catch (err) { aapiToast(`停止失败：${err.message}`, true); }
}

/* ---------------------------------------------------------------- 脚本库 */
async function aapiScriptDoc(sid) {
  if (ACC_SCRIPT_DOCS[sid]) return ACC_SCRIPT_DOCS[sid];
  const doc = await AAPI.get(`/accuracy/scripts/${encodeURIComponent(sid)}`);
  ACC_SCRIPT_DOCS[sid] = doc;
  return doc;
}

function aapiSpecSnap(spec) {
  const tools = spec.tools || {};
  const rows = [
    ["数据集", (tools.datasets || (spec.datasets || []).map(d => d.id || d.ref || d.path || "?")).join(" · ") || "—"],
    ["目标", spec.target?.profile ? `profile: ${spec.target.profile}`
      : spec.target?.type ? `${spec.target.type}${spec.target.base_url ? ` · ${spec.target.base_url}` : ""}` : "—"],
  ];
  rows.push(["抽样", `${tools.limit_per_subset ? `每子集 ${tools.limit_per_subset}` : "全量"}`
    + (tools.cases ? ` · 定向 ${tools.cases.length} 例` : "")]);
  rows.push(["Judge", tools.judge?.profile ? `profile: ${tools.judge.profile}` : "不配置（规则判分）"]);
  const th = tools.metric_thresholds;
  rows.push(["阈值判定", th === "auto" ? "auto（内置推荐阈值）"
    : th && typeof th === "object" ? Object.entries(th).map(([k, v]) => `${k} ≥ ${v}`).join(" · ")
    : "不判定（仅记录分数）"]);
  const ea = tools.extra_args || {};
  const gc = ea.generation_config || {};
  const gcBits = Object.entries(gc).filter(([, v]) => v !== undefined && v !== "")
    .map(([k, v]) => `${k}=${v}`);
  rows.push(["压力", `并发 ${ea.eval_batch_size ?? "引擎缺省"} · timeout ${ea.timeout_s ?? 14400}s · resume ${tools.resume || "auto"}`]);
  rows.push(["扩展参数", gcBits.length ? `generation_config: ${gcBits.join(" · ")}` : "未配置"]);
  if (ea.dataset_args) rows.push(["dataset_args", JSON.stringify(ea.dataset_args)]);
  return rows;
}

/* 复用面板快照（accuracy2.html renderAccSnap 调用）：live = 详情懒加载全量快照 */
async function aapiScriptSnap(sid) {
  if (!AAPI.live) return accStaticSnap(sid);
  const doc = await aapiScriptDoc(sid);
  return [
    ["场景", doc.scenario_name || doc.scenario || "—"],
    ["条数", doc.case_count == null ? "—（evalscope 基准跑上游全量，本地不可知）" : String(doc.case_count)],
    ...(doc.description ? [["说明", doc.description]] : []),
    ...aapiSpecSnap(doc.spec || {}),
  ];
}

async function wireAccScripts() {
  const [data, scens] = await Promise.all([
    AAPI.get("/accuracy/scripts"), AAPI.get("/accuracy/scenarios")]);
  setNavCount("scripts", data.count);
  ACC_SCRIPT_DOCS = {};          // 清单刷新 = 详情缓存失效（编辑/删除后重取）
  ACC_SCENARIOS = scens.items;
  ACC_SCRIPTS.splice(0, ACC_SCRIPTS.length, ...data.items.map(d => ({
    id: d.id, name: d.name, scenario: d.scenario, scenario_name: d.scenario_name,
    datasets: d.datasets || [], case_count: d.case_count, upd: aapiShortTime(d.updated_at),
  })));
  accFillScenFilter(scens.items);
  aapiSyncScenDelBtn();
  renderAccScriptsTbl();
  const foot = document.querySelector('main.page-host [data-page="scripts"] .foot-note span');
  if (foot) foot.textContent = `真实后端 · ${scens.count} 个场景 / ${data.count} 个脚本 · 无预置（全部用户自建）`
    + " · 清单口径 = 数据集 + 条数（— = evalscope 基准跑上游全量，本地不可知）";
  // 复用面板下拉：按场景分组（空场景不出组）
  const sel = document.getElementById("accScriptSel");
  if (sel) {
    const prev = sel.value;
    sel.innerHTML = scens.items.map(c => {
      const items = data.items.filter(d => d.scenario === c.id);
      return items.length ? `<optgroup label="${c.display_name}">${items.map(d =>
        `<option value="${d.id}">${d.name}（${d.id}）</option>`).join("")}</optgroup>` : "";
    }).join("") || '<option value="">暂无脚本（先在脚本库新建场景，再「保存为脚本」沉淀）</option>';
    if ([...sel.options].some(o => o.value === prev)) sel.value = prev;
    renderAccSnap();
  }
}

function aapiSyncScenDelBtn() {
  const btn = document.getElementById("accDelScenBtn");
  if (btn) btn.hidden = !ACC_SCEN_FILTER;
}

async function aapiEditScript(sid) {
  let doc;
  try { doc = await aapiScriptDoc(sid); }
  catch (err) { aapiToast(`加载脚本失败：${err.message}`, true); return; }
  const name = prompt(`脚本中文名称（id ${sid} 不可改）：`, doc.name);
  if (name === null) return;
  const description = prompt("一句话测试意图（仅详情展示，清单不带）：", doc.description || "");
  if (description === null) return;
  const patch = { name: name.trim() || sid, description: description.trim() };
  if (confirm("是否同时修改固化配置 spec？（确定 = 编辑 JSON；取消 = 只改名称与说明）")) {
    const raw = prompt("spec（suite raw JSON；保存经后端 resolve_suite 真实校验）：",
                       JSON.stringify(doc.spec));
    if (raw === null) return;
    try { patch.spec = JSON.parse(raw); }
    catch (e) { aapiToast(`spec JSON 不合法：${e.message}`, true); return; }
  }
  try { await AAPI.patch(`/accuracy/scripts/${sid}`, patch); }
  catch (err) { aapiToast(`保存失败：${err.message}`, true); return; }
  aapiToast(`已更新脚本 ${sid}（编辑即生效；历史任务持创建时快照）`);
  await wireAccScripts();
}

function bindAccScriptRows() {
  const tbl = document.getElementById("scriptsTbl");
  if (!tbl || tbl.dataset.accBound) return;
  tbl.dataset.accBound = "1";
  tbl.addEventListener("click", async e => {
    const btn = e.target.closest("button");
    const tr = btn?.closest("tr");
    if (!btn || !tr) return;
    const sid = tr.querySelector(".rid")?.textContent.trim();
    if (!sid) return;
    if (btn.textContent.includes("发起任务")) {
      const sel = document.getElementById("accScriptSel");
      if (sel && [...sel.options].some(o => o.value === sid)) {
        sel.value = sid;
        renderAccSnap();
      }
      document.querySelector('#newRunModal .chip[data-src="script"]')?.click();
    } else if (btn.textContent.includes("删除")) {
      if (!confirm(`删除脚本 ${sid}？（场景目录内脚本文件移除；历史任务持配置快照不受影响）`)) return;
      try { await AAPI.del(`/accuracy/scripts/${sid}`); }
      catch (err) { aapiToast(`删除失败：${err.message}`, true); return; }
      aapiToast(`已删除脚本 ${sid}`);
      await wireAccScripts();
    } else if (btn.textContent.includes("编辑")) {
      await aapiEditScript(sid);
    }
  });
}

/* 场景：新建（id 英文编号 / 中文名称 / 维度标签 / 说明）+ 删除（有脚本二次确认级联） */
function bindAccScenarioOps() {
  const filter = document.getElementById("accScenFilter");
  if (!filter || filter.dataset.accBound) return;
  filter.dataset.accBound = "1";
  filter.addEventListener("change", aapiSyncScenDelBtn);
  document.getElementById("accScenAll")?.addEventListener("click", aapiSyncScenDelBtn);
  document.getElementById("accNewScenBtn")?.addEventListener("click", async () => {
    const id = prompt("场景英文编号（小写字母开头，字母/数字/-/_；目录名/接口寻址，创建后不可改）：");
    if (!id) return;
    if (!ACC_ID_RE.test(id.trim())) {
      aapiToast(`场景编号不合法：${id}（须匹配 ${ACC_ID_RE.source}）`, true); return;
    }
    const name = prompt("场景中文名称（脚本库分组标题）：", id.trim());
    if (name === null) return;
    let tags = [];
    try { tags = (await AAPI.get("/functional/dim-tags")).items.map(t => t.name); }
    catch (e) { /* 标签库不可达：允许留空 */ }
    const dim = prompt(`维度标签（可留空；共享标签库可选：${tags.join(" / ") || "—"}）：`, "");
    if (dim === null) return;
    const desc = prompt("一句话说明该场景测什么（可留空）：", "");
    if (desc === null) return;
    try {
      await AAPI.post("/accuracy/scenarios", { id: id.trim(), display_name: name.trim(),
                                               dimension: dim.trim(), description: desc.trim() });
    } catch (err) { aapiToast(`创建失败：${err.message}`, true); return; }
    aapiToast(`场景 ${id.trim()} 已创建（目录即注册表）`);
    ACC_SCEN_FILTER = id.trim();
    await wireAccScripts();
  });
  document.getElementById("accDelScenBtn")?.addEventListener("click", async () => {
    const sid = ACC_SCEN_FILTER;
    if (!sid || !confirm(`删除场景 ${sid}？（历史任务持 spec 快照不受影响）`)) return;
    try { await AAPI.del(`/accuracy/scenarios/${sid}`); }
    catch (err) {
      // 409 = 场景下仍有脚本：二次确认后级联删除（force）
      if (!/仍有/.test(err.message) || !confirm(
          `${err.message}\n\n级联删除将连同场景内全部脚本一并移除，不可恢复。确定？`)) {
        aapiToast(`已取消删除 ${sid}`, true); return;
      }
      try { await AAPI.del(`/accuracy/scenarios/${sid}?force=true`); }
      catch (err2) { aapiToast(`删除失败：${err2.message}`, true); return; }
    }
    aapiToast(`已删除场景 ${sid}`);
    ACC_SCEN_FILTER = "";
    await wireAccScripts();
  });
}

/* ------------------------------------------------------ 数据集目录（live） */
const AAPI_DIM_RULES = [
  [/(knowledge|qa|mcq|exam|knowledge)/i, "知识问答"],
  [/(math|reason|gsm|aime|competition)/i, "数学推理"],
  [/(code|swe|humaneval|programming)/i, "代码"],
  [/rag/i, "RAG"],
  [/agent/i, "Agent"],
  [/(robust|noise|adv)/i, "鲁棒性"],
  [/(long|needle|context)/i, "长上下文"],
];

async function wireAccBenchmarks() {
  const data = await AAPI.get("/tools/benchmarks?engine=evalscope");
  DS_ITEMS.splice(0, DS_ITEMS.length, ...data.items.map(b => {
    const tags = String(b.tags || []);
    const dims = AAPI_DIM_RULES.filter(([re]) => re.test(tags)).map(([, d]) => d);
    const type = /agent/i.test(tags) ? "Agent" : /rag/i.test(tags) ? "RAG" : "LLM";
    return {
      n: b.name,
      m: `${b.pretty_name || b.name}${(b.subsets || []).length ? ` · ${b.subsets.length} 子集` : ""}`,
      own: "开源",
      loc: String(b.dataset_id || "").startsWith("hf:") ? "在线" : "离线",
      tags: [...new Set(dims)],
      type,
      dataset_id: b.dataset_id || "engine catalog",
      on: false,
    };
  }));
  dsRenderTokens();
  renderDs();
}

/* ------------------------------------------------------------- 模型下拉 */
async function wireAccModels() {
  const [common, db] = await Promise.all([
    AAPI.get("/common/models").catch(() => ({ items: [] })),
    AAPI.get("/models").catch(() => ({ items: [] })),
  ]);
  ACC_MODEL_SOURCES = [
    ...common.items.map(m => ({ key: `common:${m.name}`, profile: m.name,
      label: `${m.name}${m.model ? ` · ${m.model}` : ""}（预制）` })),
    ...db.items.map(m => ({ key: `db:${m.id}`,
      target: { type: "openai_compatible", base_url: m.base_url || "",
                model: m.model || "", ...(m.api_key_env ? { api_key_env: m.api_key_env } : {}) },
      label: `${m.name}${m.model ? ` · ${m.model}` : ""}（注册）` })),
  ];
  const opts = ACC_MODEL_SOURCES.map(s => `<option value="${s.key}">${s.label}</option>`).join("");
  const custom = '<option value="__custom__">自定义（手填端点）</option>';
  const modelSel = document.getElementById("accModelSel");
  if (modelSel) { modelSel.innerHTML = opts + custom; bindModelCustom("accModelSel", "accModelCustom"); }
  const sModelSel = document.getElementById("accScriptModelSel");
  if (sModelSel) {
    sModelSel.innerHTML = '<option value="">脚本内置配置（不覆盖）</option>' + opts + custom;
    bindModelCustom("accScriptModelSel", "accScriptModelCustom");
  }
  const judgeSel = document.getElementById("accJudgeSel");
  if (judgeSel) {
    judgeSel.innerHTML = '<option value="">不配置（默认 · 仅规则判分）</option>' +
      ACC_MODEL_SOURCES.filter(s => s.profile).map(s => `<option value="${s.profile}">${s.label}</option>`).join("");
  }
}

/* ------------------------------------------------- 全局版本管理（§10.6） */
let ACC_VERSIONS = [];   // [{model, version, description}]（公共 · 版本管理登记）

async function wireAccVersions() {
  const data = await AAPI.get("/versions");
  ACC_VERSIONS = data.items;
  renderAccVerDatalist();
}

function renderAccVerDatalist() {
  const opts = ACC_VERSIONS.map(v =>
    `<option value="${v.version}">${v.model}${v.description ? ` · ${v.description}` : ""}</option>`).join("");
  for (const id of ["accVerDl", "accVerDl2"]) {
    const dl = document.getElementById(id);
    if (dl) dl.innerHTML = opts;
  }
}

function accPickModelName(selectId) {
  const key = document.getElementById(selectId).value;
  if (key === "__custom__" || key === "") return null;   // 自定义端点/脚本内置 → 手填
  const src = ACC_MODEL_SOURCES.find(s => s.key === key);
  return src?.profile || null;
}

async function accRegisterVersion(selectId) {
  const model = accPickModelName(selectId)
    || prompt("模型名（被测版本挂在模型下，如 deepseek）：");
  if (!model || !model.trim()) return;
  const version = prompt(`为模型「${model.trim()}」登记版本号（如 v1.3.0）：`);
  if (!version || !version.trim()) return;
  try {
    await AAPI.post("/versions", { model: model.trim(), version: version.trim() });
    aapiToast(`版本已登记：${model.trim()} ${version.trim()}（「公共 · 模型管理」页可管理）`);
    await wireAccVersions();
  } catch (err) { aapiToast(`登记失败：${err.message}`, true); }
}

function aapiTargetOf(selectValue, paneId) {
  if (selectValue === "__custom__") {
    const pane = document.getElementById(paneId);
    const inputs = pane.querySelectorAll("input.in");
    const target = { type: "openai_compatible", base_url: inputs[0].value.trim(),
                     model: inputs[1].value.trim() };
    const keyEnv = inputs[2].value.trim();
    if (keyEnv) target.api_key_env = keyEnv;
    if (!target.base_url || !target.model) throw new Error("自定义端点需填写 模型访问 url 与 模型名称");
    return target;
  }
  const src = ACC_MODEL_SOURCES.find(s => s.key === selectValue);
  if (!src) throw new Error("目标模型已失效，请重新选择");
  return src.profile ? { profile: src.profile } : { ...src.target };
}

/* --------------------------------------------------- 创建抽屉提交（两态） */
const ACC_ID_RE = /^[a-z][a-z0-9_-]{1,63}$/;

function aapiCollectCustomSpec() {
  const $ = id => document.getElementById(id);
  const datasets = DS_ITEMS.filter(d => d.on).map(d => d.n);
  if (!datasets.length) throw new Error("请至少选择一个评测数据集");
  const target = aapiTargetOf($("accModelSel").value, "accModelCustom");
  const spec = {
    module: "accuracy",
    engine: "evalscope",
    target,
  };
  const name = $("accNameIn").value.trim();
  if (name) spec.run_name = name;
  const ver = $("accVerIn").value.trim();
  if (ver) spec.version = ver;
  const tools = { datasets };
  // 每子集抽样：chip 预设 or 自定义输入
  const limitChip = document.querySelector("#accLimitRow .chip.on")?.textContent.trim();
  const limitCustom = $("accLimitCustom").value.trim();
  if (limitCustom) {
    const n = Number(limitCustom);
    if (!Number.isInteger(n) || n < 1) throw new Error("每子集抽样条数须为正整数");
    tools.limit_per_subset = n;
  } else if (limitChip && limitChip !== "全量") {
    tools.limit_per_subset = Number(limitChip);
  }
  const seed = $("accSeedIn").value.trim();
  const extraSeed = seed !== "" ? (() => {
    const n = Number(seed);
    if (!Number.isInteger(n)) throw new Error("随机种子 seed 须为整数");
    return n;
  })() : undefined;
  const judge = $("accJudgeSel").value;
  if (judge) tools.judge = { profile: judge };
  const resume = $("accResumeSel").value;
  if (resume && resume !== "auto") tools.resume = resume;
  // 阈值判定
  const thMode = $("thMode").value;
  if (thMode === "auto（内置基准推荐阈值）") {
    tools.metric_thresholds = "auto";
  } else if (thMode === "自定义") {
    const th = {};
    [...$("thRows").children].forEach(row => {
      const [mIn, cmpSel, vIn] = row.querySelectorAll("input.in, select.in");
      const metric = mIn.value.trim(), val = Number(vIn.value);
      if (!metric) return;
      if (!metric || !vIn.value.trim()) throw new Error(`阈值行「${metric || "?"}」需填写指标名与阈值`);
      if (cmpSel.value === "≤") throw new Error(`指标 ${metric}：精度域阈值暂只支持 ≥（contract map 语义）`);
      if (Number.isNaN(val)) throw new Error(`指标 ${metric} 阈值须为数字`);
      th[metric] = val;
    });
    if (Object.keys(th).length) tools.metric_thresholds = th;
  }
  // 压力 + 扩展参数 → extra_args（抽样 seed 也在 extra_args，契约 §2）
  const extra = {};
  if (extraSeed !== undefined) extra.seed = extraSeed;
  const batch = $("accBatchIn").value.trim();
  if (batch) extra.eval_batch_size = Number(batch);
  const timeout = $("accTimeoutIn").value.trim();
  if (timeout) extra.timeout_s = Number(timeout);
  const gc = {};
  const num = (id, key) => {
    const v = $(id).value.trim();
    if (v !== "") gc[key] = Number(v);
  };
  num("accMaxTokIn", "max_tokens");
  num("accTempIn", "temperature");
  num("accTopPIn", "top_p");
  num("accTopKIn", "top_k");
  num("accGenSeedIn", "seed");
  const think = $("accThinkSel").value;
  if (think) gc.reasoning_effort = think;   // thinking 控件 → evalscope 原生键
  const advRaw = $("accAdvJson").value.trim();
  if (advRaw) {
    let adv;
    try { adv = JSON.parse(advRaw); }
    catch (e) { throw new Error(`高级 JSON 不合法：${e.message}`); }
    if (adv.generation_config) Object.assign(gc, adv.generation_config);
    if (adv.dataset_args) extra.dataset_args = adv.dataset_args;
    for (const [k, v] of Object.entries(adv)) {
      if (k !== "generation_config" && k !== "dataset_args") extra[k] = v;
    }
  }
  if (Object.keys(gc).length) extra.generation_config = gc;
  if (Object.keys(extra).length) tools.extra_args = extra;
  spec.tools = tools;
  return { spec, name };
}

function aapiExecModeOf(cardsId) {
  const on = document.querySelector(`#${cardsId} .radio-card.on`);
  return on?.dataset.exec || "now";
}

function bindAccSubmits() {
  const drawer = document.getElementById("newRunModal");
  if (!drawer || drawer.dataset.accBound) return;
  drawer.dataset.accBound = "1";
  const submit = drawer.querySelector(".drawer-foot .btn.primary");
  submit.addEventListener("click", async () => {
    const fromScript = !!drawer.querySelector('.chip[data-src="script"].on');
    try {
      let spec, name;
      if (fromScript) {
        const sid = document.getElementById("accScriptSel").value;
        if (!sid) throw new Error("请选择评测脚本");
        const doc = await aapiScriptDoc(sid);           // 清单瘦身无 spec → 详情懒加载
        spec = JSON.parse(JSON.stringify(doc.spec));   // spec 整体复用（深拷贝防改缓存）
        name = document.getElementById("accRunName2").value.trim();
        if (name) spec.run_name = name;
        else if (!spec.run_name) spec.run_name = doc.id;
        const ver = document.getElementById("accVerIn2").value.trim();
        if (ver) spec.version = ver;
        const modelKey = document.getElementById("accScriptModelSel").value;
        if (modelKey) spec.target = aapiTargetOf(modelKey, "accScriptModelCustom");
      } else {
        ({ spec, name } = aapiCollectCustomSpec());
      }
      const mode = aapiExecModeOf(fromScript ? "accExec2Cards" : "execCards");
      if (mode === "scheduled") {
        throw new Error("定时执行暂未实装（V1-PLAN §8.5 调度器缺口），请选「立即执行」或「仅创建」");
      }
      const task = await AAPI.post("/accuracy/tasks", { name: name || undefined, spec });
      if (mode === "now") {
        await AAPI.post(`/accuracy/tasks/${task.id}/run`);
        drawer.hidden = true;
        aapiToast(`任务已发起：${task.id.slice(0, 8)}（运行中，任务列表可查）`);
      } else {
        drawer.hidden = true;
        aapiToast(`任务已创建（仅创建）：${task.id.slice(0, 8)}，稍后在任务列表手动运行`);
      }
      await wireAccTasks();
      showPage("runs");
    } catch (err) { aapiToast(`发起失败：${err.message}`, true); }
  });
  // 保存为脚本（自定义面板沉淀）
  document.getElementById("accSaveScript")?.addEventListener("click", async () => {
    try {
      const { spec } = aapiCollectCustomSpec();
      if (!ACC_SCENARIOS.length) {
        aapiToast("暂无场景：请先到「脚本库」点「＋ 新建场景」，脚本须归属场景", true); return;
      }
      const scenHint = ACC_SCENARIOS.map(c => `${c.id}（${c.display_name}）`).join("、");
      const scen = prompt(`保存到哪个场景？填场景英文编号\n可选：${scenHint}`,
                          ACC_SCEN_FILTER || ACC_SCENARIOS[0].id);
      if (!scen) return;
      if (!ACC_SCENARIOS.some(c => c.id === scen.trim())) {
        aapiToast(`场景不存在：${scen}（可选：${scenHint}）`, true); return;
      }
      const id = prompt("脚本英文 id（小写字母开头，字母/数字/-/_；全平台唯一）：");
      if (!id) return;
      if (!ACC_ID_RE.test(id.trim())) { aapiToast(`脚本 id 不合法：${id}（须匹配 ${ACC_ID_RE.source}）`, true); return; }
      const sid = id.trim();
      const name = prompt("脚本中文名称：", spec.run_name || sid);
      if (name === null) return;
      await AAPI.post("/accuracy/scripts", { scenario_id: scen.trim(), id: sid,
                                             name: name.trim() || sid, description: "", spec });
      aapiToast(`已保存为脚本「${name.trim() || sid}」→ 场景 ${scen.trim()}（运行参数不沉淀），到「脚本库」查看`);
      await wireAccScripts();
    } catch (err) { aapiToast(`保存失败：${err.message}`, true); }
  });
}

/* --------------------------------------------------------- 行点击委托绑定 */
function bindAccDelegation() {
  const tbl = document.getElementById("runsTbl");
  // capture 阶段拦截：静态行 onclick="showPage('detail')" 不再执行，改开真实详情
  if (tbl && !tbl.dataset.accBound) {
    tbl.dataset.accBound = "1";
    tbl.addEventListener("click", e => {
      const tr = e.target.closest("tr.clickable");
      if (!tr) return;
      e.stopImmediatePropagation();
      const rid = tr.querySelector(".rid")?.textContent.slice(0, 8);
      const tid = ACC_ID8[rid];
      if (tid) showAccTask(tid).catch(err => aapiToast(`打开任务详情失败：${err.message}`, true));
    }, true);
  }
  // 动态重渲染的 [data-open-drawer] 按钮（脚本库行等）：文档级委托兜底
  if (!bindAccDelegation.drawerBound) {
    bindAccDelegation.drawerBound = true;
    document.addEventListener("click", e => {
      const btn = e.target.closest("[data-open-drawer]");
      if (!btn) return;
      const d = document.getElementById(btn.dataset.openDrawer);
      if (d && d.hidden) d.hidden = false;
    });
  }
}

/* ------------------------------------------------------------------ boot */
async function aapiBoot() {
  let live = false;
  try {
    await AAPI.get("/accuracy/scripts");
    live = true;
  } catch (e) { live = false; }
  AAPI.live = live;
  aapiBadge(live);
  if (!live) return;
  // 状态 chips 对齐任务层口径：passed chip → succeeded（v1 badge 语义 succeeded=已成功）
  document.querySelector('#runsFilter .chip[data-status="passed"]')
    ?.setAttribute("data-status", "succeeded");
  bindAccDelegation();
  bindAccScriptRows();
  bindAccScenarioOps();
  const steps = [
    ["任务列表", wireAccTasks], ["脚本库", wireAccScripts],
    ["数据集目录", wireAccBenchmarks], ["模型下拉", wireAccModels],
    ["版本登记", wireAccVersions],
  ];
  for (const [label, fn] of steps) {
    try {
      await fn();
    } catch (err) {
      console.error(`[aapi] ${label} 对接失败:`, err);
      aapiToast(`${label}对接失败（该板块保持演示数据）：${err.message}`, true);
    }
  }
  // 「＋ 新版本」按钮：就地登记到全局版本管理（公共 · 版本管理页可统一管理）
  document.getElementById("accVerNewBtn")
    ?.addEventListener("click", () => accRegisterVersion("accModelSel"));
  document.getElementById("accVerNewBtn2")
    ?.addEventListener("click", () => accRegisterVersion("accScriptModelSel"));
  bindAccSubmits();
  startAccPolling();
  // 刷新后停在详情页：仅当 hash 以 #detail 开头才恢复任务视图
  if (AAPI_INITIAL_HASH.startsWith("#detail")) {
    const fromHash = AAPI_INITIAL_HASH.startsWith("#detail/")
      ? AAPI_INITIAL_HASH.slice(8) : "";
    const lastTask = fromHash || sessionStorage.getItem("aite-acc-last-task");
    if (lastTask) await showAccTask(lastTask);
  }
  aapiToast("精度域已对接真实后端（任务/场景/脚本/数据集/模型真读写，脚本无预置）；趋势对比与人工评测暂为演示数据");
}

// 启动收尾（含 MOCK 提前返回）在 <html data-api-ready> 落标记：自动化验证据此等待监听绑定完成
document.addEventListener("DOMContentLoaded", () => setTimeout(() => aapiBoot().finally(() => {
  document.documentElement.dataset.apiReady = AAPI.live ? "live" : "mock";
}), 80));
