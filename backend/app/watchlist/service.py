from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import CapabilityProfile, TargetAccount, WatchCheckRun, WatchSubscription
from app.skills.service import SkillService
from app.watchlist.schema import WatchSubscriptionInput, WatchSubscriptionPatch
from app.workspaces.service import WorkspaceService


@dataclass(frozen=True)
class ScheduleResult:
    run: WatchCheckRun | None
    reason: str


class WatchlistService:
    def __init__(self, session: Session):
        self._session = session

    def create(
        self,
        *,
        workspace_id: UUID,
        created_by: UUID,
        payload: WatchSubscriptionInput,
        now: datetime | None = None,
    ) -> WatchSubscription:
        WorkspaceService(self._session).require_active_membership(workspace_id, created_by)
        target = self._confirmed_target(workspace_id, payload.target_account_id)
        self._profile(workspace_id, payload.capability_profile_id)
        SkillService(self._session).runtime_catalog(workspace_id=workspace_id).load(
            payload.root_skill_name
        )
        existing = self._session.execute(
            select(WatchSubscription).where(
                WatchSubscription.workspace_id == workspace_id,
                WatchSubscription.target_account_id == target.id,
            )
        ).scalar_one_or_none()
        if existing is not None:
            raise ValueError("该目标企业已经存在雷达订阅")
        current_time = self._aware(now)
        self._timezone(payload.timezone_name)
        subscription = WatchSubscription(
            id=uuid4(),
            workspace_id=workspace_id,
            target_account_id=target.id,
            capability_profile_id=payload.capability_profile_id,
            created_by=created_by,
            root_skill_name=payload.root_skill_name,
            topics=sorted(payload.topics),
            frequency=payload.frequency,
            timezone_name=payload.timezone_name,
            max_external_calls=payload.max_external_calls,
            max_input_tokens=payload.max_input_tokens,
            status="ACTIVE",
            next_run_at=current_time if payload.start_immediately else self.next_occurrence(
                current_time, payload.frequency, payload.timezone_name
            ),
            created_at=current_time,
            updated_at=current_time,
        )
        self._session.add(subscription)
        self._session.flush()
        return subscription

    def update(
        self,
        *,
        workspace_id: UUID,
        subscription_id: UUID,
        updated_by: UUID,
        payload: WatchSubscriptionPatch,
        now: datetime | None = None,
    ) -> WatchSubscription:
        WorkspaceService(self._session).require_active_membership(workspace_id, updated_by)
        subscription = self._owned_for_update(workspace_id, subscription_id)
        if subscription.status == "ARCHIVED":
            raise ValueError("已归档订阅不能修改")
        values = payload.model_dump(exclude_none=True)
        if "timezone_name" in values:
            self._timezone(values["timezone_name"])
        for field, value in values.items():
            setattr(subscription, field, sorted(value) if field == "topics" else value)
        current_time = self._aware(now)
        subscription.next_run_at = self.next_occurrence(
            current_time, subscription.frequency, subscription.timezone_name
        )
        subscription.updated_at = current_time
        self._session.flush()
        return subscription

    def set_paused(
        self,
        *,
        workspace_id: UUID,
        subscription_id: UUID,
        changed_by: UUID,
        paused: bool,
        now: datetime | None = None,
    ) -> WatchSubscription:
        WorkspaceService(self._session).require_active_membership(workspace_id, changed_by)
        subscription = self._owned_for_update(workspace_id, subscription_id)
        if subscription.status == "ARCHIVED":
            raise ValueError("已归档订阅不能恢复")
        current_time = self._aware(now)
        subscription.status = "PAUSED" if paused else "ACTIVE"
        subscription.next_run_at = None if paused else self.next_occurrence(
            current_time, subscription.frequency, subscription.timezone_name
        )
        subscription.updated_at = current_time
        self._session.flush()
        return subscription

    def schedule_due_run(
        self,
        *,
        workspace_id: UUID,
        subscription_id: UUID,
        available_external_calls: int,
        available_input_tokens: int,
        now: datetime | None = None,
    ) -> ScheduleResult:
        current_time = self._aware(now)
        subscription = self._owned_for_update(workspace_id, subscription_id)
        if subscription.status != "ACTIVE" or subscription.next_run_at is None:
            return ScheduleResult(run=None, reason="NOT_ACTIVE")
        if subscription.next_run_at > current_time:
            return ScheduleResult(run=None, reason="NOT_DUE")
        scheduled_for = subscription.next_run_at
        next_run_at = self.next_occurrence(
            scheduled_for, subscription.frequency, subscription.timezone_name
        )
        if (
            available_external_calls < subscription.max_external_calls
            or available_input_tokens < subscription.max_input_tokens
        ):
            subscription.next_run_at = next_run_at
            subscription.updated_at = current_time
            self._session.flush()
            return ScheduleResult(run=None, reason="BUDGET_EXHAUSTED")

        previous = self._session.execute(
            select(WatchCheckRun)
            .where(WatchCheckRun.subscription_id == subscription.id)
            .order_by(WatchCheckRun.scheduled_for.desc(), WatchCheckRun.id.desc())
        ).scalars().first()
        input_value = {
            "subscription_id": str(subscription.id),
            "scheduled_for": scheduled_for.isoformat(),
            "target_account_id": str(subscription.target_account_id),
            "topics": subscription.topics,
            "root_skill_name": subscription.root_skill_name,
            "capability_profile_id": str(subscription.capability_profile_id) if subscription.capability_profile_id else None,
        }
        input_hash = sha256(json.dumps(
            input_value, ensure_ascii=True, sort_keys=True, separators=(",", ":")
        ).encode()).hexdigest()
        existing = self._session.execute(
            select(WatchCheckRun).where(
                WatchCheckRun.subscription_id == subscription.id,
                WatchCheckRun.input_hash == input_hash,
            )
        ).scalar_one_or_none()
        if existing is not None:
            return ScheduleResult(run=existing, reason="IDEMPOTENT_REPLAY")
        run = WatchCheckRun(
            id=uuid4(),
            workspace_id=workspace_id,
            subscription_id=subscription.id,
            target_account_id=subscription.target_account_id,
            previous_run_id=previous.id if previous else None,
            scheduled_for=scheduled_for,
            analysis_as_of_date=current_time.date(),
            input_hash=input_hash,
            status="PENDING",
            budget={
                "max_external_calls": subscription.max_external_calls,
                "max_input_tokens": subscription.max_input_tokens,
            },
            usage={},
            change_summary={},
            created_at=current_time,
            updated_at=current_time,
        )
        self._session.add(run)
        subscription.last_run_at = scheduled_for
        subscription.next_run_at = next_run_at
        subscription.updated_at = current_time
        self._session.flush()
        return ScheduleResult(run=run, reason="CREATED")

    @staticmethod
    def next_occurrence(basis: datetime, frequency: str, timezone_name: str) -> datetime:
        zone = WatchlistService._timezone(timezone_name)
        local = WatchlistService._aware(basis).astimezone(zone)
        if frequency == "DAILY":
            candidate = local + timedelta(days=1)
        elif frequency == "WEEKLY":
            candidate = local + timedelta(days=7)
        elif frequency == "MONTHLY":
            year = local.year + (1 if local.month == 12 else 0)
            month = 1 if local.month == 12 else local.month + 1
            day = min(local.day, calendar.monthrange(year, month)[1])
            candidate = local.replace(year=year, month=month, day=day)
        else:
            raise ValueError("检查频率不受支持")
        return candidate.astimezone(timezone.utc)

    def _owned_for_update(self, workspace_id: UUID, subscription_id: UUID) -> WatchSubscription:
        subscription = self._session.execute(
            select(WatchSubscription)
            .where(
                WatchSubscription.id == subscription_id,
                WatchSubscription.workspace_id == workspace_id,
            )
            .with_for_update()
        ).scalar_one_or_none()
        if subscription is None:
            raise LookupError("雷达订阅不存在或不属于当前 Workspace")
        return subscription

    def _confirmed_target(self, workspace_id: UUID, target_account_id: UUID) -> TargetAccount:
        target = self._session.get(TargetAccount, target_account_id)
        if target is None or target.workspace_id != workspace_id:
            raise LookupError("目标企业不存在或不属于当前 Workspace")
        if target.status != "CONFIRMED":
            raise ValueError("只有已完成主体消歧并确认的目标企业才能订阅雷达")
        return target

    def _profile(self, workspace_id: UUID, profile_id: UUID | None) -> None:
        if profile_id is None:
            return
        profile = self._session.get(CapabilityProfile, profile_id)
        if profile is None or profile.workspace_id != workspace_id or profile.status != "ACTIVE":
            raise ValueError("能力档案不存在、未启用或不属于当前 Workspace")

    @staticmethod
    def _timezone(value: str) -> ZoneInfo:
        try:
            return ZoneInfo(value)
        except ZoneInfoNotFoundError as error:
            raise ValueError("无效的 IANA 时区") from error

    @staticmethod
    def _aware(value: datetime | None) -> datetime:
        result = value or datetime.now(timezone.utc)
        if result.tzinfo is None:
            raise ValueError("时间必须包含时区")
        return result.astimezone(timezone.utc)
