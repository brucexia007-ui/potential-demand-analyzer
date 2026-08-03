"""耐久执行链中的声明式 evaluation Skill 阶段。"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Mapping
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy.orm import Session

from app.config_center.security_config import get_model_data_policy
from app.db.models import Evidence, TargetAccount, Task
from app.llm.model_router import ModelRouter
from app.opportunities.product_fit_service import ProductFitService
from app.skills.runtime_evaluator import RuntimeEvaluationResult, SkillRuntimeEvaluator


class SkillEvaluationStageHandler:
    def __init__(
        self,
        session: Session,
        *,
        evaluator: SkillRuntimeEvaluator | None = None,
    ) -> None:
        self._session = session
        model = ModelRouter.from_settings().resolve("skill_evaluator", "high")
        self._evaluator = evaluator or SkillRuntimeEvaluator(model=model)

    def execute(
        self,
        *,
        task_id: UUID,
        run_id: UUID,
        stage_run_id: UUID,
        contract: Mapping[str, Any],
    ) -> dict[str, Any]:
        skill_name = str(contract.get("name") or "")
        if not skill_name:
            raise ValueError("evaluation WorkUnit 缺少 Skill 名称")
        if skill_name == "matching-product-capabilities":
            return self._execute_product_fit(
                task_id=task_id,
                run_id=run_id,
                stage_run_id=stage_run_id,
                skill_name=skill_name,
            )
        return self._execute_model_evaluator(
            task_id=task_id,
            run_id=run_id,
            stage_run_id=stage_run_id,
            contract=contract,
        )

    def _execute_model_evaluator(
        self,
        *,
        task_id: UUID,
        run_id: UUID,
        stage_run_id: UUID,
        contract: Mapping[str, Any],
    ) -> dict[str, Any]:
        task = self._task(task_id)
        requested_domains = contract.get("data_domains")
        if not isinstance(requested_domains, list) or not requested_domains:
            raise ValueError("evaluation Skill 必须声明 data_domains")
        allowed_domains, blocked_domains = self._allowed_model_domains(requested_domains)
        evidences = (
            self._session.query(Evidence)
            .filter(Evidence.task_id == task_id, Evidence.data_domain.in_(allowed_domains))
            .order_by(Evidence.dimension, Evidence.captured_at, Evidence.id)
            .all()
            if allowed_domains
            else []
        )
        evidence_payload = [self._evidence_payload(item) for item in evidences]
        model_contract = {
            key: contract[key]
            for key in (
                "name", "description", "questions", "output_fields",
                "stop_conditions", "budget", "references",
            )
        }
        self._session.commit()
        result = self._evaluator.evaluate(
            contract=model_contract,
            evidences=evidence_payload,
        )
        derived_data_domain = self._derived_data_domain(
            [item.data_domain for item in evidences]
        )
        self._lock_task(task_id)
        evidence_ids = self._persist_result(
            task=task,
            run_id=run_id,
            stage_run_id=stage_run_id,
            skill_name=str(contract["name"]),
            result=result,
            data_domain=derived_data_domain,
        )
        return {
            "skill_name": contract["name"],
            "status": "COMPLETED",
            "evidence_ids": evidence_ids,
            "summary": result.summary,
            "unknowns": list(result.unknowns),
            "input_evidence_count": len(evidence_payload),
            "blocked_data_domains": blocked_domains,
            "model": result.model,
            "provider": result.provider,
            "usage": result.usage,
        }

    def _execute_product_fit(
        self,
        *,
        task_id: UUID,
        run_id: UUID,
        stage_run_id: UUID,
        skill_name: str,
    ) -> dict[str, Any]:
        task = self._task(task_id)
        target = self._session.get(TargetAccount, task.target_account_id)
        if target is None or target.workspace_id != task.workspace_id:
            raise ValueError("evaluation WorkUnit 的目标企业归属非法")
        requirements: set[str] = set()
        qualifications: set[str] = set()
        for evidence in self._session.query(Evidence).filter(Evidence.task_id == task_id).all():
            metadata = dict(evidence.meta_data or {})
            fields = metadata.get("evaluation_fields")
            if isinstance(fields, dict):
                requirement = fields.get("requirement_key") or fields.get("capability_key")
                gap_status = str(fields.get("gap_status") or "").upper()
                if isinstance(requirement, str) and requirement.strip() and gap_status not in {
                    "", "NO_GAP", "SATISFIED", "UNKNOWN",
                }:
                    requirements.add(requirement.strip())
            raw_qualifications = metadata.get("mandatory_qualifications") or []
            if isinstance(raw_qualifications, list):
                qualifications.update(
                    item.strip()
                    for item in raw_qualifications
                    if isinstance(item, str) and item.strip()
                )
        if task.capability_profile_id is None:
            fit_payload = {
                "fit_verified": False,
                "hard_fit_blocker": False,
                "recommendation_score": 0.0,
                "confidence": 1.0,
                "information_completeness": 0.0,
                "matched_product_ids": [],
                "matched_requirements": [],
                "unmatched_requirements": sorted(requirements),
                "blockers": [],
                "missing_information": ["任务未选择能力档案"],
                "positive_factors": [],
                "negative_factors": ["未选择能力档案时不得输出产品适配结论"],
            }
            status = "SKIPPED_NO_CAPABILITY_PROFILE"
        else:
            fit = ProductFitService(self._session).assess(
                workspace_id=task.workspace_id,
                profile_id=task.capability_profile_id,
                requirement_keys=tuple(sorted(requirements)),
                target_industry=target.industry,
                target_region=target.region,
                mandatory_qualifications=tuple(sorted(qualifications)),
                analysis_as_of_date=datetime.now(timezone.utc),
            )
            fit_payload = {
                "fit_verified": fit.fit_verified,
                "hard_fit_blocker": fit.hard_blocker,
                "recommendation_score": fit.recommendation_score,
                "confidence": fit.confidence,
                "information_completeness": fit.information_completeness,
                "matched_product_ids": [str(item) for item in fit.matched_product_ids],
                "matched_requirements": list(fit.matched_requirements),
                "unmatched_requirements": list(fit.unmatched_requirements),
                "blockers": list(fit.blockers),
                "missing_information": list(fit.missing_information),
                "positive_factors": list(fit.positive_factors),
                "negative_factors": list(fit.negative_factors),
            }
            status = "COMPLETED"
        evidence_id = self._persist_deterministic_evidence(
            task=task,
            run_id=run_id,
            stage_run_id=stage_run_id,
            skill_name=skill_name,
            index=0,
            title="产品能力适配裁决",
            finding=json.dumps(fit_payload, ensure_ascii=False, sort_keys=True),
            fields=fit_payload,
            supporting_evidence_ids=[],
            counter_evidence_ids=[],
            confidence=float(fit_payload["confidence"]),
            opportunity_effect=(
                "positive" if fit_payload["fit_verified"]
                else ("risk" if fit_payload["hard_fit_blocker"] else "neutral")
            ),
            data_domain="internal",
        )
        return {
            "skill_name": skill_name,
            "status": status,
            "evidence_ids": [evidence_id],
            "product_fit": fit_payload,
            "input_evidence_count": len(requirements),
            "blocked_data_domains": [],
            "model": None,
            "provider": "deterministic",
            "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
        }

    def _allowed_model_domains(self, requested_domains: list[Any]) -> tuple[list[str], list[dict[str, str]]]:
        policy = get_model_data_policy(self._session)
        model = self._evaluator.model
        allowed: list[str] = []
        blocked: list[dict[str, str]] = []
        for raw_domain in requested_domains:
            if raw_domain not in {"external", "customer_private", "internal"}:
                raise ValueError(f"evaluation Skill 数据域非法: {raw_domain}")
            decision = policy.evaluate(domain=raw_domain, model=model)
            if decision.allowed:
                allowed.append(raw_domain)
            else:
                blocked.append({"domain": raw_domain, "reason": decision.reason})
        return allowed, blocked

    def _persist_result(
        self,
        *,
        task: Task,
        run_id: UUID,
        stage_run_id: UUID,
        skill_name: str,
        result: RuntimeEvaluationResult,
        data_domain: str,
    ) -> list[str]:
        ids: list[str] = []
        for index, item in enumerate(result.items):
            ids.append(self._persist_deterministic_evidence(
                task=task,
                run_id=run_id,
                stage_run_id=stage_run_id,
                skill_name=skill_name,
                index=index,
                title=item.title,
                finding=item.finding,
                fields=item.fields,
                supporting_evidence_ids=list(item.supporting_evidence_ids),
                counter_evidence_ids=list(item.counter_evidence_ids),
                confidence=item.confidence,
                opportunity_effect=item.opportunity_effect,
                data_domain=data_domain,
            ))
        return ids

    def _persist_deterministic_evidence(
        self,
        *,
        task: Task,
        run_id: UUID,
        stage_run_id: UUID,
        skill_name: str,
        index: int,
        title: str,
        finding: str,
        fields: Mapping[str, Any],
        supporting_evidence_ids: list[str],
        counter_evidence_ids: list[str],
        confidence: float,
        opportunity_effect: str,
        data_domain: str,
    ) -> str:
        fingerprint = json.dumps(
            {
                "task_id": str(task.id),
                "run_id": str(run_id),
                "skill_name": skill_name,
                "index": index,
                "title": title,
                "finding": finding,
                "fields": fields,
                "supporting_evidence_ids": supporting_evidence_ids,
                "counter_evidence_ids": counter_evidence_ids,
            },
            ensure_ascii=False,
            sort_keys=True,
            default=str,
            separators=(",", ":"),
        )
        evidence_id = uuid5(NAMESPACE_URL, fingerprint)
        evidence = self._session.get(Evidence, evidence_id)
        if evidence is None:
            now = datetime.now(timezone.utc)
            metadata = {
                "evaluation_skill": skill_name,
                "evaluation_fields": dict(fields),
                "supporting_evidence_ids": supporting_evidence_ids,
                "counter_evidence_ids": counter_evidence_ids,
                "confidence": confidence,
                "fact_or_inference": "INFERENCE",
                "opportunity_effect": opportunity_effect,
                "run_id": str(run_id),
                "stage_run_id": str(stage_run_id),
            }
            for key in (
                "capability_status", "requirement_key", "gap_status",
                "trigger_type", "window_status", "hard_fit_blocker",
            ):
                if key in fields:
                    metadata[key] = fields[key]
            evidence = Evidence(
                id=evidence_id,
                workspace_id=task.workspace_id,
                task_id=task.id,
                dimension=skill_name,
                title=title[:500],
                snippet=finding,
                url=f"urn:skill-evaluation:{skill_name}",
                source_type="skill_evaluation",
                meta_data=metadata,
                captured_at=now,
                fetched_at=now,
                content_hash=hashlib.sha256(fingerprint.encode("utf-8")).hexdigest(),
                source_reliability="B",
                relevance_score=confidence,
                freshness_score=1.0,
                data_domain=data_domain,
                fact_or_inference="INFERENCE",
                opportunity_effect=opportunity_effect,
                normalization_status="NORMALIZED",
                date_precision="UNKNOWN",
            )
            self._session.add(evidence)
            self._session.flush()
        return str(evidence.id)

    def _task(self, task_id: UUID) -> Task:
        task = self._session.get(Task, task_id)
        if task is None:
            raise LookupError("evaluation WorkUnit 对应任务不存在")
        if task.workspace_id is None:
            raise ValueError("evaluation WorkUnit 对应任务缺少 Workspace")
        return task

    def _lock_task(self, task_id: UUID) -> None:
        self._session.query(Task).filter(Task.id == task_id).with_for_update().one()

    @staticmethod
    def _evidence_payload(item: Evidence) -> dict[str, Any]:
        return {
            "id": str(item.id),
            "dimension": item.dimension,
            "title": item.title,
            "snippet": item.snippet,
            "url": item.url,
            "source_type": item.source_type,
            "data_domain": item.data_domain,
            "published_at": item.published_at,
            "captured_at": item.captured_at,
            "meta_data": dict(item.meta_data or {}),
        }

    @staticmethod
    def _derived_data_domain(domains: list[str]) -> str:
        if "customer_private" in domains:
            return "customer_private"
        if "internal" in domains:
            return "internal"
        return "external"
