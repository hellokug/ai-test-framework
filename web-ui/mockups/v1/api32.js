/* aite 静态稿 · 原地对接层（api.js）—— 仅 functional2.html 引用。
   同源 /api 代理（serve.py 反代到真实后端）→ 真数据渲染；后端不可达自动
   回退假数据并显示 MOCK 徽标（静态稿评审零影响）。
   原则：不改假数据渲染函数，live 时原地重渲染/覆盖；写操作失败 toast 显式报错。 */
"use strict";
const FAPI_INITIAL_HASH = location.hash || "";   // applyRoute 重写前捕获

const FAPI = {
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
  put(p, b) { return this.req("PUT", p, b); },
  del(p) { return this.req("DELETE", p); },
};

/* 任务层状态（执行维度）与评估层结果（用例维度）分离：
   任务 succeeded=跑完（≠用例全过），用例过没过看 result.status（评估层） */
/* taskBadge/evalCell 定义在 functional2.html 页面脚本（内联启动期即要用），
   此处运行期经 window 引用同一份。 */

function fapiBadge(live) {
  let el = document.getElementById("fapiBadge");
  if (!el) {
    el = document.createElement("div");
    el.id = "fapiBadge";
    el.style.cssText = "position:fixed;left:14px;bottom:14px;z-index:90;padding:6px 12px;" +
      "border-radius:9px;font-size:12px;font-weight:650;border:1px solid;backdrop-filter:blur(6px)";
    document.body.appendChild(el);
  }
  el.style.background = live ? "rgba(20,40,24,.85)" : "rgba(40,30,20,.85)";
  el.style.borderColor = live ? "rgba(74,222,128,.5)" : "rgba(251,191,36,.5)";
  el.style.color = live ? "#86efac" : "#fcd34d";
  el.textContent = live ? "LIVE · 已对接真实后端" : "MOCK · 假数据（后端未启动，python serve.py + aite serve）";
}

function fapiToast(msg, bad = false) { return liveToast(msg, bad); }   // 实现统一在 live2.js

/* ---------------------------------------------------------------- 渲染小件 */
function fapiVerdictBadge(v) {
  const cls = { 达标: "passed", 劣化: "failed" };
  return `<span class="badge ${cls[v] || ""}"><i></i>${v}</span>`;
}
function fapiDist(d) {
  return Object.entries(d || {}).map(([k, v]) => `${k} ${v}`).join(" · ");
}
function fapiShortTime(iso) { return shortTime(iso); }   // 实现统一在 live2.js

/* ---------------------------------------------------------- 脚本库（live） */
/* 导航徽章（v1.js setNavCount）+ 任务列表统计卡共用的真实计数 */
const NAV_COUNTS = {};
function fapiSetNavCount(pageId, n) {
  if (n === undefined || n === null) return;
  NAV_COUNTS[pageId] = n;
  setNavCount(pageId, n);
  fapiRenderRunsStats();
}

/* 任务列表页统计卡 = 真实计数（静态演示值口径已失效：内置场景已移除、比率无实时聚合） */
function fapiRenderRunsStats() {
  const stats = document.querySelector('main.page-host [data-page="runs"] .stats');
  if (!stats || NAV_COUNTS.runs === undefined
      || NAV_COUNTS.scenarios === undefined) return;
  stats.innerHTML = `
    <div class="stat"><div class="label"><i></i>累计运行</div><div class="value">${NAV_COUNTS.runs}</div></div>
    <div class="stat"><div class="label"><i style="background:var(--ok)"></i>运行中</div><div class="value">${NAV_COUNTS.running ?? 0}</div></div>
    <div class="stat"><div class="label"><i style="background:var(--ac2)"></i>评估场景</div><div class="value">${NAV_COUNTS.scenarios}</div></div>
    <div class="stat"><div class="label"><i style="background:var(--bad)"></i>版本结果</div><div class="value">${NAV_COUNTS.vresults ?? "—"}</div></div>`;
}

async function wireScripts() {
  const data = await FAPI.get("/functional/scripts");
  fapiSetNavCount("scripts", data.count);
  CASE_SCRIPTS.splice(0, CASE_SCRIPTS.length, ...data.items.map(s => ({
    id: s.id, name: s.name, sc: s.scenario,
    scLabel: s.scenario_name || s.scenario, type: fapiDist(s.row_types) || s.case_type,
    rows: s.row_count, rowDesc: "—",
    src: s.source === "import" ? "数据集导入" : "手工创建",
    srcNote: "", ext: s.external_ref || "", result: "none",
    last: fapiShortTime(s.updated_at) || "—", upd: fapiShortTime(s.updated_at),
  })));
  const sfLast = document.getElementById("sfLast");   // 最近结果无真实数据源 → 禁用
  sfLast.disabled = true;
  sfLast.title = "live 模式暂未提供脚本级结果聚合";
  renderFtScriptsTbl();
  bindScriptRowActions();
  document.getElementById("scriptCount").textContent =
    `真实后端 · 命中 ${data.count} 个脚本`;
  // 任务树数据源：真实脚本行 → FT_CASES（否则新建任务选不到新场景的用例）
  const byScen = {};
  for (const it of data.items) {
    const idx = it.row_index || [];
    (byScen[it.scenario] = byScen[it.scenario] || []).push(...idx.map(p => [p[0], p[1] || p[0]]));
  }
  for (const [sc, cases] of Object.entries(byScen)) {
    const liveIds = new Set(cases.map(c => sc + "." + c[0]));
    for (const id of [...ftSel]) if (id.startsWith(sc + ".") && !liveIds.has(id)) ftSel.delete(id);   // 出清该场景已删脚本行/已删场景的残留勾选（否则合计虚高）
    FT_CASES[sc] = cases;
    const sm = sceneById(sc);
    if (sm) sm[3] = cases.length;   // 场景行「N 例」计数同步（否则同页会话内建脚本后仍显示旧值 0）
    for (const c of cases) ftSel.add(sc + "." + c[0]);   // 新用例默认全选（全量语义）
  }
  if (typeof renderFtPick === "function") renderFtPick();
  // 场景筛选下拉刷新（写死 builtin 选项 → 真实场景全集）
  const sfScen = document.getElementById("sfScen");
  if (sfScen) {
    const cur = sfScen.value;
    sfScen.innerHTML = '<option value="">全部场景</option>' +
      [...new Set(data.items.map(i => i.scenario))].map(s => `<option>${s}</option>`).join("");
    if ([...sfScen.options].some(o => o.value === cur)) sfScen.value = cur;
  }
}

function bindScriptRowActions() {
  const tbl = document.getElementById("scriptsTbl");
  if (tbl.dataset.liveBound) return;
  tbl.dataset.liveBound = "1";
  tbl.addEventListener("click", async e => {
    const btn = e.target.closest("button");
    const tr = btn?.closest("tr");
    if (!btn || !tr) return;
    const sid = tr.querySelector(".rid").textContent.split(" ·")[0].trim();
    if (btn.textContent.includes("删除")) {
      if (!confirm(`删除脚本 ${sid}?（登记 + 行文件 + 重派生 cases.jsonl）`)) return;
      try { await FAPI.del(`/functional/scripts/${sid}`); }
      catch (err) { fapiToast(`删除失败：${err.message}`, true); return; }
      fapiToast(`已删除脚本 ${sid}`);
      await wireScripts();
    } else if (btn.textContent.includes("编辑")) {
      openEditScript(sid).catch(err => fapiToast(`打开编辑失败：${err.message}`, true));
    }
  });
}

/* -------------------------------------------------------- 评估场景（live） */
function fapiSceneCard(s) {
  const cover = Object.entries(s.coverage || {})
    .map(([k, v]) => `${k} ${v}`).join(" · ") || "—";
  const form = s.forms === "script" ? "复杂脚本 test_*.py" : "低代码数据行";
  return `<div class="scene-card"><b>${escHtml(s.display_name)}</b>
    <div class="rid mono" style="margin:2px 0 6px">${escHtml(s.id)}</div><p>${escHtml(s.description || "（无说明）")}</p>
    <div class="meta"><span class="tag">${escHtml(s.dimension || "未分类")}</span>
    <span class="tag">${s.cases} 例</span><span class="tag">${escHtml(cover)}</span>
    ${fapiDist(s.levels) ? `<span class="tag">${escHtml(fapiDist(s.levels))}</span>` : ""}
    <span class="tag" style="color:var(--ac)">${form}</span>
    </div>
    <div class="meta" style="margin-top:10px;display:flex;gap:6px">
      <button class="btn sm primary" data-mgr="${escHtml(s.id)}">管理脚本</button>
      <button class="btn sm" data-new="${escHtml(s.id)}">＋ 脚本</button>
      <button class="btn sm" data-del="${escHtml(s.id)}">删除</button>
      <button class="btn sm" data-open-drawer="newRunModal">过滤跑 →</button></div></div>`;
}

/* 预设标签（全量/冒烟/API/Agent）在页面加载时按演示 SCENES 生成，live 化后不重建的话
   点「全量」会把演示场景 id 灌回勾选集（合计虚高的同一根因），按真实场景按维度重算 */
function fapiRebuildFtPresets() {
  const dimScenes = kw => SCENES
    .filter(s => ((s[1] || "") + " " + (s[4] || "")).toLowerCase().includes(kw))
    .map(s => s[0]);
  FT_PRESETS.full = SCENES.map(s => s[0]);
  FT_PRESETS.smoke = FT_PRESETS.full.slice(0, 4);
  FT_PRESETS.api = dimScenes("api");
  FT_PRESETS.agent = [...new Set([...dimScenes("agent"), ...dimScenes("工具")])];
}

async function wireScenarios() {
  const data = await FAPI.get("/functional/scenarios");
  fapiSetNavCount("scenarios", data.count);
  SCENES.length = 0;
  for (const i of data.items) {
    SCENES.push([i.id, i.display_name, i.description || "", i.cases,
      i.dimension || "未分类", i.forms === "script" ? "脚本" : "数据驱动", ""]);
  }
  const grid = document.getElementById("sceneGrid");
  grid.innerHTML = data.items.map(fapiSceneCard).join("")
    || '<div class="ds-empty">后端暂无场景 —— 点右上「＋ 新建评估场景」创建</div>';
  if (!grid.dataset.liveBound) {
    grid.dataset.liveBound = "1";
    grid.addEventListener("click", async e => {
      const mgr = e.target.closest("[data-mgr]");
      const neu = e.target.closest("[data-new]");
      const del = e.target.closest("[data-del]");
      if (mgr) await openScriptMgr(mgr.dataset.mgr);
      if (neu) openNewScript(neu.dataset.new);
      if (del) {
        const sid = del.dataset.del;
        const ok = confirm(`删除场景 ${sid}？（整目录：脚本登记+行文件+派生 cases.jsonl；历史任务与版本归档持快照不受影响）`);
        if (!ok) return;
        try { await FAPI.del(`/functional/scenarios/${sid}`); }
        catch (err) {
          // 409 = 场景下仍有脚本/用例：二次确认后级联删除（force）
          if (!/仍有.*用例/.test(err.message) || !confirm(
              `${err.message}\n\n级联删除将连同全部脚本与用例一并移除，不可恢复。确定？`)) {
            fapiToast(`已取消删除 ${sid}`, true); return;
          }
          try { await FAPI.del(`/functional/scenarios/${sid}?force=true`); }
          catch (err2) { fapiToast(`删除失败：${err2.message}`, true); return; }
        }
        fapiToast(`已删除场景 ${sid}`);
        await wireScenarios();
      }
    });
  }
  // 场景树/创建抽屉的真实化：先出清不在册场景的 FT_CASES 残留（演示数据/已删场景，
  // 它们不渲染但会被勾选集合计，导致「合计 N 例」虚高），再补空条目 + 重置勾选集
  const liveScenes = new Set(data.items.map(i => i.id));
  for (const k of Object.keys(FT_CASES)) if (!liveScenes.has(k)) delete FT_CASES[k];
  for (const k of [...ftExpanded]) if (!liveScenes.has(k)) ftExpanded.delete(k);
  for (const i of data.items) if (!FT_CASES[i.id]) FT_CASES[i.id] = [];
  ftSel = new Set(Object.keys(FT_CASES).flatMap(sc => ftAllCases(sc)));
  fapiRebuildFtPresets();
  // 创建任务抽屉的场景树同步真实化
  if (typeof renderFtPick === "function") renderFtPick();
  // 新建脚本 / 导入抽屉的场景下拉 → 全部托管场景（场景全部用户自管）
  const userItems = data.items;
  const opts = '<option value="">请选择评估场景…</option>' +
    userItems.map(i => `<option value="${escHtml(i.id)}">${escHtml(i.id)} · ${escHtml(i.display_name)}</option>`).join("");
  const nsSel = document.getElementById("nsScen");
  if (nsSel) nsSel.innerHTML = opts;
  const impSel = document.querySelector("#importScriptDrawer .form-sec select.in");
  if (impSel) impSel.innerHTML = opts;
}

async function openScriptMgr(scenarioId) {
  const data = await FAPI.get(`/functional/scripts?scenario=${scenarioId}`);
  document.querySelector("#scriptMgrDrawer .drawer-head h3").textContent =
    `脚本管理 · ${scenarioId}`;
  const tbl = document.getElementById("scScriptTbl");
  tbl.innerHTML = `
    <thead><tr><th>脚本</th><th>类型</th><th>行数</th><th>来源</th><th>外部用例</th>
    <th style="text-align:right">操作</th></tr></thead>
    <tbody>${data.items.length ? data.items.map(s => `<tr>
      <td class="rname"><b>${escHtml(s.name)}</b><div class="rid">${escHtml(s.id)}</div></td>
      <td><span class="tag">${escHtml(fapiDist(s.row_types) || s.case_type)}</span></td><td class="num">${s.row_count}</td>
      <td class="dim">${escHtml(s.source)}</td><td class="mono dim">${escHtml(s.external_ref || "—")}</td>
      <td style="text-align:right;white-space:nowrap">
        <button class="btn sm" data-edit="${s.id}">编辑</button>
        <button class="btn sm" data-del="${s.id}">删除</button></td></tr>`).join("")
      : '<tr><td colspan="6" class="ds-empty">场景内暂无脚本 —— 「＋ 从 0 新建脚本」或「从数据集导入」</td></tr>'}</tbody>`;
  const d = document.getElementById("scriptMgrDrawer");
  d.dataset.scenario = scenarioId;
  d.hidden = false;
  if (!tbl.dataset.liveBound) {
    tbl.dataset.liveBound = "1";
    tbl.addEventListener("click", async e => {
      const edit = e.target.closest("[data-edit]");
      if (edit) { openEditScript(edit.dataset.edit).catch(err => fapiToast(`打开编辑失败：${err.message}`, true)); return; }
      const del = e.target.closest("[data-del]");
      if (!del) return;
      if (!confirm(`删除脚本 ${del.dataset.del}?`)) return;
      try { await FAPI.del(`/functional/scripts/${del.dataset.del}`); }
      catch (err) { fapiToast(`删除失败：${err.message}`, true); return; }
      await openScriptMgr(scenarioId);
      await wireScenarios();
    });
  }
}

/* -------------------------------------------------- 新建场景 / 脚本 / 导入 */
function bindLiveForm() {
  const scn = document.getElementById("scnDrawer");
  scn.querySelector(".drawer-foot .btn.primary").addEventListener("click", async () => {
    const inputs = scn.querySelectorAll(".drawer-body input.in");
    try {
      await FAPI.post("/functional/scenarios", {
        id: inputs[1].value.trim(), display_name: inputs[0].value.trim(),
        dimension: scn.querySelector("#scnDims .chip.on")?.textContent.trim() || "",
        description: inputs[2].value.trim(),
      });
      scn.hidden = true;
      fapiToast(`场景 ${inputs[1].value.trim()} 已创建（目录即注册表）`);
      await wireScenarios();
    } catch (err) { fapiToast(`创建失败：${err.message}`, true); }
  });

  const ns = document.getElementById("newScriptDrawer");
  ns.querySelector(".drawer-foot .btn.primary").addEventListener("click", async () => {
    try {
      await submitNewScript(ns);
    } catch (err) {
      if (nsCheckingRow) nsRevealRow(nsCheckingRow);   // 出错行可能处于折叠态：展开定位到它
      fapiToast(`保存失败：${err.message}`, true);
    }
  });

  const imp = document.getElementById("importScriptDrawer");
  // 场景「管理脚本」内点导入 → 预选并锁定当前场景（与新建脚本同口径）
  document.querySelectorAll('[data-open-drawer="importScriptDrawer"]').forEach(b =>
    b.addEventListener("click", () => setTimeout(() => {
      const sel = imp.querySelector(".form-sec select.in");
      const scen = b.closest("#scriptMgrDrawer")?.dataset.scenario;
      if (!sel) return;
      sel.disabled = false;
      if (scen) { sel.value = scen; sel.disabled = sel.value === scen; }
    }, 0)));
  imp.querySelector(".drawer-foot .btn.primary").addEventListener("click", async () => {
    try {
      const inputs = imp.querySelectorAll(".drawer-body input.in");
      const sel = imp.querySelectorAll(".drawer-body select.in");
      const cards = imp.querySelectorAll(".radio-card");
      const useDs = cards[0].classList.contains("on");
      const body = { scenario_id: sel[0].value, script_id: inputs[1].value.trim(),
        name: inputs[0].value.trim(), case_type: sel[1].value };
      if (useDs) body.dataset = sel[2].value; else body.jsonl = imp.querySelector("textarea").value;
      const row = await FAPI.post("/functional/scripts/import", body);
      imp.hidden = true;
      fapiToast(row.overwritten
        ? `已覆盖脚本 ${row.id}（${row.row_count} 行，原内容整体重写）`
        : `已导入脚本 ${row.id}（${row.row_count} 行）`);
      await wireScripts();
    } catch (err) { fapiToast(`导入失败：${err.message}`, true); }
  });
}

let nsCheckingRow = null;   // submitNewScript 正在校验的行卡；全部通过后清空

function submitNewScript(ns) {
  const editSid = ns.dataset.editSid || null;   // 编辑模式：PATCH，场景/脚本 id 不可改
  const scenSel = document.getElementById("nsScen");
  const payload = { name: document.getElementById("nsName").value.trim(), rows: [] };
  nsCheckingRow = null;
  if (editSid) {
    payload.script_id = editSid;
  } else {
    payload.scenario_id = (scenSel?.value || "").trim();
    payload.script_id = document.getElementById("nsSid").value.trim();
    if (!payload.scenario_id) throw new Error("请先选择评估场景（先场景后用例）");
    if (!payload.script_id) throw new Error("请填写脚本 id（英文编号）");
  }
  if (!payload.name) throw new Error("请填写脚本名称（中文名称）");
  ns.querySelectorAll("#nsRows .card").forEach(card => {
    nsCheckingRow = card;
    const title = card.querySelector(".ns-row-no")?.textContent.trim() || "行";
    const desc = card.querySelector(".ns-desc").value.trim() || `变体 ${payload.rows.length + 1}`;
    const reqText = card.querySelector(".ns-req").value.trim();
    if (!reqText) throw new Error(`${title}：request 不能为空（点「填入模板」开始）`);
    let req;
    try { req = JSON.parse(reqText); }
    catch (e) { throw new Error(`${title}：request 不是合法 JSON：${e.message}`); }
    if (!req || typeof req !== "object" || Array.isArray(req)) throw new Error(`${title}：request 须为 JSON 对象`);
    const row = { desc, ...nsSplitRequest(req) };
    const rid = card.querySelector(".ns-rid").value.trim();
    const cid = card.querySelector(".ns-cid").value.trim();
    const type = card.querySelector(".ns-type").value;
    const level = card.querySelector(".ns-level").value;
    if (rid) row.id = rid;
    if (cid) row.case_id = cid;
    if (type) row.type = type;
    if (level) row.level = level;
    const expect = {};
    const assertRows = [...card.querySelectorAll(".ns-asserts > div")];
    const loose = s => { try { return JSON.parse(s); } catch (e) { return s; } };
    [...card.querySelectorAll(".chips .chip.on")].forEach((chip, i) => {
      const k = chip.textContent.trim();
      const line = assertRows[i];   // 与 renderNsAsserts 按勾选顺序一一对应
      if (k === "http_status") {
        const rel = line.querySelector(".as-rel")?.value || "eq";
        const raws = line.querySelector(".as-val")?.value.trim() || "200";
        const nums = raws.split("，").join(",").split(",").map(s => Number(s.trim()));
        if (nums.some(n => !Number.isInteger(n) || n < 100 || n > 599)) {
          throw new Error(`${desc}：http_status 须为 100-599 的整数（∈ 逗号分隔多值）`);
        }
        // eq 保持 int（兼容格式）；其余关系走 {op: 值} 对象
        expect.http_status = rel === "eq" ? nums[0] : { [rel]: rel === "in" ? nums : nums[0] };
      } else if (k === "JSON 字段") {
        const path = line.querySelector(".as-path")?.value.trim();
        if (!path) throw new Error(`${desc}：JSON 字段断言须填字段路径`);
        const rel = line.querySelector(".as-rel")?.value || "eq";
        const raw = line.querySelector(".as-val")?.value.trim() ?? "";
        const spec = { path };
        if (rel === "eq") {
          if (!raw) throw new Error(`${desc}：字段 ${path} 期望值不能为空（== 可填数字/字符串/布尔）`);
          spec.eq = loose(raw);
        } else if (rel === "in") {
          const opts = raw.split("，").join(",").split(",").map(s => loose(s.trim())).filter(s => s !== "");
          if (!opts.length) throw new Error(`${desc}：字段 ${path} 的 ∈ 至少 1 个候选值（逗号分隔）`);
          spec.in = opts;
        } else if (rel === "len") {
          const n = Number(raw);
          if (!raw || !Number.isInteger(n) || n < 0) throw new Error(`${desc}：字段 ${path} 的长度须为 ≥0 整数`);
          spec.len = n;
        } else if (rel === "neq") {
          if (!raw) throw new Error(`${desc}：字段 ${path} 的 != 期望值不能为空`);
          spec.neq = loose(raw);
        } else if (rel === "contains") {
          if (!raw) throw new Error(`${desc}：字段 ${path} 的包含(like)关键词不能为空`);
          spec.contains = raw;
        } else {
          if (!["str", "int", "list", "dict"].includes(raw)) {
            throw new Error(`${desc}：字段 ${path} 的类型须为 str/int/list/dict`);
          }
          spec.type = raw;
        }
        expect.json = spec;
      } else if (k === "包含关键词") {
        const v = line.querySelector(".as-val")?.value.trim();
        if (!v) throw new Error(`${desc}：包含关键词须填关键词`);
        expect.contains = v;
        if (line.querySelector(".as-icb")?.checked) expect.ignore_case = true;
      } else if (k === "不含关键词") {
        const v = line.querySelector(".as-val")?.value.trim();
        if (!v) throw new Error(`${desc}：不含关键词须填关键词`);
        expect.not_contains = v;
      } else if (k === "finish_reason") {
        expect.finish_reason = line.querySelector(".as-val")?.value || "stop";
      } else if (k === "最少块数") {
        const n = Number(line.querySelector(".as-val")?.value.trim());
        if (!Number.isInteger(n) || n < 1) throw new Error(`${desc}：最少块数须为 ≥1 的整数（SSE chunk 数下限）`);
        expect.chunks_min = n;
      } else if (k === "指定工具被调用") {
        const v = line.querySelector(".as-val")?.value.trim();
        if (!v) throw new Error(`${desc}：指定工具被调用须填函数名`);
        expect.tool = v;
      } else if (k === "工具参数包含") {
        const v = line.querySelector(".as-val")?.value.trim();
        if (!v) throw new Error(`${desc}：工具参数包含须填文本`);
        expect.tool_args_contains = v;
      } else if (k === "不发起工具调用") expect.no_tool = true;
      else if (k === "无乱码") expect.no_garbled = true;
      else if (k === "无复读") expect.no_repeat = true;
      else if (k === "错误体字段") {
        const path = line.querySelector(".as-path")?.value.trim();
        if (!path) throw new Error(`${desc}：错误体字段断言须填路径`);
        const rel = line.querySelector(".as-rel")?.value || "eq";
        const raw = line.querySelector(".as-val")?.value.trim() ?? "";
        const spec = { path };
        if (rel === "eq") {
          if (!raw) throw new Error(`${desc}：错误体 ${path} 期望值不能为空`);
          spec.eq = loose(raw);
        } else if (rel === "contains") {
          if (!raw) throw new Error(`${desc}：错误体 ${path} 包含关键词不能为空`);
          spec.contains = raw;
        } else if (rel === "len") {
          const n = Number(raw);
          if (!raw || !Number.isInteger(n) || n < 0) throw new Error(`${desc}：错误体 ${path} 长度须为 ≥0 整数`);
          spec.len = n;
        } else if (rel === "type") {
          if (!["str", "int", "list", "dict"].includes(raw)) throw new Error(`${desc}：错误体 ${path} 类型须为 str/int/list/dict`);
          spec.type = raw;
        } else spec.nonempty = true;
        expect.resp = spec;   // 仅预期错误模式（配 http_status）生效
      } else if (k === "非空") expect.nonempty = true;
      else if (k === "no_leak 无泄漏") expect.no_leak = true;
      else if (k === "sse_done") expect.sse_done = true;
    });
    row.expect = expect;
    if (!Object.keys(expect).length) {
      throw new Error(`${desc}：至少选择 1 个断言（评估方法必填，空断言=恒过无意义）`);
    }
    // 生成值占位符（vars）：行内 ns-var 条目 → 后端 harness 生成器白名单结构
    const vars = {};
    card.querySelectorAll(".ns-vars .ns-var").forEach(v => {
      const name = v.dataset.name, kind = v.dataset.kind;
      const intOf = (cls, label) => {
        const raw = v.querySelector(cls)?.value.trim() ?? "";
        const n = Number(raw);
        if (raw === "" || !Number.isInteger(n)) throw new Error(`{${name}}：${label} 须为整数`);
        return n;
      };
      const seedRaw = v.querySelector(".vp-seed")?.value.trim() ?? "";
      let spec;
      if (kind === "garbled") {
        spec = { length: intOf(".vp-length", "长度") };
      } else if (kind === "random_string") {
        spec = { length: intOf(".vp-length", "长度"), charset: v.querySelector(".vp-charset")?.value || "alnum" };
      } else if (kind === "random_number") {
        spec = { min: intOf(".vp-min", "min"), max: intOf(".vp-max", "max") };
      } else if (kind === "random_choice") {
        const opts = (v.querySelector(".vp-options")?.value.trim() ?? "")
          .split("，").join(",").split(",").map(s => s.trim()).filter(Boolean);
        if (!opts.length) throw new Error(`{${name}}：候选至少 1 项（逗号分隔）`);
        spec = { options: opts };
      } else {
        const ka = Number(v.querySelector(".vp-key_at")?.value || 0.5);
        if (!(ka >= 0 && ka <= 1)) throw new Error(`{${name}}：埋码位置须在 [0,1]`);
        spec = { chars: intOf(".vp-chars", "字符量"), key_at: ka, key: (v.querySelector(".vp-key")?.value ?? "").trim() };
        if (!spec.key) throw new Error(`{${name}}：埋码关键词不能为空`);
      }
      if (seedRaw !== "") {
        const seed = Number(seedRaw);
        if (!Number.isInteger(seed)) throw new Error(`{${name}}：seed 须为整数（留空 = 每次执行新随机值）`);
        spec.seed = seed;
      }
      vars[name] = { [kind]: spec };
    });
    if (Object.keys(vars).length) row.vars = vars;
    payload.rows.push(row);
  });
  nsCheckingRow = null;
  const afterSave = async row => {
    ns.hidden = true;
    delete ns.dataset.editSid;
    if (scenSel) scenSel.disabled = false;
    document.getElementById("nsScenLock").hidden = true;
    document.getElementById("nsSid").disabled = false;
    ns.querySelector(".drawer-foot .btn.primary").textContent = "保存脚本（真实校验）";
    fapiToast(editSid ? `已更新脚本 ${row.id}（${row.row_count} 行，cases.jsonl 已重派生）`
                      : `已创建脚本 ${row.id}（${row.row_count} 行，cases.jsonl 已重派生）`);
    await wireScripts();
    return row;
  };
  return (editSid ? FAPI.patch(`/functional/scripts/${editSid}`, payload)
                  : FAPI.post("/functional/scripts", payload)).then(afterSave);
}

/* ---- 脚本编辑：详情回填（行内容/参数/断言/生成值）→ PATCH ---- */
function nsAssertLine(card, name) {
  return [...card.querySelectorAll(".ns-asserts > div")]
    .find(l => l.querySelector(".tag")?.textContent.trim() === name);
}
function nsFillExpect(card, expect) {
  const fill = name => {
    const ch = [...card.querySelectorAll(".chips .chip")].find(c => c.textContent.trim() === name);
    if (ch && !ch.classList.contains("on")) ch.click();   // 触发参数行展开（值保留机制兜底已填参数）
  };
  if (expect.http_status !== undefined) {
    fill("http_status");
    const line = nsAssertLine(card, "http_status");
    const hs = expect.http_status;
    if (hs && typeof hs === "object") {
      const [op, val] = Object.entries(hs)[0];
      line.querySelector(".as-rel").value = op;
      line.querySelector(".as-val").value = Array.isArray(val) ? val.join(",") : String(val);
    } else {
      line.querySelector(".as-rel").value = "eq";
      line.querySelector(".as-val").value = String(hs);
    }
  }
  if (expect.json && typeof expect.json === "object") {
    fill("JSON 字段");
    const line = nsAssertLine(card, "JSON 字段");
    const j = expect.json;
    line.querySelector(".as-path").value = j.path || "";
    const rel = j.eq !== undefined ? ["eq", JSON.stringify(j.eq)]
      : j.neq !== undefined ? ["neq", JSON.stringify(j.neq)]
      : j.in !== undefined ? ["in", (j.in || []).join(",")]
      : j.len !== undefined ? ["len", String(j.len)]
      : ["type", String(j.type || "")];
    line.querySelector(".as-rel").value = rel[0];
    line.querySelector(".as-val").value = rel[1];
  }
  if (expect.contains !== undefined) {
    fill("包含关键词");
    const line = nsAssertLine(card, "包含关键词");
    line.querySelector(".as-val").value = expect.contains;
    line.querySelector(".as-icb").checked = !!expect.ignore_case;
  }
  if (expect.not_contains !== undefined) {
    fill("不含关键词");
    nsAssertLine(card, "不含关键词").querySelector(".as-val").value = expect.not_contains;
  }
  if (expect.finish_reason !== undefined) {
    fill("finish_reason");
    nsAssertLine(card, "finish_reason").querySelector(".as-val").value = expect.finish_reason;
  }
  if (expect.chunks_min !== undefined) {
    fill("最少块数");
    nsAssertLine(card, "最少块数").querySelector(".as-val").value = String(expect.chunks_min);
  }
  if (expect.tool !== undefined) {
    fill("指定工具被调用");
    nsAssertLine(card, "指定工具被调用").querySelector(".as-val").value = String(expect.tool);
  }
  if (expect.tool_args_contains !== undefined) {
    fill("工具参数包含");
    nsAssertLine(card, "工具参数包含").querySelector(".as-val").value = String(expect.tool_args_contains);
  }
  if (expect.no_tool) fill("不发起工具调用");
  if (expect.no_garbled) fill("无乱码");
  if (expect.no_repeat) fill("无复读");
  if (expect.resp && typeof expect.resp === "object") {
    fill("错误体字段");
    const line = nsAssertLine(card, "错误体字段");
    const r = expect.resp;
    line.querySelector(".as-path").value = r.path || "";
    const rel = r.eq !== undefined ? ["eq", JSON.stringify(r.eq)]
      : r.contains !== undefined ? ["contains", String(r.contains)]
      : r.len !== undefined ? ["len", String(r.len)]
      : r.type !== undefined ? ["type", String(r.type)]
      : ["nonempty", ""];
    line.querySelector(".as-rel").value = rel[0];
    line.querySelector(".as-val").value = rel[1];
  }
  if (expect.nonempty) fill("非空");
  if (expect.no_leak) fill("no_leak 无泄漏");
  if (expect.sse_done) fill("sse_done");
}
function nsFillVars(card, vars) {
  const box = card.querySelector(".ns-vars");
  for (const [name, spec] of Object.entries(vars || {})) {
    const kind = Object.keys(spec)[0], kw = spec[kind] || {};
    box.insertAdjacentHTML("beforeend", nsVarRowHtml(name, kind));
    const el = box.lastElementChild;
    const set = (cls, val) => {
      const i = el.querySelector(cls);
      if (i && val !== undefined && val !== null) i.value = Array.isArray(val) ? val.join(",") : String(val);
    };
    set(".vp-length", kw.length); set(".vp-charset", kw.charset); set(".vp-min", kw.min); set(".vp-max", kw.max);
    set(".vp-options", kw.options); set(".vp-chars", kw.chars); set(".vp-key_at", kw.key_at);
    set(".vp-key", kw.key); set(".vp-seed", kw.seed);
  }
}
/* 行数据 → 行卡控件回填（openEditScript 与「从 jsonl 导入行」共用） */
function nsFillRowFromData(card, row) {
  card.querySelector(".ns-rid").value = row.id || "";
  card.querySelector(".ns-cid").value = row.case_id || "";
  card.querySelector(".ns-type").value = row.type || "";
  card.querySelector(".ns-level").value = row.level || "";
  card.querySelector(".ns-desc").value = row.desc || "";
  card.querySelector(".ns-req").value = JSON.stringify(nsMergeRequest(row), null, 2);
  nsFillExpect(card, row.expect || {});
  nsFillVars(card, row.vars);
}
async function openEditScript(sid) {
  const ns = document.getElementById("newScriptDrawer");
  nsReset();                                  // 干净态再灌值（同时清编辑态标记）
  const t = await FAPI.get(`/functional/scripts/${sid}`);
  ns.dataset.editSid = sid;
  const scenSel = document.getElementById("nsScen");
  scenSel.value = t.scenario;
  scenSel.disabled = true;                    // 编辑态：归属与脚本 id 不可改
  const sidIn = document.getElementById("nsSid");
  sidIn.value = sid; sidIn.disabled = true;
  document.getElementById("nsName").value = t.name || "";
  const rowsBox = document.getElementById("nsRows");
  rowsBox.innerHTML = ""; nsRowN = 0;
  for (const row of (t.rows || [])) {
    nsRowN += 1;
    rowsBox.insertAdjacentHTML("beforeend", nsRowHtml(nsRowN));
    const card = rowsBox.lastElementChild;
    bindChips(card);                             // 动态行卡补绑芯片/断言交互
    nsFillRowFromData(card, row);
  }
  if (!rowsBox.children.length) {
    nsRowN = 1;
    rowsBox.insertAdjacentHTML("beforeend", nsRowHtml(1));
    bindChips(rowsBox);
    renderNsAsserts(rowsBox.firstElementChild);
  }
  nsCollapseForReview();
  ns.querySelector(".drawer-foot .btn.primary").textContent = "保存修改（真实校验）";
  ns.hidden = false;
}

function openNewScript(scenarioId) {
  const ns = document.getElementById("newScriptDrawer");
  if (typeof nsReset === "function") nsReset();   // 打开即干净态（同时解锁场景）
  delete ns.dataset.editSid;
  ns.querySelector(".drawer-foot .btn.primary").textContent = "保存脚本（真实校验）";
  const sel = document.getElementById("nsScen");
  if (scenarioId) {
    sel.value = scenarioId;
    sel.disabled = sel.value === scenarioId;      // 选中成功才锁（场景不存在则保持可选）
    document.getElementById("nsScenLock").hidden = !sel.disabled;
  }
  ns.hidden = false;
}

/* ---------------------------------------- 用例明细 → 脚本行编辑直达（编辑按钮） */
async function editCaseRow(rowId, scenario) {
  if (!rowId) { fapiToast("该用例缺少行 id，无法定位脚本行", true); return; }
  if (!scenario) { fapiToast("该用例记录缺场景信息（旧版本运行），无法定位脚本行", true); return; }
  const scen = String(scenario).replace(/^pytest:/, "");
  try {
    const list = await FAPI.get(`/functional/scripts?scenario=${encodeURIComponent(scen)}`);
    for (const s of list.items) {
      const t = await FAPI.get(`/functional/scripts/${s.id}`);
      if ((t.rows || []).some(r => String(r.id) === String(rowId))) {
        await openEditScript(s.id);              // 行卡已按行数据回填，可直接改
        const card = [...document.querySelectorAll("#nsRows .card")]
          .find(c => c.querySelector(".ns-rid")?.value === String(rowId));
        if (card) {
          nsRevealRow(card);
          card.style.outline = "2px solid var(--ac2)";
          setTimeout(() => { card.style.outline = ""; }, 2000);
        }
        return;
      }
    }
    fapiToast(`行 ${rowId} 不在场景「${scen}」任何脚本中（可能已删除或改 id）`, true);
  } catch (err) {
    fapiToast(`定位脚本失败：${err.message}`, true);
  }
}
if (!window.__caseRowBound) {                    // 明细列表：编辑直达 + 行头点击展开/收起
  window.__caseRowBound = true;
  document.addEventListener("click", e => {
    const ed = e.target.closest("[data-case-edit]");
    if (ed) { editCaseRow(ed.dataset.caseEdit, ed.dataset.caseScenario); return; }
    const t = e.target.closest("[data-case-toggle]");
    if (!t) return;
    const head = t.closest(".case-item-head");
    const body = head && head.parentElement.querySelector(".case-item-body");
    if (!body) return;
    body.hidden = !body.hidden;
    const btn = head.querySelector("button[data-case-toggle]");
    if (btn) btn.textContent = body.hidden ? "详情" : "收起";
  });
}

/* ------------------------------------------------------------ 工具库 */
window.FT_TOOLS = [];
const TOOL_TEMPLATE = {
  type: "function",
  function: {
    name: "get_weather",
    description: "查询指定城市的天气",
    parameters: {
      type: "object",
      properties: {
        city: { type: "string", description: "城市名，如 北京" },
        unit: { type: "string", enum: ["celsius", "fahrenheit"], description: "温度单位" },
      },
      required: ["city"],
    },
  },
};
async function wireTools() {
  const data = await FAPI.get("/functional/tools");
  window.FT_TOOLS = data.items;
  setNavCount("tools", data.count);
  document.querySelectorAll(".ns-tool-pick").forEach(sel => { sel.innerHTML = nsToolOptions(); });
  const tbl = document.getElementById("toolsTbl");
  if (!tbl) return;
  tbl.innerHTML = `<thead><tr><th>工具名</th><th>说明</th><th>参数（* 必填）</th><th>被引用</th>
    <th style="text-align:right">操作</th></tr></thead>
    <tbody>${data.items.length ? data.items.map(t => {
      const ps = t.schema.function.parameters || {};
      const req = ps.required || [];
      const props = Object.keys(ps.properties || {}).map(k => req.includes(k) ? k + "*" : k).join(", ");
      return `<tr><td class="rname"><b class="mono">${escHtml(t.name)}</b></td>
        <td class="dim">${escHtml(t.description || "—")}</td><td class="mono dim">${escHtml(props || "—")}</td>
        <td>${t.used_by.length ? `${t.used_by.length} 个脚本` : '<span class="faint">未引用</span>'}</td>
        <td style="text-align:right;white-space:nowrap">
          <button class="btn sm" data-tool-edit="${escHtml(t.name)}">编辑</button>
          <button class="btn sm" data-tool-del="${escHtml(t.name)}">删除</button></td></tr>`;
    }).join("") : '<tr><td colspan="5" class="ds-empty">工具库为空 —— 「＋ 新建工具」定义第一个 tool_calls 工具</td></tr>'}</tbody>`;
}
function openToolDrawer(name) {
  const d = document.getElementById("toolDrawer");
  const t = name ? window.FT_TOOLS.find(x => x.name === name) : null;
  d.dataset.editName = t ? t.name : "";
  document.getElementById("toolDrawerTitle").textContent = t ? `编辑工具 · ${t.name}` : "新建工具";
  document.getElementById("toolJson").value = JSON.stringify(t ? t.schema : TOOL_TEMPLATE, null, 2);
  d.hidden = false;
}
function bindToolsUi() {
  if (!document.getElementById("toolsTbl")) return;
  document.getElementById("toolNewBtn").addEventListener("click", () => openToolDrawer(null));
  document.getElementById("toolTplBtn").addEventListener("click", () => {
    document.getElementById("toolJson").value = JSON.stringify(TOOL_TEMPLATE, null, 2);
  });
  document.getElementById("toolsTbl").addEventListener("click", async e => {
    const ed = e.target.closest("[data-tool-edit]");
    if (ed) { openToolDrawer(ed.dataset.toolEdit); return; }
    const del = e.target.closest("[data-tool-del]");
    if (!del || !confirm(`删除工具 ${del.dataset.toolDel}？`)) return;
    try { await FAPI.del(`/functional/tools/${del.dataset.toolDel}`); }
    catch (err) { fapiToast(`删除失败：${err.message}`, true); return; }
    fapiToast(`已删除工具 ${del.dataset.toolDel}`);
    await wireTools();
  });
  document.getElementById("toolSaveBtn").addEventListener("click", async () => {
    const d = document.getElementById("toolDrawer");
    let schema;
    try { schema = JSON.parse(document.getElementById("toolJson").value); }
    catch (err) { fapiToast(`工具定义不是合法 JSON：${err.message}`, true); return; }
    const editName = d.dataset.editName;
    try {
      if (editName) await FAPI.put(`/functional/tools/${editName}`, schema);
      else await FAPI.post("/functional/tools", schema);
    } catch (err) { fapiToast(`保存失败：${err.message}`, true); return; }
    d.hidden = true;
    fapiToast(editName ? `已更新工具 ${editName}（引用它的场景已重新派生）` : `已新建工具 ${schema.function?.name}`);
    await wireTools();
  });
}

/* ------------------------------------------------------------ 任务列表 */
let RUN_TASK_IDS = [];
function rebuildRunsFromTasks(items) {
  // 与假数据完全相同的数组形状（renderRuns/bindTaskFilter 按下标渲染），
  // 行点击经 openRunDetail 路由到真实任务详情
  RUN_TASK_IDS = items.map(t => t.id);
  RUNS.splice(0, RUNS.length, ...items.map(t => [
    t.name || "(未命名)",
    `${t.id.slice(0, 8)} · ${t.spec?.version || "未标注版本"}`
      + (t.retry_of ? ` · 重试 #${t.retry_count} ← ${t.retry_of.slice(0, 8)}` : ""),
    t.spec?.target?.profile || "-",
    t.status,
    t.result?.pass_rate !== undefined
      ? { rate: t.result.pass_rate, total: t.result.evaluated_cases, passed: t.result.passed_cases } : null,
    t.result ? String(t.result.total_cases) : "—",
    fapiShortTime(t.created_at),
    "-",
  ]));
  // 旧任务 result 快照无 pass_rate → /runs/{id} summaries 兜底（评估层真值，非执行层 status）
  items.forEach((t, i) => {
    if (t.result?.pass_rate !== undefined || !t.result?.run_id) return;
    FAPI.get(`/runs/${t.result.run_id}`).then(run => {
      const rows = (run.summaries || []).filter(s => (s.dataset || "").startsWith("pytest:"));
      if (!rows.length || !RUNS[i]) return;
      const total = rows.reduce((acc, r) => acc + (r.count || 0), 0);
      const rate = total ? rows.reduce((acc, r) => acc + (r.value || 0) * (r.count || 0), 0) / total : null;
      const passed = rows.reduce((acc, r) => acc + Math.round((r.value || 0) * (r.count || 0)), 0);
      RUNS[i][4] = { rate, total, passed };
      const bar = document.getElementById("runsFilter");
      renderRuns(bar?.querySelector(".chip[data-status].on")?.dataset.status || "all",
                 bar?.querySelector("[data-owner]")?.value || "all",
                 bar?.querySelector("[data-kw]")?.value?.trim().toLowerCase() || "");
    }).catch(() => { /* 静默：下一轮轮询重试 */ });
  });
  const bar = document.getElementById("runsFilter");
  const on = bar?.querySelector(".chip[data-status].on");
  const owner = bar?.querySelector("[data-owner]");
  const kw = bar?.querySelector("[data-kw]");
  renderRuns(on?.dataset.status || "all", owner?.value || "all",
             kw?.value?.trim().toLowerCase() || "");
}

async function wireTasks() {
  const data = await FAPI.get("/functional/tasks");
  fapiSetNavCount("runs", data.count);
  NAV_COUNTS.running = (data.items || []).filter(t => t.status === "running").length;
  fapiRenderRunsStats();
  rebuildRunsFromTasks(data.items);
}

// 状态自动转换：runs 页可见时每 5s 轮询，状态签名变化才重渲染（不打扰筛选状态）
function startLivePolling() {
  if (startLivePolling.timer) return;
  startLivePolling.timer = setInterval(async () => {
    if (!FAPI.live) return;
    const runsSec = document.querySelector('main.page-host [data-page="runs"]');
    if (!runsSec || runsSec.hidden) return;
    try {
      const data = await FAPI.get("/functional/tasks");
      const sig = data.items.map(t => `${t.id}:${t.status}`).join("|");
      if (sig === startLivePolling.lastSig) return;
      startLivePolling.lastSig = sig;
      fapiSetNavCount("runs", data.count);
      NAV_COUNTS.running = (data.items || []).filter(t => t.status === "running").length;
      fapiRenderRunsStats();
      rebuildRunsFromTasks(data.items);
    } catch (e) { /* 静默：下一轮再试 */ }
  }, 5000);
  startLivePolling.timer.unref?.();
}

function openRunDetail(idx) {
  const tid = RUN_TASK_IDS[idx];
  if (FAPI.live && tid) {
    showLiveTask(tid).catch(err =>
      fapiToast(`打开任务详情失败：${err.message}（后端可能未启动）`, true));
    return;
  }
  showPage("detail");   // mock 模式走原静态详情
}

/* ---------- 任务操作：运行/停止/重试/复制/编辑（ref = 列表行下标或任务 id） ----------
   run pending 首发/终态重跑、stop 仅 running；retry 仅终态 = 同一任务同配置
   再跑一轮（不再新建任务）；copy 任意状态 = 独立分支变体（同 spec 新任务）。
   两者都打开编辑抽屉（非运行中可改 spec，改完「保存并运行」）。终态集合与
   后端 core.types TASK_TERMINAL 对齐；mock 模式点击只提示。 */
async function fapiTaskAction(action, ref, ev) {
  ev?.stopPropagation();
  const byId = typeof ref === "string";
  const tid = byId ? ref : RUN_TASK_IDS[ref];
  if (!FAPI.live || !tid) {
    fapiToast("该操作需 live 模式（后端启动后）可用", true);
    return;
  }
  const onDetail = byId && sessionStorage.getItem("aite-live-last-task") === ref;
  const refresh = async () => {
    await wireTasks();
    if (onDetail) {
      const detail = document.querySelector('main.page-host [data-page="detail"]');
      if (detail && !detail.hidden) await showLiveTask(tid);
    }
  };
  try {
    if (action === "run") {
      await FAPI.post(`/functional/tasks/${tid}/run`);
      fapiToast(`任务 ${tid.slice(0, 8)} 已发起运行`);
    } else if (action === "stop") {
      await FAPI.post(`/functional/tasks/${tid}/stop`);
      fapiToast(`任务 ${tid.slice(0, 8)} 已停止`);
    } else if (action === "retry") {
      const rt = await FAPI.post(`/functional/tasks/${tid}/retry`);
      fapiToast(`已重跑同一任务 ${rt.id.slice(0, 8)}（第 ${(rt.history || []).length} 轮，运行中）`);
    } else if (action === "copy") {
      const nt = await FAPI.post(`/functional/tasks/${tid}/duplicate`);
      fapiToast(`已复制为「${nt.name}」（${nt.id.slice(0, 8)}，pending）`);
      await openTaskEdit(nt.id);
    } else if (action === "edit") {
      await openTaskEdit(tid);
    }
    await refresh();
  } catch (err) { fapiToast(`任务操作失败：${err.message}`, true); }
}

/* ---------- 任务编辑（pending 限定）：复制/重试生成的草稿改参再跑 ----------
   保存 = PATCH /functional/tasks/{id}（后端守卫 pending，非 pending 409）；
   spec 以原 spec 为底、表单字段覆盖，未识别键原样保留（CLI 造的 spec 不丢字段）。 */
let teState = null;   // 当前编辑上下文 {taskId, spec, scenes}

function teEsc(s) { return escHtml(s); }   // 转义统一走 live2.js

function teRenderScenes() {
  const box = document.getElementById("teScenes");
  if (!box || !teState) return;
  box.innerHTML = teState.scenes.length ? teState.scenes.map(s =>
    `<span class="chip on">${teEsc(s)} <span data-te-del="${teEsc(s)}" title="移除该场景" style="cursor:pointer;font-weight:700">✕</span></span>`).join("")
    : '<span class="hint" style="margin:0;color:var(--bad)">未选任何场景 —— 至少保留一个才能保存</span>';
}

async function openTaskEdit(taskId) {
  const drawer = document.getElementById("taskEditDrawer");
  if (!drawer) return;
  let t;
  try { t = await FAPI.get(`/functional/tasks/${taskId}`); }
  catch (err) { fapiToast(`载入任务失败：${err.message}`, true); return; }
  if (t.status !== "pending") {
    fapiToast(`仅 pending 任务可编辑（当前 ${t.status}）—— 重试/复制会生成 pending 新任务`, true);
    return;
  }
  const spec = t.spec || {};
  teState = { taskId, spec, scenes: [...((spec.tools || {}).datasets || [])] };
  document.getElementById("teName").value = t.name || "";
  document.getElementById("teVersion").value = spec.version || "";
  document.getElementById("teTimeout").value = (spec.extra || {}).timeout ?? "";
  document.getElementById("teCases").value = ((spec.tools || {}).cases || []).join("\n");
  document.getElementById("teSpecView").textContent = JSON.stringify(spec, null, 2);
  teRenderScenes();
  // 候选懒拉：场景 / 版本（失败静默，手填不受影响）
  try {
    const scens = await FAPI.get("/functional/scenarios");
    document.getElementById("teSceneList").innerHTML =
      (scens.items || []).map(s => `<option value="${teEsc(s.id)}">`).join("");
  } catch (e) { /* 无场景资产时留空 */ }
  try {
    const vers = await FAPI.get("/versions");
    document.getElementById("teVerList").innerHTML =
      (vers.items || []).map(v => `<option value="${teEsc(v.version)}">`).join("");
  } catch (e) { /* 手填版本不受影响 */ }
  // 目标模型：profile / 端点命中登记项则选中，否则落自定义面板回填
  const sel = document.getElementById("teModelSel");
  const target = spec.target || {};
  const hit = MODEL_SOURCES.find(s => target.profile && s.target.profile === target.profile)
    || MODEL_SOURCES.find(s => target.type === "openai_compatible"
      && s.target.base_url === (target.base_url || "") && s.target.model === (target.model || ""));
  sel.innerHTML = MODEL_SOURCES.map(s => `<option value="${s.key}">${s.label}</option>`).join("")
    + '<option value="__custom__">自定义（手填端点）</option>';
  const pane = document.getElementById("teModelCustom");
  if (hit) {
    sel.value = hit.key;
    pane.hidden = true;
  } else {
    sel.value = "__custom__";
    pane.hidden = false;
    document.getElementById("teBaseUrl").value = target.base_url || "";
    document.getElementById("teModelName").value = target.model || "";
    const kSel = pane.querySelector("[data-keysel]");
    if (kSel) {
      try { wireKeySelect(kSel, (await FAPI.get("/secrets")).items || []); }
      catch (e) { wireKeySelect(kSel, []); }
      const kEnv = pane.querySelector("[data-keyenv]");
      if (target.api_key_env && kEnv) {
        kSel.value = "__env__";
        kEnv.hidden = false;
        kEnv.value = target.api_key_env;
      }
    }
  }
  bindModelCustom("teModelSel", "teModelCustom");   // 选项重建后重挂显隐（幂等）
  if (!drawer.dataset.teBound) {                    // foot 按钮与 chips 监听只挂一次
    drawer.dataset.teBound = "1";
    drawer.querySelector("#teSaveBtn").addEventListener("click", () => saveTaskEdit(false));
    drawer.querySelector("#teRunBtn").addEventListener("click", () => saveTaskEdit(true));
    document.getElementById("teScenes").addEventListener("click", e => {
      const del = e.target.closest("[data-te-del]");
      if (!del || !teState) return;
      teState.scenes = teState.scenes.filter(s => s !== del.dataset.teDel);
      teRenderScenes();
    });
    document.getElementById("teAddBtn").addEventListener("click", () => {
      const input = document.getElementById("teAddScene");
      const s = input.value.trim();
      if (!s || !teState) return;
      if (teState.scenes.includes(s)) { fapiToast(`场景 ${s} 已在列表`, true); return; }
      teState.scenes.push(s);
      input.value = "";
      teRenderScenes();
    });
  }
  drawer.hidden = false;
}

async function saveTaskEdit(runAfter) {
  if (!teState) return;
  const { taskId, spec: orig } = teState;
  const name = document.getElementById("teName").value.trim();
  const version = document.getElementById("teVersion").value.trim();
  if (!version) { fapiToast("被测版本必填（终态结果按版本归档）", true); return; }
  if (!teState.scenes.length) { fapiToast("至少保留一个场景", true); return; }
  // 目标模型：与「新建运行」同规则（登记项直接引用，自定义端点校验必填）
  let target;
  const modelKey = document.getElementById("teModelSel").value;
  if (modelKey === "__custom__") {
    target = { type: "openai_compatible",
               base_url: document.getElementById("teBaseUrl").value.trim(),
               model: document.getElementById("teModelName").value.trim() };
    const kSel = document.querySelector("#teModelCustom [data-keysel]");
    const keyEnv = kSel ? keyEnvValue(kSel) : "";
    if (keyEnv) target.api_key_env = keyEnv;
    if (!target.base_url || !target.model) {
      fapiToast("自定义端点需填写 模型访问 url 与 模型名称", true);
      return;
    }
  } else {
    const src = MODEL_SOURCES.find(s => s.key === modelKey);
    if (!src) { fapiToast("目标模型已失效，请重新选择", true); return; }
    target = { ...src.target };
  }
  const spec = JSON.parse(JSON.stringify(orig || {}));   // 原样打底，未识别字段不丢
  spec.version = version;
  spec.target = target;
  spec.tools = { ...(orig.tools || {}) };
  spec.tools.datasets = [...teState.scenes];
  const cases = document.getElementById("teCases").value.split("\n")
    .map(s => s.trim()).filter(Boolean);
  if (cases.length) spec.tools.cases = cases; else delete spec.tools.cases;
  const timeout = document.getElementById("teTimeout").value.trim();
  if (timeout) spec.extra = { ...(orig.extra || {}), timeout: Number(timeout) };
  else if (orig.extra && orig.extra.timeout !== undefined) {
    delete spec.extra.timeout;
    if (!Object.keys(spec.extra).length) delete spec.extra;
  }
  if (name) spec.run_name = name; else delete spec.run_name;
  try {
    await FAPI.patch(`/functional/tasks/${taskId}`, { name: name || undefined, spec });
    if (runAfter) await FAPI.post(`/functional/tasks/${taskId}/run`);
    document.getElementById("taskEditDrawer").hidden = true;
    fapiToast(runAfter
      ? `配置已保存，任务 ${taskId.slice(0, 8)} 已发起运行`
      : `配置已保存（${taskId.slice(0, 8)}，仍为 pending，随时可运行）`);
    await wireTasks();
    await showLiveTask(taskId);
  } catch (err) {
    fapiToast(`保存失败：${err.message}${/409/.test(err.message) ? "（任务已不在 pending，可能已被发起运行）" : ""}`, true);
  }
}

let showLiveTaskSeq = 0;

async function showLiveTask(taskId, roundSel) {
  const seq = ++showLiveTaskSeq;
  const detail = document.querySelector('main.page-host [data-page="detail"]');
  if (!showLiveTask.original) showLiveTask.original = detail.innerHTML;
  sessionStorage.setItem("aite-live-last-task", taskId);
  // 先切页给即时反馈（加载骨架），再异步拉数据；失败显式报错不静默
  showPage("detail");
  detail.innerHTML = `<header class="topbar">
      <div><h1>加载中…</h1><div class="crumb">task ${taskId}</div></div>
      <div class="actions"><button class="btn" onclick="backToRuns()">← 任务列表</button></div>
    </header>
    <section class="card"><div class="ds-empty">正在加载任务详情…</div></section>`;
  let t;
  try {
    t = await FAPI.get(`/functional/tasks/${taskId}`);
  } catch (err) {
    if (seq !== showLiveTaskSeq) return;
    if (/^404/.test(err.message)) {
      // 任务已不存在（如数据清理后残留的会话记录）：回落任务列表，不卡「加载失败」
      sessionStorage.removeItem("aite-live-last-task");
      fapiToast(`任务 ${taskId} 不存在（可能已被删除），已回到任务列表`, true);
      backToRuns();
      return;
    }
    detail.innerHTML = `<header class="topbar">
        <div><h1>${taskId} <span class="badge failed"><i></i>加载失败</span></h1>
        <div class="crumb">任务详情加载失败</div></div>
        <div class="actions"><button class="btn" onclick="backToRuns()">← 任务列表</button></div>
      </header>
      <section class="card"><div class="ds-empty">加载失败：${err.message}（后端未启动或网络异常？）</div></section>`;
    return;
  }
  if (seq !== showLiveTaskSeq) return;
  let result = null;
  try { result = await FAPI.get(`/functional/tasks/${taskId}/result`); }
  catch (e) { /* 409 非终态 */ }
  // 轮次选择：默认看最新一轮；roundSel 由「执行历史」chips 传入切换。
  // run_id 严格取所选轮自己的——fail-fast 轮没有运行记录，绝不回退最新轮；
  // 旧数据（无 history）沿用 result 快照的 run_id
  const rounds = t.history || [];
  const sel = rounds.length
    ? rounds[Math.min(Math.max(roundSel || rounds.length, 1), rounds.length) - 1]
    : null;
  const runId = sel?.run_id || (rounds.length ? null
    : (t.result?.run_id || result?.result?.run_id || null));
  let run = null;
  if (runId) {
    try { run = await FAPI.get(`/runs/${runId}`); }   // DB 唯一权威（原则 8）
    catch (e) { /* run 行可能尚未可见 */ }
  }
  let cases = [];
  if (runId) {
    try { cases = (await FAPI.get(`/runs/${runId}/cases`)).items || []; } catch (e) {}
  }
  // 执行历史取自任务行 history（每轮 trigger/当时 spec 快照/结果概要），见「执行历史」卡
  const summaries = (run?.summaries || [])
    .filter(x => (x.dataset || "").startsWith("pytest:"));
  const overall = (run?.summaries || [])
    .find(x => x.metric === "pass_rate" && !x.dataset);
  const totalCases = (run?.total_cases || 0)
    || summaries.reduce((a, x) => a + (x.count || 0), 0)
    || (result?.result?.total_cases ?? 0);
  const errorCases = run?.error_cases ?? result?.result?.error_cases ?? 0;
  const passedCases = cases.filter(x => (x.metrics || []).some(m => m.passed === true));
  const failedCases = cases.filter(x => (x.metrics || []).some(m => m.passed === false));
  const progress = run?.details?.progress || null;
  const runStatus = run?.status || t.status;
  const pct = progress && progress.total
    ? Math.round((progress.done || 0) / progress.total * 100) : null;
  const pp = v => v === null || v === undefined ? "—" : `${(v * 100).toFixed(1)}%`;
  const esc = escHtml;   // 转义统一走 live2.js
  window.__copyStore = window.__copyStore || new Map();
  let __cseq = 0;
  // 「展开全文」id 必须全渲染唯一：曾在 caseBlock 内每例归零，跨用例重复 id
  // （点击展开命中 DOM 第一个同名 pre，全文又被最后渲染的用例覆盖 → 折叠显示
  // A 用例内容、展开变 B 用例内容）；存全文的 Map 也随之每次渲染重建
  window.__expStore = new Map();
  let __eseq = 0;
  const copyBtn = (text, label) => {
    const id = `cc${++__cseq}`;
    window.__copyStore.set(id, text);
    return `<button type="button" class="btn sm" data-copy-id="${id}" style="padding:1px 8px;font-size:11px">${label || "复制"}</button>`;
  };
  if (!window.__ccopyBound) {
    window.__ccopyBound = true;
    document.addEventListener("click", async e => {
      const b = e.target.closest("[data-copy-id]");
      if (!b) return;
      try {
        await navigator.clipboard.writeText((window.__copyStore || new Map()).get(b.dataset.copyId) || "");
        const orig = b.textContent;
        b.textContent = "已复制 ✓";
        setTimeout(() => { b.textContent = orig; }, 1200);
      } catch (err) { b.textContent = "复制失败"; }
    });
  }
  const secHead = (label, btnsHtml) => `<div style="display:flex;align-items:center;gap:8px;margin:10px 0 4px">
    <span style="font-weight:650;font-size:12.5px">${label}</span>${btnsHtml || ""}</div>`;
  const dlBtn = (obj, name) => {
    window.__dlStore = window.__dlStore || new Map();
    const id = `dl${++__cseq}`;
    window.__dlStore.set(id, { obj, name });
    return `<button type="button" class="btn sm" data-dl-id="${id}" style="padding:1px 8px;font-size:11px">下载 .json</button>`;
  };
  if (!window.__dlexpBound) {
    window.__dlexpBound = true;
    document.addEventListener("click", e => {
      const b = e.target.closest("[data-dl-id]");
      if (!b) return;
      const it = (window.__dlStore || new Map()).get(b.dataset.dlId);
      if (!it) return;
      const blob = new Blob([JSON.stringify(it.obj, null, 2)], { type: "application/json" });
      const a2 = document.createElement("a");
      a2.href = URL.createObjectURL(blob);
      a2.download = it.name;
      a2.click();
      URL.revokeObjectURL(a2.href);
    });
  }
  if (!window.__nsexpBound) {
    window.__nsexpBound = true;
    document.addEventListener("click", e => {
      const b = e.target.closest(".ns-exp");
      if (!b) return;
      const pre = document.getElementById(b.dataset.expId);
      if (!pre) return;
      const store = window.__expStore || new Map();
      const full = store.get(b.dataset.expId) || "";
      if (pre.dataset.expanded === "1") {
        pre.textContent = full.slice(0, 300) + "…";
        pre.style.maxHeight = "120px";
        pre.dataset.expanded = "";
        b.textContent = b.textContent.replace("收起", `展开全文（共 ${full.length} 字符）`);
      } else {
        pre.textContent = full;
        pre.style.maxHeight = "none";
        pre.dataset.expanded = "1";
        b.textContent = "收起";
      }
    });
  }
  const caseBlock = (x, isFailed) => {
    const st = isFailed ? "failed" : "passed";
    const out = (typeof x.output === "object" && x.output !== null) ? x.output : {};
    const errLines = (x.error || "").split(String.fromCharCode(10));
    const errFirst = errLines[0] || "";
    const COLLAPSE_AT = 300;
    // 长文本折叠 pre（展开全文）：请求体/响应体/断言期望共用
    const dumpPre = text => {
      if (text.length <= COLLAPSE_AT)
        return `<pre style="white-space:pre-wrap;font-size:12px;margin:0">${esc(text)}</pre>`;
      const id = `ex${++__eseq}`;
      window.__expStore.set(id, text);
      return `<pre id="${id}" style="white-space:pre-wrap;font-size:12px;max-height:120px;overflow:hidden;margin:0">${esc(text.slice(0, COLLAPSE_AT))}…</pre>
        <div style="margin-top:2px"><button type="button" class="btn sm ns-exp" data-exp-id="${id}" style="padding:1px 8px;font-size:11px">展开全文（共 ${text.length} 字符）</button></div>`;
    };
    const pretty = v => v === null || v === undefined ? null : JSON.stringify(v, null, 2);
    // —— 请求头/请求体/响应体原样展示（引擎 _request 留痕 / 响应去留痕键），不再拆字段 ——
    const { _request: reqMeta, ...respRaw } = out;
    const rawObj = (typeof x.raw === "object" && x.raw !== null) ? x.raw : {};
    // 请求体 = 实际发出的完整 payload（model + messages + 全部扩展参数）；
    // 旧 run 无留痕时回退 messages ⊕ raw.params 快照（缺 model，仅近似还原；
    // _ 前缀控制键不进快照，与真实 payload 口径一致）
    const reqObj = (reqMeta && typeof reqMeta === "object") ? reqMeta.body
      : (x.input == null && !rawObj.params ? null
        : { ...(Array.isArray(x.input) ? { messages: x.input } : {}),
            ...Object.fromEntries(Object.entries(rawObj.params || {}).filter(([k]) => !k.startsWith("_"))) });
    const reqText = typeof reqObj === "string" ? reqObj : (pretty(reqObj) ?? "—");
    const reqHead = (reqMeta && typeof reqMeta === "object")
      ? `${reqMeta.method || "POST"} ${reqMeta.url || ""}`
      : "旧数据无请求留痕，以 messages ⊕ params 快照代替";
    // 请求头：留痕为实际发送的合并结果（默认鉴权 ⊕ 自定义，敏感值已由引擎打码）
    const hdr = (reqMeta && typeof reqMeta === "object" && reqMeta.headers
      && typeof reqMeta.headers === "object") ? reqMeta.headers : null;
    const hdrText = hdr ? Object.entries(hdr).map(([k, v]) => `${k}: ${v}`).join("\n") : "";
    const respText = typeof x.output === "string" ? x.output
      : x.output == null ? "（无响应记录）" : (pretty(respRaw) ?? "—");
    const expText = pretty(x.expected);
    // —— 断言判定：期望 vs 实际逐行对照。实际值从响应提取，口径同引擎 check()：
    // 预期错误模式取 _status/_body，正常响应取 choices[0].message 与流式控制键 ——
    const choice0 = (out.choices || [])[0] || {};
    const msg0 = choice0.message || {};
    const content0 = (msg0.content || "").trim();
    const tools0 = msg0.tool_calls || [];
    const fn0 = (tools0[0] || {}).function || {};
    const _OPS = { eq: "==", neq: "!=", gte: ">=", lte: "<=", in: "∈" };
    const clip = (v, n = 80) => {
      const s = typeof v === "string" ? v : (JSON.stringify(v) ?? String(v));
      return s.length > n ? `${s.slice(0, n)}…` : s;
    };
    const expObj = (typeof x.expected === "object" && x.expected !== null) ? x.expected : {};
    const actualBody = out._body !== undefined ? out._body : out;
    const rows = [];
    const addRow = (name, expect, actual) => rows.push({ name, expect, actual });
    for (const [k, v] of Object.entries(expObj)) {
      if (k === "ignore_case") continue;   // 修饰键，并入包含关键词行标注
      if (k === "http_status") addRow("HTTP 状态码",
        typeof v === "object" ? `${_OPS[Object.keys(v)[0]] || "?"} ${Object.values(v)[0]}` : `== ${v}`,
        out._status != null ? String(out._status) : "—");
      else if (k === "contains") addRow(`包含关键词${expObj.ignore_case ? "（忽略大小写）" : ""}`,
        clip(v), content0 || "（空）");
      else if (k === "not_contains") addRow("不含关键词", clip(v), content0 || "（空）");
      else if (k === "nonempty") addRow("回复非空", "非空", content0 ? `非空（${content0.length} 字符）` : "（空）");
      else if (k === "no_garbled") addRow("无乱码", "无乱码", content0 || "（空）");
      else if (k === "no_repeat") addRow("无复读", "无复读", content0 || "（空）");
      else if (k === "no_leak") addRow("无泄漏", `不出现 ${clip(v, 40)}`, content0 || "（空）");
      else if (k === "finish_reason") addRow("finish_reason", String(v),
        choice0.finish_reason != null ? String(choice0.finish_reason) : "—");
      else if (k === "chunks_min") addRow("流式块数", `>= ${v}`, out._chunks != null ? String(out._chunks) : "—");
      else if (k === "sse_done") addRow("SSE 收尾 data: [DONE]", String(v), String(out._sse_ok));
      else if (k === "json") addRow("JSON 结构断言", clip(v, 70), clip(respRaw, 70));
      else if (k === "resp") addRow("响应体字段断言", clip(v, 70), clip(actualBody, 70));
      else if (k === "tool") addRow("指定工具被调用", String(v), fn0.name || "（未发起工具调用）");
      else if (k === "no_tool") addRow("不发起工具调用", "无工具调用",
        tools0.length ? tools0.map(t => (t.function || {}).name || "?").join(", ") : "无工具调用");
      else if (k === "tool_args_contains") addRow("工具参数包含", clip(v),
        fn0.arguments != null ? clip(fn0.arguments) : "（无参数）");
    }
    const rowHtml = r => `<tr>
        <td class="dim" style="padding:3px 10px 3px 0;white-space:nowrap;font-size:12px;vertical-align:top">${esc(r.name)}</td>
        <td class="mono" style="padding:3px 14px 3px 0;font-size:12px;vertical-align:top;width:38%;word-break:break-all">${esc(String(r.expect))}</td>
        <td class="mono" style="padding:3px 0;font-size:12px;word-break:break-all">${esc(String(r.actual))}</td>
      </tr>`;
    const assertSec = rows.length ? `
      ${secHead("断言判定", `<span class="badge ${st}"><i></i>${isFailed ? "不通过" : "通过"}</span> ${copyBtn(expText ?? "", "复制断言配置")}`)}
      <table style="width:100%;border-collapse:collapse">
        <thead><tr>
          <th style="text-align:left;padding:2px 10px 4px 0;font-weight:600;color:var(--tx3);font-size:12px">断言项</th>
          <th style="text-align:left;padding:2px 14px 4px 0;font-weight:600;color:var(--tx3);font-size:12px;width:38%">期望</th>
          <th style="text-align:left;padding:2px 0 4px;font-weight:600;color:var(--tx3);font-size:12px">实际</th>
        </tr></thead>
        <tbody>${rows.map(rowHtml).join("")}</tbody>
      </table>` : "";
    const statusTag = out._status != null ? ` · HTTP ${out._status}` : "";
    // —— 失败日志：首行醒目，引擎原始日志全文直接展示（不再折叠） ——
    const logSec = isFailed && x.error ? `
      <div style="margin-top:10px;padding:8px 10px;border-left:3px solid var(--bad);background:color-mix(in srgb,var(--bad) 8%,transparent);border-radius:0 8px 8px 0">
        <div style="font-weight:650;font-size:12.5px;color:var(--bad);margin-bottom:4px">引擎原始日志</div>
        <div class="mono" style="font-size:12px;color:var(--bad);word-break:break-all">${esc(errFirst)}</div>
        <pre style="font-size:11px;white-space:pre-wrap;margin:6px 0 0">${esc(x.error)}</pre>
      </div>` : "";
    // —— 列表行：状态徽标 + 行 id + 用例 id/类型/等级标签 + 失败首行 + 编辑/详情 ——
    // （用例 id/类型/等级来自执行记录 metadata = 脚本行 case_id/type/level 透传）
    const md = (typeof x.metadata === "object" && x.metadata) || {};
    const tags = [
      md.case_id ? `<span class="tag mono" title="外部用例 id">用例 ${esc(md.case_id)}</span>` : "",
      md.type ? `<span class="tag" title="行类型">${esc(md.type)}</span>` : "",
      md.level ? `<span class="tag" title="优先级等级">${esc(md.level)}</span>` : "",
    ].join("");
    const errSnippet = isFailed && errFirst
      ? `<span class="mono" title="${esc(x.error || "")}" style="flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--bad);font-size:11.5px">${esc(errFirst)}</span>`
      : `<span style="flex:1"></span>`;
    return `<div class="case-item" style="border:1px solid ${isFailed ? "color-mix(in srgb,var(--bad) 35%,transparent)" : "var(--line)"};border-radius:10px;margin-top:8px;overflow:hidden">
      <div class="case-item-head" data-case-toggle style="display:flex;align-items:center;gap:8px;padding:8px 12px;cursor:pointer;font-size:12.5px">
        <span class="badge ${st}"><i></i>${isFailed ? "失败" : "通过"}</span>
        <span class="mono" style="font-weight:650">${esc(x.external_key || "—")}</span>
        ${tags}
        ${errSnippet}
        <button type="button" class="btn sm" data-case-edit="${esc(x.external_key || "")}"
          data-case-scenario="${esc(x.dataset || "")}" title="打开所属脚本并定位此行编辑">编辑</button>
        <button type="button" class="btn sm" data-case-toggle>详情</button>
      </div>
      <div class="case-item-body" hidden style="border-top:1px solid var(--line);padding:8px 12px">
        ${assertSec}
        ${hdrText ? secHead("请求头", copyBtn(hdrText, "复制请求头")) + dumpPre(hdrText) : ""}
        ${secHead(`请求体 · ${esc(reqHead)}`, [copyBtn(reqText, "复制请求体"), dlBtn(reqObj, `${x.external_key || "case"}-request.json`)].join(""))}
        ${dumpPre(reqText)}
        ${secHead(`响应体${statusTag}`, [copyBtn(respText, "复制响应体"), dlBtn(respRaw, `${x.external_key || "case"}-response.json`)].join(""))}
        ${dumpPre(respText)}
        ${logSec}
      </div>
    </div>`;
  };
  // —— 执行历史（同一任务的每轮运行；紧凑单行：轮次 pills + 该轮 spec 复制）——
  const dot = s => `<span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:${
    s === "succeeded" ? "var(--ok)" : s === "failed" ? "var(--bad)"
      : s === "running" ? "var(--ac)" : "var(--tx3)"};margin:0 4px 0 0;vertical-align:1px"></span>`;
  const stTxt = s => s === "running" ? "进行中" : s === "succeeded" ? "通过"
    : s === "failed" ? "失败" : s === "cancelled" ? "已停止" : s === "stale" ? "已中断" : s;
  const rateOf = r => r.pass_rate != null ? pp(r.pass_rate) : "—";
  const roundChips = rounds.map(r => `
      <span class="chip${sel && r.n === sel.n ? " on" : ""}" style="cursor:pointer"
        title="查看该轮结果（spec 快照随轮留档）"
        onclick="showLiveTask('${taskId}', ${r.n})">#${r.n}
        ${r.trigger === "retry" ? "重试" : "运行"} ${fapiShortTime(r.started_at).slice(6)}
        ${dot(r.status)}${stTxt(r.status)} ${rateOf(r)}</span>`).join("");
  const histSec = `
    <section class="card" style="margin-bottom:14px;padding:10px 14px">
      <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">
        <span style="font-weight:650;font-size:12.5px;color:var(--tx3)">执行历史</span>
        ${roundChips || '<span class="faint" style="font-size:12px">尚无运行记录 —— 发起运行后在此累积每一轮</span>'}
        <span style="flex:1"></span>
        ${sel ? copyBtn(JSON.stringify(sel.spec || {}, null, 2), "复制该轮 spec") : ""}
      </div>
      ${sel && sel.error ? `<div class="hint" style="color:var(--bad);margin:6px 0 0">该轮错误：${esc(sel.error)}</div>` : ""}
      ${t.retry_of ? `<div class="hint" style="margin:6px 0 0">旧版重试链来源：<span class="mono">${esc(String(t.retry_of))}</span>（兼容保留，新语义重跑不再产生）</div>` : ""}
    </section>`;
  detail.innerHTML = `
    <header class="topbar">
      <div><h1>${escHtml(t.name || "(未命名)")} ${taskBadge(t.status)}</h1>
      <div class="crumb">task ${t.id} · 引擎 pytest · 被测版本 ${t.spec?.version || "未标注"}
      · 模型 ${t.spec?.target?.profile || t.spec?.target?.model || "-"}${rounds.length ? ` · 第 ${sel?.n || rounds.length} / ${rounds.length} 轮` : ""}${t.retry_of ? ` · 旧链 ← ${esc(String(t.retry_of)).slice(0, 8)}` : ""}</div></div>
      <div class="actions">
        ${["pending", "succeeded", "failed", "cancelled", "stale"].includes(t.status) ? `<button class="btn" onclick="fapiTaskAction('run', '${taskId}')">${t.status === "pending" ? "运行" : "重跑"}</button>
        <button class="btn" onclick="fapiTaskAction('edit', '${taskId}')" title="改配置（非运行中可改）：模型/版本/场景/用例过滤/超时">编辑</button>` : ""}
        ${["running", "stopping"].includes(t.status) ? `<button class="btn" onclick="fapiTaskAction('stop', '${taskId}')">停止</button>` : ""}
        ${["succeeded", "failed", "cancelled", "stale"].includes(t.status) ? `<button class="btn" onclick="fapiTaskAction('retry', '${taskId}')" title="同配置立即再跑一轮（同一任务，追加执行历史）">重试</button>` : ""}
        <button class="btn" onclick="fapiTaskAction('copy', '${taskId}')" title="复制为独立分支变体（同 spec 新任务）并打开编辑">复制</button>
        <button class="btn" onclick="showLiveTask('${taskId}')">刷新</button>
        <button class="btn" onclick="backToRuns()">← 任务列表</button></div>
    </header>
    <section class="stats" style="grid-template-columns:repeat(4,1fr);margin-bottom:14px">
      <div class="stat"><div class="label"><i></i>总用例通过率</div>
        <div class="value">${overall?.value != null ? pp(overall.value) : "—"}</div></div>
      <div class="stat"><div class="label"><i style="background:var(--ok)"></i>成功用例</div>
        <div class="value">${passedCases.length}</div><span class="delta up">▲ 断言通过</span></div>
      <div class="stat"><div class="label"><i style="background:var(--bad)"></i>失败用例</div>
        <div class="value">${failedCases.length}</div><span class="delta down">▼ 断言不通过</span></div>
      <div class="stat"><div class="label"><i style="background:var(--ac2)"></i>执行错误</div>
        <div class="value">${errorCases}</div><span class="delta flat">超时/连接等未跑成</span></div>
    </section>
    ${histSec}
    <section class="card" style="margin-bottom:14px">
      <div class="card-head"><div><h3>场景级汇总</h3>
        <div class="sub">每场景一行 pass_rate · 来自 run 指标摘要（DB 权威）</div></div></div>
      <table class="sumtable">
        <thead><tr><th>场景</th><th>pass_rate</th><th>N</th><th>阈值判定</th></tr></thead>
        <tbody>${summaries.length ? summaries.map(x => `<tr>
          <td class="mono">${(x.dataset || "").replace("pytest:", "")}</td>
          <td>${passCell((x.value ?? 0) * 100)}</td><td class="num">${x.count || 0}</td>
          <td>${x.passed === null || x.passed === undefined
            ? '<span class="faint">—</span>' : badge(x.passed ? "passed" : "failed")}</td></tr>`).join("")
          : '<tr><td colspan="4" class="ds-empty">暂无场景级摘要</td></tr>'}</tbody>
      </table>
    </section>
    <section class="card">
      <div class="card-head"><div><h3>用例明细（原始输入输出）</h3>
        <div class="sub">成功/失败分组列表 · 行 id + 用例 id/类型/等级标签 · 「编辑」直达脚本行（改完保存即生效）·「详情」展开原始输入输出</div></div></div>
      <div class="detail-stack">
        ${failedCases.length ? `<div class="seg-title" style="margin-top:0">失败用例（${failedCases.length}）</div>
          ${failedCases.map(x => caseBlock(x, true)).join("")}` : ""}
        ${passedCases.length ? `<div class="seg-title">成功用例（${passedCases.length}）</div>
          ${passedCases.map(x => caseBlock(x, false)).join("")}` : ""}
        ${!cases.length ? '<div class="ds-empty">该 run 无用例级记录（旧版本运行）—— 场景级结果见上方汇总表</div>' : ""}
      </div>
    </section>`;
}

function backToRuns() { showPage("runs"); }

/* ------------------------------------------------- 版本结果库 + 质量大屏 */
async function wireResults() {
  const data = await FAPI.get("/functional/version-results");
  fapiSetNavCount("vresults", data.count);
  const rows = data.items;
  const counts = {};
  document.getElementById("vrTbl").innerHTML = `
    <thead><tr><th>版本</th><th>模型</th><th>来源 run</th><th>规模</th><th>总体通过率</th>
    <th>维度明细</th><th>归档时间</th><th style="text-align:right">操作</th></tr></thead>
    <tbody>${rows.length ? rows.map(v => {
      const d = v.details || {};
      const key = `${v.model}`;
      counts[key] = (counts[key] || 0) + 1;
      return `<tr>
      <td class="mono">${v.version}</td><td class="dim">${v.model}</td>
      <td class="dim">${(d.run_name || "-")}</td>
      <td class="dim">${Object.keys(d.dimensions || {}).length} 维度 · ${d.cases ?? "?"} 例</td>
      <td>${passCell((d.overall ?? 0) * 100)}</td>
      <td class="dim mono" style="font-size:11px;white-space:normal">
        ${Object.entries(d.dimensions || {}).map(([k, x]) => `${k} ${(x * 100).toFixed(1)}%`).join(" · ")}</td>
      <td class="dim">${fapiShortTime(v.updated_at)}</td>
      <td style="text-align:right"><button class="btn sm" data-board="${v.model}"
        data-target="${v.version}">大屏</button></td></tr>`;
    }).join("") : '<tr><td colspan="8" class="ds-empty">后端暂无归档 —— 功能任务 spec 带 version 执行后自动归档</td></tr>'}</tbody>`;
  document.querySelector('main.page-host [data-page="vresults"] .foot-note span').textContent =
    `真实后端 · 共 ${rows.length} 条归档 · 分析口径只读本库`;
  document.querySelectorAll("#vrTbl [data-board]").forEach(btn =>
    btn.addEventListener("click", () => renderBoard(btn.dataset.board, btn.dataset.target)));

  // 质量大屏：模型 chips + 目标/基线版本选择器真实化
  const models = [...new Set(rows.map(r => r.model))];
  const versions = [...new Set(rows.map(r => r.version))];   // updated_at 倒序
  const boardSec = document.querySelector('main.page-host [data-page="board"]');
  const boardTbl = document.getElementById("boardTbl");
  if (!models.length || !versions.length) {
    boardTbl.innerHTML =
      '<tr><td class="ds-empty">后端暂无归档 —— 功能任务 spec 带 version 执行后自动归档</td></tr>';
    return;
  }
  const targetSel = boardSec.querySelectorAll("select.in")[0];
  const baselineSel = boardSec.querySelectorAll("select.in")[1];
  targetSel.innerHTML = versions.map(v => `<option>${v}</option>`).join("");
  baselineSel.innerHTML = versions.map(v => `<option>${v}</option>`).join("");
  targetSel.selectedIndex = 0;                                // 目标 = 最新
  baselineSel.selectedIndex = Math.min(1, versions.length - 1);
  const chipRow = boardSec.querySelector(".chips");
  const gapLabel = chipRow.querySelector(".ds-flabel.gap");
  chipRow.querySelectorAll(".chip").forEach(c => c.remove());
  const refreshBoard = () => {
    const model = chipRow.querySelector(".chip.on")?.dataset.model || models[0];
    renderBoard(model, targetSel.value, baselineSel.value);
  };
  models.forEach((m, i) => {
    const chip = document.createElement("span");
    chip.className = "chip" + (i === 0 ? " on" : "");
    chip.dataset.model = m;
    chip.textContent = m;
    chip.style.cursor = "pointer";
    chip.addEventListener("click", () => {
      chipRow.querySelectorAll(".chip").forEach(x => x.classList.remove("on"));
      chip.classList.add("on");
      refreshBoard();
    });
    gapLabel ? chipRow.insertBefore(chip, gapLabel) : chipRow.appendChild(chip);
  });
  targetSel.addEventListener("change", refreshBoard);
  baselineSel.addEventListener("change", refreshBoard);
  await refreshBoard();
}

async function renderBoard(model, target, baseline) {
  let board;
  try {
    board = await FAPI.get(`/functional/quality/board?model=${encodeURIComponent(model)}` +
      `&target=${encodeURIComponent(target)}&baseline=${encodeURIComponent(baseline)}`);
  } catch (err) {
    document.getElementById("boardTbl").innerHTML =
      `<tr><td class="ds-empty">${err.message}</td></tr>`;
    return;
  }
  const kpi = board.kpi;
  const cards = document.querySelectorAll(
    'main.page-host [data-page="board"] section.stats .stat');
  const set = (i, val, deltaText, cls) => {
    const c = cards[i];
    if (!c) return;
    c.innerHTML = `${c.querySelector(".label").outerHTML}
      <div class="value">${val}</div><span class="delta ${cls}">${deltaText}</span>`;
  };
  const pp = v => v === null ? "—" : `${(v * 100).toFixed(1)}%`;
  const pd = v => v === null ? "—" : `${v >= 0 ? "▲ +" : "▼ "}${(v * 100).toFixed(1)}pp`;
  set(0, pp(kpi.target_overall), `${pd(kpi.delta)} vs ${baseline}`, kpi.delta >= 0 ? "up" : "down");
  set(1, String(kpi.degraded), "▼ 劣化维度数", "down");
  set(2, String(kpi.improved), "▲ 优化维度数", "up");
  set(3, `${kpi.dimensions} / ${board.dimensions[0]?.cases ?? "?"}`, "评估维度 / 子用例", "flat");

  document.getElementById("boardTbl").innerHTML = `
    <thead><tr><th>评估维度（场景）</th><th>${baseline}（基线）</th><th>${target}</th>
    <th>较基线 Δ</th><th>判定</th></tr></thead>
    <tbody>${board.dimensions.map(d => `<tr>
      <td class="mono" style="font-size:12px">${d.scenario}</td>
      <td>${d.baseline === null ? '<span class="faint">—</span>' : passCell(d.baseline * 100)}</td>
      <td>${d.target === null ? '<span class="faint">—</span>' : passCell(d.target * 100)}</td>
      <td class="num mono" style="color:${d.verdict === "劣化" ? "var(--bad)" : d.verdict === "达标" ? "var(--ok)" : "var(--tx3)"}">
        ${d.delta === null ? "—" : `${d.delta >= 0 ? "▲ +" : "▼ "}${(d.delta * 100).toFixed(1)}pp`}</td>
      <td>${fapiVerdictBadge(d.verdict)}</td></tr>`).join("")}</tbody>`;

  const degraded = board.dimensions.filter(d => d.verdict === "劣化");
  document.getElementById("boardVerdict").innerHTML =
    (degraded.length
      ? degraded.map(d => `<div style="border:1px solid color-mix(in srgb,var(--bad) 35%,transparent);
          border-radius:10px;padding:10px 14px"><div class="mono" style="font-size:12.5px;color:var(--bad)">
          ✕ ${d.scenario}（▼ ${(Math.abs(d.delta) * 100).toFixed(1)}pp）</div>
          <div class="dim" style="font-size:12.5px;margin-top:4px">${d.baseline} → ${d.target}
          —— 建议阻断发布并回溯变更</div></div>`).join("")
      : `<div style="border:1px solid color-mix(in srgb,var(--ok) 35%,transparent);border-radius:10px;
          padding:10px 14px;color:var(--ok);font-size:13px">✓ 无劣化维度</div>`)
    + `<div class="dim" style="font-size:12.5px">总体 ${pp(kpi.target_overall)}
       （${pd(kpi.delta)} vs ${baseline}）· 劣化 ${kpi.degraded} · 优化 ${kpi.improved}
       · 新增 ${kpi.added} · 缺失 ${kpi.missing}</div>`;

  // 图表：全部版本趋势 + 各版本总体柱状（基于该模型全部归档）
  const all = (await FAPI.get("/functional/version-results",
    { model: encodeURIComponent(model) })).items;
  const vers = [...all].sort((a, b) => (a.updated_at < b.updated_at ? -1 : 1));
  const dims = [...new Set(vers.flatMap(v => Object.keys(v.details?.dimensions || {})))];
  window.chartFtTrend = t => {
    const c = t.charts;
    return {
      grid: { left: 6, right: 14, top: 28, bottom: 4, containLabel: true },
      tooltip: { trigger: "axis", backgroundColor: c.tipBg, borderColor: c.tipBorder,
        textStyle: { color: c.tipTx, fontSize: 12 }, valueFormatter: v => v + "%" },
      legend: { top: 0, textStyle: { color: c.axis, fontSize: 11 } },
      xAxis: { type: "category", data: vers.map(v => v.version), ...baseAxis(c),
        splitLine: { show: false }, axisLabel: { color: c.axis, fontWeight: 600 } },
      yAxis: { type: "value", min: 0, max: 100, ...baseAxis(c),
        axisLabel: { color: c.axis, formatter: "{value}%" } },
      series: dims.slice(0, 6).map((d, i) => ({
        name: d, type: "line", symbol: "circle", symbolSize: 7,
        lineStyle: { width: 2.2 }, itemStyle: { color: c.radar[i % c.radar.length] },
        data: vers.map(v => ((v.details?.dimensions || {})[d] ?? 0) * 100) })),
    };
  };
  window.chartFtModel = t => {
    const c = t.charts;
    return {
      grid: { left: 6, right: 14, top: 28, bottom: 4, containLabel: true },
      tooltip: { trigger: "axis", backgroundColor: c.tipBg, borderColor: c.tipBorder,
        textStyle: { color: c.tipTx, fontSize: 12 }, valueFormatter: v => v + "%" },
      legend: { top: 0, textStyle: { color: c.axis, fontSize: 11 } },
      xAxis: { type: "category", data: vers.map(v => v.version), ...baseAxis(c),
        splitLine: { show: false }, axisLabel: { color: c.axis } },
      yAxis: { type: "value", min: 0, max: 100, ...baseAxis(c),
        axisLabel: { color: c.axis, formatter: "{value}%" } },
      series: [{ name: "总体通过率", type: "bar", barWidth: 26,
        itemStyle: { color: c.radar[0], borderRadius: [3, 3, 0, 0] },
        data: vers.map(v => ((v.details?.overall ?? 0) * 100)) }],
    };
  };
  autoCharts();
}

/* ---------------------------------------------------------- 创建任务提交 */
/* ------------------------------------- 被测版本（全局版本注册表 = 公共 · 版本管理） */
let FT_VERSIONS = [];   // [{version, description}]（公共 · 版本管理创建，与模型无关）

/* 抽屉被测版本下拉：全量列出登记版本（版本与模型无关，发起处与目标模型自由组合）；
   重建选项时保留现选中值（不闪跳）。注册表为空给引导选项，必填校验在提交处。 */
function renderTaskVersions(prefer) {
  const sel = document.getElementById("ftVerSel");
  if (!sel) return;
  const prev = prefer !== undefined ? prefer : sel.value;
  sel.innerHTML = FT_VERSIONS.map(v =>
    `<option value="${v.version}">${v.version}` +
    `${v.description ? `（${v.description}）` : ""}</option>`).join("")
    || '<option value="">暂无版本 —— 点「＋ 新版本」就地创建</option>';
  if (prev && [...sel.options].some(o => o.value === prev)) sel.value = prev;
}

async function refreshTaskVersions(prefer) {
  FT_VERSIONS = (await FAPI.get("/versions")).items;
  renderTaskVersions(prefer);
}

/* 「＋ 新版本」就地创建：版本与模型无关，只登记版本号；
   创建落库后立即回填下拉并选中——发起处无需再去「公共 · 版本管理」页 */
async function ftCreateVersion() {
  const version = (prompt("输入版本号（如 v1.3.0）：") || "").trim();
  if (!version) return;
  try {
    await FAPI.post("/versions", { version });
    fapiToast(`版本已创建：${version}（「公共 · 版本管理」页可管理）`);
    await refreshTaskVersions(version);
  } catch (err) { fapiToast(`创建失败：${err.message}`, true); }
}

async function wireTaskVersions() {
  const btn = document.getElementById("ftVerNewBtn");
  if (btn && !btn.dataset.liveBound) {
    btn.dataset.liveBound = "1";
    btn.addEventListener("click", ftCreateVersion);
  }
  await refreshTaskVersions();
}

function bindTaskSubmit() {
  const drawer = document.getElementById("newRunModal");
  const submit = drawer.querySelector(".drawer-foot .btn.primary");
  submit.addEventListener("click", async () => {
    const secs = drawer.querySelectorAll(".form-sec");
    const name = secs[0].querySelector("input.in").value.trim();
    const modelKey = document.getElementById("ftModelSel").value;
    const version = document.getElementById("ftVerSel").value.trim();   // 全局版本注册表下拉（公共 · 版本管理创建）
    if (!version) { fapiToast("请选择被测版本（没有就点「＋ 新版本」就地创建）", true); return; }
    const timeout = secs[2].querySelector("input.in").value.trim();
    const scenes = SCENES.map(s => s[0]).filter(sc => ftSceneState(sc) !== "none");
    if (!scenes.length) { fapiToast("请至少选择一个评估场景", true); return; }
    let target;
    if (modelKey === "__custom__") {
      const pane = document.getElementById("ftModelCustom");
      const inputs = pane.querySelectorAll("input.in:not([data-keyenv])");
      target = { type: "openai_compatible", base_url: inputs[0].value.trim(),
                 model: inputs[1].value.trim() };
      const kSel = pane.querySelector("[data-keysel]");
      const keyEnv = kSel ? keyEnvValue(kSel) : "";
      if (keyEnv) target.api_key_env = keyEnv;
      if (!target.base_url || !target.model) {
        fapiToast("自定义端点需填写 模型访问 url 与 模型名称", true);
        return;
      }
    } else {
      const src = MODEL_SOURCES.find(s => s.key === modelKey);
      if (!src) { fapiToast("目标模型已失效，请重新选择", true); return; }
      target = { ...src.target };
    }
    const spec = {
      module: "functional", engine: "pytest",
      version, target,
      tools: {},
    };
    if (name) spec.run_name = name;
    if (timeout) spec.extra = { timeout: Number(timeout) };
    // 场景全部在托管根（builtin 已移除）：tests_dir 缺省即托管根，不传亦可
    spec.tools.tests_dir = "data/functional/scenarios";
    // 场景级选择 → tools.datasets（只跑勾选的场景，适配器按名过滤未知名报错）
    spec.tools.datasets = scenes;
    // 部分勾选的场景 → tools.cases 限定用例（场景内用例 id，不带场景前缀）
    const partialScenes = scenes.filter(sc => ftSceneState(sc) === "part");
    if (partialScenes.length) {
      spec.tools.cases = [...ftSel]
        .filter(s => partialScenes.includes(s.split(".")[0]))
        .map(s => s.split(".").slice(1).join("."));
    }
    try {
      const task = await FAPI.post("/functional/tasks",
        { name: name || undefined, spec });
      await FAPI.post(`/functional/tasks/${task.id}/run`);
      drawer.hidden = true;
      fapiToast(`任务已发起：${task.id.slice(0, 8)}（运行中，任务列表可查）`);
      await wireTasks();
      showPage("runs");
    } catch (err) { fapiToast(`发起失败：${err.message}`, true); }
  });
}

/* ---------------------------------------------------------- 模型下拉真实化 */
let MODEL_SOURCES = [];   // [{key, label, target}]：key = common:<名> / db:<id> / __custom__

async function wireModels() {
  const [common, db, secrets] = await Promise.all([
    FAPI.get("/common/models").catch(() => ({ items: [] })),
    FAPI.get("/models").catch(() => ({ items: [] })),
    FAPI.get("/secrets").catch(() => ({ items: [] })),
  ]);
  MODEL_SOURCES = [
    ...common.items.map(m => ({
      key: `common:${m.name}`, name: m.name,
      label: `${m.name}${m.model ? ` · ${m.model}` : ""}（预制）`,
      target: { profile: m.name },
    })),
    ...db.items.map(m => ({
      key: `db:${m.id}`, name: m.name,
      label: `${m.name}${m.model ? ` · ${m.model}` : ""}（注册）`,
      target: { type: "openai_compatible", base_url: m.base_url || "",
                model: m.model || "", api_key_env: m.api_key_env || undefined },
    })),
  ];
  const sel = document.getElementById("ftModelSel");
  if (!sel) return;
  sel.innerHTML = MODEL_SOURCES.map(s => `<option value="${s.key}">${s.label}</option>`).join("")
    + '<option value="__custom__">自定义（手填端点）</option>';
  bindModelCustom("ftModelSel", "ftModelCustom");   // 选项重建后重挂自定义面板开关
  const keySel = document.querySelector("#ftModelCustom [data-keysel]");
  if (keySel) wireKeySelect(keySel, secrets.items);   // 自定义端点的鉴权密钥：选已归档 / 手填环境变量
}

/* ---------------- 公共 · 模型管理页（common2.html，DB 网关别名注册） ---------------- */
let MODEL_ROWS = [];
async function wireCommonModelsPage() {
  const [dbData, commonData, secretData] = await Promise.all([
    FAPI.get("/models"),
    FAPI.get("/common/models").catch(() => ({ items: [] })),
    FAPI.get("/secrets").catch(() => ({ items: [] })),
  ]);
  MODEL_ROWS = dbData.items;
  const keyMask = {};
  for (const s of secretData.items) keyMask[s.name] = s.mask;
  const keyCell = env => {
    if (!env) return '<span class="faint">—</span>';
    return `<span class="mono dim">${env}</span>`
      + `<div class="rid">${keyMask[env] ? `🔑 ${keyMask[env]}` : "⚠ 密钥未录入（密钥库可录）"}</div>`;
  };
  // 注册过的模型经 POST /models 已同步进 common.yaml（target.profile 可引用），
  // 预制行剔除同名条目，否则同一模型显示「预制」+「注册」两行（一次新增出现俩）
  const registered = new Set(MODEL_ROWS.map(m => m.name));
  const commonRows = commonData.items.filter(m => !registered.has(m.name)).map(m => `<tr>
      <td class="rname"><b class="mono">${escHtml(m.name)}</b></td>
      <td><span class="tag" style="color:var(--ac)">预制</span></td>
      <td class="mono dim" style="font-size:12px">${escHtml(m.base_url || "—")}</td>
      <td class="mono">${escHtml(m.model || "—")}</td><td>${keyCell(m.api_key_env)}</td>
      <td style="text-align:right"><span class="tag">随 common.yaml 发布</span></td></tr>`);
  const dbRows = MODEL_ROWS.map(m => `<tr>
      <td class="rname"><b class="mono">${escHtml(m.name)}</b></td>
      <td><span class="tag">注册</span></td>
      <td class="mono dim" style="font-size:12px">${escHtml(m.base_url || "—")}</td>
      <td class="mono">${escHtml(m.model || "—")}</td><td>${keyCell(m.api_key_env)}</td>
      <td style="text-align:right"><button class="btn sm danger" data-del-model="${m.id}">删除</button></td></tr>`);
  const tbl = document.getElementById("modelsTbl");
  tbl.innerHTML = `
    <thead><tr><th>名称</th><th>来源</th><th>base_url</th><th>model</th>
    <th>密钥（api_key_env / 库内掩码）</th><th style="text-align:right">操作</th></tr></thead>
    <tbody>${[...commonRows, ...dbRows].join("")
      || '<tr><td colspan="6" class="ds-empty">暂无模型 —— 点「＋ 注册模型」，或编辑 data/common.yaml 录入预制</td></tr>'}</tbody>`;
  if (!tbl.dataset.liveBound) {
    tbl.dataset.liveBound = "1";
    tbl.addEventListener("click", async e => {
      const del = e.target.closest("[data-del-model]");
      if (!del || !confirm("删除该模型注册?")) return;
      try { await FAPI.del(`/models/${del.dataset.delModel}`); }
      catch (err) { fapiToast(`删除失败：${err.message}`, true); return; }
      await wireCommonModelsPage();
    });
  }
  // 密钥库：列表（掩码）+ 录入（PUT，密文落盘）—— 复用上方 secretData
  const secretTbl = document.getElementById("secretTbl");
  if (secretTbl) {
    secretTbl.innerHTML = `
      <thead><tr><th>环境变量名</th><th>密钥（掩码）</th><th style="text-align:right">操作</th></tr></thead>
      <tbody>${secretData.items.length ? secretData.items.map(i => `<tr>
        <td class="mono">${escHtml(i.name)}</td><td class="mono dim">${escHtml(i.mask)}</td>
        <td style="text-align:right"><button class="btn sm danger" data-del-secret="${escHtml(i.name)}">删除</button></td>
      </tr>`).join("") : '<tr><td colspan="3" class="ds-empty">暂无录入的密钥</td></tr>'}</tbody>`;
    if (!secretTbl.dataset.liveBound) {
      secretTbl.dataset.liveBound = "1";
      secretTbl.addEventListener("click", async e => {
        const del = e.target.closest("[data-del-secret]");
        if (!del || !confirm(`删除密钥 ${del.dataset.delSecret}?`)) return;
        try { await FAPI.del(`/secrets/${del.dataset.delSecret}`); }
        catch (err) { fapiToast(`删除失败：${err.message}`, true); return; }
        await wireCommonModelsPage();
      });
    }
  }
  const secretName = document.getElementById("secretName");
  const secretValue = document.getElementById("secretValue");
  const secretHint = document.getElementById("secretHint");
  if (secretName && !secretName.dataset.liveBound) {
    secretName.dataset.liveBound = "1";
    document.getElementById("secretSave").addEventListener("click", async () => {
      const name = secretName.value.trim();
      const value = secretValue.value;
      if (!name || !value) { fapiToast("环境变量名与密钥值均必填", true); return; }
      try {
        const out = await FAPI.req("PUT", `/secrets/${encodeURIComponent(name)}`, { value });
        secretName.value = ""; secretValue.value = "";
        if (secretHint) secretHint.textContent = `已保存：${out.mask}`;
        fapiToast(`密钥 ${name} 已密文落盘（运行时自动解密注入）`);
        await wireCommonModelsPage();
      } catch (err) { fapiToast(`保存失败：${err.message}`, true); }
    });
  }

  // 注册抽屉密钥下拉：已归档密钥（掩码）+ 仅环境变量 + 现场归档；密钥库增删后随 wire 刷新
  const keySel = document.getElementById("mKeySel");
  if (keySel) {
    wireKeySelect(keySel, secretData.items);
    keySel.insertAdjacentHTML("beforeend",
      '<option value="__archive__">＋ 现场归档新密钥（密文落盘）</option>');
    if (!keySel.dataset.arcBound) {
      keySel.dataset.arcBound = "1";
      keySel.addEventListener("change", () => {
        document.getElementById("mKeyArchive").hidden = keySel.value !== "__archive__";
      });
    }
    keySel.dispatchEvent(new Event("change"));   // 选项重建后重同步归档面板/回退框显隐
  }

  const drawer = document.getElementById("modelDrawer");
  if (drawer && !drawer.dataset.liveBound) {
    drawer.dataset.liveBound = "1";
    drawer.querySelector(".drawer-foot .btn.primary").addEventListener("click", async () => {
      // 直属 .field 的输入 = 名称/base_url/model（密钥区输入嵌套在 #mKeyArchive，不在此列）
      const fields = [...drawer.querySelectorAll(".drawer-body > .field > input.in")];
      const type = drawer.querySelector(".drawer-body select.in").value;   // 首个 select = 类型
      const name = fields[0].value.trim();
      let keyEnv = "";
      const keySel = document.getElementById("mKeySel");
      if (keySel.value === "__archive__") {   // 现场归档：先密文落盘再注册
        const an = document.getElementById("mKeyArcName").value.trim();
        const av = document.getElementById("mKeyArcVal").value;
        if (!an || !av) { fapiToast("现场归档需填写密钥名与密钥值", true); return; }
        try { await FAPI.req("PUT", `/secrets/${encodeURIComponent(an)}`, { value: av }); }
        catch (err) { fapiToast(`密钥归档失败：${err.message}`, true); return; }
        keyEnv = an;
      } else {
        keyEnv = keyEnvValue(keySel);
      }
      try {
        const out = await FAPI.post("/models", { name, type,
          base_url: fields[1].value.trim(), model: fields[2].value.trim(),
          api_key_env: keyEnv || undefined, params: {} });
        drawer.hidden = true;
        fapiToast(`模型 ${name} 已注册`
          + (keyEnv && out.key_archived === false
            ? `（⚠ 密钥 ${keyEnv} 未归档且环境变量未设置，运行时将无鉴权——先到密钥库录入）` : ""));
        await wireCommonModelsPage();
      } catch (err) { fapiToast(`注册失败：${err.message}`, true); }
    });
  }
}

/* ---------------- 公共 · 版本管理页（common2.html，全局版本创建 §10.6） ---------------- */
async function wireCommonVersions() {
  const verData = await FAPI.get("/versions");
  const rows = verData.items;
  const tbl = document.getElementById("versionsTbl");
  tbl.innerHTML = `
    <thead><tr><th>版本号</th><th>说明</th><th>创建时间</th>
    <th style="text-align:right">操作</th></tr></thead>
    <tbody>${rows.length ? rows.map(v => `<tr>
      <td class="mono"><b>${v.version}</b></td>
      <td class="dim" style="white-space:normal">${v.description || "—"}</td>
      <td class="dim">${fapiShortTime(v.created_at)}</td>
      <td style="text-align:right"><button class="btn sm danger" data-del-ver="${v.id}">删除</button></td></tr>`).join("")
      : '<tr><td colspan="4" class="ds-empty">暂无版本 —— 下方表单创建后，精度/功能任务发起即可选择被测版本</td></tr>'}</tbody>`;
  if (!tbl.dataset.liveBound) {
    tbl.dataset.liveBound = "1";
    tbl.addEventListener("click", async e => {
      const del = e.target.closest("[data-del-ver]");
      if (!del || !confirm("删除该版本?（历史任务/归档持版本字符串不受影响）")) return;
      try { await FAPI.del(`/versions/${del.dataset.delVer}`); }
      catch (err) { fapiToast(`删除失败：${err.message}`, true); return; }
      await wireCommonVersions();
    });
  }
  // 创建表单：版本与模型无关，只填版本号 + 说明（可选）
  const save = document.getElementById("verSave");
  if (!save.dataset.liveBound) {
    save.dataset.liveBound = "1";
    save.addEventListener("click", async () => {
      const version = document.getElementById("verVersionIn").value.trim();
      const description = document.getElementById("verDescIn").value.trim();
      const hint = document.getElementById("verHint");
      if (!version) { fapiToast("版本号必填", true); return; }
      try {
        await FAPI.post("/versions", { version, description });
        document.getElementById("verVersionIn").value = "";
        document.getElementById("verDescIn").value = "";
        if (hint) hint.textContent = `已创建：${version}`;
        fapiToast(`版本 ${version} 已创建（精度/功能发起可选）`);
        await wireCommonVersions();
      } catch (err) { fapiToast(`创建失败：${err.message}`, true); }
    });
  }
}

/* ---------------- 公共 · 数据集管理页（common2.html：目录 + 路径/在线源设置 + 导入抽屉） ---------------- */
let DS_ROWS = [];    // 本机数据集行：GET /datasets 合并行 + kind:"local"
let DS_BENCH = [];   // evalscope 基准全集行：GET /datasets/online/datasets + kind:"bench"（type 固定 accuracy）
let DS_BENCH_LIVE = false;             // DS_BENCH 已被 live 数据灌过（列举失败时防 mock 演示行冒充真数据）
window.DS_PAGE = { num: 1, size: 15 }; // 目录表分页：num 当前页，size 每页行数（0 = 全部一页铺完）

function dsRef(r) { return `${r.type}:${r.name}`; }
function dsStatsCell(stats) {
  if (!stats || !Object.keys(stats).length) return "—";
  const count = stats.count !== undefined ? `${stats.count} 条` : "";
  const rest = Object.entries(stats).filter(([k]) => k !== "count").slice(0, 3)
    .map(([k, v]) => `${k}=${typeof v === "object" ? JSON.stringify(v).slice(0, 30) : v}`);
  return [count, ...rest].filter(Boolean).join(" · ") || "—";
}
/* 来源私有性（ownership 后端下发：open=开源 / self=自建；与存储位置正交——
   内网直链拉取的自有语料是在线获取，但属自建） */
function dsOwnTag(r) {
  const own = r.ownership || (r.kind === "bench" ? "open" : "self");
  return own === "open"
    ? '<span class="tag t-online">开源</span>'
    : '<span class="tag">自建</span>';
}
/* 能力维度 key → 中文标签（与后端 aite/datasets/catalog.CAPABILITIES 同源） */
const DS_CAPS = { knowledge: "基础知识", math: "数学", code: "代码",
  reasoning: "推理", instruction: "指令遵循", agent: "智能体",
  longcontext: "长文本", safety: "安全" };
function dsCapChips(r) {
  return (r.capabilities || []).map(k =>
    `<span class="tag" title="能力维度">${dsEsc(DS_CAPS[k] || k)}</span>`).join(" ");
}
/* 统一筛选（mock/live 共用）：原生 select 单选 + 关键字搜索；原生下拉选中即自动收起 */
window.DS_FILTERS = { type: "", kind: "", state: "", cap: "", q: "" };
function dsFilterMatch(r, f) {
  if (f.type && r.type !== f.type) return false;
  // 类别：基准菜单行独立；本机行按私有性（开源/自建）分——与存储位置正交
  const own = r.ownership || (r.kind === "bench" ? "open" : "self");
  if (f.kind && (r.kind === "bench" ? f.kind !== "bench" : f.kind !== own)) return false;
  // 存储位置：本机行恒为本地（已落盘）；基准行按是否已下载（未下载=在线）
  const storage = r.kind === "bench" ? (r.local ? "local" : "online") : "local";
  if (f.state && storage !== f.state) return false;
  if (f.cap && !(r.capabilities || []).includes(f.cap)) return false;
  if (f.q) {
    const caps = (r.capabilities || []).map(k => DS_CAPS[k] || k);
    const hay = r.kind === "bench"
      ? [r.name, r.pretty_name, r.dataset_id, (r.tags || []).join(" "), ...caps]
      : [r.name, r.notes, r.path, r.source, ...caps];
    if (!hay.some(v => String(v || "").toLowerCase().includes(f.q))) return false;
  }
  return true;
}
/* 目录全部行：本机在前（后端已按 type,name 排好），evalscope 基准全集在后按名排 */
function dsAllRows() {
  return [...DS_ROWS.filter(r => r.kind !== "bench"),
    ...DS_BENCH.slice().sort((a, b) => String(a.name).localeCompare(String(b.name)))];
}
function dsEsc(s) { return escHtml(s); }   // 转义统一走 live2.js
/* 行内截断单元：超宽省略 + hover 看全文；表格 table-layout:fixed，一律 100% 吃列宽不撑表 */
function dsClip(text) {
  const t = String(text ?? "");
  return `<div style="max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${dsEsc(t)}">${dsEsc(t)}</div>`;
}
/* 分页切片（mock/live 共用）：size 0 = 全部；num 越界自动收拢回合法页 */
function dsPageOf(rows) {
  const size = window.DS_PAGE.size > 0 ? window.DS_PAGE.size : (rows.length || 1);
  const pages = Math.max(1, Math.ceil(rows.length / size));
  window.DS_PAGE.num = Math.min(Math.max(1, window.DS_PAGE.num), pages);
  return { pages, slice: rows.slice((window.DS_PAGE.num - 1) * size, window.DS_PAGE.num * size) };
}
/* 页码条：页数 ≤9 全铺，否则 1 2 … n-1 n n+1 … 末页；右侧每页行数切换。
   单页时不铺页码但保留每页条数下拉（「全部」的逃生口，选页数即恢复分页） */
function dsPagerHtml(pages) {
  const n = window.DS_PAGE.num;
  const btn = (p, label, opts = {}) =>
    `<button class="btn sm${opts.cur ? " primary" : ""}" data-ds-page="${p}"${opts.dis ? " disabled" : ""}>${label}</button>`;
  const sizeSel = `<select class="in" id="dsPageSize" title="每页行数；「全部」一页铺完不翻页" style="width:auto">` +
    [15, 30, 50].map(s => `<option value="${s}"${window.DS_PAGE.size === s ? " selected" : ""}>${s} 条/页</option>`).join("") +
    `<option value="0"${!window.DS_PAGE.size ? " selected" : ""}>全部</option></select>`;
  const nav = pages <= 1 ? "" :
    btn("prev", "‹ 上一页", { dis: n <= 1 }) +
    (() => {
      const marks = [];
      if (pages <= 9) { for (let i = 1; i <= pages; i++) marks.push(i); }
      else {
        const want = [...new Set([1, 2, n - 1, n, n + 1, pages - 1, pages])]
          .filter(p => p >= 1 && p <= pages).sort((a, b) => a - b);
        want.forEach((p, i) => { if (i && p - want[i - 1] > 1) marks.push("…"); marks.push(p); });
      }
      return marks.map(p => p === "…" ? '<span class="dim">…</span>' : btn(p, p, { cur: p === n })).join("");
    })() +
    btn("next", "下一页 ›", { dis: n >= pages }) +
    `<span class="hint">第 ${n} / ${pages} 页</span>`;
  return `<div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap;justify-content:flex-end;margin-top:10px">${nav}${sizeSel}</div>`;
}
function dsRenderTable() {
  const rows = dsAllRows().filter(r => dsFilterMatch(r, window.DS_FILTERS));
  const local = DS_ROWS.filter(r => r.kind !== "bench");
  const opened = local.filter(r => (r.ownership || "self") === "open").length;
  const cnt = document.getElementById("dsFCount");
  if (cnt) cnt.textContent =
    `共 ${rows.length} 行 · 本机 ${local.length}（自建 ${local.length - opened} · 开源 ${opened}）` +
    ` · 基准 ${DS_BENCH.length}（本机已有 ${DS_BENCH.filter(b => b.local).length}）`;
  const { pages, slice } = dsPageOf(rows);
  document.getElementById("dsTbl").innerHTML = `
    <thead><tr><th style="width:7%">类型</th><th style="width:22%">名称</th><th style="width:18%;white-space:nowrap">来源</th><th style="width:15%">规模 / 概要</th><th style="width:18%">path · dataset_id</th><th style="width:20%;white-space:nowrap;text-align:right">操作</th></tr></thead>
    <tbody>${slice.map(r => r.kind === "bench" ? dsBenchRow(r) : dsLocalRow(r)).join("")
      || '<tr><td colspan="6" class="ds-empty">没有符合筛选条件的数据集 —— 调整筛选，或从「导入」区新增</td></tr>'}</tbody>`;
  const pager = document.getElementById("dsPager");
  if (pager) pager.innerHTML = (pages > 1 || !window.DS_PAGE.size) ? dsPagerHtml(pages) : "";
}
function dsLocalRow(r) {
  const ref = encodeURIComponent(dsRef(r));
  const unreg = r.registered === false;
  const originCell = dsOwnTag(r) + (unreg
    ? ' <span class="badge pending" title="本机已有（注册表/目录），尚未登记 catalog；走一次导入即可入册"><i></i>未登记</span>'
    : "");
  const sub = dsCapChips(r) + (r.notes ? ` <span title="${dsEsc(r.notes)}">${dsEsc(r.notes)}</span>` : "");
  const subHtml = sub
    ? `<div class="rid faint" style="max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${sub}</div>`
    : '<div class="rid faint">&nbsp;</div>';
  return `<tr>
  <td><span class="tag">${r.type}</span></td><td class="rname"><b class="mono">${dsEsc(r.name)}</b>${subHtml}</td>
  <td style="white-space:nowrap">${originCell}</td>
  <td class="dim" style="white-space:normal">${r.statsText || dsStatsCell(r.stats)}</td>
  <td class="mono faint" style="font-size:11px">${r.path ? dsClip(r.path) : "—"}</td>
  <td style="text-align:right;white-space:nowrap"><button class="btn sm" data-ds-view="${dsRef(r)}" title="查看该数据集自己的来源/统计/notes">详情</button>
  <button class="btn sm" data-ds-dl="${ref}" title="下载数据集文件">下载</button>
  ${unreg ? "" : `<button class="btn sm danger" data-ds-del="${dsRef(r)}">删除</button>`}</td></tr>`;
}
function dsSubCount(b) { return b.subset_count !== undefined ? b.subset_count : (b.subsets || []).length; }
function dsBenchRow(b) {
  const badge = b.local
    ? `<span class="badge passed" title="本机已有同源/同名数据集，可直接用"><i></i>已有 · ${dsEsc(b.local)}</span>`
    : b.local_like
      ? `<span class="badge pending" title="本机有近似名数据集（可能是同一基准的另一份转换）"><i></i>近似 · ${dsEsc(b.local_like)}</span>`
      : '<span class="dim" title="本机没有：导入后完全离线可用，跑任务时也会自动下载">未下载</span>';
  const capHtml = dsCapChips(b) ||
    (b.tags || []).slice(0, 3).map(t =>
      `<span class="tag" title="evalscope 原生标签（未映射到平台能力维度）">${dsEsc(t)}</span>`).join(" ");
  return `<tr>
  <td><span class="tag">accuracy</span></td>
  <td class="rname"><b class="mono">${dsEsc(b.name)}</b>
    <div class="rid faint" style="overflow:hidden;white-space:nowrap">${b.pretty_name && b.pretty_name !== b.name ? dsEsc(b.pretty_name) : ""}
      ${capHtml}</div></td>
  <td style="white-space:nowrap"><span class="tag t-online">evalscope</span> ${badge}</td>
  <td class="dim" style="white-space:normal">${(b.metrics || []).join(", ") || "—"} · subsets ${dsSubCount(b)}${b.few_shot_num ? ` · few-shot ${b.few_shot_num}` : ""}</td>
  <td class="mono faint" style="font-size:11px">${b.dataset_id ? dsClip(b.dataset_id) : "—"}</td>
  <td style="text-align:right;white-space:nowrap"><button class="btn sm" data-ds-view="bench:${dsEsc(b.name)}" title="基准信息与导入参数">详情</button>
  <button class="btn sm" data-ds-import="${dsEsc(b.name)}" title="打开导入抽屉并预填 dataset id">导入</button></td></tr>`;
}
function dsOpenDetail(ref) {
  if (String(ref).startsWith("bench:")) return dsOpenBenchDetail(String(ref).slice(6));
  const r = DS_ROWS.find(x => dsRef(x) === ref);
  if (!r) return;
  document.getElementById("dsDetailTitle").textContent = `数据集详情 · ${r.type}/${r.name}`;
  const kvs = [
    ["类型", `<span class="tag">${r.type}</span>`],
    ["名称", `<b class="mono">${r.name}</b>`],
    ["登记 id", `<span class="mono faint">${r.id}</span>`],
    ["来源", `${dsOwnTag(r)} <span class="dim">${r.source || "—"}</span>`],
    ["能力维度", dsCapChips(r) || "—"],
    ["version（复现凭证：HF split / 生成 seed）", r.version ? `<span class="tag mono">${r.version}</span>` : "—"],
    ["规模 stats", dsStatsCell(r.stats)],
    ["文件路径", r.path ? `<span class="mono" style="font-size:12px">${r.path}</span>` : "—"],
    ["schema_version", r.schema_version || "1"],
    ["notes", r.notes || "—"],
  ];
  document.getElementById("dsDetailBody").innerHTML =
    `<div class="ds-detail">${kvs.map(([k, v]) =>
      `<div class="ds-kv"><span>${k}</span><div>${v}</div></div>`).join("")}</div>`;
  document.getElementById("dsDetailDl").href =
    `${FAPI.base}/datasets/${encodeURIComponent(ref)}/download`;
  document.getElementById("dsDetailDl").hidden = false;
  document.getElementById("dsDetailDel").dataset.dsDel = ref;
  document.getElementById("dsDetailDel").hidden = false;
  document.getElementById("dsDetail").hidden = false;
}
/* 基准版式详情（bench:<name>）：还不是本机登记，下载/删除不适用，给「去导入」与跳本机 */
function dsOpenBenchDetail(name) {
  const b = DS_BENCH.find(x => x.name === name);
  if (!b) return;
  document.getElementById("dsDetailTitle").textContent = `evalscope 基准 · ${b.name}`;
  const subs = b.subsets || [];
  const subN = dsSubCount(b);
  const localCell = b.local
    ? `<span class="badge passed"><i></i>本机已有</span> <button class="btn sm" data-ds-view="accuracy:${b.local}">查看本机数据集</button>`
    : b.local_like
      ? `<span class="badge pending"><i></i>近似 · ${b.local_like}</span> <button class="btn sm" data-ds-view="accuracy:${b.local_like}">查看近似数据集</button>`
      : '<span class="dim">未下载（导入后完全离线可用；跑任务时未预下载的也会自动下载）</span>';
  const kvs = [
    ["基准名", `<b class="mono">${b.name}</b>`],
    ["pretty_name", b.pretty_name || "—"],
    ["dataset_id", `<span class="mono">${b.dataset_id || "—"}</span>`],
    ["标签", (b.tags || []).map(t => `<span class="tag">${t}</span>`).join(" ") || "—"],
    ["能力维度", dsCapChips(b) || "—"],
    ["指标", `<span class="mono dim">${(b.metrics || []).join(", ") || "—"}</span>`],
    ["subsets", `${subN} 个${subs.length ? `：${subs.slice(0, 6).join("、")}${subN > 6 ? " …" : ""}` : ""}`],
    ["few-shot", String(b.few_shot_num || 0)],
    ["本机状态", localCell],
  ];
  document.getElementById("dsDetailBody").innerHTML =
    `<div class="ds-detail">${kvs.map(([k, v]) =>
      `<div class="ds-kv"><span>${k}</span><div>${v}</div></div>`).join("")}</div>
    <div style="margin-top:10px"><button class="btn primary sm" data-ds-import="${b.name}">导入此基准（预填 dataset id）</button></div>`;
  document.getElementById("dsDetailDl").hidden = true;
  document.getElementById("dsDetailDel").hidden = true;
  document.getElementById("dsDetail").hidden = false;
}
/* 行内/详情「导入」基准：预填 dataset id 与名称，切到导入抽屉「开源基准」模式（dsEntry 在页面内联） */
function dsBenchImport(name) {
  const b = DS_BENCH.find(x => x.name === name);
  if (!b) return;
  if (!dsIsLive()) { dsMockGuard(); return; }
  const set = (id, v) => { const el = document.getElementById(id); if (el && !el.value) el.value = v; };
  document.getElementById("dsHfId").value = b.dataset_id || b.name || "";
  set("dsImpName", b.name || "");
  dsEntry("hf");
  // 能力维度按基准画像预填（dsEntry 已清掉上次打开的残留勾选）
  document.querySelectorAll("#dsCapChips .chip").forEach(c =>
    c.classList.toggle("on", (b.capabilities || []).includes(c.dataset.cap)));
  fapiToast(`已预填 ${b.dataset_id || b.name}：确认 split/采样参数后点「校验并导入」`);
}
function dsDownload(ref) {
  if (!dsIsLive()) { dsMockGuard(); return; }
  location.href = `${FAPI.base}/datasets/${encodeURIComponent(ref)}/download`;
}
/* 目录表格 + 详情抽屉的点击委托（mock/live 一套）：详情类 mock 也可看，写操作 mock 一律 guard */
function dsBindDatasetsTable() {
  [document.getElementById("dsTbl"), document.getElementById("dsDetailBody")]
    .forEach(host => {
      if (!host || host.dataset.dsBound) return;
      host.dataset.dsBound = "1";
      host.addEventListener("click", async e => {
        const view = e.target.closest("[data-ds-view]");
        if (view) { dsOpenDetail(view.dataset.dsView); return; }
        const imp = e.target.closest("[data-ds-import]");
        if (imp) { dsBenchImport(imp.dataset.dsImport); return; }
        const dl = e.target.closest("[data-ds-dl]");
        if (dl) { dsDownload(dl.dataset.dsDl); return; }
        if (!dsIsLive()) { dsMockGuard(); return; }
        const del = e.target.closest("[data-ds-del]");
        if (!del) return;
        if (!confirm(`删除数据集登记 ${del.dataset.dsDel}?（文件保留）`)) return;
        try {
          await FAPI.del(`/datasets/${encodeURIComponent(del.dataset.dsDel)}`);
          fapiToast("已删除登记（数据文件保留）");
        } catch (err) { fapiToast(`删除失败：${err.message}`, true); return; }
        await wireCommonDatasetsPage();
      });
    });
  const pager = document.getElementById("dsPager");
  if (pager && !pager.dataset.dsBound) {
    pager.dataset.dsBound = "1";
    pager.addEventListener("click", e => {
      const b = e.target.closest("[data-ds-page]");
      if (!b || b.disabled) return;
      const p = b.dataset.dsPage;
      window.DS_PAGE.num = p === "prev" ? window.DS_PAGE.num - 1
        : p === "next" ? window.DS_PAGE.num + 1 : Number(p);
      dsRenderTable();
    });
    pager.addEventListener("change", e => {
      if (e.target.id !== "dsPageSize") return;
      window.DS_PAGE.size = Number(e.target.value) || 0;   // 0 = 全部
      window.DS_PAGE.num = 1;
      dsRenderTable();
    });
  }
  const banner = document.getElementById("dsBenchBanner");
  if (banner && !banner.dataset.dsBound) {
    banner.dataset.dsBound = "1";
    banner.addEventListener("click", e => {
      if (!e.target.closest("[data-ds-bench-retry]")) return;
      dsLoadBench().then(dsRenderTable);
    });
  }
}
/* 筛选应用（mock/live 共用，页面下拉 change/搜索 input 都走这里；原生 select 选中即收起） */
function dsApplyFilters() {
  const val = id => ((document.getElementById(id) || {}).value || "").trim();
  window.DS_FILTERS = {
    type: val("dsFType"), kind: val("dsFKind"), state: val("dsFState"),
    cap: val("dsFCap"), q: val("dsFQ").toLowerCase(),
  };
  window.DS_PAGE.num = 1;                                // 换筛选回第 1 页
  dsRenderTable();
}
/* 开源基准全集加载（live 专用）：失败不再清空目录里的基准行，改目录上方横幅给原因 + 重试 */
async function dsLoadBench() {
  try {
    const online = await FAPI.get("/datasets/online/datasets");
    DS_BENCH = (online.items || []).map(b => ({ ...b, kind: "bench", type: "accuracy" }));
    DS_BENCH_LIVE = true;
    dsBenchBanner("");
  } catch (err) {
    if (!DS_BENCH_LIVE) DS_BENCH = [];   // 首载失败：mock 演示行不能冒充真数据，宁缺勿假
    dsBenchBanner(`evalscope 基准全集列举失败（目录暂缺开源基准行）：${err.message}`);
  }
}
/* 失败横幅：非隐藏即显示，带「重试」按钮（点击委托见 dsBindDatasetsTable） */
function dsBenchBanner(msg) {
  const el = document.getElementById("dsBenchBanner");
  if (!el) return;
  el.hidden = !msg;
  el.innerHTML = msg
    ? `<span class="badge failed"><i></i>加载失败</span> <span>${dsEsc(msg)}</span> <button class="btn sm" data-ds-bench-retry>重试</button>`
    : "";
}
async function wireCommonDatasetsPage() {
  dsBindDatasetsTable();
  const benchP = dsLoadBench();          // 与本机列表并行起跑（基准全集冷启动可能要起子进程，慢）
  const data = await FAPI.get("/datasets");
  DS_ROWS = (data.items || []).map(r => ({ ...r, kind: "local" }));
  dsRenderTable();                       // 本机行先出
  await benchP;
  dsRenderTable();
}
async function wireDatasetSettings() {
  const snap = await FAPI.get("/datasets/settings");
  const fill = (id, v) => { const el = document.getElementById(id); if (el) el.value = v || ""; };
  fill("dsSetDir", snap.settings.datasets_dir);
  fill("dsSetCache", snap.settings.cache_dir);
  fill("dsSetHf", snap.settings.hf_endpoint);
  fill("dsSetRemote", snap.settings.remote_url);
  const lockNotes = [];
  if (snap.env_locked.datasets_dir) lockNotes.push("datasets_dir 被 AITE_DATASETS_DIR 覆盖");
  if (snap.env_locked.cache_dir) lockNotes.push("cache_dir 被 HF_HOME 覆盖");
  if (snap.env_locked.hf_endpoint) lockNotes.push("hf_endpoint 被 HF_ENDPOINT 覆盖");
  if (snap.env_locked.remote_url) lockNotes.push("remote_url 被 AITE_DATASET_REMOTE_URL 覆盖");
  if (lockNotes.length) lockNotes.push("环境变量优先于设置文件");
  const lockEl = document.getElementById("dsSetLock");
  if (lockEl) lockEl.textContent = lockNotes.join(" · ");
  const save = document.getElementById("dsSetSave");
  if (save && !save.dataset.liveBound) {
    save.dataset.liveBound = "1";
    save.addEventListener("click", async () => {
      const val = id => document.getElementById(id).value.trim();
      const patch = { hf_endpoint: val("dsSetHf"), remote_url: val("dsSetRemote") };
      if (val("dsSetDir")) patch.datasets_dir = val("dsSetDir");
      if (val("dsSetCache")) patch.cache_dir = val("dsSetCache");
      try {
        await FAPI.put("/datasets/settings", patch);
        fapiToast("数据集设置已保存（上传/导入/下载即时走新路径）");
        await wireDatasetSettings();
      } catch (err) { fapiToast(`保存失败：${err.message}`, true); }
    });
  }
  const probe = document.getElementById("dsProbeBtn");
  if (probe && !probe.dataset.liveBound) {
    probe.dataset.liveBound = "1";
    probe.addEventListener("click", async () => {
      const hfB = document.getElementById("dsProbeHf"), rmB = document.getElementById("dsProbeRemote");
      const hint = document.getElementById("dsProbeHint");
      probe.disabled = true;
      if (hint) hint.textContent = "探测中…";
      try {
        const st = await FAPI.get("/datasets/online/status");
        const hf = st.hf, rm = st.remote;
        const setBadge = (el, cls, txt) => {
          if (!el) return;
          el.hidden = false;
          el.className = `badge ${cls}`;
          el.innerHTML = `<i></i>${txt}`;
        };
        setBadge(hfB, hf.ok ? "passed" : "failed",
          `HF 镜像 ${hf.ok ? `通 · ${hf.latency_ms}ms` : `不通（${hf.error || hf.status}）`}`);
        setBadge(rmB, !rm.configured ? "pending" : (rm.ok ? "passed" : "failed"),
          `远程源 ${!rm.configured ? "未配置" : (rm.ok ? `通 · ${rm.latency_ms}ms` : `不通（${rm.error}）`)}`);
        if (hint) hint.textContent = hf.ok
          ? "HF 通：用「HF 镜像仓」导入" : "HF 不通：配好 remote_url 后用「远程直链」导入";
      } catch (err) { if (hint) hint.textContent = `探测失败：${err.message}`; }
      finally { probe.disabled = false; }
    });
  }
}
async function wireDatasetImportDrawer() {
  // 导入抽屉提交：按当前来源模式分发（来源参数属于数据集自己，逐项读）
  const go = document.getElementById("dsImpGo");
  if (go && !go.dataset.liveBound) {
    go.dataset.liveBound = "1";
    go.addEventListener("click", async () => {
      const mode = document.querySelector("#dsModeChips .chip.on")?.dataset.dsmode || "upload";
      const val = id => { const el = document.getElementById(id); return el ? el.value.trim() : ""; };
      const caps = [...document.querySelectorAll("#dsCapChips .chip.on")].map(c => c.dataset.cap);
      const own = document.querySelector("#dsOwnChips .chip.on")?.dataset.own || undefined;
      const name = val("dsImpName"), notes = val("dsImpNotes");
      const force = document.getElementById("dsImpForce").checked;
      try {
        let doneMsg = "";
        if (mode === "functional") {
          if (!name) { fapiToast("请填数据集名（类型下唯一）", true); return; }
          await FAPI.post("/datasets/functional/import",
            { name, jsonl: document.getElementById("dsImpText").value, notes, force });
          doneMsg = `功能数据集 ${name} 已导入（harness.Case 校验通过）`;
        } else if (mode === "hf") {
          const hfId = val("dsHfId");
          if (!hfId) { fapiToast("请填 HF dataset id（如 openai/gsm8k）", true); return; }
          const n = val("dsHfN"), seed = val("dsHfSeed");
          go.disabled = true;
          fapiToast("HF 拉取中（下载→转换→采样，视数据集大小可能要一会儿）…");
          const row = await FAPI.post("/datasets/hf/import", {
            hf_id: hfId, name: name || undefined,
            type: document.getElementById("dsHfType")?.value || "accuracy",
            split: val("dsHfSplit") || "test",
            subset: val("dsHfSubset") || undefined,
            n: n ? Number(n) : undefined,
            seed: seed ? Number(seed) : 42,
            fields: val("dsHfFields") || undefined, notes,
            capabilities: caps, ownership: own });
          doneMsg = `已导入 ${row.type}/${row.name}（${(row.stats || {}).count ?? "—"} 条，之后完全离线可用）`;
        } else if (mode === "remote") {
          if (!name) { fapiToast("请填数据集名（类型下唯一）", true); return; }
          go.disabled = true;
          const row = await FAPI.post("/datasets/remote/import", {
            type: document.getElementById("dsRemoteType").value,
            name, url: val("dsRemoteUrl"), notes, force,
            capabilities: caps, ownership: own });
          doneMsg = `已导入 ${row.type}/${row.name}（${(row.stats || {}).count ?? "—"} 条）`;
        } else {
          if (!name) { fapiToast("请填数据集名（类型下唯一）", true); return; }
          const file = document.getElementById("dsImpFile").files[0];
          if (!file) { fapiToast("请选择数据集文件（.jsonl/.json/.csv）", true); return; }
          const text = await file.text();
          const row = await FAPI.post("/datasets/upload",
            { type: document.getElementById("dsImpType")?.value || "accuracy",
              name, filename: file.name, content: text, notes, force,
              capabilities: caps, ownership: own });
          doneMsg = `已上传 ${row.type}/${row.name}（${(row.stats || {}).count ?? "—"} 条，规范化为 jsonl 落位数据集目录）`;
        }
        document.getElementById("dsImport").hidden = true;
        fapiToast(doneMsg);
        await wireCommonDatasetsPage();
      } catch (err) { fapiToast(`导入失败：${err.message}`, true); }
      finally { go.disabled = false; }
    });
  }
  // 详情抽屉「删除登记」：与目录行删除同语义（仅删登记，文件保留）
  const delBtn = document.getElementById("dsDetailDel");
  if (delBtn && !delBtn.dataset.liveBound) {
    delBtn.dataset.liveBound = "1";
    delBtn.addEventListener("click", async () => {
      const ref = delBtn.dataset.dsDel;
      if (!ref || !confirm(`删除数据集登记 ${ref}?（文件保留）`)) return;
      try { await FAPI.del(`/datasets/${encodeURIComponent(ref)}`); }
      catch (err) { fapiToast(`删除失败：${err.message}`, true); return; }
      fapiToast("已删除登记（数据文件保留）");
      document.getElementById("dsDetail").hidden = true;
      await wireCommonDatasetsPage();
    });
  }
}

/* ---------------- 公共 · 在线数据集（导入抽屉「在线数据集」模式内的基准库） ---------------- */
let DS_ONLINE = [];
let DS_ONLINE_ONLY = false;
let DS_ONLINE_SEL = "";

function dsOnlineFiltered() {
  const q = (document.getElementById("dsOnlineSearch")?.value || "").trim().toLowerCase();
  return DS_ONLINE.filter(b => {
    if (DS_ONLINE_ONLY && !b.local) return false;
    if (!q) return true;
    return [b.name, b.pretty_name, b.dataset_id, (b.tags || []).join(" ")]
      .some(v => String(v || "").toLowerCase().includes(q));
  });
}
function dsOnlineRender() {
  const tbl = document.getElementById("dsOnlineTbl");
  if (!tbl) return;
  const rows = dsOnlineFiltered();
  tbl.innerHTML = `
    <thead><tr><th>基准</th><th>dataset_id</th><th>标签</th><th>指标</th><th>subsets</th><th>本机</th><th style="text-align:right">操作</th></tr></thead>
    <tbody>${rows.map(b => `<tr class="${b.name === DS_ONLINE_SEL ? "on" : ""}">
      <td class="rname"><b class="mono">${b.name}</b><div class="rid mono faint">${b.pretty_name && b.pretty_name !== b.name ? b.pretty_name : ""}</div></td>
      <td class="mono dim" style="font-size:11px">${b.dataset_id || "—"}</td>
      <td class="dim">${(b.tags || []).slice(0, 4).map(t => `<span class="tag">${t}</span>`).join(" ")}</td>
      <td class="mono dim" style="font-size:11px">${(b.metrics || []).join(", ") || "—"}</td>
      <td class="dim">${(b.subsets || []).length}</td>
      <td>${dsOnlineBadgeCell(b)}</td>
      <td style="text-align:right"><button class="btn sm" data-ds-online="${b.name}">选用</button></td>
    </tr>`).join("") || '<tr><td colspan="7" class="ds-empty">无匹配（换个关键词试试）</td></tr>'}</tbody>`;
  const only = document.getElementById("dsOnlineLocalOnly");
  if (only) {
    const small = only.querySelector("small");
    if (small) small.textContent = DS_ONLINE.filter(b => b.local).length;
    only.classList.toggle("on", DS_ONLINE_ONLY);
  }
}
/* 基准库点选：填入下方参数区（dataset id + 名称），再按需调 split/采样后提交 */
function dsOnlineSelect(name) {
  const b = DS_ONLINE.find(x => x.name === name);
  if (!b) return;
  DS_ONLINE_SEL = name;
  const set = (id, v) => { const el = document.getElementById(id); if (el) el.value = v; };
  set("dsHfId", b.dataset_id || "");
  set("dsImpName", b.name || "");
  dsOnlineRender();
  fapiToast(`已选用 ${b.pretty_name || b.name}：确认 split/采样参数后点「校验并导入」`);
}
async function wireDatasetOnlinePage() {
  const tbl = document.getElementById("dsOnlineTbl");
  if (!tbl) return;
  const badge = document.getElementById("dsOnlineBadge");
  try {
    const data = await FAPI.get("/datasets/online/datasets");
    DS_ONLINE = data.items || [];
    if (badge) {
      badge.hidden = false;
      badge.className = "badge passed";
      badge.innerHTML = `<i></i>evalscope ${data.count} 个 · 本机已有 ${data.local_ready}`;
    }
    dsOnlineRender();
  } catch (err) {
    if (badge) { badge.hidden = false; badge.className = "badge failed"; badge.innerHTML = "<i></i>基准库列举失败"; }
    tbl.innerHTML = `<tbody><tr><td colspan="7" class="ds-empty">在线数据集列举失败：${err.message}</td></tr></tbody>`;
    return;
  }
  const search = document.getElementById("dsOnlineSearch");
  if (search && !search.dataset.liveBound) {
    search.dataset.liveBound = "1";
    search.addEventListener("input", dsOnlineRender);
  }
  const only = document.getElementById("dsOnlineLocalOnly");
  if (only && !only.dataset.liveBound) {
    only.dataset.liveBound = "1";
    only.addEventListener("click", () => {
      DS_ONLINE_ONLY = !DS_ONLINE_ONLY;
      dsOnlineRender();
    });
  }
  if (!tbl.dataset.liveBound) {
    tbl.dataset.liveBound = "1";
    tbl.addEventListener("click", e => {
      const btn = e.target.closest("[data-ds-online]");
      if (btn) dsOnlineSelect(btn.dataset.dsOnline);
    });
  }
}

/* ------------------------------------------------------------------ boot */

async function fapiBoot() {
  let live = false;
  try {
    await FAPI.get("/functional/scenarios");
    live = true;
  } catch (e) { live = false; }
  FAPI.live = live;
  fapiBadge(live);
  if (!live) return;
  if (document.getElementById("modelsTbl")) {                 // 公共 · 模型管理页
    try { await wireCommonModelsPage(); }
    catch (err) { fapiToast(`模型管理对接失败：${err.message}`, true); }
    try { await wireCommonVersions(); }                        // 版本管理（§10.6）
    catch (err) { fapiToast(`版本管理对接失败：${err.message}`, true); }
    try { await wireCommonDatasetsPage(); }                    // 数据集目录（§12）
    catch (err) { fapiToast(`数据集目录对接失败：${err.message}`, true); }
    try { await wireDatasetSettings(); }                       // 全局设置（存储位置/在线源默认值）
    catch (err) { fapiToast(`数据集设置对接失败：${err.message}`, true); }
    try { await wireDatasetOnlinePage(); }                     // 在线数据集（evalscope 原生基准全集）
    catch (err) { fapiToast(`在线数据集对接失败：${err.message}`, true); }
    try { await wireDatasetImportDrawer(); }                   // 导入抽屉（四来源提交）+ 详情删除绑定
    catch (err) { fapiToast(`数据集导入对接失败：${err.message}`, true); }
    return;
  }
  if (!document.getElementById("scriptsTbl")) return;         // 其余静态页不对接口
  // 逐段 fail-soft：单板块失败 toast 告知，不推翻整页 LIVE 状态（ping 才是权威）
  const steps = [
    ["场景", wireScenarios], ["工具库", wireTools], ["脚本库", wireScripts], ["任务列表", wireTasks],
    ["版本结果库", wireResults], ["模型下拉", wireModels], ["被测版本", wireTaskVersions],
  ];
  for (const [label, fn] of steps) {
    try {
      await fn();
    } catch (err) {
      console.error(`[fapi] ${label} 对接失败:`, err);
      fapiToast(`${label}对接失败（该板块保持演示数据）：${err.message}`, true);
    }
  }
  bindLiveForm();
  bindToolsUi();
  bindTaskSubmit();
  startLivePolling();
  // 刷新后停在详情页：仅当刷新时确实停在详情页（hash #detail / #detail/<id>）
  // 才恢复任务视图；停在别的页（如评估场景）刷新就留在原地——不再从
  // sessionStorage 无条件兜底跳详情（旧任务被删后会落「加载失败」页）
  if (FAPI_INITIAL_HASH.startsWith("#detail")) {
    const fromHash = FAPI_INITIAL_HASH.startsWith("#detail/")
      ? FAPI_INITIAL_HASH.slice(8) : "";
    const lastTask = fromHash || sessionStorage.getItem("aite-live-last-task");
    if (lastTask) await showLiveTask(lastTask);
  }
  fapiToast("已对接真实后端（数据真读写；任务状态 5s 自动同步）");
}

// 启动收尾（含 MOCK / 不对接页的提前返回）在 <html data-api-ready> 落标记：自动化验证据此等待监听绑定完成
document.addEventListener("DOMContentLoaded", () => setTimeout(() => fapiBoot().finally(() => {
  document.documentElement.dataset.apiReady = FAPI.live ? "live" : "mock";
}), 80));

// 动态重渲染的 [data-open-drawer] 按钮没有 v1.js 的启动期监听（脚本库行「定向执行」、
// 场景卡「过滤跑 →」等）——文档级委托兜底；与启动期监听重复触发幂等（同为 unhide）。
document.addEventListener("click", e => {
  const btn = e.target.closest("[data-open-drawer]");
  if (!btn) return;
  const d = document.getElementById(btn.dataset.openDrawer);
  if (d && d.hidden) d.hidden = false;
});
