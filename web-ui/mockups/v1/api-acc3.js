/* aite 静态稿 · 精度域原地对接层（api-acc3.js）—— 仅 accuracy2.html 引用。
   模式与 functional2 的 api32.js 同源：serve.py 同源反代 /api → 真数据渲染；
   后端不可达自动回退假数据（MOCK 徽标），写操作失败 toast 显式报错。
   覆盖：任务列表/任务详情/评估场景（独立导航页：场景卡片 + 新建/删除，查看脚本
   跳脚本库带筛选）/脚本库（场景筛选 · 无预置：脚本发起/编辑/删除；清单瘦身口径
   = 数据集 + 条数，spec 详情懒加载）/创建抽屉（从脚本发起 + 自定义六卡 → suite
   raw 组装，保存为脚本须选场景）/数据集选择器（benchmarks 真目录）/模型下拉。
   契约 = docs/domain-backends.md §17。
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

function aapiToast(msg, bad = false) { return liveToast(msg, bad); }   // 实现统一在 live2.js

function aapiShortTime(iso) { return shortTime(iso); }   // 实现统一在 live2.js
const aapiFirstLine = t => (t || "").split("\n").map(s => s.trim()).find(Boolean) || "";

/* 任务层状态（succeeded=跑完，≠样本全过；样本分数看指标汇总） */
const ACC_ID8 = {};            // id 前 8 位 → 任务 id（行点击路由）
let ACC_TASK_IDS = [];
let ACC_SCRIPT_DOCS = {};      // id → 脚本详情（含 spec；清单瘦身不带，懒加载缓存）
let ACC_SCENARIOS = [];        // 场景清单（/accuracy/scenarios：筛选/分组/保存落点）
let ACC_MODEL_SOURCES = [];    // [{key, label, profile|target}]
let ACC_RUBRICS = [];          // rubric 资产（/accuracy/rubrics：评测抽屉选用 + 校准抽屉数据源）
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
      t.spec?.target?.profile || t.spec?.target?.model
        || (t.spec?.task_type === "judge_calibration"
          ? (t.spec.judge?.profile || t.spec.judge?.model || "判官校准") : "-"),
      (t.status === "succeeded" && t.result?.status === "error")
        ? "failed" : t.status,        // 历史兼容：run 挂但任务 succeeded 的旧行按 failed 展示
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

function aapiNum(v) {
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
      <section class="card"><div class="ds-empty">加载失败：${escHtml(err.message)}（后端未启动或网络异常？）</div></section>`;
    return;
  }
  if (seq !== showAccTask.seq) return;
  if (t.spec?.task_type === "judge_calibration") {
    return accRenderCalDetail(t, taskId, seq);   // 校准任务无 run 行，渲染校准卡
  }
  let result = null;
  try { result = await AAPI.get(`/accuracy/tasks/${taskId}/result`); }
  catch (e) { /* 409 非终态 */ }
  // 取消/失败轮 result 为 null：run 引用兜底执行历史轮（同 api-perf 口径）
  const runId = t.result?.run_id || result?.result?.run_id
    || (t.history || []).filter(h => h.run_id).at(-1)?.run_id || null;
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
  // 错误文案两源：任务层 t.error（执行器抛错）+ run 行 error（历史任务 run 挂了但任务 succeeded）
  const runErr = (t.error || (run?.status === "error" ? run.error : "")) || "";
  // 历史兼容展示口径（与列表行一致）：run error 的旧行按 failed 展示，新任务后端已直落 failed
  const dispStatus = (t.status === "succeeded" && runErr) ? "failed" : t.status;
  const dur = (t.started_at && t.finished_at)
    ? fmtDur((new Date(t.finished_at) - new Date(t.started_at)) / 1000) : "—";   // fmtDur 用 live2.js 共享实现

  detail.innerHTML = `
    <header class="topbar">
      <div><h1>${escHtml(t.name || "(未命名)")} ${badge(t.status)}</h1>
      <div class="crumb">task ${t.id} · 引擎 ${escHtml(spec.engine || run?.details?.tool || "自研")}
      · 数据集 ${escHtml(datasets)} · ${spec.version ? `版本 ${escHtml(spec.version)} · ` : ""}模型
      ${escHtml(spec.target?.profile || spec.target?.model || "-")}
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
    ${runErr ? `<section class="card" style="margin-bottom:14px;border-color:color-mix(in srgb,var(--bad) 40%,transparent)">
      <div class="card-head"><div><h3 style="color:var(--bad)">执行错误</h3></div></div>
      <pre style="white-space:pre-wrap;font-size:12.5px;margin:0">${escHtml(runErr)}</pre></section>` : ""}
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
        <div class="detail-stack">${failures.length ? failures.map(accFailBlock).join("")
          : '<div class="ds-empty">无失败样本记录</div>'}</div>
      </section>
      ${runId ? `<section class="card">
        <div class="card-head"><div><h3>逐样本明细 <span class="dim" id="accSamplesMeta">加载中…</span></h3>
          <div class="sub">逐样本评分 vs 标准答案 · reviews/*.jsonl（work_dir 落盘，接口 /runs/{id}/samples）
            <label style="margin-left:10px;cursor:pointer;font-size:12px"><input type="checkbox" id="accSamplesWrong"
              onchange="accLoadSamples('${runId}', this.checked, ${seq})"> 只看答错</label></div></div></div>
        <div class="detail-stack" id="accSamplesBody"><div class="ds-empty">加载中…</div></div>
      </section>` : ""}
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
  if (runId) accLoadSamples(runId, false, seq);
  if (running && t.status === "running") {
    accDetailTimer = setTimeout(() => {
      if (seq === showAccTask.seq) showAccTask(taskId);
    }, 5000);
  }
}

/* 逐样本明细（详情卡懒加载）：数据源 /runs/{id}/samples（reviews/*.jsonl）。
   seq 防串页：详情快速切换时旧请求不得回填新页面。 */

/* 失败样本三段式分色块：输入 / 标准答案 / 模型输出各自色标+底色，一眼分清。
   颜色约定：输入=蓝(--run) · 标准答案=绿(--ok) · 模型输出=琥珀(--warn)。 */
const accTriBlock = (label, color, body) => {
  const text = String(body ?? "").trim();
  if (!text) return "";
  return `<div style="margin-top:8px;border-left:3px solid ${color};
      background:color-mix(in srgb,${color} 8%,transparent);
      border-radius:6px;padding:7px 12px">
    <div style="font-size:11px;font-weight:700;color:${color};letter-spacing:.5px">${label}</div>
    <pre style="white-space:pre-wrap;margin:3px 0 0;font-size:12px">${text}</pre>
  </div>`;
};

const accTriBlocks = (s) => {
  const input = s.question || s.input || s.prompt || s.user_input || "";
  const golds = [...new Set([s.target, s.correct_answer, s.expected, s.answer]
    .filter(v => v != null && String(v).trim() !== ""))];
  const outs = [s.extracted_prediction ? `提取答案：${s.extracted_prediction}` : "",
    s.prediction || s.output || s.response || s.generated || s.actual || ""].filter(Boolean);
  return accTriBlock("输 入", "var(--run)", input) +
    accTriBlock("标准答案", "var(--ok)", golds.join("\n")) +
    accTriBlock("模型输出", "var(--warn)", outs.join("\n"));
};

/* 失败样本卡：已知三段字段抽出分色展示，其余键保留 JSON 明细兜底。 */
const accFailBlock = (f, i) => {
  const cid = f.case_id || f.case || f.id || f.sample_id || `#${i + 1}`;
  const why = f.reason || f.error || f.why || f.detail || "";
  const extra = { ...f };
  delete extra.case_id; delete extra.case; delete extra.id; delete extra.sample_id;
  delete extra.reason; delete extra.error; delete extra.why; delete extra.detail;
  for (const k of ["question", "input", "prompt", "user_input", "target", "correct_answer",
    "expected", "answer", "prediction", "output", "response", "generated", "actual",
    "extracted_prediction"]) delete extra[k];
  return `<details style="border:1px solid color-mix(in srgb,var(--bad) 30%,transparent);
      border-radius:10px;padding:8px 12px;margin-top:8px">
    <summary style="cursor:pointer;font-size:12.5px;color:var(--bad)" class="mono">✕ ${cid}</summary>
    ${why ? `<div class="kv" style="margin-top:8px"><span>原因</span>
      <pre style="white-space:pre-wrap;margin:0">${String(why)}</pre></div>` : ""}
    ${accTriBlocks(f)}
    ${Object.keys(extra).length ? `<div class="kv"><span>明细</span>
      <pre style="white-space:pre-wrap;margin:0;font-size:12px">${JSON.stringify(extra, null, 2)}</pre></div>` : ""}
  </details>`;
};

const accSampleBlock = (s) => {
  const mark = s.passed === true ? '<span style="color:var(--good)">✓</span>'
    : s.passed === false ? '<span style="color:var(--bad)">✕</span>'
      : '<span class="faint">—</span>';
  const score = s.score == null ? "—"
    : (typeof s.score === "number" ? s.score.toFixed(2) : String(s.score));
  const q1 = (s.question || "").replace(/\s+/g, " ").slice(0, 90);
  return `<details style="border:1px solid var(--line);border-radius:10px;padding:8px 12px;margin-top:8px">
    <summary style="cursor:pointer;font-size:12.5px" class="mono">${mark} ${s.dataset || "—"}
      #${s.index ?? s.sample_id ?? "?"} · 得分 ${score}${s.extracted_prediction ? ` · 答 ${s.extracted_prediction}` : ""}${s.target ? ` · 标 ${s.target}` : ""}
      ${q1 ? `<span class="dim" style="font-weight:400"> · ${q1}</span>` : ""}</summary>
    ${accTriBlocks(s)}
    ${s.explanation ? `<div class="kv" style="margin-top:8px"><span>判定说明</span>
      <pre style="white-space:pre-wrap;margin:0;font-size:12px">${s.explanation}</pre></div>` : ""}
    ${s.judge_summary ? `<div class="kv"><span>评委</span>
      <pre style="white-space:pre-wrap;margin:0;font-size:12px">${JSON.stringify(s.judge_summary)}</pre></div>` : ""}
    ${s.status && s.status !== "success" ? `<div class="kv"><span>评分状态</span>
      <pre style="white-space:pre-wrap;margin:0;font-size:12px">${s.status}</pre></div>` : ""}
  </details>`;
};

async function accLoadSamples(runId, wrongOnly, seq) {
  const box = document.getElementById("accSamplesBody");
  const meta = document.getElementById("accSamplesMeta");
  if (!box || !runId) return;
  try {
    const d = await AAPI.get(`/runs/${runId}/samples?limit=200${wrongOnly ? "&wrong_only=1" : ""}`);
    if (seq !== undefined && seq !== showAccTask.seq) return;
    if (meta) meta.textContent = `（共 ${d.total} 条 · 答错 ${d.wrong} 条）`;
    box.innerHTML = d.items.length ? d.items.map(accSampleBlock).join("")
      : `<div class="ds-empty">${wrongOnly ? "没有答错样本（全部正确）" : "无逐样本记录"}</div>`;
  } catch (e) {
    if (seq !== undefined && seq !== showAccTask.seq) return;
    if (meta) meta.textContent = "";
    box.innerHTML = '<div class="ds-empty">无逐样本明细（自研引擎或旧版运行无 reviews 产物）</div>';
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
  rows.push(["Judge", tools.judge?.profile ? `profile: ${tools.judge.profile}`
    + (tools.judge.prompt_template ? " · 评分标准已自定义" : "") : "不配置（规则判分）"]);
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
  setNavCount("scenarios", scens.count);
  ACC_SCRIPT_DOCS = {};          // 清单刷新 = 详情缓存失效（编辑/删除后重取）
  ACC_SCENARIOS = scens.items;
  ACC_SCRIPTS.splice(0, ACC_SCRIPTS.length, ...data.items.map(d => ({
    id: d.id, name: d.name, scenario: d.scenario, scenario_name: d.scenario_name,
    datasets: d.datasets || [], case_count: d.case_count, upd: aapiShortTime(d.updated_at),
  })));
  accFillScenFilter(scens.items);
  renderAccScriptsTbl();
  renderAccScenGrid(scens.items);   // 评估场景页卡片（items 自带 scripts/cases 计数）
  const foot = document.querySelector('main.page-host [data-page="scripts"] .foot-note span');
  if (foot) foot.textContent = `真实后端 · ${scens.count} 个场景 / ${data.count} 个脚本 · 无预置（全部用户自建）`
    + " · 清单口径 = 数据集 + 条数（— = evalscope 基准跑上游全量，本地不可知）";
  // 复用面板下拉：按场景分组（空场景不出组）
  const sel = document.getElementById("accScriptSel");
  if (sel) {
    const prev = sel.value;
    sel.innerHTML = scens.items.map(c => {
      const items = data.items.filter(d => d.scenario === c.id);
      return items.length ? `<optgroup label="${escHtml(c.display_name)}">${items.map(d =>
        `<option value="${escHtml(d.id)}">${escHtml(d.name)}（${escHtml(d.id)}）</option>`).join("")}</optgroup>` : "";
    }).join("") || '<option value="">暂无脚本（先到「评估场景」页建场景，再沉淀脚本）</option>';
    if ([...sel.options].some(o => o.value === prev)) sel.value = prev;
    renderAccSnap();
  }
}

/* 场景删除（评估场景页卡片 data-acc-scen-del）：live = 后端删除（409 → 二次确认
   级联 force）；静态稿（AAPI.live=false）= 仅移除本地演示数据 */
async function aapiDeleteScenario(sid) {
  if (!AAPI.live) {
    if (!confirm(`删除场景 ${sid}？（静态演示：仅移除本地演示数据）`)) return;
    for (let i = ACC_SCRIPTS.length - 1; i >= 0; i--)
      if (ACC_SCRIPTS[i].scenario === sid) ACC_SCRIPTS.splice(i, 1);
    const k = ACC_SCENS.findIndex(c => c.id === sid);
    if (k >= 0) ACC_SCENS.splice(k, 1);
    accFillScenFilter(accScensStatic());
    renderAccScriptsTbl();
    renderAccScenGrid(accScensStatic());
    return;
  }
  if (!confirm(`删除场景 ${sid}？（历史任务持 spec 快照不受影响）`)) return;
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

/* 场景新建（「评估场景」页顶栏按钮；删除走场景卡 → aapiDeleteScenario） */
function bindAccScenarioOps() {
  const btn = document.getElementById("accNewScenBtn");
  if (!btn || btn.dataset.accBound) return;
  btn.dataset.accBound = "1";
  btn.addEventListener("click", async () => {
    const id = prompt("场景英文编号（小写字母开头，字母/数字/-/_；目录名/接口寻址，创建后不可改）：");
    if (!id) return;
    if (!ACC_ID_RE.test(id.trim())) {
      aapiToast(`场景编号不合法：${id}（须匹配 ${ACC_ID_RE.source}）`, true); return;
    }
    const name = prompt("场景中文名称（评估场景卡与脚本库分组标题）：", id.trim());
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
    aapiToast(`场景 ${id.trim()} 已创建（目录即注册表），卡片「查看脚本」可开始沉淀脚本`);
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
  let data;
  try {
    data = await AAPI.get("/tools/benchmarks?engine=evalscope");
  } catch (err) {
    // 失败要持久落在选择器上：静默留在 10 条静态演示行会被当成「全集只剩几条」
    const list = document.getElementById("dsList");
    if (list) list.innerHTML =
      `<div style="color:#c0392b;padding:8px 4px;font-size:12px">基准目录加载失败：${err.message}` +
      ` —— 下面是静态演示数据（10 条），不是开源全集；确认后端在跑后刷新本页重试</div>`;
    throw err;   // boot 循环照常弹 toast
  }
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
  const [common, db, secrets] = await Promise.all([
    AAPI.get("/common/models").catch(() => ({ items: [] })),
    AAPI.get("/models").catch(() => ({ items: [] })),
    AAPI.get("/secrets").catch(() => ({ items: [] })),
  ]);
  // 模型注册（POST /models）会把同名预制同步进 common.yaml，两来源各渲染一条重复项：
  // 同名只留预制一条（Judge 下拉依赖预制条目的 profile 引用，不能反向去重预制侧）
  const commonNames = new Set(common.items.map(m => m.name));
  ACC_MODEL_SOURCES = [
    ...common.items.map(m => ({ key: `common:${m.name}`, profile: m.name,
      label: `${m.name}${m.model ? ` · ${m.model}` : ""}（预制）` })),
    ...db.items.filter(m => !commonNames.has(m.name)).map(m => ({ key: `db:${m.id}`,
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
  const calJudgeSel = document.getElementById("accCalJudgeSel");
  if (calJudgeSel) {
    calJudgeSel.innerHTML = '<option value="">— 选择判官 —</option>'
      + '<option value="__custom__">自定义端点…</option>'
      + ACC_MODEL_SOURCES.filter(s => s.profile).map(s => `<option value="${s.profile}">${s.label}</option>`).join("");
  }
  // 自定义端点的鉴权密钥：选已归档（掩码）/ 仅环境变量手填
  document.querySelectorAll("#accModelCustom [data-keysel], #accScriptModelCustom [data-keysel]")
    .forEach(sel => wireKeySelect(sel, secrets.items));
}

/* ------------------------------------------------- 全局版本管理（§10.6） */
let ACC_VERSIONS = [];   // [{version, description}]（公共 · 版本管理创建，与模型无关）

async function wireAccVersions() {
  const data = await AAPI.get("/versions");
  ACC_VERSIONS = data.items;   // 建议面板每次开放时现读 ACC_VERSIONS，刷新后无需重渲染
}

/* 被测版本建议下拉：原生 datalist 的下拉面板由浏览器渲染（白底系统样式），与深色
   主题不搭，改为复用数据集筛选同款 ds-menu 视觉的自定义面板。输入仍自由填写
   （历史任务允许引用已下线版本号），聚焦/输入时按登记版本过滤，支持 ↑↓/Enter/Esc */
function ensureVerComboCss() {
  if (document.getElementById("verComboCss")) return;
  const st = document.createElement("style");
  st.id = "verComboCss";
  st.textContent = [
    ".ver-combo{position:relative;flex:1;display:flex;min-width:0}",
    ".ver-combo .in{width:100%}",
    ".ver-combo .ds-menu{max-height:220px;overflow:auto}",
    ".ver-combo .ds-mitem.on{background:var(--hov);color:var(--tx)}",
  ].join("\n");
  document.head.appendChild(st);
}

function wireVerCombo(input) {
  if (input.dataset.verBound) return;
  input.dataset.verBound = "1";
  ensureVerComboCss();
  const menu = document.createElement("div");
  menu.className = "ds-menu";
  menu.hidden = true;
  input.parentElement.appendChild(menu);        // 静态稿里 input 包在 .ver-combo 容器内
  let active = -1;                              // 键盘高亮项下标（-1 = 未选）

  const matched = () => {
    const q = input.value.trim().toLowerCase();
    return ACC_VERSIONS.filter(v => !q
      || v.version.toLowerCase().includes(q)
      || (v.description || "").toLowerCase().includes(q));
  };
  const render = () => {
    const list = matched();
    const rows = list.length ? list.map((v, i) =>
      `<div class="ds-mitem${i === active ? " on" : ""}" data-v="${v.version}">` +
      `<span>${v.version}</span><small>${v.description || ""}</small></div>`).join("")
      : `<div class="ds-mitem" style="pointer-events:none;color:var(--tx3)">${
          ACC_VERSIONS.length ? "无匹配 —— 当前输入可直接使用"
                              : "暂无登记版本 —— 点「＋ 新版本」就地创建，或直接输入"}</div>`;
    menu.innerHTML = `<div class="ds-mhead"><span>被测版本</span>` +
      `<span>公共 · 版本管理 · ${ACC_VERSIONS.length} 个</span></div>` + rows;
  };
  const open = () => { active = -1; render(); menu.hidden = false; };
  const close = () => { menu.hidden = true; };

  input.addEventListener("focus", open);
  input.addEventListener("click", open);   // 点击已聚焦输入框也重新展开（对齐原生 datalist）
  input.addEventListener("input", open);
  input.addEventListener("blur", () => setTimeout(close, 120));
  menu.addEventListener("mousedown", e => {     // mousedown 先于 blur，preventDefault 保焦点
    const item = e.target.closest(".ds-mitem[data-v]");
    if (!item) return;
    e.preventDefault();
    input.value = item.dataset.v;
    close();
  });
  input.addEventListener("keydown", e => {
    if (menu.hidden) return;
    if (e.key === "Escape") { close(); return; }
    const list = matched();
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      active = e.key === "ArrowDown" ? Math.min(active + 1, list.length - 1)
                                     : Math.max(active - 1, 0);
      render();
    } else if (e.key === "Enter" && active >= 0 && list[active]) {
      e.preventDefault();
      input.value = list[active].version;
      close();
    }
  });
}

/* 版本创建与模型无关：只登记版本号，发起任务时与目标模型自由组合 */
async function accRegisterVersion(btn) {
  const version = prompt("输入版本号（如 v1.3.0）：");
  if (!version || !version.trim()) return;
  const v = version.trim();
  try {
    await AAPI.post("/versions", { version: v });
    aapiToast(`版本已创建：${v}（「公共 · 版本管理」页可管理）`);
    await wireAccVersions();
    // 创建成功回填发起处输入（同功能任务下拉「创建即选中」语义）
    const input = btn?.closest(".p-row")?.querySelector(".ver-combo input");
    if (input) input.value = v;
  } catch (err) { aapiToast(`创建失败：${err.message}`, true); }
}

function aapiTargetOf(selectValue, paneId) {
  if (selectValue === "__custom__") {
    const pane = document.getElementById(paneId);
    const inputs = pane.querySelectorAll("input.in:not([data-keyenv])");
    const target = { type: "openai_compatible", base_url: inputs[0].value.trim(),
                     model: inputs[1].value.trim() };
    const kSel = pane.querySelector("[data-keysel]");
    const keyEnv = kSel ? keyEnvValue(kSel) : "";
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
  const rubric = $("accJudgeRubric").value.trim();
  if (rubric) {
    if (!judge) throw new Error("评分标准（rubric）需先选择 Judge 裁判模型");
    tools.judge.prompt_template = rubric;
  }
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
        aapiToast("暂无场景：请先到「评估场景」页新建场景，脚本须归属场景", true); return;
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
/* ------------------------------------------- rubric 资产 + 判官校准（§沉淀复用） */

async function wireAccRubrics() {
  try {
    const d = await AAPI.get("/accuracy/rubrics");
    ACC_RUBRICS = d.items || [];
  } catch (e) { ACC_RUBRICS = []; }
  const opt = r => `<option value="${r.id}">${escHtml(r.name)} · ${r.fingerprint}</option>`;
  const cal = document.getElementById("accCalRubricSel");
  if (cal) cal.innerHTML = '<option value="">— 选择评分标准 —</option>' + ACC_RUBRICS.map(opt).join("");
  const sel = document.getElementById("accJudgeRubricSel");
  if (sel) {
    sel.innerHTML = '<option value="">手写（用上方文本域）</option>' + ACC_RUBRICS.map(opt).join("");
    if (!sel.dataset.bound) {
      sel.dataset.bound = "1";
      sel.addEventListener("change", () => {
        const r = ACC_RUBRICS.find(x => x.id === sel.value);
        if (r) document.getElementById("accJudgeRubric").value = r.content;
      });
    }
  }
  const save = document.getElementById("accRubricSave");
  if (save && !save.dataset.bound) {
    save.dataset.bound = "1";
    save.addEventListener("click", async () => {
      const content = document.getElementById("accJudgeRubric").value.trim();
      if (!content) { aapiToast("文本域为空：先写评分标准再沉淀", true); return; }
      const name = prompt("评分标准名称（唯一，如「严格二分制-客服域」）：");
      if (!name || !name.trim()) return;
      try {
        const item = await AAPI.post("/accuracy/rubrics", { name: name.trim(), content });
        await wireAccRubrics();
        const sel2 = document.getElementById("accJudgeRubricSel");
        if (sel2) sel2.value = item.id;
        aapiToast(`已沉淀「${item.name}」（指纹 ${item.fingerprint}），校准抽屉可直接选用`);
      } catch (err) { aapiToast(`沉淀失败：${err.message}`, true); }
    });
  }
}

function accCalCollect() {
  const $ = id => document.getElementById(id);
  const ds = $("accCalDataset").value;
  if (!ds) throw new Error("请选择校准数据集（accuracy 类型，行含 human_score 人工标注列）");
  const judgeSel = $("accCalJudgeSel").value;
  let judge;
  if (judgeSel === "__custom__") {
    const url = $("accCalJudgeUrl").value.trim();
    const model = $("accCalJudgeModel").value.trim();
    if (!url || !model) throw new Error("自定义判官需填 访问 url 与 模型名");
    judge = { base_url: url, model };
  } else {
    if (!judgeSel) throw new Error("请选择判官（预制或自定义端点）");
    judge = { profile: judgeSel };
  }
  const rub = ACC_RUBRICS.find(x => x.id === $("accCalRubricSel").value);
  if (!rub) throw new Error("请选择评分标准（rubric 资产）——校准对象 = 判官 + 这版标准");
  const spec = { module: "accuracy", engine: "judge_calibration",
    task_type: "judge_calibration", dataset: ds, judge,
    rubric_id: rub.id, rubric_text: rub.content };
  const limit = $("accCalLimit").value.trim();
  if (limit) {
    const n = Number(limit);
    if (!Number.isInteger(n) || n < 1) throw new Error("抽样条数须为正整数");
    spec.limit = n;
  }
  const name = $("accCalName").value.trim() || `判官校准·${rub.name}·${ds}`;
  return { spec, name };
}

async function wireAccCalibration() {
  const dsSel = document.getElementById("accCalDataset");
  if (dsSel && !dsSel.dataset.loaded) {
    try {
      const d = await AAPI.get("/datasets?type=accuracy");
      dsSel.innerHTML = '<option value="">— 选择校准数据集 —</option>'
        + (d.items || []).map(it =>
          `<option value="${escHtml(it.name)}">${escHtml(it.name)}${it.notes ? ` · ${escHtml(String(it.notes)).slice(0, 40)}` : ""}</option>`).join("");
      dsSel.dataset.loaded = "1";
    } catch (e) { dsSel.innerHTML = '<option value="">数据集清单加载失败（后端未启动？）</option>'; }
  }
  const jSel = document.getElementById("accCalJudgeSel");
  if (jSel && !jSel.dataset.bound) {
    jSel.dataset.bound = "1";
    jSel.addEventListener("change", () => {
      document.getElementById("accCalJudgeCustom").hidden = jSel.value !== "__custom__";
    });
  }
  const view = document.getElementById("accCalRubricSel");
  if (view && !view.dataset.bound) {
    view.dataset.bound = "1";
    view.addEventListener("change", () => {
      const r = ACC_RUBRICS.find(x => x.id === view.value);
      document.getElementById("accCalRubricView").value = r ? r.content : "";
    });
  }
  const btn = document.getElementById("accCalRunBtn");
  if (btn && !btn.dataset.bound) {
    btn.dataset.bound = "1";
    btn.addEventListener("click", async () => {
      try {
        const { spec, name } = accCalCollect();
        const task = await AAPI.post("/accuracy/tasks", { name, spec });
        await AAPI.post(`/accuracy/tasks/${task.id}/run`);
        document.getElementById("accCalDrawer").hidden = true;
        aapiToast(`校准任务已发起：${task.id.slice(0, 8)}（运行中，详情页看校准卡）`);
        await wireAccTasks();
        showPage("runs");
      } catch (err) { aapiToast(`发起失败：${err.message}`, true); }
    });
  }
}

function accCalStat(label, value, sub, color) {
  return `<div class="stat"><div class="label"><i style="background:${color || "var(--ac)"}"></i>${label}</div>
    <div class="value" style="font-size:22px">${value}</div>${sub || ""}</div>`;
}

/* 校准任务详情（无 run 行）：κ 一致率统计 + 混淆矩阵 + 不一致行 + 配置快照 */
async function accRenderCalDetail(t, taskId, seq) {
  const detail = document.querySelector('main.page-host [data-page="detail"]');
  let result = null;
  try { result = await AAPI.get(`/accuracy/tasks/${taskId}/result`); }
  catch (e) { /* 409 非终态 */ }
  if (seq !== undefined && seq !== showAccTask.seq) return;
  const rep = result?.result?.calibration_report || null;
  const spec = t.spec || {};
  const m = rep?.metrics || null;
  const running = t.status === "pending" || t.status === "running" || t.status === "stopping";
  const judge = escHtml(spec.judge?.profile || spec.judge?.model || "—");
  const fp = rep ? rep.rubric_sha256_12 : "—";
  const dur = (t.started_at && t.finished_at)
    ? fmtDur((new Date(t.finished_at) - new Date(t.started_at)) / 1000) : "—";
  const grid = !m ? "" : (() => {
    const vBad = Number(m.kappa) < 0.6;
    return `<section class="stats" style="grid-template-columns:repeat(4,1fr);margin-bottom:14px">
      ${accCalStat("Cohen's κ", String(m.kappa),
        `<span class="delta ${vBad ? "down" : "up"}">${vBad ? "▼ " : "▲ "}${escHtml(m.kappa_verdict)}</span>`,
        vBad ? "var(--bad)" : "var(--good)")}
      ${accCalStat("一致率", (Number(m.agreement) * 100).toFixed(1) + "%",
        `<span class="dim" style="font-size:11px">95%CI ${m.agreement_ci95[0]} ~ ${m.agreement_ci95[1]}</span>`)}
      ${accCalStat("漏放率（人工0判1）", (Number(m.miss_rate_漏放) * 100).toFixed(1) + "%",
        `<span class="dim" style="font-size:11px">漏放 → 精度报告虚高</span>`,
        Number(m.miss_rate_漏放) > 0.05 ? "var(--bad)" : "var(--warn)")}
      ${accCalStat("误杀率（人工1判0）", (Number(m.kill_rate_误杀) * 100).toFixed(1) + "%",
        `<span class="dim" style="font-size:11px">误杀 → 精度报告偏低</span>`,
        Number(m.kill_rate_误杀) > 0.1 ? "var(--bad)" : "var(--warn)")}
    </section>`;
  })();
  const cm = rep ? rep.confusion_matrix["行=人工分,列=判官分"] : {};
  const levels = Object.keys(cm);
  const cmRows = levels.map(h => `<tr><td class="mono">${escHtml(h)}</td>` +
    levels.map(j => `<td class="num mono">${cm[h][j] ?? 0}</td>`).join("") + "</tr>").join("");
  const disRows = rep ? rep.disagree_rows : [];
  detail.innerHTML = `
    <header class="topbar">
      <div><h1>${escHtml(t.name || "(未命名)")} ${badge(t.status)}</h1>
      <div class="crumb">判官校准 · 数据集 ${escHtml(String(spec.dataset || "—"))} · 判官 ${judge}
      · rubric 指纹 ${escHtml(String(fp))} · ${t.started_at ? aapiShortTime(t.started_at) : "未开始"} · 耗时 ${dur}</div></div>
      <div class="actions">
        ${t.status === "running" ? `<button class="btn" onclick="stopAccTask('${taskId}')">停止</button>` : ""}
        <button class="btn" onclick="showAccTask('${taskId}')">刷新</button>
        <button class="btn" onclick="backToRuns()">← 任务列表</button></div>
    </header>
    ${t.error ? `<section class="card" style="margin-bottom:14px;border-color:color-mix(in srgb,var(--bad) 40%,transparent)">
      <div class="card-head"><div><h3 style="color:var(--bad)">执行错误</h3></div></div>
      <pre style="white-space:pre-wrap;font-size:12.5px;margin:0">${escHtml(t.error)}</pre></section>` : ""}
    ${m ? grid : `<section class="card"><div class="ds-empty">${running
      ? "校准中…（逐行调判官，本页每 5 秒自动刷新）" : "暂无校准结果"}</div></section>`}
    ${m ? `<div style="display:flex;flex-direction:column;gap:14px">
      ${rep.partial ? `<section class="card" style="padding:10px 16px;background:color-mix(in srgb,var(--warn) 10%,transparent)">
        <span style="font-size:12.5px">⚠ 部分报告（被停止）：有效 ${m.n_valid}/${m.n} 行，仅作排查参考</span></section>` : ""}
      <section class="card"><div class="card-head"><div><h3>混淆矩阵（校准明细）</h3>
        <div class="sub">行 = 人工分 · 列 = 判官分；对角线 = 判对；样本 n=${m.n} · 有效 ${m.n_valid} · 解析失败 ${m.parse_failures} · 判官 ${escHtml(rep.judge || "—")}</div></div></div>
        <table class="sumtable"><thead><tr><th>人工 ↓ / 判官 →</th>
          ${levels.map(j => `<th class="num">${escHtml(j)}</th>`).join("")}</tr></thead>
          <tbody>${cmRows || '<tr><td colspan="9" class="ds-empty">无有效行</td></tr>'}</tbody></table>
        <span class="hint" style="margin-top:8px">κ ≥0.6 才建议出正式精度报告；漏放/误杀方向见上方统计——调 rubric 措辞或换判官后重新校准</span>
      </section>
      <section class="card"><div class="card-head"><div><h3>不一致行 <span class="dim">(全部 ${disRows.length} 条)</span></h3>
        <div class="sub">判官与人工标注不一致的样本，是 rubric 措辞改进的直接输入</div></div></div>
        <table class="sumtable"><thead><tr><th>#</th><th>题目</th><th>人工分</th><th>判官分</th><th>方向</th></tr></thead>
        <tbody>${disRows.map(d => `<tr><td class="mono">${d.i}</td><td>${escHtml(d.question)}</td>
          <td class="num mono">${d.human}</td><td class="num mono">${d.judge}</td>
          <td>${d.human === 0 ? '<span class="badge failed"><i></i>漏放</span>' : '<span class="badge"><i></i>误杀</span>'}</td></tr>`).join("")
          || '<tr><td colspan="5" class="ds-empty">全部一致 ✓</td></tr>'}</tbody></table>
      </section>
      <section class="card"><div class="card-head"><div><h3>任务配置快照</h3>
        <div class="sub">校准结论只对这份配置（判官 + rubric 指纹）有效</div></div></div>
        <div class="detail-stack">
          <div class="kv"><span>校准集</span><div class="mono" style="font-size:12px">${escHtml(String(rep.dataset || spec.dataset || "—"))}</div></div>
          <div class="kv"><span>判官</span><div class="mono" style="font-size:12px">${escHtml(rep.judge || "—")}</div></div>
          <div class="kv"><span>rubric</span><div class="mono" style="font-size:12px">id ${escHtml(String(spec.rubric_id || "内联"))} · 指纹 ${escHtml(String(fp))}${spec.limit ? ` · 抽样 ${escHtml(String(spec.limit))} 行` : " · 全量"}</div></div>
        </div>
      </section>
    </div>` : ""}
  `;
  if (running && t.status === "running") {
    accDetailTimer = setTimeout(() => {
      if (seq === showAccTask.seq) showAccTask(taskId);
    }, 5000);
  }
}

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
    ["任务列表", wireAccTasks], ["脚本库/评估场景", wireAccScripts],
    ["数据集目录", wireAccBenchmarks], ["模型下拉", wireAccModels],
    ["版本创建", wireAccVersions], ["rubric 资产", wireAccRubrics],
    ["判官校准抽屉", wireAccCalibration],
  ];
  for (const [label, fn] of steps) {
    try {
      await fn();
    } catch (err) {
      console.error(`[aapi] ${label} 对接失败:`, err);
      aapiToast(`${label}对接失败（该板块保持演示数据）：${err.message}`, true);
    }
  }
  // 「＋ 新版本」按钮：就地创建到全局版本管理（公共 · 版本管理页可统一管理）
  document.getElementById("accVerNewBtn")
    ?.addEventListener("click", e => accRegisterVersion(e.currentTarget));
  document.getElementById("accVerNewBtn2")
    ?.addEventListener("click", e => accRegisterVersion(e.currentTarget));
  // 版本建议下拉与 /versions 拉取解耦绑定：接口失败时面板显示引导文案，恢复后自动有选项
  for (const id of ["accVerIn", "accVerIn2"]) {
    const input = document.getElementById(id);
    if (input) wireVerCombo(input);
  }
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
