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

function fapiToast(msg, bad = false) {
  const el = document.createElement("div");
  el.style.cssText = "position:fixed;top:16px;right:16px;z-index:99;max-width:420px;padding:10px 14px;" +
    "border-radius:10px;font-size:12.5px;border:1px solid;backdrop-filter:blur(6px);" +
    "background:" + (bad ? "rgba(60,20,20,.92)" : "rgba(20,32,48,.92)") + ";" +
    "border-color:" + (bad ? "rgba(251,113,133,.5)" : "rgba(96,165,250,.5)") + ";color:#e8eefc";
  el.textContent = msg;
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 4200);
}

/* ---------------------------------------------------------------- 渲染小件 */
function fapiVerdictBadge(v) {
  const cls = { 达标: "passed", 劣化: "failed" };
  return `<span class="badge ${cls[v] || ""}"><i></i>${v}</span>`;
}
function fapiShortTime(iso) { return (iso || "").slice(5, 16).replace("T", " "); }

/* ---------------------------------------------------------- 脚本库（live） */
async function wireScripts() {
  const data = await FAPI.get("/functional/scripts");
  CASE_SCRIPTS.splice(0, CASE_SCRIPTS.length, ...data.items.map(s => ({
    id: s.id, name: s.name, sc: s.scenario,
    scLabel: s.scenario_name || s.scenario, type: s.case_type,
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
  return `<div class="scene-card"><b>${s.display_name}</b>
    <div class="rid mono" style="margin:2px 0 6px">${s.id}</div><p>${s.description || "（无说明）"}</p>
    <div class="meta"><span class="tag">${s.dimension || "未分类"}</span>
    <span class="tag">${s.cases} 例</span><span class="tag">${cover}</span>
    <span class="tag" style="color:var(--ac)">${form}</span>
    ${s.source === "builtin" ? '<span class="tag">内置只读</span>' : ""}</div>
    <div class="meta" style="margin-top:10px;display:flex;gap:6px">
      ${s.source === "user" ? `<button class="btn sm primary" data-mgr="${s.id}">管理脚本</button>
      <button class="btn sm" data-new="${s.id}">＋ 脚本</button>
      <button class="btn sm" data-del="${s.id}">删除</button>` : '<span class="tag">随框架发布</span>'}
      <button class="btn sm" data-open-drawer="newRunModal">过滤跑 →</button></div></div>`;
}

async function wireScenarios() {
  const data = await FAPI.get("/functional/scenarios");
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
        catch (err) { fapiToast(`删除失败：${err.message}`, true); return; }
        fapiToast(`已删除场景 ${sid}`);
        await wireScenarios();
      }
    });
  }
  // 场景树/创建抽屉的真实化：用户场景补空 FT_CASES 条目 + 重置勾选集
  for (const i of data.items) if (!FT_CASES[i.id]) FT_CASES[i.id] = [];
  ftSel = new Set(Object.keys(FT_CASES).flatMap(sc => ftAllCases(sc)));
  // 创建任务抽屉的场景树同步真实化
  if (typeof renderFtPick === "function") renderFtPick();
  // 新建脚本 / 导入抽屉的场景下拉 → 用户场景（builtin 只读不可建脚本）
  const userItems = data.items.filter(i => i.source === "user");
  const opts = '<option value="">请选择评估场景…</option>' +
    userItems.map(i => `<option value="${i.id}">${i.id} · ${i.display_name}</option>`).join("");
  const nsSel = document.querySelector("#newScriptDrawer .drawer-body select.in");
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
      <td class="rname"><b>${s.name}</b><div class="rid">${s.id}</div></td>
      <td><span class="tag">${s.case_type}</span></td><td class="num">${s.row_count}</td>
      <td class="dim">${s.source}</td><td class="mono dim">${s.external_ref || "—"}</td>
      <td style="text-align:right;white-space:nowrap">
        <button class="btn sm" data-edit="${s.id}">编辑</button>
        <button class="btn sm" data-del="${s.id}">删除</button></td></tr>`).join("")
      : '<tr><td colspan="6" class="ds-empty">场景内暂无脚本 —— 「＋ 从 0 新建脚本」或「从数据集导入」</td></tr>'}</tbody>`;
  const d = document.getElementById("scriptMgrDrawer");
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
    } catch (err) { fapiToast(`保存失败：${err.message}`, true); }
  });

  const imp = document.getElementById("importScriptDrawer");
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

function submitNewScript(ns) {
  const body = ns.querySelector(".drawer-body");
  const inputs = body.querySelectorAll("input.in");
  const editSid = ns.dataset.editSid || null;   // 编辑模式：PATCH，场景/脚本 id 不可改
  const scenSel = document.getElementById("nsScen");
  const payload = {
    name: inputs[0].value.trim(),
    external_ref: inputs[2].value.trim(),
    case_type: document.getElementById("nsCaseType")?.querySelector(".chip.on")?.textContent.trim() || "基础",
    rows: [],
  };
  if (editSid) {
    payload.script_id = editSid;
  } else {
    payload.scenario_id = (scenSel?.value || "").split(" ·")[0].trim();
    payload.script_id = inputs[1].value.trim();
    if (!payload.scenario_id) throw new Error("请先选择评估场景（先场景后用例）");
    if (!payload.script_id) throw new Error("请填写脚本 id");
  }
  ns.querySelectorAll("#nsRows .card").forEach(card => {
    const title = card.querySelector(".seg-title")?.textContent.trim() || "变体";
    const carrier = card.querySelector(".ns-carrier")?.value || "messages";
    const content = card.querySelector("textarea").value.trim();
    let conv = [];
    if (carrier !== "none") {
      try { conv = JSON.parse(content || "[]"); }
      catch (e) { throw new Error(`${title}：content 须为合法 JSON 数组`); }
      if (!Array.isArray(conv)) throw new Error(`${title}：content 须为 JSON 数组`);
    }
    // 「行 N[ · 变体说明]」→ 变体说明；无说明回退「变体 k」（harness desc 必填）
    const desc = title.replace(/^行\s*\d+/, "").replace(/^[\s·]+/, "").trim()
      || `变体 ${payload.rows.length + 1}`;
    // 承载：messages=标准；input=对话进自定义字段；none=纯参数校验（引擎 _omit_messages 省略）
    const row = { desc, messages: carrier === "messages" ? conv : [] };
    let collected;
    try { collected = nsCollectParams(card, desc); }
    catch (e) { throw new Error(e.message); }
    row.params = collected.raw !== undefined ? { _raw_body: collected.raw } : collected.base;
    if (carrier === "input") row.params = { ...(row.params || {}), input: conv, _omit_messages: true };
    else if (carrier === "none") row.params = { ...(row.params || {}), _omit_messages: true };
    const toolsRaw = card.querySelector(".ns-tools")?.value.trim() ?? "";
    if (toolsRaw) {
      const tools = JSON.parse(toolsRaw);   // 非法 JSON 抛错由外层 toast 显式报出
      if (!Array.isArray(tools)) throw new Error(`${desc}：tools 须为 JSON 数组（OpenAI tools 格式）`);
      row.tools = tools;
    }
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
  const afterSave = async row => {
    ns.hidden = true;
    delete ns.dataset.editSid;
    const scenSel = document.getElementById("nsScen");
    if (scenSel) scenSel.disabled = false;
    inputs[1].disabled = false;
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
  const customDesc = row.desc && !/^行\s*\d+$/.test(row.desc) ? ` · ${row.desc}` : "";
  if (customDesc) card.querySelector(".seg-title").textContent += customDesc;
  const p = { ...(row.params || {}) };
  let carrier = "messages", conv = row.messages ?? [];
  if (p._omit_messages) {
    if (p.input !== undefined) { carrier = "input"; conv = p.input; }
    else carrier = "none";
  }
  card.querySelector(".ns-carrier").value = carrier;
  card.querySelector("textarea").value = carrier === "none" ? "" : JSON.stringify(conv);
  // 常用键 → 结构化控件；其余（控制键/_raw_body/自定义键）→ JSON 框
  const rest = { ...p };
  delete rest._omit_messages;
  if (carrier === "input") delete rest.input;
  const setNum = (cls, key) => {
    const el = card.querySelector(cls);
    if (el && rest[key] !== undefined) { el.value = String(rest[key]); delete rest[key]; }
  };
  setNum(".ns-temp", "temperature");
  setNum(".ns-topp", "top_p");
  setNum(".ns-maxt", "max_tokens");
  if (rest.stop !== undefined) {
    const s = rest.stop; delete rest.stop;
    card.querySelector(".ns-stop").value = Array.isArray(s) ? s.join(",") : String(s);
  }
  if (rest.stream !== undefined) { card.querySelector(".ns-stream").value = String(rest.stream); delete rest.stream; }
  if (rest.response_format && typeof rest.response_format === "object"
      && ["text", "json_object"].includes(rest.response_format.type)) {
    card.querySelector(".ns-respf").value = rest.response_format.type;
    delete rest.response_format;
  }
  if (rest.thinking && typeof rest.thinking === "object") {
    const t = rest.thinking;
    if (t.type === "disabled") { card.querySelector(".ns-think").value = "disabled"; delete rest.thinking; }
    else if (t.type === "enabled" && (t.reasoning_effort === undefined
             || ["low", "high", "max", "none"].includes(t.reasoning_effort))) {
      card.querySelector(".ns-think").value = t.reasoning_effort || "high";
      delete rest.thinking;
    }
  }
  if (rest.stream_options && typeof rest.stream_options === "object"
      && rest.stream_options.include_usage === true) {
    card.querySelector(".ns-sopt").value = "include_usage";
    delete rest.stream_options;
  }
  if (rest.tool_choice !== undefined) {
    const tcv = rest.tool_choice;
    let matched = true;
    if (tcv && typeof tcv === "object" && tcv.function?.name) {
      card.querySelector(".ns-tchoice").value = "named";
      const nameIn = card.querySelector(".ns-tcname");
      nameIn.value = tcv.function.name;
      nameIn.style.display = "";
    } else if (["none", "auto", "required"].includes(tcv)) {
      card.querySelector(".ns-tchoice").value = tcv;
    } else matched = false;
    if (matched) delete rest.tool_choice;
  }
  if (rest.logprobs !== undefined) { card.querySelector(".ns-logprobs").value = String(rest.logprobs); delete rest.logprobs; }
  if (rest.top_logprobs !== undefined) { card.querySelector(".ns-tlp").value = String(rest.top_logprobs); delete rest.top_logprobs; }
  if (rest.user_id !== undefined) { card.querySelector(".ns-uid").value = String(rest.user_id); delete rest.user_id; }
  if (rest.frequency_penalty !== undefined) { card.querySelector(".ns-fp").value = String(rest.frequency_penalty); delete rest.frequency_penalty; }
  if (rest.presence_penalty !== undefined) { card.querySelector(".ns-pp").value = String(rest.presence_penalty); delete rest.presence_penalty; }
  if ("_raw_body" in rest) {
    card.querySelector(".ns-rawbody").checked = true;
    card.querySelector(".ns-params").value = String(rest._raw_body);
    delete rest._raw_body;
  } else if (Object.keys(rest).length) {
    card.querySelector(".ns-params").value = JSON.stringify(rest);
  }
  if (row.tools) card.querySelector(".ns-tools").value = JSON.stringify(row.tools);
  nsFillExpect(card, row.expect || {});
  nsFillVars(card, row.vars);
}
async function openEditScript(sid) {
  const ns = document.getElementById("newScriptDrawer");
  nsReset();                                  // 干净态再灌值（同时清编辑态标记）
  const t = await FAPI.get(`/functional/scripts/${sid}`);
  ns.dataset.editSid = sid;
  const inputs = ns.querySelectorAll(".drawer-body input.in");
  const scenSel = document.getElementById("nsScen");
  scenSel.value = `${t.scenario}`;
  [...scenSel.options].forEach(o => { if (o.value.split(" ·")[0] === t.scenario) scenSel.value = o.value; });
  scenSel.disabled = true;                    // 编辑态：归属与脚本 id 不可改
  inputs[1].value = sid; inputs[1].disabled = true;
  inputs[0].value = t.name || "";
  inputs[2].value = t.external_ref || "";
  const typeChip = [...(document.getElementById("nsCaseType")?.querySelectorAll(".chip") || [])]
    .find(c => c.textContent.trim() === (t.case_type || "基础"));
  if (typeChip) typeChip.classList.add("on");
  const rowsBox = document.getElementById("nsRows");
  rowsBox.innerHTML = ""; nsRowN = 0;
  for (const row of (t.rows || [])) {
    nsRowN += 1;
    rowsBox.insertAdjacentHTML("beforeend", nsRowHtml(nsRowN));
    const card = rowsBox.lastElementChild;
    bindChips(card);                             // 动态行卡补绑芯片/断言交互
    const customDesc = row.desc && !/^行\s*\d+$/.test(row.desc) ? ` · ${row.desc}` : "";
    card.querySelector(".seg-title").textContent = `行 ${nsRowN}${customDesc}`;
    nsFillRowFromData(card, row);
  }
  if (!rowsBox.children.length) {
    nsRowN = 1;
    rowsBox.insertAdjacentHTML("beforeend", nsRowHtml(1));
    bindChips(rowsBox);
    renderNsAsserts(rowsBox.firstElementChild);
  }
  ns.querySelector(".drawer-foot .btn.primary").textContent = "保存修改（真实校验）";
  ns.hidden = false;
}

function openNewScript(scenarioId) {
  const ns = document.getElementById("newScriptDrawer");
  if (typeof nsReset === "function") nsReset();   // 打开即干净态（nsReset 由页面内联脚本提供）
  delete ns.dataset.editSid;                       // 从编辑态回到新建态
  const scenSel = document.getElementById("nsScen");
  if (scenSel) scenSel.disabled = false;
  ns.querySelectorAll(".drawer-body input.in")[1].disabled = false;
  ns.querySelector(".drawer-foot .btn.primary").textContent = "保存脚本（真实校验）";
  const sel = scenSel || ns.querySelector("select.in");
  sel.value = `${scenarioId}`;
  [...sel.options].forEach(o => { if (o.value.split(" ·")[0] === scenarioId) sel.value = o.value; });
  ns.hidden = false;
}

/* ------------------------------------------------------------ 任务列表 */
let RUN_TASK_IDS = [];
function rebuildRunsFromTasks(items) {
  // 与假数据完全相同的数组形状（renderRuns/bindTaskFilter 按下标渲染），
  // 行点击经 openRunDetail 路由到真实任务详情
  RUN_TASK_IDS = items.map(t => t.id);
  RUNS.splice(0, RUNS.length, ...items.map(t => [
    t.name || "(未命名)",
    `${t.id.slice(0, 8)} · ${t.spec?.version || "未标注版本"}`,
    t.spec?.target?.profile || "-",
    t.status,
    t.result?.pass_rate !== undefined ? { rate: t.result.pass_rate, total: t.result.evaluated_cases } : null,
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
      RUNS[i][4] = { rate, total };
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

let showLiveTaskSeq = 0;
async function fapiPresets() {
  // 模型预制（/common/models）缓存——详情页 curl 组装用
  if (window.__fapiPresets && window.__fapiPresets.length) return window.__fapiPresets;
  try {
    const r = await fetch("/api/v1/common/models");
    const items = ((await r.json()).items) || [];
    if (items.length) window.__fapiPresets = items;
    return items;
  } catch (e) { return window.__fapiPresets || []; }
}

async function showLiveTask(taskId) {
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
  const runId = t.result?.run_id || result?.result?.run_id || null;
  let run = null;
  if (runId) {
    try { run = await FAPI.get(`/runs/${runId}`); }   // DB 唯一权威（原则 8）
    catch (e) { /* run 行可能尚未可见 */ }
  }
  let cases = [];
  if (runId) {
    try { cases = (await FAPI.get(`/runs/${runId}/cases`)).items || []; } catch (e) {}
  }
  const summaries = (run?.summaries || [])
    .filter(x => (x.dataset || "").startsWith("pytest:"));
  const overall = (run?.summaries || [])
    .find(x => x.metric === "pass_rate" && !x.dataset);
  const totalCases = (run?.total_cases || 0)
    || summaries.reduce((a, x) => a + (x.count || 0), 0)
    || (result?.result?.total_cases ?? 0);
  const errorCases = run?.error_cases ?? result?.result?.error_cases ?? 0;
  // 目标模型预制解析（curl 复现用：base_url / model / api_key_env）
  const presets = await fapiPresets();
  const targetProfile = t.spec?.target?.profile || "";
  const preset = presets.find(p => p.name === targetProfile) || null;
  const passedCases = cases.filter(x => (x.metrics || []).some(m => m.passed === true));
  const failedCases = cases.filter(x => (x.metrics || []).some(m => m.passed === false));
  const progress = run?.details?.progress || null;
  const runStatus = run?.status || t.status;
  const pct = progress && progress.total
    ? Math.round((progress.done || 0) / progress.total * 100) : null;
  const pp = v => v === null || v === undefined ? "—" : `${(v * 100).toFixed(1)}%`;
  function fapiExpectRows(expect, response) {
    // 展示用途的逐断言标注（期望 vs 实际）；无法客户端判定的键标注"引擎判定"
    const rows = [];
    if (!expect || typeof expect !== "object") return rows;
    const status = response && response._status !== undefined ? response._status : 200;
    const content = (() => {
      if (!response || typeof response !== "object") return "";
      if (typeof response._body !== "undefined") return String(response._body);
      return response.choices?.[0]?.message?.content ?? "";
    })();
    const finish = response?.choices?.[0]?.finish_reason ?? null;
    const ic = expect.ignore_case === true;
    const norm = t => (ic ? String(t).toLowerCase() : String(t));
    const push = (name, exp, act, ok) => rows.push(
      { name, exp: String(exp), act: String(act), ok });
    for (const [k, v] of Object.entries(expect)) {
      if (k === "ignore_case") continue;
      if (k === "contains") {
        for (const kw of [].concat(v)) {
          const hit = norm(content).includes(norm(kw));
          push("包含关键词", kw, hit ? "命中" : "未命中", hit);
        }
      } else if (k === "not_contains") {
        for (const kw of [].concat(v)) {
          const hit = norm(content).includes(norm(kw));
          push("不得包含", kw, hit ? "出现了（违规）" : "未出现", !hit);
        }
      } else if (k === "nonempty") {
        push("非空", v ? "期望非空" : "任意", content.trim() ? "非空" : "空",
             !!content.trim() === !!v);
      } else if (k === "http_status") {
        push("HTTP 状态码", v, status, Number(v) === status);
      } else if (k === "finish_reason") {
        push("finish_reason", v, finish, finish === v);
      } else if (k === "sse_done") {
        push("sse_done 正常收尾", v, response._sse_ok === true);
      } else if (k === "chunks_min") {
        push("分块数 ≥", v, response._chunks, (response._chunks || 0) >= v);
      } else if (k === "no_leak") {
        push("无泄漏 no_leak", "敏感模式扫描", "引擎判定", null);
      } else if (k === "json" || k === "resp") {
        push(k, JSON.stringify(v), "结构断言（引擎判定）", null);
      } else if (k === "tool" || k === "tool_args_contains" || k === "no_tool") {
        push(k, v ?? "—", "工具调用断言（引擎判定）", null);
      } else {
        push(k, JSON.stringify(v), "引擎判定", null);
      }
    }
    return rows;
  }

  const esc = s => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  window.__copyStore = window.__copyStore || new Map();
  let __cseq = 0;
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
    // cases 接口的 input 即 messages 数组本身（非 {messages:[...]} 包裹）——归一化
    const rawIn = x.input;
    const io = Array.isArray(rawIn) ? { messages: rawIn } : (rawIn || {});
    const msgs = io.messages || [];
    const out = (typeof x.output === "object" && x.output !== null) ? x.output : {};
    const content = out.choices?.[0]?.message?.content ?? "";
    const errLines = (x.error || "").split(String.fromCharCode(10));
    const errFirst = errLines[0] || "";
    // —— 输入：按角色分块；长内容默认折叠（展开全文/复制/下载完整请求）——
    const BQ = String.fromCharCode(92), NLQ = String.fromCharCode(10);
    window.__expStore = window.__expStore || new Map();
    let __eseq = 0;
    const COLLAPSE_AT = 300;
    const msgHtml = m => {
      const c = typeof m.content === "string" ? m.content : JSON.stringify(m.content ?? "");
      const long = c.length > COLLAPSE_AT;
      const preStyle = "white-space:pre-wrap;margin:4px 0 0;padding:6px 8px;border-left:3px solid var(--line);font-size:12px;";
      if (!long) return `<div style="margin:4px 0"><span class="tag mono">${esc(m.role)}</span>
        <pre style="${preStyle}">${esc(c)}</pre></div>`;
      const id = `ex${++__eseq}`;
      window.__expStore.set(id, c);
      return `<div style="margin:4px 0"><span class="tag mono">${esc(m.role)}</span>
        <pre id="${id}" style="${preStyle}max-height:120px;overflow:hidden">${esc(c.slice(0, COLLAPSE_AT))}…</pre>
        <div style="margin-top:2px"><button type="button" class="btn sm ns-exp" data-exp-id="${id}" style="padding:1px 8px;font-size:11px">展开全文（共 ${c.length} 字符）</button></div></div>`;
    };
    const inputHtml = msgs.length ? msgs.map(msgHtml).join("")
      : `<pre style="font-size:12px;white-space:pre-wrap">${esc(JSON.stringify(io, null, 2))}</pre>`;
    // 复现三件套：完整输入 JSON / curl（预制模型解析 URL 与密钥占位）/ 下载 .json
    // 完整请求体 = model + messages + 行参数快照（raw.params，剔除控制键）+ tools
    // ——与引擎 _post 的合并顺序一致（params 同名键覆盖、tools 顶层追加）
    const rawRec = (typeof x.raw === "object" && x.raw) ? x.raw : {};
    const rawParams = (typeof rawRec.params === "object" && rawRec.params) || {};
    const rawParamsClean = Object.fromEntries(
      Object.entries(rawParams).filter(([k]) => !k.startsWith("_")));
    const rawTools = Array.isArray(rawRec.tools) ? rawRec.tools : null;
    const inputJson = JSON.stringify(io, null, 2);
    const reqBody = { model: preset ? preset.model : (targetProfile || "target-model"),
      ...(msgs.length ? { messages: msgs.map(m => ({ role: m.role, content: m.content ?? "" })) } : {}),
      ...rawParamsClean,
      ...(rawTools ? { tools: rawTools } : {}) };
    const base = (preset?.base_url || "https://target.example/v1").replace(/[/]+$/, "");
    const urlFull = base.endsWith("/chat/completions") ? base : base + "/chat/completions";
    const auth = preset?.api_key_env
      ? `Authorization: Bearer ${"$" + preset.api_key_env}`
      : "Authorization: Bearer your-api-key";
    const bodyCompact = JSON.stringify(reqBody).split("'").join(BQ + "'");
    const curlTxt = [
      `curl -X POST "${urlFull}"`,
      `  -H "Content-Type: application/json"`,
      `  -H "${auth}"`,
      `  -d '${bodyCompact}'`,
    ].join(" " + BQ + NLQ);
    // —— 输出：content 正文为主体，形态/元信息降级为标签，原始 JSON 折叠 ——
    const meta = [];
    if (out.finish_reason) meta.push(`finish_reason=${out.finish_reason}`);
    if (out._chunks) meta.push(`chunks=${out._chunks}`);
    if (out._sse_ok !== undefined) meta.push(`sse_done=${out._sse_ok}`);
    if (out.usage) meta.push(`usage=${JSON.stringify(out.usage)}`);
    const outputHtml = `<pre style="white-space:pre-wrap;font-size:12px;max-height:200px;overflow:auto;margin:0">${esc(content || "—")}</pre>`
      + (meta.length ? `<div class="hint" style="margin-top:4px">${meta.map(esc).join(" · ")}</div>` : "")
      + `<details style="margin-top:4px"><summary class="dim" style="font-size:11px;cursor:pointer">原始响应 JSON</summary>
         <pre style="font-size:11px;white-space:pre-wrap;max-height:180px;overflow:auto;margin:4px 0 0">${esc(JSON.stringify(out, null, 2))}</pre></details>`;
    // —— 断言对照：期望 | 实际 两列并排 ——
    const rows = fapiExpectRows(x.expected, out)
      .map(r => ({ ...r, ok: isFailed ? r.ok : true }));
    const rowHtml = r => {
      const color = r.ok === false ? "var(--bad)" : r.ok === true ? "var(--ok)" : "var(--tx3)";
      return `<div style="display:grid;grid-template-columns:150px 1fr 1fr;gap:8px;font-size:12px;margin:3px 0;align-items:start">
        <span style="font-weight:650;color:${color}">${esc(r.name)}</span>
        <span style="color:${color};word-break:break-all">期望 ${esc(r.exp)}</span>
        <span style="color:${color};word-break:break-all">实际 ${esc(r.act)}</span></div>`;
    };
    const assertHtml = rows.length
      ? `<div style="display:grid;grid-template-columns:150px 1fr 1fr;gap:8px;font-size:11px;color:var(--tx3)">
           <span>断言</span><span>期望</span><span>实际</span></div>
         ${rows.map(rowHtml).join("")}`
      : '<span class="faint" style="font-size:12px">—</span>';
    // —— 失败日志：首行醒目，全文折叠（不再重复渲染输入/输出摘要） ——
    const logSec = isFailed && x.error ? `
      <div style="margin-top:10px;padding:8px 10px;border-left:3px solid var(--bad);background:color-mix(in srgb,var(--bad) 8%,transparent);border-radius:0 8px 8px 0">
        <div class="mono" style="font-size:12px;color:var(--bad);word-break:break-all">${esc(errFirst)}</div>
        <details style="margin-top:4px"><summary class="dim" style="font-size:11px;cursor:pointer">引擎原始日志（含输入/输出全文）</summary>
          <pre style="font-size:11px;white-space:pre-wrap;max-height:220px;overflow:auto;margin:4px 0 0">${esc(x.error)}</pre></details>
      </div>` : "";
    return `<details ${isFailed ? "open" : ""} style="border:1px solid ${isFailed ? "color-mix(in srgb,var(--bad) 35%,transparent)" : "var(--line)"};border-radius:10px;padding:8px 12px;margin-top:8px">
      <summary style="cursor:pointer;font-size:12.5px">
        <span class="mono">${esc(x.external_key)}</span>
        <span class="badge ${st}" style="margin-left:8px"><i></i>${isFailed ? "失败" : "通过"}</span></summary>
      ${secHead("输入", [copyBtn(inputJson, "复制输入 JSON"), copyBtn(curlTxt, "复制 curl"), dlBtn(reqBody, `${x.external_key || "case"}-request.json`)].join(""))}
      ${inputHtml}
      ${secHead("输出", content ? copyBtn(content, "复制正文") : "")}
      ${outputHtml}
      ${secHead("断言", copyBtn(rows.map(r => `${r.name}: 期望 ${r.exp} / 实际 ${r.act}`).join("；"), "复制断言"))}
      ${assertHtml}
      ${logSec}
    </details>`;
  };
  detail.innerHTML = `
    <header class="topbar">
      <div><h1>${t.name || "(未命名)"} ${taskBadge(t.status)}</h1>
      <div class="crumb">task ${t.id} · 引擎 pytest · 被测版本 ${t.spec?.version || "未标注"}
      · 模型 ${t.spec?.target?.profile || t.spec?.target?.model || "-"}</div></div>
      <div class="actions">
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
        <div class="sub">成功/失败分组 · 点开看原始 输入(messages)/输出(模型响应) · 数据源 = 用例结果表</div></div></div>
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
  // 被测版本转自由输入（版本管理页未建前，允许手输任意版本号；datalist 给历史提示）
  const verSel = document.getElementById("ftVerSel");
  if (verSel && verSel.tagName === "SELECT") {
    const input = document.createElement("input");
    input.id = "ftVerSel";
    input.className = "in";
    input.setAttribute("list", "ftVerDl");
    input.placeholder = "如 v1.3.0（可自由输入）";
    input.value = verSel.value.replace(/（.*）/, "");
    const dl = document.createElement("datalist");
    dl.id = "ftVerDl";
    dl.innerHTML = versions.map(v => `<option>${v}</option>`).join("");
    verSel.replaceWith(input);
    input.insertAdjacentElement("afterend", dl);
  }
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
function bindTaskSubmit() {
  const drawer = document.getElementById("newRunModal");
  const submit = drawer.querySelector(".drawer-foot .btn.primary");
  submit.addEventListener("click", async () => {
    const secs = drawer.querySelectorAll(".form-sec");
    const name = secs[0].querySelector("input.in").value.trim();
    const modelKey = document.getElementById("ftModelSel").value;
    const version = document.getElementById("ftVerSel").value.trim();   // 提交时查询（live 下可能是替换后的 input）
    const timeout = secs[2].querySelector("input.in").value.trim();
    const scenes = SCENES.map(s => s[0]).filter(sc => ftSceneState(sc) !== "none");
    if (!scenes.length) { fapiToast("请至少选择一个评估场景", true); return; }
    let target;
    if (modelKey === "__custom__") {
      const pane = document.getElementById("ftModelCustom");
      const inputs = pane.querySelectorAll("input.in");
      target = { type: "openai_compatible", base_url: inputs[0].value.trim(),
                 model: inputs[1].value.trim() };
      const keyEnv = inputs[2].value.trim();
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
    const userSel = scenes.filter(sc => !BUILTIN_IDS.includes(sc));
    const builtinSel = scenes.filter(sc => BUILTIN_IDS.includes(sc));
    if (userSel.length && builtinSel.length) {
      fapiToast("内置与自建场景暂不支持同任务混跑（两者在不同目录），请分两次发起", true);
      return;
    }
    // 必须显式给场景根：内置 = 包内目录，自建 = 用户根（parse_tool_suite 两者缺一即拒）
    spec.tools.tests_dir = userSel.length
      ? "data/functional/scenarios"
      : "aite/functional/scenarios";
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
  const [common, db] = await Promise.all([
    FAPI.get("/common/models").catch(() => ({ items: [] })),
    FAPI.get("/models").catch(() => ({ items: [] })),
  ]);
  MODEL_SOURCES = [
    ...common.items.map(m => ({
      key: `common:${m.name}`,
      label: `${m.name}${m.model ? ` · ${m.model}` : ""}（预制）`,
      target: { profile: m.name },
    })),
    ...db.items.map(m => ({
      key: `db:${m.id}`,
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
  const commonRows = commonData.items.map(m => `<tr>
      <td class="rname"><b class="mono">${m.name}</b></td>
      <td><span class="tag" style="color:var(--ac)">预制</span></td>
      <td class="mono dim" style="font-size:12px">${m.base_url || "—"}</td>
      <td class="mono">${m.model || "—"}</td><td>${keyCell(m.api_key_env)}</td>
      <td style="text-align:right"><span class="tag">随 common.yaml 发布</span></td></tr>`);
  const dbRows = MODEL_ROWS.map(m => `<tr>
      <td class="rname"><b class="mono">${m.name}</b></td>
      <td><span class="tag">注册</span></td>
      <td class="mono dim" style="font-size:12px">${m.base_url || "—"}</td>
      <td class="mono">${m.model || "—"}</td><td>${keyCell(m.api_key_env)}</td>
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
        <td class="mono">${i.name}</td><td class="mono dim">${i.mask}</td>
        <td style="text-align:right"><button class="btn sm danger" data-del-secret="${i.name}">删除</button></td>
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

  const drawer = document.getElementById("modelDrawer");
  if (drawer && !drawer.dataset.liveBound) {
    drawer.dataset.liveBound = "1";
    drawer.querySelector(".drawer-foot .btn.primary").addEventListener("click", async () => {
      const inputs = drawer.querySelectorAll(".drawer-body input.in");
      const type = drawer.querySelector(".drawer-body select.in").value;
      try {
        await FAPI.post("/models", { name: inputs[0].value.trim(), type,
          base_url: inputs[1].value.trim(), model: inputs[2].value.trim(),
          api_key_env: inputs[3].value.trim(), params: {} });
        drawer.hidden = true;
        fapiToast(`模型 ${inputs[0].value.trim()} 已注册`);
        await wireCommonModelsPage();
      } catch (err) { fapiToast(`注册失败：${err.message}`, true); }
    });
  }
}

/* ---------------- 公共 · 版本管理（common2.html，全局版本登记 §10.6） ---------------- */
async function wireCommonVersions() {
  const [verData, dbData] = await Promise.all([
    FAPI.get("/versions"),
    FAPI.get("/models").catch(() => ({ items: [] })),
  ]);
  const rows = verData.items;
  const tbl = document.getElementById("versionsTbl");
  tbl.innerHTML = `
    <thead><tr><th>模型</th><th>版本</th><th>说明</th><th>登记时间</th>
    <th style="text-align:right">操作</th></tr></thead>
    <tbody>${rows.length ? rows.map(v => `<tr>
      <td class="rname"><b class="mono">${v.model}</b></td>
      <td class="mono">${v.version}</td>
      <td class="dim" style="white-space:normal">${v.description || "—"}</td>
      <td class="dim">${fapiShortTime(v.created_at)}</td>
      <td style="text-align:right"><button class="btn sm danger" data-del-ver="${v.id}">删除</button></td></tr>`).join("")
      : '<tr><td colspan="5" class="ds-empty">暂无登记版本 —— 下方表单登记后，精度/功能任务发起即可按模型选择被测版本</td></tr>'}</tbody>`;
  if (!tbl.dataset.liveBound) {
    tbl.dataset.liveBound = "1";
    tbl.addEventListener("click", async e => {
      const del = e.target.closest("[data-del-ver]");
      if (!del || !confirm("删除该版本登记?（历史任务/归档持版本字符串不受影响）")) return;
      try { await FAPI.del(`/versions/${del.dataset.delVer}`); }
      catch (err) { fapiToast(`删除失败：${err.message}`, true); return; }
      await wireCommonVersions();
    });
  }
  // 登记表单：模型下拉 = 注册模型 + 登记过的模型名（支持手填其他）
  const sel = document.getElementById("verModelSel");
  const names = [...new Set([...dbData.items.map(m => m.name),
                             ...rows.map(v => v.model)])];
  sel.innerHTML = names.map(n => `<option value="${n}">${n}</option>`).join("")
    + '<option value="__other__">其他（手填）</option>';
  let nameIn = document.getElementById("verModelOther");
  if (!nameIn) {
    nameIn = document.createElement("input");
    nameIn.className = "in"; nameIn.id = "verModelOther";
    nameIn.placeholder = "模型名（如 deepseek）"; nameIn.style.display = "none";
    sel.insertAdjacentElement("afterend", nameIn);
  }
  if (!sel.dataset.liveBound) {
    sel.dataset.liveBound = "1";
    sel.addEventListener("change", () => {
      nameIn.style.display = sel.value === "__other__" ? "" : "none";
    });
    document.getElementById("verSave").addEventListener("click", async () => {
      const model = (sel.value === "__other__" ? nameIn.value : sel.value).trim();
      const version = document.getElementById("verVersionIn").value.trim();
      const description = document.getElementById("verDescIn").value.trim();
      const hint = document.getElementById("verHint");
      if (!model || !version) { fapiToast("模型与版本号均必填", true); return; }
      try {
        await FAPI.post("/versions", { model, version, description });
        document.getElementById("verVersionIn").value = "";
        document.getElementById("verDescIn").value = "";
        if (hint) hint.textContent = `已登记：${model} ${version}`;
        fapiToast(`版本 ${model} ${version} 已登记（精度/功能发起可选）`);
        await wireCommonVersions();
      } catch (err) { fapiToast(`登记失败：${err.message}`, true); }
    });
  }
}

/* ------------------------------------------------------------------ boot */
const BUILTIN_IDS = ["agent_tasks", "api_limits", "api_params", "api_shape",
  "api_stream", "basic_chat", "long_context", "structured_output", "tool_calling"];

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
    return;
  }
  if (!document.getElementById("scriptsTbl")) return;         // 其余静态页不对接口
  // 逐段 fail-soft：单板块失败 toast 告知，不推翻整页 LIVE 状态（ping 才是权威）
  const steps = [
    ["场景", wireScenarios], ["脚本库", wireScripts], ["任务列表", wireTasks],
    ["版本结果库", wireResults], ["模型下拉", wireModels],
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

document.addEventListener("DOMContentLoaded", () => setTimeout(fapiBoot, 80));

// 动态重渲染的 [data-open-drawer] 按钮没有 v1.js 的启动期监听（脚本库行「定向执行」、
// 场景卡「过滤跑 →」等）——文档级委托兜底；与启动期监听重复触发幂等（同为 unhide）。
document.addEventListener("click", e => {
  const btn = e.target.closest("[data-open-drawer]");
  if (!btn) return;
  const d = document.getElementById(btn.dataset.openDrawer);
  if (d && d.hidden) d.hidden = false;
});
