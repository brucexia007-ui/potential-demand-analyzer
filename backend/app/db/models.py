from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
from uuid import uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Computed,
    Date,
    DateTime,
    Enum as SQLEnum,
    ForeignKey,
    Integer,
    LargeBinary,
    Float,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from pgvector.sqlalchemy import VECTOR


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    username: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True, default=None)
    notification_prefs: Mapped[dict | None] = mapped_column(JSONB, nullable=True, default=None)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class Workspace(Base):
    """业务资产的最小隔离边界；首版不引入组织树或复杂 RBAC。"""
    __tablename__ = "workspaces"

    id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    organization_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    business_unit_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="ACTIVE")
    default_model_policy: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class WorkspaceMember(Base):
    """Workspace 与用户的成员关系；角色枚举在服务层统一校验。"""
    __tablename__ = "workspace_members"
    __table_args__ = (
        UniqueConstraint("workspace_id", "user_id", name="uq_workspace_members_workspace_user"),
    )

    id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="OWNER")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="ACTIVE")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class TargetAccount(Base):
    """目标企业主数据；仅 input_name 必填，所有消歧字段均允许为空。"""
    __tablename__ = "target_accounts"
    __table_args__ = (
        Index("ix_target_accounts_workspace_input_name", "workspace_id", "input_name"),
    )

    id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    owner_user_id: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    input_name: Mapped[str] = mapped_column(String(255), nullable=False)
    official_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    website: Mapped[str | None] = mapped_column(Text, nullable=True)
    credit_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    industry: Mapped[str | None] = mapped_column(String(100), nullable=True)
    region: Mapped[str | None] = mapped_column(String(100), nullable=True)
    stock_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    parent_id: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("target_accounts.id", ondelete="SET NULL"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="UNRESOLVED")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class BusinessWebhookDelivery(Base):
    """业务快照外发的确认凭证与审计账本；目标密钥和签名密钥不落库。"""
    __tablename__ = "business_webhook_deliveries"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "idempotency_key",
            name="uq_business_webhook_deliveries_workspace_key",
        ),
        CheckConstraint(
            "status IN ('PREVIEWED', 'CONFIRMED', 'SENDING', 'SUCCEEDED', 'FAILED', 'EXPIRED')",
            name="ck_business_webhook_deliveries_status",
        ),
        CheckConstraint(
            "octet_length(destination_hash) = 32 AND octet_length(payload_hash) = 32",
            name="ck_business_webhook_deliveries_hashes",
        ),
        CheckConstraint(
            "response_digest IS NULL OR octet_length(response_digest) = 32",
            name="ck_business_webhook_deliveries_response_digest",
        ),
        CheckConstraint("attempt_count >= 0", name="ck_business_webhook_deliveries_attempt_count"),
    )

    id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    target_account_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("target_accounts.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    created_by: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    destination_display: Mapped[str] = mapped_column(Text, nullable=False)
    destination_hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    payload_hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="PREVIEWED", index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_digest: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    failure_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class TaskStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class BatchStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    PARTIAL = "PARTIAL"


class LogLevel(str, Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


class DiscoveryResearchPlan(Base):
    """自动商机线索发现的不可变执行计划快照与人工确认凭证。"""
    __tablename__ = "discovery_research_plans"
    __table_args__ = (
        CheckConstraint("depth IN ('quick', 'standard', 'deep')", name="ck_discovery_research_plans_depth"),
        CheckConstraint(
            "status IN ('PREVIEWED', 'CONFIRMED', 'CONSUMED', 'EXPIRED')",
            name="ck_discovery_research_plans_status",
        ),
        CheckConstraint(
            "(status = 'PREVIEWED' AND confirmed_at IS NULL AND consumed_at IS NULL) OR "
            "(status = 'CONFIRMED' AND confirmed_at IS NOT NULL AND consumed_at IS NULL) OR "
            "(status = 'CONSUMED' AND consumed_at IS NOT NULL) OR status = 'EXPIRED'",
            name="ck_discovery_research_plans_lifecycle",
        ),
    )

    id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True)
    target_account_id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("target_accounts.id", ondelete="CASCADE"), nullable=False, index=True)
    capability_profile_id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("capability_profiles.id", ondelete="RESTRICT"), nullable=False, index=True)
    created_by: Mapped[UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    root_skill_name: Mapped[str] = mapped_column(String(128), nullable=False)
    skill_version: Mapped[str] = mapped_column(String(160), nullable=False)
    depth: Mapped[str] = mapped_column(String(16), nullable=False)
    demand_direction: Mapped[str] = mapped_column(String(255), nullable=False)
    requires_confirmation: Mapped[bool] = mapped_column(Boolean, nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PREVIEWED", index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class Task(Base):
    __tablename__ = "tasks"
    __table_args__ = (
        CheckConstraint(
            "desired_state IN ('RUNNING', 'PAUSED', 'CANCELLED')",
            name="ck_tasks_desired_state",
        ),
        CheckConstraint(
            "observed_state IN ('PENDING', 'QUEUED', 'RUNNING', 'PAUSING', 'PAUSED', "
            "'WAITING_FOR_INPUT', 'RECOVERING', 'CANCELLING', 'COMPLETED', 'FAILED', "
            "'CANCELLED', 'PARTIAL')",
            name="ck_tasks_observed_state",
        ),
        CheckConstraint(
            "research_mode IN ('DIRECTED_RESEARCH', 'OPPORTUNITY_DISCOVERY')",
            name="ck_tasks_research_mode",
        ),
        CheckConstraint(
            "research_mode != 'OPPORTUNITY_DISCOVERY' OR capability_profile_id IS NOT NULL",
            name="ck_tasks_discovery_requires_capability_profile",
        ),
    )

    id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    workspace_id: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="SET NULL"), nullable=True, index=True
    )
    target_account_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("target_accounts.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    batch_id: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("batches.id", ondelete="SET NULL"), nullable=True, index=True
    )
    research_brief_id: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("research_briefs.id", ondelete="SET NULL"), nullable=True, unique=True
    )
    company_name: Mapped[str] = mapped_column(String(255), nullable=False)
    demand_direction: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[TaskStatus] = mapped_column(SQLEnum(TaskStatus), default=TaskStatus.PENDING)
    desired_state: Mapped[str] = mapped_column(String(32), nullable=False, default="RUNNING")
    observed_state: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")
    control_version: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    execution_generation: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    active_run_id: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "task_runs.id",
            name="fk_tasks_active_run_id_task_runs",
            ondelete="SET NULL",
            use_alter=True,
        ),
        nullable=True,
    )
    research_mode: Mapped[str] = mapped_column(String(32), nullable=False, default="DIRECTED_RESEARCH")
    capability_profile_id: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("capability_profiles.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    discovery_plan_id: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("discovery_research_plans.id", ondelete="RESTRICT"), nullable=True, unique=True
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    celery_task_id: Mapped[str | None] = mapped_column(String(255), nullable=True)  # WBS-9: Celery 任务 ID
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)


class TaskRun(Base):
    """任务的一次初始或恢复运行记录。"""
    __tablename__ = "task_runs"
    __table_args__ = (
        UniqueConstraint("task_id", "generation", name="uq_task_runs_task_generation"),
        CheckConstraint(
            "status IN ('PENDING', 'QUEUED', 'RUNNING', 'PAUSED', 'COMPLETED', 'FAILED', 'CANCELLED', 'PARTIAL')",
            name="ck_task_runs_status",
        ),
    )

    id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    task_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    generation: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")
    resume_from_run_id: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("task_runs.id", ondelete="SET NULL"), nullable=True
    )
    failure_class: Mapped[str | None] = mapped_column(String(64), nullable=True)
    failure_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class TaskCommand(Base):
    """幂等的任务控制命令账本。"""
    __tablename__ = "task_commands"
    __table_args__ = (
        UniqueConstraint("task_id", "idempotency_key", name="uq_task_commands_task_idempotency"),
        CheckConstraint("command_type IN ('PAUSE', 'RESUME', 'CANCEL')", name="ck_task_commands_type"),
        CheckConstraint("status IN ('PENDING', 'APPLIED', 'REJECTED')", name="ck_task_commands_status"),
    )

    id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    task_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    command_type: Mapped[str] = mapped_column(String(32), nullable=False)
    requested_by: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    requested_control_version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class TaskStageRun(Base):
    """一次 TaskRun 内可独立提交、重试和恢复的最小工作单元。"""
    __tablename__ = "task_stage_runs"
    __table_args__ = (
        UniqueConstraint("run_id", "dimension", "stage", "unit_key", name="uq_task_stage_runs_unit"),
        CheckConstraint(
            "status IN ('PENDING', 'QUEUED', 'RUNNING', 'PAUSED', 'COMPLETED', 'FAILED', 'CANCELLED', 'SKIPPED')",
            name="ck_task_stage_runs_status",
        ),
        CheckConstraint("octet_length(input_hash) = 32", name="ck_task_stage_runs_input_hash_size"),
    )

    id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("task_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    dimension: Mapped[str] = mapped_column(String(100), nullable=False)
    stage: Mapped[str] = mapped_column(String(64), nullable=False)
    unit_key: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")
    input_hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    next_cursor: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lease_epoch: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    checkpoint_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    asset_ref: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class ResearchCandidate(Base):
    """搜索召回候选的可追溯持久化资产，不等同于最终 Evidence。"""
    __tablename__ = "research_candidates"
    __table_args__ = (
        UniqueConstraint("task_id", "dimension", "canonical_url_hash", name="uq_research_candidates_url"),
        UniqueConstraint("task_id", "candidate_id", name="uq_research_candidates_task_candidate_id"),
        CheckConstraint("octet_length(canonical_url_hash) = 32", name="ck_research_candidates_url_hash_size"),
        CheckConstraint(
            "content_hash IS NULL OR octet_length(content_hash) = 32",
            name="ck_research_candidates_content_hash_size",
        ),
    )

    id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    task_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    stage_run_id: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("task_stage_runs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    dimension: Mapped[str] = mapped_column(String(100), nullable=False)
    candidate_id: Mapped[str] = mapped_column(String(128), nullable=False)
    canonical_url: Mapped[str] = mapped_column(Text, nullable=False)
    canonical_url_hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    title_fingerprint: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    snippet: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_provider: Mapped[str | None] = mapped_column(String(100), nullable=True)
    source_query: Mapped[str | None] = mapped_column(Text, nullable=True)
    original_rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    fetch_status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")
    content_hash: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    meta_data: Mapped[dict] = mapped_column("metadata", JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class ExternalCallAttempt(Base):
    """一次外部调用的元数据和费用审计，不保存完整 Prompt 或模型响应。"""
    __tablename__ = "external_call_attempts"
    __table_args__ = (
        CheckConstraint(
            "status IN ('STARTED', 'SUCCEEDED', 'FAILED', 'TIMED_OUT', 'UNKNOWN')",
            name="ck_external_call_attempts_status",
        ),
        CheckConstraint(
            "billing_outcome IN ('PENDING', 'SETTLED', 'UNKNOWN', 'NOT_BILLABLE')",
            name="ck_external_call_attempts_billing_outcome",
        ),
        CheckConstraint("octet_length(request_hash) = 32", name="ck_external_call_attempts_request_hash_size"),
        CheckConstraint(
            "response_hash IS NULL OR octet_length(response_hash) = 32",
            name="ck_external_call_attempts_response_hash_size",
        ),
    )

    id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    task_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    run_id: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("task_runs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    stage_run_id: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("task_stage_runs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    provider: Mapped[str] = mapped_column(String(100), nullable=False)
    model: Mapped[str | None] = mapped_column(String(160), nullable=True)
    operation: Mapped[str] = mapped_column(String(64), nullable=False)
    request_hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    response_hash: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    raw_response_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="STARTED")
    billing_outcome: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_amount: Mapped[float | None] = mapped_column(Numeric(18, 6), nullable=True)
    cost_currency: Mapped[str | None] = mapped_column(String(8), nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_class: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class ExternalCallIdempotencyKey(Base):
    """不分区的全局调用幂等注册表，保证同一物理请求只登记一次。"""
    __tablename__ = "external_call_idempotency_keys"

    idempotency_key: Mapped[str] = mapped_column(String(128), primary_key=True)
    attempt_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("external_call_attempts.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class TaskBudgetLedgerEntry(Base):
    """预算预留、结算和退还的不可变账本行；是否告警不影响调用执行。"""
    __tablename__ = "task_budget_ledger_entries"
    __table_args__ = (
        UniqueConstraint("task_id", "idempotency_key", name="uq_task_budget_ledger_idempotency"),
        CheckConstraint(
            "entry_type IN ('RESERVATION', 'SETTLEMENT', 'REFUND', 'ADJUSTMENT')",
            name="ck_task_budget_ledger_entry_type",
        ),
        CheckConstraint("amount >= 0", name="ck_task_budget_ledger_amount_non_negative"),
    )

    id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    task_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    run_id: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("task_runs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    stage_run_id: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("task_stage_runs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    external_call_attempt_id: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("external_call_attempts.id", ondelete="SET NULL"), nullable=True, index=True
    )
    dimension: Mapped[str | None] = mapped_column(String(100), nullable=True)
    entry_type: Mapped[str] = mapped_column(String(32), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    amount: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="USD")
    token_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    meta_data: Mapped[dict] = mapped_column("metadata", JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class ReportEvidenceReference(Base):
    """报告与 Evidence 的显式引用关系，供完成门和审计查询使用。"""
    __tablename__ = "report_evidence_references"
    __table_args__ = (
        UniqueConstraint("report_id", "citation_key", name="uq_report_evidence_references_citation"),
    )

    id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    report_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("reports.id", ondelete="CASCADE"), nullable=False, index=True
    )
    evidence_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("evidences.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    citation_key: Mapped[str] = mapped_column(String(128), nullable=False)
    section_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    locator: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class EvidenceAuditReuseKey(Base):
    """相同内容、策略和模型版本下的审计结果复用索引。"""
    __tablename__ = "evidence_audit_reuse_keys"
    __table_args__ = (
        UniqueConstraint(
            "content_hash", "audit_policy_version", "model_version",
            name="uq_evidence_audit_reuse_key",
        ),
        CheckConstraint("octet_length(content_hash) = 32", name="ck_evidence_audit_reuse_hash_size"),
    )

    id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    evidence_audit_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("evidence_audits.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    content_hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    audit_policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    model_version: Mapped[str] = mapped_column(String(160), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class TaskEvent(Base):
    """可重放的任务事件流；sequence 由后续 Repository 在同一任务事务内原子分配。"""
    __tablename__ = "task_events"
    __table_args__ = (
        UniqueConstraint("task_id", "sequence", name="uq_task_events_task_sequence"),
    )

    id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    task_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    run_id: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("task_runs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    stage_run_id: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("task_stage_runs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class OutboxEvent(Base):
    """与业务状态同事务写入的消息记录；Relay 成功发布后才写入 published_at。"""
    __tablename__ = "outbox_events"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_outbox_events_idempotency"),
        Index(
            "ix_outbox_events_unpublished",
            "available_at",
            "created_at",
            "id",
            postgresql_where=text("published_at IS NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    task_id: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=True, index=True
    )
    run_id: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("task_runs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    stage_run_id: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("task_stage_runs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    topic: Mapped[str] = mapped_column(String(128), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    claimed_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    delivery_attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class ExecutionRetentionPolicy(Base):
    """执行数据的默认清理策略；首版普通表达到容量阈值后再评审分区。"""
    __tablename__ = "execution_retention_policies"
    __table_args__ = (
        CheckConstraint("retention_days > 0", name="ck_execution_retention_policies_days_positive"),
    )

    resource_name: Mapped[str] = mapped_column(String(64), primary_key=True)
    retention_days: Mapped[int] = mapped_column(Integer, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class Batch(Base):
    __tablename__ = "batches"
    __table_args__ = (
        CheckConstraint(
            "research_mode IN ('DIRECTED_RESEARCH', 'OPPORTUNITY_DISCOVERY')",
            name="ck_batches_research_mode",
        ),
        CheckConstraint(
            "research_mode != 'OPPORTUNITY_DISCOVERY' OR capability_profile_id IS NOT NULL",
            name="ck_batches_discovery_requires_capability_profile",
        ),
    )

    id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    workspace_id: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="SET NULL"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[BatchStatus] = mapped_column(SQLEnum(BatchStatus), default=BatchStatus.PENDING)
    root_skill_name: Mapped[str] = mapped_column(String(128), nullable=False, default="pilot-opportunity")
    research_mode: Mapped[str] = mapped_column(String(32), nullable=False, default="DIRECTED_RESEARCH")
    capability_profile_id: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("capability_profiles.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    harness_config: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    total_tasks: Mapped[int] = mapped_column(Integer, default=0)
    completed_tasks: Mapped[int] = mapped_column(Integer, default=0)
    failed_tasks: Mapped[int] = mapped_column(Integer, default=0)
    cancelled_tasks: Mapped[int] = mapped_column(Integer, default=0)
    paused: Mapped[bool] = mapped_column(Boolean, default=False)  # WBS-9: 暂停调度
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class Report(Base):
    __tablename__ = "reports"

    id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="SET NULL"), nullable=True, index=True
    )
    task_id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tasks.id"), nullable=False)
    content_md: Mapped[str] = mapped_column(Text, nullable=False)
    raw_data: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    evidence_index: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    current_version_id: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "report_versions.id",
            name="fk_reports_current_version_id_report_versions",
            ondelete="SET NULL",
            use_alter=True,
        ),
        nullable=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class ResearchRun(Base):
    """可复用的研究运行；主任务与追问/补充研究均以此作为业务账本。"""
    __tablename__ = "research_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING', 'RUNNING', 'WAITING_FOR_INPUT', 'COMPLETED', 'FAILED', 'CANCELLED', 'PARTIAL')",
            name="ck_research_runs_status",
        ),
        CheckConstraint(
            "run_type IN ('INITIAL', 'FOLLOW_UP', 'REVALIDATION')",
            name="ck_research_runs_run_type",
        ),
    )

    id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    task_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    task_run_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("task_runs.id", ondelete="CASCADE"), nullable=False, unique=True, index=True
    )
    parent_run_id: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("research_runs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    run_type: Mapped[str] = mapped_column(String(32), nullable=False, default="INITIAL")
    skill_version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")
    budget: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    input_context: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class ResearchPlanSnapshot(Base):
    """Research Director 批准的不可变目标树与任务计划版本。"""
    __tablename__ = "research_plan_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "plan_version",
            name="uq_research_plan_snapshots_run_version",
        ),
        CheckConstraint(
            "status IN ('APPROVED', 'SUPERSEDED', 'COMPLETED', 'FAILED')",
            name="ck_research_plan_snapshots_status",
        ),
    )

    id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("research_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    planning_stage_run_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("task_stage_runs.id", ondelete="RESTRICT"), nullable=False, unique=True
    )
    schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
    plan_version: Mapped[int] = mapped_column(Integer, nullable=False)
    primary_goal_key: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="APPROVED")
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    validation: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class ResearchQuestion(Base):
    """研究问题及其递归子问题。"""
    __tablename__ = "research_questions"

    id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("research_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    plan_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("research_plan_snapshots.id", ondelete="CASCADE"), nullable=False, index=True
    )
    goal_key: Mapped[str] = mapped_column(String(64), nullable=False)
    parent_id: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("research_questions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    question: Mapped[str] = mapped_column(Text, nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    priority: Mapped[str] = mapped_column(String(16), nullable=False)
    required: Mapped[bool] = mapped_column(Boolean, nullable=False)
    success_criteria: Mapped[list] = mapped_column(JSONB, nullable=False)
    stop_criteria: Mapped[list] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")
    sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)

    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING', 'RUNNING', 'ANSWERED', 'BLOCKED', 'CANCELLED')",
            name="ck_research_questions_status",
        ),
        UniqueConstraint(
            "plan_id",
            "goal_key",
            name="uq_research_questions_plan_goal_key",
        ),
    )


class PlannedResearchTask(Base):
    """LLM 计划中的可执行研究任务及其耐久调度状态。"""
    __tablename__ = "planned_research_tasks"
    __table_args__ = (
        UniqueConstraint(
            "plan_id",
            "task_key",
            name="uq_planned_research_tasks_plan_task_key",
        ),
        CheckConstraint(
            "status IN ('PENDING', 'MATERIALIZED', 'RUNNING', 'COMPLETED', 'BLOCKED', 'CANCELLED')",
            name="ck_planned_research_tasks_status",
        ),
    )

    id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    plan_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("research_plan_snapshots.id", ondelete="CASCADE"), nullable=False, index=True
    )
    task_key: Mapped[str] = mapped_column(String(64), nullable=False)
    goal_keys: Mapped[list] = mapped_column(JSONB, nullable=False)
    task_type: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    skill_name: Mapped[str] = mapped_column(String(100), nullable=False)
    tool_name: Mapped[str] = mapped_column(String(100), nullable=False)
    evidence_usage: Mapped[str] = mapped_column(String(32), nullable=False)
    search_strategy: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    expected_evidence: Mapped[list] = mapped_column(JSONB, nullable=False)
    dependencies: Mapped[list] = mapped_column(JSONB, nullable=False)
    priority: Mapped[str] = mapped_column(String(16), nullable=False)
    budget: Mapped[dict] = mapped_column(JSONB, nullable=False)
    success_conditions: Mapped[list] = mapped_column(JSONB, nullable=False)
    stop_conditions: Mapped[list] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")
    sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    materialized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class SearchQuery(Base):
    """Planner 生成并实际执行的搜索词。"""
    __tablename__ = "search_queries"
    __table_args__ = (
        UniqueConstraint("run_id", "dimension", "query", "provider", "iteration", name="uq_search_queries_run_query"),
        CheckConstraint(
            "status IN ('PENDING', 'RUNNING', 'COMPLETED', 'FAILED', 'CANCELLED')",
            name="ck_search_queries_status",
        ),
    )

    id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("research_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    question_id: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("research_questions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    dimension: Mapped[str] = mapped_column(String(100), nullable=False)
    query: Mapped[str] = mapped_column(Text, nullable=False)
    provider: Mapped[str] = mapped_column(String(100), nullable=False)
    iteration: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")
    raw_metadata: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class SearchResult(Base):
    """搜索 Provider 返回的原始结果，不等同于最终 Evidence。"""
    __tablename__ = "search_results"
    __table_args__ = (
        UniqueConstraint("query_id", "rank", name="uq_search_results_query_rank"),
    )

    id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    query_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("search_queries.id", ondelete="CASCADE"), nullable=False, index=True
    )
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    snippet: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_metadata: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class FetchArtifact(Base):
    """页面抓取结果与快照引用；正文仍由受控文件/对象存储保存。"""
    __tablename__ = "fetch_artifacts"
    __table_args__ = (
        UniqueConstraint("result_id", "attempt", name="uq_fetch_artifacts_result_attempt"),
        CheckConstraint(
            "status IN ('PENDING', 'FETCHED', 'FAILED', 'BLOCKED', 'SKIPPED')",
            name="ck_fetch_artifacts_status",
        ),
    )

    id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    result_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("search_results.id", ondelete="CASCADE"), nullable=False, index=True
    )
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")
    snapshot_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class ReportVersion(Base):
    """用户确认后的不可变报告版本。"""
    __tablename__ = "report_versions"
    __table_args__ = (
        UniqueConstraint("report_id", "version_no", name="uq_report_versions_report_version"),
        CheckConstraint("version_no > 0", name="ck_report_versions_version_positive"),
        CheckConstraint("status IN ('CONFIRMED', 'SUPERSEDED')", name="ck_report_versions_status"),
    )

    id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    report_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("reports.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    parent_version_id: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("report_versions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    research_run_id: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("research_runs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    content_md: Mapped[str] = mapped_column(Text, nullable=False)
    raw_data: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    evidence_index: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="CONFIRMED")
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class ReportDraft(Base):
    """基于不可变正式版本生成、等待用户裁决的报告修订草案。"""
    __tablename__ = "report_drafts"
    __table_args__ = (
        UniqueConstraint("report_id", "idempotency_key", name="uq_report_drafts_report_idempotency"),
        CheckConstraint(
            "status IN ('DRAFT', 'PARTIALLY_ACCEPTED', 'ACCEPTED', 'REJECTED', 'STALE')",
            name="ck_report_drafts_status",
        ),
    )

    id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    report_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("reports.id", ondelete="CASCADE"), nullable=False, index=True
    )
    base_version_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("report_versions.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    thread_id: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("report_threads.id", ondelete="SET NULL"), nullable=True, index=True
    )
    research_run_id: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("research_runs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    proposed_content_md: Mapped[str] = mapped_column(Text, nullable=False)
    proposed_raw_data: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    proposed_evidence_index: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    change_set: Mapped[list[dict]] = mapped_column(JSONB, nullable=False, default=list)
    decision: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="DRAFT")
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    accepted_version_id: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("report_versions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_by: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    decided_by: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ReportThread(Base):
    """报告版本上的持久化讨论会话。"""
    __tablename__ = "report_threads"
    __table_args__ = (
        CheckConstraint("status IN ('ACTIVE', 'ARCHIVED')", name="ck_report_threads_status"),
    )

    id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    report_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("reports.id", ondelete="CASCADE"), nullable=False, index=True
    )
    bound_version_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("report_versions.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False, default="未命名会话")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="ACTIVE")
    created_by: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class ReportMessage(Base):
    """报告会话消息；消息在模型调用前先持久化。"""
    __tablename__ = "report_messages"
    __table_args__ = (
        UniqueConstraint("thread_id", "idempotency_key", name="uq_report_messages_thread_idempotency"),
        CheckConstraint("role IN ('USER', 'ASSISTANT', 'SYSTEM')", name="ck_report_messages_role"),
        CheckConstraint(
            "intent IN ('QUESTION', 'EXPLANATION', 'FOLLOW_UP_RESEARCH', 'REPORT_REVISION', 'STATUS')",
            name="ck_report_messages_intent",
        ),
    )

    id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    thread_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("report_threads.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    intent: Mapped[str] = mapped_column(String(32), nullable=False, default="QUESTION")
    content: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str | None] = mapped_column(String(160), nullable=True)
    token_usage: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class MessageCitation(Base):
    """消息到研究资产的引用，避免会话答案失去来源。"""
    __tablename__ = "message_citations"
    __table_args__ = (
        UniqueConstraint("message_id", "artifact_type", "artifact_id", "quoted_range", name="uq_message_citations_source"),
    )

    id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    message_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("report_messages.id", ondelete="CASCADE"), nullable=False, index=True
    )
    artifact_type: Mapped[str] = mapped_column(String(64), nullable=False)
    artifact_id: Mapped[str] = mapped_column(String(128), nullable=False)
    quoted_range: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class ClarificationRequest(Base):
    """重大不确定性的问题账本；仅回答后才允许恢复对应 WorkUnit。"""
    __tablename__ = "clarification_requests"
    __table_args__ = (
        CheckConstraint("phase IN ('PRE_EXECUTION', 'IN_EXECUTION', 'PRE_REPORT')", name="ck_clarification_requests_phase"),
        CheckConstraint("materiality IN ('BLOCKING', 'MAJOR')", name="ck_clarification_requests_materiality"),
        CheckConstraint("status IN ('OPEN', 'ANSWERED', 'CANCELLED', 'SUPERSEDED')", name="ck_clarification_requests_status"),
        UniqueConstraint("task_id", "request_key", name="uq_clarification_requests_task_key"),
    )

    id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    task_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    run_id: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("research_runs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    stage_run_id: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("task_stage_runs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    thread_id: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("report_threads.id", ondelete="SET NULL"), nullable=True, index=True
    )
    phase: Mapped[str] = mapped_column(String(32), nullable=False)
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    materiality: Mapped[str] = mapped_column(String(32), nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    options: Mapped[dict] = mapped_column(JSONB, nullable=False, default=list)
    recommended_option: Mapped[str | None] = mapped_column(String(128), nullable=True)
    impact: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="OPEN")
    control_version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    request_key: Mapped[str] = mapped_column(String(128), nullable=False)
    created_by: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class ClarificationResponse(Base):
    """用户对澄清请求的持久化回答与恢复幂等键。"""
    __tablename__ = "clarification_responses"
    __table_args__ = (
        UniqueConstraint("resume_idempotency_key", name="uq_clarification_responses_resume_idempotency"),
    )

    id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    request_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("clarification_requests.id", ondelete="CASCADE"), nullable=False, index=True
    )
    answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    selected_option: Mapped[str | None] = mapped_column(String(128), nullable=True)
    responded_by: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    resume_idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False)
    responded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class ContextSnapshot(Base):
    """带来源的 L2 结构化上下文；原始资产仍保留在 L3。"""
    __tablename__ = "context_snapshots"
    __table_args__ = (
        CheckConstraint("domain IN ('external', 'customer_private', 'internal')", name="ck_context_snapshots_domain"),
        CheckConstraint("generation >= 0", name="ck_context_snapshots_generation_nonnegative"),
    )

    id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    run_id: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("research_runs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    thread_id: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("report_threads.id", ondelete="SET NULL"), nullable=True, index=True
    )
    report_version_id: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("report_versions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    scope: Mapped[str] = mapped_column(String(64), nullable=False)
    domain: Mapped[str] = mapped_column(String(32), nullable=False)
    generation: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    structured_content: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    model: Mapped[str | None] = mapped_column(String(160), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class ContextSnapshotSource(Base):
    """ContextSnapshot 中每项摘要对应的 L3 原始来源。"""
    __tablename__ = "context_snapshot_sources"
    __table_args__ = (
        UniqueConstraint("snapshot_id", "entry_key", name="uq_context_snapshot_sources_entry"),
    )

    id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    snapshot_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("context_snapshots.id", ondelete="CASCADE"), nullable=False, index=True
    )
    entry_key: Mapped[str] = mapped_column(String(128), nullable=False)
    source_type: Mapped[str] = mapped_column(String(64), nullable=False)
    source_id: Mapped[str] = mapped_column(String(128), nullable=False)
    relation: Mapped[str] = mapped_column(String(32), nullable=False, default="SUPPORTS")
    quoted_range: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class Evidence(Base):
    """证据记录 — WBS-6 扩展了快照路径、哈希和可信度列"""
    __tablename__ = "evidences"
    __table_args__ = (
        CheckConstraint(
            "data_domain IN ('external', 'customer_private', 'internal')",
            name="ck_evidences_data_domain",
        ),
        CheckConstraint(
            "fact_or_inference IN ('FACT', 'INFERENCE', 'ASSUMPTION')",
            name="ck_evidences_fact_or_inference",
        ),
        CheckConstraint(
            "opportunity_effect IN ('positive', 'negative', 'baseline', 'trigger', 'window', 'risk', 'neutral')",
            name="ck_evidences_opportunity_effect",
        ),
        CheckConstraint(
            "date_precision IN ('DAY', 'MONTH', 'YEAR', 'UNKNOWN')",
            name="ck_evidences_date_precision",
        ),
        CheckConstraint(
            "normalization_status IN ('RAW', 'NORMALIZED', 'CONFLICTED')",
            name="ck_evidences_normalization_status",
        ),
    )

    id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="SET NULL"), nullable=True, index=True
    )
    task_id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tasks.id"), nullable=False)
    dimension: Mapped[str] = mapped_column(String(100), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    snippet: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    source_type: Mapped[str] = mapped_column(String(100), nullable=False)
    meta_data: Mapped[dict] = mapped_column("metadata", JSONB, nullable=False, default=dict)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)

    # ── WBS-6 EvidenceTrust 新增列 ──────────────────────────────────
    source_reliability: Mapped[str | None] = mapped_column(String(10), nullable=True)  # S/A/B/C/UNKNOWN
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)  # SHA-256 hex
    raw_text_path: Mapped[str | None] = mapped_column(Text, nullable=True)  # txt.gz 快照路径
    html_snapshot_path: Mapped[str | None] = mapped_column(Text, nullable=True)  # html.gz 快照路径
    screenshot_path: Mapped[str | None] = mapped_column(Text, nullable=True)  # png 截图路径
    snapshot_size: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 快照总字节数
    snapshot_retention_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    relevance_score: Mapped[float | None] = mapped_column(Float, nullable=True)  # 相关性 0-1
    freshness_score: Mapped[float | None] = mapped_column(Float, nullable=True)  # 时效性 0-1
    data_domain: Mapped[str] = mapped_column(String(32), nullable=False, default="external")
    event_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    event_end_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    effective_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    contract_start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    contract_end_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    date_precision: Mapped[str] = mapped_column(String(16), nullable=False, default="UNKNOWN")
    procurement_stage: Mapped[str | None] = mapped_column(String(64), nullable=True)
    fact_or_inference: Mapped[str] = mapped_column(String(16), nullable=False, default="FACT")
    opportunity_effect: Mapped[str] = mapped_column(String(16), nullable=False, default="neutral")
    normalization_status: Mapped[str] = mapped_column(String(16), nullable=False, default="RAW")


class CustomerPrivateDocument(Base):
    """客户交流、RFP 和会议材料的独立受控存储索引。"""
    __tablename__ = "customer_private_documents"
    __table_args__ = (
        CheckConstraint("size_bytes >= 0", name="ck_customer_private_documents_size_nonnegative"),
        CheckConstraint(
            "sensitivity IN ('INTERNAL', 'CONFIDENTIAL', 'HIGHLY_CONFIDENTIAL')",
            name="ck_customer_private_documents_sensitivity",
        ),
        CheckConstraint(
            "status IN ('UPLOADED', 'SCANNED', 'READY', 'FAILED', 'DELETED')",
            name="ck_customer_private_documents_status",
        ),
    )

    id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    task_id: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True, index=True
    )
    original_filename: Mapped[str] = mapped_column(String(512), nullable=False)
    storage_ref: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(128), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sensitivity: Mapped[str] = mapped_column(String(32), nullable=False, default="CONFIDENTIAL")
    authorization_scope: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="UPLOADED")
    uploaded_by: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class CapabilityProfile(Base):
    """我方企业/业务单元能力档案；一个 Workspace 可维护多个档案。"""
    __tablename__ = "capability_profiles"
    __table_args__ = (
        UniqueConstraint("workspace_id", "name", name="uq_capability_profiles_workspace_name"),
        Index(
            "uq_capability_profiles_one_active_default",
            "workspace_id",
            unique=True,
            postgresql_where=text("is_default = true AND status = 'ACTIVE'"),
        ),
        CheckConstraint("status IN ('ACTIVE', 'ARCHIVED')", name="ck_capability_profiles_status"),
    )

    id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    legal_entity_name: Mapped[str | None] = mapped_column(String(500), nullable=True)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="ACTIVE")
    created_by: Mapped[UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class CapabilityProduct(Base):
    """能力档案中的产品版本与适用边界。"""
    __tablename__ = "capability_products"
    __table_args__ = (
        UniqueConstraint("profile_id", "name", "version_label", name="uq_capability_products_profile_name_version"),
        CheckConstraint("status IN ('DRAFT', 'ACTIVE', 'ARCHIVED')", name="ck_capability_products_status"),
    )

    id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True)
    profile_id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("capability_profiles.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    product_line: Mapped[str | None] = mapped_column(String(255), nullable=True)
    version_label: Mapped[str] = mapped_column(String(100), nullable=False, default="current")
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    capabilities: Mapped[list[dict]] = mapped_column(JSONB, nullable=False, default=list)
    constraints: Mapped[list[dict]] = mapped_column(JSONB, nullable=False, default=list)
    unsuitable_scenarios: Mapped[list[dict]] = mapped_column(JSONB, nullable=False, default=list)
    differentiators: Mapped[list[dict]] = mapped_column(JSONB, nullable=False, default=list)
    supported_regions: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    supported_industries: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="DRAFT")
    effective_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class CapabilitySolution(Base):
    __tablename__ = "capability_solutions"
    __table_args__ = (CheckConstraint("status IN ('DRAFT', 'ACTIVE', 'ARCHIVED')", name="ck_capability_solutions_status"),)

    id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True)
    profile_id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("capability_profiles.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    industry: Mapped[str | None] = mapped_column(String(255), nullable=True)
    problem_statement: Mapped[str] = mapped_column(Text, nullable=False, default="")
    solution_summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    product_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    constraints: Mapped[list[dict]] = mapped_column(JSONB, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="DRAFT")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class CapabilityCase(Base):
    __tablename__ = "capability_cases"
    __table_args__ = (CheckConstraint("status IN ('DRAFT', 'ACTIVE', 'ARCHIVED')", name="ck_capability_cases_status"),)

    id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True)
    profile_id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("capability_profiles.id", ondelete="CASCADE"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    customer_industry: Mapped[str | None] = mapped_column(String(255), nullable=True)
    challenge: Mapped[str] = mapped_column(Text, nullable=False, default="")
    outcome: Mapped[str] = mapped_column(Text, nullable=False, default="")
    metrics: Mapped[list[dict]] = mapped_column(JSONB, nullable=False, default=list)
    product_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="DRAFT")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class CapabilityQualification(Base):
    __tablename__ = "capability_qualifications"
    __table_args__ = (
        CheckConstraint("qualification_type IN ('CERTIFICATION', 'QUALIFICATION', 'LICENSE', 'SECURITY', 'OTHER')", name="ck_capability_qualifications_type"),
        CheckConstraint("status IN ('DRAFT', 'ACTIVE', 'EXPIRED', 'ARCHIVED')", name="ck_capability_qualifications_status"),
    )

    id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True)
    profile_id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("capability_profiles.id", ondelete="CASCADE"), nullable=False, index=True)
    qualification_type: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(500), nullable=False)
    issuer: Mapped[str | None] = mapped_column(String(500), nullable=True)
    certificate_no: Mapped[str | None] = mapped_column(String(255), nullable=True)
    applicable_regions: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="DRAFT")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class CapabilityKnowledgeDocument(Base):
    __tablename__ = "capability_knowledge_documents"
    __table_args__ = (
        UniqueConstraint("profile_id", "content_hash", "version_no", name="uq_capability_documents_profile_hash_version"),
        CheckConstraint("size_bytes >= 0 AND version_no > 0", name="ck_capability_documents_size_version"),
        CheckConstraint("status IN ('UPLOADED', 'SCANNED', 'PARSING', 'READY', 'FAILED', 'ARCHIVED')", name="ck_capability_documents_status"),
    )

    id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True)
    profile_id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("capability_profiles.id", ondelete="CASCADE"), nullable=False, index=True)
    entity_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    entity_id: Mapped[UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    original_filename: Mapped[str] = mapped_column(String(512), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(128), nullable=False)
    storage_ref: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    sensitivity: Mapped[str] = mapped_column(String(32), nullable=False, default="INTERNAL")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="UPLOADED")
    uploaded_by: Mapped[UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class CapabilityKnowledgeChunk(Base):
    __tablename__ = "capability_knowledge_chunks"
    __table_args__ = (
        UniqueConstraint("document_id", "ordinal", name="uq_capability_chunks_document_ordinal"),
        CheckConstraint("ordinal >= 0", name="ck_capability_chunks_ordinal"),
        Index(
            "ix_capability_chunks_search_vector",
            "search_vector",
            postgresql_using="gin",
        ),
        Index(
            "ix_capability_chunks_content_trgm",
            "content",
            postgresql_using="gin",
            postgresql_ops={"content": "gin_trgm_ops"},
        ),
    )

    id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True)
    document_id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("capability_knowledge_documents.id", ondelete="CASCADE"), nullable=False, index=True)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    page_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    heading: Mapped[str | None] = mapped_column(String(500), nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    search_vector: Mapped[str] = mapped_column(
        TSVECTOR,
        Computed(
            "to_tsvector('simple', coalesce(heading, '') || ' ' || content)",
            persisted=True,
        ),
        nullable=False,
    )
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class CapabilityKnowledgeEmbedding(Base):
    """知识切片在指定模型下生成的不可变真实向量。"""
    __tablename__ = "capability_knowledge_embeddings"
    __table_args__ = (
        UniqueConstraint(
            "chunk_id", "model_name", "content_hash",
            name="uq_capability_embeddings_chunk_model_content",
        ),
        CheckConstraint("dimensions = 1536", name="ck_capability_embeddings_dimensions"),
        Index(
            "ix_capability_embeddings_hnsw_cosine",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    chunk_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("capability_knowledge_chunks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    model_name: Mapped[str] = mapped_column(String(255), nullable=False)
    dimensions: Mapped[int] = mapped_column(Integer, nullable=False, default=1536)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    embedding: Mapped[list[float]] = mapped_column(VECTOR(1536), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class CapabilityProductMatchSnapshot(Base):
    """一次手动产品匹配的不可变输入与结果快照。"""
    __tablename__ = "capability_product_match_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "task_id", "input_hash",
            name="uq_capability_product_match_snapshots_input",
        ),
        CheckConstraint(
            "status IN ('MATCHED', 'PARTIAL', 'NO_MATCH', 'NEEDS_VALIDATION', 'BLOCKED')",
            name="ck_capability_product_match_snapshots_status",
        ),
    )

    id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    task_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    profile_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("capability_profiles.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    created_by: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    analysis_as_of_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    input_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    result_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class Claim(Base):
    """可被报告、问答、匹配和商机假设共同引用的原子结论。"""
    __tablename__ = "claims"
    __table_args__ = (
        CheckConstraint(
            "claim_type IN ('FACT', 'INFERENCE', 'ASSUMPTION')",
            name="ck_claims_claim_type",
        ),
        CheckConstraint(
            "opportunity_effect IN ('positive', 'negative', 'baseline', 'trigger', 'window', 'risk', 'neutral')",
            name="ck_claims_opportunity_effect",
        ),
        CheckConstraint(
            "status IN ('UNVERIFIED', 'SUPPORTED', 'CUSTOMER_CONFIRMED', 'CONFLICTED', 'EXPIRED', 'REFUTED')",
            name="ck_claims_status",
        ),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_claims_confidence_range"),
    )

    id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    task_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    report_version_id: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("report_versions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    source_gate_factor_id: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("gate_decision_factors.id", ondelete="RESTRICT"),
        nullable=True,
        unique=True,
    )
    claim_text: Mapped[str] = mapped_column(Text, nullable=False)
    claim_type: Mapped[str] = mapped_column(String(16), nullable=False)
    opportunity_effect: Mapped[str] = mapped_column(String(16), nullable=False, default="neutral")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="UNVERIFIED")
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class ClaimEvidenceLink(Base):
    """Claim 与支持或反向 Evidence 的显式关系。"""
    __tablename__ = "claim_evidence_links"
    __table_args__ = (
        UniqueConstraint("claim_id", "evidence_id", "relation", name="uq_claim_evidence_links_relation"),
        CheckConstraint("relation IN ('SUPPORTS', 'REFUTES')", name="ck_claim_evidence_links_relation"),
        CheckConstraint("weight >= 0 AND weight <= 1", name="ck_claim_evidence_links_weight_range"),
    )

    id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    claim_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("claims.id", ondelete="CASCADE"), nullable=False, index=True
    )
    evidence_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("evidences.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    relation: Mapped[str] = mapped_column(String(16), nullable=False)
    weight: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


# ── WBS-6 EvidenceTrust 新增表 ──────────────────────────────────────────


class OpportunityProject(Base):
    __tablename__ = "opportunity_projects"
    id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True)
    target_account_id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("target_accounts.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(500), nullable=False)
    procurement_nature: Mapped[str] = mapped_column(String(32), nullable=False, default="UNKNOWN")
    lifecycle_stage: Mapped[str] = mapped_column(String(32), nullable=False, default="UNKNOWN")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class OpportunityEvent(Base):
    __tablename__ = "opportunity_events"
    id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True)
    target_account_id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("target_accounts.id", ondelete="CASCADE"), nullable=False, index=True)
    project_id: Mapped[UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("opportunity_projects.id", ondelete="SET NULL"), nullable=True, index=True)
    evidence_id: Mapped[UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("evidences.id", ondelete="RESTRICT"), nullable=True, index=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    lifecycle_stage: Mapped[str] = mapped_column(String(32), nullable=False, default="UNKNOWN")
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class OpportunityContract(Base):
    __tablename__ = "opportunity_contracts"
    id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True)
    target_account_id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("target_accounts.id", ondelete="CASCADE"), nullable=False, index=True)
    project_id: Mapped[UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("opportunity_projects.id", ondelete="SET NULL"), nullable=True, index=True)
    evidence_id: Mapped[UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("evidences.id", ondelete="RESTRICT"), nullable=True, index=True)
    supplier_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="STATUS_UNKNOWN")
    start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    end_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    terms: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class CustomerCapability(Base):
    __tablename__ = "customer_capabilities"
    id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True)
    target_account_id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("target_accounts.id", ondelete="CASCADE"), nullable=False, index=True)
    evidence_id: Mapped[UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("evidences.id", ondelete="RESTRICT"), nullable=True, index=True)
    capability_key: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="UNKNOWN")
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class TargetRequirement(Base):
    __tablename__ = "target_requirements"
    id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True)
    target_account_id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("target_accounts.id", ondelete="CASCADE"), nullable=False, index=True)
    evidence_id: Mapped[UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("evidences.id", ondelete="RESTRICT"), nullable=True, index=True)
    requirement_key: Mapped[str] = mapped_column(String(255), nullable=False)
    source_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    strength: Mapped[str] = mapped_column(String(32), nullable=False, default="UNKNOWN")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class PolicyObligation(Base):
    __tablename__ = "policy_obligations"
    id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True)
    target_account_id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("target_accounts.id", ondelete="CASCADE"), nullable=False, index=True)
    evidence_id: Mapped[UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("evidences.id", ondelete="RESTRICT"), nullable=True, index=True)
    obligation_key: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="UNKNOWN")
    mandatory_level: Mapped[str] = mapped_column(String(32), nullable=False, default="UNKNOWN")
    effective_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class GateDecision(Base):
    __tablename__ = "gate_decisions"
    id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True)
    target_account_id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("target_accounts.id", ondelete="CASCADE"), nullable=False, index=True)
    task_id: Mapped[UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True, index=True)
    decision: Mapped[str] = mapped_column(String(32), nullable=False)
    gate_level: Mapped[str] = mapped_column(String(8), nullable=False)
    analysis_as_of_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    input_hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    summary: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class GateDecisionFactor(Base):
    __tablename__ = "gate_decision_factors"
    id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    gate_decision_id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("gate_decisions.id", ondelete="CASCADE"), nullable=False, index=True)
    evidence_id: Mapped[UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("evidences.id", ondelete="RESTRICT"), nullable=True, index=True)
    factor_type: Mapped[str] = mapped_column(String(64), nullable=False)
    effect: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class GateDecisionHistory(Base):
    __tablename__ = "gate_decision_history"
    id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    gate_decision_id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("gate_decisions.id", ondelete="CASCADE"), nullable=False, index=True)
    from_decision: Mapped[str | None] = mapped_column(String(32), nullable=True)
    to_decision: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class OpportunityHypothesis(Base):
    """由 OIG G4/G5 裁决产生、仍待销售和客户验证的正式商机假设。"""
    __tablename__ = "opportunity_hypotheses"
    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING_SALES_REVIEW', 'SALES_ACCEPTED', 'SALES_REJECTED', 'DEFERRED', "
            "'CUSTOMER_VALIDATED', 'VALIDATION_FAILED', 'CONVERTED', 'EXPIRED')",
            name="ck_opportunity_hypotheses_status",
        ),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_opportunity_hypotheses_confidence"),
        CheckConstraint(
            "information_completeness >= 0 AND information_completeness <= 1",
            name="ck_opportunity_hypotheses_completeness",
        ),
    )

    id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True)
    target_account_id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("target_accounts.id", ondelete="CASCADE"), nullable=False, index=True)
    source_task_id: Mapped[UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True, index=True)
    source_run_id: Mapped[UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("research_runs.id", ondelete="SET NULL"), nullable=True, index=True)
    gate_decision_id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("gate_decisions.id", ondelete="RESTRICT"), nullable=False, unique=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    customer_problem_hypothesis: Mapped[str] = mapped_column(Text, nullable=False)
    business_impact_hypothesis: Mapped[str] = mapped_column(Text, nullable=False)
    trigger_event: Mapped[str] = mapped_column(Text, nullable=False)
    counter_evidence_summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    hard_blockers: Mapped[list[dict]] = mapped_column(JSONB, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING_SALES_REVIEW", index=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    information_completeness: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    owner_user_id: Mapped[UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    deferred_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class OpportunityHypothesisHistory(Base):
    """商机假设的人工作业状态账本；正式商机阶段由独立 Opportunity 对象维护。"""
    __tablename__ = "opportunity_hypothesis_history"
    __table_args__ = (
        UniqueConstraint("hypothesis_id", "request_key", name="uq_opportunity_hypothesis_history_request"),
    )

    id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    hypothesis_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("opportunity_hypotheses.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    from_status: Mapped[str] = mapped_column(String(32), nullable=False)
    to_status: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    request_key: Mapped[str] = mapped_column(String(128), nullable=False)
    changed_by: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class Opportunity(Base):
    """经销售接受、客户验证和阶段门确认后创建的正式销售机会。"""
    __tablename__ = "opportunities"
    __table_args__ = (
        UniqueConstraint(
            "source_hypothesis_id",
            name="uq_opportunities_source_hypothesis",
        ),
        CheckConstraint(
            "stage IN ('QUALIFICATION', 'DISCOVERY', 'SOLUTION_SHAPING', 'PROPOSAL', "
            "'TENDER', 'NEGOTIATION', 'WON', 'LOST', 'CANCELLED')",
            name="ck_opportunities_stage",
        ),
        CheckConstraint(
            "amount_source IN ('UNSPECIFIED', 'CUSTOMER_CONFIRMED', 'USER_ESTIMATE', 'CRM_IMPORTED')",
            name="ck_opportunities_amount_source",
        ),
        CheckConstraint(
            "amount IS NULL OR amount >= 0",
            name="ck_opportunities_amount_nonnegative",
        ),
        CheckConstraint(
            "probability >= 0 AND probability <= 1",
            name="ck_opportunities_probability_range",
        ),
        CheckConstraint(
            "amount IS NULL OR currency IS NOT NULL",
            name="ck_opportunities_amount_requires_currency",
        ),
        CheckConstraint(
            "currency IS NULL OR char_length(currency) = 3",
            name="ck_opportunities_currency_length",
        ),
        CheckConstraint(
            "stage NOT IN ('WON', 'LOST', 'CANCELLED') OR closed_at IS NOT NULL",
            name="ck_opportunities_terminal_stage_closed_at",
        ),
        CheckConstraint(
            "stage != 'LOST' OR (close_reason IS NOT NULL AND char_length(trim(close_reason)) > 0)",
            name="ck_opportunities_lost_reason",
        ),
    )

    id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    target_account_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("target_accounts.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    source_hypothesis_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("opportunity_hypotheses.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    stage: Mapped[str] = mapped_column(String(32), nullable=False, default="QUALIFICATION", index=True)
    owner_user_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    amount_source: Mapped[str] = mapped_column(String(32), nullable=False, default="UNSPECIFIED")
    probability: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    expected_close_date: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    close_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class OpportunityStageHistory(Base):
    """正式商机的不可变、幂等阶段变更账本。"""
    __tablename__ = "opportunity_stage_history"
    __table_args__ = (
        UniqueConstraint(
            "opportunity_id",
            "request_key",
            name="uq_opportunity_stage_history_request",
        ),
        CheckConstraint(
            "from_stage IS NULL OR from_stage IN ('QUALIFICATION', 'DISCOVERY', 'SOLUTION_SHAPING', "
            "'PROPOSAL', 'TENDER', 'NEGOTIATION', 'WON', 'LOST', 'CANCELLED')",
            name="ck_opportunity_stage_history_from_stage",
        ),
        CheckConstraint(
            "to_stage IN ('QUALIFICATION', 'DISCOVERY', 'SOLUTION_SHAPING', 'PROPOSAL', "
            "'TENDER', 'NEGOTIATION', 'WON', 'LOST', 'CANCELLED')",
            name="ck_opportunity_stage_history_to_stage",
        ),
        CheckConstraint(
            "octet_length(request_hash) = 32",
            name="ck_opportunity_stage_history_request_hash",
        ),
    )

    id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    opportunity_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("opportunities.id", ondelete="CASCADE"), nullable=False, index=True
    )
    from_stage: Mapped[str | None] = mapped_column(String(32), nullable=True)
    to_stage: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    request_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    changed_by: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class OpportunityStakeholder(Base):
    """客户决策链中的持续业务对象；姓名未知时允许仅维护角色。"""
    __tablename__ = "opportunity_stakeholders"
    __table_args__ = (
        CheckConstraint(
            "role_type IN ('ECONOMIC_BUYER', 'BUSINESS_OWNER', 'TECHNICAL_DECISION_MAKER', "
            "'SECURITY_COMPLIANCE', 'PROCUREMENT', 'USER', 'CHAMPION', 'BLOCKER', 'OTHER')",
            name="ck_opportunity_stakeholders_role_type",
        ),
        CheckConstraint(
            "influence IN ('UNKNOWN', 'LOW', 'MEDIUM', 'HIGH')",
            name="ck_opportunity_stakeholders_influence",
        ),
        CheckConstraint(
            "attitude IN ('UNKNOWN', 'SUPPORTIVE', 'NEUTRAL', 'OPPOSED')",
            name="ck_opportunity_stakeholders_attitude",
        ),
        CheckConstraint(
            "relationship_strength IN ('UNKNOWN', 'NONE', 'WEAK', 'MEDIUM', 'STRONG')",
            name="ck_opportunity_stakeholders_relationship",
        ),
        CheckConstraint(
            "truth_status IN ('PUBLIC_INFERENCE', 'SALES_JUDGMENT', 'CUSTOMER_CONFIRMED')",
            name="ck_opportunity_stakeholders_truth_status",
        ),
        CheckConstraint(
            "truth_status = 'SALES_JUDGMENT' OR source_claim_id IS NOT NULL",
            name="ck_opportunity_stakeholders_evidence_required",
        ),
        CheckConstraint(
            "status IN ('ACTIVE', 'ARCHIVED')",
            name="ck_opportunity_stakeholders_status",
        ),
    )

    id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    target_account_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("target_accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    opportunity_id: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("opportunities.id", ondelete="CASCADE"), nullable=True, index=True
    )
    role_type: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    full_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    role_title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    department: Mapped[str | None] = mapped_column(String(255), nullable=True)
    influence: Mapped[str] = mapped_column(String(16), nullable=False, default="UNKNOWN")
    attitude: Mapped[str] = mapped_column(String(16), nullable=False, default="UNKNOWN")
    goals: Mapped[str] = mapped_column(Text, nullable=False, default="")
    concerns: Mapped[str] = mapped_column(Text, nullable=False, default="")
    relationship_strength: Mapped[str] = mapped_column(String(16), nullable=False, default="UNKNOWN")
    truth_status: Mapped[str] = mapped_column(String(32), nullable=False)
    source_claim_id: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("claims.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    communication_strategy: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ACTIVE", index=True)
    created_by: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class OpportunityQualificationFramework(Base):
    """Workspace 级可版本化资格框架，不绑定单一销售方法。"""
    __tablename__ = "opportunity_qualification_frameworks"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "framework_key",
            "version_no",
            name="uq_opportunity_qualification_frameworks_version",
        ),
        UniqueConstraint(
            "workspace_id",
            "framework_key",
            "content_hash",
            name="uq_opportunity_qualification_frameworks_content",
        ),
        CheckConstraint(
            "version_no > 0",
            name="ck_opportunity_qualification_frameworks_version_positive",
        ),
        CheckConstraint(
            "methodology IN ('CUSTOM', 'MEDDPICC', 'BANT', 'SPICED', 'HYBRID')",
            name="ck_opportunity_qualification_frameworks_methodology",
        ),
        CheckConstraint(
            "status IN ('DRAFT', 'PUBLISHED', 'ARCHIVED')",
            name="ck_opportunity_qualification_frameworks_status",
        ),
        CheckConstraint(
            "minimum_score >= 0 AND minimum_score <= 1",
            name="ck_opportunity_qualification_frameworks_score",
        ),
        CheckConstraint(
            "minimum_completeness >= 0 AND minimum_completeness <= 1",
            name="ck_opportunity_qualification_frameworks_completeness",
        ),
        CheckConstraint(
            "octet_length(content_hash) = 32",
            name="ck_opportunity_qualification_frameworks_content_hash",
        ),
        CheckConstraint(
            "jsonb_typeof(criteria) = 'array' AND jsonb_typeof(hard_blocker_rules) = 'array'",
            name="ck_opportunity_qualification_frameworks_json_arrays",
        ),
        CheckConstraint(
            "status != 'PUBLISHED' OR published_at IS NOT NULL",
            name="ck_opportunity_qualification_frameworks_published_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    framework_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    methodology: Mapped[str] = mapped_column(String(16), nullable=False, default="CUSTOM")
    criteria: Mapped[list[dict]] = mapped_column(JSONB, nullable=False, default=list)
    hard_blocker_rules: Mapped[list[dict]] = mapped_column(JSONB, nullable=False, default=list)
    minimum_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.7)
    minimum_completeness: Mapped[float] = mapped_column(Float, nullable=False, default=0.7)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="DRAFT", index=True)
    content_hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_by: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class OpportunityQualificationCard(Base):
    """正式商机创建前后均可复用的不可变资格评估快照。"""
    __tablename__ = "opportunity_qualification_cards"
    __table_args__ = (
        UniqueConstraint(
            "hypothesis_id",
            "assessment_no",
            name="uq_opportunity_qualification_cards_assessment",
        ),
        UniqueConstraint(
            "hypothesis_id",
            "input_hash",
            name="uq_opportunity_qualification_cards_input",
        ),
        CheckConstraint(
            "assessment_no > 0",
            name="ck_opportunity_qualification_cards_assessment_positive",
        ),
        CheckConstraint(
            "gate_result IN ('INCOMPLETE', 'PASS', 'FAIL')",
            name="ck_opportunity_qualification_cards_gate_result",
        ),
        CheckConstraint(
            "score >= 0 AND score <= 1",
            name="ck_opportunity_qualification_cards_score",
        ),
        CheckConstraint(
            "information_completeness >= 0 AND information_completeness <= 1",
            name="ck_opportunity_qualification_cards_completeness",
        ),
        CheckConstraint(
            "octet_length(input_hash) = 32",
            name="ck_opportunity_qualification_cards_input_hash",
        ),
        CheckConstraint(
            "jsonb_typeof(criteria) = 'array' AND jsonb_typeof(hard_blockers) = 'array' "
            "AND jsonb_typeof(missing_fields) = 'array'",
            name="ck_opportunity_qualification_cards_json_arrays",
        ),
        CheckConstraint(
            "gate_result != 'PASS' OR jsonb_array_length(hard_blockers) = 0",
            name="ck_opportunity_qualification_cards_pass_has_no_blocker",
        ),
    )

    id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    hypothesis_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("opportunity_hypotheses.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    framework_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("opportunity_qualification_frameworks.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    assessment_no: Mapped[int] = mapped_column(Integer, nullable=False)
    framework_key: Mapped[str] = mapped_column(String(64), nullable=False)
    framework_version: Mapped[str] = mapped_column(String(64), nullable=False)
    criteria: Mapped[list[dict]] = mapped_column(JSONB, nullable=False, default=list)
    hard_blockers: Mapped[list[dict]] = mapped_column(JSONB, nullable=False, default=list)
    missing_fields: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    gate_result: Mapped[str] = mapped_column(String(16), nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    information_completeness: Mapped[float] = mapped_column(Float, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    input_hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    assessed_by: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    assessed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class OpportunityCompetitor(Base):
    """正式商机中的竞争对象，也覆盖维持现状、延期和不投资。"""
    __tablename__ = "opportunity_competitors"
    __table_args__ = (
        CheckConstraint(
            "competitor_type IN ('COMMERCIAL_VENDOR', 'INCUMBENT_VENDOR', 'CUSTOMER_SELF_BUILD', "
            "'STATUS_QUO', 'DELAY', 'NO_INVESTMENT')",
            name="ck_opportunity_competitors_type",
        ),
        CheckConstraint(
            "competitor_type NOT IN ('COMMERCIAL_VENDOR', 'INCUMBENT_VENDOR') "
            "OR (name IS NOT NULL AND char_length(trim(name)) > 0)",
            name="ck_opportunity_competitors_vendor_name",
        ),
        CheckConstraint(
            "truth_status IN ('PUBLIC_EVIDENCE', 'SALES_JUDGMENT', 'CUSTOMER_CONFIRMED')",
            name="ck_opportunity_competitors_truth_status",
        ),
        CheckConstraint(
            "truth_status = 'SALES_JUDGMENT' OR source_claim_id IS NOT NULL",
            name="ck_opportunity_competitors_evidence_required",
        ),
        CheckConstraint(
            "status IN ('ACTIVE', 'DISMISSED')",
            name="ck_opportunity_competitors_status",
        ),
    )

    id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    opportunity_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("opportunities.id", ondelete="CASCADE"), nullable=False, index=True
    )
    competitor_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    truth_status: Mapped[str] = mapped_column(String(32), nullable=False)
    source_claim_id: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("claims.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ACTIVE", index=True)
    created_by: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class CompetitiveBattlecard(Base):
    """单个竞争对象的不可变作战卡快照。"""
    __tablename__ = "competitive_battlecards"
    __table_args__ = (
        UniqueConstraint(
            "competitor_id",
            "version_no",
            name="uq_competitive_battlecards_version",
        ),
        UniqueConstraint(
            "competitor_id",
            "input_hash",
            name="uq_competitive_battlecards_input",
        ),
        CheckConstraint("version_no > 0", name="ck_competitive_battlecards_version_positive"),
        CheckConstraint("octet_length(input_hash) = 32", name="ck_competitive_battlecards_input_hash"),
        CheckConstraint(
            "jsonb_typeof(current_contract) = 'object' AND "
            "jsonb_typeof(competitor_strengths) = 'array' AND "
            "jsonb_typeof(competitor_weaknesses) = 'array' AND "
            "jsonb_typeof(our_differentiators) = 'array' AND "
            "jsonb_typeof(customer_decision_criteria) = 'array' AND "
            "jsonb_typeof(must_win_metrics) = 'array' AND "
            "jsonb_typeof(our_risks) = 'array' AND "
            "jsonb_typeof(prohibited_commitments) = 'array' AND "
            "jsonb_typeof(discovery_questions) = 'array' AND "
            "jsonb_typeof(ecosystem_partners) = 'array'",
            name="ck_competitive_battlecards_json_contract",
        ),
    )

    id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    competitor_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("opportunity_competitors.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    current_contract: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    switching_cost_assessment: Mapped[str] = mapped_column(Text, nullable=False, default="")
    competitor_strengths: Mapped[list[dict]] = mapped_column(JSONB, nullable=False, default=list)
    competitor_weaknesses: Mapped[list[dict]] = mapped_column(JSONB, nullable=False, default=list)
    our_differentiators: Mapped[list[dict]] = mapped_column(JSONB, nullable=False, default=list)
    customer_decision_criteria: Mapped[list[dict]] = mapped_column(JSONB, nullable=False, default=list)
    must_win_metrics: Mapped[list[dict]] = mapped_column(JSONB, nullable=False, default=list)
    our_risks: Mapped[list[dict]] = mapped_column(JSONB, nullable=False, default=list)
    prohibited_commitments: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    discovery_questions: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    ecosystem_partners: Mapped[list[dict]] = mapped_column(JSONB, nullable=False, default=list)
    input_hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_by: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class OpportunityValueHypothesis(Base):
    """价值工程计算模型的不可变快照；数值来源与假设由服务层逐项校验。"""
    __tablename__ = "opportunity_value_hypotheses"
    __table_args__ = (
        UniqueConstraint(
            "opportunity_id",
            "version_no",
            name="uq_opportunity_value_hypotheses_version",
        ),
        UniqueConstraint(
            "opportunity_id",
            "input_hash",
            name="uq_opportunity_value_hypotheses_input",
        ),
        CheckConstraint("version_no > 0", name="ck_opportunity_value_hypotheses_version_positive"),
        CheckConstraint(
            "status IN ('NEEDS_VALIDATION', 'CUSTOMER_CONFIRMED', 'REJECTED')",
            name="ck_opportunity_value_hypotheses_status",
        ),
        CheckConstraint(
            "currency IS NULL OR char_length(currency) = 3",
            name="ck_opportunity_value_hypotheses_currency_length",
        ),
        CheckConstraint(
            "time_horizon_months IS NULL OR time_horizon_months > 0",
            name="ck_opportunity_value_hypotheses_time_horizon",
        ),
        CheckConstraint("octet_length(input_hash) = 32", name="ck_opportunity_value_hypotheses_input_hash"),
        CheckConstraint(
            "jsonb_typeof(inputs) = 'array' AND jsonb_typeof(formulas) = 'array' AND "
            "jsonb_typeof(outputs) = 'array' AND jsonb_typeof(sensitivity_scenarios) = 'array' AND "
            "jsonb_typeof(assumptions) = 'array' AND jsonb_typeof(missing_parameters) = 'array'",
            name="ck_opportunity_value_hypotheses_json_arrays",
        ),
        CheckConstraint(
            "status != 'CUSTOMER_CONFIRMED' OR jsonb_array_length(missing_parameters) = 0",
            name="ck_opportunity_value_hypotheses_confirmed_complete",
        ),
    )

    id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    opportunity_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("opportunities.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="NEEDS_VALIDATION")
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    time_horizon_months: Mapped[int | None] = mapped_column(Integer, nullable=True)
    inputs: Mapped[list[dict]] = mapped_column(JSONB, nullable=False, default=list)
    formulas: Mapped[list[dict]] = mapped_column(JSONB, nullable=False, default=list)
    outputs: Mapped[list[dict]] = mapped_column(JSONB, nullable=False, default=list)
    sensitivity_scenarios: Mapped[list[dict]] = mapped_column(JSONB, nullable=False, default=list)
    assumptions: Mapped[list[dict]] = mapped_column(JSONB, nullable=False, default=list)
    missing_parameters: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    input_hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_by: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class OpportunityHypothesisClaim(Base):
    __tablename__ = "opportunity_hypothesis_claims"
    __table_args__ = (
        UniqueConstraint("hypothesis_id", "claim_id", "relation", name="uq_opportunity_hypothesis_claim_relation"),
        CheckConstraint("relation IN ('SUPPORTS', 'REFUTES')", name="ck_opportunity_hypothesis_claim_relation"),
    )

    id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    hypothesis_id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("opportunity_hypotheses.id", ondelete="CASCADE"), nullable=False, index=True)
    claim_id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("claims.id", ondelete="RESTRICT"), nullable=False, index=True)
    relation: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class OpportunityHypothesisProduct(Base):
    __tablename__ = "opportunity_hypothesis_products"
    __table_args__ = (
        UniqueConstraint("hypothesis_id", "product_id", name="uq_opportunity_hypothesis_product"),
        CheckConstraint("fit_score >= 0 AND fit_score <= 1", name="ck_opportunity_hypothesis_product_fit"),
    )

    id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    hypothesis_id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("opportunity_hypotheses.id", ondelete="CASCADE"), nullable=False, index=True)
    product_id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("capability_products.id", ondelete="RESTRICT"), nullable=False, index=True)
    fit_score: Mapped[float] = mapped_column(Float, nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class NextBestAction(Base):
    __tablename__ = "next_best_actions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING', 'IN_PROGRESS', 'COMPLETED', 'FAILED', 'CANCELLED')",
            name="ck_next_best_actions_status",
        ),
    )

    id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True)
    hypothesis_id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("opportunity_hypotheses.id", ondelete="CASCADE"), nullable=False, index=True)
    objective: Mapped[str] = mapped_column(Text, nullable=False)
    target_role: Mapped[str | None] = mapped_column(String(255), nullable=True)
    recommended_channel: Mapped[str | None] = mapped_column(String(64), nullable=True)
    talking_point: Mapped[str] = mapped_column(Text, nullable=False, default="")
    suggested_questions: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    collateral: Mapped[list[dict]] = mapped_column(JSONB, nullable=False, default=list)
    prerequisites: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    expected_outcome: Mapped[str] = mapped_column(Text, nullable=False, default="")
    owner_user_id: Mapped[UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")
    result: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class NextBestActionHistory(Base):
    """下一步行动的幂等状态与结果审计账本。"""
    __tablename__ = "next_best_action_history"
    __table_args__ = (
        UniqueConstraint("action_id", "request_key", name="uq_next_best_action_history_request"),
    )

    id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    action_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("next_best_actions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    from_status: Mapped[str] = mapped_column(String(32), nullable=False)
    to_status: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    result: Mapped[str | None] = mapped_column(Text, nullable=True)
    request_key: Mapped[str] = mapped_column(String(128), nullable=False)
    changed_by: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class EvidenceAudit(Base):
    """证据审计记录（WBS-10 EvidenceAuditor 填充）"""
    __tablename__ = "evidence_audits"

    id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    evidence_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("evidences.id", ondelete="CASCADE"), nullable=False
    )
    support_level: Mapped[str | None] = mapped_column(String(20), nullable=True)  # STRONG/WEAK/REFUTED
    reliability_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    relevance_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    freshness_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    audit_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class ClaimAudit(Base):
    """结论审计记录（WBS-10 EvidenceAuditor 填充）"""
    __tablename__ = "claim_audits"

    id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    report_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("reports.id", ondelete="CASCADE"), nullable=False
    )
    claim_text: Mapped[str | None] = mapped_column(Text, nullable=True)  # 结论原文
    support_status: Mapped[str | None] = mapped_column(String(20), nullable=True)  # SUPPORTED/WEAK/UNSUPPORTED/CONTRADICTED
    evidence_ids: Mapped[dict | None] = mapped_column(JSONB, nullable=True, default=list)  # 关联证据 ID 列表
    skeptic_level: Mapped[str | None] = mapped_column(String(20), nullable=True)  # NONE/LOW/MEDIUM/HIGH
    skeptic_notes: Mapped[str | None] = mapped_column(Text, nullable=True)  # 质疑笔记
    suggested_revision: Mapped[str | None] = mapped_column(Text, nullable=True)  # 建议修正文本
    severity: Mapped[str | None] = mapped_column(String(20), nullable=True)  # WBS-20a: fatal/major/minor/acceptable
    replan_count: Mapped[int] = mapped_column(Integer, default=0)  # WBS-20a: 该 claim 已触发 Re-Plan 次数
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class TaskLog(Base):
    __tablename__ = "task_logs"

    id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    task_id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tasks.id"), nullable=False)
    step_name: Mapped[str] = mapped_column(String(100), nullable=False)
    level: Mapped[LogLevel] = mapped_column(SQLEnum(LogLevel), default=LogLevel.INFO)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class ExperienceRecord(Base):
    """长期记忆：记录成功的搜索经验供后续任务复用"""
    __tablename__ = "experience_records"

    id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    task_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    dimension: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    company_name: Mapped[str] = mapped_column(String(255), nullable=False)
    demand_direction: Mapped[str] = mapped_column(String(255), nullable=False)
    goal: Mapped[str] = mapped_column(Text, nullable=False)
    search_queries: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    strategy: Mapped[str] = mapped_column(Text, nullable=False, default="")
    quality_score: Mapped[float] = mapped_column(nullable=False, default=0.0)
    iteration_count: Mapped[int] = mapped_column(nullable=False, default=0)
    token_used: Mapped[int] = mapped_column(nullable=False, default=0)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    meta_data: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class Notification(Base):
    """站内通知"""
    __tablename__ = "notifications"

    id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    task_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    notification_type: Mapped[str] = mapped_column(String(50), nullable=False)  # task_completed, task_failed
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


# ── WBS-33 Skill V2 标准目录、版本与评测模型 ─────────────────────────


class Skill(Base):
    """标准 Skill 业务身份；正文只存在于受控文件目录及不可变版本快照。"""
    __tablename__ = "skills"
    __table_args__ = (
        CheckConstraint("scope IN ('SYSTEM', 'WORKSPACE')", name="ck_skills_scope"),
        CheckConstraint(
            "(scope = 'SYSTEM' AND workspace_id IS NULL) OR "
            "(scope = 'WORKSPACE' AND workspace_id IS NOT NULL)",
            name="ck_skills_scope_workspace",
        ),
        CheckConstraint(
            "status IN ('DRAFT', 'PUBLISHED', 'ARCHIVED')",
            name="ck_skills_status",
        ),
        UniqueConstraint("workspace_id", "name", name="uq_skills_workspace_name"),
        Index(
            "uq_skills_system_name",
            "name",
            unique=True,
            postgresql_where=text("workspace_id IS NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=True, index=True
    )
    owner_user_id: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    display_name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    scope: Mapped[str] = mapped_column(String(16), nullable=False, default="WORKSPACE")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="DRAFT")
    current_version_id: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "skill_versions.id",
            name="fk_skills_current_version_id_skill_versions",
            ondelete="SET NULL",
            use_alter=True,
        ),
        nullable=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class SkillVersion(Base):
    """Skill 的不可变源码快照与编译结果。"""
    __tablename__ = "skill_versions"
    __table_args__ = (
        UniqueConstraint("skill_id", "version", name="uq_skill_versions_skill_version"),
        UniqueConstraint("skill_id", "content_hash", name="uq_skill_versions_skill_hash"),
        CheckConstraint("version >= 1", name="ck_skill_versions_version_positive"),
        CheckConstraint("char_length(content_hash) = 64", name="ck_skill_versions_hash_length"),
        CheckConstraint(
            "status IN ('DRAFT', 'COMPILED', 'EVALUATED', 'PUBLISHED', 'REJECTED', 'ARCHIVED')",
            name="ck_skill_versions_status",
        ),
    )

    id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    skill_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("skills.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    source_path: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    compiled_spec: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="DRAFT")
    created_by: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    compiled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class SkillDependencyRecord(Base):
    """已编译版本到子 Skill 的稳定依赖。"""
    __tablename__ = "skill_dependencies"
    __table_args__ = (
        UniqueConstraint(
            "parent_version_id", "child_skill_id", name="uq_skill_dependencies_parent_child"
        ),
    )

    id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    parent_version_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("skill_versions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    child_skill_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("skills.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    version_constraint: Mapped[str] = mapped_column(String(64), nullable=False)
    condition: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class SkillImportSource(Base):
    """GitHub/离线导入的固定来源快照；v3.3 本地创建时可不存在。"""
    __tablename__ = "skill_import_sources"
    __table_args__ = (
        CheckConstraint(
            "source_type IN ('GITHUB', 'OFFLINE_ARCHIVE')",
            name="ck_skill_import_sources_type",
        ),
        UniqueConstraint("skill_id", "commit_sha", "path", name="uq_skill_import_sources_snapshot"),
    )

    id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    skill_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("skills.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("skill_versions.id", ondelete="RESTRICT"),
        nullable=False, unique=True, index=True,
    )
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    repo_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    path: Mapped[str] = mapped_column(Text, nullable=False)
    commit_sha: Mapped[str] = mapped_column(String(64), nullable=False)
    license: Mapped[str | None] = mapped_column(String(128), nullable=True)
    snapshot_path: Mapped[str] = mapped_column(Text, nullable=False)
    imported_by: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class SkillImportJob(Base):
    """外部 Skill 从安全快照到人工确认导入的持久审计状态机。"""
    __tablename__ = "skill_import_jobs"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "request_hash",
            name="uq_skill_import_jobs_workspace_request_hash",
        ),
        CheckConstraint(
            "source_type IN ('GITHUB', 'OFFLINE_ARCHIVE')",
            name="ck_skill_import_jobs_source_type",
        ),
        CheckConstraint(
            "status IN ('QUEUED', 'FETCHING', 'PREVIEWED', 'BLOCKED', 'FAILED', 'MOCKED', 'IMPORTED', 'EXPIRED')",
            name="ck_skill_import_jobs_status",
        ),
        CheckConstraint("char_length(request_hash) = 64", name="ck_skill_import_jobs_request_hash"),
        CheckConstraint("dispatch_attempt >= 1", name="ck_skill_import_jobs_dispatch_attempt"),
        CheckConstraint(
            "snapshot_hash IS NULL OR char_length(snapshot_hash) = 64",
            name="ck_skill_import_jobs_snapshot_hash",
        ),
    )

    id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_by: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    repo_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    commit_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    path: Mapped[str] = mapped_column(Text, nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    archive_snapshot_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    snapshot_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_snapshot_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    converted_snapshot_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    merge_snapshot_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    conversion_result: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    merge_result: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    diff_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    mock_result: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    dispatch_attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    celery_task_id: Mapped[str | None] = mapped_column(String(255), nullable=True, unique=True, index=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    imported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    skill_id: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("skills.id", ondelete="SET NULL"), nullable=True, index=True
    )
    version_id: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("skill_versions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    upstream_source_id: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("skill_import_sources.id", ondelete="RESTRICT"),
        nullable=True, index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class SkillEvalCase(Base):
    """Workspace 内可复用的 Skill 黄金评测用例。"""
    __tablename__ = "skill_eval_cases"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "skill_id", "name", name="uq_skill_eval_cases_workspace_skill_name"
        ),
    )

    id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    skill_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("skills.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    input: Mapped[dict] = mapped_column(JSONB, nullable=False)
    expected_trigger: Mapped[bool] = mapped_column(Boolean, nullable=False)
    expected_outputs: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class SkillEvalRun(Base):
    """某 SkillVersion 在单个黄金用例上的不可变评测结果。"""
    __tablename__ = "skill_eval_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING', 'RUNNING', 'PASSED', 'FAILED', 'ERROR')",
            name="ck_skill_eval_runs_status",
        ),
    )

    id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("skill_versions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    case_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("skill_eval_cases.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="PENDING")
    metrics: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    result: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    model: Mapped[str | None] = mapped_column(String(160), nullable=True)
    initiated_by: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


# ── WBS-7 ResearchBrief 任务输入模型 ────────────────────────────────────


class ResearchBrief(Base):
    """结构化任务输入（WBS-7）

    保存用户的完整意图：行业、地区、业务目标、任务深度、已知线索等。
    通过 tasks.research_brief_id 与任务关联。
    """
    __tablename__ = "research_briefs"

    id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="SET NULL"), nullable=True, index=True
    )
    task_id: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )  # 无 FK 约束：避免与 tasks.research_brief_id 形成循环依赖
    company_name: Mapped[str] = mapped_column(String(255), nullable=False)
    industry: Mapped[str | None] = mapped_column(String(100), nullable=True)
    region: Mapped[str | None] = mapped_column(String(100), nullable=True)
    demand_direction: Mapped[str] = mapped_column(String(255), nullable=False)
    business_goal: Mapped[str | None] = mapped_column(Text, nullable=True)
    skill_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    report_profile: Mapped[str | None] = mapped_column(String(50), nullable=True)
    depth: Mapped[str | None] = mapped_column(String(20), default="standard")
    focus_modules: Mapped[dict | None] = mapped_column(JSONB, nullable=True, default=list)
    time_range: Mapped[str | None] = mapped_column(String(50), nullable=True)
    known_clues: Mapped[dict | None] = mapped_column(JSONB, nullable=True, default=list)
    user_constraints: Mapped[dict | None] = mapped_column(JSONB, nullable=True, default=dict)
    expected_outputs: Mapped[dict | None] = mapped_column(JSONB, nullable=True, default=list)
    raw_input: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


# ── WBS-1 配置中心数据模型 ──────────────────────────────────────────────


class Setting(Base):
    """通用配置键值存储"""
    __tablename__ = "settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    category: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    value_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    value_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class LLMProvider(Base):
    """LLM Provider 配置"""
    __tablename__ = "llm_providers"
    __table_args__ = (
        CheckConstraint(
            "models_json IS NULL OR jsonb_typeof(models_json) = 'array'",
            name="ck_llm_providers_models_array",
        ),
        CheckConstraint(
            "fallback_models_json IS NULL OR jsonb_typeof(fallback_models_json) = 'array'",
            name="ck_llm_providers_fallback_models_array",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    provider_type: Mapped[str] = mapped_column(String(50), nullable=False)
    base_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    api_key_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    models_json: Mapped[list[str] | None] = mapped_column(JSONB(none_as_null=True), nullable=True)
    default_model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    fallback_models_json: Mapped[list[str] | None] = mapped_column(JSONB(none_as_null=True), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    priority: Mapped[int] = mapped_column(Integer, default=100)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=60)
    retry_count: Mapped[int] = mapped_column(Integer, default=2)
    last_test_success: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    last_tested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_test_latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_test_error_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    last_test_error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_test_config_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class SearchProvider(Base):
    """搜索 Provider 配置"""
    __tablename__ = "search_providers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    provider_type: Mapped[str] = mapped_column(String(50), nullable=False)
    api_key_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    appcode_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    app_key_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    app_secret_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    base_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    priority: Mapped[int] = mapped_column(Integer, default=100)
    daily_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    per_task_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=30)
    last_test_success: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    last_tested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_test_latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_test_error_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    last_test_error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_test_config_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class ModelRoute(Base):
    """Agent 角色 → 模型路由配置"""
    __tablename__ = "model_routes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    agent_role: Mapped[str] = mapped_column(String(50), nullable=False)
    complexity_level: Mapped[str] = mapped_column(String(20), nullable=False)
    provider_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("llm_providers.id"), nullable=True)
    model_name: Mapped[str] = mapped_column(String(100), nullable=False)
    fallback_model_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class ProviderHealth(Base):
    """Provider 健康状态（WBS-4 熔断时启用）"""
    __tablename__ = "provider_health"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider_type: Mapped[str] = mapped_column(String(50), nullable=False)  # "llm" | "search"
    provider_id: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="healthy")
    consecutive_429: Mapped[int] = mapped_column(Integer, default=0)
    consecutive_errors: Mapped[int] = mapped_column(Integer, default=0)
    last_error_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    last_error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    cooldown_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


# ── WBS-9 批量调度控制模型 ────────────────────────────────────────────────


class TaskDispatch(Base):
    """调度追踪（WBS-9）

    记录每个子任务的 Celery task ID，用于可靠的 revoke 和状态追踪。
    解决此前 cancel_batch 使用 DB Task.id 去 revoke Celery 任务的 bug。
    """
    __tablename__ = "task_dispatches"

    id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    task_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    batch_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("batches.id", ondelete="CASCADE"), nullable=False, index=True
    )
    celery_task_id: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="queued")  # queued/running/completed/failed/revoked
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class BatchImportRow(Base):
    """导入行追踪（WBS-9）

    记录 CSV/Excel 导入的每一行，含验证状态和 Dry Run 采样评分。
    创建批次后可关联到生成的 Task。
    """
    __tablename__ = "batch_import_rows"

    id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    batch_id: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("batches.id", ondelete="SET NULL"), nullable=True, index=True
    )
    row_index: Mapped[int] = mapped_column(Integer, nullable=False)
    raw_data_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    parsed_company_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    parsed_demand_direction: Mapped[str | None] = mapped_column(String(255), nullable=True)
    parsed_skill_id: Mapped[UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    validation_status: Mapped[str] = mapped_column(
        String(50), nullable=False, default="pending"  # pending/valid/warning/error
    )
    sample_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    task_id: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class WatchSubscription(Base):
    """已确认目标企业的持续增量研究订阅。"""
    __tablename__ = "watch_subscriptions"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "target_account_id",
            name="uq_watch_subscriptions_workspace_target",
        ),
        CheckConstraint("status IN ('ACTIVE', 'PAUSED', 'ARCHIVED')", name="ck_watch_subscriptions_status"),
        CheckConstraint("frequency IN ('DAILY', 'WEEKLY', 'MONTHLY')", name="ck_watch_subscriptions_frequency"),
        CheckConstraint("jsonb_typeof(topics) = 'array'", name="ck_watch_subscriptions_topics_array"),
        CheckConstraint("max_external_calls >= 0", name="ck_watch_subscriptions_external_budget"),
        CheckConstraint("max_input_tokens >= 0", name="ck_watch_subscriptions_token_budget"),
    )

    id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    target_account_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("target_accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    capability_profile_id: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("capability_profiles.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    created_by: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    root_skill_name: Mapped[str] = mapped_column(String(128), nullable=False, default="pilot-opportunity")
    topics: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    frequency: Mapped[str] = mapped_column(String(16), nullable=False)
    timezone_name: Mapped[str] = mapped_column(String(64), nullable=False, default="Asia/Shanghai")
    max_external_calls: Mapped[int] = mapped_column(Integer, nullable=False, default=20)
    max_input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=120000)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ACTIVE", index=True)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class WatchCheckRun(Base):
    """一次不可替代的增量检查运行及其变化摘要。"""
    __tablename__ = "watch_check_runs"
    __table_args__ = (
        UniqueConstraint("subscription_id", "scheduled_for", name="uq_watch_check_runs_schedule"),
        UniqueConstraint("subscription_id", "input_hash", name="uq_watch_check_runs_input"),
        CheckConstraint(
            "status IN ('PENDING', 'RUNNING', 'COMPLETED', 'PARTIAL', 'FAILED', 'SKIPPED_BUDGET')",
            name="ck_watch_check_runs_status",
        ),
        CheckConstraint("char_length(input_hash) = 64", name="ck_watch_check_runs_input_hash"),
    )

    id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    subscription_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("watch_subscriptions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    target_account_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("target_accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    previous_run_id: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("watch_check_runs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    task_id: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True, unique=True, index=True
    )
    scheduled_for: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    analysis_as_of_date: Mapped[date] = mapped_column(Date, nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="PENDING", index=True)
    budget: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    usage: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    change_summary: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class WinLossReason(Base):
    """Workspace 可治理的业务结果原因字典。"""
    __tablename__ = "win_loss_reasons"
    __table_args__ = (
        UniqueConstraint("workspace_id", "code", name="uq_win_loss_reasons_workspace_code"),
        CheckConstraint(
            "category IN ('WIN', 'LOSS', 'NO_OPPORTUNITY', 'IDENTIFICATION_ERROR')",
            name="ck_win_loss_reasons_category",
        ),
        CheckConstraint("sort_order >= 0", name="ck_win_loss_reasons_sort_order"),
    )

    id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    label: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    category: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_by: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class BusinessFeedback(Base):
    """销售人工提交的可追溯业务结果，不自动改写 Skill 或评分权重。"""
    __tablename__ = "business_feedback"
    __table_args__ = (
        UniqueConstraint("workspace_id", "request_key", name="uq_business_feedback_workspace_request"),
        CheckConstraint(
            "feedback_type IN ('SIGNAL_ACCEPTED', 'SIGNAL_REJECTED', 'CUSTOMER_VALIDATED', "
            "'CUSTOMER_INVALIDATED', 'STAGE_ADVANCED', 'WON', 'LOST', 'NO_OPPORTUNITY', "
            "'IDENTIFICATION_ERROR')",
            name="ck_business_feedback_type",
        ),
        CheckConstraint("char_length(request_hash) = 64", name="ck_business_feedback_request_hash"),
    )

    id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    target_account_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("target_accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    hypothesis_id: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("opportunity_hypotheses.id", ondelete="SET NULL"), nullable=True, index=True
    )
    opportunity_id: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("opportunities.id", ondelete="SET NULL"), nullable=True, index=True
    )
    task_id: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True, index=True
    )
    reason_id: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("win_loss_reasons.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    feedback_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    outcome_data: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    recorded_by: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    request_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class ExternalAgentRun(Base):
    """WBS-21a: 外部 Agent 执行记录（PlaywrightFieldAgent 等）

    记录每次外部 Agent（如浏览器自动化）的执行过程，包括状态、截图、URL、观察结果。
    """
    __tablename__ = "external_agent_runs"

    id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    task_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    agent_type: Mapped[str] = mapped_column(String(50), nullable=False)  # playwright_field
    target_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING")  # PENDING/OK/BLOCKED/ERROR/EMPTY
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    step_count: Mapped[int] = mapped_column(Integer, default=0)
    screenshot_paths: Mapped[dict | None] = mapped_column(JSONB, nullable=True, default=list)  # list[str]
    visited_urls: Mapped[dict | None] = mapped_column(JSONB, nullable=True, default=list)  # list[str]
    observations: Mapped[str | None] = mapped_column(Text, nullable=True)  # 观察结论文本
    blocked_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_ids: Mapped[dict | None] = mapped_column(JSONB, nullable=True, default=list)  # list[str]
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
