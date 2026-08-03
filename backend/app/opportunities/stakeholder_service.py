"""客户决策链服务：严格区分公开推断、销售判断与客户确认。"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy.orm import Session

from app.db.models import Claim, Opportunity, OpportunityStakeholder, TargetAccount, Task
from app.opportunities.stakeholder_schema import StakeholderInput


_ROLES = {
    "ECONOMIC_BUYER", "BUSINESS_OWNER", "TECHNICAL_DECISION_MAKER",
    "SECURITY_COMPLIANCE", "PROCUREMENT", "USER", "CHAMPION", "BLOCKER", "OTHER",
}
_INFLUENCE = {"UNKNOWN", "LOW", "MEDIUM", "HIGH"}
_ATTITUDE = {"UNKNOWN", "SUPPORTIVE", "NEUTRAL", "OPPOSED"}
_RELATIONSHIP = {"UNKNOWN", "NONE", "WEAK", "MEDIUM", "STRONG"}
_TRUTH = {"PUBLIC_INFERENCE", "SALES_JUDGMENT", "CUSTOMER_CONFIRMED"}


class OpportunityStakeholderService:
    def __init__(self, db: Session) -> None:
        self._db = db

    def create(
        self,
        *,
        workspace_id: UUID,
        target_account_id: UUID,
        created_by: UUID,
        payload: StakeholderInput,
    ) -> OpportunityStakeholder:
        account = self._account(workspace_id, target_account_id)
        self._validate_opportunity(workspace_id, account.id, payload.opportunity_id)
        claim = self._validate_claim(
            workspace_id=workspace_id,
            target_account_id=account.id,
            truth_status=payload.truth_status,
            claim_id=payload.source_claim_id,
        )
        values = self._normalize(payload)
        now = datetime.now(timezone.utc)
        stakeholder = OpportunityStakeholder(
            workspace_id=workspace_id,
            target_account_id=account.id,
            created_by=created_by,
            created_at=now,
            updated_at=now,
            status="ACTIVE",
            source_claim_id=claim.id if claim is not None else None,
            **values,
        )
        self._db.add(stakeholder)
        self._db.flush()
        return stakeholder

    def update(
        self,
        *,
        workspace_id: UUID,
        stakeholder_id: UUID,
        payload: StakeholderInput,
    ) -> OpportunityStakeholder:
        stakeholder = self._stakeholder(workspace_id, stakeholder_id, lock=True)
        self._validate_opportunity(workspace_id, stakeholder.target_account_id, payload.opportunity_id)
        claim = self._validate_claim(
            workspace_id=workspace_id,
            target_account_id=stakeholder.target_account_id,
            truth_status=payload.truth_status,
            claim_id=payload.source_claim_id,
        )
        for key, value in self._normalize(payload).items():
            setattr(stakeholder, key, value)
        stakeholder.source_claim_id = claim.id if claim is not None else None
        stakeholder.status = "ACTIVE"
        stakeholder.updated_at = datetime.now(timezone.utc)
        self._db.flush()
        return stakeholder

    def archive(self, *, workspace_id: UUID, stakeholder_id: UUID) -> OpportunityStakeholder:
        stakeholder = self._stakeholder(workspace_id, stakeholder_id, lock=True)
        stakeholder.status = "ARCHIVED"
        stakeholder.updated_at = datetime.now(timezone.utc)
        self._db.flush()
        return stakeholder

    def list_for_account(
        self,
        *,
        workspace_id: UUID,
        target_account_id: UUID,
        include_archived: bool = False,
    ) -> list[OpportunityStakeholder]:
        self._account(workspace_id, target_account_id)
        query = self._db.query(OpportunityStakeholder).filter(
            OpportunityStakeholder.workspace_id == workspace_id,
            OpportunityStakeholder.target_account_id == target_account_id,
        )
        if not include_archived:
            query = query.filter(OpportunityStakeholder.status == "ACTIVE")
        return query.order_by(
            OpportunityStakeholder.role_type.asc(),
            OpportunityStakeholder.updated_at.desc(),
        ).all()

    def _account(self, workspace_id: UUID, account_id: UUID) -> TargetAccount:
        account = self._db.get(TargetAccount, account_id)
        if account is None:
            raise LookupError("目标企业不存在")
        if account.workspace_id != workspace_id:
            raise PermissionError("目标企业不属于当前 Workspace")
        return account

    def _stakeholder(
        self,
        workspace_id: UUID,
        stakeholder_id: UUID,
        *,
        lock: bool,
    ) -> OpportunityStakeholder:
        query = self._db.query(OpportunityStakeholder).filter(
            OpportunityStakeholder.id == stakeholder_id,
            OpportunityStakeholder.workspace_id == workspace_id,
        )
        stakeholder = (query.with_for_update() if lock else query).one_or_none()
        if stakeholder is None:
            if self._db.get(OpportunityStakeholder, stakeholder_id) is not None:
                raise PermissionError("利益相关者不属于当前 Workspace")
            raise LookupError("利益相关者不存在")
        return stakeholder

    def _validate_opportunity(
        self,
        workspace_id: UUID,
        account_id: UUID,
        opportunity_id: UUID | None,
    ) -> None:
        if opportunity_id is None:
            return
        opportunity = self._db.get(Opportunity, opportunity_id)
        if opportunity is None:
            raise LookupError("正式商机不存在")
        if opportunity.workspace_id != workspace_id:
            raise PermissionError("正式商机不属于当前 Workspace")
        if opportunity.target_account_id != account_id:
            raise ValueError("正式商机与目标企业不一致")

    def _validate_claim(
        self,
        *,
        workspace_id: UUID,
        target_account_id: UUID,
        truth_status: str,
        claim_id: UUID | None,
    ) -> Claim | None:
        if truth_status not in _TRUTH:
            raise ValueError("真实性状态不受支持")
        if truth_status != "SALES_JUDGMENT" and claim_id is None:
            raise ValueError("公开推断或客户确认必须引用 Claim")
        if claim_id is None:
            return None
        claim = (
            self._db.query(Claim)
            .join(Task, Task.id == Claim.task_id)
            .filter(
                Claim.id == claim_id,
                Claim.workspace_id == workspace_id,
                Task.workspace_id == workspace_id,
                Task.target_account_id == target_account_id,
            )
            .one_or_none()
        )
        if claim is None:
            raise ValueError("只能引用当前目标企业的 Claim")
        if truth_status == "CUSTOMER_CONFIRMED" and claim.status != "CUSTOMER_CONFIRMED":
            raise ValueError("客户确认的利益相关者必须引用 CUSTOMER_CONFIRMED Claim")
        if truth_status == "PUBLIC_INFERENCE" and claim.status not in {"SUPPORTED", "CUSTOMER_CONFIRMED"}:
            raise ValueError("公开推断必须引用已支持的 Claim")
        return claim

    @staticmethod
    def _normalize(payload: StakeholderInput) -> dict:
        if payload.role_type not in _ROLES:
            raise ValueError("利益相关者角色不受支持")
        if payload.influence not in _INFLUENCE:
            raise ValueError("影响力不受支持")
        if payload.attitude not in _ATTITUDE:
            raise ValueError("态度不受支持")
        if payload.relationship_strength not in _RELATIONSHIP:
            raise ValueError("关系强度不受支持")
        return {
            "opportunity_id": payload.opportunity_id,
            "role_type": payload.role_type,
            "full_name": OpportunityStakeholderService._optional(payload.full_name, 255, "姓名"),
            "role_title": OpportunityStakeholderService._optional(payload.role_title, 255, "职位"),
            "department": OpportunityStakeholderService._optional(payload.department, 255, "部门"),
            "influence": payload.influence,
            "attitude": payload.attitude,
            "goals": OpportunityStakeholderService._text(payload.goals, 4000, "目标"),
            "concerns": OpportunityStakeholderService._text(payload.concerns, 4000, "顾虑"),
            "relationship_strength": payload.relationship_strength,
            "truth_status": payload.truth_status,
            "communication_strategy": OpportunityStakeholderService._text(
                payload.communication_strategy, 4000, "沟通策略"
            ),
        }

    @staticmethod
    def _optional(value: str | None, limit: int, label: str) -> str | None:
        text = (value or "").strip()
        if len(text) > limit:
            raise ValueError(f"{label}不得超过 {limit} 个字符")
        return text or None

    @staticmethod
    def _text(value: str, limit: int, label: str) -> str:
        text = value.strip()
        if len(text) > limit:
            raise ValueError(f"{label}不得超过 {limit} 个字符")
        return text
