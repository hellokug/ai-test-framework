# 多会话认领（活租约，不是账本）

多会话并行开发时在这里登记「谁此刻在改哪块」，context_sync 钩子会把它注入/推送给
所有会话。规则（详见 AGENTS.md「多会话并行认领」）：**动工前**看在认领，撞域就换域
或排队；**动工时**在「## 在认领」下加一行；**收尾删掉自己那行**——历史进
docs/efficiency-log.md / HANDOVER，本文件只留活跃租约。同一域同时只允许一个写者；
共享协作文档（CLAUDE.md / AGENTS.md / roadmap / HANDOVER / efficiency-log /
共享 assets 与 `?v=` bump）任何时候全局单写者。

格式：`- YYYY-MM-DD HH:MM｜会话/任务一句话｜认领范围（目录或文件）｜预计释放`

## 在认领

- 2026-11-12 23:50｜任务执行历史归属任务（owner 已拍板）：retry/重跑不再新建同级任务，同一任务多轮运行，每轮运行快照当时 spec（保审计）；详情页加「执行历史」轮次选择；copy 保留为分支变体；retry_of 列保留兼容旧链｜aite/core/taskkit.py + aite/storage/{models,repositories}.py + aite/web/routers/domain_tasks.py + aite/cli/task_cli.py + docs/domain-backends.md + 四域任务相关 tests + web-ui/mockups/v1/{api32.js,functional2.html,render_check.cjs,fixtures}（完成 bump ?v=18）｜预计 1-2 天，后端先行