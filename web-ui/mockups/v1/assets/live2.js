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
function shortTime(iso) { return (iso || "").slice(5, 16).replace("T", " "); }
function fmtDur(s) {
  return s == null ? "—" : s >= 3600 ? `${(s / 3600).toFixed(1)} 小时`
    : s >= 60 ? `${Math.round(s / 60)} 分钟` : `${Math.round(s)} 秒`;
}
