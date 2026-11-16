# 效率优化台账

「效率复盘」中无法当场固化为规则或当场修复的低效点，记一行在此；能固化的规则
直接进 `AGENTS.md`，不入账。大任务开工前看尾部 10 条，能顺手消除的做掉并把
状态改为 done。

| 日期 | 低效点 | 建议动作 | 状态 |
|---|---|---|---|
| 2026-11-08 | 全量回归跑了 3 次（85~143s/次），改动其实是单域 | scripts/affected_tests.py：按 git diff 映射受影响测试文件（可复用 arch_map 依赖图） | done（2026-11-16 以 scripts/preflight.py 落地：选测（ast 导入图反向闭包）与门禁/重启判定合成一键收尾，未单独立 affected_tests.py） |
| 2026-11-08 | 精度前端 E2E 靠手工 ~15 步（起服/灌数/驱动/收尾） | 固化 scripts/e2e_acc_smoke.py：起隔离后端 + httpx 灌数 + 断言一条龙；浏览器只留视觉验收 | open |
| 2026-11-08 | pytest 汇总行被窄 tail/grep 吞掉，同一套回归重跑 3 次（54s/次） | 输出确认统一 `2>&1 \| tail -1` 直接取「N passed」行；首跑遵守 AGENTS.md 门禁速查的 venv 入口 | done |
| 2026-11-09 | 会话中途 AGENTS.md 被并行会话更新，本会话看不到新规则（IAB 坑重踩约 8 次调用） | context_sync 钩子已上线（tests/unit/test_zcode_hooks.py 通过），待 owner 批准。新会话核对：弹出审批时批准 3 条 context_sync 声明 → `python scripts/hooks/hook_status.py` 显示 4 条全部已批准、当前会话已运行 → 改一行 AGENTS.md 后下一次 Bash 收到差异；都通过即改 done | done（2026-11-10 核对：4 条全部已批准、当前会话已运行，改 AGENTS.md 后下一次 Bash 收到差异） |
| 2026-11-10 | 功能任务重试/复制的 IAB 验证，起隔离实例手工 6 步（起后端/探活/起反代/写灌数脚本/灌数/找页面路径），约 3 分钟 | 固化 scripts/iab_sandbox.py：一条命令起隔离后端（临时库）+ 反代 8776 + 可选灌数，打印页面 URL，退出时停服并删临时库；与 11-08 精度 E2E 项共用底座 | done（2026-11-12 落地 up/down 子命令 + PID/临时库清理，本会话冒烟通过；AGENTS.md 隔离实例条目已改为指向它） |
| 2026-11-10 | 抽屉改动检测依赖 isTrusted，IAB 真实输入摸索约 10 次调用（locator 超时、导航后 cua 零事件、cua.type 不可信、单调用超 110s） | iab_helpers.mjs 补 `cuaClick(tab, sel)`（滚动 + 取中心 + cua.click）、`cuaEdit(tab, sel)`（点中 + Backspace 产生可信 input）、`wakeInput(tab)`（截图唤醒输入通道）；规则已写进 AGENTS.md「IAB 真实输入」 | done（2026-11-10 本会话：cuaPoint/cuaClick/cuaEdit/wakeInput 已落 iab_helpers.mjs 并实测导入） |
| 2026-11-10 | 功能任务详情（api32.js showLiveTask/caseBlock）渲染改动缺轻量验证，本次临时写 vm 沙箱脚本（按函数边界切出 showLiveTask + 打桩 FAPI/document + 5 类用例 fixture）断言 HTML，一次通过 | 固化为 web-ui/mockups/render_check.cjs：可复用的切函数 + 打桩底座，fixture 放旁边 json；改详情渲染先跑它（秒级），IAB 只留视觉验收 | done（2026-11-11 落地 render_check.cjs+fixtures，5 类断言过）|
| 2026-11-09 | 向用户托管场景写数据前未重读落点现状（脚本 001 当天被 owner 改过、TC_002 已占用），写完才撞编号冲突，删建返工一轮（约 2 分钟） | 规则：向 data/ 托管根等用户共享目录写入前，先 cat 落点文件与登记（scripts.json/updated_at），编号避开已占用空间 | done（2026-11-11 补记） |
| 2026-11-13 | iab_sandbox 反代/后端 Popen 随前台 Bash 退出被杀（Windows job object），验证中途代理死→502/拒连空转约 3 次调用；state 文件 PID 与实际监听 PID 不一致也误导定位 | scripts/iab_sandbox.py 两处 Popen 加 Windows detached 进程组（creationflags=CREATE_NEW_PROCESS_GROUP\|DETACHED_PROCESS）+ state 记录后核验实际监听 PID；单独一轮做 + 沙箱起停回归 | open |
| 2026-11-17 | 精度趋势页（wireAccTrends）聚合逻辑冒烟靠临时 vm 脚本一次性验证后即删，下次改趋势页要重写 | 扩展 web-ui/mockups/render_check.cjs：增加 trends 断言组（聚合形状/基线/判定表/劣化卡/图表选项），fixture 复用合成任务+run 摘要 | open |
