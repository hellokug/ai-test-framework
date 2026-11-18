#!/usr/bin/env node
/* 功能任务详情渲染检查（无浏览器秒级门禁）：按函数边界从 v1/api32.js 切出
 * showLiveTask（含 caseBlock），打桩 document/sessionStorage/FAPI 等全局，
 * 用 render_check_fixtures.json 的 5 类用例（断言失败/成功/流式/旧数据快照/
 * 执行错误）断言渲染出的 HTML。改详情渲染先跑它，IAB 只留视觉验收。
 *
 * 用法：node web-ui/mockups/render_check.cjs
 */
"use strict";
const fs = require("fs");
const path = require("path");
const assert = require("assert");

const src = fs.readFileSync(path.join(__dirname, "v1", "api32.js"), "utf8");
const start = src.indexOf("async function showLiveTask(taskId, roundSel)");
const end = src.indexOf("function backToRuns()");
assert(start > 0 && end > start, "api32.js 切出边界定位失败（showLiveTask/backToRuns）");
const sliced = src.slice(start, end);

const fx = JSON.parse(fs.readFileSync(path.join(__dirname, "render_check_fixtures.json"), "utf8"));

// —— 打桩全局：仅覆盖切片代码触达的面，不模拟真实 DOM ——
const detail = { innerHTML: "" };
const storage = { _m: new Map(), setItem(k, v) { this._m.set(k, v); },
  getItem(k) { return this._m.get(k) ?? null; }, removeItem(k) { this._m.delete(k); } };
global.window = { __copyStore: new Map(), __expStore: new Map(), __dlStore: new Map() };
global.sessionStorage = storage;
global.document = { querySelector: () => detail, addEventListener() {}, createElement: () => ({}) };
global.showPage = () => {};
global.fapiToast = () => {};
global.backToRuns = () => {};
global.fapiTaskAction = () => {};
global.taskBadge = s => `<span data-badge="${s}"></span>`;
global.badge = s => `<span data-badge="${s}"></span>`;
global.passCell = v => String(v);
global.fapiShortTime = iso => (iso || "").slice(5, 16);
global.showLiveTaskSeq = 0;   // 切片边界在 let 声明之后，全局补桩
global.FAPI = { async get(url) {
  if (url === `/functional/tasks/${fx.task.id}/result`) return { result: { run_id: fx.run.id } };
  if (url === `/functional/tasks/${fx.task.id}`) return fx.task;
  if (url === `/runs/${fx.run.id}`) return fx.run;
  if (url === `/runs/${fx.run.id}/cases`) return { items: fx.cases };
  throw new Error(`render_check 未打桩的 URL: ${url}`);
} };

// live2.js 共享内核（escHtml 等）在页面上先于 api32.js 加载，切片引用前先就位
(0, eval)(fs.readFileSync(path.join(__dirname, "v1", "assets", "live2.js"), "utf8"));
// eslint-disable-next-line no-eval -- 切片代码来自受版本控制的 api32.js，上下文全局已打桩
(0, eval)(sliced);

(async () => {
  await showLiveTask(fx.task.id);
  const html = detail.innerHTML;
  const byKey = {};   // 各用例块内断言：以 external_key 为界粗分块
  for (const c of fx.cases) {
    const at = html.indexOf(`>${c.external_key}</span>`);
    byKey[c.external_key] = at < 0 ? "" : html.slice(at, html.indexOf("case-item\"", at + 1));
  }
  const sec = (block, label) => block.slice(block.indexOf(label));
  // 内容断言跑在可见文本上：escHtml 升级后引号/尖括号以实体入库，先解码再匹配
  const txt = s => s.replace(/&quot;/g, '"').replace(/&#39;/g, "'")
    .replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&amp;/g, "&");

  // 1 断言失败（期望 400 实际 200）：断言判定行 + 请求头/体完整 + 错误首行
  const fail = byKey["assert-400"];
  assert(fail, "失败用例块缺失");
  assert(/期望 HTTP 400，实际 200/.test(fail), "失败首行（断言 400 vs 200）未展示");
  assert(fail.includes("请求头") && fail.includes("Authorization: Bearer ******"),
    "请求头未展示或鉴权头未打码");
  assert(fail.includes("X-Trace: 1"), "自定义请求头 X-Trace 未展示");
  const failReq = txt(sec(fail, "请求体"));
  assert(/"model"\s*:\s*"gpt-x"/.test(failReq), "请求体缺 model");
  assert(/"temperature"\s*:\s*0.7/.test(failReq) && /"max_tokens"\s*:\s*64/.test(failReq),
    "请求体缺扩展参数");
  const failAssert = sec(fail, "断言判定");
  assert(failAssert.includes("HTTP 状态码") && failAssert.includes("== 400"),
    "断言判定缺 HTTP 状态码期望行");
  assert(/<td[^>]*>200<\/td>/.test(failAssert), "断言判定缺实际值 200");
  assert(fail.includes(`响应体 · HTTP 200`), "响应体标题未带实际 HTTP 状态");

  // 2 成功（trace 完整）：断言判定 + 同块内含请求头 + 响应体
  const ok = byKey["trace-ok"];
  assert(ok && ok.includes("请求头") && ok.includes("X-Trace: 1"), "成功用例请求头未展示");
  const okAssert = sec(ok, "断言判定");
  assert(okAssert.includes("包含关键词") && okAssert.includes("hi"), "成功用例断言判定缺包含关键词行");
  assert(ok.includes("响应体") && /"content"\s*:\s*"hi/.test(txt(ok)), "成功用例响应体未展示");

  // 3 流式：body.stream=true 留痕原样
  const stream = byKey["stream-ok"];
  assert(stream && /"stream"\s*:\s*true/.test(txt(sec(stream, "请求体"))), "流式请求体缺 stream:true");

  // 4 旧数据（无 _request）：messages ⊕ params 快照，控制键不进、缺 model 标注
  const legacy = byKey["legacy-case"];
  assert(legacy.includes("旧数据无请求留痕"), "旧数据未标注回退口径");
  const legacyReq = txt(sec(legacy, "请求体"));
  assert(/"messages"/.test(legacyReq) && /"temperature"\s*:\s*1/.test(legacyReq),
    "旧数据快照缺 messages/params");
  assert(!/"_headers"/.test(legacyReq) && !/"_raw_body"/.test(legacyReq), "旧数据快照混入 _ 前缀控制键");
  assert(sec(legacy, "断言判定").includes("回复非空"), "旧数据断言判定缺非空行");

  // 5 执行错误（无响应记录）
  const err = byKey["net-err"];
  assert(err && err.includes("（无响应记录）"), "执行错误用例缺「无响应记录」占位");

  // 失败用例引擎原始日志直接展示（不再 details 折叠）
  assert(fail.includes("引擎原始日志"), "失败日志区缺失");
  assert(!/<details/.test(html), "引擎原始日志仍在用 details 折叠框");

  // 展开全文 id 跨用例唯一（历史缺陷：每例从 ex1 重新编号 → 展开串到别的用例）
  const expIds = [...html.matchAll(/data-exp-id="([^"]+)"/g)].map(m => m[1]);
  assert(expIds.length >= 2, "可展开区块不足 2 个（夹具长响应体缺失）");
  assert(new Set(expIds).size === expIds.length, "展开按钮 id 跨用例重复（折叠 200/展开 400 串块根因）");
  const preIds = [...html.matchAll(/<pre id="(ex[^"]+)"/g)].map(m => m[1]);
  assert(preIds.length === expIds.length && new Set(preIds).size === preIds.length,
    "pre id 与展开按钮未一一对应或不唯一");

  // 6 执行历史：轮次 chips 可切换 + 该轮 spec 快照；编辑/重跑按钮随非运行中显隐
  const hist = sec(html, "执行历史");
  assert(html.includes(`showLiveTask('${fx.task.id}', 1)`), "执行历史缺轮次切换入口");
  assert(hist.includes("#1") && hist.includes("#2"), "执行历史缺轮次 chip");
  assert(hist.includes("复制该轮 spec"), "执行历史缺该轮 spec 快照入口");
  assert(html.includes(">重跑<"), "终态任务缺重跑按钮");
  assert(html.includes("fapiTaskAction('edit'") === !["running", "stopping"].includes(fx.task.status),
    "编辑按钮未按非运行中状态渲染");

  // —— 通用分页条（live2.js uiPageSlice/uiPagerHtml/uiPagerBind，任务列表/数据集目录共用）：
  //    档位下拉 / 页码点击 / 最前最后直达 / 输入跳页四项能力 + 越界收拢、单页只留档位 ——
  {
    const rows = Array.from({ length: 37 }, (_, i) => i);      // 37 行 @15/页 = 3 页
    let p = uiPageSlice("t", rows);
    assert(p.pages === 3 && p.slice.length === 15 && p.slice[0] === 0, "首页切片 15 行失败");
    uiPageState("t").num = 2;
    p = uiPageSlice("t", rows);
    assert(p.slice[0] === 15 && p.slice.length === 15, "第 2 页切片偏移失败");
    uiPageState("t").num = 99;                                  // 越界 → 收拢回末页
    p = uiPageSlice("t", rows);
    assert(uiPageState("t").num === 3 && p.slice[0] === 30 && p.slice.length === 7, "页码越界未收拢回末页");
    uiPageState("t").size = 0;                                  // 「全部」→ 一页铺完
    p = uiPageSlice("t", rows);
    assert(p.pages === 1 && p.slice.length === 37, "档位「全部」未一页铺完");

    uiPageState("t").size = 15; uiPageState("t").num = 2;
    let bar = uiPagerHtml("t", 3);
    assert(bar.includes('data-pg-to="1"') && /title="跳至最前页"/.test(bar), "缺最前页直达按钮");
    assert(bar.includes('data-pg-to="3"') && /title="跳至最后页"/.test(bar), "缺最后页直达按钮");
    assert(bar.includes("data-pg-jump") && bar.includes("第 2 / 3 页"), "缺跳页输入/页位指示");
    assert(bar.includes("data-pg-size") && bar.includes("50 条/页") && bar.includes(">全部</option>"), "档位下拉缺 15/30/50/全部");
    assert((bar.match(/btn sm primary/g) || []).length === 1 && bar.includes('data-pg-to="2"'), "当前页未高亮且仅一处");
    bar = uiPagerHtml("t", 1);                                  // 单页：只留档位下拉（逃生口）
    assert(!bar.includes("data-pg-to") && bar.includes("data-pg-size"), "单页仍铺页码或缺档位下拉");
    bar = uiPagerHtml("t", Math.ceil(200 / 15));                // 14 页：首末 + 当前窗口 + 省略号
    assert(bar.includes("…") && bar.includes('data-pg-to="14"') && bar.includes('data-pg-to="1"'), "多页省略号/首末页缺失");

    // 委托行为：stub 宿主收监听器，模拟点击/档位/跳页回车
    const lsn = {};
    const host = { dataset: {}, addEventListener(t, fn) { (lsn[t] = lsn[t] || []).push(fn); } };
    const docQs = global.document.querySelector;
    global.document.querySelector = () => host;
    let renders = 0;
    uiPagerBind("#stub", "t2", () => renders++);
    uiPagerBind("#stub", "t2", () => renders++);                // 二次绑定应被 pgBound 挡住
    global.document.querySelector = docQs;
    const fakeBtn = to => ({ disabled: false, dataset: { pgTo: String(to) } });
    lsn.click.forEach(fn => fn({ target: { closest: s => s === "[data-pg-to]" ? fakeBtn(3) : null } }));
    assert(renders === 1 && uiPageState("t2").num === 3, "页码点击未翻页或重复绑定未挡");
    lsn.change.forEach(fn => fn({ target: { matches: s => s === "[data-pg-size]", value: "0" } }));
    assert(renders === 2 && uiPageState("t2").size === 0 && uiPageState("t2").num === 1, "档位切换未生效");
    uiPageState("t2").size = 15; uiPageState("t2").num = 1;
    lsn.keydown.forEach(fn => fn({ key: "Enter", target: { matches: s => s === "[data-pg-jump]", value: "abc" } }));
    assert(renders === 2, "非数字跳页输入不应触发重渲染");
    lsn.keydown.forEach(fn => fn({ key: "Enter", target: { matches: s => s === "[data-pg-jump]", value: "2" } }));
    assert(renders === 3 && uiPageState("t2").num === 2, "跳页回车未生效");
  }

  console.log(`render_check PASS：${fx.cases.length} 类用例渲染断言全过（断言判定/请求头/完整请求体/展开id唯一/日志直出/旧数据快照/错误占位/执行历史）+ 通用分页条（切片/越界收拢/四项能力/委托绑定）`);
})().catch(err => { console.error("render_check FAIL:", err.message); process.exit(1); });
