# 插件开发指南

aite 的四类组件（Connectors / Datasets / Metrics / Judges）与渲染器全部走同一个
Registry（`aite/core/registry.py`），三种注册方式任选：

## 1. 装饰器（进程内）

```python
from aite.core.registry import register_metric
from aite.core.interfaces import Metric
from aite.core.types import MetricResult


@register_metric("domain_compliance")
class DomainCompliance(Metric):
    aggregation = "rate"           # 汇总方式: mean|rate|p50|p95|p99|min|max|sum
    higher_is_better = True        # False 时阈值自动反转为 <=
    wants_variants = False         # True 则拿到 repeats/perturbations 全组响应

    async def compute(self, *, case, response, responses=None, ctx) -> MetricResult:
        ok = "必须包含的词" in response.output_text
        value = 1.0 if ok else 0.0
        return MetricResult(
            metric=self.name, value=value, raw={"hits": ok},
            passed=self.judge_passed(value),   # threshold 由 YAML 注入
        )
```

YAML 使用：`metrics: [{domain_compliance: {threshold: 0.9}}]`。

契约要点：

- `value` 归一化 0~1、越大越好；原始量纲放 `raw`。
- 构造参数来自 YAML 中除 `threshold/weight` 外的所有键。
- 报错不要抛异常：把 `error="..."` 写进 MetricResult，引擎会记录并跳过。

## 2. YAML 显式插件模块

```yaml
plugins: [myteam.aite_metrics, myteam.connectors]
run_name: ...
```

引擎在解析组件前导入这些模块（失败仅告警，不中断运行）。

## 3. entry-points（安装包级）

在你的 `pyproject.toml`：

```toml
[project.entry-points."aite.plugins"]
my_metrics = "myteam.aite_metrics"
```

安装后 `aite` 任何入口都会自动加载。

## 自定义连接器（TargetSystem）

```python
from aite.core.interfaces import TargetSystem
from aite.core.registry import register_connector
from aite.core.types import TargetResponse, Timings, TokenUsage


@register_connector("my_search_api")
class MySearchApi(TargetSystem):
    def __init__(self, config):        # config = YAML target 段
        self.config = config

    async def invoke(self, case, options) -> TargetResponse:
        import time; t0 = time.perf_counter()
        try:
            result = await self._call(case, options)
            return TargetResponse(
                output=result, usage=TokenUsage(1, 1, 2),
                timings=Timings(total_ms=(time.perf_counter()-t0)*1000))
        except Exception as exc:
            return TargetResponse(error=str(exc))   # 永远不要抛出
```

Agent/RAG 连接器约定：把轨迹放进 `response.trace`（`AgentTrace`），把检索上下文放进
`response.raw["retrieval_context"]`，Agent/RAG 指标即可直接工作
（参考 `connectors/langchain_agent.py` 与 `connectors/tracing.py`）。

## 自定义数据集（Dataset）

```python
from aite.core.registry import register_dataset
from aite.core.types import TestCase


@register_dataset("my_catalog")
class MyCatalog:
    def load(self, config):                 # 返回 async iterator
        async def gen():
            for row in fetch_rows(config.id, config.limit):
                yield TestCase(id=row["id"], input=row["q"],
                               expected=row["a"], metadata=row["tags"])
        return gen()
```

引擎在 load 之后统一做 sampling / limit / 变体物化，插件无需重复实现。

## 自定义 Judge

继承 `aite.core.interfaces.Judge`；`threshold` 在原始分纲（如 1-10），
设置 `scale_max=10` 后汇总层自动换算为归一化阈值。LLM 调用一律走
`ctx.llm_complete(...)`（Gateway 提供 temp=0+seed+限流+缓存+计费），
JSON 解析用 `aite.metrics.llm_based.parse_json_loose`。

## 自定义渲染器

```python
from aite.core.interfaces import ReportRenderer
from aite.core.registry import register_renderer

@register_renderer("slack")
class SlackRenderer(ReportRenderer):
    def render(self, report, dest):
        ...  # 返回写出的文件 Path
```

YAML：`report: [html, slack]`。
