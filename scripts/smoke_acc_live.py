"""精度域前端对接真机冒烟（live 后端全链路，隔离 DB/场景目录不污染用户数据）。

前置：aite serve --port 8310（AITE_DB 指向临时库；场景根默认 ./data/accuracy/
scenarios 相对 CWD，冒烟请在临时工作目录启动服务）。
覆盖：场景/脚本无预置（双空清单）→ 场景+脚本 CRUD → 条数口径（自研域落定 /
工具域未知）→ 创建任务(spec+version) → 任务列表/详情 → resolve_suite 双路径。
"""

import sys

import httpx

BASE = "http://127.0.0.1:8310/api/v1"


def main() -> int:
    c = httpx.Client(base_url=BASE, timeout=30)

    # 1. 场景/脚本无预置：空根 → 双空清单
    r = c.get("/accuracy/scenarios")
    assert r.status_code == 200 and r.json() == {"items": [], "count": 0}, r.text
    r = c.get("/accuracy/scripts")
    assert r.status_code == 200 and r.json() == {"items": [], "count": 0}, r.text
    print("1. 无预置资产（场景/脚本双空清单）OK")

    # 2. 场景 + 脚本 CRUD（evalscope spec，工具域条数不可知 → None）
    r = c.post("/accuracy/scenarios",
               json={"id": "smoke-scen", "display_name": "冒烟场景",
                     "dimension": "Agent"})
    assert r.status_code == 201, r.text
    spec = {"module": "accuracy", "run_name": "smoke-baseline", "engine": "evalscope",
            "version": "v9.9.9", "target": {"profile": "deepseek"},
            "tools": {"datasets": ["gsm8k"], "limit_per_subset": 5,
                      "metric_thresholds": "auto"},
            "report": ["json"], "output_dir": "./reports"}
    r = c.post("/accuracy/scripts", json={"scenario_id": "smoke-scen",
                                          "id": "smoke-acc", "name": "冒烟基线",
                                          "spec": spec})
    assert r.status_code == 201, r.text
    got = c.get("/accuracy/scripts/smoke-acc").json()
    assert got["spec"]["version"] == "v9.9.9"
    assert got["datasets"] == ["gsm8k"] and got["case_count"] is None
    r = c.patch("/accuracy/scripts/smoke-acc", json={"name": "冒烟基线改"})
    assert r.status_code == 200 and r.json()["name"] == "冒烟基线改"
    r = c.post("/accuracy/scripts", json={"scenario_id": "smoke-scen",
                                          "id": "smoke-acc", "spec": spec})
    assert r.status_code == 409                       # 全平台唯一
    r = c.delete("/accuracy/scenarios/smoke-scen")
    assert r.status_code == 409                       # 有脚本拒删
    assert c.delete("/accuracy/scenarios/smoke-scen",
                    params={"force": "true"}).status_code == 200
    print("2. 场景/脚本 CRUD + 条数口径 + 删除保护 OK")

    # 3. 创建任务（spec + version 顶层键；不 run，不触发 evalscope 子进程）
    r = c.post("/accuracy/tasks", json={"name": "smoke-acc-task", "spec": spec})
    assert r.status_code == 200, r.text
    task = r.json()
    assert task["status"] == "pending"
    r = c.get("/accuracy/tasks")
    assert any(t["id"] == task["id"] for t in r.json()["items"])
    r = c.get(f"/accuracy/tasks/{task['id']}")
    assert r.json()["spec"]["version"] == "v9.9.9"
    print(f"3. 创建任务 OK：{task['id'][:8]} pending，spec.version 落库")

    # 4. 任务详情 404 语义（前端回落路径）
    assert c.get("/accuracy/tasks/no-such").status_code == 404
    assert c.get("/accuracy/tasks/no-such/result").status_code == 404
    print("4. 详情/结果 404 OK")

    # 5. resolve_suite 双路径 version 兼容（run 时校验的预检）
    from aite.service.run_service import resolve_suite
    s1 = resolve_suite(spec)                                   # 工具域：raw 透传
    assert s1.is_tool and s1.raw["version"] == "v9.9.9"
    selfbuilt = {"module": "accuracy", "run_name": "agent-mini", "version": "v1.0",
                 "target": {"type": "custom_http", "base_url": "http://x",
                            "extract": "output"},
                 "datasets": [{"type": "local", "path": "d.jsonl",
                               "fields": {"input": "q", "expected": "a"}}],
                 "metrics": [{"contains": {}}]}
    s2 = resolve_suite(selfbuilt)                              # 自研域：SuiteConfig.version
    assert not s2.is_tool and s2.config.version == "v1.0"
    print("5. resolve_suite 双路径 version 兼容 OK")

    print("\n全部冒烟通过 ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
