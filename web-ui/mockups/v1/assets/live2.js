/* live 对接层共享内核：accuracy2 / common2 / functional2 在 assets/v1.js 之后、
   页面内联脚本之前加载（perf 域暂独立用 api-perf.js 的 papiEsc，待其认领会话
   释放后统一收敛到这里）。live 域跨页面共用的工具函数放这里，别再各域拷贝。 */
function escHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g,
    c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
}
