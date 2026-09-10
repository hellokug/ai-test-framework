"""SQLAlchemy 2.0 table definitions - portable across SQLite / MySQL / PostgreSQL.

Rules (see docs/architecture.md):
- no SQLite-specific column types; JSON via sa.JSON (maps to dialect-native)
- UUID primary keys via GUID TypeDecorator (CHAR(36) storage)
- timestamps stored timezone-aware (naive UTC on SQLite)
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import sqlalchemy as sa
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from aite.core.types import utcnow


class Base(DeclarativeBase):
    pass


class GUID(sa.TypeDecorator):
    """Platform-independent UUID: CHAR(36) everywhere, canonical dashed str in Python."""
    impl = sa.CHAR(36)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return value
        return str(value)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return str(value) if not isinstance(value, uuid.UUID) else str(value)


def new_id() -> str:
    return str(uuid.uuid4())


class ModelConfigRow(Base):
    __tablename__ = "model_config"
    id: Mapped[str] = mapped_column(GUID(), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(sa.String(255), unique=True)
    type: Mapped[str] = mapped_column(sa.String(64), default="openai_compatible")
    base_url: Mapped[str | None] = mapped_column(sa.String(1024), nullable=True)
    model: Mapped[str | None] = mapped_column(sa.String(255), nullable=True)
    api_key_env: Mapped[str | None] = mapped_column(sa.String(255), nullable=True)
    params: Mapped[dict] = mapped_column(sa.JSON(), default=dict)
    notes: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(), default=utcnow)


class TestSuiteRow(Base):
    __tablename__ = "test_suite"
    id: Mapped[str] = mapped_column(GUID(), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(sa.String(512), unique=True)
    description: Mapped[str] = mapped_column(sa.Text(), default="")
    config: Mapped[dict] = mapped_column(sa.JSON(), default=dict)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(), default=utcnow, onupdate=utcnow
    )


class TestCaseRow(Base):
    __tablename__ = "test_case"
    __table_args__ = (sa.UniqueConstraint("suite_id", "external_key", name="uq_case_suite_key"),)
    id: Mapped[int] = mapped_column(sa.Integer, primary_key=True, autoincrement=True)
    suite_id: Mapped[str] = mapped_column(GUID(), sa.ForeignKey("test_suite.id"), index=True)
    external_key: Mapped[str] = mapped_column(sa.String(512))     # case.id (+variant)
    input: Mapped[dict | str] = mapped_column(sa.JSON())
    expected: Mapped[dict | str | None] = mapped_column(sa.JSON(), nullable=True)
    metadata_: Mapped[dict] = mapped_column("metadata", sa.JSON(), default=dict)
    retrieval_context: Mapped[list | None] = mapped_column(sa.JSON(), nullable=True)
    variant: Mapped[str] = mapped_column(sa.String(64), default="base")
    variant_of: Mapped[str | None] = mapped_column(sa.String(512), nullable=True)
    dataset: Mapped[str | None] = mapped_column(sa.String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(), default=utcnow)


class TestRunRow(Base):
    __tablename__ = "test_run"
    id: Mapped[str] = mapped_column(GUID(), primary_key=True, default=new_id)
    run_name: Mapped[str] = mapped_column(sa.String(512), index=True)
    suite_id: Mapped[str | None] = mapped_column(GUID(), sa.ForeignKey("test_suite.id"), nullable=True)
    status: Mapped[str] = mapped_column(sa.String(32), default="running", index=True)
    overall_score: Mapped[float | None] = mapped_column(sa.Float(), nullable=True)
    config_snapshot: Mapped[dict] = mapped_column(sa.JSON(), default=dict)
    total_cases: Mapped[int] = mapped_column(sa.Integer, default=0)
    error_cases: Mapped[int] = mapped_column(sa.Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(sa.Float(), default=0.0)
    started_at: Mapped[datetime] = mapped_column(sa.DateTime(), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(sa.DateTime(), nullable=True)
    error: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    # 工具域元数据（reports/work_dir/failures 等）；自研引擎 details 不入库无膨胀
    details: Mapped[dict | None] = mapped_column(sa.JSON(), nullable=True)


class TestCaseResultRow(Base):
    __tablename__ = "test_case_result"
    id: Mapped[int] = mapped_column(sa.Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(GUID(), sa.ForeignKey("test_run.id"), index=True)
    case_id: Mapped[int] = mapped_column(sa.Integer, sa.ForeignKey("test_case.id"))
    external_key: Mapped[str] = mapped_column(sa.String(512))
    variant: Mapped[str] = mapped_column(sa.String(64), default="base")
    variant_of: Mapped[str | None] = mapped_column(sa.String(512), nullable=True)
    dataset: Mapped[str | None] = mapped_column(sa.String(512), nullable=True)
    output: Mapped[dict | str | None] = mapped_column(sa.JSON(), nullable=True)
    error: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    usage: Mapped[dict | None] = mapped_column(sa.JSON(), nullable=True)
    timings: Mapped[dict | None] = mapped_column(sa.JSON(), nullable=True)
    cost_usd: Mapped[float | None] = mapped_column(sa.Float(), nullable=True)
    trace: Mapped[dict | None] = mapped_column(sa.JSON(), nullable=True)
    raw: Mapped[dict | None] = mapped_column(sa.JSON(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(), default=utcnow)


class MetricResultRow(Base):
    __tablename__ = "metric_result"
    id: Mapped[int] = mapped_column(sa.Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(GUID(), sa.ForeignKey("test_run.id"), index=True)
    case_result_id: Mapped[int] = mapped_column(sa.Integer, sa.ForeignKey("test_case_result.id"), index=True)
    metric: Mapped[str] = mapped_column(sa.String(255), index=True)
    value: Mapped[float | None] = mapped_column(sa.Float(), nullable=True)
    passed: Mapped[bool | None] = mapped_column(sa.Boolean(), nullable=True)
    raw: Mapped[dict | None] = mapped_column(sa.JSON(), nullable=True)
    error: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    source: Mapped[str] = mapped_column(sa.String(32), default="metric")   # metric | judge
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(), default=utcnow)


class RunMetricSummaryRow(Base):
    __tablename__ = "run_metric_summary"
    id: Mapped[int] = mapped_column(sa.Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(GUID(), sa.ForeignKey("test_run.id"), index=True)
    run_name: Mapped[str] = mapped_column(sa.String(512), index=True)
    metric: Mapped[str] = mapped_column(sa.String(255), index=True)
    dataset: Mapped[str | None] = mapped_column(sa.String(512), nullable=True)
    group: Mapped[str | None] = mapped_column(sa.String(255), nullable=True)
    aggregation: Mapped[str] = mapped_column(sa.String(16), default="mean")
    value: Mapped[float | None] = mapped_column(sa.Float(), nullable=True)
    threshold: Mapped[float | None] = mapped_column(sa.Float(), nullable=True)
    passed: Mapped[bool | None] = mapped_column(sa.Boolean(), nullable=True)
    count: Mapped[int] = mapped_column(sa.Integer, default=0)
    pass_rate: Mapped[float | None] = mapped_column(sa.Float(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(), default=utcnow)


class ManualEvalTaskRow(Base):
    __tablename__ = "manual_eval_task"
    id: Mapped[str] = mapped_column(GUID(), primary_key=True, default=new_id)
    run_id: Mapped[str | None] = mapped_column(GUID(), sa.ForeignKey("test_run.id"), nullable=True, index=True)
    case_result_id: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    title: Mapped[str] = mapped_column(sa.String(512), default="")
    case_input: Mapped[dict | str | None] = mapped_column(sa.JSON(), nullable=True)
    expected: Mapped[dict | str | None] = mapped_column(sa.JSON(), nullable=True)
    output: Mapped[dict | str | None] = mapped_column(sa.JSON(), nullable=True)
    rubric: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    status: Mapped[str] = mapped_column(sa.String(32), default="pending", index=True)  # pending|done
    assignee: Mapped[str | None] = mapped_column(sa.String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(), default=utcnow)


class ManualEvalScoreRow(Base):
    __tablename__ = "manual_eval_score"
    id: Mapped[int] = mapped_column(GUID(), primary_key=True, default=new_id)
    task_id: Mapped[str] = mapped_column(GUID(), sa.ForeignKey("manual_eval_task.id"), index=True)
    score: Mapped[float] = mapped_column(sa.Float())
    passed: Mapped[bool | None] = mapped_column(sa.Boolean(), nullable=True)
    reviewer: Mapped[str] = mapped_column(sa.String(255), default="")
    comment: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(), default=utcnow)


class BgTaskRow(Base):
    __tablename__ = "bg_task"
    id: Mapped[str] = mapped_column(GUID(), primary_key=True, default=new_id)
    kind: Mapped[str] = mapped_column(sa.String(64))              # run_suite | import
    status: Mapped[str] = mapped_column(sa.String(32), default="pending", index=True)
    payload: Mapped[dict] = mapped_column(sa.JSON(), default=dict)
    result: Mapped[dict | None] = mapped_column(sa.JSON(), nullable=True)
    error: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    run_id: Mapped[str | None] = mapped_column(GUID(), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(sa.DateTime(), nullable=True)


class AgentNodeRow(Base):
    """执行机（模块 G）：拉取式 agent 注册/心跳记录。"""

    __tablename__ = "agent_node"
    id: Mapped[str] = mapped_column(GUID(), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(sa.String(128), unique=True)
    labels: Mapped[list] = mapped_column(sa.JSON(), default=list)
    status: Mapped[str] = mapped_column(sa.String(32), default="online", index=True)
    agent_version: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)
    current_task_id: Mapped[str | None] = mapped_column(GUID(), nullable=True)
    last_heartbeat: Mapped[datetime] = mapped_column(sa.DateTime(), default=utcnow)
    registered_at: Mapped[datetime] = mapped_column(sa.DateTime(), default=utcnow)


class AgentTaskRow(Base):
    """派发到执行机的任务队列（一期压测域；回传结果统一入库 test_run）。"""

    __tablename__ = "agent_task"
    id: Mapped[str] = mapped_column(GUID(), primary_key=True, default=new_id)
    node_id: Mapped[str | None] = mapped_column(GUID(), nullable=True, index=True)
    status: Mapped[str] = mapped_column(sa.String(32), default="queued", index=True)
    suite_raw: Mapped[dict] = mapped_column(sa.JSON(), default=dict)
    run_name: Mapped[str] = mapped_column(sa.String(512))
    engine: Mapped[str] = mapped_column(sa.String(64))
    result: Mapped[dict | None] = mapped_column(sa.JSON(), nullable=True)
    error: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(), default=utcnow)
    dispatched_at: Mapped[datetime | None] = mapped_column(sa.DateTime(), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(sa.DateTime(), nullable=True)


def aware(dt: datetime | None) -> datetime | None:
    """SQLite returns naive datetimes; normalize to UTC-aware."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt
