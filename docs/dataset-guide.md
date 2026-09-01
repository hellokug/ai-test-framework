# 数据集使用指南：在线与离线

> 三种数据集类型、离线下载命令、在线/离线评测写法一页说清。
> 所有命令均在项目根目录执行。

## 三种数据集类型

| type | 网络 | 数据来源 | 适合 |
|---|---|---|---|
| `builtin` | ❌ | 包内置 5 个采样集 + `./data/` 外部注册 | 冒烟、演示、离线全集 |
| `local` | ❌ | 本地 JSONL/JSON/CSV 文件 | 私有业务数据 |
| `hf_hub` | ✅ | HuggingFace 运行时下载 | 临时跑全量公开基准 |

查看可用 builtin（含已导入的离线全集）：

```bash
aite dataset list            # 目录 + 推荐指标
aite dataset show ceval_full # 单个数据集详情
```

## 在线评测（hf_hub）

一次性环境准备（装依赖 + 国内镜像）：

```powershell
pip install 'aite[bench]'
$env:HF_ENDPOINT = "https://hf-mirror.com"    # 每次 PowerShell 会话设一次
```

```yaml
datasets:
  - {type: hf_hub, id: openai/gsm8k, split: test}
  - {type: hf_hub, id: ceval/ceval-exam, kwargs: {name: accountant}, split: val}  # 指定子集
```

```bash
aite run -c suite.yaml
```

注意：`hf_hub` 每次跑都依赖网络与 HF 缓存；换机器/离线环境会挂——要反复跑的基准建议先离线化（见下）。

## 下载成离线数据集（推荐做法）

### 标准：add-hf 命令

```bash
# 下载 → 字段自动识别（question/answer/choices 等常见命名）→ 预采样 → 注册
aite dataset add-hf openai/gsm8k --name gsm8k_full --split test -n 200

# 字段名生僻时手动映射
aite dataset add-hf some/data --name mydata --fields input=qry,expected=gold
```

- 产物：`./data/datasets/<name>.jsonl` + 注册表 `./data/aite_datasets.json`（不进包源码）
- 之后 `{type: builtin, id: <name>}` **永久离线可用**
- `-n` 预采样强烈建议（全量动辄上万条，先想清楚要不要全跑）
- Python API 同名：`aite.datasets.add_hf.add_hf_dataset(...)`
- 想让外部数据集也支持 `metrics: auto`：在 `data/aite_datasets.json` 对应条目里加
  `"recommend": [{"name": "choice_accuracy", "threshold": 0.25}]`（ceval_full 已带）

### 特殊形状：转换脚本模式

HF 上不少数据集选项是 A/B/C/D **独立列**（如 C-Eval），`add-hf` 认不出，需小转换脚本
（参考 `scripts/convert_ceval.py`，核心是把四列拼成 `choices` 列表 + 字母答案）：

```python
records.append({
    "id": f"{subject}-{i}",
    "question": row["question"],
    "choices": [str(row[k]) for k in "ABCD"],   # A-D 四列 → choices
    "answer": row["answer"].upper(),            # 字母答案
    "subject": subject,                          # 元数据可 group_by
})
# 写入 ./data/datasets/xxx.jsonl 并 userstore.upsert(name, {...}) 注册
```

已用此方式落地的离线全集：`ceval_full`（52 科目 1606 题，val+dev；test split
答案不公开无法自动评测）。

### 离线数据的目标格式

```jsonl
{"id": "c-1", "question": "题干", "answer": "标准答案"}
{"id": "c-2", "question": "多选题干", "choices": ["甲","乙","丙","丁"], "answer": "C"}
{"id": "c-3", "question": "RAG题干", "answer": "答案", "retrieval_context": ["参考文档1"]}
```

字段自动识别：输入认 `question/input/instruction/prompt`；答案认
`answer/expected/gold/output/label`；`answer` 可为数组（多候选任一命中即过）；
其余字段全进 metadata。

## 离线评测（builtin / local）

```yaml
datasets:
  - {type: builtin, id: ceval_full, group_by: subject}   # 离线全集，分科目统计
  - {type: local,  path: ./data/my_cases.jsonl}          # 私有数据
  - {type: builtin, id: gsm8k, sample: {n: 50, seed: 42, shuffle: true}}  # 抽样
metrics: auto          # builtin 数据集自动用推荐指标；local 需显式配置
```

```bash
aite run -c suite.yaml
```

### 常用参数速查

| 参数 | 作用 | 备注 |
|---|---|---|
| `sample: {n, seed, shuffle}` | 抽样 | shuffle 默认 false=取前 N；跨 run 对比必须锁同一批题 |
| `limit` / `--limit` | 截断前 N | 无随机语义 |
| `group_by: subject` | 按 metadata 字段分组统计 | 报告出分组 breakdown |
| `--concurrency` | 并发覆盖 | 云端 API 有效；本地单卡 Ollama 无收益（GPU 吞吐是瓶颈） |

## 命令速查

```bash
aite dataset list                                  # 所有 builtin + 已导入离线集
aite dataset show <id>                             # 详情
aite dataset add-hf <hf_id> --name <id> [-n 200]   # 在线下载 → 离线注册
aite run -c suite.yaml                             # 跑评测
```
