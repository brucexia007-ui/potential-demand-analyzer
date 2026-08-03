"""G5 产品适配：硬门槛、加权推荐和置信度三层裁决。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timezone
import json
import re
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import CapabilityProduct, CapabilityProfile, CapabilityQualification


PRODUCT_FIT_ALGORITHM_VERSION = "product-fit/v1"


@dataclass(frozen=True)
class ProductFitAssessment:
    fit_verified: bool
    hard_blocker: bool
    recommendation_score: float
    confidence: float
    information_completeness: float
    matched_product_ids: tuple[UUID, ...]
    matched_requirements: tuple[str, ...]
    unmatched_requirements: tuple[str, ...]
    blockers: tuple[str, ...]
    missing_information: tuple[str, ...]
    positive_factors: tuple[str, ...]
    negative_factors: tuple[str, ...]


class ProductFitService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def assess(
        self,
        *,
        workspace_id: UUID,
        profile_id: UUID,
        requirement_keys: tuple[str, ...],
        target_industry: str | None,
        target_region: str | None,
        mandatory_qualifications: tuple[str, ...],
        analysis_as_of_date: date | datetime,
        candidate_product_ids: tuple[UUID, ...] | None = None,
    ) -> ProductFitAssessment:
        as_of = self._analysis_instant(analysis_as_of_date)
        requirements = tuple(dict.fromkeys(item.strip() for item in requirement_keys if item.strip()))
        required_qualifications = tuple(dict.fromkeys(
            item.strip() for item in mandatory_qualifications if item.strip()
        ))
        profile = self._session.get(CapabilityProfile, profile_id)
        if profile is None or profile.workspace_id != workspace_id or profile.status != "ACTIVE":
            return self._blocked("能力档案不存在、已归档或不属于当前 Workspace", requirements)
        selected_product_ids = tuple(dict.fromkeys(candidate_product_ids or ()))
        product_statement = select(CapabilityProduct).where(
            CapabilityProduct.workspace_id == workspace_id,
            CapabilityProduct.profile_id == profile_id,
            CapabilityProduct.status == "ACTIVE",
        )
        if candidate_product_ids is not None:
            if not selected_product_ids:
                return ProductFitAssessment(
                    fit_verified=False, hard_blocker=False, recommendation_score=0.0,
                    confidence=1.0, information_completeness=1.0,
                    matched_product_ids=(), matched_requirements=(),
                    unmatched_requirements=requirements, blockers=(),
                    missing_information=("尚未选择候选产品版本",),
                    positive_factors=(), negative_factors=("允许明确保留无匹配结果",),
                )
            product_statement = product_statement.where(
                CapabilityProduct.id.in_(selected_product_ids)
            )
        loaded_products = list(self._session.execute(product_statement).scalars())
        if candidate_product_ids is not None and len(loaded_products) != len(selected_product_ids):
            return self._blocked("选定产品不存在、未启用或不属于当前能力档案", requirements)
        products = [product for product in loaded_products if self._active_on(
            effective_from=product.effective_from,
            effective_to=product.effective_to,
            as_of=as_of,
        )]
        if candidate_product_ids is not None and len(products) != len(loaded_products):
            return self._blocked("选定产品版本在分析日无效", requirements)
        if not products:
            return self._blocked("能力档案中没有在分析日有效的已启用产品", requirements)
        if not requirements:
            return ProductFitAssessment(
                fit_verified=False, hard_blocker=False, recommendation_score=0.0, confidence=0.2,
                information_completeness=0.25, matched_product_ids=(), matched_requirements=(),
                unmatched_requirements=(), blockers=(), missing_information=("缺少已证实的客户需求/缺口",),
                positive_factors=(), negative_factors=("没有客户需求时禁止仅凭产品生成商机",),
            )

        qualification_blocker, qualification_unknown = self._qualification_gate(
            workspace_id=workspace_id,
            profile_id=profile_id,
            mandatory_qualifications=required_qualifications,
            target_region=target_region,
            as_of=as_of,
        )
        if qualification_blocker:
            return self._blocked(qualification_blocker, requirements)
        if qualification_unknown:
            return ProductFitAssessment(
                fit_verified=False, hard_blocker=False, recommendation_score=0.0,
                confidence=0.2, information_completeness=0.5,
                matched_product_ids=(), matched_requirements=(),
                unmatched_requirements=requirements, blockers=(),
                missing_information=(qualification_unknown,), positive_factors=(),
                negative_factors=("强制资质适用范围尚未确认",),
            )

        candidates: list[tuple[float, CapabilityProduct, set[str], list[str]]] = []
        product_blockers: list[str] = []
        missing: set[str] = set()
        for product in products:
            blockers = self._hard_blockers(
                product=product, target_industry=target_industry, target_region=target_region,
            )
            if blockers:
                product_blockers.extend(f"{product.name} {product.version_label}：{item}" for item in blockers)
                continue
            if product.supported_industries and not target_industry:
                missing.add("目标企业行业")
            if product.supported_regions and not target_region:
                missing.add("目标企业地区")
            capability_text = json.dumps({
                "name": product.name,
                "summary": product.summary,
                "capabilities": product.capabilities,
                "differentiators": product.differentiators,
            }, ensure_ascii=False)
            matched = {
                requirement for requirement in requirements
                if self._similarity(requirement, capability_text) >= 0.25
            }
            coverage = len(matched) / len(requirements)
            industry_fit = self._scope_score(product.supported_industries, target_industry)
            region_fit = self._scope_score(product.supported_regions, target_region)
            capability_quality = 1.0 if product.capabilities and product.summary else 0.5
            score = 100 * (
                0.55 * coverage + 0.15 * industry_fit + 0.15 * region_fit + 0.15 * capability_quality
            )
            candidates.append((score, product, matched, blockers))

        if not candidates:
            return ProductFitAssessment(
                fit_verified=False, hard_blocker=True, recommendation_score=0.0, confidence=0.9,
                information_completeness=1.0 if target_industry and target_region else 0.7,
                matched_product_ids=(), matched_requirements=(), unmatched_requirements=requirements,
                blockers=tuple(product_blockers), missing_information=tuple(sorted(missing)),
                positive_factors=(), negative_factors=("所有已启用产品均命中硬性适用边界",),
            )

        candidates.sort(key=lambda item: item[0], reverse=True)
        best_score, best_product, matched, _ = candidates[0]
        unmatched = tuple(item for item in requirements if item not in matched)
        completeness = (0.5 + (0.25 if target_industry else 0.0) + (0.25 if target_region else 0.0))
        confidence = min(1.0, 0.45 + 0.25 * completeness + 0.3 * (len(matched) / len(requirements)))
        fit_verified = not unmatched and best_score >= 60 and confidence >= 0.6
        return ProductFitAssessment(
            fit_verified=fit_verified,
            hard_blocker=False,
            recommendation_score=round(best_score, 1),
            confidence=round(confidence, 3),
            information_completeness=round(completeness, 3),
            matched_product_ids=(best_product.id,),
            matched_requirements=tuple(item for item in requirements if item in matched),
            unmatched_requirements=unmatched,
            blockers=tuple(product_blockers),
            missing_information=tuple(sorted(missing)),
            positive_factors=(f"最佳候选产品：{best_product.name} {best_product.version_label}",),
            negative_factors=((f"仍有 {len(unmatched)} 项需求未被产品能力覆盖",) if unmatched else ()),
        )

    @staticmethod
    def _hard_blockers(
        *, product: CapabilityProduct, target_industry: str | None, target_region: str | None,
    ) -> list[str]:
        blockers: list[str] = []
        if target_industry and product.supported_industries and not ProductFitService._matches_scope(
            product.supported_industries, target_industry,
        ):
            blockers.append("目标行业不在支持范围")
        if target_region and product.supported_regions and not ProductFitService._matches_scope(
            product.supported_regions, target_region,
        ):
            blockers.append("目标地区不在交付范围")
        for constraint in product.constraints:
            if not constraint.get("hard"):
                continue
            constraint_type = str(constraint.get("type") or "").lower()
            values = constraint.get("values") or [constraint.get("value")]
            normalized = [str(item).strip() for item in values if item]
            if constraint_type == "prohibited_industry" and target_industry and ProductFitService._matches_scope(normalized, target_industry):
                blockers.append("命中禁止服务行业")
            if constraint_type == "prohibited_region" and target_region and ProductFitService._matches_scope(normalized, target_region):
                blockers.append("命中禁止交付地区")
        return blockers

    @staticmethod
    def _scope_score(scopes: list[str], target: str | None) -> float:
        if not scopes:
            return 1.0
        if not target:
            return 0.5
        return 1.0 if ProductFitService._matches_scope(scopes, target) else 0.0

    @staticmethod
    def _matches_scope(scopes: list[str], target: str) -> bool:
        normalized_target = target.strip().lower()
        return any(
            scope.strip().lower() in normalized_target or normalized_target in scope.strip().lower()
            for scope in scopes
        )

    def _qualification_gate(
        self,
        *,
        workspace_id: UUID,
        profile_id: UUID,
        mandatory_qualifications: tuple[str, ...],
        target_region: str | None,
        as_of: datetime,
    ) -> tuple[str | None, str | None]:
        if not mandatory_qualifications:
            return None, None
        qualifications = [item for item in self._session.execute(
            select(CapabilityQualification).where(
                CapabilityQualification.workspace_id == workspace_id,
                CapabilityQualification.profile_id == profile_id,
                CapabilityQualification.status == "ACTIVE",
            )
        ).scalars() if self._active_on(
            effective_from=item.valid_from,
            effective_to=item.valid_to,
            as_of=as_of,
        )]
        missing: list[str] = []
        unknown_region: list[str] = []
        for required in mandatory_qualifications:
            named = [item for item in qualifications if self._text_matches(item.name, required)]
            if not named:
                missing.append(required)
                continue
            if target_region is None and all(item.applicable_regions for item in named):
                unknown_region.append(required)
                continue
            if target_region is not None and not any(
                not item.applicable_regions
                or self._matches_scope(item.applicable_regions, target_region)
                for item in named
            ):
                missing.append(required)
        if missing:
            return f"缺少在分析日及目标地区有效的强制资质：{'、'.join(missing)}", None
        if unknown_region:
            return None, f"缺少目标企业地区，无法确认强制资质适用性：{'、'.join(unknown_region)}"
        return None, None

    @staticmethod
    def _analysis_instant(value: date | datetime) -> datetime:
        if isinstance(value, datetime):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("analysis_as_of_date 必须携带时区")
            return value
        return datetime.combine(value, time.min, tzinfo=timezone.utc)

    @staticmethod
    def _active_on(
        *, effective_from: datetime | None, effective_to: datetime | None, as_of: datetime
    ) -> bool:
        def aware(value: datetime | None) -> datetime | None:
            if value is None:
                return None
            return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value

        start = aware(effective_from)
        end = aware(effective_to)
        return (start is None or start <= as_of) and (end is None or end > as_of)

    @staticmethod
    def _text_matches(left: str, right: str) -> bool:
        normalized_left = re.sub(r"\s+", "", left).casefold()
        normalized_right = re.sub(r"\s+", "", right).casefold()
        return normalized_left in normalized_right or normalized_right in normalized_left

    @staticmethod
    def _similarity(left: str, right: str) -> float:
        left_tokens = ProductFitService._tokens(left)
        right_tokens = ProductFitService._tokens(right)
        return len(left_tokens & right_tokens) / max(1, len(left_tokens))

    @staticmethod
    def _tokens(value: str) -> set[str]:
        normalized = re.sub(r"\s+", "", value.lower())
        chinese = "".join(re.findall(r"[\u4e00-\u9fff]", normalized))
        latin = set(re.findall(r"[a-z0-9][a-z0-9._-]+", normalized))
        return latin | {chinese[index:index + 2] for index in range(max(0, len(chinese) - 1))}

    @staticmethod
    def _blocked(reason: str, requirements: tuple[str, ...]) -> ProductFitAssessment:
        return ProductFitAssessment(
            fit_verified=False, hard_blocker=True, recommendation_score=0.0, confidence=1.0,
            information_completeness=1.0, matched_product_ids=(), matched_requirements=(),
            unmatched_requirements=requirements, blockers=(reason,), missing_information=(),
            positive_factors=(), negative_factors=(reason,),
        )
