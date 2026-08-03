"""企业能力档案与产品版本的强类型输入契约。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID


ProductStatus = Literal["DRAFT", "ACTIVE"]
QualificationStatus = Literal["DRAFT", "ACTIVE"]
QualificationType = Literal["CERTIFICATION", "QUALIFICATION", "LICENSE", "SECURITY", "OTHER"]


@dataclass(frozen=True)
class CreateCapabilityProfileInput:
    name: str
    legal_entity_name: str | None = None
    description: str = ""
    is_default: bool = False

    def __post_init__(self) -> None:
        if not self.name.strip() or len(self.name.strip()) > 255:
            raise ValueError("能力档案名称必须为 1 至 255 个字符")
        if self.legal_entity_name is not None and len(self.legal_entity_name.strip()) > 500:
            raise ValueError("企业主体名称不能超过 500 个字符")
        if len(self.description.strip()) > 10_000:
            raise ValueError("能力档案描述不能超过 10000 个字符")


@dataclass(frozen=True)
class CreateCapabilityProductInput:
    name: str
    version_label: str
    summary: str = ""
    product_line: str | None = None
    capabilities: tuple[dict, ...] = ()
    constraints: tuple[dict, ...] = ()
    unsuitable_scenarios: tuple[dict, ...] = ()
    differentiators: tuple[dict, ...] = ()
    supported_regions: tuple[str, ...] = ()
    supported_industries: tuple[str, ...] = ()
    status: ProductStatus = "DRAFT"
    effective_from: datetime | None = None
    effective_to: datetime | None = None

    def __post_init__(self) -> None:
        if not self.name.strip() or len(self.name.strip()) > 255:
            raise ValueError("产品名称必须为 1 至 255 个字符")
        if not self.version_label.strip() or len(self.version_label.strip()) > 100:
            raise ValueError("产品版本必须为 1 至 100 个字符")
        if len(self.summary.strip()) > 20_000:
            raise ValueError("产品摘要不能超过 20000 个字符")
        if self.status not in {"DRAFT", "ACTIVE"}:
            raise ValueError("产品初始状态只能为 DRAFT 或 ACTIVE")
        if self.status == "ACTIVE" and (not self.summary.strip() or not self.capabilities):
            raise ValueError("启用产品必须填写摘要和至少一项能力")
        if self.effective_from and self.effective_to and self.effective_to <= self.effective_from:
            raise ValueError("产品失效时间必须晚于生效时间")
        for values, label in (
            (self.supported_regions, "支持地区"),
            (self.supported_industries, "支持行业"),
        ):
            if any(not item.strip() or len(item.strip()) > 255 for item in values):
                raise ValueError(f"{label}存在空值或超长值")


@dataclass(frozen=True)
class CreateCapabilitySolutionInput:
    name: str
    industry: str | None = None
    problem_statement: str = ""
    solution_summary: str = ""
    product_ids: tuple[UUID, ...] = ()
    constraints: tuple[dict, ...] = ()
    status: ProductStatus = "DRAFT"

    def __post_init__(self) -> None:
        if not self.name.strip() or len(self.name.strip()) > 255:
            raise ValueError("方案名称必须为 1 至 255 个字符")
        if self.status == "ACTIVE" and (not self.problem_statement.strip() or not self.solution_summary.strip()):
            raise ValueError("启用方案必须填写客户问题与方案摘要")


@dataclass(frozen=True)
class CreateCapabilityCaseInput:
    title: str
    customer_industry: str | None = None
    challenge: str = ""
    outcome: str = ""
    metrics: tuple[dict, ...] = ()
    product_ids: tuple[UUID, ...] = ()
    status: ProductStatus = "DRAFT"

    def __post_init__(self) -> None:
        if not self.title.strip() or len(self.title.strip()) > 500:
            raise ValueError("案例标题必须为 1 至 500 个字符")
        if self.status == "ACTIVE" and (not self.challenge.strip() or not self.outcome.strip()):
            raise ValueError("启用案例必须填写客户挑战与实施结果")


@dataclass(frozen=True)
class CreateCapabilityQualificationInput:
    qualification_type: QualificationType
    name: str
    issuer: str | None = None
    certificate_no: str | None = None
    applicable_regions: tuple[str, ...] = ()
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    status: QualificationStatus = "DRAFT"

    def __post_init__(self) -> None:
        if self.qualification_type not in {"CERTIFICATION", "QUALIFICATION", "LICENSE", "SECURITY", "OTHER"}:
            raise ValueError("不支持的资质类型")
        if not self.name.strip() or len(self.name.strip()) > 500:
            raise ValueError("资质名称必须为 1 至 500 个字符")
        if self.valid_from and self.valid_to and self.valid_to <= self.valid_from:
            raise ValueError("资质失效时间必须晚于生效时间")
        if self.status == "ACTIVE" and self.valid_to and self.valid_to <= datetime.now(self.valid_to.tzinfo):
            raise ValueError("已失效资质不能直接启用")
