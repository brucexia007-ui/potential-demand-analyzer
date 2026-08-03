"""WBS-32-02：Workspace 与目标企业的最小服务层。"""
from __future__ import annotations

from dataclasses import dataclass
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db.models import TargetAccount, User, Workspace, WorkspaceMember
from app.target_accounts.schema import TargetAccountCreateInput


@dataclass(frozen=True)
class TargetAccountCreateResult:
    created: bool
    account: TargetAccount | None
    candidates: tuple[TargetAccount, ...] = ()


class WorkspaceService:
    """只处理默认工作区归属和目标企业主数据，不隐式跨 Workspace 操作。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_or_create_default_workspace(self, user: User) -> Workspace:
        workspace_id = uuid5(NAMESPACE_URL, f"kanyikan:workspace:{user.id}")
        workspace = self._session.get(Workspace, workspace_id)
        if workspace is None:
            workspace = Workspace(
                id=workspace_id,
                name=f"{user.username} 的默认工作区",
                status="ACTIVE",
                default_model_policy={},
            )
            self._session.add(workspace)
            self._session.flush()

        membership = (
            self._session.query(WorkspaceMember)
            .filter(
                WorkspaceMember.workspace_id == workspace.id,
                WorkspaceMember.user_id == user.id,
            )
            .one_or_none()
        )
        if membership is None:
            self._session.add(WorkspaceMember(
                workspace_id=workspace.id,
                user_id=user.id,
                role="OWNER",
                status="ACTIVE",
            ))
            self._session.flush()
        self._ensure_qualification_framework(workspace=workspace, user=user)
        return workspace

    def _ensure_qualification_framework(self, *, workspace: Workspace, user: User) -> None:
        """全新 Workspace 至少提供一套可直接使用、后续可版本化替换的资格框架。"""
        from app.db.models import OpportunityQualificationFramework
        from app.opportunities.qualification_schema import (
            QualificationBlockerRule,
            QualificationCriterionDefinition,
            QualificationFrameworkPublishInput,
        )
        from app.opportunities.qualification_service import OpportunityQualificationService

        published = (
            self._session.query(OpportunityQualificationFramework.id)
            .filter(
                OpportunityQualificationFramework.workspace_id == workspace.id,
                OpportunityQualificationFramework.status == "PUBLISHED",
            )
            .first()
        )
        if published is not None:
            return
        OpportunityQualificationService(self._session).publish_framework(
            workspace_id=workspace.id,
            published_by=user.id,
            payload=QualificationFrameworkPublishInput(
                framework_key="SYSTEM_PRE_SALES_DEFAULT",
                name="默认售前商机资格标准",
                methodology="HYBRID",
                criteria=(
                    QualificationCriterionDefinition("customer_problem", "客户问题已验证", 2, True),
                    QualificationCriterionDefinition("business_impact", "业务影响与优先级", 1, True),
                    QualificationCriterionDefinition("timing_window", "时机与采购窗口", 1, True),
                    QualificationCriterionDefinition("stakeholder_access", "关键角色可触达", 1, False),
                    QualificationCriterionDefinition("budget_path", "预算或资金路径", 1, False),
                    QualificationCriterionDefinition("decision_process", "决策与采购流程", 1, False),
                    QualificationCriterionDefinition("delivery_feasibility", "交付与合规可行", 2, True),
                ),
                hard_blocker_rules=(
                    QualificationBlockerRule(
                        "customer_problem",
                        "CUSTOMER_PROBLEM_REFUTED",
                        "客户已否定该问题或需求。",
                    ),
                    QualificationBlockerRule(
                        "delivery_feasibility",
                        "DELIVERY_BLOCKED",
                        "存在无法补齐的交付、资质、安全或合规阻断。",
                    ),
                ),
                minimum_score=0.65,
                minimum_completeness=0.7,
            ),
        )

    def get_or_create_default_workspace_for_user_id(self, user_id: UUID | str) -> Workspace:
        user = self._session.get(User, user_id)
        if user is None:
            raise ValueError("任务创建者不存在，无法确定 Workspace")
        return self.get_or_create_default_workspace(user)

    def require_active_membership(self, workspace_id: UUID, user_id: UUID) -> WorkspaceMember:
        membership = (
            self._session.query(WorkspaceMember)
            .filter(
                WorkspaceMember.workspace_id == workspace_id,
                WorkspaceMember.user_id == user_id,
                WorkspaceMember.status == "ACTIVE",
            )
            .one_or_none()
        )
        if membership is None:
            raise PermissionError("用户不属于当前 Workspace")
        return membership

    def create_target_account(
        self,
        *,
        workspace_id: UUID,
        owner_user_id: UUID,
        request: TargetAccountCreateInput,
    ) -> TargetAccountCreateResult:
        self.require_active_membership(workspace_id, owner_user_id)
        input_name = " ".join(request.input_name.split())
        if not input_name:
            raise ValueError("企业名称不能为空")

        candidates = (
            self._session.query(TargetAccount)
            .filter(
                TargetAccount.workspace_id == workspace_id,
                func.lower(func.btrim(TargetAccount.input_name)) == input_name.lower(),
            )
            .order_by(TargetAccount.created_at.asc())
            .all()
        )
        if candidates:
            return TargetAccountCreateResult(
                created=False,
                account=None,
                candidates=tuple(candidates),
            )

        parent_id = UUID(request.parent_id) if request.parent_id else None
        account = TargetAccount(
            workspace_id=workspace_id,
            owner_user_id=owner_user_id,
            input_name=input_name,
            official_name=request.official_name,
            website=request.website,
            credit_code=request.credit_code,
            industry=request.industry,
            region=request.region,
            stock_code=request.stock_code,
            parent_id=parent_id,
            status="UNRESOLVED",
        )
        self._session.add(account)
        self._session.flush()
        return TargetAccountCreateResult(created=True, account=account)
