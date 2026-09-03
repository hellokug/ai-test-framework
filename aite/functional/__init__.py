"""功能测试包（模块 D）：pytest 执行引擎的场景与模型访问层。

- ``harness``：pytest 插件（``-p aite.functional.harness`` 加载），为场景用例
  提供统一的模型访问（``chat`` / ``chat_raw`` fixture）
- ``scenarios``：内置功能场景，一个子目录 = 一个场景（真实调用模型 + 规则断言）；
  用户也可通过 suite yaml 的 ``tools.tests_dir`` 指定自建场景目录
"""
