---
name: mockup-verify
description: aite 静态稿（web-ui/mockups/v1）在内置浏览器里的交互验证 runbook：门禁、起实例、对接层就绪、isTrusted 真实输入、已知坑规避、收尾。当改动了 v1 页面、assets/v1.js、assets/v1.css、api32.js、api-acc3.js 需要在浏览器验证交互，或 owner 要求验证 / 截图静态稿功能时使用；纯后端接口冒烟、非静态稿前端不要加载。
---

# 静态稿 IAB 验证 runbook

按顺序执行。命令在仓库根的 Git Bash 跑；给 Windows 原生 exe 传 `D:/...` 风格路径。

## 1. 先过门禁（改动不合法，验证无意义）

- `.venv/Scripts/python.exe scripts/mockup_symbols.py --check`：跨脚本覆盖 / 同脚本重复定义 / `?v=` 不一致。
- 共享文件（`assets/v1.css`、`assets/v1.js`、`api32.js`、`api-acc3.js`）改动后，引用它的页面统一 bump 同一个 `?v=`。
- 同名函数以最后加载的对接层为准；查某函数在哪定义、哪处生效：`scripts/mockup_symbols.py <页面> <函数名…>`。

## 2. 起实例

- 验证要写数据 → 隔离实例（防脏 owner 数据）：
  `aite serve --port 8310 --db <临时库>` + `AITE_API=http://127.0.0.1:8310 python web-ui/mockups/serve.py 8776`
  （8766 常被 owner 占用），页面在 `/web-ui/mockups/v1/<页面>.html`，此时**不装** guardWrites。
- 只读验证 → 直连 owner 的 `http://127.0.0.1:8766`，每次页面（重新）加载后装 `guardWrites(tab)` 拦非 GET，
  结束看 `readGuard(tab)` 的 writes/errors 收口。

## 3. 等对接层就绪

`waitForApi(tab)`：等 `<html data-api-ready="live|mock">`（全部板块对接完、保存等监听绑完才落标记）。
加载后立刻点按钮会落空。

## 4. 选输入方式（决定成败的一步）

- 不依赖 `isTrusted`（开抽屉、点按钮后直接读 DOM）→ `domClick` / `domFill`。
- 依赖 `isTrusted`（改动检测 / dirty 标记等 `e.isTrusted` 判断）→ 只能 `tab.cua.*`，用 helpers 封装：
  - `cuaClick(tab, sel, text?)`：滚动到中心 + 坐标点击（text = 精确匹配可见元素文本）。
  - `cuaEdit(tab, sel, n)`：点中输入框 + End + n×Backspace（cua keypress 不能**插入**字符，只能删）。
  - `wakeInput(tab)`：页面加载/导航后 cua 事件整段不送达（页内零事件）时，截一张图即恢复。
- 已知坑：加载后第一击常被吞（先 `cua.move` 一发或 `wakeInput`）；`cua.click`/`cua.move` 各约 5 秒，
  一个 js 调用 ≤12 次（110 秒上限，超了拆批）；`locator(sel, { hasText })` 不按文本过滤（用属性选择器 +
  `cuaClick`）；Node 侧无全局 `fetch`，查后端放进 `tab.playwright.evaluate` 走页面同源 `/api`；
  截图单独一个 js 调用（偶发 30s 超时会拖垮同调用整串操作），截图本身也能唤醒输入通道。
- 原生 confirm/prompt/alert 取不到 → `stubDialogs(tab, { confirm, prompt })` 打桩。

## 5. 断言

读 DOM 事实（`waitUntil(tab, fn)` / evaluate），别靠截图猜页面状态。每批一个观察点，别截图 + 快照都要。

## 6. 收尾

`mockup_symbols.py --check` 已在改动时跑过 → 改动记 `web-ui/HANDOVER.md` 日期条目 → 效率复盘
（`session_stats.py` 并进最后一次核对命令）。
