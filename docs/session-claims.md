# 多会话认领（活租约，不是账本）

多会话并行开发时在这里登记「谁此刻在改哪块」，context_sync 钩子会把它注入/推送给
所有会话。规则（详见 AGENTS.md「多会话并行认领」）：**动工前**看在认领，撞域就换域
或排队；**动工时**在「## 在认领」下加一行；**收尾删掉自己那行**——历史进
docs/efficiency-log.md / HANDOVER，本文件只留活跃租约。同一域同时只允许一个写者；
共享协作文档（CLAUDE.md / AGENTS.md / roadmap / HANDOVER / efficiency-log /
共享 assets 与 `?v=` bump）任何时候全局单写者。

格式：`- YYYY-MM-DD HH:MM｜会话/任务一句话｜认领范围（目录或文件）｜预计释放`

## 在认领

- 2026-11-13 | 性能域前端对接：perf2.html + 新建 api-perf.js（任务/详情/大屏 timeseries/数据集真读写） | web-ui/mockups/v1/perf2.html + api-perf.js + web-ui/HANDOVER.md | 本轮内
- 2026-11-13 | perf 真实负载导入：csv/jsonl/ShareGPT 对话数组 → perf 型数据集上传 + 语料池对话格式适配 | aite/datasets/local.py + aite/tools/perf_dataset.py + aite/service/dataset_files.py + aite/web/routers + tests + docs/dataset-guide.md + roadmap | 本轮内

