"""preflight（收尾一键）纯函数回归：diff 归类、导入图反向闭包选测、重启判定与门禁计划。"""
from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_SPEC = importlib.util.spec_from_file_location("preflight", ROOT / "scripts" / "preflight.py")
pf = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(pf)


def _write(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def test_classify_buckets():
    b = pf.classify([
        "aite/service/x.py", "web/routers/y.py",
        "tests/unit/test_x.py", "scripts/preflight.py",
        "web-ui/mockups/v1/api32.js", "web-ui/assets/v1.css",
        "docs/efficiency-log.md", "start.sh",
    ])
    assert b["pkg_py"] == ["aite/service/x.py", "web/routers/y.py"]
    assert b["tests_py"] == ["tests/unit/test_x.py"]
    assert b["scripts_py"] == ["scripts/preflight.py"]
    assert b["mockups"] == ["web-ui/mockups/v1/api32.js", "web-ui/assets/v1.css"]
    assert b["docs"] == ["docs/efficiency-log.md"]
    assert b["other"] == ["start.sh"]


def test_module_name():
    assert pf.module_name("aite/service/x.py") == "aite.service.x"
    assert pf.module_name("aite/__init__.py") == "aite"
    assert pf.module_name("web-ui/mockups/x.js") is None
    assert pf.module_name("scripts/x.py") is None


def test_affected_tests_by_import_graph(tmp_path: Path):
    _write(tmp_path / "aite" / "b.py", "def f():\n    return 1\n")
    _write(tmp_path / "aite" / "a.py", "from aite.b import f\n")
    _write(tmp_path / "aite" / "c.py", "X = 1\n")
    _write(tmp_path / "tests" / "test_a.py", "import aite.a\n")
    _write(tmp_path / "tests" / "test_b.py", "import aite.b\n")
    _write(tmp_path / "tests" / "test_c.py", "import aite.c\n")
    picked = pf.affected_tests(tmp_path, ["aite/b.py"])
    assert "tests/test_a.py" in picked
    assert "tests/test_b.py" in picked
    assert "tests/test_c.py" not in picked
    assert pf.affected_tests(tmp_path, ["aite/c.py"]) == ["tests/test_c.py"]


def test_relative_import_resolution(tmp_path: Path):
    _write(tmp_path / "aite" / "pkg" / "__init__.py", "")
    _write(tmp_path / "aite" / "pkg" / "base.py", "Y = 1\n")
    _write(tmp_path / "aite" / "pkg" / "m.py", "from .base import Y\n")
    _write(tmp_path / "tests" / "test_base.py", "import aite.pkg.base\n")
    _write(tmp_path / "tests" / "test_m.py", "import aite.pkg.m\n")
    picked = pf.affected_tests(tmp_path, ["aite/pkg/base.py"])
    assert "tests/test_base.py" in picked
    assert "tests/test_m.py" in picked


def test_scripts_name_convention(tmp_path: Path):
    _write(tmp_path / "tests" / "unit" / "test_widgets.py", "X = 1\n")
    assert pf.affected_tests(tmp_path, ["scripts/widgets.py"]) == ["tests/unit/test_widgets.py"]


def test_restart_hint():
    assert any("→ 需重启" in h for h in pf.restart_hint(pf.classify(["aite/x.py"])))
    assert any("无需重启" in h for h in pf.restart_hint(pf.classify(["docs/a.md"])))
    hints = pf.restart_hint(pf.classify(["web-ui/mockups/v1/api32.js"]))
    assert any("bump" in h for h in hints)


def test_gate_plan_mockups_only_adds_mockup_gate():
    assert "mockup_symbols --check" not in [n for n, _ in pf.gate_plan(False)]
    assert "mockup_symbols --check" in [n for n, _ in pf.gate_plan(True)]
