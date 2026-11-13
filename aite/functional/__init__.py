"""功能测试包（模块 D）：pytest 执行引擎的场景与模型访问层。

- ``harness``：pytest 插件（``-p aite.functional.harness`` 加载），为场景用例
  提供统一的模型访问（``chat`` / ``chat_raw`` fixture）
- 场景全部用户自管：默认根 ``config.DEFAULT_FUNCTIONAL_SCENARIOS_DIR``
  （或 suite yaml ``tools.tests_dir`` 指定），一个子目录 = 一个场景
"""
