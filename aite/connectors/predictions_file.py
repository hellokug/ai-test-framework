"""离线评测入口：从本地 predictions JSONL 查补丁作为响应（SWE-bench predictions 契约）。

批量 agent（DeepSeek Harness / mini-swe-agent / SWE-agent 等）跑完 SWE-bench
后产出 predictions.jsonl（每行含 instance_id 与 model_patch）。本连接器按 case
逐条取出补丁作为 TargetResponse，让离线结果与在线 agent 走同一条评测管线
（打分 / 报告 / 存档 / 跨 run 对比）。

Config::

    target:
      type: predictions_file
      params:
        path: ./predictions.jsonl    # 每行一个 JSON 对象
        key_field: instance_id      # 可选；case.metadata 无此键时回退 case.id
        patch_field: model_patch    # 可选

缺预测 = 该实例未解（SWE-bench 语义）：返回空输出而非执行错误，交由
swe_resolution 按空补丁计 0 分；文件缺失/损坏才是显式 error。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

from aite.core.interfaces import TargetSystem
from aite.core.registry import register_connector
from aite.core.types import TargetResponse

if TYPE_CHECKING:
    from aite.core.loader import TargetConfig
    from aite.core.types import InvokeOptions, TestCase


@register_connector("predictions_file")
class PredictionsFileConnector(TargetSystem):
    name: ClassVar[str] = "predictions_file"

    def __init__(self, config: TargetConfig) -> None:
        params = config.params or {}
        self.path = Path(str(params.get("path") or ""))
        self.key_field = str(params.get("key_field") or "instance_id")
        self.patch_field = str(params.get("patch_field") or "model_patch")
        self._records: dict[str, dict[str, Any]] | None = None
        self._mtime: float | None = None

    def _load(self) -> dict[str, dict[str, Any]]:
        """JSONL → key_field 索引；mtime 变化即重读（同一进程内可换文件续跑）。"""
        if not self.path.exists():
            raise FileNotFoundError(f"predictions file not found: {self.path}")
        mtime = self.path.stat().st_mtime
        if self._records is None or mtime != self._mtime:
            records: dict[str, dict[str, Any]] = {}
            with self.path.open(encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    rec = json.loads(line)
                    key = rec.get(self.key_field)
                    if key is not None:
                        records[str(key)] = rec
            self._records, self._mtime = records, mtime
        return self._records

    async def invoke(self, case: TestCase, options: InvokeOptions) -> TargetResponse:
        try:
            records = self._load()
        except FileNotFoundError:
            return TargetResponse(error=f"predictions file not found: {self.path}")
        except (OSError, ValueError) as exc:
            return TargetResponse(error=f"predictions file unreadable: {self.path} ({exc})")
        key = str(case.metadata.get(self.key_field) or case.id)
        rec = records.get(key)
        if rec is None:
            return TargetResponse(output=None,
                                  raw={"predictions_file": {"missing": key}})
        return TargetResponse(output=rec.get(self.patch_field),
                              raw={"predictions_file": {"key": key}})

    async def close(self) -> None:
        self._records = None
