"""ZCode 钩子脚本回归：context_sync（规则同步 / 账本与认领注入 / 上下文提醒）、
stop_efficiency_gate（量化复盘门禁）及其取数来源 session_stats。

钩子由 ZCode 以独立进程调用，这里两层覆盖：函数级（改路径常量指向 tmp_path）验证逻辑，
子进程级验证与 ZCode 的契约（退出码、stdout 严格 schema、坏输入 fail-open）。
"""
from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
CONTEXT_SYNC = SCRIPTS / "hooks" / "context_sync.py"
STOP_GATE = SCRIPTS / "hooks" / "stop_efficiency_gate.py"

EFF_LOG = (
    "说明 | 不是表格\n\n| 日期 | 低效点 | 建议动作 | 状态 |\n|---|---|---|---|\n"
    "| 2026-11-08 | 全量回归跑 3 次 | affected_tests.py | open |\n"
    "| 2026-11-08 | 汇总行被吞 | 统一 `2>&1 \\| tail -1` | done |\n"
    "| 2026-11-09 | 管道 `a \\| b` 截断 | 改用文件 | open |\n"
    "| 2026-11-10 | 已关账带说明 | 已落地 affected_tests.py | done（2026-11-16 核对） |\n"
)

CLAIMS_MD = (
    "# 多会话认领（活租约，不是账本）\n\n格式：一行一条\n\n## 在认领\n\n"
    "- 2026-11-12 10:00｜会话 A 改功能引擎｜aite/functional｜12:00\n"
    "- 2026-11-12 10:05｜会话 B 改静态稿｜web-ui/mockups｜12:30\n\n"
    "## 已释放\n\n- 2026-11-11｜旧任务｜旧域｜已删\n"
)


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    previous = sys.modules.get(name)
    sys.modules[name] = module   # @dataclass 装饰时按模块名回查 sys.modules
    try:
        spec.loader.exec_module(module)
    finally:
        if previous is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = previous
    return module


def _usage_db(path: Path) -> Path:
    """session_stats / context_sync 读到的最小计量库：一轮已完成，2 次主请求 + 1 次标题请求。"""
    con = sqlite3.connect(path)
    con.executescript("""
        create table model_usage (session_id text, turn_id text, query_source text, started_at int,
            duration_ms int, time_to_first_token_ms int, tool_call_count int, input_tokens int,
            output_tokens int, cache_read_input_tokens int);
        create table tool_usage (session_id text, turn_id text, tool_call_id text, tool_name text,
            started_at int, completed_at int, duration_ms int, output_bytes int, exit_code int,
            error_type text);
        create table turn_usage (session_id text, turn_id text, started_at int, completed_at int);
        create table part (session_id text, time_updated int, data text);
        create table session (id text, directory text, time_updated int);
    """)
    con.executemany("insert into model_usage values (?,?,?,?,?,?,?,?,?,?)", [
        ("s1", "t1", "main_turn", 1_000, 200_000, 5_000, 1, 100_000, 800, 90_000),
        ("s1", "t1", "main_turn", 300_000, 300_000, 0, 2, 227_000, 1_200, 220_000),
        ("s1", "t1", "session_title", 1_500, 4_000, 0, 0, 271, 10, 0),
        ("s2", "t9", "main_turn", 1_000, 10_000, 0, 1, 50_000, 100, 0),
    ])
    con.executemany("insert into tool_usage values (?,?,?,?,?,?,?,?,?,?)", [
        ("s1", "t1", "c1", "Bash", 210_000, 270_000, 60_000, 2_048, 0, None),
        ("s1", "t1", "c2", "Read", 230_000, 290_000, 60_000, 4_096, 0, None),   # 与 c1 并行
    ])
    con.execute("insert into turn_usage values ('s1', 't1', 0, 600000)")
    con.commit()
    con.close()
    return path


@pytest.fixture()
def sync(tmp_path, monkeypatch):
    module = _load("context_sync_under_test", CONTEXT_SYNC)
    monkeypatch.setattr(module, "AGENTS", tmp_path / "AGENTS.md")
    monkeypatch.setattr(module, "EFF_LOG", tmp_path / "efficiency-log.md")
    monkeypatch.setattr(module, "CLAIMS", tmp_path / "session-claims.md")
    monkeypatch.setattr(module, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(module, "USAGE_DB", tmp_path / "missing.sqlite")
    (tmp_path / "AGENTS.md").write_text("# 规则\n- 规则 A\n- 规则 B\n", encoding="utf-8")
    (tmp_path / "efficiency-log.md").write_text(EFF_LOG, encoding="utf-8")
    return module


@pytest.fixture()
def stats(tmp_path, monkeypatch):
    module = _load("session_stats", SCRIPTS / "session_stats.py")
    monkeypatch.setitem(sys.modules, "session_stats", module)
    monkeypatch.setattr(module, "DB", _usage_db(tmp_path / "usage.sqlite"))
    return module


# ---------------------------------------------------------------- context_sync

def test_session_start_injects_open_items_and_records_baseline(sync):
    text = sync._on_session_start({"source": "startup"}, "s1")
    assert "全量回归跑 3 次" in text and "管道 `a | b` 截断" in text
    assert "汇总行被吞" not in text
    assert "已关账带说明" not in text
    assert sync._snapshot_path("s1").exists()


def test_agents_change_is_pushed_once(sync):
    sync._on_session_start({"source": "startup"}, "s1")
    sync.AGENTS.write_text("# 规则\n- 规则 A（修订）\n- 规则 B\n- 规则 C\n", encoding="utf-8")
    text = sync._on_check({}, "s1")
    assert "-- 规则 A" in text and "+- 规则 A（修订）" in text and "+- 规则 C" in text
    assert sync._on_check({}, "s1") == ""


def test_session_without_snapshot_gets_silent_baseline(sync):
    assert sync._on_check({}, "late") == ""
    sync.AGENTS.write_text("# 规则\n- 规则 D\n", encoding="utf-8")
    assert "+- 规则 D" in sync._on_check({}, "late")


def test_resume_reports_changes_since_baseline(sync):
    sync._on_session_start({"source": "startup"}, "s1")
    sync.AGENTS.write_text("# 规则\n- 规则 A\n", encoding="utf-8")
    text = sync._on_session_start({"source": "resume"}, "s1")
    assert "-- 规则 B" in text and "未结项" in text


def test_long_diff_is_truncated(sync):
    sync._on_session_start({"source": "startup"}, "s1")
    sync.AGENTS.write_text("\n".join(f"- 行 {i}" for i in range(300)), encoding="utf-8")
    text = sync._on_check({}, "s1")
    assert "其余" in text
    assert len(text.splitlines()) <= sync.MAX_DIFF_LINES + 2


def test_session_start_injects_active_claims_and_baselines(sync, tmp_path, monkeypatch):
    claims = tmp_path / "session-claims.md"
    claims.write_text(CLAIMS_MD, encoding="utf-8")
    monkeypatch.setattr(sync, "CLAIMS", claims)
    text = sync._on_session_start({"source": "startup"}, "s1")
    assert "会话 A 改功能引擎" in text and "web-ui/mockups" in text
    assert "旧任务" not in text                     # 「## 已释放」段不注入
    assert sync._snapshot_path("s1", "claims").exists()


def test_claims_change_is_pushed_once(sync, tmp_path, monkeypatch):
    claims = tmp_path / "session-claims.md"
    claims.write_text(CLAIMS_MD, encoding="utf-8")
    monkeypatch.setattr(sync, "CLAIMS", claims)
    sync._on_session_start({"source": "startup"}, "s1")
    claims.write_text(CLAIMS_MD.replace(
        "- 2026-11-12 10:05",
        "- 2026-11-12 11:00｜会话 C 排队等 aite/functional｜docs｜13:00\n- 2026-11-12 10:05",
    ), encoding="utf-8")
    text = sync._on_check({}, "s1")
    assert "会话 C" in text                          # 并行会话新增认领 → 推送
    assert sync._on_check({}, "s1") == ""            # 每处变更只推一次
    claims.write_text(CLAIMS_MD, encoding="utf-8")   # 收尾释放也是变更，仍会推
    assert "在认领" in sync._on_check({}, "s1")


def test_absent_or_empty_claims_are_silent(sync, tmp_path, monkeypatch):
    monkeypatch.setattr(sync, "CLAIMS", tmp_path / "absent.md")
    assert "在认领" not in sync._on_session_start({"source": "startup"}, "s1")
    empty = tmp_path / "empty.md"
    empty.write_text("# 多会话认领\n\n## 在认领\n\n（当前无）\n", encoding="utf-8")
    monkeypatch.setattr(sync, "CLAIMS", empty)
    assert sync._claims_delta("s1") == ""            # 空表既不注入也不推


def test_context_warning_follows_last_main_request(sync, tmp_path, monkeypatch):
    monkeypatch.setattr(sync, "USAGE_DB", _usage_db(tmp_path / "usage.sqlite"))
    assert "227K" in sync._context_warning("s1")          # 最后一次主请求 227K，标题请求不算
    assert sync._context_warning("s2") == ""                # 50K 低于阈值
    assert sync._context_warning("unknown") == ""


def test_context_warning_without_usage_db_is_silent(sync):
    assert sync._context_warning("s1") == ""


def _run_hook(script: Path, raw: bytes, tmp_path: Path, *flags: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "TEMP": str(tmp_path), "TMP": str(tmp_path)}   # 快照写进 tmp_path
    return subprocess.run([sys.executable, *flags, str(script)], input=raw, capture_output=True,
                          env=env, timeout=60)


@pytest.mark.parametrize("raw", [b"", b"not json", b"[1, 2]", b"\xff\xfe garbage",
                                 b'{"hook_event_name": "Stop", "session_id": "s1"}',
                                 b'{"hook_event_name": "PostToolUse"}'])
def test_context_sync_cli_fails_open_silently(raw, tmp_path):
    done = _run_hook(CONTEXT_SYNC, raw, tmp_path, "-I", "-S")
    assert done.returncode == 0 and done.stdout == b"" and done.stderr == b""


def test_context_sync_cli_output_matches_zcode_schema(tmp_path):
    payload = {"hook_event_name": "SessionStart", "session_id": "schema-check", "source": "startup"}
    done = _run_hook(CONTEXT_SYNC, json.dumps(payload).encode(), tmp_path, "-I", "-S")
    assert done.returncode == 0
    if done.stdout:   # 仓库账本无未结项时合法地不输出
        body = json.loads(done.stdout.decode("ascii"))
        assert set(body) == {"hookSpecificOutput"}
        assert set(body["hookSpecificOutput"]) == {"hookEventName", "additionalContext"}
        assert body["hookSpecificOutput"]["hookEventName"] == "SessionStart"


# ---------------------------------------------------------------- session_stats

def test_brief_splits_wall_time_and_counts_only_main_requests(stats):
    turn = stats.load_turns(stats.connect(), "s1")[-1]
    line = stats.brief(turn)
    assert "耗时 10.0 分" in line and "模型 8.3 分" in line
    assert "工具 1.3 分" in line              # 两次并行调用按区间合并：210s→290s
    assert "模型请求 2 次" in line and "只发 1 个工具调用的 1 次" in line
    assert "100.0K→227.0K" in line


def test_union_ms_merges_overlaps():
    module = _load("session_stats_union", SCRIPTS / "session_stats.py")
    assert module.union_ms([(0, 10), (5, 20), (30, 40)]) == 30
    assert module.union_ms([]) == 0


def test_hook_status_matches_grant_by_event_command_and_matcher():
    status = _load("hook_status_under_test", SCRIPTS / "hooks" / "hook_status.py")
    decl = {"event": "PostToolUse", "matcher": "^(Bash)$", "display": "py -I -S hooks/context_sync.py"}
    grant = {"decision": "trusted", "eventAtGrant": "PostToolUse", "matcherAtGrant": "^(Bash)$",
             "displayCommandAtGrant": "py -I -S hooks/context_sync.py"}
    assert status.grant_of(decl, [grant]) is grant
    assert status.grant_of({**decl, "matcher": None}, [grant]) is None
    assert status.grant_of({**decl, "display": "py hooks/context_sync.py"}, [grant]) is None
    assert status.grant_of(decl, [{**grant, "decision": "denied"}]) is None
    stop = {"event": "Stop", "matcher": None, "display": "py hooks/stop_efficiency_gate.py"}
    assert status.grant_of(stop, [{**grant, "eventAtGrant": "Stop", "matcherAtGrant": None,
                                   "displayCommandAtGrant": stop["display"]}])
    return status


# ---------------------------------------------------------------- stop_efficiency_gate

def test_quantified_requires_duration_and_tokens_in_last_review():
    gate = _load("stop_gate_under_test", STOP_GATE)
    assert gate._quantified("### 效率复盘\n- 消耗：耗时 9.2 分；token 输入 1.9M")
    assert not gate._quantified("### 效率复盘\n- 消耗：很多次调用")
    assert not gate._quantified("### 效率复盘\n- 消耗：耗时 9 分")
    assert not gate._quantified("上一轮耗时 5 分 token 1M\n### 效率复盘\n- 消耗：未统计")


def test_stats_line_comes_from_session_stats(stats):
    gate = _load("stop_gate_stats", STOP_GATE)
    line = gate._stats_line("s1", "t1")
    assert line.startswith("耗时 10.0 分") and "token" in line
    assert gate._stats_line(None, None) == ""


def _gate(payload: dict, tmp_path: Path) -> subprocess.CompletedProcess:
    return _run_hook(STOP_GATE, json.dumps(payload, ensure_ascii=False).encode("utf-8"), tmp_path)


QUANTIFIED = "### 效率复盘\n- **消耗**：耗时 9.2 分；token 输入 1.9M\n<!--eff-review-->"


@pytest.mark.parametrize(("payload", "code", "needle"), [
    ({"stopHookActive": True, "toolCallCount": 9, "responseText": "无"}, 0, None),
    ({"toolCallCount": 2, "responseText": "简单问答"}, 0, None),
    ({"toolCallCount": 9, "responseText": QUANTIFIED}, 0, None),
    ({"toolCallCount": 9, "responseText": "做完了"}, 2, "效率复盘缺失"),
    ({"toolCallCount": 9, "responseText": "### 效率复盘\n- 消耗：多次\n<!--eff-review-->"}, 2, "没有量化"),
])
def test_stop_gate_cli(payload, code, needle, tmp_path):
    done = _gate({"hook_event_name": "Stop", "session_id": "sess_test_nonexistent", **payload}, tmp_path)
    assert done.returncode == code
    if needle:
        message = done.stderr.decode("utf-8")
        assert needle in message and "session_stats.py" in message   # 取不到实测数字时给出取数命令


# ---------------------------------------------------------------- stop_commit_gate

def _git_repo(base: Path) -> Path:
    repo = base / "repo"
    (repo / "aite" / "web").mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    (repo / "aite" / "web" / "x.py").write_text("x", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qm", "init"], cwd=repo, check=True)
    return repo


def test_commit_gate_stale_detection_excludes_ledger_noise(tmp_path, monkeypatch):
    gate = _load("commit_gate_under_test", SCRIPTS / "hooks" / "stop_commit_gate.py")
    repo = _git_repo(tmp_path)
    past = time.time() - 3600
    for rel in ("aite/web/x.py", "docs/session-claims.md"):   # 一老一「噪音」
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("changed", encoding="utf-8")
        os.utime(path, (past, past))
    monkeypatch.setattr(gate, "PROJECT_DIR", repo)
    entries = gate._dirty_entries()
    assert len(entries) == 2
    stale = gate._oldest_age_minutes(entries)                  # 噪音不计积压
    assert stale is not None and stale[0] >= 50 and stale[1] == "aite/web/x.py"
    fresh = repo / "aite" / "web" / "y.py"                     # 新改动不影响「最老」判定
    fresh.write_text("new", encoding="utf-8")
    assert gate._oldest_age_minutes(gate._dirty_entries())[0] >= 50
    assert "aite/web" in gate._grouped(entries)


def test_commit_gate_cli_blocks_stale_and_defer_marker_releases(tmp_path):
    repo = _git_repo(tmp_path)
    stale_file = repo / "aite" / "web" / "x.py"
    stale_file.write_text("y", encoding="utf-8")
    past = time.time() - 7200
    os.utime(stale_file, (past, past))
    env = {**os.environ, "AITE_HOOK_PROJECT_DIR": str(repo)}
    script = str(SCRIPTS / "hooks" / "stop_commit_gate.py")
    plain = subprocess.run([sys.executable, script], timeout=60, capture_output=True, env=env,
                           input=json.dumps({"responseText": "做了"},
                                            ensure_ascii=False).encode("utf-8"))
    deferred = subprocess.run([sys.executable, script], timeout=60, capture_output=True, env=env,
                              input=b'{"responseText": "defer <!--commit-deferred: \xe5\x9c\xa8\xe9\x80\x94-->"}')
    assert plain.returncode == 2 and "提交门禁" in plain.stderr.decode("utf-8")
    assert deferred.returncode == 0
    claims = repo / "docs" / "session-claims.md"
    claims.parent.mkdir(parents=True, exist_ok=True)
    claims.write_text("## 在认领\n\n- t｜s｜aite/web/x.py｜later\n", encoding="utf-8")
    claimed = subprocess.run([sys.executable, script], timeout=60, capture_output=True, env=env,
                             input=json.dumps({"responseText": "做了"},
                                              ensure_ascii=False).encode("utf-8"))
    assert claimed.returncode == 0                     # 认领范围内的在写文件豁免


def test_commit_gate_claim_scope_parsing_and_exclusion(tmp_path, monkeypatch):
    gate = _load("commit_gate_claims", SCRIPTS / "hooks" / "stop_commit_gate.py")
    assert gate._expand_braces("aite/storage/{models,repositories}.py") == [
        "aite/storage/models.py", "aite/storage/repositories.py"]
    repo = _git_repo(tmp_path)
    past = time.time() - 3600
    for rel in ("aite/core/taskkit.py", "aite/web/x.py"):      # 一在写（认领中）一积压
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("wip", encoding="utf-8")
        os.utime(path, (past, past))
    claims = repo / "docs" / "session-claims.md"
    claims.parent.mkdir(parents=True, exist_ok=True)
    claims.write_text(
        "# 认领\n\n## 在认领\n\n"
        "- 2026-11-12 10:00｜会话 A 改任务历史｜aite/core/taskkit.py + "
        "aite/storage/{models,repositories}.py｜预计 1-2 天\n\n## 已释放\n\n",
        encoding="utf-8")
    monkeypatch.setattr(gate, "PROJECT_DIR", repo)
    monkeypatch.setattr(gate, "CLAIMS", claims)
    prefixes = gate._claimed_prefixes()
    assert "aite/storage/models.py" in prefixes                # 花括号展开进认领范围
    pool = [e for e in gate._dirty_entries()
            if e[1] not in gate.NOISE and not gate._is_claimed(e[1], prefixes)]
    assert [e[1] for e in pool] == ["aite/web/x.py"]           # 在写文件排除，剩积压可催
    stale = gate._oldest_age_minutes(pool)
    assert stale is not None and stale[1] == "aite/web/x.py"


# ---------------------------------------------------------------- stop_teach_gate

def test_teach_gate_detects_code_edits_and_delivery_items():
    gate = _load("teach_gate_under_test", SCRIPTS / "hooks" / "stop_teach_gate.py")
    raw = ('{"role":"user","content":"改一下"}\n'
           '{"name":"Edit","input":{"file_path":"D:/proj/aite/web/app.py","old_string":"a"}}\n'
           '{"name":"Write","input":{"file_path":"D:/proj/docs/note.md"}}')
    assert gate._code_files(raw) == ["D:/proj/aite/web/app.py"]   # .md 不算改码
    assert gate.DELIVERY == ("【契约摘要】", "【风险点位清单】", "【数据流简述】", "【依赖变更】")
    full = "x".join(gate.DELIVERY)
    assert gate._missing_items(full) == []
    assert gate._missing_items("【契约摘要】和【数据流简述】") == ["【风险点位清单】", "【依赖变更】"]


def test_teach_gate_cli_requires_delivery_items(tmp_path):
    transcript = tmp_path / "t.jsonl"
    transcript.write_text('{"role":"user","content":"go"}\n'
                          '{"name":"Write","input":{"file_path":"a.py"}}', encoding="utf-8")
    base = {"hook_event_name": "Stop", "transcript_path": str(transcript)}
    plain = _run_hook(SCRIPTS / "hooks" / "stop_teach_gate.py",
                      json.dumps({**base, "responseText": "done"}).encode("utf-8"), tmp_path)
    assert plain.returncode == 2 and "契约摘要" in plain.stderr.decode("utf-8")
    taught = _run_hook(SCRIPTS / "hooks" / "stop_teach_gate.py",
                       json.dumps({**base, "responseText": " ".join(
                           ("【契约摘要】", "【风险点位清单】", "【数据流简述】", "【依赖变更】"))}).encode("utf-8"),
                       tmp_path)
    assert taught.returncode == 0
    partial = _run_hook(SCRIPTS / "hooks" / "stop_teach_gate.py",
                        json.dumps({**base, "responseText": "【契约摘要】 only"}).encode("utf-8"),
                        tmp_path)
    assert partial.returncode == 2
    skipped = _run_hook(SCRIPTS / "hooks" / "stop_teach_gate.py",
                        json.dumps({**base, "responseText": "typo <!--teach-skip-->"}).encode("utf-8"),
                        tmp_path)
    assert skipped.returncode == 0
