"""架构地图生成器：docs/architecture-map.html（给 owner 的单一掌控入口）。

设计原则（对应 CLAUDE.md）：
- 事实从代码与 git 实时提取（import 图 / 行数 / 提交热度 / REST 清单 / CLI 清单 /
  注册表 / 分层合同），不手抄行数——文档过期即撒谎；
- 人工知识（意图、任务、适配器写法、文档裁决）以字面数据内置，逐条 _check() 校验，
  代码漂移即生成失败（原则 9：错误显式化，禁止静默产出过期地图）；
- 重构后重跑 ``python scripts/arch_map.py`` 即可刷新地图（--pytest 额外跑全量测试）。
"""

from __future__ import annotations

import argparse
import ast
import re
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import tomllib

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "architecture-map.html"
HTTP_METHODS = {"get", "post", "put", "delete", "patch", "head", "options"}


def _check(cond: bool, msg: str) -> None:
    if not cond:
        raise SystemExit(f"[arch_map] 校验失败：{msg}（地图会撒谎，拒绝生成）")


# ---------------------------------------------------------------- 代码事实采集

def load_layers() -> list[list[str]]:
    """分层顺序以 pyproject [tool.importlinter] 合同为唯一事实来源。"""
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    layers = data["tool"]["importlinter"]["contracts"][0]["layers"]
    return [[p.strip() for p in layer.split("|")] for layer in layers]


def scan_modules(layers: list[list[str]]) -> dict[str, dict]:
    mods: dict[str, dict] = {}
    for p in sorted((ROOT / "aite").rglob("*.py")):
        parts = list(p.with_suffix("").relative_to(ROOT).parts)
        if parts[-1] == "__init__":
            parts = parts[:-1]
        name = ".".join(parts)
        if name == "aite":
            continue
        layer = next((i for i, prefixes in enumerate(layers)
                      if any(name == x or name.startswith(x + ".") for x in prefixes)), None)
        mods[name] = {"path": p, "layer": layer, "is_pkg": p.name == "__init__.py"}
    return mods


def parse_imports(mods: dict[str, dict]) -> tuple[set, set]:
    """返回 (文件级依赖边, 注册边)。注册边 = 包 __init__ 中带 noqa: F401 的子模块导入
    （import 即注册惯例），画图时与普通依赖区分。TYPE_CHECKING 块不计入。"""
    edges: set[tuple[str, str]] = set()
    reg: set[tuple[str, str]] = set()
    for name, m in mods.items():
        lines = m["path"].read_text(encoding="utf-8").splitlines()
        tree = ast.parse("\n".join(lines))
        pkg = name if m["is_pkg"] else name.rpartition(".")[0]
        for node in ast.walk(tree):
            if isinstance(node, ast.If) and "TYPE_CHECKING" in ast.unparse(node.test):
                continue
            targets: list[str] = []
            if isinstance(node, ast.Import):
                targets = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                base = node.module or ""
                if node.level:
                    seg = pkg.split(".")
                    if node.level > 1:
                        seg = seg[: -(node.level - 1)]
                    base = ".".join(seg + ([base] if base else []))
                for a in node.names:
                    full = f"{base}.{a.name}"
                    targets.append(full if full in mods else base)
            for t in targets:
                if not t.startswith("aite") or t == name or t not in mods:
                    continue
                edges.add((name, t))
                if m["is_pkg"] and t.startswith(name + ".") \
                        and "noqa: F401" in lines[node.lineno - 1]:
                    reg.add((name, t))
    return edges, reg


def commit_heat() -> dict[str, int]:
    out = subprocess.run(["git", "log", "--format=%x00", "--name-only", "--", "aite/"],
                         capture_output=True, text=True, encoding="utf-8", errors="replace",
                         cwd=ROOT)
    heat: dict[str, int] = defaultdict(int)
    for block in out.stdout.split("\x00"):
        for f in {ln.strip() for ln in block.splitlines() if ln.strip().startswith("aite/")}:
            heat[f] += 1
    return heat


# --------------------------------------------------------- 接口面提取（REST/CLI）

def extract_rest() -> list[dict]:
    rows: list[dict] = []
    for p in sorted((ROOT / "aite" / "web" / "routers").glob("*.py")):
        if p.stem == "__init__":
            continue
        tree = ast.parse(p.read_text(encoding="utf-8"))
        prefix = ""
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                rows.extend(_route_rows(node, p.name, prefix))
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
                prefix = _router_prefix(node.value) or prefix
    return rows


def _route_rows(fn_node, fname: str, prefix: str) -> list[dict]:
    rows = []
    for dec in fn_node.decorator_list:
        if not (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)
                and dec.func.attr in HTTP_METHODS):
            continue
        path = "<动态>"
        if dec.args and isinstance(dec.args[0], ast.Constant):
            path = str(dec.args[0].value)
        rows.append({"file": fname, "method": dec.func.attr.upper(),
                     "path": prefix + path, "fn": fn_node.name})
    return rows


def _router_prefix(call: ast.Call) -> str | None:
    for kw in call.keywords:
        if kw.arg == "prefix" and isinstance(kw.value, ast.Constant):
            return str(kw.value.value)
    return None


def extract_cli() -> tuple[list[dict], list[str]]:
    tree = ast.parse((ROOT / "aite" / "cli" / "main.py").read_text(encoding="utf-8"))
    helps: dict[str, str] = {}
    groups: list[dict] = []
    tops: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call) \
                and isinstance(node.value.func, ast.Attribute) and node.value.func.attr == "Typer":
            helps[node.targets[0].id] = _kw_str(node.value, "help") or ""
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr == "add_typer" and node.args \
                and isinstance(node.args[0], ast.Name) and node.args[0].id != "domain_app":
            groups.append({"name": _kw_str(node, "name") or node.args[0].id,
                           "help": helps.get(node.args[0].id, ""), "line": node.lineno})
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and _is_app_command(node):
            tops.append(_kw_str_decor(node, "name") or node.name)
    return groups, sorted(tops)


def _is_app_command(fn_node) -> bool:
    for dec in fn_node.decorator_list:
        if (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)
                and dec.func.attr == "command" and isinstance(dec.func.value, ast.Name)
                and dec.func.value.id == "app"):
            return True
    return False


def _kw_str_decor(fn_node, arg: str) -> str | None:
    for dec in fn_node.decorator_list:
        if isinstance(dec, ast.Call):
            got = _kw_str(dec, arg)
            if got is not None:
                return got
    return None


def _kw_str(call: ast.Call, arg: str) -> str | None:
    for kw in call.keywords:
        if kw.arg == arg and isinstance(kw.value, ast.Constant):
            return str(kw.value.value)
    return None


def run_gate(cmd: list[str], timeout: int = 180, detail_re: str = "") -> dict:
    try:
        p = subprocess.run([sys.executable, *cmd], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", cwd=ROOT, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "detail": f"无法执行: {exc}"}
    lines = (p.stdout + "\n" + p.stderr).strip().splitlines()
    if detail_re:
        picked = [ln for ln in lines if re.search(detail_re, ln)]
        if picked:
            return {"ok": p.returncode == 0, "detail": picked[-1].strip()}
    return {"ok": p.returncode == 0, "detail": lines[-1].strip() if lines else ""}


# ------------------------------------------------------------ 内置人工知识（带校验）

LAYER_INTENT = {
    "aite.cli": "命令行入口：run/serve/list + dataset/bench/tool/common/case-doc 等管理子命令；同步薄壳，业务在 service",
    "aite.web": "HTTP/SSE 入口：18 个 router 挂 /api/v1 + 静态 SPA；路由只做「校验 → service → 返回」",
    "aite.service": "CLI/Web 共享编排层：suite 双路由、四域任务装配、资产目录、版本归档、执行机纳管——加编排逻辑来这里",
    "aite.tools": "开源引擎适配层（平台核心价值）：ToolAdapter 三步契约，suite 的 engine: 字段路由到这里",
    "aite.chaos": "可靠性故障注入：发现/注入/观测三层，独立注入器注册表",
    "aite.functional": "pytest 插件 harness + cases.jsonl 摄取（功能域子进程内运行的世界）",
    "aite.storage": "持久层：ORM 模型（契约级）+ Repository 唯一实现 + 只加列迁移；所有 SQL 都在这里",
    "aite.gateway": "LLM 网关：judge/LLM 指标的统一推理通道（限流 / 成本熔断 / 磁盘缓存）",
    "aite.datasets": "插件：数据集加载与目录（builtin 采样集 / local / hf_hub / catalog / 外部格式转换）",
    "aite.connectors": "插件：被测系统适配（openai_compatible 主力 + anthropic/gemini/custom_http/离线文件/langchain）",
    "aite.metrics": "插件：存量自研指标（冻结态，禁止新增算法）+ judge（score/pairwise/multi）",
    "aite.reporting": "插件：html/json/rich/junit 渲染器 + 从 DB 重建报告",
    "aite.core": "契约与自研引擎：types/interfaces（冻结）+ registry/loader/engine/aggregator/taskkit/profiles",
    "aite.events": "进程内 EventBus（SSE 事件源，分布式预留同形协议）",
    "aite.utils": "无业务依赖底座：文本/异步/计价/密钥库/日志",
}

CONTRACT_FILES = [
    ("aite/core/interfaces.py", "Repository 等 6 类抽象端口；改动 = 架构变更"),
    ("aite/core/types.py", "全平台数据契约（pydantic 模型）"),
    ("aite/storage/models.py", "全部表结构；Schema 只增不改"),
    ("aite/web/routers/", "/api/v1 路由契约（前端唯一依赖面）"),
]

ADAPTERS = [
    {"engine": "evalscope", "file": "aite/tools/evalscope_adapter.py",
     "runs": "进程内 run_task()（extra_args.python_exe 可切子进程+文件契约）",
     "wraps": "evalscope 精度基准（可切 OpenCompass/VLMEvalKit/RAGEval 后端）",
     "dir": "越高越好",
     "notes": "断点续推 resume auto/force/off + tool_identity_hash 身份指纹；judge 判官经 common.yaml 预制"},
    {"engine": "evalscope-perf", "file": "aite/tools/perf_adapter.py",
     "runs": "子进程 evalscope CLI（包无 __main__，shutil.which 定位）",
     "wraps": "evalscope perf——LLM 压测（TTFT/吞吐/延迟分档）",
     "dir": "越低越好",
     "notes": "四模式 baseline/ramp/probe/soak；时长双键 duration（整场）/level_duration（每档）互斥"},
    {"engine": "locust", "file": "aite/tools/locust_adapter.py",
     "runs": "子进程 locust --headless 逐档执行",
     "wraps": "locust 通用 HTTP 压测",
     "dir": "越低越好",
     "notes": "locustfile 由适配器生成（SSE TTFT 自定义事件）；到达模型 poisson/spike/custom；不支持 probe"},
    {"engine": "pytest", "file": "aite/tools/pytest_adapter.py",
     "runs": "子进程 pytest -p aite.functional.harness 逐场景 + junit-xml 收集",
     "wraps": "pytest（功能测试：9 场景 64 例 + 用户自建目录）",
     "dir": "越高越好",
     "notes": "tools.cases 按 nodeid 精确定向；模型访问经 harness 插件 fixture"},
    {"engine": "evalscope-rageval", "file": "aite/tools/rageval_adapter.py",
     "runs": "进程内 run_task()（RAGEval 后端）",
     "wraps": "ragas（经 evalscope RAGEval 官方后端）——RAG 五指标",
     "dir": "越高越好",
     "notes": "平台零指标算法：只做字段映射 / critic_llm+embeddings 注入 / 分数归一；A/B 编排按 records 有无 response 自动分派"},
]

RECIPE = [
    ("aite/tools/<name>_adapter.py",
     "新建文件：@register_tool(\"<engine>\") 装饰适配器类，继承 ToolAdapter（aite/tools/base.py）"),
    ("build_config(req)", "平台 ToolRunRequest → 工具原生配置。纯函数、不碰网络，单测直接喂 dict"),
    ("async run(req)",
     "执行：子进程型走 tools/subproc.py（run_tool_subprocess + which_or_fail），进程内 SDK 用 run_sync_in_thread 包裹；缺依赖 raise RuntimeError 带安装指引"),
    ("collect(req)",
     "收集产出 → ToolRunResult（rows 指标行含 higher_is_better 方向 + failures 失败明细）；容错跳过损坏文件必须告警"),
    ("list_tasks()", "枚举引擎侧基准/场景清单（REST /tools/benchmarks 与前端选择器的数据源）"),
    ("aite/tools/__init__.py", "顶部加一行 import（noqa: F401  # (registers)）——import 即注册"),
    ("注册表自动生效",
     "list_tools() / GET /api/v1/tools/engines / engine 路由自动认识新引擎；不需要改任何 if-else"),
    ("tests/unit/test_<name>_adapter.py",
     "单测 build_config/collect 纯函数（fake server 模式参照 test_perf_adapter.py）；跑该域测试 + ruff + lint-imports"),
]

DOCS = [
    ("README.md", "用户手册总入口：快速开始 / 四域用法 / Web 平台 / 执行机", "保留（能力变更时同步）"),
    ("CLAUDE.md", "给协作者（含 AI）的宪法：定位红线 + 14 条架构原则 + 协作与教学总纲 + 流程与门禁", "保留——这是掌控的锚，最高优先级文档"),
    ("docs/architecture.md", "四层架构 + 数据流 + 存储策略（一页纸）", "保留；目录树小节已被本地图接管，可精简"),
    ("docs/architecture-map.html", "本地图：实时事实 + 全景 + 资产清单（arch_map.py 生成）", "保留——重构后重跑脚本再生，不手改"),
    ("docs/roadmap.md", "计划 + 全部决策与踩坑的唯一账本", "保留"),
    ("docs/handover.md", "分域验证命令 / 踩坑索引 / Backlog / 开工模板", "与本地图重复约半：验证命令与 Backlog 保留，模块地图节并入本地图后归档"),
    ("docs/config-reference.md", "suite YAML 全字段参考", "保留"),
    ("docs/domain-backends.md", "四域任务契约设计稿 + 实调记录（880 行，最长）",
     "§1-9 契约已落地为代码（taskkit/domain_tasks），契约节并入 architecture.md；§10-13 讨论稿随实施收割进 roadmap，原文归档（roadmap 已记账）"),
    ("docs/functional-testing.md", "功能域深读：设计决策记录（为什么这么做）", "保留——领域深读的样板"),
    ("docs/plugin-guide.md", "四类组件插件开发指南", "保留"),
    ("docs/dataset-guide.md", "数据集三类型 + 离线导入", "保留"),
    ("docs/compat.md", "evalscope/lm-eval 三层兼容策略（一页纸）", "保留"),
    ("docs/archive/ollama-guide.md", "本机 Ollama 操作手记（已归档 2026-11-08）", "已归档：个人环境存档，不再更新"),
    ("docs/archive/requirements-datasetgov.md", "数据集治理需求存档（已明确暂缓，2026-11-08 归档）", "已归档：恢复实施时从 M0 开始"),
    ("docs/archive/test-designs/deepseek-inference-api.md", "具体模型的 API 测试设计（工作物，2026-11-08 移入归档）", "不是平台文档：属用平台产出的工作物存档"),
    ("web-ui/README.md", "前端现状 + Vite 迁移路径", "保留"),
    ("web-ui/V1-PLAN.md", "V1 页面规划（P0/P1/V2 标注）", "保留；V1 交付后归档"),
    ("web-ui/HANDOVER.md", "前端会话交接便签（多轮堆叠，其他会话仍以其为开工必读）", "暂缓收割（roadmap 已记账）：静态稿主线稳定后收割进 V1-PLAN 再归档"),
    ("web-ui/TOOL-SUITE-PARAMS.md", "精度任务参数契约（前端对接清单，已实装）", "暂缓收割（roadmap 已记账）：并入 docs/config-reference.md 精度章后归档原文"),
]

CRUFT = [
    ("aite.db / -shm / -wal / .bak-*", "运行时数据库与备份（.gitignore 已覆盖）", "bak 删除；db 是你的本地数据，自行处置"),
    ("cc_check.json / _probe.js", "根目录调试探针（未 ignore、未提交）", "删除或移入临时目录"),
    ("perf_dashboard_*.html", "压测大屏预览产物（.gitignore 已覆盖）", "删除（脚本可再生）"),
    ("outputs/ reports/ data/", "运行产物与导入数据（.gitignore 已覆盖）", "保留（data/datasets/debug 随仓库分发）"),
    ("web-ui/mockups/A-*.html B-*.html C-*.html", "5 个早期主题探索稿", "保留被 V1-PLAN 引用的 A2/A3，其余归档"),
    ("web-ui/mockups/v1/_patch_*.py _blk*.js _t*.js", "一次性补丁脚本与临时代码块（跑完即删是它们自己的约定）", "已执行完的删除"),
]

GLOSSARY = [
    ("Suite / 套件", "一次评测的完整声明（YAML 文件或 spec 字典）：被测 target + 数据集 + 指标阈值 + 报告格式。loader.py 的 pydantic 模型是它的权威定义"),
    ("engine", "suite 里的路由开关：写了 engine: 就路由到 aite/tools 适配器，不写就走自研引擎"),
    ("Run", "suite 的一次执行实例（test_run 表）。工具域与自研域落同一张表，所以趋势页能并列对比"),
    ("Case / 用例", "最小评测单元（输入+预期）。可靠性域会展开 repeat-N / perturb-* 变体再聚合回原 case"),
    ("Domain / 域", "accuracy / functional / perf / reliability 四个评测域，各有独立的任务 API、CLI 子命令和任务表"),
    ("Task / 任务", "四域后台任务（bg_task + 各域任务表），统一状态机 create→running→succeeded/failed/stale（DomainTaskKit）"),
    ("Target / 被测系统", "你要评的东西（OpenAI 兼容端点为主），由 connectors 适配；common.yaml 里可存预制（profile）"),
    ("ToolAdapter", "开源引擎适配器契约：build_config → run → collect（+list_tasks），注册表分发"),
    ("Registry / 注册表", "名称→组件的字典。全项目三套同构：core/registry.py（连接器/数据集/指标/判官/渲染器）、tools/base.py（适配器）、chaos/injectors/base.py（注入器）"),
    ("断点续推", "evalscope 工具域能力：run_state.json 记录完成态凭证 + 身份指纹（tool_identity_hash），没跑完续推、跑完了才全新"),
    ("SSE 事件流", "实时进度：频道 runs:{task_id} 与 {D}:tasks:{id}，EventBus 进程内发布，Web 以 /stream 端点透出"),
    ("上下文收敛", "Web 启动时把上次进程崩溃残留的 running 任务统一标 error/stale（AppState.startup），状态机闭合"),
]

CONVENTIONS = [
    ("契约文件改动 = 架构变更", "interfaces.py / types.py / models.py / /api/v1 路由：先讨论 → roadmap 记录 → 独立小 PR"),
    ("不造指标", "新评测能力 = aite/tools 新适配器；metrics/ 冻结"),
    ("每次提交前", "ruff check aite tests + lint-imports + 改动域测试；全量 pytest 留给跨域重构/发布前"),
    ("改默认值", "只进 aite/config.py；密钥走 api_key_env 环境变量"),
    ("函数 ≤ 80 行", "scripts/audit_func_len.py 机器审计；路由只做校验→service→返回"),
    ("Schema 只增不改", "加列/加表进 models.py + migrations.py；破坏性变更走新表回填"),
    ("表驱动 / 注册表", "组件分发用注册表，配置值分派用 dict；禁止 if-elif 链膨胀"),
    ("重构后", "python scripts/arch_map.py 再生本地图 + docs/roadmap.md 记进度"),
]

ESCALATION = [
    ("L1 一看就懂", "本地图 + README 对应章节；改 YAML/配置层能解决的事不动代码"),
    ("L2 域内改动", "只动一个域的文件（适配器/场景/路由）+ 该域测试；契约文件零改动"),
    ("L3 跨域改动", "共享收口文件（cli/main.py / repositories.py / tools/__init__.py / service）——只追加不重排他人段落；改动前在本图核对依赖方向"),
    ("L4 契约改动", "必须先对齐讨论 → roadmap 记录 → 独立小 PR 串行合入，然后才开域分支"),
]


# ------------------------------------------------- 数据结构与必要性（奥卡姆）

def extract_types() -> list[dict]:
    """core/types.py 的数据契约：类名 + 字段（名: 类型）。"""
    out: list[dict] = []
    tree = ast.parse((ROOT / "aite" / "core" / "types.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        fields = []
        for stmt in node.body:
            if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                fields.append(f"{stmt.target.id}: {ast.unparse(stmt.annotation).split(' = ')[0]}")
        out.append({"name": node.name, "base": ast.unparse(node.bases[0])
                    if node.bases else "", "fields": fields})
    return out


TABLE_NOTES = {
    "test_run": "每次执行一行：状态/成本/时长/引擎路由（含工具域）",
    "run_metric_summary": "统一结果模型：指标×数据集聚合行 + overall 加权行（趋势页数据源）",
    "test_case_result": "逐 case 执行快照（Phase A 物化的 responses）",
    "metric_result": "逐 case×指标原始分",
    "bg_task": "后台任务（Web 发起），启动时收敛崩溃残留",
}


def extract_tables() -> list[dict]:
    """storage/models.py 的表清单：表名 + 列数（含 mixin 基类列，live 提取）。"""
    tree = ast.parse((ROOT / "aite" / "storage" / "models.py").read_text(encoding="utf-8"))
    mixin_cols: dict[str, int] = {}
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and not any(
                getattr(s, "value", None) and isinstance(getattr(s, "value", None), ast.Constant)
                for s in node.body if isinstance(s, ast.Assign)):
            mixin_cols[node.name] = sum(1 for s in node.body if isinstance(s, ast.AnnAssign))
    out: list[dict] = []
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        table, cols, mixins = None, 0, []
        for stmt in node.body:
            if isinstance(stmt, ast.Assign) and isinstance(stmt.value, ast.Constant) \
                    and getattr(stmt.targets[0], "id", "") == "__tablename__":
                table = str(stmt.value.value)
            elif isinstance(stmt, ast.AnnAssign):
                cols += 1
        for base in node.bases:
            b = ast.unparse(base)
            if b != "Base" and mixin_cols.get(b, 0) > 0:
                cols += mixin_cols[b]
                mixins.append(b)
        if table:
            out.append({"table": table, "cols": cols,
                        "note": TABLE_NOTES.get(table,
                                                f"列继承自 {'/'.join(mixins)}" if mixins else "")})
    return out


ENTRYPOINT_SUFFIXES = (".cli.main", ".web.app", ".functional.harness",
                       ".tools.perf_dataset", ".service.agent_client",
                       ".tools.perf_wrapper")   # evalscope perf 协停 wrapper（子进程脚本拉起）


def necessity_audit(mods: dict, edges: set, reg: set) -> dict:
    """奥卡姆审计：零内部引用模块（区分包声明/入口/待复核）、大文件观察名单。"""
    fan_in: set[str] = set()
    for _s, d in edges | reg:
        fan_in.add(d)
    pkgs: list[dict] = []
    entries: list[dict] = []
    orphans: list[dict] = []
    for name, m in mods.items():
        if name in fan_in:
            continue
        rel = rel_of(m["path"])
        row = {"file": rel, "loc": len(m["path"].read_text(encoding="utf-8").splitlines())}
        if m["is_pkg"]:
            pkgs.append(row)
        elif name.endswith(ENTRYPOINT_SUFFIXES):
            entries.append(row)
        else:
            orphans.append(row)
    big = sorted(({"file": rel_of(m["path"]),
                   "loc": len(m["path"].read_text(encoding="utf-8").splitlines())}
                  for m in mods.values()), key=lambda r: -r["loc"])[:8]
    return {"pkgs": pkgs, "orphans": orphans, "entries": entries, "big": big}


def render_data_structures(types: list[dict], tables: list[dict]) -> str:
    _check(len(types) >= 10, f"types.py 类数异常: {len(types)}")
    _check(len(tables) >= 15, f"models.py 表数异常: {len(tables)}")
    tt = "".join(f'<tr><td><b>{esc(t["name"])}</b><br><span class="sub">{esc(t["base"])}</span></td>'
                 f'<td>{"、".join(esc(f) for f in t["fields"])}</td></tr>' for t in types)
    tr = "".join(f'<tr><td><code>{esc(t["table"])}</code></td><td>{t["cols"]}</td>'
                 f'<td>{esc(t["note"])}</td></tr>' for t in tables)
    return ('<p class="sub">契约数据结构（<a href="'
            + (ROOT / "aite/core/types.py").resolve().as_uri()
            + '"><code>aite/core/types.py</code></a>）是全平台的通用语言：suite 经 loader 变成 '
            'TestCase 流，连接器产出 TargetResponse，指标产出 MetricResult→聚合为 '
            'MetricSummary，一次执行整体是 RunReport；改动它们 = 契约变更（L4）。'
            '存储侧（<a href="' + (ROOT / "aite/storage/models.py").resolve().as_uri()
            + '"><code>aite/storage/models.py</code></a>）Schema 只增不改。'
            '以下清单 live 提取，悬停/点击源码。</p>'
            f'<table><tr><th>契约类（{len(types)}）</th><th>字段</th></tr>{tt}</table><br>'
            f'<table><tr><th>表（{len(tables)}）</th><th>列数</th><th>说明（核心表）</th></tr>{tr}</table>')


def render_necessity(audit: dict) -> str:
    _check(not audit["orphans"], f"发现零内部引用且非入口的模块（须人工裁决后写入白名单）: "
                                 f"{[o['file'] for o in audit['orphans']]}")
    rows_p = "".join(f'<span class="chip"><a href="{(ROOT / r["file"]).resolve().as_uri()}">'
                     f'{esc(r["file"])}</a></span>' for r in audit["pkgs"])
    rows_e = "".join(f'<tr><td>{file_link(r["file"])}</td><td>{r["loc"]}</td>'
                     f'<td>入口（CLI/Web/插件名加载/子进程脚本），零内部引用是正常的</td></tr>'
                     for r in audit["entries"])
    entry_html = (f'<table><tr><th>显式入口模块（{len(audit["entries"])}）</th><th>行数</th>'
                  f'<th>裁决</th></tr>{rows_e}</table>' if audit["entries"]
                  else '<div class="sub">显式入口：无孤儿——所有入口（cli.main / web.app / '
                       'functional.harness / perf_dataset / agent_client）都被正常引用。</div>')
    rows_b = "".join(f'<span class="chip {"hot0" if r["loc"] >= 800 else "hot1" if r["loc"] >= 500 else ""}">'
                     f'<a href="{(ROOT / r["file"]).resolve().as_uri()}">{esc(r["file"])} {r["loc"]} 行</a></span>'
                     for r in audit["big"])
    return ('<p class="sub">奥卡姆剃刀的机器化：扫描全部模块的 import 图，找出没有任何'
            '其他模块引用的「孤儿」，按调用约定分类裁决；出现真正的死代码 → 生成直接'
            '失败（防止死代码静默堆积）。判为正常的类别：包 __init__（由 Python 导入'
            '机制加载）与显式入口（CLI/Web/插件名加载/子进程脚本）。</p>'
            f'<div class="sub">包声明（{len(audit["pkgs"])} 个，正常）：{rows_p}</div>'
            + entry_html +
            f'<h3>大文件观察名单（重构候选，改前先看依赖图爆炸半径）</h3>'
            f'<div>{rows_b}</div>'
            '<p class="sub">冻结态说明：aite/metrics/ 为存量自研指标（定位红线，禁止新增，'
            '按 roadmap「存量迁移」逐步替换）；mockups/A2、A3 主题稿被 V1-PLAN 引用保留。</p>')


# ------------------------------------------------------------------ HTML 渲染

CSS = """
body{font-family:"Segoe UI","Microsoft YaHei",system-ui,sans-serif;margin:0;background:#f4f6f8;color:#1c2733}
.wrap{max-width:1240px;margin:0 auto;padding:24px}
h1{font-size:26px;margin:0 0 4px} h2{font-size:20px;margin:38px 0 12px;border-bottom:2px solid #dbe3ea;padding-bottom:6px}
.sub{color:#5a6b7d;font-size:13px}
.badge{display:inline-block;border-radius:10px;padding:1px 9px;font-size:12px;margin-right:6px}
.ok{background:#e2f5ea;color:#14713d}.warn{background:#fdf0dc;color:#8a5a00}
.card{background:#fff;border:1px solid #e3e9ef;border-radius:10px;padding:16px 18px;margin:12px 0}
table{border-collapse:collapse;width:100%;font-size:13px;background:#fff}
th{background:#eef2f6;text-align:left;padding:7px 10px;border:1px solid #e3e9ef}
td{padding:6px 10px;border:1px solid #e3e9ef;vertical-align:top}
code{background:#eef2f6;border-radius:4px;padding:1px 5px;font-size:12.5px}
a{color:#0b5cad;text-decoration:none} a:hover{text-decoration:underline}
.lane{position:relative;border-left:4px solid #9db2c6;background:#fff;border-radius:6px;margin:8px 0;padding:10px 14px}
.lane .lname{font-weight:600;font-size:14px}
.lane .ldesc{font-size:12.5px;color:#5a6b7d;margin:2px 0 6px}
.chip{display:inline-block;border-radius:5px;padding:1px 7px;margin:2px 3px 2px 0;font-size:12px;font-family:Consolas,monospace;background:#f0f4f8;border:1px solid #d5dee7}
.chip a{color:#1c2733}
.hot0{background:#ffe9e9;border-color:#f3c2c2}.hot1{background:#ffe3d1;border-color:#f0b48a}
.hot2{background:#fdf3cf;border-color:#ecd28a}.contract{border-color:#7c5cd6;background:#efe9fb}
details{margin:8px 0} summary{cursor:pointer;font-size:13.5px;padding:4px 0}
.tip{background:#eef6ff;border:1px solid #cfe3f8;border-radius:8px;padding:10px 14px;font-size:13.5px;margin:10px 0}
"""


def esc(s: object) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def file_link(rel: str) -> str:
    p = (ROOT / rel).resolve()
    _check(p.exists(), f"file_link 目标不存在: {rel}")
    return f'<a href="{p.as_uri()}"><code>{esc(rel)}</code></a>'


def rel_of(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def is_contract(path: Path) -> bool:
    rel = rel_of(path)
    named = {c[0] for c in CONTRACT_FILES if not c[0].endswith("/")}
    return rel in named or rel.startswith("aite/web/routers/")


def heat_class(n: int) -> str:
    return "hot0" if n >= 10 else "hot1" if n >= 5 else "hot2" if n >= 2 else ""


def render_layer_bands(layers, mods, heat) -> str:
    """每层一条横带：意图 + 统计 + 文件 chip（按提交热度着色，可点击跳源码）。"""
    html = []
    depth = len(layers)
    for i, prefixes in enumerate(layers):
        names = [m for m, v in mods.items() if v["layer"] == i]
        if not names:
            continue
        loc = 0
        chips = []
        for m in sorted((mods[n] for n in names), key=lambda x: rel_of(x["path"])):
            loc += len(m["path"].read_text(encoding="utf-8").splitlines())
            rel = rel_of(m["path"])
            cls = heat_class(heat.get(rel, 0))
            if is_contract(m["path"]):
                cls += " contract"
            uri = m["path"].resolve().as_uri()
            chips.append(f'<span class="chip {cls}"><a href="{uri}">{esc(rel)}</a></span>')
        top = sorted(names, key=lambda n: -heat.get(rel_of(mods[n]["path"]), 0))[:2]
        tops = ", ".join(rel_of(mods[n]["path"]).split("/")[-1] for n in top) if top else "—"
        intent = next((v for k, v in LAYER_INTENT.items()
                       if any(k == p or p.startswith(k + ".") for p in prefixes)), "")
        label = " | ".join(p.replace("aite.", "") for p in prefixes)
        html.append(f'<div class="lane"><span class="lname">L{depth - i} {esc(label)}</span> '
                    f'<span class="sub">{len(names)} 文件 · {loc} 行 · 最热: {esc(tops)}</span>'
                    f'<div class="ldesc">{esc(intent)}</div><div>{"".join(chips)}</div></div>')
    return "\n".join(html)


def _node_key(module: str) -> str:
    parts = module.split(".")[1:]
    if parts[:2] == ["web", "routers"] and len(parts) > 2:
        return "routers." + parts[2]
    return ".".join(parts[:2])


def build_graph(mods, edges, reg, heat):
    """聚合到模块级节点；返回节点表与带权依赖边。"""
    nodes: dict[str, dict] = {}
    for name, m in mods.items():
        k = _node_key(name)
        n = nodes.setdefault(k, {"loc": 0, "files": 0, "layer": m["layer"], "heat": 0,
                                 "contract": False})
        rel = rel_of(m["path"])
        n["loc"] += len(m["path"].read_text(encoding="utf-8").splitlines())
        n["files"] += 1
        n["heat"] = max(n["heat"], heat.get(rel, 0))
        n["contract"] = n["contract"] or is_contract(m["path"])
    wt: dict[tuple[str, str], dict] = {}
    for s, d in edges:
        a, b = _node_key(s), _node_key(d)
        if a == b:
            continue
        e = wt.setdefault((a, b), {"w": 0, "reg": True})
        e["w"] += 1
        if (s, d) not in reg:
            e["reg"] = False
    return nodes, wt


HUB_NODES = {"core.types", "core.interfaces", "core.registry", "utils.text",
             "utils.logging", "config"}


def render_graph_svg(nodes, wt, layers) -> str:
    W, band_x, band_w, gap = 1180, 16, 1148, 56
    y = 10
    pos: dict[str, tuple[float, float, float]] = {}
    bands: list[str] = []
    for i, prefixes in enumerate(layers):
        members = sorted((k for k, v in nodes.items() if v["layer"] == i), key=str.lower)
        if not members:
            continue
        label = esc(" | ".join(p.replace("aite.", "") for p in prefixes))
        x, row_y = band_x + 10, y + 24
        for k in members:
            w = min(180, 22 + 6.4 * len(k))
            if x + w > band_x + band_w - 10:
                x, row_y = band_x + 10, row_y + 30
            pos[k] = (x, row_y, w)
            x += w + 8
        h = (row_y - y) + 34
        bands.append(f'<rect x="{band_x}" y="{y}" width="{band_w}" height="{h}" rx="8" '
                     f'fill="#fbfcfe" stroke="#dde5ec"/>'
                     f'<text x="{band_x + 10}" y="{y + 16}" font-size="11" fill="#5a6b7d">'
                     f'{label}</text>')
        y += h + gap
    height = int(y)
    paths = []
    for (a, b), e in sorted(wt.items(), key=lambda kv: -kv[1]["w"]):
        if a not in pos or b not in pos or nodes[a]["layer"] is None \
                or nodes[b]["layer"] is None or nodes[a]["layer"] == nodes[b]["layer"]:
            continue
        x1, y1, w1 = pos[a]
        x2, y2, w2 = pos[b]
        sy, ty = y1 + 22, y2
        if ty < sy:
            sy, ty = y1, y2 + 22
        mx = (x1 + w1 / 2 + x2 + w2 / 2) / 2
        width = min(3, 0.5 + e["w"] * 0.4)
        style = f'stroke="#7d9ab5" stroke-width="{width}" opacity="0.5"'
        cls = "edge"
        if e["reg"]:
            cls = "edge reg"
            style = 'stroke="#2f9e63" stroke-width="1.2" stroke-dasharray="4 3" opacity="0.6"'
        if b in HUB_NODES:
            cls += " hub"
        paths.append(f'<path class="{cls}" data-w="{e["w"]}" '
                     f'd="M{x1 + w1 / 2:.0f} {sy} C{mx:.0f} {sy + (ty - sy) // 2}, '
                     f'{mx:.0f} {sy + (ty - sy) // 2}, {x2 + w2 / 2:.0f} {ty}" '
                     f'fill="none" {style}/>')
    chips = []
    for k, (x, yy, w) in pos.items():
        v = nodes[k]
        cls = heat_class(v["heat"])
        if v["contract"]:
            cls += " contract"
        title = f"{k}: {v['files']} 文件 {v['loc']} 行, 提交热度 {v['heat']}"
        src = " hubsrc" if k in HUB_NODES else ""
        chips.append(f'<g class="node{src}"><title>{esc(title)}</title>'
                     f'<rect x="{x:.0f}" y="{yy:.0f}" width="{w:.0f}" height="22" rx="5" '
                     f'class="nrect {cls}"/>'
                     f'<text x="{x + 7:.0f}" y="{yy + 15:.0f}" font-size="10.5" '
                     f'font-family="Consolas,monospace" fill="#1c2733">{esc(k)}</text></g>')
    legend = ('<div class="legend sub">边：<span style="color:#7d9ab5">━ 依赖</span>　'
              '<span style="color:#2f9e63">╌ import 即注册（虚线）</span>｜节点颜色：'
              '<span class="chip hot0">≥10 次提交</span><span class="chip hot1">≥5</span>'
              '<span class="chip hot2">≥2</span><span class="chip contract">契约文件</span>'
              '（节点悬停可看行数/热度）</div>')
    js = ('<script>function applyFilters(){var hub=document.getElementById("fHub").checked,'
          'minW=+document.getElementById("fW").value;'
          'document.querySelectorAll("path.edge").forEach(function(p){'
          'var w=+p.dataset.w,isHub=p.classList.contains("hub");'
          'p.style.display=((isHub&&hub)||w<minW)?"none":"";});}</script>')
    ctrl = (f'{legend}<div class="sub">显示控制：<label><input type="checkbox" id="fHub" checked '
            f'onchange="applyFilters()"> 隐藏基础枢纽（types/interfaces/registry/text/logging/config）'
            f'扇入边</label>　<label>最小边权重 <input type="number" id="fW" value="1" min="1" '
            f'max="9" style="width:44px" onchange="applyFilters()"></label></div>')
    svg_style = ('<style>.nrect{fill:#f0f4f8;stroke:#d5dee7}.nrect.hot0{fill:#ffe9e9;stroke:#f3c2c2}'
                 '.nrect.hot1{fill:#ffe3d1;stroke:#f0b48a}.nrect.hot2{fill:#fdf3cf;stroke:#ecd28a}'
                 '.nrect.contract{fill:#efe9fb;stroke:#7c5cd6}</style>')
    return (f'<div class="card">{ctrl}<svg viewBox="0 0 {W} {height}" width="100%" '
            f'style="background:#fff;border:1px solid #e3e9ef;border-radius:8px">'
            f'{svg_style}{"".join(bands)}{"".join(paths)}{"".join(chips)}</svg>{js}</div>')


def est_line(path: Path, fn: str) -> int:
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if f"def {fn}(" in line:
            return i
    return 1


def render_rest(rows: list[dict]) -> str:
    by_file: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_file[r["file"]].append(r)
    head = (f'<p class="sub">共 {len(rows)} 个端点，全局前缀 <code>/api/v1</code>；'
            f'前端（SPA 与 mockup）只允许依赖这张表。处理函数可点击跳转源码。</p>')
    parts = [head]
    for f in sorted(by_file):
        fp = (ROOT / "aite" / "web" / "routers" / f).resolve()
        tr = ""
        for r in by_file[f]:
            method = esc(r["method"])
            path = esc(r["path"])
            fn = esc(r["fn"])
            line = est_line(fp, r["fn"])
            tr += (f'<tr><td><span class="badge ok">{method}</span></td>'
                   f'<td><code>{path}</code></td>'
                   f'<td><a href="{fp.as_uri()}#L{line}">{fn}</a></td></tr>')
        parts.append(f'<details open><summary><b>{esc(f)}</b> — {len(by_file[f])} 个端点</summary>'
                     f'<table><tr><th>方法</th><th>路径</th><th>处理函数</th></tr>{tr}</table></details>')
    return "\n".join(parts)


def render_adapters() -> str:
    head = ('<p class="sub">suite YAML 里的 <code>engine:</code> 字段 = 路由开关。五种引擎共用同一契约：'
            '<code>build_config → run → collect</code>（+<code>list_tasks</code>），注册表分发，'
            '互不认识彼此；结果统一落 <code>run_metric_summary</code>，所以四个域能在趋势页并列对比。</p>')
    tr = ""
    for a in ADAPTERS:
        p = ROOT / a["file"]
        _check(p.exists(), f"适配器文件不存在: {a['file']}")
        src = p.read_text(encoding="utf-8")
        registered = f'@register_tool("{a["engine"]}")' in src
        _check(registered, f'{a["file"]} 未以 {a["engine"]} 注册（内置知识与代码漂移）')
        tr += (f'<tr><td><b><code>{a["engine"]}</code></b></td>'
               f'<td><a href="{p.resolve().as_uri()}">{a["file"]}</a></td>'
               f'<td>{esc(a["runs"])}</td><td>{esc(a["wraps"])}</td>'
               f'<td style="white-space:nowrap">{esc(a["dir"])}</td>'
               f'<td>{esc(a["notes"])}</td></tr>')
    recipe = "".join(f'<tr><td><code>{esc(step)}</code></td><td>{esc(what)}</td></tr>'
                     for step, what in RECIPE)
    return (head + '<table><tr><th>engine 路由名</th><th>适配器文件</th><th>怎么执行</th>'
            '<th>对接的开源引擎</th><th>阈值方向</th><th>关键行为</th></tr>' + tr + '</table>'
            + '<h3>加一个新引擎的标准动作（照抄即可，无需理解全局）</h3>'
            + '<table><tr><th>位置</th><th>做什么</th></tr>' + recipe + '</table>')


def render_docs() -> str:
    tr = ""
    for rel, what, verdict in DOCS:
        p = ROOT / rel
        _check(p.exists() or rel == "docs/architecture-map.html",
               f"DOCS 清单中的文件不存在: {rel}")
        tr += (f'<tr><td><a href="{p.resolve().as_uri()}"><code>{esc(rel)}</code></a></td>'
               f'<td>{esc(what)}</td><td>{esc(verdict)}</td></tr>')
    return ('<p class="sub">裁决原则：规则性内容只允许一个权威出处；叙事随代码的交给生成器；'
            '已完成的设计稿收割进 roadmap 后归档；会话便签收割后删除。</p>'
            '<table><tr><th>文档</th><th>干什么用</th><th>裁决</th></tr>' + tr + '</table>')


def render_glossary() -> str:
    tr = "".join(f'<tr><td><b>{esc(t)}</b></td><td>{esc(d)}</td></tr>' for t, d in GLOSSARY)
    return '<table><tr><th>术语</th><th>含义与落点</th></tr>' + tr + '</table>'


def git_health() -> dict:
    def run(args: list[str]) -> str:
        return subprocess.run(["git", *args], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", cwd=ROOT).stdout.strip()

    files = run(["ls-files", "--others", "--exclude-standard"]).splitlines()
    untracked = [f for f in files if f.startswith(("aite/", "tests/", "docs/", "scripts/"))]
    changed = run(["diff", "--name-only"]).splitlines()
    return {"head": run(["log", "-1", "--format=%h %cs %s"])[:80],
            "modified": len([f for f in changed if f.strip()]),
            "diff": run(["diff", "--shortstat"]) or "无",
            "untracked_code": len(untracked)}


def render_health(h: dict, gates: dict, pytest_badge: str) -> str:
    def badge(name: str, g: dict) -> str:
        cls = "ok" if g["ok"] else "warn"
        detail = g["detail"] or ("通过" if g["ok"] else "未通过")
        return f'<span class="badge {cls}">{name}: {esc(detail)}</span>'

    cruft = "".join(f'<tr><td><code>{esc(a)}</code></td><td>{esc(b)}</td><td>{esc(c)}</td></tr>'
                    for a, b, c in CRUFT)
    return (f'<div class="card"><p>{badge("ruff", gates["ruff"])}'
            f'{badge("lint-imports", gates["lint"])}{pytest_badge}</p>'
            f'<p class="sub">最近提交：<code>{esc(h["head"])}</code>｜已跟踪改动 {h["modified"]} 个文件'
            f'（{esc(h["diff"])}）｜未提交的新代码文件 {h["untracked_code"]} 个。'
            f'未提交 = 未受 git 保护，任何误操作都可能丢工作。</p>'
            f'<h3>仓库卫生（根目录与临时物）</h3>'
            f'<table><tr><th>对象</th><th>是什么</th><th>处置建议</th></tr>{cruft}</table></div>')


def render_conventions() -> str:
    rows = "".join(f'<tr><td><b>{esc(a)}</b></td><td>{esc(b)}</td></tr>' for a, b in CONVENTIONS)
    ladder = "".join(f'<tr><td><b>{esc(a)}</b></td><td>{esc(b)}</td></tr>' for a, b in ESCALATION)
    return (f'<table><tr><th>规则</th><th>动作</th></tr>{rows}</table>'
            f'<h3>改动升级阶梯（什么改动走什么流程）</h3>'
            f'<table><tr><th>级别</th><th>定义与流程</th></tr>{ladder}</table>')


def _verify(rest_rows, cli_groups, mods, reg, nodes) -> None:
    """知识漂移校验：内置描述与代码不符即拒绝生成（原则 9）。"""
    _check(len(mods) > 100, f"aite 模块数异常: {len(mods)}")
    for cf, _what in CONTRACT_FILES:
        if not cf.endswith("/"):
            _check((ROOT / cf).exists(), f"契约文件不存在: {cf}")
    _check(len(rest_rows) >= 55, f"REST 端点数异常: {len(rest_rows)}")
    _check(len(cli_groups) >= 10, f"CLI 命令组数异常: {len(cli_groups)}")
    _check(len(reg) >= 20, f"import 即注册边异常: {len(reg)}")
    _check(len(nodes) >= 40, f"模块级节点数异常: {len(nodes)}")
    engines = {a["engine"] for a in ADAPTERS}
    _check(len(engines) == 5, "适配器清单应以 5 个引擎为准")


def build_html(bands: str, graph_svg: str, rest_rows: list[dict], cli_groups: list[dict],
               cli_tops: list[str], health: dict, gates: dict, pytest_badge: str,
               types: list[dict], tables: list[dict], audit: dict) -> str:
    gen_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    toc_names = ["这张图怎么用", "全景分层", "模块依赖图", "对外接口：REST", "对外接口：CLI",
                 "适配器与引擎", "核心数据结构", "术语表", "必要性审计（奥卡姆）",
                 "文档裁决", "仓库健康", "规则与升级阶梯"]
    toc = "".join(f'<a href="#{i}">{esc(name)}</a>' for i, name in enumerate(toc_names))
    group_html = " · ".join(f'<code>{esc(g["name"])}</code>' for g in cli_groups)
    top_html = " · ".join(f'<code>{esc(c)}</code>' for c in cli_tops)
    return f"""<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<title>aite 架构地图</title><style>{CSS}</style></head><body><div class="wrap">
<h1>aite 架构地图 <span class="sub">— 一张图掌控全项目</span></h1>
<p class="sub">生成于 {gen_at} · 数据源：代码 AST + git 历史 + pyproject 合同
（<code>python scripts/arch_map.py</code> 再生，勿手改）</p>
<p class="toc sub">{toc}</p>

<h2 id="0">这张图怎么用</h2>
<div class="tip"><b>平台定位一句话：</b>aite 是评测<b>平台</b>不是评测<b>引擎</b>——不造任何评估指标；
评测能力来自开源引擎（evalscope / pytest / locust / ragas），aite 做三件事：
统一入口（suite 的 <code>engine:</code> 路由）、统一结果格式、数据集与任务治理。
所以：<b>想接新评测能力 → 加适配器；想改口径 → 查术语表；想知道能不能改 → 看升级阶梯。</b></div>
<div class="tip">地图分两类内容：<b>实时事实</b>（行数/依赖/接口清单——脚本每次从代码重新提取，永不撒谎）
和<b>内置知识</b>（意图/写法/裁决——逐条与代码校验，代码漂移即拒绝生成）。搜索请用浏览器 Ctrl+F。</div>

<h2 id="1">全景分层（自上而下：越靠上离用户越近，箭头只准向下）</h2>
<p class="sub">分层顺序即 <code>pyproject.toml</code> 的 import-linter 合同，<code>lint-imports</code>
每次提交强制校验。「最热」= git 提交次数最高的文件，也是改动最频繁、最值得关注的地方。
紫色 chip = 契约文件（改动需走 L4 流程）。</p>
{bands}

<h2 id="2">模块依赖图（谁依赖谁，一眼看清牵一发动哪里）</h2>
{graph_svg}

<h2 id="3">对外接口：REST（/api/v1 全量清单）</h2>
{render_rest(rest_rows)}

<h2 id="4">对外接口：CLI（aite 命令全集）</h2>
<p class="sub">入口 <code>aite</code>（pyproject [project.scripts] → aite.cli.main:main）。
原则：接口新增一律 CLI + Python API 双形态。</p>
<div class="card"><b>顶层命令：</b>{top_html}<br><b>命令组：</b>{group_html}<br>
<span class="sub">另有 functional/accuracy/perf/reliability 四个域命令组
（_mount_domain_tasks 动态挂载），各含 tasks 子组（list/get/create/rm/run/stop/retry）。</span></div>

<h2 id="5">适配器与引擎（平台核心资产）</h2>
{render_adapters()}

<h2 id="6">核心数据结构（平台通用语言）</h2>
{render_data_structures(types, tables)}

<h2 id="7">术语表（平台唯一口径）</h2>
{render_glossary()}

<h2 id="8">必要性审计（奥卡姆：如无必要，勿增实体）</h2>
{render_necessity(audit)}

<h2 id="9">文档裁决（每份文档是干什么的，留/并/归档）</h2>
{render_docs()}

<h2 id="10">仓库健康（生成时实测）</h2>
{render_health(health, gates, pytest_badge)}

<h2 id="11">规则与升级阶梯（改动前先对号入座）</h2>
{render_conventions()}

<p class="sub">本图由 scripts/arch_map.py 生成；权威依据：CLAUDE.md（宪法）· docs/architecture.md（分层）
· docs/roadmap.md（决策账本）。图与代码冲突时以代码为准，并回报修正地图知识。</p>
</div></body></html>"""


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="生成 docs/architecture-map.html")
    ap.add_argument("--pytest", action="store_true", help="额外跑全量测试（约 1 分钟）")
    args = ap.parse_args()

    layers = load_layers()
    mods = scan_modules(layers)
    edges, reg = parse_imports(mods)
    heat = commit_heat()
    nodes, wt = build_graph(mods, edges, reg, heat)

    rest_rows = extract_rest()
    cli_groups, cli_tops = extract_cli()
    types = extract_types()
    tables = extract_tables()
    audit = necessity_audit(mods, edges, reg)
    gates = {"ruff": run_gate(["-m", "ruff", "check", "aite", "tests"]),
             "lint": run_gate(["-c", "import sys; from importlinter.cli import lint_imports; "
                                    "sys.exit(lint_imports())"])}
    pytest_badge = ""
    if args.pytest:
        r = run_gate(["-m", "pytest", "tests/", "-W", "ignore", "-p", "no:cacheprovider"],
                     timeout=600, detail_re=r"\d+ passed")
        cls = "ok" if r["ok"] else "warn"
        pytest_badge = f'<span class="badge {cls}">pytest: {esc(r["detail"])}</span>'
    health = git_health()

    _verify(rest_rows, cli_groups, mods, reg, nodes)
    html = build_html(bands=render_layer_bands(layers, mods, heat),
                      graph_svg=render_graph_svg(nodes, wt, layers),
                      rest_rows=rest_rows, cli_groups=cli_groups, cli_tops=cli_tops,
                      health=health, gates=gates, pytest_badge=pytest_badge,
                      types=types, tables=tables, audit=audit)
    OUT.write_text(html, encoding="utf-8", newline="\n")
    print(f"[arch_map] OK → {OUT}")
    print(f"  模块 {len(mods)} / 文件依赖边 {len(edges)}（import 即注册 {len(reg)}）/ "
          f"REST 端点 {len(rest_rows)} / CLI 组 {len(cli_groups)}")
    print(f"  ruff: {gates['ruff']['ok']}  lint-imports: {gates['lint']['ok']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
