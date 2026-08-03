"""WBS-34-17：自动商机线索发现计划的预览、确认与一次性消费。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from typing import Literal
from uuid import UUID

from sqlalchemy.orm import Session

from app.db.models import (
    CapabilityProduct,
    CapabilityProfile,
    DiscoveryResearchPlan,
    TargetAccount,
    Task,
    TaskStatus,
)
from app.execution.outbox_repository import OutboxRepository
from app.skills.service import SkillService


ResearchDepth = Literal["quick", "standard", "deep"]
_DEPTH_RATIO: dict[str, float] = {"quick": 0.4, "standard": 0.75, "deep": 1.0}


class DiscoveryResearchPlanService:
    """计划内容由服务端生成并持久化；执行只能消费同一份已确认快照。"""

    def __init__(self, db: Session) -> None:
        self._db = db

    def create_preview(
        self,
        *,
        workspace_id: UUID,
        created_by: UUID,
        target_account_id: UUID,
        capability_profile_id: UUID,
        root_skill_name: str,
        demand_direction: str,
        depth: ResearchDepth,
    ) -> DiscoveryResearchPlan:
        target = self._db.get(TargetAccount, target_account_id)
        if target is None or target.workspace_id != workspace_id:
            raise LookupError("目标企业不存在或不属于当前 Workspace")
        if target.status == "ARCHIVED":
            raise ValueError("已归档目标企业不能创建研究计划")

        profile = self._db.get(CapabilityProfile, capability_profile_id)
        if profile is None or profile.workspace_id != workspace_id:
            raise LookupError("能力档案不存在或不属于当前 Workspace")
        if profile.status != "ACTIVE":
            raise ValueError("自动商机线索发现只能使用 ACTIVE 能力档案")

        direction = demand_direction.strip()
        if not direction:
            raise ValueError("研究方向不能为空")
        runtime = SkillService(self._db).runtime_catalog(
            workspace_id=workspace_id
        ).load_for_execution(root_skill_name, {
            "research_mode": "OPPORTUNITY_DISCOVERY",
            "industry": target.industry,
            "region": target.region,
            "product_selected": True,
        })
        products = (
            self._db.query(CapabilityProduct)
            .filter(
                CapabilityProduct.workspace_id == workspace_id,
                CapabilityProduct.profile_id == capability_profile_id,
                CapabilityProduct.status == "ACTIVE",
            )
            .order_by(CapabilityProduct.name, CapabilityProduct.version_label)
            .all()
        )
        if not products:
            raise ValueError("能力档案至少需要一个 ACTIVE 产品才能启动自动商机线索发现")

        dimensions = [
            {
                "skill_name": name,
                "description": runtime.get(name).description,
                "questions": list(runtime.get(name).questions),
                "sources": list(runtime.get(name).sources),
            }
            for name in runtime.research_skills
        ]
        evaluation_skills = [
            {"skill_name": name, "description": runtime.get(name).description}
            for name in runtime.evaluation_skills
        ]
        declared_external_calls = sum(item.budget.get("max_external_calls", 0) for item in runtime.skills)
        declared_input_tokens = sum(item.budget.get("max_input_tokens", 0) for item in runtime.skills)
        ratio = _DEPTH_RATIO[depth]
        estimated_calls = max(1, round(declared_external_calls * ratio)) if declared_external_calls else 0
        estimated_input_tokens = round(declared_input_tokens * ratio)
        target_name = target.official_name or target.input_name
        product_scope = [
            {
                "id": str(item.id),
                "name": item.name,
                "version_label": item.version_label,
                "product_line": item.product_line,
            }
            for item in products
        ]
        snapshot = {
            "research_mode": "OPPORTUNITY_DISCOVERY",
            "target": {
                "id": str(target.id),
                "input_name": target.input_name,
                "official_name": target.official_name,
                "website": target.website,
                "credit_code": target.credit_code,
                "industry": target.industry,
                "region": target.region,
                "status": target.status,
            },
            "capability_profile": {
                "id": str(profile.id),
                "name": profile.name,
                "legal_entity_name": profile.legal_entity_name,
                "products": product_scope,
            },
            "research_hypotheses": [
                f"待验证：{target_name}存在与“{direction}”相关且处于有效时间窗口的客户问题或需求信号。",
                f"待验证：能力档案“{profile.name}”中的至少一个产品能够通过硬门槛并形成可验证的适配关系。",
                "反向假设：客户维持现状、自研、延期或暂无投资可能比采购任何供应商更合理。",
            ],
            "skill": {
                "root_name": runtime.root.name,
                "version": runtime.version,
                "description": runtime.root.description,
                "execution_order": list(runtime.execution_order),
                "research_dimensions": dimensions,
                "evaluation_skills": evaluation_skills,
            },
            "scope": {"demand_direction": direction, "depth": depth},
            "estimate": {
                "external_calls": estimated_calls,
                "input_tokens": estimated_input_tokens,
                "duration_minutes": {
                    "minimum": round(estimated_calls * 3 / 60, 1),
                    "maximum": round(estimated_calls * 12 / 60, 1),
                },
                "monetary_cost": {
                    "status": "UNAVAILABLE",
                    "amount": None,
                    "currency": None,
                    "reason": "当前模型与搜索供应商没有统一价目表，禁止伪造金额估算。",
                },
                "basis": "依据已发布 Skill 的预算上限和研究深度估算，不是执行承诺。",
            },
            "confirmation": {
                "required": depth in {"standard", "deep"},
                "reasons": [
                    "目标企业主体决定所有外部证据、Claim 与商机判断的归属。",
                    "能力档案和产品范围决定后续产品适配、硬阻断与能力缺口判断。",
                    "研究深度会直接影响外部调用次数、耗时与上下文预算。",
                ],
            },
        }
        encoded = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        now = datetime.now(timezone.utc)
        requires_confirmation = depth in {"standard", "deep"}
        plan = DiscoveryResearchPlan(
            workspace_id=workspace_id,
            target_account_id=target.id,
            capability_profile_id=profile.id,
            created_by=created_by,
            root_skill_name=runtime.root.name,
            skill_version=runtime.version,
            depth=depth,
            demand_direction=direction,
            requires_confirmation=requires_confirmation,
            input_hash=sha256(encoded).hexdigest(),
            snapshot=snapshot,
            status="PREVIEWED" if requires_confirmation else "CONFIRMED",
            expires_at=now + timedelta(minutes=30),
            confirmed_at=None if requires_confirmation else now,
        )
        self._db.add(plan)
        self._db.flush()
        return plan

    def confirm(self, *, workspace_id: UUID, plan_id: UUID, confirmed_by: UUID) -> DiscoveryResearchPlan:
        plan = self._owned_plan(workspace_id=workspace_id, plan_id=plan_id)
        now = datetime.now(timezone.utc)
        if plan.expires_at <= now:
            plan.status = "EXPIRED"
            plan.updated_at = now
            self._db.flush()
            raise ValueError("研究计划已过期，请重新生成预览")
        if plan.status == "CONSUMED":
            raise ValueError("研究计划已经被任务消费，不能重复确认")
        if plan.status == "EXPIRED":
            raise ValueError("研究计划已过期，请重新生成预览")
        if plan.status == "CONFIRMED":
            return plan
        if plan.created_by != confirmed_by:
            raise PermissionError("只有计划创建者可以确认该研究计划")
        plan.status = "CONFIRMED"
        plan.confirmed_at = now
        plan.updated_at = now
        self._db.flush()
        return plan

    def require_executable(self, *, workspace_id: UUID, plan_id: UUID, requested_by: UUID) -> DiscoveryResearchPlan:
        plan = self._owned_plan(workspace_id=workspace_id, plan_id=plan_id)
        now = datetime.now(timezone.utc)
        if plan.expires_at <= now:
            plan.status = "EXPIRED"
            plan.updated_at = now
            self._db.flush()
            raise ValueError("研究计划已过期，请重新生成预览")
        if plan.created_by != requested_by:
            raise PermissionError("研究计划不属于当前用户")
        if plan.status != "CONFIRMED":
            raise ValueError("标准或深度研究必须先确认计划")
        return plan

    def consume(self, *, plan: DiscoveryResearchPlan) -> None:
        if plan.status != "CONFIRMED":
            raise ValueError("只有已确认的研究计划可以被任务消费")
        now = datetime.now(timezone.utc)
        plan.status = "CONSUMED"
        plan.consumed_at = now
        plan.updated_at = now
        self._db.flush()

    def launch(
        self,
        *,
        workspace_id: UUID,
        plan_id: UUID,
        requested_by: UUID,
    ) -> tuple[Task, bool]:
        """锁定并一次性消费计划；相同计划的重试返回原任务，不重复创建。"""
        plan = (
            self._db.query(DiscoveryResearchPlan)
            .filter(
                DiscoveryResearchPlan.id == plan_id,
                DiscoveryResearchPlan.workspace_id == workspace_id,
            )
            .with_for_update()
            .one_or_none()
        )
        if plan is None:
            raise LookupError("研究计划不存在或不属于当前 Workspace")
        existing = self._db.query(Task).filter(Task.discovery_plan_id == plan.id).one_or_none()
        if existing is not None:
            return existing, False
        self.require_executable(
            workspace_id=workspace_id,
            plan_id=plan.id,
            requested_by=requested_by,
        )
        target = self._db.get(TargetAccount, plan.target_account_id)
        if target is None or target.workspace_id != workspace_id or target.status == "ARCHIVED":
            raise ValueError("计划中的目标企业已失效，请重新生成预览")
        profile = self._db.get(CapabilityProfile, plan.capability_profile_id)
        if profile is None or profile.workspace_id != workspace_id or profile.status != "ACTIVE":
            raise ValueError("计划中的能力档案已失效，请重新生成预览")

        task = Task(
            user_id=requested_by,
            workspace_id=workspace_id,
            target_account_id=target.id,
            company_name=target.official_name or target.input_name,
            demand_direction=plan.demand_direction,
            status=TaskStatus.PENDING,
            desired_state="RUNNING",
            observed_state="PENDING",
            research_mode="OPPORTUNITY_DISCOVERY",
            capability_profile_id=profile.id,
            discovery_plan_id=plan.id,
        )
        self._db.add(task)
        self._db.flush()
        OutboxRepository(self._db).enqueue(
            task_id=task.id,
            run_id=None,
            stage_run_id=None,
            topic="execution.task_start",
            idempotency_key=f"discovery-plan-start:{plan.id}",
            payload={
                "task_id": str(task.id),
                "company_name": task.company_name,
                "demand_direction": task.demand_direction,
                "skill_id": plan.root_skill_name,
                "domain_context": plan.snapshot,
            },
        )
        self.consume(plan=plan)
        return task, True

    def _owned_plan(self, *, workspace_id: UUID, plan_id: UUID) -> DiscoveryResearchPlan:
        plan = self._db.get(DiscoveryResearchPlan, plan_id)
        if plan is None or plan.workspace_id != workspace_id:
            raise LookupError("研究计划不存在或不属于当前 Workspace")
        return plan
