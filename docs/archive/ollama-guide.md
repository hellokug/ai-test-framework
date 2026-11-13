# Ollama 本地环境操作手册

> 沉淀自 2026-08-29 实操：安装确认 → GPU 诊断 → 模型切换 → aite 验证全流程。
> 本机环境：Windows + NVIDIA RTX 2050（笔记本版，4GB 显存）。

## 0. 本机环境信息存档

| 项 | 值 |
|---|---|
| 安装位置 | `C:\Users\<用户名>\AppData\Local\Programs\Ollama\` |
| ollama.exe | `C:\Users\<用户名>\AppData\Local\Programs\Ollama\ollama.exe`（**未加入 PATH**） |
| 版本 | 0.33.1 |
| 服务端口 | `http://localhost:11434`（OpenAI 兼容端点为 `/v1`） |
| GPU | RTX 2050 4GB，驱动 531.88 / CUDA 12.1，推理后端 Vulkan |
| 可用显存 | 约 3.3GB（显示占用后） |
| 已装模型 | deepseek-r1:8b（4.9GB）、qwen2.5:3b（约 1.9GB） |

**关键认知：`aite` 不依赖 `ollama` 命令**。它直接请求
`http://localhost:11434/v1`，所以命令行敲不敲得动 `ollama` 不影响评测，
只影响手动管理模型时的操作方式（用全路径即可，见下文）。

## 1. 启动流程

```bash
# 1. 先检查服务是否已在跑（Windows 安装后默认开机自启，常驻系统托盘）
curl -s --max-time 3 http://localhost:11434/api/version
# 返回 {"version":"0.33.1"} 即正常；超时/拒绝则未启动

# 2. 未启动时，用全路径启动（二选一）
"C:/Users/<用户名>/AppData/Local/Programs/Ollama/ollama app.exe"   # 图形模式，托盘常驻
"C:/Users/<用户名>/AppData/Local/Programs/Ollama/ollama.exe" serve  # 纯命令行模式
```

可选：把安装目录加入 PATH 后可直接敲 `ollama`：

```powershell
setx PATH "$env:PATH;C:\Users\<用户名>\AppData\Local\Programs\Ollama"   # 对新开的终端生效
```

## 2. GPU 使用确认与"切换"

Ollama **默认自动用 GPU**，无需手动切换。要确认的是"装进去多少"：

```bash
# 1. 看 GPU 与显存占用
nvidia-smi

# 2. 看模型加载时卸载到 GPU 的层数（判断是否完整进显存）
#    日志位置：C:\Users\<用户名>\AppData\Local\Ollama\server.log
#    找关键行：
#      offloaded 37/37 layers to GPU   ← 完整进显存（qwen2.5:3b，理想状态）
#      offloaded 13/33 layers to GPU   ← 只装下部分（r1:8b，剩余层在 CPU 拖速度）
```

**显存适配规则（本机 4GB）**：

| 模型 | 体积 | 显存装下？ | 实测单条延迟（GSM8K） |
|---|---|---|---|
| qwen2.5:3b (Q4) | ~1.9GB | ✅ 37/37 层全进 | ~14.5s（热） |
| deepseek-r1:8b (Q4) | ~4.9GB | ❌ 13/33 层，剩余 CPU | ~225s |
| qwen2.5:7b (Q4) | ~4.7GB | ❌ 同上量级 | 预计与 r1:8b 相近 |

结论：**4GB 显存选 3B 级模型才能全量进 GPU**；8B 想全进需 ≥6GB 显存。
速度问题先查这条日志再考虑别的。

## 3. 模型管理流程

```bash
OLLAMA="C:/Users/<用户名>/AppData/Local/Programs/Ollama/ollama.exe"

"$OLLAMA" list              # 查看已装模型
"$OLLAMA" pull qwen2.5:3b   # 拉取新模型（约 2GB，看网速）
"$OLLAMA" rm <模型名>        # 删除模型省磁盘
curl -s http://localhost:11434/api/tags   # REST 方式查模型列表（免 PATH）
```

**在 aite 里切换模型**：改 suite YAML 的 `target.model` 一行即可，例如
`quickstart.yaml`：

```yaml
target:
  base_url: http://localhost:11434/v1
  model: qwen2.5:3b          # ← 改这里：qwen2.5:3b / deepseek-r1:8b
  params: {temperature: 0.2, max_tokens: 1024}
```

注意：换回 `deepseek-r1:8b` 时把 `max_tokens` 调到 4096——r1 输出带
`<think>` 思考过程，1024 会把答案截断。

## 4. 冷启动与热跑

- 首次请求含模型加载（本机约 20s），之后 Ollama 默认**缓存 5 分钟**
  （keep_alive），期间连续评测都是热速度
- 实测对比（3 条 GSM8K）：冷启动 p95 约 34s，热跑 14.5s
- 隔久了再跑多出一次冷启动属正常现象，不是性能退化

## 5. 本机模型选型建议

| 场景 | 用什么 | 理由 |
|---|---|---|
| 日常回归 / 冒烟 / 体验 | `qwen2.5:3b` | 全量进显存，14.5s/条 |
| 深度验收（准确率优先） | `deepseek-r1:8b` | 3/3 全对，但 ~225s/条，适合小样本 |
| 极速冒烟（可选） | `qwen2.5:1.5b` | 更小更快，准确率进一步下降 |

## 6. 常见问题排查

| 症状 | 原因 | 处理 |
|---|---|---|
| `ollama` 命令找不到 | 未加 PATH | 用全路径（见第 1 节），aite 不受影响 |
| aite 报连接失败 | 服务未启动 | 第 1 节启动流程 |
| 速度奇慢 | 模型未全量进显存 | 第 2 节查日志，换小模型 |
| 首条延迟高 | 冷启动 | 正常，5 分钟内复跑即热 |
