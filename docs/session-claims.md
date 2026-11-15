# 多会话认领（活租约，不是账本）

多会话并行开发时在这里登记「谁此刻在改哪块」，context_sync 钩子会把它注入/推送给
所有会话。规则（详见 AGENTS.md「多会话并行认领」）：**动工前**看在认领，撞域就换域
或排队；**动工时**在「## 在认领」下加一行；**收尾删掉自己那行**——历史进
docs/efficiency-log.md / HANDOVER，本文件只留活跃租约。同一域同时只允许一个写者；
共享协作文档（CLAUDE.md / AGENTS.md / roadmap / HANDOVER / efficiency-log /
共享 assets 与 `?v=` bump）任何时候全局单写者。

格式：`- YYYY-MM-DD HH:MM｜会话/任务一句话｜认领范围（目录或文件）｜预计释放`

## 在认领

- 2026-11-15 23:59｜性能语料池选不了 ShareGPT 纠偏（owner 指正；根因：add_hf convert_records 只认扁平输入键，ShareGPT 纯对话记录整条被丢——fields=None 导入即 422「no usable records」；顺带真实导入一份 ShareGPT 语料进平台）｜aite/datasets/add_hf.py, tests/unit/test_add_hf.py｜预计释放 本会话收尾
