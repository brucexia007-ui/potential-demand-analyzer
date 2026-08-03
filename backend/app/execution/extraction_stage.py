"""TEO-08-04：可重入批提取、Evidence 幂等写入与充分性决策。"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.agents.extractor_agent import BatchExtractionSchemaError, BatchExtractionRetryResult, ExtractorAgent
from app.agents.eval.extraction_evaluator import ExtractionEvaluator
from app.agents.harness.extraction_batch import (
    ExtractionCandidatePayload,
    plan_extraction_batches,
)
from app.agents.harness.spec import DimensionGoal
from app.agents.harness.state import Evidence as RuntimeEvidence
from app.agents.eval.evidence_sufficiency import evaluate_evidence_sufficiency
from app.db.models import Evidence, ResearchCandidate, TargetAccount, Task, TaskEvent, TaskRun, TaskStageRun
from app.evidence.procurement_event_normalizer import normalize_procurement_fields
from app.evidence.source_reliability import score_source_reliability
from app.evidence.snapshot_service import SnapshotService
from app.execution.event_repository import TaskEventRepository
from app.skills.schema import EvidencePolicy


class ExtractionStageHandler:
    """每个批次独立执行，所有输入均从已持久化的候选快照重新加载。"""

    def __init__(
        self,
        session: Session,
        *,
        extractor: ExtractorAgent | None = None,
        snapshot_service: SnapshotService | None = None,
    ) -> None:
        self._session = session
        self._extractor = extractor or ExtractorAgent()
        self._snapshots = snapshot_service or SnapshotService()
        self._events = TaskEventRepository(session)

    def plan_batches(
        self,
        *,
        task_id: UUID,
        run_id: UUID,
        fetch_stage_run_id: UUID,
        dimension: str,
        **batch_options: Any,
    ) -> dict[str, Any]:
        """从已完成抓取阶段加载正文快照并创建可序列化的批次描述。"""
        fetch_asset = self._completed_asset(fetch_stage_run_id, run_id, "抓取")
        candidate_ids = self._fetched_candidate_ids(fetch_asset)
        candidates = self._candidates_by_ids(task_id, candidate_ids)
        payloads = [
            ExtractionCandidatePayload(
                candidate_id=candidate_id,
                title=candidates[candidate_id].title,
                content=self._snapshot_content(candidates[candidate_id]),
            )
            for candidate_id in candidate_ids
        ]
        plan = plan_extraction_batches(payloads, **batch_options)
        return {
            "batches": [
                {"index": batch.index, "candidate_ids": [item.candidate_id for item in batch.candidates]}
                for batch in plan.batches
            ],
            "candidate_count": len(payloads),
            "soft_input_limit_tokens": plan.soft_input_limit_tokens,
            "hard_input_limit_tokens": plan.hard_input_limit_tokens,
            "output_limit_tokens": plan.output_limit_tokens,
        }

    def extract_batch(
        self,
        *,
        task_id: UUID,
        run_id: UUID,
        stage_run_id: UUID,
        dimension: str,
        batch_descriptor: Mapping[str, Any],
        must_extract: list[str],
        policy: EvidencePolicy,
        quality_thresholds: Mapping[str, float | int],
        reference_context: Sequence[Mapping[str, Any]] = (),
        max_batch_retries: int = 1,
        required_fields: tuple[str, ...] = (),
        critical_claim_ids: tuple[str, ...] = (),
        consecutive_low_gain_batches: int = 0,
    ) -> dict[str, Any]:
        """执行一个批次；成功结果、充分性结论和事件均可安全重放。"""
        stage = self._stage_for_run(stage_run_id, run_id)
        if stage.asset_ref.get("batch_completed") is True:
            return dict(stage.asset_ref)
        batch_index, candidate_ids = self._batch_descriptor(batch_descriptor)
        candidates = self._candidates_by_ids(task_id, candidate_ids)
        payloads = tuple(
            ExtractionCandidatePayload(
                candidate_id=candidate_id,
                title=candidates[candidate_id].title,
                content=self._snapshot_content(candidates[candidate_id]),
            )
            for candidate_id in candidate_ids
        )
        batch = plan_extraction_batches(
            payloads,
            min_batch_size=1,
            max_batch_size=max(1, len(payloads)),
        ).batches[0]
        batch = type(batch)(
            index=batch_index,
            candidates=batch.candidates,
            estimated_input_tokens=batch.estimated_input_tokens,
            estimated_output_tokens=batch.estimated_output_tokens,
            constraint_limited=batch.constraint_limited,
        )
        # 外部模型调用会在独立账本 Session 中锁定任务预算行；此处必须先结束
        # 主工作单元的只读事务，避免同一任务的两条数据库连接互相等待。
        self._session.commit()
        try:
            extraction = self._extractor.execute_batch_with_minimal_retry(
                batch,
                must_extract,
                max_batch_retries=max_batch_retries,
                reference_context=reference_context,
            )
        except BatchExtractionSchemaError as error:
            if "截断" not in str(error) or len(payloads) == 1:
                raise
            items_by_candidate_id = {}
            rejected_by_candidate_id = {}
            retried_candidate_ids = []
            attempts = 1
            for payload in payloads:
                single_batch = plan_extraction_batches(
                    (payload,), min_batch_size=1, max_batch_size=1,
                ).batches[0]
                recovered = self._extractor.execute_batch_with_minimal_retry(
                    single_batch,
                    must_extract,
                    max_batch_retries=max_batch_retries,
                    reference_context=reference_context,
                )
                items_by_candidate_id.update(recovered.items_by_candidate_id)
                rejected_by_candidate_id.update(recovered.rejected_by_candidate_id)
                retried_candidate_ids.extend(recovered.retried_candidate_ids)
                attempts += recovered.attempt_count
            extraction = BatchExtractionRetryResult(
                items_by_candidate_id=items_by_candidate_id,
                rejected_by_candidate_id=rejected_by_candidate_id,
                retried_candidate_ids=tuple(retried_candidate_ids),
                attempt_count=attempts,
            )
        # 多个提取批次可以并行进行模型调用，但模型返回后写 Evidence、事件和阶段产物前
        # 必须先以同一任务行串行化持久化，避免外键的 KEY SHARE 与事件序列的 FOR UPDATE
        # 在并发事务中形成锁升级死锁。
        self._lock_task_for_persistence(task_id)
        evidence_ids: list[str] = []
        latest_evidences: list[Evidence] = []
        for candidate_id, item in extraction.items_by_candidate_id.items():
            evidence = self._get_or_create_evidence(
                task_id=task_id,
                dimension=dimension,
                candidate=candidates[candidate_id],
                candidate_id=candidate_id,
                item=item,
            )
            evidence_ids.append(str(evidence.id))
            latest_evidences.append(evidence)
        for candidate_id in sorted(extraction.rejected_by_candidate_id):
            lead = self._get_or_create_candidate_lead(
                task_id=task_id,
                dimension=dimension,
                candidate=candidates[candidate_id],
                candidate_id=candidate_id,
                rejection_reason=str(extraction.rejected_by_candidate_id[candidate_id]),
            )
            if lead is not None:
                evidence_ids.append(str(lead.id))
                latest_evidences.append(lead)

        sufficiency = self._evaluate_sufficiency(
            task_id=task_id,
            dimension=dimension,
            policy=policy,
            latest_evidences=latest_evidences,
            required_fields=required_fields,
            critical_claim_ids=critical_claim_ids,
            consecutive_low_gain_batches=consecutive_low_gain_batches,
        )
        quality_evaluation = self._evaluate_quality(
            run_id=run_id,
            dimension=dimension,
            required_fields=required_fields or tuple(must_extract),
            quality_thresholds=quality_thresholds,
        )
        sufficiency["quality_evaluation"] = quality_evaluation
        if not quality_evaluation["passed"]:
            quality_gaps = [
                f"quality:{item}" for item in quality_evaluation["analysis"]["hard_failures"]
            ]
            sufficiency["mandatory_gaps"] = list(dict.fromkeys(
                [*sufficiency["mandatory_gaps"], *quality_gaps]
            ))
            sufficiency["is_sufficient"] = False
            sufficiency["should_stop"] = False
            sufficiency["should_expand"] = (
                sufficiency["evidence_count"] < policy.max_evidence_count
            )
        result = {
            "batch_completed": True,
            "batch_index": batch_index,
            "candidate_ids": candidate_ids,
            "evidence_ids": evidence_ids,
            "rejected_candidate_ids": sorted(extraction.rejected_by_candidate_id),
            "sufficiency": sufficiency,
        }
        stage.asset_ref = result
        self._session.flush()
        self._append_event_once(
            task_id=task_id,
            run_id=run_id,
            stage_run_id=stage_run_id,
            event_type="BATCH_EXTRACTION_COMPLETED",
            payload={"batch_index": batch_index, "evidence_ids": evidence_ids},
        )
        self._append_event_once(
            task_id=task_id,
            run_id=run_id,
            stage_run_id=stage_run_id,
            event_type="EVIDENCE_SUFFICIENCY_EVALUATED",
            payload={"batch_index": batch_index, "sufficiency": sufficiency},
        )
        if sufficiency["should_stop"]:
            self._append_event_once(
                task_id=task_id,
                run_id=run_id,
                stage_run_id=stage_run_id,
                event_type="EVIDENCE_EARLY_STOP",
                payload={"batch_index": batch_index},
            )
        elif sufficiency["should_expand"]:
            self._append_event_once(
                task_id=task_id,
                run_id=run_id,
                stage_run_id=stage_run_id,
                event_type="EVIDENCE_EXPANSION_REQUESTED",
                payload={"batch_index": batch_index, "mandatory_gaps": sufficiency["mandatory_gaps"]},
            )
        return result

    def _get_or_create_evidence(self, *, task_id: UUID, dimension: str, candidate: ResearchCandidate, candidate_id: str, item) -> Evidence:
        identity = json.dumps(
            {
                "candidate_id": candidate_id,
                "fields": dict(item.fields),
                "citation_excerpt": item.citation_excerpt,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        evidence_id = uuid5(NAMESPACE_URL, f"execution-evidence/v1:{task_id}:{dimension}:{identity}")
        existing = self._session.get(Evidence, evidence_id)
        if existing is not None:
            return existing
        task = self._session.get(Task, task_id)
        if task is None:
            raise LookupError("Evidence 所属任务不存在")
        fields = dict(item.fields)
        title = str(fields.pop("title", fields.pop("项目名称", candidate.title)))[:200]
        snapshot_content = self._snapshot_content(candidate)
        published_at = candidate.published_at
        publish_date_source: str | None = None
        if published_at is None:
            from app.evidence.date_normalizer import infer_date_from_texts

            published_at, publish_date_source = infer_date_from_texts(
                url=candidate.canonical_url,
                title=title,
                snippet=candidate.snippet or "",
                body_excerpt=snapshot_content[:2000],
            )
        deterministic_fields = normalize_procurement_fields(
            title=title,
            content=snapshot_content,
            published_at=published_at,
        )
        for field_name, value in deterministic_fields.items():
            if value not in (None, ""):
                fields.setdefault(field_name, value)
        if publish_date_source is not None:
            fields.setdefault("publish_date_source", publish_date_source)
        screening = (candidate.meta_data or {}).get("screening")
        scorecard = (
            screening.get("scorecard")
            if isinstance(screening, dict)
            else None
        )
        metadata = {
            **fields,
            "candidate_id": candidate_id,
            "fact_cluster": f"candidate:{candidate_id}",
            "batch_extraction_confidence": float(item.confidence),
            "source_provider": candidate.source_provider or "",
            "deterministic_fields": sorted(deterministic_fields),
            "screening_scorecard": (
                dict(scorecard) if isinstance(scorecard, dict) else {}
            ),
        }
        target = self._session.get(TargetAccount, task.target_account_id)
        official_domains: tuple[str, ...] = ()
        if target is not None and target.website:
            from urllib.parse import urlsplit

            host = (urlsplit(target.website).hostname or "").lower().removeprefix("www.")
            official_domains = (host,) if host else ()
        source_reliability = score_source_reliability(
            candidate.canonical_url,
            official_domains=official_domains,
        ).value
        if (
            source_reliability == "UNKNOWN"
            and isinstance(scorecard, dict)
            and scorecard.get("source_tier") in {"S", "A", "B", "C"}
        ):
            source_reliability = str(scorecard["source_tier"])
        evidence = Evidence(
            id=evidence_id,
            task_id=task_id,
            workspace_id=task.workspace_id,
            dimension=dimension,
            title=title,
            snippet=item.citation_excerpt[:1000],
            url=candidate.canonical_url,
            source_type="batch_extraction",
            meta_data=metadata,
            published_at=published_at,
            source_reliability=source_reliability,
        )
        self._session.add(evidence)
        self._session.flush()
        return evidence

    def _get_or_create_candidate_lead(
        self,
        *,
        task_id: UUID,
        dimension: str,
        candidate: ResearchCandidate,
        candidate_id: str,
        rejection_reason: str,
    ) -> Evidence | None:
        screening = (candidate.meta_data or {}).get("screening")
        scorecard = screening.get("scorecard") if isinstance(screening, dict) else None
        if not isinstance(scorecard, dict):
            return None
        if (
            scorecard.get("subject_relation") != "target_exact"
            or scorecard.get("evidence_role") != "target_procurement_evidence"
            or int(scorecard.get("deterministic_score") or 0) < 120
        ):
            return None
        evidence_id = uuid5(
            NAMESPACE_URL,
            f"execution-candidate-lead/v1:{task_id}:{dimension}:{candidate_id}",
        )
        existing = self._session.get(Evidence, evidence_id)
        if existing is not None:
            return existing
        task = self._session.get(Task, task_id)
        if task is None:
            raise LookupError("候选线索所属任务不存在")
        published_at = candidate.published_at
        publish_date_source: str | None = None
        if published_at is None:
            from app.evidence.date_normalizer import infer_date_from_texts

            published_at, publish_date_source = infer_date_from_texts(
                url=candidate.canonical_url,
                title=candidate.title,
                snippet=candidate.snippet or "",
            )
        metadata = {
            "candidate_id": candidate_id,
            "fact_cluster": f"candidate:{candidate_id}",
            "fact_or_inference": "ASSUMPTION",
            "validation_status": "UNVERIFIED_SEARCH_LEAD",
            "rejection_reason": rejection_reason,
            "screening_scorecard": dict(scorecard),
            "source_provider": candidate.source_provider or "",
        }
        if publish_date_source is not None:
            metadata["publish_date_source"] = publish_date_source
        evidence = Evidence(
            id=evidence_id,
            task_id=task_id,
            workspace_id=task.workspace_id,
            dimension=dimension,
            title=candidate.title[:500],
            snippet=(candidate.snippet or candidate.title)[:1000],
            url=candidate.canonical_url,
            source_type="search_candidate_lead",
            meta_data=metadata,
            published_at=published_at,
            source_reliability="C",
            relevance_score=0.6,
            data_domain="external",
            fact_or_inference="ASSUMPTION",
            opportunity_effect="neutral",
            normalization_status="RAW",
            date_precision="DAY" if published_at is not None else "UNKNOWN",
        )
        self._session.add(evidence)
        self._session.flush()
        return evidence

    def _lock_task_for_persistence(self, task_id: UUID) -> None:
        self._session.execute(
            select(Task.id).where(Task.id == task_id).with_for_update()
        ).scalar_one()

    def _evaluate_sufficiency(
        self,
        *,
        task_id: UUID,
        dimension: str,
        policy: EvidencePolicy,
        latest_evidences: list[Evidence],
        required_fields: tuple[str, ...],
        critical_claim_ids: tuple[str, ...],
        consecutive_low_gain_batches: int,
    ) -> dict[str, Any]:
        records = list(self._session.execute(
            select(Evidence).where(Evidence.task_id == task_id, Evidence.dimension == dimension)
        ).scalars())
        runtime_by_id = {record.id: self._runtime_evidence(record) for record in records}
        result = evaluate_evidence_sufficiency(
            policy=policy,
            evidences=tuple(runtime_by_id.values()),
            latest_batch=tuple(runtime_by_id[item.id] for item in latest_evidences),
            required_fields=required_fields,
            critical_claim_ids=critical_claim_ids,
            consecutive_low_gain_batches=consecutive_low_gain_batches,
        )
        return {
            "evidence_count": result.evidence_count,
            "mandatory_gaps": list(result.mandatory_gaps),
            "is_sufficient": result.is_sufficient,
            "should_stop": result.should_stop,
            "should_expand": result.should_expand,
            "batch_novelty_ratio": result.batch_novelty_ratio,
            "batch_duplicate_ratio": result.batch_duplicate_ratio,
        }

    def _evaluate_quality(
        self,
        *,
        run_id: UUID,
        dimension: str,
        required_fields: tuple[str, ...],
        quality_thresholds: Mapping[str, float | int],
    ) -> dict[str, Any]:
        run = self._session.get(TaskRun, run_id)
        if run is None:
            raise LookupError("提取评估所属运行不存在")
        analysis_as_of = run.created_at
        if analysis_as_of.tzinfo is None or analysis_as_of.utcoffset() is None:
            analysis_as_of = analysis_as_of.replace(tzinfo=timezone.utc)
        records = list(self._session.execute(
            select(Evidence).where(
                Evidence.task_id == run.task_id,
                Evidence.dimension == dimension,
            )
        ).scalars())
        evaluation = ExtractionEvaluator().evaluate(
            [self._runtime_evidence(record) for record in records],
            DimensionGoal(
                goal=f"{dimension} Skill 提取质量",
                must_extract=list(required_fields),
            ),
            quality_thresholds=quality_thresholds,
            analysis_as_of=analysis_as_of,
        )
        return {
            "passed": evaluation.passed,
            "score": evaluation.score,
            "feedback": evaluation.feedback,
            "suggestions": list(evaluation.suggestions),
            "dimension_scores": dict(evaluation.dimension_scores),
            "analysis": dict(evaluation.analysis),
        }

    @staticmethod
    def _runtime_evidence(record: Evidence) -> RuntimeEvidence:
        return RuntimeEvidence(
            id=str(record.id),
            dimension=record.dimension,
            title=record.title,
            snippet=record.snippet,
            url=record.url,
            source_type=record.source_type,
            metadata=dict(record.meta_data or {}),
            published_at=record.published_at,
        )

    def _snapshot_content(self, candidate: ResearchCandidate) -> str:
        snapshot = (candidate.meta_data or {}).get("snapshot")
        path = snapshot.get("relative_path") if isinstance(snapshot, dict) else ""
        content = self._snapshots.read_snapshot(str(path)) if path else None
        if not content:
            raise ValueError(f"候选正文快照不可用: {candidate.candidate_id}")
        return content.decode("utf-8", errors="replace")

    def _append_event_once(self, *, task_id: UUID, run_id: UUID, stage_run_id: UUID, event_type: str, payload: dict) -> None:
        existing = self._session.execute(
            select(TaskEvent.id).where(TaskEvent.stage_run_id == stage_run_id, TaskEvent.event_type == event_type)
        ).scalar_one_or_none()
        if existing is None:
            self._events.append(
                task_id=task_id,
                run_id=run_id,
                stage_run_id=stage_run_id,
                event_type=event_type,
                payload=payload,
            )

    def _stage_for_run(self, stage_run_id: UUID, run_id: UUID) -> TaskStageRun:
        stage = self._session.get(TaskStageRun, stage_run_id)
        if stage is None:
            raise LookupError(f"工作单元不存在: {stage_run_id}")
        if stage.run_id != run_id:
            raise ValueError("工作单元不属于当前运行")
        return stage

    def _completed_asset(self, stage_run_id: UUID, run_id: UUID, stage_label: str) -> Mapping[str, Any]:
        stage = self._stage_for_run(stage_run_id, run_id)
        if stage.status != "COMPLETED":
            raise ValueError(f"{stage_label}阶段尚未完成，不能执行后继阶段")
        return stage.asset_ref

    @staticmethod
    def _fetched_candidate_ids(asset: Mapping[str, Any]) -> list[str]:
        values = list(asset.get("fetched_candidate_ids") or []) + list(asset.get("reused_candidate_ids") or [])
        if len(values) != len(set(values)) or not all(isinstance(value, str) and value for value in values):
            raise ValueError("抓取阶段资产缺少合法候选 ID")
        return values

    @staticmethod
    def _batch_descriptor(descriptor: Mapping[str, Any]) -> tuple[int, list[str]]:
        if set(descriptor) != {"index", "candidate_ids"}:
            raise ValueError("批次描述字段非法")
        index = descriptor.get("index")
        ids = descriptor.get("candidate_ids")
        if type(index) is not int or index < 1 or not isinstance(ids, list) or not ids:
            raise ValueError("批次描述非法")
        if len(ids) != len(set(ids)) or not all(isinstance(value, str) and value for value in ids):
            raise ValueError("批次描述包含非法候选 ID")
        return index, ids

    def _candidates_by_ids(self, task_id: UUID, candidate_ids: list[str]) -> dict[str, ResearchCandidate]:
        records = self._session.execute(
            select(ResearchCandidate).where(
                ResearchCandidate.task_id == task_id,
                ResearchCandidate.candidate_id.in_(candidate_ids),
            )
        ).scalars()
        by_id = {record.candidate_id: record for record in records}
        missing = [candidate_id for candidate_id in candidate_ids if candidate_id not in by_id]
        if missing:
            raise ValueError(f"候选资产不存在: {missing}")
        return by_id
