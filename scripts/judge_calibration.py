"""判官校准最小闭环：判官对带人工标注的校准集逐行打分，与人工分对比产出校准卡。

用法（判官 = 任一 OpenAI 兼容 /chat/completions 端点，温度固定 0）：
    .venv/Scripts/python.exe scripts/judge_calibration.py \
        --dataset examples/accuracy/judge_calibration_demo.jsonl \
        --rubric examples/accuracy/judge_rubric_template.txt \
        --base-url http://localhost:11434/v1 --model qwen2.5:3b

校准集行字段（别名容错）：question|input|query、model_output|prediction|pred|output、
reference_answer|gold|answer|expected、human_score|human_label|label。
报告 JSON 落 --output（默认数据集同目录 judge_calibration_report.json），
含 rubric sha256 指纹——校准结论只对该指纹的 rubric 有效，rubric 一改需重新校准。
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path

import httpx

FIELD_ALIASES = {
    "question": ("question", "input", "query"),
    "pred": ("model_output", "prediction", "pred", "output"),
    "gold": ("reference_answer", "gold", "answer", "expected"),
    "human": ("human_score", "human_label", "label"),
}
SCORE_RE = re.compile(r"SCORE\s*[:：]\s*(-?\d+(?:\.\d+)?)", re.IGNORECASE)
OUTPUT_CONTRACT = "\n\n输出要求：先用一两句话说明理由，最后单独一行输出「SCORE: <分数>」。"


def resolve_field(row: dict, field: str):
    for key in FIELD_ALIASES[field]:
        if row.get(key) not in (None, ""):
            return row[key]
    return None


def _read_raw(path: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8-sig")
    suffix = path.suffix.lower()
    if suffix == ".jsonl":
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    if suffix == ".json":
        data = json.loads(text)
        if isinstance(data, list):
            return data
        for key in ("data", "cases", "items", "records"):
            if isinstance(data.get(key), list):
                return data[key]
        raise ValueError("JSON 对象里没有数组字段（data/cases/items/records）")
    if suffix == ".csv":
        return list(csv.DictReader(text.splitlines()))
    raise ValueError(f"不支持的格式 {suffix}（支持 jsonl/json/csv）")


def load_rows(path: Path) -> list[dict]:
    """读校准集并按别名归一为 question/pred/gold/human，缺任一字段即报错。"""
    rows = []
    for i, item in enumerate(_read_raw(path), 1):
        rec = {name: resolve_field(item, name) for name in FIELD_ALIASES}
        missing = [k for k, v in rec.items() if v is None]
        if missing:
            raise ValueError(f"第 {i} 行缺字段 {missing}（可用别名见脚本头注释）")
        rec["human"] = float(rec["human"])
        rows.append(rec)
    return rows


def build_user_prompt(rubric: str, row: dict) -> str:
    """rubric 模板按行替换占位符（不用 str.format，避免模板里其他花括号炸掉）。"""
    prompt = rubric
    for key in ("question", "pred", "gold"):
        prompt = prompt.replace("{" + key + "}", str(row[key]))
    return prompt + OUTPUT_CONTRACT


def call_judge(client: httpx.Client, args: argparse.Namespace, user_prompt: str) -> str:
    resp = client.post(
        f"{args.base_url.rstrip('/')}/chat/completions",
        json={"model": args.model, "temperature": 0,
              "messages": [{"role": "system", "content": "你是严格的评测判官，只按评分标准打分。"},
                           {"role": "user", "content": user_prompt}]},
        headers={"Authorization": f"Bearer {args.api_key}"},
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def parse_score(text: str) -> float | None:
    hits = SCORE_RE.findall(text or "")
    return float(hits[-1]) if hits else None


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


def run(args: argparse.Namespace) -> dict:
    """逐行调判官打分并汇总校准卡（单行失败计为解析失败，不拖垮整轮）。"""
    rows = load_rows(Path(args.dataset))
    if args.limit:
        rows = rows[: args.limit]
    rubric = Path(args.rubric).read_text(encoding="utf-8-sig")
    rubric_fp = hashlib.sha256(rubric.strip().encode("utf-8")).hexdigest()[:12]
    scores: list[float | None] = []
    with httpx.Client(timeout=args.timeout) as client:
        for i, row in enumerate(rows, 1):
            score: float | None = None
            try:
                score = parse_score(call_judge(client, args, build_user_prompt(rubric, row)))
            except Exception as exc:  # noqa: BLE001 —— 单行网络失败不拖垮整轮
                print(f"[{i}/{len(rows)}] 调用失败：{exc}", file=sys.stderr)
            scores.append(score)
            mark = "✗" if score is None else "✓" if score == row["human"] else "≠"
            print(f"[{i}/{len(rows)}] human={row['human']:g} judge="
                  f"{'无' if score is None else format(score, 'g')} {mark}", file=sys.stderr)
    return build_report(args, rubric_fp, rows, scores)


def build_report(args: argparse.Namespace, rubric_fp: str,
                 rows: list[dict], scores: list[float | None]) -> dict:
    valid = [(r, s) for r, s in zip(rows, scores, strict=True) if s is not None]
    n, hu, jd = len(rows), [r["human"] for r, _ in valid], [s for _, s in valid]
    if not n:
        raise ValueError("校准集为空")
    agree = sum(h == j for h, j in zip(hu, jd, strict=True))
    lo, hi = wilson_ci(agree, len(valid)) if valid else (0.0, 0.0)
    kappa = cohen_kappa(hu, jd) if valid else 0.0
    levels = sorted(set(hu) | set(jd))
    metrics = {
        "n": n, "n_valid": len(valid), "parse_failures": n - len(valid),
        "agreement": round(agree / len(valid), 4) if valid else 0.0,
        "agreement_ci95": [round(lo, 4), round(hi, 4)],
        "kappa": round(kappa, 4), "kappa_verdict": kappa_verdict(kappa),
        "miss_rate_漏放": round(sum(h == 0 and j > h for h, j in zip(hu, jd, strict=True)) / len(valid), 4) if valid else None,
        "kill_rate_误杀": round(sum(h == 1 and j < h for h, j in zip(hu, jd, strict=True)) / len(valid), 4) if valid else None,
    }
    if len(levels) > 2 and valid:
        metrics["spearman"] = round(spearman(hu, jd), 4)
        metrics["within_1_rate"] = round(sum(abs(h - j) <= 1 for h, j in zip(hu, jd, strict=True)) / len(valid), 4)
        metrics["mae"] = round(sum(abs(h - j) for h, j in zip(hu, jd, strict=True)) / len(valid), 4)
    conf = confusion(hu, jd)
    matrix = {format(h, "g"): {format(j, "g"): c for (hh, j), c in conf.items() if hh == h}
              for h in levels}
    disagree = [{"i": i, "question": r["question"], "human": r["human"], "judge": s}
                for i, (r, s) in enumerate(zip(rows, scores, strict=True), 1)
                if s is not None and s != r["human"]]
    return {
        "dataset": args.dataset,
        "judge": {"base_url": args.base_url, "model": args.model},
        "rubric_sha256_12": rubric_fp,
        "metrics": metrics,
        "confusion_matrix": {"行=人工分,列=判官分": matrix},
        "disagree_rows": disagree,
    }


def print_summary(report: dict) -> None:
    m = report["metrics"]
    miss, kill = m.get("miss_rate_漏放"), m.get("kill_rate_误杀")
    lines = [
        "==== 判官校准卡 ====",
        f"判官: {report['judge']['model']} @ {report['judge']['base_url']}"
        f"    rubric指纹: {report['rubric_sha256_12']}",
        f"n={m['n']} 有效={m['n_valid']} 解析失败={m['parse_failures']}",
        f"一致率 {m['agreement']:g}（95%CI {m['agreement_ci95'][0]:g}~{m['agreement_ci95'][1]:g}）",
        f"Cohen's κ {m['kappa']:g}（{m['kappa_verdict']}）",
        f"漏放率(人工0判1,致报告虚高) {miss:g}    误杀率(人工1判0,致报告偏低) {kill:g}",
        "混淆矩阵(行=人工,列=判官): "
        + "  ".join(f"{h}:{json.dumps(cols, ensure_ascii=False)}"
                    for h, cols in report["confusion_matrix"]["行=人工分,列=判官分"].items()),
    ]
    if "spearman" in m:
        lines.append(f"Spearman ρ {m['spearman']:g}    容差≤1命中率 {m['within_1_rate']:g}    MAE {m['mae']:g}")
    lines.append(f"不一致 {len(report['disagree_rows'])} 行，明细见报告 JSON 的 disagree_rows")
    print("\n".join(lines))


def main() -> None:
    ap = argparse.ArgumentParser(description="判官校准：判官分 vs 人工标注 → 校准卡")
    ap.add_argument("--dataset", required=True, help="校准集文件（jsonl/json/csv，含人工标注列）")
    ap.add_argument("--rubric", required=True, help="rubric 模板文件（含 {question}/{pred}/{gold}）")
    ap.add_argument("--base-url", required=True, help="判官 OpenAI 兼容端点，如 http://localhost:11434/v1")
    ap.add_argument("--model", required=True, help="判官模型名")
    ap.add_argument("--api-key", default="EMPTY", help="判官 API Key（本地端点用默认值）")
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 行（0=全部），冒烟用")
    ap.add_argument("--timeout", type=float, default=120, help="单行判官调用超时秒数")
    ap.add_argument("--output", help="报告 JSON 路径（默认数据集同目录 judge_calibration_report.json）")
    args = ap.parse_args()
    report = run(args)
    out = Path(args.output) if args.output else Path(args.dataset).with_name("judge_calibration_report.json")
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print_summary(report)
    print(f"报告已写入 {out}")


if __name__ == "__main__":
    main()
