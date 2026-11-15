"""判官校准执行器（精度域 task_type=judge_calibration）。

spec 契约（POST /accuracy/tasks 的第二形态，与 suite raw 共用 AccuracyTaskRow
与泛型域任务路由，不建新表、不动 taskkit）::

    {"task_type": "judge_calibration",
     "dataset": "<accuracy 数据集名>",       # 或 dataset_path: 绝对路径
     "judge": {"profile": "ollama-local"},   # 或 base_url+model（密钥走 api_key_env）
     "rubric_text": "评分标准（{question}/{pred}/{gold} 占位符）",
     "rubric_id": "沉淀资产 id（留痕，可选）", "limit": 0, "timeout_s": 120}

与 evalscope 精度任务的本质区别：被评对象是校准集里**预置的模型输出**列
（不跑被测模型——跑了就不是标注过的那批输出，κ 无意义），结论是「判官 vs
人工标注」的一致性而非模型分。执行器逐行调判官（温度 0，SCORE 行解析），
行间响应 cancel_event（graceful 停止 = 已判行保留、未判行记 None 出部分报告）；
报告含 rubric 内容指纹（执行侧现算，不信任客户端）——rubric 改一字旧报告即失效。
"""

from __future__ import annotations

import asyncio
import re
from collections import Counter
from pathlib import Path
from typing import Any

import httpx

from aite.datasets.local import _read_records
from aite.service.rubric_store import fingerprint

FIELD_ALIASES = {
    "question": ("question", "input", "query"),
    "pred": ("model_output", "prediction", "pred", "output"),
    "gold": ("reference_answer", "gold", "answer", "expected"),
    "human": ("human_score", "human_label", "label"),
}
SCORE_RE = re.compile(r"SCORE\s*[:：]\s*(-?\d+(?:\.\d+)?)", re.IGNORECASE)
OUTPUT_CONTRACT = "\n\n输出要求：先用一两句话说明理由，最后单独一行输出「SCORE: <分数>」。"
SYSTEM_PROMPT = "你是严格的评测判官，只按评分标准打分。"


def resolve_field(row: dict, field: str) -> Any:
    for key in FIELD_ALIASES[field]:
        if row.get(key) not in (None, ""):
            return row[key]
    return None


def load_rows(path: Path) -> list[dict]:
    """读校准集（jsonl/json/csv）并按别名归一，缺任一字段在入口报错。"""
    rows = []
    for i, item in enumerate(_read_records(path), 1):
        rec = {name: resolve_field(item, name) for name in FIELD_ALIASES}
        missing = [k for k, v in rec.items() if v is None]
        if missing:
            raise ValueError(f"第 {i} 行缺字段 {missing}（可用别名见 FIELD_ALIASES）")
        rec["human"] = float(rec["human"])
        rows.append(rec)
    return rows


def resolve_dataset_file(spec: dict) -> Path:
    """spec.dataset（accuracy 数据集名，走平台数据集目录）或 dataset_path（测试/临时）。"""
    path = spec.get("dataset_path")
    if path:
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"校准集不存在: {p}")
        return p
    name = str(spec.get("dataset") or "").strip()
    if not name:
        raise ValueError("校准 spec 缺 dataset（accuracy 数据集名）或 dataset_path")
    from aite.datasets.userstore import dataset_dir  # 懒导入对齐 service→datasets 边界

    p = dataset_dir("accuracy") / f"{name}.jsonl"
    if not p.exists():
        raise FileNotFoundError(f"accuracy 数据集不存在: {name}（{p}；先在数据集页上传）")
    return p


def resolve_judge(judge: dict) -> tuple[str, str, str]:
    """spec.judge（common 预制引用或内联端点）→ (chat 完整 URL, api_key, model 名)。

    复用 profiles.apply_common 的预制展开与 base.api_endpoint 的密钥解析
    （api_key 明文在此被拒，密钥一律 api_key_env）——与 evalscope 任务的
    判官解析同源，保证「校准的判官 = 跑任务的判官」。"""
    if not isinstance(judge, dict) or not (judge.get("profile") or judge.get("model")):
        raise ValueError("校准 spec.judge 需含 profile 或 base_url+model")
    from aite.core.profiles import apply_common
    from aite.tools.base import api_endpoint  # 懒导入：service 不在顶层依赖 tools

    resolved = apply_common({"tools": {"judge": dict(judge)}})["tools"]["judge"]
    if not resolved.get("base_url") or not resolved.get("model"):
        raise ValueError(f"判官配置解析后缺 base_url/model: {sorted(resolved)}")
    url, key = api_endpoint(resolved)
    return url, key, str(resolved["model"])


def build_user_prompt(rubric: str, row: dict) -> str:
    """rubric 模板按行替换占位符（不用 str.format，防模板其他花括号炸掉）。"""
    prompt = rubric
    for key in ("question", "pred", "gold"):
        prompt = prompt.replace("{" + key + "}", str(row[key]))
    return prompt + OUTPUT_CONTRACT


def parse_score(text: str) -> float | None:
    hits = SCORE_RE.findall(text or "")
    return float(hits[-1]) if hits else None


def _judge_once(client: httpx.Client, url: str, api_key: str, model: str,
                prompt: str) -> float | None:
    """单行判官调用：网络/解析失败计 None（进度帧可见，不拖垮整轮）。"""
    try:
        resp = client.post(url, json={"model": model, "temperature": 0,
                                      "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                                                   {"role": "user", "content": prompt}]},
                           headers={"Authorization": f"Bearer {api_key}"})
        resp.raise_for_status()
        return parse_score(resp.json()["choices"][0]["message"]["content"])
    except Exception:  # noqa: BLE001 —— 单行失败计解析失败
        return None


def wilson_ci(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    p = k / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    spread = z * (p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5 / denom
    return max(0.0, center - spread), min(1.0, center + spread)


def cohen_kappa(human: list[float], judge: list[float]) -> float:
    n = len(human)
    levels = sorted(set(human) | set(judge))
    h_marg, j_marg = Counter(human), Counter(judge)
    po = sum(h == j for h, j in zip(human, judge, strict=True)) / n
    pe = sum(h_marg.get(lv, 0) * j_marg.get(lv, 0) for lv in levels) / n / n
    if abs(1 - pe) < 1e-12:
        return 1.0 if po > 1 - 1e-12 else 0.0
    return (po - pe) / (1 - pe)


def confusion(human: list[float], judge: list[float]) -> dict[tuple[float, float], int]:
    return dict(Counter(zip(human, judge, strict=True)))


def _avg_ranks(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def spearman(human: list[float], judge: list[float]) -> float:
    rh, rj = _avg_ranks(human), _avg_ranks(judge)
    mh, mj = sum(rh) / len(rh), sum(rj) / len(rj)
    cov = sum((a - mh) * (b - mj) for a, b in zip(rh, rj, strict=True))
    vh = sum((a - mh) ** 2 for a in rh) ** 0.5
    vj = sum((b - mj) ** 2 for b in rj) ** 0.5
    return cov / (vh * vj) if vh and vj else 0.0


def kappa_verdict(kappa: float) -> str:
    if kappa < 0.4:
        return "弱——判官结论不可用于出报告，先改 rubric 或换判官再校准"
    if kappa < 0.6:
        return "中等——可作内部参考，不建议出正式结论"
    if kappa < 0.8:
        return "较强——可出正式报告，注意漏放率"
    return "优秀——可信度高"


def build_report(rows: list[dict], scores: list[float | None], *, judge_desc: str,
                 rubric_fp: str, dataset_desc: str, partial: bool = False) -> dict:
    valid = [(r, s) for r, s in zip(rows, scores, strict=True) if s is not None]
    n, hu, jd = len(rows), [r["human"] for r, _ in valid], [s for _, s in valid]
    nv = len(valid)
    if not n:
        raise ValueError("校准集为空")
    agree = sum(h == j for h, j in zip(hu, jd, strict=True))
    lo, hi = wilson_ci(agree, nv) if nv else (0.0, 0.0)
    kappa = cohen_kappa(hu, jd) if nv else 0.0
    levels = sorted(set(hu) | set(jd))
    metrics: dict[str, Any] = {
        "n": n, "n_valid": nv, "parse_failures": n - nv,
        "agreement": round(agree / nv, 4) if nv else 0.0,
        "agreement_ci95": [round(lo, 4), round(hi, 4)],
        "kappa": round(kappa, 4), "kappa_verdict": kappa_verdict(kappa),
        "miss_rate_漏放": round(sum(h == 0 and j > h for h, j in zip(hu, jd, strict=True)) / nv, 4)
        if nv else None,
        "kill_rate_误杀": round(sum(h == 1 and j < h for h, j in zip(hu, jd, strict=True)) / nv, 4)
        if nv else None,
    }
    if len(levels) > 2 and nv:
        metrics["spearman"] = round(spearman(hu, jd), 4)
        metrics["within_1_rate"] = round(
            sum(abs(h - j) <= 1 for h, j in zip(hu, jd, strict=True)) / nv, 4)
        metrics["mae"] = round(sum(abs(h - j) for h, j in zip(hu, jd, strict=True)) / nv, 4)
    conf = confusion(hu, jd).items() if nv else []
    matrix = {format(h, "g"): {format(j, "g"): c for (hh, j), c in conf if hh == h}
              for h in levels}
    disagree = [{"i": i, "question": str(r["question"])[:120], "human": r["human"],
                 "judge": s} for i, (r, s) in enumerate(zip(rows, scores, strict=True), 1)
                if s is not None and s != r["human"]]
    return {"dataset": dataset_desc, "judge": judge_desc, "rubric_sha256_12": rubric_fp,
            "partial": partial, "metrics": metrics,
            "confusion_matrix": {"行=人工分,列=判官分": matrix},
            "disagree_rows": disagree}


async def execute_calibration(ctx: Any, *,
                              transport: httpx.BaseTransport | None = None) -> dict:
    """TaskExecutor 入口：spec 解析 → 逐行调判官（行间可 graceful 停止）→ 校准报告。"""
    spec = ctx.spec or {}
    rows = load_rows(resolve_dataset_file(spec))
    limit = int(spec.get("limit") or 0)
    if limit:
        rows = rows[:limit]
    rubric = str(spec.get("rubric_text") or "").strip()
    if not rubric:
        raise ValueError("校准 spec 缺 rubric_text（评分标准文本；可从 rubric 资产带入）")
    url, api_key, model = resolve_judge(spec.get("judge") or {})
    rubric_fp = fingerprint(rubric)
    scores: list[float | None] = []
    partial = False
    with httpx.Client(timeout=float(spec.get("timeout_s") or 120),
                      transport=transport) as client:
        for i, row in enumerate(rows, 1):
            if ctx.cancel_event.is_set():
                partial = True
                break
            prompt = build_user_prompt(rubric, row)
            score = await asyncio.to_thread(_judge_once, client, url, api_key, model, prompt)
            scores.append(score)
            shown = "无" if score is None else format(score, "g")
            ctx.emit({"note": f"[{i}/{len(rows)}] human={row['human']:g} judge={shown}"})
    if partial:
        scores.extend([None] * (len(rows) - len(scores)))
    report = build_report(rows, scores, judge_desc=f"{model} @ {url}",
                          rubric_fp=rubric_fp,
                          dataset_desc=str(spec.get("dataset") or spec.get("dataset_path") or ""),
                          partial=partial)
    return {"engine": "judge_calibration", "total_cases": len(rows),
            "evaluated_cases": report["metrics"]["n_valid"],
            "calibration_report": report}
