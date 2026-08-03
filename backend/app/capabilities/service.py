"""多能力档案、多产品版本的 Workspace 隔离服务。"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.capabilities.schema import (
    CreateCapabilityCaseInput,
    CreateCapabilityProductInput,
    CreateCapabilityProfileInput,
    CreateCapabilityQualificationInput,
    CreateCapabilitySolutionInput,
)
from app.db.models import (
    CapabilityCase,
    CapabilityProduct,
    CapabilityProfile,
    CapabilityQualification,
    CapabilitySolution,
    WorkspaceMember,
)


class CapabilityService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create_profile(
        self,
        *,
        workspace_id: UUID,
        created_by: UUID,
        payload: CreateCapabilityProfileInput,
    ) -> CapabilityProfile:
        self._require_member(workspace_id=workspace_id, user_id=created_by)
        profiles = list(self._session.execute(
            select(CapabilityProfile).where(CapabilityProfile.workspace_id == workspace_id).with_for_update()
        ).scalars())
        normalized_name = payload.name.strip()
        if any(profile.name == normalized_name for profile in profiles):
            raise ValueError("同一 Workspace 下能力档案名称不能重复")
        make_default = payload.is_default or not any(profile.status == "ACTIVE" for profile in profiles)
        if make_default:
            for profile in profiles:
                profile.is_default = False
            self._session.flush()
        profile = CapabilityProfile(
            workspace_id=workspace_id,
            name=normalized_name,
            legal_entity_name=payload.legal_entity_name.strip() if payload.legal_entity_name else None,
            description=payload.description.strip(),
            is_default=make_default,
            status="ACTIVE",
            created_by=created_by,
        )
        self._session.add(profile)
        self._session.flush()
        return profile

    def list_profiles(self, *, workspace_id: UUID, include_archived: bool = False) -> list[CapabilityProfile]:
        statement = select(CapabilityProfile).where(CapabilityProfile.workspace_id == workspace_id)
        if not include_archived:
            statement = statement.where(CapabilityProfile.status == "ACTIVE")
        return list(self._session.execute(
            statement.order_by(CapabilityProfile.is_default.desc(), CapabilityProfile.created_at, CapabilityProfile.id)
        ).scalars())

    def get_profile(self, *, workspace_id: UUID, profile_id: UUID) -> CapabilityProfile:
        profile = self._session.get(CapabilityProfile, profile_id)
        if profile is None:
            raise LookupError("能力档案不存在")
        if profile.workspace_id != workspace_id:
            raise PermissionError("能力档案不属于当前 Workspace")
        return profile

    def set_default(self, *, workspace_id: UUID, profile_id: UUID, updated_by: UUID) -> CapabilityProfile:
        self._require_member(workspace_id=workspace_id, user_id=updated_by)
        profiles = list(self._session.execute(
            select(CapabilityProfile).where(CapabilityProfile.workspace_id == workspace_id).with_for_update()
        ).scalars())
        target = next((profile for profile in profiles if profile.id == profile_id), None)
        if target is None:
            raise LookupError("能力档案不存在")
        if target.status != "ACTIVE":
            raise ValueError("归档档案不能设为默认")
        now = datetime.now(timezone.utc)
        for profile in profiles:
            if profile.id != target.id and profile.is_default:
                profile.is_default = False
                profile.updated_at = now
        self._session.flush()
        target.is_default = True
        target.updated_at = now
        self._session.flush()
        return target

    def archive_profile(
        self,
        *,
        workspace_id: UUID,
        profile_id: UUID,
        updated_by: UUID,
        replacement_default_id: UUID | None = None,
    ) -> CapabilityProfile:
        self._require_member(workspace_id=workspace_id, user_id=updated_by)
        profiles = list(self._session.execute(
            select(CapabilityProfile).where(CapabilityProfile.workspace_id == workspace_id).with_for_update()
        ).scalars())
        target = next((profile for profile in profiles if profile.id == profile_id), None)
        if target is None:
            raise LookupError("能力档案不存在")
        if target.status == "ARCHIVED":
            return target
        active_others = [profile for profile in profiles if profile.id != target.id and profile.status == "ACTIVE"]
        if target.is_default and active_others:
            replacement = next((profile for profile in active_others if profile.id == replacement_default_id), None)
            if replacement is None:
                raise ValueError("归档默认档案前必须选择另一个启用档案作为默认")
            target.is_default = False
            self._session.flush()
            replacement.is_default = True
        elif replacement_default_id is not None:
            raise ValueError("只有归档默认档案时可以指定替代默认档案")
        target.status = "ARCHIVED"
        target.is_default = False
        target.updated_at = datetime.now(timezone.utc)
        for product in self.list_products(workspace_id=workspace_id, profile_id=target.id, include_archived=False):
            product.status = "ARCHIVED"
            product.updated_at = target.updated_at
        for model in (CapabilitySolution, CapabilityCase, CapabilityQualification):
            for item in self._list_profile_entities(
                model=model, workspace_id=workspace_id, profile_id=target.id, include_archived=False,
            ):
                item.status = "ARCHIVED"
                item.updated_at = target.updated_at
        self._session.flush()
        return target

    def create_product(
        self,
        *,
        workspace_id: UUID,
        profile_id: UUID,
        created_by: UUID,
        payload: CreateCapabilityProductInput,
    ) -> CapabilityProduct:
        self._require_member(workspace_id=workspace_id, user_id=created_by)
        profile = self.get_profile(workspace_id=workspace_id, profile_id=profile_id)
        if profile.status != "ACTIVE":
            raise ValueError("不能向已归档能力档案添加产品")
        name = payload.name.strip()
        version_label = payload.version_label.strip()
        existing = self._session.execute(
            select(CapabilityProduct).where(
                CapabilityProduct.profile_id == profile.id,
                CapabilityProduct.name == name,
                CapabilityProduct.version_label == version_label,
            )
        ).scalar_one_or_none()
        if existing is not None:
            raise ValueError("同一档案下产品名称与版本组合不能重复")
        product = CapabilityProduct(
            workspace_id=workspace_id,
            profile_id=profile.id,
            name=name,
            product_line=payload.product_line.strip() if payload.product_line else None,
            version_label=version_label,
            summary=payload.summary.strip(),
            capabilities=[dict(item) for item in payload.capabilities],
            constraints=[dict(item) for item in payload.constraints],
            unsuitable_scenarios=[dict(item) for item in payload.unsuitable_scenarios],
            differentiators=[dict(item) for item in payload.differentiators],
            supported_regions=list(dict.fromkeys(item.strip() for item in payload.supported_regions)),
            supported_industries=list(dict.fromkeys(item.strip() for item in payload.supported_industries)),
            status=payload.status,
            effective_from=payload.effective_from,
            effective_to=payload.effective_to,
            created_by=created_by,
        )
        self._session.add(product)
        self._session.flush()
        return product

    def list_products(
        self,
        *,
        workspace_id: UUID,
        profile_id: UUID,
        include_archived: bool = False,
    ) -> list[CapabilityProduct]:
        self.get_profile(workspace_id=workspace_id, profile_id=profile_id)
        statement = select(CapabilityProduct).where(
            CapabilityProduct.workspace_id == workspace_id,
            CapabilityProduct.profile_id == profile_id,
        )
        if not include_archived:
            statement = statement.where(CapabilityProduct.status != "ARCHIVED")
        return list(self._session.execute(
            statement.order_by(CapabilityProduct.name, CapabilityProduct.created_at, CapabilityProduct.id)
        ).scalars())

    def archive_product(
        self,
        *,
        workspace_id: UUID,
        product_id: UUID,
        updated_by: UUID,
    ) -> CapabilityProduct:
        self._require_member(workspace_id=workspace_id, user_id=updated_by)
        product = self._session.get(CapabilityProduct, product_id)
        if product is None:
            raise LookupError("产品不存在")
        if product.workspace_id != workspace_id:
            raise PermissionError("产品不属于当前 Workspace")
        product.status = "ARCHIVED"
        product.updated_at = datetime.now(timezone.utc)
        self._session.flush()
        return product

    def create_solution(
        self, *, workspace_id: UUID, profile_id: UUID, created_by: UUID, payload: CreateCapabilitySolutionInput,
    ) -> CapabilitySolution:
        self._require_writable_profile(workspace_id=workspace_id, profile_id=profile_id, user_id=created_by)
        product_ids = self._validate_product_ids(
            workspace_id=workspace_id, profile_id=profile_id, product_ids=payload.product_ids,
        )
        solution = CapabilitySolution(
            workspace_id=workspace_id,
            profile_id=profile_id,
            name=payload.name.strip(),
            industry=payload.industry.strip() if payload.industry else None,
            problem_statement=payload.problem_statement.strip(),
            solution_summary=payload.solution_summary.strip(),
            product_ids=product_ids,
            constraints=[dict(item) for item in payload.constraints],
            status=payload.status,
        )
        self._session.add(solution)
        self._session.flush()
        return solution

    def list_solutions(
        self, *, workspace_id: UUID, profile_id: UUID, include_archived: bool = False,
    ) -> list[CapabilitySolution]:
        return self._list_profile_entities(
            model=CapabilitySolution, workspace_id=workspace_id, profile_id=profile_id, include_archived=include_archived,
        )

    def create_case(
        self, *, workspace_id: UUID, profile_id: UUID, created_by: UUID, payload: CreateCapabilityCaseInput,
    ) -> CapabilityCase:
        self._require_writable_profile(workspace_id=workspace_id, profile_id=profile_id, user_id=created_by)
        product_ids = self._validate_product_ids(
            workspace_id=workspace_id, profile_id=profile_id, product_ids=payload.product_ids,
        )
        case = CapabilityCase(
            workspace_id=workspace_id,
            profile_id=profile_id,
            title=payload.title.strip(),
            customer_industry=payload.customer_industry.strip() if payload.customer_industry else None,
            challenge=payload.challenge.strip(),
            outcome=payload.outcome.strip(),
            metrics=[dict(item) for item in payload.metrics],
            product_ids=product_ids,
            status=payload.status,
        )
        self._session.add(case)
        self._session.flush()
        return case

    def list_cases(
        self, *, workspace_id: UUID, profile_id: UUID, include_archived: bool = False,
    ) -> list[CapabilityCase]:
        return self._list_profile_entities(
            model=CapabilityCase, workspace_id=workspace_id, profile_id=profile_id, include_archived=include_archived,
        )

    def create_qualification(
        self, *, workspace_id: UUID, profile_id: UUID, created_by: UUID,
        payload: CreateCapabilityQualificationInput,
    ) -> CapabilityQualification:
        self._require_writable_profile(workspace_id=workspace_id, profile_id=profile_id, user_id=created_by)
        qualification = CapabilityQualification(
            workspace_id=workspace_id,
            profile_id=profile_id,
            qualification_type=payload.qualification_type,
            name=payload.name.strip(),
            issuer=payload.issuer.strip() if payload.issuer else None,
            certificate_no=payload.certificate_no.strip() if payload.certificate_no else None,
            applicable_regions=list(dict.fromkeys(item.strip() for item in payload.applicable_regions)),
            valid_from=payload.valid_from,
            valid_to=payload.valid_to,
            status=payload.status,
        )
        self._session.add(qualification)
        self._session.flush()
        return qualification

    def list_qualifications(
        self, *, workspace_id: UUID, profile_id: UUID, include_archived: bool = False,
    ) -> list[CapabilityQualification]:
        return self._list_profile_entities(
            model=CapabilityQualification, workspace_id=workspace_id, profile_id=profile_id,
            include_archived=include_archived,
        )

    def archive_portfolio_item(
        self, *, workspace_id: UUID, item_type: str, item_id: UUID, updated_by: UUID,
    ) -> CapabilitySolution | CapabilityCase | CapabilityQualification:
        self._require_member(workspace_id=workspace_id, user_id=updated_by)
        models = {
            "solution": CapabilitySolution,
            "case": CapabilityCase,
            "qualification": CapabilityQualification,
        }
        model = models.get(item_type)
        if model is None:
            raise ValueError("不支持的能力对象类型")
        item = self._session.get(model, item_id)
        if item is None:
            raise LookupError("能力对象不存在")
        if item.workspace_id != workspace_id:
            raise PermissionError("能力对象不属于当前 Workspace")
        item.status = "ARCHIVED"
        item.updated_at = datetime.now(timezone.utc)
        self._session.flush()
        return item

    def _require_writable_profile(self, *, workspace_id: UUID, profile_id: UUID, user_id: UUID) -> CapabilityProfile:
        self._require_member(workspace_id=workspace_id, user_id=user_id)
        profile = self.get_profile(workspace_id=workspace_id, profile_id=profile_id)
        if profile.status != "ACTIVE":
            raise ValueError("不能向已归档能力档案添加内容")
        return profile

    def _validate_product_ids(
        self, *, workspace_id: UUID, profile_id: UUID, product_ids: tuple[UUID, ...],
    ) -> list[str]:
        normalized = list(dict.fromkeys(product_ids))
        if not normalized:
            return []
        products = list(self._session.execute(select(CapabilityProduct).where(
            CapabilityProduct.workspace_id == workspace_id,
            CapabilityProduct.profile_id == profile_id,
            CapabilityProduct.id.in_(normalized),
            CapabilityProduct.status != "ARCHIVED",
        )).scalars())
        if {product.id for product in products} != set(normalized):
            raise ValueError("方案或案例只能关联当前档案内未归档的产品")
        return [str(item) for item in normalized]

    def _list_profile_entities(self, *, model, workspace_id: UUID, profile_id: UUID, include_archived: bool):
        self.get_profile(workspace_id=workspace_id, profile_id=profile_id)
        statement = select(model).where(model.workspace_id == workspace_id, model.profile_id == profile_id)
        if not include_archived:
            statement = statement.where(model.status != "ARCHIVED")
        return list(self._session.execute(statement.order_by(model.created_at, model.id)).scalars())

    def _require_member(self, *, workspace_id: UUID, user_id: UUID) -> None:
        member = self._session.execute(select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.user_id == user_id,
            WorkspaceMember.status == "ACTIVE",
        )).scalar_one_or_none()
        if member is None:
            raise PermissionError("当前用户不是 Workspace 活跃成员")
