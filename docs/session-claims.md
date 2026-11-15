# 多会话认领（活租约，不是账本）

多会话并行开发时在这里登记「谁此刻在改哪块」，context_sync 钩子会把它注入/推送给
所有会话。规则（详见 AGENTS.md「多会话并行认领」）：**动工前**看在认领，撞域就换域
或排队；**动工时**在「## 在认领」下加一行；**收尾删掉自己那行**——历史进
docs/efficiency-log.md / HANDOVER，本文件只留活跃租约。同一域同时只允许一个写者；
共享协作文档（CLAUDE.md / AGENTS.md / roadmap / HANDOVER / efficiency-log /
共享 assets 与 `?v=` bump）任何时候全局单写者。

格式：`- YYYY-MM-DD HH:MM｜会话/任务一句话｜认领范围（目录或文件）｜预计释放`

## 在认领

- 2026-11-15 23:22｜性能任务编辑能力（owner 指正「失败的任务不支持编辑」：列表/详情行加「编辑」→ 复用新建抽屉回填 spec，保存 = PATCH /perf/tasks/{id}；后端 kit.update pending+终态已支持且有集成测试，纯前端）；顺修 papiCollectSpec probe 分支 { max_parallel } 简写引用错变量（maxp，现状 probe 提交必 ReferenceError）｜web-ui/mockups/v1/{perf2.html,api-perf.js}（api-perf bump v8）｜预计释放 本会话收尾
- 2026-11-15 23:3x｜性能爬坡模式纠偏（owner 指正三点：①时长二选一不双开 ②档位 min/max/跨度自动生成不手写数组 ③ramp/rate 支持 SLA 逐档熔断自动停止+报告最大合规并发/QPS）——**后端立即动工**（perf_common 梯度展开+SLA 校验、perf_adapter 逐档执行+sla 结论、locust 显式拒 sla、单测补齐）；**前端 perf2.html/api-perf.js 与 23:22 撞域，排队等释放后动**｜aite/tools/{perf_common.py,perf_adapter.py,locust_adapter.py}, tests/{unit/test_perf_adapter.py,integration/test_perfdata_api.py}（前端待 23:22 释放）｜预计释放 本会话收尾
- 2026-11-15 23:59｜性能语料池选不了 ShareGPT 纠偏（owner 指正；根因：add_hf convert_records 只认扁平输入键，ShareGPT 纯对话记录整条被丢——fields=None 导入即 422「no usable records」；顺带真实导入一份 ShareGPT 语料进平台）｜aite/datasets/add_hf.py, tests/unit/test_add_hf.py｜预计释放 本会话收尾
