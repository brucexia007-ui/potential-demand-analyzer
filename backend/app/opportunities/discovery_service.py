"""WBS-34-16：自动线索发现进入外部研究前的确定性主体与能力档案预检。"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Literal
from uuid import UUID

from sqlalchemy.orm import Session

from app.db.models import CapabilityProfile, TargetAccount, Task


DiscoveryPreflightStatus = Literal["READY", "NEEDS_TARGET_CONFIRMATION"]


@dataclass(frozen=True)
class DiscoveryPreflightResult:
    status: DiscoveryPreflightStatus
    task_id: UUID
    workspace_id: UUID
    target_account_id: UUID
    capability_profile_id: UUID
    target_confirmed: bool
    assumption_authorized: bool
    question: str | None
    target_summary: dict
    input_hash: str


class OpportunityDiscoveryPreflightService:
    def __init__(self, db: Session) -> None:
        self._db = db

    def evaluate(
        self,
        *,
        task_id: UUID,
        allow_unresolved_assumption: bool = False,
    ) -> DiscoveryPreflightResult:
        task = self._db.get(Task, task_id)
        if task is None:
            raise LookupError("自动线索发现任务不存在")
        if task.research_mode != "OPPORTUNITY_DISCOVERY":
            raise ValueError("只有 OPPORTUNITY_DISCOVERY 任务可以执行自动发现预检")
        if task.workspace_id is None or task.target_account_id is None:
            raise ValueError("自动线索发现任务缺少 Workspace 或目标企业强绑定")
        target = self._db.get(TargetAccount, task.target_account_id)
        if target is None or target.workspace_id != task.workspace_id:
            raise ValueError("自动线索发现目标企业不存在或 Workspace 归属不一致")
        if target.status == "ARCHIVED":
            raise ValueError("已归档目标企业不能启动自动线索发现")
        if task.capability_profile_id is None:
            raise ValueError("自动线索发现必须选择企业能力档案")
        profile = self._db.get(CapabilityProfile, task.capability_profile_id)
        if (
            profile is None
            or profile.workspace_id != task.workspace_id
            or profile.status != "ACTIVE"
        ):
            raise ValueError("自动线索发现能力档案不存在、已归档或不属于当前 Workspace")

        summary = {
            "input_name": target.input_name,
            "official_name": target.official_name,
            "website": target.website,
            "credit_code": target.credit_code,
            "industry": target.industry,
            "region": target.region,
            "stock_code": target.stock_code,
            "status": target.status,
            "capability_profile_id": str(profile.id),
            "capability_profile_name": profile.name,
        }
        encoded = json.dumps(
            {
                "task_id": str(task.id),
                "workspace_id": str(task.workspace_id),
                "target_account_id": str(target.id),
                "target": summary,
                "allow_unresolved_assumption": allow_unresolved_assumption,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        confirmed = target.status == "CONFIRMED"
        assumption_authorized = allow_unresolved_assumption and not confirmed
        needs_confirmation = not confirmed and not assumption_authorized
        return DiscoveryPreflightResult(
            status="NEEDS_TARGET_CONFIRMATION" if needs_confirmation else "READY",
            task_id=task.id,
            workspace_id=task.workspace_id,
            target_account_id=target.id,
            capability_profile_id=profile.id,
            target_confirmed=confirmed,
            assumption_authorized=assumption_authorized,
            question=(
                f"请确认自动线索发现的目标企业是否为“{target.input_name}”。"
                "确认后外部证据、Claim 和商机判断都将绑定到该企业主体。"
                if needs_confirmation else None
            ),
            target_summary=summary,
            input_hash=sha256(encoded).hexdigest(),
        )

    def confirm_target(self, *, task_id: UUID) -> DiscoveryPreflightResult:
        task = self._db.get(Task, task_id)
        if task is None or task.target_account_id is None:
            raise LookupError("自动线索发现任务或目标企业不存在")
        target = self._db.get(TargetAccount, task.target_account_id)
        if target is None or target.workspace_id != task.workspace_id:
            raise ValueError("自动线索发现目标企业归属非法")
        if target.status == "ARCHIVED":
            raise ValueError("已归档目标企业不能被确认用于自动线索发现")
        target.status = "CONFIRMED"
        self._db.flush()
        return self.evaluate(task_id=task_id)
