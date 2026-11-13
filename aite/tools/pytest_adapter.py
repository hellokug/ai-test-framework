"""pytest 适配器（模块 D：功能测试执行引擎）。

suite yaml 写 ``engine: pytest`` 即路由到本适配器；一个场景 = 一个子目录
（数据驱动 ``cases.jsonl`` 或手写 ``test_*.py``），逐场景 subprocess
跑 ``python -m pytest``，从 junit-xml 收集 ``pass_rate``，复用平台的
入库 / 阈值 / 趋势链路。场景全部用户自管：``tools.tests_dir`` 指定，
缺省 = 平台托管场景根（``config.DEFAULT_FUNCTIONAL_SCENARIOS_DIR``，
与资产层同源）。

- 模型访问经 ``-p aite.functional.harness`` 插件按导入路径加载（安装/源码环境
  行为一致，规避 conftest confcutdir 陷阱）
- 退出码 0/1 = 正常（有失败用例）；2/3/4/5 = 执行异常，抛错走 run 级 error
- ``tools.datasets``：可空 = 跑全部子目录，非空时作为子目录名筛选
- ``tools.cases``：用例 id 数组（省略 = 全部），单用例问题复现。精确定向
  ``cases.jsonl::<id>`` nodeid（非 -k 子串匹配，打错 id 显式报错）；id 在任一
  选中场景存在即合法，无匹配 id 的场景整场景跳过（日志可见）；仅支持
  cases.jsonl 数据驱动场景。repeat 用例：指定原 id 选中其全部轮次
  （id / id[r2]…），``id[r3]`` 精确复现单轮
- ``limit_per_subset`` 对 pytest 无语义，忽略
- 阈值：显式 ``metric_thresholds: {pass_rate: x}`` 优先；``auto`` → 1.0
  （功能测试零容忍口径）；不配则无判定
"""

from __future__ import annotations

import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from aite import config
from aite.tools.base import (
    ToolAdapter,
    ToolMetricRow,
    ToolRunRequest,
    ToolRunResult,
    api_endpoint,
    register_tool,
    run_sync_in_thread,
)
from aite.tools.subproc import run_tool_subprocess
from aite.utils.logging import get_logger

logger = get_logger(__name__)

DEFAULT_TIMEOUT = 1800   # 单场景总超时（秒），req.extra.timeout 可覆盖
HARNESS_PLUGIN = "aite.functional.harness"
PASS_RATE = "pass_rate"


def _available_scenarios(root: Path) -> list[str]:
    """枚举 root 下的直接子目录名（= 可用场景）。

    场景形态两种：数据驱动（含 cases.jsonl）或手写用例（含 test_*.py）。
    """
    if not root.is_dir():
        raise ValueError(f"功能测试目录不存在: {root}")
    names = sorted(d.name for d in root.iterdir()
                   if d.is_dir() and (any(d.glob("test_*.py"))
                                      or (d / "cases.jsonl").is_file()))
    if not names:
        raise ValueError(f"{root} 下没有含 cases.jsonl 或 test_*.py 的场景子目录")
    return names


@register_tool("pytest")
class PytestAdapter(ToolAdapter):
    name = "pytest"

    def build_config(self, req: ToolRunRequest) -> dict:
        """解析场景目录 → 执行计划（纯函数：argv / env / 超时映射，可单测）。"""
        root = Path(req.tests_dir) if req.tests_dir \
            else Path(config.DEFAULT_FUNCTIONAL_SCENARIOS_DIR)
        available = _available_scenarios(root)
        if req.datasets:
            unknown = [s for s in req.datasets if s not in available]
            if unknown:
                raise ValueError(f"未知功能场景: {unknown}；可用: {available}")
            names = list(req.datasets)
        else:
            names = available

        model = str(req.target.get("model") or "").strip()
        if not model:
            raise ValueError("pytest engine 需要 target.model")
        url, api_key = api_endpoint(req.target)
        env = {
            "AITE_FT_BASE_URL": url,
            "AITE_FT_MODEL": model,
            "AITE_FT_API_KEY": api_key,
            "PYTHONIOENCODING": "utf-8",   # 子进程 stdio 统一 utf-8，日志不乱码
        }
        timeout = int(req.extra.get("timeout", DEFAULT_TIMEOUT))

        case_filter = list(req.cases) if req.cases else None
        ids_by_scenario: dict[str, list[str]] | None = None
        if case_filter:
            from aite.functional.harness import load_cases  # 懒加载：tools 包不硬依赖 pytest
            ids_by_scenario = {}
            for n in names:
                jsonl = root / n / "cases.jsonl"
                if not jsonl.is_file():
                    raise ValueError(
                        f"用例级过滤（tools.cases）仅支持 cases.jsonl 数据驱动场景: {root / n}")
                ids_by_scenario[n] = [c.id for c in load_cases(jsonl)]
            all_ids = {i for ids in ids_by_scenario.values() for i in ids}

            def _hit(case_id: str, run_id: str) -> bool:
                # 原 id 命中其全部 repeat 轮次（run_id / run_id[rN]），id[rN] 精确单轮
                return run_id == case_id or run_id.startswith(f"{case_id}[r")

            unknown = [c for c in case_filter if not any(_hit(c, i) for i in all_ids)]
            if unknown:
                raise ValueError(f"未知用例 id: {unknown}；可用: {sorted(all_ids)}")

        scenarios: list[dict] = []
        skipped: list[str] = []
        for n in names:
            targets = [str(root / n)]
            if case_filter:
                wanted = [i for i in ids_by_scenario[n]
                          if any(_hit(c, i) for c in case_filter)]
                if not wanted:   # 该场景没有要复现的用例 → 整场景跳过
                    skipped.append(n)
                    continue
                targets = [f"{root / n / 'cases.jsonl'}::{i}" for i in wanted]
            scenarios.append({
                "name": n,
                "targets": targets,
                "junit_xml": str(req.work_dir / "junit" / f"{n}.xml"),
                "log": str(req.work_dir / "logs" / f"{n}.log"),
                # 逐用例原始记录落盘路径（输入/输出/断言）→ 平台用例级数据源
                "results": str(req.work_dir / "results" / f"{n}.jsonl"),
            })
        argv = {s["name"]: [
            sys.executable, "-m", "pytest", *s["targets"],
            f"--junitxml={s['junit_xml']}",
            "-p", HARNESS_PLUGIN,
            "-p", "no:cacheprovider",     # 不在 rootdir 落 .pytest_cache
            # 主套件把 fixtures 列入 norecursedirs（防误收集），显式传目录仍会被拦，
            # 统一清空兜底（pytest 行为：显式目录参数同样受 norecursedirs 过滤）
            "-o", "norecursedirs=",
            # 清空继承的根 addopts（如主套件 -n auto）——内层场景必须串行，
            # 否则嵌套 xdist worker 放大开销
            "-o", "addopts=",
        ] for s in scenarios}
        return {"scenarios": scenarios, "argv": argv, "env": env,
                "timeout": timeout, "skipped": skipped}

    async def run(self, req: ToolRunRequest) -> ToolRunResult:
        cfg = self.build_config(req)
        if cfg["skipped"]:
            logger.info("用例过滤跳过场景（无匹配用例）: %s", cfg["skipped"])
        for sc in cfg["scenarios"]:
            await run_sync_in_thread(self._run_one, sc, cfg)
        return self.collect(req)

    def _run_one(self, sc: dict, cfg: dict) -> None:
        """单场景 subprocess 执行；stdout/stderr 落日志，异常退出码抛错。"""
        for key in ("junit_xml", "log", "results"):
            Path(sc[key]).parent.mkdir(parents=True, exist_ok=True)
        env_extra = {**cfg["env"], "AITE_FT_RESULTS": sc["results"]}
        rc, out, err = run_tool_subprocess(
            cfg["argv"][sc["name"]], log_path=Path(sc["log"]), timeout=cfg["timeout"],
            env_extra=env_extra)
        if rc is None:
            raise RuntimeError(f"功能场景 {sc['name']} 超时（{cfg['timeout']}s）")
        if rc not in (0, 1):   # 0=全过 1=有失败；2+ = 执行异常
            raise RuntimeError(
                f"功能场景 {sc['name']} 执行异常（exit={rc}），"
                f"日志: {sc['log']}\\n{out[-2000:]}")

    def collect(self, req: ToolRunRequest) -> ToolRunResult:
        """解析各场景 junit-xml → 场景级 pass_rate 行。"""
        cfg = self.build_config(req)   # 纯函数，重复调用无副作用
        out = ToolRunResult()
        for sc in cfg["scenarios"]:
            xml_path = Path(sc["junit_xml"])
            if not xml_path.is_file():
                raise RuntimeError(f"缺少 junit 报告: {xml_path}")
            tests = failures = errors = skipped = 0
            for suite in ET.parse(xml_path).getroot().iter("testsuite"):
                tests += int(suite.get("tests", 0))
                failures += int(suite.get("failures", 0))
                errors += int(suite.get("errors", 0))
                skipped += int(suite.get("skipped", 0))
            denom = tests - skipped   # skipped 剔除分母；count 同口径，overall 加权才准确
            if denom <= 0:
                raise RuntimeError(f"功能场景 {sc['name']} 未收集到用例（tests={tests}）")
            rate = (tests - failures - errors - skipped) / denom
            thr = req.metric_thresholds.get(PASS_RATE)
            if thr is None and req.auto_thresholds:
                thr = 1.0   # 功能测试零容忍口径
            out.rows.append(ToolMetricRow(
                dataset=f"pytest:{sc['name']}", metric=PASS_RATE,
                value=rate, count=denom, threshold=thr,
                passed=(rate >= thr) if thr is not None else None,
            ))
            out.raw_score[sc["name"]] = rate
            for tc in ET.parse(xml_path).getroot().iter("testcase"):
                node = tc.find("failure")
                if node is None:
                    node = tc.find("error")
                if node is None:
                    continue
                detail = (node.text or node.get("message") or "").strip()
                out.failures.append({
                    "dataset": f"pytest:{sc['name']}",
                    "case": tc.get("name") or "?",
                    "detail": (detail[:600] or "（无详情）"),
                })
            out.report_files[f"{sc['name']}.xml"] = str(xml_path)
            if Path(sc["log"]).is_file():
                out.report_files[f"{sc['name']}.log"] = sc["log"]
            results_path = Path(sc.get("results") or "")
            if results_path.is_file():
                for line in results_path.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    rec = json.loads(line)
                    rec["scenario"] = sc["name"]
                    out.case_records.append(rec)
        return out

    def list_tasks(self) -> list[dict]:
        """枚举平台托管场景根（用户场景；根不存在/为空返回 []，列表不报错）。"""
        root = Path(config.DEFAULT_FUNCTIONAL_SCENARIOS_DIR)
        if not root.is_dir():
            return []
        return [{
            "name": n,
            "pretty_name": n,
            "metrics": [PASS_RATE],
            "tags": ["functional"],
            "few_shot_num": None,
        } for n in sorted(d.name for d in root.iterdir()
                          if d.is_dir() and (any(d.glob("test_*.py"))
                                             or (d / "cases.jsonl").is_file()))]
