"""perf / locust 两压测适配器的共享解析、常量与产出逻辑（DRY 收口，原则 3）。

load：dataset / prompt / synthetic 三选一；mode：baseline / ramp / soak（probe 为
evalscope-perf 特有，由 allow_probe 控制）；duration：整场总时长与 level_duration
每档秒数二选一（probe+duration → 看门狗语义仅 perf 使用）；梯度：ramp（并发）
与 rate（QPS 开环）统一 {from, to, step} 展开，sla 键随载体（ramp.sla /
rate.sla，{指标: 阈值}）驱动逐档熔断。
两适配器共用同一套错误文案、校验口径、阈值方向（LOWER_IS_BETTER）、指标行
发射（append_metric_rows）与时序落盘（write_timeseries）——任何一处出现第二
份副本即漂移温床（教训：_LOWER_IS_BETTER 双份定义曾致 evalscope 的
tpot_p95/itl_p95 阈值方向误判）。
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aite.tools.base import ToolMetricRow

DEFAULT_TIMEOUT = 3600                 # 整场压测总超时（秒），extra.timeout 可覆盖
DURATION_DRIVEN_NUMBER_CAP = 1_000_000  # 时长驱动安全上限（先到为准，防默认条数提前结束）
MODES = ("baseline", "ramp", "probe", "soak")
# 时延族指标（ttft/latency/tpot/itl）：均值档 + p50/p90/p95/p99/max 五档位。
# 两压测适配器同名产出（locust 逐请求样本现算，evalscope 读分位文件/逐请求 db）。
LATENCY_TIERS = ("p50", "p90", "p95", "p99", "max")
# 阈值方向单一来源：越低越好（<=）；qps/output_tps/avg_* 越高越好（>=）。
# 两压测适配器共用——此前双份定义曾漂移（perf 缺 *_p95_ms 致方向误判）。
LOWER_IS_BETTER = frozenset(
    {"ttft_ms", "latency_avg_ms", "itl_ms", "tpot_ms"}        # 均值档
    | {f"{base}_{tier}_ms" for base in ("ttft", "latency", "tpot", "itl")
       for tier in LATENCY_TIERS}
    | {"error_rate"})
# SLA 爬坡可判指标 = 档级指标行的键全集（_SUMMARY_METRICS + latency_avg + 分位
# 档 + max 档 + error_rate）；方向复用 LOWER_IS_BETTER，不另设第二份方向表。
LEVEL_SLA_METRICS = frozenset({
    "ttft_ms", "tpot_ms", "itl_ms", "latency_avg_ms", "error_rate",
    "qps", "output_tps", "avg_input_tokens", "avg_output_tokens",
    *{f"{base}_{tier}_ms" for base in ("ttft", "latency", "tpot", "itl")
      for tier in LATENCY_TIERS},
})
# 平台数据集输入键约定（与 aite/datasets/local 一致，自包含避免深 import）
INPUT_KEYS = ("input", "question", "instruction", "prompt", "query", "user")


def positive_ints(value) -> bool:
    return (isinstance(value, list) and bool(value)
            and all(isinstance(v, int) and not isinstance(v, bool) and v >= 1
                    for v in value))


# 梯度载体的合法键：展开三键 + sla（SLA 熔断判据随载体走，与 probe.sla 同构）
GRADIENT_KEYS = frozenset({"from", "to", "step", "sla"})


def expand_gradient(spec: dict, *, name: str, integer: bool) -> tuple[list, dict]:
    """{from, to, step[, sla]} → (梯度值列表, sla 载体剥离)。

    并发 ramp（integer=True，整数档）与 QPS 开环 rate（integer=False，浮点档）
    共用同一展开与校验；to 必含且必含在梯度里（爬坡终点即最大值）。
    用户不再手写档位数组——给最小/最大/跨度三值自动展开（owner 2026-11-15
    指正：手写 "1,2,3,4,5,5,6,7,8" 既是输入负担也是错值温床）。
    """
    if not isinstance(spec, dict):
        raise ValueError(f"perf.{name} 梯度须为 {{from, to, step}} 对象")
    unknown = set(spec) - GRADIENT_KEYS
    if unknown:
        raise ValueError(f"perf.{name} 未知键 {sorted(unknown)}"
                         f"（可用: {sorted(GRADIENT_KEYS)}）")
    try:
        start, stop, step = (int(spec["from"]) if integer else float(spec["from"]),
                             int(spec["to"]) if integer else float(spec["to"]),
                             int(spec["step"]) if integer else float(spec["step"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"perf.{name} 需要 from / to / step "
                         f"{'整数' if integer else '数值'}") from exc
    if start < 1 or stop < start or step <= 0:
        raise ValueError(f"perf.{name} 需要 from ≥1、to ≥ from 且 step > 0")
    n = int((stop - start) / step + 1e-9)
    levels = [round(start + i * step, 6) if not integer else start + i * step
              for i in range(n + 1)]
    if levels[-1] != stop:
        levels.append(stop)
    return levels, dict(spec.get("sla") or {})


def expand_ramp(ramp: dict) -> list[int]:
    """from/to/step → 并发梯度（to 必含：爬坡终点即最大并发）。"""
    levels, _sla = expand_gradient(ramp, name="ramp", integer=True)
    return levels


def resolve_sla(cfg: dict, *, allow: bool) -> dict:
    """爬坡 SLA（ramp.sla / rate.sla，{指标: 阈值}）→ 校验后的副本（空 = 未配）。

    判定方向复用 LOWER_IS_BETTER（≤ 阈值合规），其余 ≥（如 qps ≥ 10）；
    配置后压测逐档收口判定、首次违反即停（不再升档）并报告最大合规档。
    allow=False（无逐档熔断能力的引擎）配了即报错——错误显式化，不静默忽略。
    """
    sla: dict = {}
    for key in ("ramp", "rate"):
        carrier = cfg.get(key)
        if isinstance(carrier, dict) and carrier.get("sla") is not None:
            if sla:
                raise ValueError("perf.ramp.sla 与 perf.rate.sla 只能配一处"
                                 "（两载体互斥，正常不会同时出现）")
            sla = carrier["sla"]
    if not sla:
        return {}
    if not allow:
        raise ValueError("SLA 爬坡自动停止仅 evalscope-perf 引擎支持"
                         "（需逐档起压、档间收口判定）；locust 如需摸高请分档"
                         "建多次任务，或移除 sla 配置")
    if not isinstance(sla, dict) or not sla:
        raise ValueError("perf.sla 须为 {指标: 阈值} 非空对象")
    unknown = set(sla) - LEVEL_SLA_METRICS
    if unknown:
        raise ValueError(f"SLA 未知指标 {sorted(unknown)}"
                         f"（可用: {sorted(LEVEL_SLA_METRICS)}）")
    for v in sla.values():
        if not isinstance(v, (int, float)) or isinstance(v, bool) or v <= 0:
            raise ValueError("SLA 阈值须为正数（指标上限/下限）")
    return dict(sla)


def sla_verdict(metrics: dict, sla: dict) -> tuple[bool, str | None]:
    """档级指标 × SLA → (是否全部合规, 首个违反指标)。

    方向按 LOWER_IS_BETTER（≤ 阈值合规），其余 ≥；该指标本档未产出（None）
    不判定——宁缺毋错，不让缺数据误触发熔断。
    """
    violated = None
    for name, thr in sla.items():
        value = metrics.get(name)
        if value is None:
            continue
        ok = (float(value) <= float(thr) if name in LOWER_IS_BETTER
              else float(value) >= float(thr))
        if not ok and violated is None:
            violated = name
    return violated is None, violated


def resolve_load(cfg: dict) -> tuple[str, str, dict | None]:
    """负载 dataset / prompt / synthetic 三选一 → (dataset, prompt, synthetic)。

    synthetic 归一化为 {count, prompt_length}（int 已校验）。
    """
    dataset = str(cfg.get("dataset") or "").strip()
    prompt = str(cfg.get("prompt") or "").strip()
    synthetic = cfg.get("synthetic")
    chosen = [k for k, v in (("dataset", dataset), ("prompt", prompt),
                             ("synthetic", synthetic)) if v]
    if len(chosen) > 1:
        raise ValueError("perf.dataset / perf.prompt / perf.synthetic 三选一")
    if not chosen:
        raise ValueError("perf.dataset（数据集文件）/ perf.prompt（固定提示词）"
                         " / perf.synthetic（合成负载）三选一")
    if synthetic:
        if (not isinstance(synthetic, dict)
                or not isinstance(synthetic.get("count"), int)
                or isinstance(synthetic.get("count"), bool) or synthetic["count"] < 1
                or not isinstance(synthetic.get("prompt_length"), int)
                or isinstance(synthetic.get("prompt_length"), bool)
                or synthetic["prompt_length"] < 1):
            raise ValueError("perf.synthetic 须为 {count: ≥1, prompt_length: ≥1}"
                             "（条数 / 每条目标字符数）")
        synthetic = {"count": synthetic["count"],
                     "prompt_length": synthetic["prompt_length"]}
    return dataset, prompt, synthetic


def dataset_output_tokens(dataset: str | Path) -> int | None:
    """平台数据集 meta（同名 .stats.json）声明的期望输出长度；缺失/非法 → None。

    供 perf 适配器做 max_tokens 兜底：数据集生成时 output_tokens 写 meta，
    任务未显式配 max_tokens 时按其定长施压（输出长度对齐场景设计）。
    """
    meta = Path(dataset).with_suffix(".stats.json")
    if not meta.is_file():
        return None
    try:
        value = json.loads(meta.read_text(encoding="utf-8")).get("output_tokens")
    except (OSError, ValueError):
        return None
    if isinstance(value, int) and not isinstance(value, bool) and value >= 1:
        return value
    return None


def resolve_mode_parallel(cfg: dict, *, allow_probe: bool) -> tuple[str | None, list[int], dict]:
    """mode 分派 → (mode, parallel, probe_cfg)。

    baseline/ramp/soak/直配(v1) 公共解析；probe 仅 perf 支持（allow_probe=False
    时配置即报错）。probe_cfg 为 probe 段原 dict，供 perf 拼装 SLA 参数。
    """
    mode = cfg.get("mode")
    if mode is not None and mode not in MODES:
        raise ValueError(f"perf.mode 须 ∈ {sorted(MODES)}")
    if mode == "probe" and not allow_probe:
        raise ValueError("该引擎不支持 probe 模式（SLA 二分搜索是 evalscope perf"
                         " 专有）——如需摸高请用 engine: evalscope-perf")
    parallel: list[int] | None = None
    probe_cfg: dict = {}
    if mode in ("baseline", "soak"):
        b = cfg.get(mode) or {}
        if not isinstance(b.get("parallel"), int) or isinstance(b.get("parallel"), bool):
            raise ValueError(f"perf.{mode} 需要 parallel（整数）")
        parallel = [b["parallel"]]
    elif mode == "ramp":
        parallel = expand_ramp(cfg.get("ramp") or {})
    elif mode == "probe":
        probe_cfg = cfg.get("probe") or {}
        start = probe_cfg.get("from", 1)
        if not isinstance(start, int) or isinstance(start, bool) or start < 1:
            raise ValueError("perf.probe.from 须为 ≥1 整数（搜索起点）")
        parallel = [start]
    elif mode is None:
        parallel = cfg.get("parallel") or [1]   # v1 兼容：直配梯度
    if mode is not None and cfg.get("parallel"):
        raise ValueError(f"perf.mode={mode} 模式下不要直接配 perf.parallel")
    if not positive_ints(parallel):
        raise ValueError("perf.parallel 须为 ≥1 的整数数组")
    return mode, parallel, probe_cfg


@dataclass
class DurationPlan:
    """时长双键解析结果。per_level 直传 --duration；watchdog 为整场预算（probe）。"""

    per_level: float | None = None
    watchdog: float | None = None

    @property
    def duration_driven(self) -> bool:
        return self.per_level is not None or self.watchdog is not None


def resolve_durations(cfg: dict, *, mode: str | None, n_levels: int,
                      require: bool = False) -> DurationPlan:
    """duration（整场总时长）/ level_duration（每档秒数）二选一。

    单档直传；多档均分（近似预算）；probe 轮数不可预知 → 看门狗（total）。
    require=True（locust：无请求数上限语义）时长必配。
    """
    total = cfg.get("duration")
    per = cfg.get("level_duration")
    if total is not None and per is not None:
        raise ValueError("perf.duration 与 perf.level_duration 二选一"
                         "（整场总时长 / 每档秒数）")
    if total is not None:
        total = float(total)
        if total <= 0:
            raise ValueError("perf.duration 须为正数（秒）")
    if per is not None:
        per = float(per)
        if per <= 0:
            raise ValueError("perf.level_duration 须为正数（秒）")
    if require and total is None and per is None:
        raise ValueError("该引擎需要时长驱动：配 perf.duration（整场总时长）或"
                         " perf.level_duration（每档秒数）——无请求数上限语义")
    plan = DurationPlan()
    if mode == "probe" and total is not None:
        plan.watchdog = total          # 轮数不可预知，无法均分 → 看门狗
    elif per is not None:
        plan.per_level = per
    elif total is not None:
        plan.per_level = total if n_levels == 1 else total / n_levels
    return plan


def export_prompts(dataset: str, dest: Path) -> int:
    """平台数据集文件（jsonl/csv）→ 逐行 prompt 文件（evalscope custom 数据集）。

    返回导出条数。行数 = 压测条数；输入键取 input/question/instruction/
    prompt/query/user 首个命中（与平台 local 数据集约定一致）。
    """
    src = Path(dataset)
    if not src.is_file():
        raise ValueError(f"perf.dataset 文件不存在: {src}")
    records: list[dict] = []
    with open(src, encoding="utf-8") as fh:
        if src.suffix.lower() == ".csv":
            records = [dict(row) for row in csv.DictReader(fh)]
        else:
            for lineno, line in enumerate(fh, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError as exc:
                    raise ValueError(f"{src.name}:{lineno}: 行不是合法 JSON: {exc}") from exc
                if not isinstance(rec, dict):
                    raise ValueError(f"{src.name}:{lineno}: 行须为 JSON 对象")
                records.append(rec)
    prompts: list[str] = []
    for i, rec in enumerate(records, 1):
        for key in INPUT_KEYS:
            value = rec.get(key)
            if isinstance(value, str) and value.strip():
                prompts.append(value.strip())
                break
        else:
            raise ValueError(f"{src.name}: 第 {i} 条记录无输入字段（{list(INPUT_KEYS)}）")
    if not prompts:
        raise ValueError(f"{src.name}: 没有可用的 prompt")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text("\n".join(prompts) + "\n", encoding="utf-8")
    return len(prompts)


_SYNTHETIC_SKELETON = (
    "请围绕以下主题撰写一段说明文字，要求内容连贯、信息量充足：{filler}。"
    "主题编号 {i}，请从多个角度展开论述，保持输出长度稳定。"
)


def generate_synthetic_prompts(count: int, prompt_length: int) -> list[str]:
    """确定性合成负载：固定句式骨架 + 填充文本到目标字符数（每次生成完全一致）。

    「框架自动生成固定输入输出」——无数据集时压测可复现（输入一致则输出分布
    一致，TTFT/吞吐曲线可比）。填充用循环数字串避免真实语义导致的缓存命中。
    """
    if count < 1 or prompt_length < 1:
        raise ValueError("perf.synthetic 的 count / prompt_length 须为 ≥1 整数")
    prompts = []
    for i in range(1, count + 1):
        filler_cycle = "0123456789"
        filler = (filler_cycle * (prompt_length // len(filler_cycle) + 1))[:prompt_length]
        prompts.append(_SYNTHETIC_SKELETON.format(i=i, filler=filler)[: prompt_length + 60])
    return prompts


def append_metric_rows(rows: list[ToolMetricRow], thresholds: dict,
                       label: str, metrics: dict, total: int) -> None:
    """档级指标 → ToolMetricRow 追加（两压测适配器共享的发射逻辑）。

    value 为 None 跳过（宁缺毋错）；thresholds 未配的指标 passed=None（仅
    记录不判定）；方向按 LOWER_IS_BETTER（<=），其余 >=。
    """
    for name, value in metrics.items():
        if value is None:
            continue
        thr = thresholds.get(name)
        higher = name not in LOWER_IS_BETTER
        rows.append(ToolMetricRow(
            dataset=label, metric=name, value=float(value), count=total,
            threshold=thr, higher_is_better=higher,
            passed=((float(value) >= thr) if higher
                    else (float(value) <= thr)) if thr is not None else None,
        ))


def write_timeseries(out: Any, work_dir: Path, timeseries: dict) -> None:
    """秒级时序落盘（out.report_files 登记；series 为空不写，防空文件误导大屏）。"""
    if timeseries.get("series"):
        ts_path = work_dir / "timeseries.json"
        ts_path.write_text(json.dumps(timeseries, ensure_ascii=False),
                           encoding="utf-8")
        out.report_files["timeseries.json"] = str(ts_path)
