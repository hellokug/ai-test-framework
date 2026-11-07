/* aite V1 静态稿 · 共享脚本：主题切换 / 模块导航 / 页内路由 / 图表助手 */
"use strict";

/* ============ 主题（与 A3 一致） ============ */
const THEMES = {
  obsidian: { name: "Obsidian 暗紫", desc: "Linear / Vercel 风 · 默认", dots: ["#8b7cf6", "#22d3ee", "#0a0a11"],
    charts: { axis: "#5d6275", axisLine: "rgba(255,255,255,.08)", split: "rgba(255,255,255,.05)", radarSplit: "rgba(255,255,255,.07)", splitArea: ["rgba(255,255,255,.02)", "transparent"], tipBg: "#181824", tipBorder: "rgba(255,255,255,.1)", tipTx: "#eceef6", tipShadow: false, pass: "#8b7cf6", err: "#22d3ee", radar: ["#8b7cf6", "#22d3ee", "#f472b6"], glow: false } },
  github: { name: "GitHub Dark", desc: "github.com 开发者经典", dots: ["#58a6ff", "#3fb950", "#0d1117"],
    charts: { axis: "#6e7681", axisLine: "#30363d", split: "rgba(110,118,129,.18)", radarSplit: "rgba(110,118,129,.25)", splitArea: ["rgba(110,118,129,.06)", "transparent"], tipBg: "#161b22", tipBorder: "#30363d", tipTx: "#e6edf3", tipShadow: false, pass: "#58a6ff", err: "#d29922", radar: ["#58a6ff", "#3fb950", "#a371f7"], glow: false } },
  spotify: { name: "Spotify 暗夜绿", desc: "spotify.com 音乐律动", dots: ["#1ed760", "#509bf5", "#121212"],
    charts: { axis: "#6a6a6a", axisLine: "#2a2a2a", split: "rgba(255,255,255,.08)", radarSplit: "rgba(255,255,255,.1)", splitArea: ["rgba(255,255,255,.03)", "transparent"], tipBg: "#282828", tipBorder: "#3e3e3e", tipTx: "#fff", tipShadow: false, pass: "#1ed760", err: "#f573a0", radar: ["#1ed760", "#509bf5", "#f573a0"], glow: false } },
  aurora: { name: "Aurora 极光", desc: "炫彩发光 · 大屏气质", dots: ["#22d3ee", "#e879f9", "#070a18"],
    charts: { axis: "rgba(203,213,225,.45)", axisLine: "rgba(255,255,255,.12)", split: "rgba(255,255,255,.06)", radarSplit: "rgba(255,255,255,.09)", splitArea: ["rgba(255,255,255,.03)", "transparent"], tipBg: "rgba(10,13,30,.85)", tipBorder: "rgba(255,255,255,.14)", tipTx: "#eef2ff", tipShadow: false, pass: "#22d3ee", err: "#e879f9", radar: ["#22d3ee", "#818cf8", "#e879f9"], glow: true } },
  stripe: { name: "Stripe 云端紫", desc: "stripe.com 亮色商务", dots: ["#635bff", "#00d4ff", "#f6f9fc"],
    charts: { axis: "#8792a2", axisLine: "#e6ebf1", split: "#eef2f6", radarSplit: "#eef2f6", splitArea: ["#fafcfe", "transparent"], tipBg: "#ffffff", tipBorder: "#e6ebf1", tipTx: "#0a2540", tipShadow: true, pass: "#635bff", err: "#00d4ff", radar: ["#635bff", "#00d4ff", "#df1b41"], glow: false } },
  antd: { name: "Ant Design 晴蓝", desc: "ant.design 企业亮色", dots: ["#1677ff", "#13c2c2", "#f5f6fa"],
    charts: { axis: "#9298a9", axisLine: "#e7e9f2", split: "#eef0f6", radarSplit: "#eef0f6", splitArea: ["#fafbfe", "transparent"], tipBg: "#ffffff", tipBorder: "#e7e9f2", tipTx: "#171a2b", tipShadow: true, pass: "#1677ff", err: "#13c2c2", radar: ["#1677ff", "#52c41a", "#722ed1"], glow: false } },
};
const DOMAIN_FILES = { functional: "functional2.html", perf: "perf2.html", accuracy: "accuracy2.html", reliability: "reliability3.html" };
const DOMAIN_LABELS = { functional: "功能", perf: "性能", accuracy: "精度", reliability: "可靠性" };
const TOP_NAV = [
  { label: "任务管理", href: "accuracy2.html", modules: Object.keys(DOMAIN_FILES) },
  { label: "数据集管理", href: "common2.html#datasets", pages: ["datasets"] },
  { label: "模型管理", href: "common2.html#models", pages: ["models"] },
  { label: "执行机管理", href: "common2.html#agents", pages: ["agents"] },
];

/* ============ 壳：顶栏 + 侧栏 + 页内路由 ============ */
function buildShell() {
  // 顶栏脚手架只建一次（主题按钮等监听绑定在其上）
  document.getElementById("topnav").innerHTML = `
    <a class="brand" href="accuracy2.html">
      <div class="brand-mark"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M13 2 4 14h6l-1 8 9-12h-6l1-8z"/></svg></div>
      <b>aite</b>
    </a>
    <nav class="mtabs" id="mtabs"></nav>
    <div class="macts">
      <label class="search">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="11" cy="11" r="7"/><line x1="21" y1="21" x2="16.5" y2="16.5"/></svg>
        <input placeholder="全局搜索：运行 / 套件 / 数据集…">
      </label>
      <div class="theme-wrap">
        <button class="icon-btn" id="themeBtn" title="切换主题">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><circle cx="8.5" cy="10" r="1.2" fill="currentColor" stroke="none"/><circle cx="12" cy="7.5" r="1.2" fill="currentColor" stroke="none"/><circle cx="15.5" cy="10" r="1.2" fill="currentColor" stroke="none"/><path d="M12 21a9 9 0 0 0 0-18"/><path d="M12 21c-4 0-3.5-4.5 0-4.5s2-4.5-1.5-4.5"/></svg>
        </button>
        <div class="theme-menu" id="themeMenu" hidden><div class="theme-head">主题配色</div><div id="themeList"></div></div>
      </div>
      <button class="icon-btn" title="任务/通知">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 0 1-3.46 0"/></svg>
        <span class="dot"></span>
      </button>
      <div class="avatar" title="zhimeng · 管理员">Z</div>
    </div>`;
  document.getElementById("mtabs").innerHTML = TOP_NAV.map(t =>
    `<a class="mtab" data-label="${t.label}" href="${t.href}">${t.label}</a>`).join("");
}

/* 每次路由（载入 / hashchange）都重算：tab 高亮、侧栏内容、通栏标记、当前页 */
function applyRoute() {
  const module = document.body.dataset.module;
  const pages = JSON.parse(document.getElementById("page-list").textContent);
  const isTask = !!DOMAIN_FILES[module];
  const curPageId = (location.hash || "").replace("#", "") || pages[0].id;
  const GROUP = { datasets: "数据集管理", models: "模型管理", agents: "执行机管理", tasks: "任务管理" };
  const grp = isTask ? "任务管理" : (GROUP[curPageId] || GROUP[pages[0].id]);
  if (module === "common") document.body.dataset.moduleLabel = grp;
  const activeLabel = isTask || grp === "任务管理" ? "任务管理" : grp;
  document.querySelectorAll("#mtabs .mtab").forEach(a => a.classList.toggle("active", a.dataset.label === activeLabel));
  const side = document.getElementById("sideNav");
  const sidePages = isTask ? pages : pages.filter(p => GROUP[p.id] === grp);
  // 单页一级模块（用例/数据集/模型/执行机管理）：左侧栏冗余，隐藏并让内容通栏
  const singlePage = !isTask && module === "common" && grp !== "任务管理" && sidePages.length <= 1;
  document.body.classList.toggle("no-side", singlePage);
  let sideHtml = "";
  const showSwitch = isTask || (module === "common" && curPageId === "tasks");
  if (showSwitch) {
    sideHtml += `<div style="padding:2px 10px 12px"><select id="domainSwitch" class="in" style="width:100%;height:32px;padding:4px 10px">` +
      Object.keys(DOMAIN_FILES).map(k => `<option value="${k}" ${k === module ? "selected" : ""}>${DOMAIN_LABELS[k]}域</option>`).join("") +
      `</select></div>`;
  }
  sideHtml += `<div class="nav-label">${document.body.dataset.moduleLabel}</div><nav class="side">` +
    sidePages.map(p => `<a data-page="${p.id}">${p.label}${p.count ? `<span class="count">${p.count}</span>` : ""}</a>`).join("") +
    `</nav><div class="side-foot"><span>aite v0.4 · 静态稿</span><a href="/docs" onclick="return false">API Docs</a></div>`;
  side.hidden = singlePage;
  side.innerHTML = sideHtml;
  side.querySelectorAll("nav a").forEach(a => a.addEventListener("click", () => showPage(a.dataset.page)));
  const sw = document.getElementById("domainSwitch");
  if (sw) sw.addEventListener("change", () => { location.href = DOMAIN_FILES[sw.value] + (location.hash || ""); });
  showPage(pages.some(p => p.id === curPageId) ? curPageId : pages[0].id);
}

function showPage(id) {
  document.querySelectorAll("main.page-host > section").forEach(s => (s.hidden = s.dataset.page !== id));
  document.querySelectorAll("#sideNav nav a").forEach(a => a.classList.toggle("active", a.dataset.page === id));
  history.replaceState(null, "", "#" + id);
  const page = (JSON.parse(document.getElementById("page-list").textContent) || []).find(p => p.id === id);
  const crumb = document.getElementById("crumb");
  if (crumb && page) crumb.textContent = `${document.body.dataset.moduleLabel} / ${page.label}`;
  window.dispatchEvent(new Event("page-shown"));
}

/* ============ 主题切换 ============ */
let curTheme = localStorage.getItem("aite-theme") || "obsidian";
function renderThemeMenu() {
  const list = document.getElementById("themeList");
  if (!list) return;
  list.innerHTML = Object.entries(THEMES).map(([k, t]) =>
    `<div class="theme-item ${k === curTheme ? "on" : ""}" data-k="${k}"><div><b>${t.name}</b><small>${t.desc}</small></div>
     <div class="dots">${t.dots.map(c => `<i style="background:${c}"></i>`).join("")}</div></div>`).join("");
  list.querySelectorAll(".theme-item").forEach(el => el.addEventListener("click", e => {
    e.stopPropagation(); applyTheme(el.dataset.k);
    const menu = document.getElementById("themeMenu"); if (menu) menu.hidden = true;
  }));
}
function applyTheme(key) {
  curTheme = key;
  document.body.dataset.theme = key;
  localStorage.setItem("aite-theme", key);
  renderThemeMenu();
  window.dispatchEvent(new Event("theme-changed"));
}
function initTheme() {
  if (!THEMES[curTheme]) curTheme = "obsidian";
  const btn = document.getElementById("themeBtn");
  const menu = document.getElementById("themeMenu");
  if (btn) btn.addEventListener("click", e => { e.stopPropagation(); menu.hidden = !menu.hidden; });
  document.addEventListener("click", e => { if (menu && !e.target.closest(".theme-wrap")) menu.hidden = true; });
  applyTheme(curTheme);
}

/* ============ ECharts 助手 ============ */
function hexA(hex, a) { const n = parseInt(hex.slice(1), 16); return `rgba(${n >> 16 & 255},${n >> 8 & 255},${n & 255},${a})`; }
function vGrad(hex, a) { return { type: "linear", x: 0, y: 0, x2: 0, y2: 1, colorStops: [{ offset: 0, color: hexA(hex, a) }, { offset: 1, color: hexA(hex, 0) }] }; }
function baseAxis(c) {
  return {
    axisLabel: { color: c.axis }, axisLine: { lineStyle: { color: c.axisLine } },
    splitLine: { lineStyle: { color: c.split } },
  };
}
function trendOption(t, dates, pass, err) {
  const c = t.charts;
  return {
    grid: { left: 6, right: 14, top: 12, bottom: 4, containLabel: true },
    tooltip: { trigger: "axis", backgroundColor: c.tipBg, borderColor: c.tipBorder, textStyle: { color: c.tipTx, fontSize: 12 },
      axisPointer: { lineStyle: { color: c.axisLine } }, extraCssText: c.tipShadow ? "box-shadow:0 6px 24px rgba(10,37,64,.16);" : "" },
    xAxis: { type: "category", boundaryGap: false, data: dates, ...baseAxis(c), splitLine: { show: false } },
    yAxis: { type: "value", min: 60, max: 100, ...baseAxis(c), axisLabel: { color: c.axis, formatter: "{value}%" } },
    series: [
      { name: "通过率", type: "line", smooth: true, symbol: "none", lineStyle: { width: 2.5, color: c.pass },
        areaStyle: { color: vGrad(c.pass, 0.18) }, data: pass },
      { name: "错误率", type: "line", smooth: true, symbol: "none", lineStyle: { width: 2, color: c.err, type: [6, 4] }, data: err },
    ],
  };
}
function radarOption(t, indicators, seriesData) {
  const c = t.charts;
  const mk = (name, value, col) => ({ name, value, lineStyle: { width: 2, color: col }, itemStyle: { color: col }, areaStyle: { color: hexA(col, 0.13) } });
  return {
    legend: { bottom: 0, textStyle: { color: c.axis, fontSize: 12 }, itemWidth: 9, itemHeight: 9, icon: "circle" },
    tooltip: { backgroundColor: c.tipBg, borderColor: c.tipBorder, textStyle: { color: c.tipTx, fontSize: 12 } },
    radar: { radius: "60%", center: ["50%", "46%"], indicator: indicators.map(n => ({ name: n, max: 100 })),
      axisName: { color: c.axis, fontSize: 12 }, splitArea: { areaStyle: { color: c.splitArea } },
      splitLine: { lineStyle: { color: c.radarSplit } }, axisLine: { lineStyle: { color: c.radarSplit } } },
    series: [{ type: "radar", symbolSize: 4,
      data: seriesData.map((s, i) => mk(s.name, s.value, c.radar[i % c.radar.length])) }],
  };
}
function perfLineOption(t, { title, dates, avg, p95, color }) {
  const c = t.charts;
  return {
    grid: { left: 6, right: 12, top: 28, bottom: 4, containLabel: true },
    tooltip: { trigger: "axis", backgroundColor: c.tipBg, borderColor: c.tipBorder, textStyle: { color: c.tipTx, fontSize: 12 } },
    legend: { top: 0, right: 0, textStyle: { color: c.axis, fontSize: 11 }, itemWidth: 12, itemHeight: 3, icon: "roundRect" },
    xAxis: { type: "category", boundaryGap: false, data: dates, ...baseAxis(c), splitLine: { show: false },
      axisLabel: { color: c.axis, formatter: v => v + "s" } },
    yAxis: { type: "value", ...baseAxis(c), axisLabel: { color: c.axis } },
    series: [
      { name: "avg", type: "line", smooth: true, symbol: "none", lineStyle: { width: 2.2, color },
        areaStyle: { color: vGrad(color, 0.12) }, data: avg },
      { name: "p95", type: "line", smooth: true, symbol: "none", lineStyle: { width: 1.6, color, type: [5, 4], opacity: 0.75 }, data: p95 },
    ],
  };
}
function autoCharts() {
  document.querySelectorAll(".chart[data-chart]").forEach(el => {
    const builder = window[el.dataset.chart];
    if (typeof builder !== "function") return;
    let inst = echarts.getInstanceByDom(el);
    if (!inst) inst = echarts.init(el);
    inst.setOption(builder(THEMES[curTheme]), true);
  });
  document.querySelectorAll(".chart").forEach(el => echarts.getInstanceByDom(el)?.resize());
}

/* ============ 通用小件 ============ */
function badge(status) {
  const map = { passed: "已通过", running: "运行中", failed: "失败", pending: "待评测/排队", stale: "已失联", cancelled: "已取消", succeeded: "已成功" };
  return `<span class="badge ${status}"><i></i>${map[status] || status}</span>`;
}
function passCell(pct) {
  if (pct === null || pct === undefined) return '<span class="faint">—</span>';
  const cls = pct >= 90 ? "" : pct >= 70 ? "mid" : "bad";
  return `<div class="passcell"><div class="track ${cls}"><i style="width:${pct}%"></i></div><span class="num">${pct.toFixed ? pct.toFixed(1) : pct}%</span></div>`;
}
function bindChips(root = document) {
  root.querySelectorAll(".chip[data-single]").forEach(c => c.addEventListener("click", () => {
    c.closest(".chips").querySelectorAll(".chip").forEach(x => x.classList.remove("on")); c.classList.add("on");
  }));
  root.querySelectorAll(".chip[data-multi]").forEach(c => c.addEventListener("click", () => c.classList.toggle("on")));
}
function bindRadioCards(root = document) {
  root.querySelectorAll(".radio-cards").forEach(group => group.querySelectorAll(".radio-card").forEach(card =>
    card.addEventListener("click", () => group.querySelectorAll(".radio-card").forEach(x => x.classList.remove("on")) || card.classList.add("on"))));
}
/* 预制/自定义切换：容器[data-src-toggle]内 .chip[data-src] 切换 [data-pane] 显隐 */
function bindSrcToggles() {
  document.querySelectorAll("[data-src-toggle]").forEach(box => {
    const apply = () => {
      const on = box.querySelector(".chip[data-src].on");
      const v = on ? on.dataset.src : "preset";
      box.querySelectorAll("[data-pane]").forEach(p => { p.hidden = p.dataset.pane !== v; });
    };
    box.querySelectorAll(".chip[data-src]").forEach(ch => ch.addEventListener("click", () => {
      box.querySelectorAll(".chip[data-src]").forEach(x => x.classList.remove("on"));
      ch.classList.add("on"); apply();
    }));
    apply();
  });
}

/* 目标模型下拉含「自定义」项：选中即展开手填端点字段 */
function bindModelCustom(selectId, paneId) {
  const sel = document.getElementById(selectId);
  const pane = document.getElementById(paneId);
  if (!sel || !pane) return;
  const sync = () => { pane.hidden = sel.value !== "__custom__"; };
  sel.addEventListener("change", sync);
  sync();
}

function bindDrawers() {
  document.querySelectorAll("[data-open-drawer]").forEach(btn => btn.addEventListener("click", () => {
    const d = document.getElementById(btn.dataset.openDrawer); if (d) d.hidden = false;
  }));
  document.querySelectorAll("[data-close]").forEach(el => el.addEventListener("click", e => {
    if (el.classList.contains("mask") && e.target !== el) return;   // 点在内容上不关闭
    const host = el.classList.contains("mask") ? el : el.closest(".drawer, .mask");
    host?.setAttribute("hidden", "");
  }));
}

/* 任务列表筛选条：状态 chips(data-status) + 执行人([data-owner]) + 关键词([data-kw]) → render(status, owner, kw) */
function bindTaskFilter(boxId, render) {
  const box = document.getElementById(boxId);
  if (!box) return;
  const chips = [...box.querySelectorAll(".chip[data-status]")];
  const owner = box.querySelector("[data-owner]");
  const kw = box.querySelector("[data-kw]");
  const apply = () => {
    const on = chips.find(c => c.classList.contains("on"));
    render(on ? on.dataset.status : "all", owner ? owner.value : "all", kw ? kw.value.trim().toLowerCase() : "");
  };
  chips.forEach(c => c.addEventListener("click", () => {
    chips.forEach(x => x.classList.remove("on")); c.classList.add("on"); apply();
  }));
  if (owner) owner.addEventListener("change", apply);
  if (kw) kw.addEventListener("input", apply);
  apply();
}

document.addEventListener("DOMContentLoaded", () => {
  buildShell();
  applyRoute();
  window.addEventListener("hashchange", applyRoute);
  initTheme();
  bindSrcToggles();
  bindChips(); bindRadioCards(); bindDrawers();
  autoCharts();
  window.addEventListener("resize", autoCharts);
  window.addEventListener("page-shown", autoCharts);
  window.addEventListener("theme-changed", autoCharts);
});
