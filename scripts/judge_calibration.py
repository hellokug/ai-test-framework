"""判官校准 CLI（薄封装）：核心实现单一来源 aite.service.judge_calibration_task。

用法（判官 = 任一 OpenAI 兼容 /chat/completions 端点）：
    .venv/Scripts/python.exe scripts/judge_calibration.py \
        --dataset examples/accuracy/judge_calibration_demo.jsonl \
        --rubric examples/accuracy/judge_rubric_template.txt \
        --base-url http://localhost:11434/v1 --model qwen2.5:3b

平台内优先用精度页「判官校准」抽屉（POST /accuracy/tasks 的
task_type=judge_calibration 形态）：rubric 可沉淀为资产、结果落任务表、
前端有校准卡。本脚本用于临时/命令行场景，指标口径与平台任务完全一致。
报告 JSON 默认落数据集同目录 judge_calibration_report.json。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aite.service.judge_calibration_task import (  # noqa: E402,F401
    FIELD_ALIASES as FIELD_ALIASES,
)
from aite.service.judge_calibration_task import (  # noqa: E402
    SYSTEM_PROMPT,
    build_report,
    build_user_prompt,
    fingerprint,
    load_rows,
    parse_score,
)
from aite.service.judge_calibration_task import (
    cohen_kappa as cohen_kappa,
)
from aite.service.judge_calibration_task import (
    confusion as confusion,
)
from aite.service.judge_calibration_task import (
    kappa_verdict as kappa_verdict,
)
from aite.service.judge_calibration_task import (
    resolve_field as resolve_field,
)
from aite.service.judge_calibration_task import (
    spearman as spearman,
)
from aite.service.judge_calibration_task import (
    wilson_ci as wilson_ci,
)


def _call(client: httpx.Client, url: str, args: argparse.Namespace,
          prompt: str) -> float | None:
    """CLI 单行调用（允许 --api-key 明文：仅命令行现场，不入配置/git）。"""
    try:
        resp = client.post(url, json={"model": args.model, "temperature": 0,
                                      "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                                                   {"role": "user", "content": prompt}]},
                           headers={"Authorization": f"Bearer {args.api_key}"})
        resp.raise_for_status()
        return parse_score(resp.json()["choices"][0]["message"]["content"])
    except Exception as exc:  # noqa: BLE001 —— 单行失败计解析失败，不拖垮整轮
        print(f"[调用失败：{exc}]", file=sys.stderr)
        return None


def run(args: argparse.Namespace) -> dict:
    rows = load_rows(Path(args.dataset))
    if args.limit:
        rows = rows[:args.limit]
    rubric = Path(args.rubric).read_text(encoding="utf-8-sig")
    url = args.base_url.rstrip("/") + "/chat/completions"
    scores: list[float | None] = []
    with httpx.Client(timeout=args.timeout) as client:
        for i, row in enumerate(rows, 1):
            score = _call(client, url, args, build_user_prompt(rubric, row))
            scores.append(score)
            mark = "✗" if score is None else "✓" if score == row["human"] else "≠"
            shown = "无" if score is None else format(score, "g")
            print(f"[{i}/{len(rows)}] human={row['human']:g} judge={shown} {mark}",
                  file=sys.stderr)
    return build_report(rows, scores, judge_desc=f"{args.model} @ {args.base_url}",
                        rubric_fp=fingerprint(rubric),
                        dataset_desc=str(args.dataset))


def print_summary(report: dict) -> None:
    m = report["metrics"]
    miss, kill = m.get("miss_rate_漏放"), m.get("kill_rate_误杀")
    lines = [
        "==== 判官校准卡 ====",
        f"判官: {report['judge']}    rubric指纹: {report['rubric_sha256_12']}",
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
    out = (Path(args.output) if args.output
           else Path(args.dataset).with_name("judge_calibration_report.json"))
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print_summary(report)
    print(f"报告已写入 {out}")


if __name__ == "__main__":
    main()
