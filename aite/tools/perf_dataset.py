"""性能场景数据集生成器（模块 J）：语料池 + 长度分桶 + 前缀共享率。

压测负载形态由输入/输出长度分布与前缀缓存命中率决定——精度域 QA 数据集直接
复用会导致输出长度随模型漂移（结果不可复现不可比）、缓存命中只有一个固定档位
（synthetic 刻意零命中），无法代表 RAG 等高共享前缀场景。本模块从语料池按
场景配置确定性生成压测专用负载（jsonl，input 字段），两引擎经 ``perf.dataset``
直接消费；REST ``POST /perf/datasets`` 供前端对接。

设计约束：纯函数无 LLM 依赖；同 seed + 同语料 + 同配置 → 逐字节一致（可复现、
曲线可比）；长度以字符计量（与 synthetic.prompt_length 口径一致）。

命令行（脚本化生成）：
  python -m aite.tools.perf_dataset --corpus ./corpus --count 200 \
      --lengths 512,2048 --weights 0.7,0.3 --prefix-sharing 0.6 --seed 42
"""

from __future__ import annotations

import json
import random
from pathlib import Path

from aite.core.types import utcnow

INPUT_KEYS = ("text", "input", "content", "prompt", "question")
_CHUNK_CHARS = 400          # 语料池块目标字符数（切分粒度）
_PREFIX_RATIO = 0.8         # 共享前缀占目标长度的比例（其余为个体化后缀）


def load_corpus(path: str | Path) -> list[str]:
    """语料池：目录（.txt/.md/.jsonl）或单文件 → 文本块池（确定性顺序）。"""
    p = Path(path)
    if p.is_dir():
        files = sorted(x for x in p.iterdir()
                       if x.is_file() and x.suffix.lower() in (".txt", ".md", ".jsonl"))
        if not files:
            raise ValueError(f"语料池目录为空（需 .txt/.md/.jsonl）: {p}")
        texts: list[str] = []
        for f in files:
            texts.extend(_read_corpus_file(f))
    elif p.is_file():
        texts = _read_corpus_file(p)
    else:
        raise ValueError(f"perf 语料池不存在: {p}")
    pool = _chunk(texts)
    if len(pool) < 2:
        raise ValueError("语料池内容过少（切分后不足 2 块）——补充业务文档/对话日志")
    return pool


def _read_corpus_file(f: Path) -> list[str]:
    if f.suffix.lower() == ".jsonl":
        texts = []
        for lineno, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError as exc:
                raise ValueError(f"{f.name}:{lineno}: 行不是合法 JSON: {exc}") from exc
            hit = next((rec[k] for k in INPUT_KEYS
                        if isinstance(rec.get(k), str) and rec[k].strip()), None)
            if hit is None:
                raise ValueError(f"{f.name}:{lineno}: 无文本字段（{INPUT_KEYS}）")
            texts.append(hit)
        return texts
    return [f.read_text(encoding="utf-8")]


def _chunk(texts: list[str]) -> list[str]:
    """按行切段（≥20 字符）后贪婪合并为 ~_CHUNK_CHARS 的块。"""
    chunks: list[str] = []
    buf = ""
    for text in texts:
        for raw in text.splitlines():
            seg = raw.strip()
            if len(seg) < 20:
                continue
            buf = f"{buf}\n{seg}" if buf else seg
            if len(buf) >= _CHUNK_CHARS:
                chunks.append(buf)
                buf = ""
    if buf:
        chunks.append(buf)
    return chunks


def _fill_text(pool: list[str], start: int, target: int) -> str:
    """从 pool[start] 起循环拼接至 target 字符（截断定长）。"""
    parts: list[str] = []
    total = 0
    i = start
    while total < target:
        seg = pool[i % len(pool)]
        parts.append(seg)
        total += len(seg) + 1
        i += 1
    return "\n".join(parts)[:target]


def validate_scenario(cfg: dict) -> dict:
    """场景配置校验/归一化（API 与脚本共用；非法即 ValueError）。"""
    if not isinstance(cfg, dict):
        raise ValueError("场景配置须为对象")
    count = cfg.get("count")
    if not isinstance(count, int) or isinstance(count, bool) or count < 1:
        raise ValueError("count 须为 ≥1 的整数（生成条数）")
    lengths = cfg.get("input_lengths")
    if (not isinstance(lengths, list) or not lengths
            or not all(isinstance(v, int) and not isinstance(v, bool) and v >= 1
                       for v in lengths)):
        raise ValueError("input_lengths 须为 ≥1 的整数数组（输入长度分桶，字符）")
    weights = cfg.get("input_weights")
    if weights is None:
        weights = [1.0 / len(lengths)] * len(lengths)
    if (not isinstance(weights, list) or len(weights) != len(lengths)
            or not all(isinstance(v, (int, float)) and not isinstance(v, bool)
                       and v > 0 for v in weights)):
        raise ValueError("input_weights 须与 input_lengths 等长的正数数组")
    sharing = cfg.get("prefix_sharing", 0.0)
    if not isinstance(sharing, (int, float)) or isinstance(sharing, bool) \
            or not 0 <= sharing <= 1:
        raise ValueError("prefix_sharing 须为 0~1（共享前缀的请求占比）")
    groups = cfg.get("prefix_groups", 4)
    if not isinstance(groups, int) or isinstance(groups, bool) or groups < 1:
        raise ValueError("prefix_groups 须为 ≥1 整数（共享前缀组数）")
    output = cfg.get("output_tokens")
    if output is not None and (not isinstance(output, int) or isinstance(output, bool)
                               or output < 1):
        raise ValueError("output_tokens 须为 ≥1 整数（期望输出长度，写 meta 供参考）")
    seed = cfg.get("seed", 42)
    if not isinstance(seed, int) or isinstance(seed, bool):
        raise ValueError("seed 须为整数（确定性复现）")
    corpus = str(cfg.get("corpus") or "").strip()
    if not corpus:
        raise ValueError("corpus 必填（语料池：目录或 .txt/.md/.jsonl 文件）")
    return {"count": count, "lengths": lengths, "weights": weights,
            "sharing": float(sharing), "groups": groups,
            "output": output, "seed": seed, "corpus": corpus}


def generate_perf_dataset(cfg: dict, dest_dir: str | Path | None = None) -> dict:
    """场景配置 → 压测负载 jsonl（确定性）。

    dest_dir 给出时落盘 ``<name|perf-scene>-<时间戳>.jsonl`` + 同名 .stats.json，
    返回 path/file/count/stats/preview（preview 的 input 截断，防响应膨胀）。
    """
    spec = validate_scenario(cfg)
    pool = load_corpus(spec["corpus"])
    lengths, weights = spec["lengths"], spec["weights"]
    n_shared = round(spec["count"] * spec["sharing"])

    # 每组一条确定性组前缀长文（同组同样本共享前缀 → 服务端 KV cache 命中）
    group_prefixes: dict[int, str] = {}
    max_len = max(lengths)
    for g in range(spec["groups"] if n_shared else 0):
        rng = random.Random(f"{spec['seed']}:prefix:{g}")
        group_prefixes[g] = _fill_text(pool, rng.randrange(len(pool)),
                                       max(max_len - 1, 1))

    # 桶分配：按权重展开成确定性序列（前 floor 后 shuffle 会破坏可复现顺序，
    # 改用按权重比例的循环铺排——count 大时分布精确，count 小时亦确定）
    expanded: list[int] = []
    for length, w in zip(lengths, weights, strict=True):
        expanded.extend([length] * round(spec["count"] * w))
    expanded = (expanded * (spec["count"] // max(len(expanded), 1) + 1))[:spec["count"]]

    records: list[dict] = []
    for i, bucket in enumerate(expanded):
        rng = random.Random(f"{spec['seed']}:{i}")
        shared = i < n_shared
        if shared:
            g = i % spec["groups"]
            prefix = group_prefixes[g][: max(int(bucket * _PREFIX_RATIO) - 1, 0)]
            unique = _fill_text(pool, rng.randrange(len(pool)), bucket)
            text = f"{prefix}\n{unique}"[:bucket]
            prefix_chars = min(len(prefix), bucket)
        else:
            g = None
            prefix_chars = 0
            text = _fill_text(pool, rng.randrange(len(pool)), bucket)
        records.append({"input": text, "bucket": bucket, "actual_chars": len(text),
                        "prefix_group": g, "prefix_chars": prefix_chars,
                        "output_tokens": spec["output"]})

    stats = {
        "count": len(records),
        "buckets": {str(b): expanded.count(b) for b in sorted(set(expanded))},
        "shared_ratio": round(n_shared / len(records), 3) if records else 0.0,
        "prefix_groups": spec["groups"] if n_shared else 0,
        "avg_chars": round(sum(r["actual_chars"] for r in records) / len(records), 1)
        if records else 0.0,
        "output_tokens": spec["output"],
        "seed": spec["seed"],
    }
    result: dict = {"count": len(records), "stats": stats, "records": records}

    if dest_dir is not None:
        name = str(cfg.get("name") or "").strip() or "perf-scene"
        stamp = utcnow().astimezone().strftime("%Y%m%d-%H%M%S")   # 原则 11：utcnow 惯例
        dest = Path(dest_dir)
        dest.mkdir(parents=True, exist_ok=True)
        file_path = dest / f"{name}-{stamp}.jsonl"
        with open(file_path, "w", encoding="utf-8") as fh:
            for rec in records:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        stats_path = dest / f"{name}-{stamp}.stats.json"
        stats_path.write_text(json.dumps(stats, ensure_ascii=False), encoding="utf-8")
        result["path"] = str(file_path)
        result["file"] = file_path.name
        result["preview"] = [{**rec, "input": rec["input"][:200],
                              "input_truncated": len(rec["input"]) > 200}
                             for rec in records[:2]]
        del result["records"]
    return result


def main() -> None:  # pragma: no cover - 脚本入口
    import argparse

    ap = argparse.ArgumentParser(description="性能场景数据集生成（模块 J）")
    ap.add_argument("--corpus", required=True, help="语料池：目录或 .txt/.md/.jsonl")
    ap.add_argument("--count", type=int, required=True, help="生成条数")
    ap.add_argument("--lengths", required=True, help="输入长度分桶，逗号分隔（如 512,2048）")
    ap.add_argument("--weights", help="桶配比，逗号分隔（缺省均分）")
    ap.add_argument("--prefix-sharing", type=float, default=0.0, help="共享前缀占比 0~1")
    ap.add_argument("--prefix-groups", type=int, default=4)
    ap.add_argument("--output-tokens", type=int, help="期望输出长度（写 meta）")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--name", default="perf-scene")
    ap.add_argument("--out-dir", default="./perf_datasets")
    args = ap.parse_args()

    cfg = {"corpus": args.corpus, "count": args.count,
           "input_lengths": [int(x) for x in args.lengths.split(",")],
           "prefix_sharing": args.prefix_sharing, "prefix_groups": args.prefix_groups,
           "seed": args.seed, "name": args.name}
    if args.weights:
        cfg["input_weights"] = [float(x) for x in args.weights.split(",")]
    if args.output_tokens:
        cfg["output_tokens"] = args.output_tokens
    result = generate_perf_dataset(cfg, dest_dir=args.out_dir)
    print(json.dumps({k: v for k, v in result.items() if k != "preview"},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
