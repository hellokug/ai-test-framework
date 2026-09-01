"""把 HF ceval/ceval-exam（52 科目）的 val+dev 全集转成 aite 离线数据集 ceval_full。

- 只取 val(1346)/dev(260)：test split 答案不公开，无法自动评测
- A/B/C/D 四个独立列 -> choices 列表 + 字母答案（aite builtin 的多选题格式，
  加载时自动拼装成 "A. xxx / B. xxx ..." 选项题面）
- subject/split 写入 metadata，可用 group_by: subject 做分科目统计
"""

import json
from datetime import datetime, timezone

from datasets import get_dataset_config_names, load_dataset

from aite.datasets import userstore

HF_ID = "ceval/ceval-exam"
SPLITS = ("val", "dev")
DS_ID = "ceval_full"

configs = get_dataset_config_names(HF_ID)
records = []
for cfg in configs:
    for split in SPLITS:
        ds = load_dataset(HF_ID, cfg, split=split)
        for i, row in enumerate(ds):
            answer = str(row.get("answer") or "").strip().upper()
            if answer not in ("A", "B", "C", "D"):
                continue  # 无答案行（test 泄入保护）跳过
            records.append({
                "id": f"{cfg}-{split}-{i}",
                "question": str(row.get("question") or ""),
                "choices": [str(row.get(k) or "") for k in "ABCD"],
                "answer": answer,
                "subject": cfg,
                "split": split,
            })

out_dir = userstore.data_root() / "datasets"
out_dir.mkdir(parents=True, exist_ok=True)
path = out_dir / f"{DS_ID}.jsonl"
with path.open("w", encoding="utf-8") as fh:
    for r in records:
        fh.write(json.dumps(r, ensure_ascii=False) + "\n")

userstore.upsert(DS_ID, {
    "file": f"datasets/{DS_ID}.jsonl",
    "external": True,
    "upstream": f"hf:{HF_ID} (val+dev, {len(configs)} subjects)",
    "n": len(records),
    "imported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
})
print(f"configs={len(configs)} records={len(records)} -> {path}")
