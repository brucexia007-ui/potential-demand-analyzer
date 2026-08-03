"""Skill V2 API 与运行期策略结构。"""
from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class EvidencePolicy(BaseModel):
    """Skill 驱动的证据充分性门槛。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    min_evidence_count: int = Field(ge=1, le=200)
    target_evidence_count: int = Field(ge=1, le=200)
    max_evidence_count: int = Field(ge=1, le=200)
    min_distinct_domains: int = Field(ge=1, le=100)
    min_trusted_sources: int = Field(ge=0, le=100)
    min_critical_claim_support: int = Field(ge=0, le=200)
    max_low_gain_batches: int = Field(ge=0, le=20)

    @model_validator(mode="after")
    def validate_evidence_count_order(self) -> "EvidencePolicy":
        if not self.min_evidence_count <= self.target_evidence_count <= self.max_evidence_count:
            raise ValueError("min、target、max evidence count 必须递增")
        if self.min_critical_claim_support > self.max_evidence_count:
            raise ValueError("min_critical_claim_support 不能大于 max_evidence_count")
        return self


class SkillCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None, min_length=1, max_length=160)
    markdown: str = Field(min_length=1, max_length=1_048_576)


class SkillVersionCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    markdown: str = Field(min_length=1, max_length=1_048_576)


class SkillVersionResponse(BaseModel):
    id: UUID
    version: int
    status: str
    content_hash: str
    compiled_spec: dict
    compiled_at: datetime | None
    published_at: datetime | None
    created_at: datetime


class SkillSummaryResponse(BaseModel):
    id: UUID
    name: str
    display_name: str
    description: str
    scope: str
    status: str
    editable: bool
    current_version_id: UUID | None
    latest_version: SkillVersionResponse | None
    created_at: datetime
    updated_at: datetime


class SkillDetailResponse(SkillSummaryResponse):
    versions: list[SkillVersionResponse]


class SkillListResponse(BaseModel):
    skills: list[SkillSummaryResponse]
    total: int


class SkillSourceResponse(BaseModel):
    skill_id: UUID
    version_id: UUID
    markdown: str


class SkillCompilePreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: str = Field(min_length=1, max_length=1_048_576)


class SkillCompilePreviewResponse(BaseModel):
    valid: bool
    compiled_spec: dict | None
    errors: list[str]
    warnings: list[str]


class SkillDryRunResponse(BaseModel):
    tool_plan: list[str]
    budget: dict[str, int]
    external_execution: bool


class RuntimeSkillResponse(BaseModel):
    name: str
    description: str
    version: int
    execution_order: list[str]
    research_skills: list[str]
    evaluation_skills: list[str]


class RuntimeSkillListResponse(BaseModel):
    skills: list[RuntimeSkillResponse]
    total: int


class SkillMutationResponse(BaseModel):
    skill: SkillSummaryResponse
    version: SkillVersionResponse | None = None


class SkillEvalCaseCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=160)
    input: dict
    expected_trigger: bool
    expected_outputs: dict = Field(default_factory=dict)


class SkillEvalCaseResponse(BaseModel):
    id: UUID
    skill_id: UUID
    name: str
    input: dict
    expected_trigger: bool
    expected_outputs: dict
    enabled: bool
    created_at: datetime


class SkillEvalRunResponse(BaseModel):
    id: UUID
    version_id: UUID
    case_id: UUID
    status: str
    metrics: dict
    result: dict
    model: str | None
    initiated_by: UUID | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime


class SkillEvalSuiteResponse(BaseModel):
    passed: bool
    version_status: str
    runs: list[SkillEvalRunResponse]


class GitHubSkillImportPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repo_url: str = Field(
        min_length=20,
        max_length=500,
        pattern=r"^https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?/?$",
    )
    commit_sha: str = Field(pattern=r"^[0-9a-fA-F]{40}$")
    path: str = Field(default="", max_length=500)


class SkillUpstreamUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    commit_sha: str = Field(pattern=r"^[0-9a-fA-F]{40}$")


class SkillImportJobResponse(BaseModel):
    id: UUID
    source_type: Literal["GITHUB", "OFFLINE_ARCHIVE"]
    repo_url: str | None
    commit_sha: str | None
    path: str
    request_hash: str
    snapshot_hash: str | None
    conversion_result: dict
    merge_result: dict
    diff_text: str
    mock_result: dict
    status: Literal["QUEUED", "FETCHING", "PREVIEWED", "BLOCKED", "FAILED", "MOCKED", "IMPORTED", "EXPIRED"]
    dispatch_attempt: int
    error_code: str | None
    error_message: str | None
    expires_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    confirmed_at: datetime | None
    imported_at: datetime | None
    skill_id: UUID | None
    version_id: UUID | None
    upstream_source_id: UUID | None
    created_at: datetime
    updated_at: datetime


class SkillImportMockResponse(BaseModel):
    job: SkillImportJobResponse
    compiled_name: str
    execution_phase: str
    synthetic_questions: list[str]
    planned_sources: list[str]
    expected_output_fields: list[str]
    network_calls: int
    model_calls: int
    filesystem_writes: int


class SkillImportConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    confirmed: bool
    conflict_action: Literal["CREATE_NEW", "CREATE_VERSION"]


class SkillImportConfirmResponse(BaseModel):
    job: SkillImportJobResponse
    skill: SkillSummaryResponse
    version: SkillVersionResponse
    created_skill: bool


class SkillGraphNodeResponse(BaseModel):
    skill_id: UUID
    version_id: UUID
    name: str
    display_name: str
    version: int
    status: str
    execution_phase: Literal["research", "evaluation"]
    allowed_tools: list[str]
    data_domains: list[Literal["external", "customer_private", "internal"]]
    editable: bool


class SkillGraphEdgeResponse(BaseModel):
    parent_version_id: UUID
    child_skill_id: UUID
    min_version: int
    condition: dict


class SkillGraphResponse(BaseModel):
    root_skill_id: UUID
    root_version_id: UUID
    nodes: list[SkillGraphNodeResponse]
    edges: list[SkillGraphEdgeResponse]
    execution_order: list[str]


class SkillGraphEdgeInputRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    child_skill_id: UUID
    min_version: int = Field(ge=1)
    condition: dict = Field(default_factory=dict)


class SkillGraphPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    edges: list[SkillGraphEdgeInputRequest] = Field(max_length=50)


class SkillGraphPreviewResponse(BaseModel):
    markdown: str
    diff_text: str
    compiled_version: int
    graph: SkillGraphResponse
