/* live 对接层共享内核：accuracy2 / common2 / functional2 在 assets/v1.js 之后、
   页面内联脚本之前加载（perf 域暂独立用 api-perf.js 的 papiEsc，待其认领会话
   释放后统一收敛到这里）。live 域跨页面共用的工具函数放这里，别再各域拷贝。 */
function escHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g,
    c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
}

/* 轻提示（fapiToast/aapiToast 是薄别名；perf 域 papiToast 待其认领会话释放后收敛） */
function liveToast(msg, bad = false) {
  const el = document.createElement("div");
  el.style.cssText = "position:fixed;top:16px;right:16px;z-index:99;max-width:440px;padding:10px 14px;" +
    "border-radius:10px;font-size:12.5px;border:1px solid;backdrop-filter:blur(6px);" +
    "background:" + (bad ? "rgba(60,20,20,.92)" : "rgba(20,32,48,.92)") + ";" +
    "border-color:" + (bad ? "rgba(251,113,133,.5)" : "rgba(96,165,250,.5)") + ";color:#e8eefc";
  el.textContent = msg;
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 4600);
}
/* 后端时间统一是 UTC aware ISO（+00:00），必须 parse 后按浏览器本地时区渲染，
   裸切片拿到的是 UTC 数字（本地看差 8 小时）；解析失败回退旧切片 */
function shortTime(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d)) return String(iso).slice(5, 16).replace("T", " ");
  const p = n => String(n).padStart(2, "0");
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}
function fmtDur(s) {
  return s == null ? "—" : s >= 3600 ? `${(s / 3600).toFixed(1)} 小时`
    : s >= 60 ? `${Math.round(s / 60)} 分钟` : `${Math.round(s)} 秒`;
}

/* 执行历史轮级起止（四域任务 history 每轮留档 started_at/finished_at，UTC ISO）：
   完整到秒的本地时间、紧凑起止段、轮耗时——functional/accuracy 执行历史卡共用 */
function liveFullTime(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (isNaN(d)) return String(iso);
  const p = n => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}
function roundDur(r) {
  return r && r.started_at && r.finished_at
    ? fmtDur((new Date(r.finished_at) - new Date(r.started_at)) / 1000) : null;
}
function roundSpan(a, b) {   // 同日 HH:MM→HH:MM；跨日 HH:MM→MM-DD HH:MM；未收口只显示开始
  if (!a) return "—";
  const s = shortTime(a), e = b ? shortTime(b) : "";
  return !e ? s : s.slice(0, 5) === e.slice(0, 5)
    ? `${s.slice(6)}→${e.slice(6)}` : `${s.slice(6)}→${e}`;
}

/* 在线数据集「本机已有/近似」徽章：common2 mock 渲染与 api32 live 渲染同款，实现只此一份 */
function dsOnlineBadgeCell(b) {
  if (b.local) return `<span class="badge passed"><i></i>本机已有 · ${b.local}</span>`;
  if (b.local_like) return `<span class="badge pending"><i></i>近似 · ${b.local_like}</span>`;
  return '<span class="dim">—</span>';
}

/* ---------------- 通用分页条（任务列表 runs / 数据集目录 ds 共用；页面内联脚本前已加载） ----------------
   四项能力：每页条数档位下拉（15/30/50/全部）、页码点击（≤9 全铺，>9 首末+当前窗口+省略号）、
   最前/最后直达（« »）、输入页码回车精准跳页（越界自动收拢回合法页）。
   状态挂 window.UI_PAGES[key]；宿主容器由各域对接层（api32/api-acc3）调 uiPagerBind
   一次性事件委托（rerender 回调各域自带），操作后重渲染。 */
window.UI_PAGES = {};                       // key → { num, size }（size 0 = 全部一页铺完）
function uiPageState(key) { return window.UI_PAGES[key] || (window.UI_PAGES[key] = { num: 1, size: 15 }); }
function uiPageSlice(key, rows) {           // 切片 + 总页数；num 越界自动收拢回合法页
  const st = uiPageState(key);
  const size = st.size > 0 ? st.size : (rows.length || 1);
  const pages = Math.max(1, Math.ceil(rows.length / size));
  st.num = Math.min(Math.max(1, st.num), pages);
  return { pages, slice: rows.slice((st.num - 1) * size, st.num * size) };
}
function uiPagerHtml(key, pages) {          // 页码条；单页不铺页码但保留档位下拉（「全部」的逃生口）
  const st = uiPageState(key);
  const n = st.num;
  const btn = (to, label, o = {}) =>
    `<button class="btn sm${o.cur ? " primary" : ""}" data-pg-to="${to}"${o.dis ? " disabled" : ""}${o.title ? ` title="${o.title}"` : ""}>${label}</button>`;
  const sizeSel = `<select class="in" data-pg-size title="每页行数；「全部」一页铺完不翻页" style="width:auto">` +
    [15, 30, 50].map(s => `<option value="${s}"${st.size === s ? " selected" : ""}>${s} 条/页</option>`).join("") +
    `<option value="0"${!st.size ? " selected" : ""}>全部</option></select>`;
  const nav = pages <= 1 ? "" :
    btn(1, "«", { dis: n <= 1, title: "跳至最前页" }) +
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
    btn(pages, "»", { dis: n >= pages, title: "跳至最后页" }) +
    `<span class="hint">第 ${n} / ${pages} 页 · 跳至</span>` +
    `<input class="in mono" data-pg-jump inputmode="numeric" style="width:52px;text-align:center" value="${n}" aria-label="页码，回车跳转">` +
    `<span class="hint">页</span>`;
  return `<div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap;justify-content:flex-end;margin-top:10px">${nav}${sizeSel}</div>`;
}
/* 宿主容器一次性事件委托：页码/«»/上下页点击、档位 change、跳页输入回车 → 改状态后 rerender() */
function uiPagerBind(hostSel, key, rerender) {
  const host = document.querySelector(hostSel);
  if (!host || host.dataset.pgBound) return;
  host.dataset.pgBound = "1";
  host.addEventListener("click", e => {
    const b = e.target.closest("[data-pg-to]");
    if (!b || b.disabled) return;
    const st = uiPageState(key);
    const p = b.dataset.pgTo;
    st.num = p === "prev" ? st.num - 1 : p === "next" ? st.num + 1 : Number(p) || 1;
    rerender();
  });
  host.addEventListener("change", e => {
    if (!e.target.matches("[data-pg-size]")) return;
    const st = uiPageState(key);
    st.size = Number(e.target.value) || 0;   // 0 = 全部
    st.num = 1;
    rerender();
  });
  host.addEventListener("keydown", e => {
    if (!e.target.matches("[data-pg-jump]") || e.key !== "Enter") return;
    const v = parseInt(e.target.value, 10);
    if (!Number.isFinite(v)) return;         // 非数字忽略不跳
    uiPageState(key).num = v;                // 越界由 uiPageSlice 收拢回合法页
    rerender();
  });
}
