"""客户雷达的增量研究派发、内容去重和 OIG 重裁决。"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import (
    Claim,
    Evidence,
    ExternalCallAttempt,
    GateDecision,
    TargetAccount,
    Task,
    TaskStatus,
    WatchCheckRun,
    WatchSubscription,
)
from app.db.session import SessionLocal
from app.execution.outbox_repository import OutboxRepository
from app.opportunities.assessment_service import OpportunityAssessmentService
from app.watchlist.service import ScheduleResult, WatchlistService
from app.worker.celery_app import celery_app


_TERMINAL_SUCCESS = frozenset({"COMPLETED", "PARTIAL"})
_TERMINAL_FAILURE = frozenset({"FAILED", "CANCELLED"})
_TRACKING_QUERY_KEYS = frozenset({
    "fbclid", "gclid", "mc_cid", "mc_eid", "spm", "from", "source",
})
_PROCUREMENT_STAGES = frozenset({
    "PLANNED", "SOURCING", "TENDERING", "EVALUATING", "AWARDED",
    "CONTRACTED", "IMPLEMENTING", "LIVE", "MAINTAINING", "EXPANDING",
    "REPLACING", "CANCELLED", "EXPIRED",
})


def _normalized_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).casefold()


def _canonical_url(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    parsed = urlsplit(raw)
    hostname = (parsed.hostname or "").lower()
    netloc = hostname
    if parsed.port and not (
        parsed.scheme.lower() == "http" and parsed.port == 80
    ) and not (
        parsed.scheme.lower() == "https" and parsed.port == 443
    ):
        netloc = f"{hostname}:{parsed.port}"
    path = re.sub(r"/{2,}", "/", parsed.path or "/")
    if path != "/":
        path = path.rstrip("/")
    query = urlencode(sorted(
        (key, val)
        for key, val in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in _TRACKING_QUERY_KEYS
    ))
    return urlunsplit((parsed.scheme.lower(), netloc, path, query, ""))


def evidence_fingerprint(evidence: Any) -> str:
    """优先使用正文哈希；无正文快照时用规范 URL 与摘要稳定降级。"""
    content_hash = str(getattr(evidence, "content_hash", None) or "").strip().lower()
    if re.fullmatch(r"[0-9a-f]{64}", content_hash):
        return content_hash
    material = {
        "url": _canonical_url(getattr(evidence, "url", "")),
        "title": _normalized_text(getattr(evidence, "title", "")),
        "snippet": _normalized_text(getattr(evidence, "snippet", "")),
    }
    return sha256(json.dumps(
        material, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")).hexdigest()


def claim_identity(claim: Any) -> str:
    return sha256(_normalized_text(getattr(claim, "claim_text", "")).encode("utf-8")).hexdigest()


def claim_state_fingerprint(claim: Any) -> str:
    state = {
        "type": str(getattr(claim, "claim_type", "") or "").upper(),
        "effect": str(getattr(claim, "opportunity_effect", "") or "").lower(),
        "status": str(getattr(claim, "status", "") or "").upper(),
    }
    return sha256(json.dumps(state, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def pre_gate_evidence_delta(
    *,
    current_evidences: Iterable[Any],
    historical_evidences: Iterable[Any],
) -> dict[str, Any]:
    """在进入 OIG/REPORT 前判定是否存在任何全新 Evidence。"""
    current_items = list(current_evidences)
    known = {evidence_fingerprint(item) for item in historical_evidences}
    current_hashes = {evidence_fingerprint(item) for item in current_items}
    new_hashes = sorted(current_hashes - known)
    return {
        "has_new_evidence": bool(new_hashes),
        "new_evidence_count": len(new_hashes),
        "duplicate_evidence_count": len(current_items) - len(new_hashes),
        "new_evidence_hashes": new_hashes,
    }


def detect_incremental_changes(
    *,
    current_evidences: Iterable[Any],
    historical_evidences: Iterable[Any],
    current_claims: Iterable[Any],
    latest_claims: Iterable[Any],
) -> dict[str, Any]:
    """返回可持久化变化摘要；证据查全历史，Claim 只对比最近状态。"""
    current_evidence_items = list(current_evidences)
    known_evidence = {evidence_fingerprint(item) for item in historical_evidences}
    current_by_hash = {evidence_fingerprint(item): item for item in current_evidence_items}
    new_evidence = [
        item for fingerprint, item in sorted(current_by_hash.items())
        if fingerprint not in known_evidence
    ]
    latest_claim_state = {
        claim_identity(item): claim_state_fingerprint(item) for item in latest_claims
    }
    changed_claims: list[Any] = []
    for item in current_claims:
        identity = claim_identity(item)
        if latest_claim_state.get(identity) != claim_state_fingerprint(item):
            changed_claims.append(item)

    procurement = []
    policy = []
    contract_window = []
    for item in new_evidence:
        metadata = dict(getattr(item, "meta_data", None) or {})
        dimension = str(getattr(item, "dimension", "") or "").upper()
        stage = str(
            getattr(item, "procurement_stage", None) or metadata.get("event_stage") or ""
        ).upper()
        fingerprint = evidence_fingerprint(item)
        if stage in _PROCUREMENT_STAGES or dimension in {"PROCUREMENT", "BIDDING"}:
            procurement.append(fingerprint)
        if (
            dimension == "POLICY"
            or getattr(item, "effective_from", None) is not None
            or getattr(item, "effective_to", None) is not None
            or metadata.get("effective_start")
            or metadata.get("effective_end")
        ):
            policy.append(fingerprint)
        if (
            dimension == "CONTRACT_WINDOW"
            or getattr(item, "contract_start_at", None) is not None
            or getattr(item, "contract_end_at", None) is not None
            or metadata.get("contract_start_date")
            or metadata.get("contract_end_date")
        ):
            contract_window.append(fingerprint)

    new_hashes = sorted(evidence_fingerprint(item) for item in new_evidence)
    changed_claim_ids = sorted(claim_identity(item) for item in changed_claims)
    material = bool(procurement or policy or contract_window or changed_claim_ids)
    return {
        "has_material_change": material,
        "new_evidence_count": len(new_hashes),
        "duplicate_evidence_count": len(current_evidence_items) - len(new_hashes),
        "changed_claim_count": len(changed_claim_ids),
        "categories": {
            "procurement": sorted(procurement),
            "policy": sorted(policy),
            "contract_window": sorted(contract_window),
            "claim": changed_claim_ids,
        },
        "new_evidence_hashes": new_hashes,
    }


class IncrementalResearchCoordinator:
    def __init__(self, session: Session) -> None:
        self._session = session

    def dispatch_one(
        self,
        *,
        subscription_id: UUID,
        available_external_calls: int,
        available_input_tokens: int,
        now: datetime | None = None,
    ) -> ScheduleResult:
        subscription = self._session.get(WatchSubscription, subscription_id)
        if subscription is None:
            raise LookupError("雷达订阅不存在")
        result = WatchlistService(self._session).schedule_due_run(
            workspace_id=subscription.workspace_id,
            subscription_id=subscription.id,
            available_external_calls=available_external_calls,
            available_input_tokens=available_input_tokens,
            now=now,
        )
        run = result.run
        if run is None or result.reason != "CREATED":
            return result
        target = self._session.get(TargetAccount, subscription.target_account_id)
        if target is None or target.workspace_id != subscription.workspace_id:
            raise ValueError("雷达目标企业不存在或 Workspace 归属非法")

        boundary = self._incremental_since(run, subscription)
        known_hashes = self._historical_evidence_hashes(subscription.id, exclude_run_id=run.id)
        topics = sorted(str(item) for item in subscription.topics)
        task = Task(
            user_id=subscription.created_by,
            workspace_id=subscription.workspace_id,
            target_account_id=target.id,
            company_name=target.official_name or target.input_name,
            demand_direction=(
                f"客户雷达增量检查（{', '.join(topics)}）；"
                f"只研究 {boundary.isoformat()} 之后发布、发生或发生状态变化的信息"
            ),
            status=TaskStatus.PENDING,
            desired_state="RUNNING",
            observed_state="PENDING",
            research_mode=(
                "OPPORTUNITY_DISCOVERY"
                if subscription.capability_profile_id is not None
                else "DIRECTED_RESEARCH"
            ),
            capability_profile_id=subscription.capability_profile_id,
        )
        self._session.add(task)
        self._session.flush()
        run.task_id = task.id
        run.status = "RUNNING"
        run.started_at = self._aware(now)
        run.updated_at = self._aware(now)
        domain_context = {
            "watch_check_run_id": str(run.id),
            "incremental_only": True,
            "incremental_since": boundary.isoformat(),
            "incremental_topics": topics,
            "known_evidence_count": len(known_hashes),
            "known_evidence_set_hash": self._set_hash(known_hashes),
            "research_mode": task.research_mode,
            "industry": target.industry,
            "region": target.region,
            "product_selected": subscription.capability_profile_id is not None,
            "incremental_policy": (
                "搜索与抽取仅接受时间边界后的新增或状态变化内容；旧内容只可作为链接线索，"
                "不得作为本轮新增事实。最终由内容指纹再次去重。"
            ),
        }
        OutboxRepository(self._session).enqueue(
            task_id=task.id,
            run_id=None,
            stage_run_id=None,
            topic="execution.task_start",
            idempotency_key=f"watch-check-start:{run.id}",
            payload={
                "task_id": str(task.id),
                "company_name": task.company_name,
                "demand_direction": task.demand_direction,
                "skill_id": subscription.root_skill_name,
                "domain_context": domain_context,
            },
        )
        self._session.flush()
        return result

    def reconcile_one(
        self,
        *,
        run_id: UUID,
        now: datetime | None = None,
    ) -> WatchCheckRun:
        run = self._session.execute(
            select(WatchCheckRun).where(WatchCheckRun.id == run_id).with_for_update()
        ).scalar_one_or_none()
        if run is None:
            raise LookupError("雷达检查运行不存在")
        if run.status not in {"RUNNING", "PENDING"} or run.task_id is None:
            return run
        task = self._session.get(Task, run.task_id)
        if task is None:
            raise ValueError("雷达检查运行缺少研究任务")
        observed_state = str(task.observed_state or "")
        if observed_state not in _TERMINAL_SUCCESS | _TERMINAL_FAILURE:
            return run
        current_time = self._aware(now)
        if observed_state in _TERMINAL_FAILURE:
            run.status = "FAILED"
            run.error_code = f"TASK_{observed_state}"
            run.error_message = task.error_message or f"研究任务终态为 {observed_state}"
            run.finished_at = current_time
            run.updated_at = current_time
            self._session.flush()
            return run

        current_evidences = self._task_evidences(task.id)
        current_claims = self._task_claims(task.id)
        historical_evidences = self._historical_evidences(run.subscription_id, run.id)
        latest_claims = self._latest_prior_claims(run)
        summary = detect_incremental_changes(
            current_evidences=current_evidences,
            historical_evidences=historical_evidences,
            current_claims=current_claims,
            latest_claims=latest_claims,
        )
        summary["analysis_as_of_date"] = run.analysis_as_of_date.isoformat()
        summary["task_observed_state"] = observed_state
        if summary["has_material_change"]:
            existing_decision = self._session.execute(
                select(GateDecision)
                .where(GateDecision.task_id == task.id)
                .order_by(GateDecision.created_at.desc(), GateDecision.id.desc())
            ).scalars().first()
            assessment = (
                None
                if existing_decision is not None
                else OpportunityAssessmentService(self._session).assess_and_persist(
                    task_id=task.id,
                    analysis_as_of_date=datetime.combine(
                        run.analysis_as_of_date,
                        datetime.min.time(),
                        tzinfo=timezone.utc,
                    ),
                )
            )
            decision = existing_decision or assessment.decision
            summary["gate_decision_id"] = str(decision.id)
            summary["gate_level"] = decision.gate_level
            summary["gate_decision_created"] = existing_decision is None
        else:
            summary["gate_decision_id"] = None
            summary["gate_level"] = None
            summary["gate_decision_created"] = False

        run.change_summary = summary
        run.usage = self._usage(task.id)
        run.status = "PARTIAL" if observed_state == "PARTIAL" else "COMPLETED"
        run.finished_at = current_time
        run.updated_at = current_time
        self._session.flush()
        return run

    def evaluate_pre_gate(
        self,
        *,
        run_id: UUID,
        task_id: UUID,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """供耐久 DAG 使用；无新增内容时直接收口检查运行。"""
        run = self._session.execute(
            select(WatchCheckRun).where(WatchCheckRun.id == run_id).with_for_update()
        ).scalar_one_or_none()
        if run is None or run.task_id != task_id:
            raise ValueError("雷达检查运行与研究任务不一致")
        if run.status in {"COMPLETED", "PARTIAL", "FAILED"}:
            return dict(run.change_summary or {})
        delta = pre_gate_evidence_delta(
            current_evidences=self._task_evidences(task_id),
            historical_evidences=self._historical_evidences(run.subscription_id, run.id),
        )
        if not delta["has_new_evidence"]:
            current_time = self._aware(now)
            run.change_summary = {
                **delta,
                "has_material_change": False,
                "changed_claim_count": 0,
                "categories": {
                    "procurement": [],
                    "policy": [],
                    "contract_window": [],
                    "claim": [],
                },
                "gate_decision_id": None,
                "gate_level": None,
                "gate_decision_created": False,
                "analysis_as_of_date": run.analysis_as_of_date.isoformat(),
                "terminal_reason": "NO_NEW_EVIDENCE",
            }
            run.usage = self._usage(task_id)
            run.status = "COMPLETED"
            run.finished_at = current_time
            run.updated_at = current_time
            self._session.flush()
        return delta

    def _incremental_since(
        self,
        run: WatchCheckRun,
        subscription: WatchSubscription,
    ) -> datetime:
        previous = self._session.get(WatchCheckRun, run.previous_run_id) if run.previous_run_id else None
        value = (
            previous.finished_at if previous and previous.finished_at
            else previous.scheduled_for if previous else subscription.created_at
        )
        return self._aware(value)

    def _historical_run_task_ids(self, subscription_id: UUID, exclude_run_id: UUID) -> list[UUID]:
        return list(self._session.execute(
            select(WatchCheckRun.task_id).where(
                WatchCheckRun.subscription_id == subscription_id,
                WatchCheckRun.id != exclude_run_id,
                WatchCheckRun.task_id.is_not(None),
            )
        ).scalars())

    def _historical_evidence_hashes(self, subscription_id: UUID, exclude_run_id: UUID) -> set[str]:
        task_ids = self._historical_run_task_ids(subscription_id, exclude_run_id)
        if not task_ids:
            return set()
        return {evidence_fingerprint(item) for item in self._session.execute(
            select(Evidence).where(Evidence.task_id.in_(task_ids))
        ).scalars()}

    def _historical_evidences(self, subscription_id: UUID, exclude_run_id: UUID) -> list[Evidence]:
        task_ids = self._historical_run_task_ids(subscription_id, exclude_run_id)
        if not task_ids:
            return []
        return list(self._session.execute(
            select(Evidence).where(Evidence.task_id.in_(task_ids))
        ).scalars())

    def _latest_prior_claims(self, run: WatchCheckRun) -> list[Claim]:
        prior_task_ids = list(self._session.execute(
            select(WatchCheckRun.task_id)
            .where(
                WatchCheckRun.subscription_id == run.subscription_id,
                WatchCheckRun.scheduled_for < run.scheduled_for,
                WatchCheckRun.task_id.is_not(None),
            )
            .order_by(WatchCheckRun.scheduled_for.desc(), WatchCheckRun.id.desc())
        ).scalars())
        if not prior_task_ids:
            return []
        claims = list(self._session.execute(
            select(Claim)
            .where(Claim.task_id.in_(prior_task_ids))
            .order_by(Claim.updated_at.desc(), Claim.id.desc())
        ).scalars())
        latest_by_identity: dict[str, Claim] = {}
        for item in claims:
            latest_by_identity.setdefault(claim_identity(item), item)
        return list(latest_by_identity.values())

    def _task_evidences(self, task_id: UUID) -> list[Evidence]:
        return list(self._session.execute(
            select(Evidence).where(Evidence.task_id == task_id)
        ).scalars())

    def _task_claims(self, task_id: UUID) -> list[Claim]:
        return list(self._session.execute(
            select(Claim).where(Claim.task_id == task_id)
        ).scalars())

    def _usage(self, task_id: UUID) -> dict[str, int]:
        row = self._session.execute(
            select(
                func.count(ExternalCallAttempt.id),
                func.coalesce(func.sum(ExternalCallAttempt.input_tokens), 0),
                func.coalesce(func.sum(ExternalCallAttempt.output_tokens), 0),
            ).where(ExternalCallAttempt.task_id == task_id)
        ).one()
        return {
            "external_calls": int(row[0]),
            "input_tokens": int(row[1]),
            "output_tokens": int(row[2]),
        }

    @staticmethod
    def _set_hash(values: Iterable[str]) -> str:
        return sha256("\n".join(sorted(set(values))).encode("utf-8")).hexdigest()

    @staticmethod
    def _aware(value: datetime | None) -> datetime:
        result = value or datetime.now(timezone.utc)
        if result.tzinfo is None or result.utcoffset() is None:
            return result.replace(tzinfo=timezone.utc)
        return result.astimezone(timezone.utc)


def _nonnegative_env(name: str, default: int, env: Mapping[str, str] | None = None) -> int:
    source = os.environ if env is None else env
    try:
        value = int(source.get(name, str(default)))
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} 必须为非负整数") from error
    if value < 0:
        raise ValueError(f"{name} 必须为非负整数")
    return value


@celery_app.task(name="tasks.dispatch_due_watchlists")
def dispatch_due_watchlists(limit: int = 100) -> dict[str, Any]:
    if limit < 1 or limit > 1000:
        raise ValueError("limit 必须在 1 到 1000 之间")
    external_budget = _nonnegative_env("WATCHLIST_DISPATCH_EXTERNAL_CALL_BUDGET", 10000)
    token_budget = _nonnegative_env("WATCHLIST_DISPATCH_INPUT_TOKEN_BUDGET", 50_000_000)
    now = datetime.now(timezone.utc)
    discovery = SessionLocal()
    try:
        subscription_ids = list(discovery.execute(
            select(WatchSubscription.id)
            .where(
                WatchSubscription.status == "ACTIVE",
                WatchSubscription.next_run_at.is_not(None),
                WatchSubscription.next_run_at <= now,
            )
            .order_by(WatchSubscription.next_run_at, WatchSubscription.id)
            .limit(limit)
        ).scalars())
    finally:
        discovery.close()

    created = 0
    reasons: dict[str, int] = {}
    for subscription_id in subscription_ids:
        session = SessionLocal()
        try:
            result = IncrementalResearchCoordinator(session).dispatch_one(
                subscription_id=subscription_id,
                available_external_calls=external_budget,
                available_input_tokens=token_budget,
                now=now,
            )
            if result.reason == "CREATED" and result.run is not None:
                reserved = dict(result.run.budget or {})
                external_budget -= int(reserved.get("max_external_calls", 0))
                token_budget -= int(reserved.get("max_input_tokens", 0))
                created += 1
            reasons[result.reason] = reasons.get(result.reason, 0) + 1
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
    return {"due": len(subscription_ids), "created": created, "reasons": reasons}


@celery_app.task(name="tasks.reconcile_watchlist_runs")
def reconcile_watchlist_runs(limit: int = 100) -> dict[str, int]:
    if limit < 1 or limit > 1000:
        raise ValueError("limit 必须在 1 到 1000 之间")
    discovery = SessionLocal()
    try:
        run_ids = list(discovery.execute(
            select(WatchCheckRun.id)
            .join(Task, Task.id == WatchCheckRun.task_id)
            .where(
                WatchCheckRun.status.in_(("PENDING", "RUNNING")),
                Task.observed_state.in_(tuple(_TERMINAL_SUCCESS | _TERMINAL_FAILURE)),
            )
            .order_by(WatchCheckRun.scheduled_for, WatchCheckRun.id)
            .limit(limit)
        ).scalars())
    finally:
        discovery.close()
    reconciled = 0
    for run_id in run_ids:
        session = SessionLocal()
        try:
            IncrementalResearchCoordinator(session).reconcile_one(run_id=run_id)
            session.commit()
            reconciled += 1
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
    return {"eligible": len(run_ids), "reconciled": reconciled}
