/* 静态稿 IAB 验证助手：绕开内置浏览器的已知坑（AGENTS.md「静态稿」节）。
   - playwright 可操作性等待常超时、抽屉滑入动画期坐标点击落空 → domClick / domFill 直接走 DOM；
   - IAB 后台标签页定时器重度节流，页面内 sleep 不可靠 → waitUntil 在 Node 侧轮询页面状态；
   - 静态稿对接真实后端（8300），验证时误触「保存」会写脏 owner 数据 → guardWrites 默认拦截非 GET。

   每个 js 调用照常先跑 control-browser 的 bootstrap、tabs.get 拿到 tab，然后：
     const { pathToFileURL } = await import("node:url");
     const h = await import(pathToFileURL("D:/Project/ai-test-framework/web-ui/mockups/iab_helpers.mjs").href);
     await h.guardWrites(tab);                          // 每次页面（重新）加载后装一次
     await h.waitForApi(tab);                           // 带对接层的页面：等监听全部绑定
     await h.domClick(tab, '[data-open-drawer="newScriptDrawer"]');
     await h.waitUntil(tab, () => !document.getElementById("newScriptDrawer").hidden);
     await h.domFill(tab, "#nsRows .ns-row .ns-desc", "描述");
     const { writes, errors } = await h.readGuard(tab);
   截图另起一个 js 调用：截图偶发 30s 超时，和操作串捆在一起会拖垮整串。
   传给 evaluate 的函数会序列化进页面执行，不能引用本模块或调用方的变量。 */

export async function domClick(tab, selector) {
  await tab.playwright.evaluate(sel => {
    const el = document.querySelector(sel);
    if (!el) throw new Error(`domClick: 找不到 ${sel}`);
    el.scrollIntoView({ block: "center" });
    el.click();
  }, selector);
}

export async function domFill(tab, selector, value) {
  await tab.playwright.evaluate(([sel, val]) => {
    const el = document.querySelector(sel);
    if (!el) throw new Error(`domFill: 找不到 ${sel}`);
    el.value = val;
    if (el.value !== String(val)) throw new Error(`domFill: ${sel} 没有取值为 ${val} 的选项`);
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
  }, [selector, value]);
}

export async function waitUntil(tab, predicate, { arg, timeoutMs = 5000, intervalMs = 150 } = {}) {
  const deadline = Date.now() + timeoutMs;
  let last;
  do {
    last = await tab.playwright.evaluate(predicate, arg);
    if (last) return last;
    await new Promise(resolve => setTimeout(resolve, intervalMs));
  } while (Date.now() < deadline);
  throw new Error(`waitUntil 超时 ${timeoutMs}ms：${String(predicate).slice(0, 120)} → ${JSON.stringify(last)}`);
}

/* functional2 / common2 / accuracy2 的对接层先异步探测后端、逐板块对接，最后才绑保存等监听；
   启动收尾时写 <html data-api-ready="live|mock">。加载后立刻点按钮会落在监听绑定之前（点了没反应）。 */
export async function waitForApi(tab, { timeoutMs = 20000 } = {}) {
  return waitUntil(tab, () => document.documentElement.dataset.apiReady, { timeoutMs, intervalMs: 250 });
}

/* 记录非 GET 请求与页面报错；block=true 时非 GET 直接回 418，不到达后端。 */
export async function guardWrites(tab, { block = true } = {}) {
  return tab.playwright.evaluate(blockWrites => {
    if (window.__iabGuard) return "already-installed";
    const guard = (window.__iabGuard = { writes: [], errors: [] });
    window.addEventListener("error", e => guard.errors.push(String(e.message || e)));
    window.addEventListener("unhandledrejection", e => guard.errors.push(`unhandledrejection: ${e.reason}`));
    const realFetch = window.fetch.bind(window);
    window.fetch = (input, init = {}) => {
      const method = String(init.method || (input instanceof Request ? input.method : "GET")).toUpperCase();
      if (method === "GET" || method === "HEAD") return realFetch(input, init);
      guard.writes.push(`${method} ${typeof input === "string" ? input : input.url}`);
      if (!blockWrites) return realFetch(input, init);
      return Promise.resolve(new Response(
        JSON.stringify({ detail: `iab guard 已拦截 ${method}（验证期不写后端）` }),
        { status: 418, headers: { "Content-Type": "application/json" } }));
    };
    const realOpen = XMLHttpRequest.prototype.open;
    XMLHttpRequest.prototype.open = function (method, url, ...rest) {
      if (!/^(GET|HEAD)$/i.test(method)) guard.writes.push(`${String(method).toUpperCase()} ${url}（XHR 未拦截）`);
      return realOpen.call(this, method, url, ...rest);
    };
    return "installed";
  }, block);
}

export async function readGuard(tab) {
  return tab.playwright.evaluate(() => {
    const guard = window.__iabGuard;
    return guard ? { writes: [...guard.writes], errors: [...guard.errors] } : null;
  });
}

/* 原生 confirm/prompt/alert 挂在宿主层，自动化取不到——统一打桩成固定回答。 */
export async function stubDialogs(tab, { confirm = true, prompt = "" } = {}) {
  await tab.playwright.evaluate(([answer, text]) => {
    window.confirm = () => answer;
    window.prompt = () => text;
    window.alert = () => {};
  }, [confirm, prompt]);
}

/* ---------- 真实（isTrusted）输入：抽屉改动检测这类 e.isTrusted 判断只能走 cua ----------
   坑：页面加载/导航后 cua 输入常整段不送达（页内零事件，wakeInput 截一张图即恢复）；
   cua.click / cua.move 各约 5 秒，一个 js 调用别超过约 12 次（110 秒上限）；
   keypress 不能插入字符，真实改值用编辑键（Backspace 删）。完整 runbook 见
   .agents/skills/mockup-verify。 */
export async function cuaPoint(tab, selector, text) {
  return tab.playwright.evaluate(([sel, t]) => {
    const el = [...document.querySelectorAll(sel)].find(e => e.getClientRects().length && (!t || e.textContent.trim() === t));
    if (!el) throw new Error(`cuaPoint: 找不到可见元素 ${sel}${t ? `（文本 ${t}）` : ""}`);
    el.scrollIntoView({ block: "center" });
    const r = el.getBoundingClientRect();
    return { x: Math.round(r.left + Math.min(r.width / 2, 40)), y: Math.round(r.top + r.height / 2) };
  }, [selector, text ?? null]);
}

export async function cuaClick(tab, selector, text) {
  await tab.cua.click(await cuaPoint(tab, selector, text));
}

/* 真实编辑输入框：点中 + End + n 次 Backspace（产生 isTrusted 的 input 事件） */
export async function cuaEdit(tab, selector, n = 1) {
  await tab.cua.click(await cuaPoint(tab, selector));
  await tab.cua.keypress({ keys: ["End"] });
  for (let i = 0; i < n; i += 1) await tab.cua.keypress({ keys: ["Backspace"] });
}

/* 输入通道唤醒：加载/导航后 cua 事件整段不送达时调用，截图一次即恢复 */
export async function wakeInput(tab) {
  await tab.screenshot();
  return "input-wake";
}
