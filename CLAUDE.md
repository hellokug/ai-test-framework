# aite 项目约定

## 项目标准（已确认，不得随意变更）

### 架构与目标
- 项目目标与四层架构（接口层 / 编排层 / 插件层 / 基础层）确定后不随意改动。
- 契约级内容视为架构变更，须先讨论并在 `docs/roadmap.md` 记录决策后再改：
  `aite/core/interfaces.py`、`aite/core/types.py`、`/api/v1` 路由契约、`aite/storage/models.py` 表结构。
- 模块内部实现可自由演进，不受此限。

### 前后端与模块解耦
- 前后端逻辑分离：前端只依赖 `/api/v1` 契约，不碰后端内部；后端不内嵌前端逻辑。
- 跨模块只允许通过 `core/interfaces.py` 接口或 registry 访问，禁止深 import 其他模块内部实现。
- 不为一次性需求增加新抽象层；现有插件边界够用就不再加层。

### 开发流程
- 节奏：对齐需求 → 小设计（对象建模 / 接口签名）→ 测试 → 实现 → 更新 roadmap，不一次性写大量代码。
- 新功能必须带测试；修 bug 先写复现用例再修。
- 能力变更同步 README / docs。
- 接口新增一律双形态：CLI 子命令 + Python API 函数（REST 仅在明确需要时再加）。

### Git 管理
- 所有代码通过 git 管理，保持工作区干净；不随意提交。
- commit 对应一个完整可验证的单元（测试通过才提交），不提交 WIP、密钥、运行时产物。
- commit message 格式：`type: 简述`（type ∈ feat / fix / refactor / test / docs / chore）。

## 当前阶段

核心功能打磨优先，前端体验优化放到最后（现有免构建 SPA 暂不动）。

## 计划与进度

- 计划文件：`docs/roadmap.md`。完成或变更一项后，立即更新对应条目和文末进度记录。
- 完成标准：相关测试通过 + `ruff check aite tests` 零报错。

## 开发验证

```bash
pytest tests/
ruff check aite tests
```
