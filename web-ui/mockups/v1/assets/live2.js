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
