"""可配置资格框架的版本发布与可审计、确定性资格评估。"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from uuid import UUID

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db.models import (
    Claim,
    OpportunityHypothesis,
    OpportunityHypothesisClaim,
    OpportunityQualificationCard,
    OpportunityQualificationFramework,
    Workspace,
)
from app.opportunities.qualification_schema import (
    QualificationAssessmentInput,
    QualificationCriterionAssessment,
    QualificationFrameworkPublishInput,
)


_KEY_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")
_CRITERION_PATTERN = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
_STATUS_SCORE = {
    "CUSTOMER_CONFIRMED": 1.0,
    "SUPPORTED": 0.6,
    "UNKNOWN": 0.0,
    "NEGATIVE": 0.0,
}


@dataclass(frozen=True)
class QualificationFrameworkPublishResult:
    framework: OpportunityQualificationFramework
    created: bool


@dataclass(frozen=True)
class QualificationAssessmentResult:
    card: OpportunityQualificationCard
    created: bool


class OpportunityQualificationService:
    def __init__(self, db: Session) -> None:
        self._db = db

    def publish_framework(
        self,
        *,
        workspace_id: UUID,
        published_by: UUID,
        payload: QualificationFrameworkPublishInput,
    ) -> QualificationFrameworkPublishResult:
        normalized = self._normalize_framework(payload)
        content_hash = self._hash(normalized)
        workspace = (
            self._db.query(Workspace)
            .filter(Workspace.id == workspace_id)
            .with_for_update()
            .one_or_none()
        )
        if workspace is None:
            raise LookupError("Workspace 不存在")
        versions = (
            self._db.query(OpportunityQualificationFramework)
            .filter(
                OpportunityQualificationFramework.workspace_id == workspace_id,
                OpportunityQualificationFramework.framework_key == normalized["framework_key"],
            )
            .with_for_update()
            .all()
        )
        now = datetime.now(timezone.utc)
        same_content = next(
            (item for item in versions if item.content_hash == content_hash),
            None,
        )
        for item in versions:
            if item.status == "PUBLISHED" and item is not same_content:
                item.status = "ARCHIVED"
        if same_content is not None:
            same_content.status = "PUBLISHED"
            same_content.published_at = now
            self._db.flush()
            return QualificationFrameworkPublishResult(same_content, False)

        framework = OpportunityQualificationFramework(
            workspace_id=workspace_id,
            framework_key=normalized["framework_key"],
            version_no=max((item.version_no for item in versions), default=0) + 1,
            name=normalized["name"],
            methodology=normalized["methodology"],
            criteria=normalized["criteria"],
            hard_blocker_rules=normalized["hard_blocker_rules"],
            minimum_score=normalized["minimum_score"],
            minimum_completeness=normalized["minimum_completeness"],
            status="PUBLISHED",
            content_hash=content_hash,
            created_by=published_by,
            published_at=now,
            created_at=now,
        )
        self._db.add(framework)
        self._db.flush()
        return QualificationFrameworkPublishResult(framework, True)

    def assess(
        self,
        *,
        workspace_id: UUID,
        hypothesis_id: UUID,
        assessed_by: UUID,
        payload: QualificationAssessmentInput,
    ) -> QualificationAssessmentResult:
        hypothesis = (
            self._db.query(OpportunityHypothesis)
            .filter(
                OpportunityHypothesis.id == hypothesis_id,
                OpportunityHypothesis.workspace_id == workspace_id,
            )
            .with_for_update()
            .one_or_none()
        )
        if hypothesis is None:
            if self._db.get(OpportunityHypothesis, hypothesis_id) is not None:
                raise PermissionError("商机假设不属于当前 Workspace")
            raise LookupError("商机假设不存在")

        framework = (
            self._db.query(OpportunityQualificationFramework)
            .filter(
                OpportunityQualificationFramework.id == payload.framework_id,
                OpportunityQualificationFramework.workspace_id == workspace_id,
            )
            .one_or_none()
        )
        if framework is None:
            if self._db.get(OpportunityQualificationFramework, payload.framework_id) is not None:
                raise PermissionError("资格框架不属于当前 Workspace")
            raise LookupError("资格框架不存在")
        if framework.status != "PUBLISHED":
            raise ValueError("只能使用当前已发布的资格框架进行新评估")

        definitions = {item["key"]: item for item in framework.criteria}
        assessments: dict[str, QualificationCriterionAssessment] = {}
        for item in payload.criteria:
            key = item.criterion_key.strip()
            if key not in definitions:
                raise ValueError(f"资格标准中不存在评估项：{key}")
            if key in assessments:
                raise ValueError(f"评估项重复：{key}")
            if item.status not in _STATUS_SCORE:
                raise ValueError(f"评估状态不受支持：{item.status}")
            assessments[key] = item

        all_claim_ids = {
            claim_id
            for item in assessments.values()
            for claim_id in item.claim_ids
        }
        claims = self._linked_claims(
            workspace_id=workspace_id,
            hypothesis_id=hypothesis.id,
            claim_ids=all_claim_ids,
        )

        criterion_snapshots: list[dict] = []
        missing_fields: list[str] = []
        weighted_score = 0.0
        total_weight = sum(float(item["weight"]) for item in framework.criteria)
        completed = 0
        for definition in framework.criteria:
            key = definition["key"]
            item = assessments.get(key)
            status = item.status if item is not None else "UNKNOWN"
            claim_ids = tuple(item.claim_ids) if item is not None else ()
            note = self._optional_text(
                item.note if item is not None else "",
                field=f"评估项 {key} 备注",
                max_length=2000,
            )
            self._validate_evidence_status(key, status, claim_ids, claims)
            factor = _STATUS_SCORE[status]
            weight = float(definition["weight"])
            weighted_score += weight * factor
            if status != "UNKNOWN":
                completed += 1
            if definition["required"] and status == "UNKNOWN":
                missing_fields.append(key)
            criterion_snapshots.append(
                {
                    "key": key,
                    "label": definition["label"],
                    "weight": weight,
                    "required": bool(definition["required"]),
                    "status": status,
                    "score_factor": factor,
                    "claim_ids": [str(claim_id) for claim_id in sorted(claim_ids, key=str)],
                    "note": note,
                }
            )

        blockers = [
            {
                "code": rule["code"],
                "criterion_key": rule["criterion_key"],
                "message": rule["message"],
                "matched_status": next(
                    item["status"]
                    for item in criterion_snapshots
                    if item["key"] == rule["criterion_key"]
                ),
            }
            for rule in framework.hard_blocker_rules
            if any(
                item["key"] == rule["criterion_key"]
                and item["status"] == rule["when_status"]
                for item in criterion_snapshots
            )
        ]
        score = round(weighted_score / total_weight, 6)
        completeness = round(completed / len(framework.criteria), 6)
        if blockers:
            gate_result = "FAIL"
        elif score < framework.minimum_score or completeness < framework.minimum_completeness:
            gate_result = "INCOMPLETE"
        else:
            gate_result = "PASS"

        normalized_input = {
            "framework_id": str(framework.id),
            "hypothesis_id": str(hypothesis.id),
            "criteria": criterion_snapshots,
            "summary": self._optional_text(payload.summary, field="资格评估摘要", max_length=4000),
        }
        input_hash = self._hash(normalized_input)
        existing = (
            self._db.query(OpportunityQualificationCard)
            .filter(
                OpportunityQualificationCard.hypothesis_id == hypothesis.id,
                OpportunityQualificationCard.input_hash == input_hash,
            )
            .one_or_none()
        )
        if existing is not None:
            return QualificationAssessmentResult(existing, False)

        assessment_no = (
            self._db.query(func.max(OpportunityQualificationCard.assessment_no))
            .filter(OpportunityQualificationCard.hypothesis_id == hypothesis.id)
            .scalar()
            or 0
        ) + 1
        summary = normalized_input["summary"] or self._default_summary(
            gate_result=gate_result,
            score=score,
            completeness=completeness,
            blockers=len(blockers),
            missing=len(missing_fields),
        )
        now = datetime.now(timezone.utc)
        card = OpportunityQualificationCard(
            workspace_id=workspace_id,
            hypothesis_id=hypothesis.id,
            framework_id=framework.id,
            assessment_no=assessment_no,
            framework_key=framework.framework_key,
            framework_version=str(framework.version_no),
            criteria=criterion_snapshots,
            hard_blockers=blockers,
            missing_fields=missing_fields,
            gate_result=gate_result,
            score=score,
            information_completeness=completeness,
            summary=summary,
            input_hash=input_hash,
            assessed_by=assessed_by,
            assessed_at=now,
            created_at=now,
        )
        self._db.add(card)
        self._db.flush()
        return QualificationAssessmentResult(card, True)

    def list_published_frameworks(self, *, workspace_id: UUID) -> list[OpportunityQualificationFramework]:
        return (
            self._db.query(OpportunityQualificationFramework)
            .filter(
                OpportunityQualificationFramework.workspace_id == workspace_id,
                OpportunityQualificationFramework.status == "PUBLISHED",
            )
            .order_by(
                OpportunityQualificationFramework.framework_key.asc(),
                OpportunityQualificationFramework.version_no.desc(),
            )
            .all()
        )

    def list_assessments(
        self,
        *,
        workspace_id: UUID,
        hypothesis_id: UUID,
    ) -> list[OpportunityQualificationCard]:
        hypothesis = self._db.get(OpportunityHypothesis, hypothesis_id)
        if hypothesis is None:
            raise LookupError("商机假设不存在")
        if hypothesis.workspace_id != workspace_id:
            raise PermissionError("商机假设不属于当前 Workspace")
        return (
            self._db.query(OpportunityQualificationCard)
            .filter(
                OpportunityQualificationCard.workspace_id == workspace_id,
                OpportunityQualificationCard.hypothesis_id == hypothesis_id,
            )
            .order_by(OpportunityQualificationCard.assessment_no.desc())
            .all()
        )

    def _normalize_framework(self, payload: QualificationFrameworkPublishInput) -> dict:
        framework_key = payload.framework_key.strip().upper()
        if not _KEY_PATTERN.fullmatch(framework_key):
            raise ValueError("framework_key 必须为 2 到 64 位大写字母、数字或下划线")
        name = self._required_text(payload.name, field="资格框架名称", max_length=255)
        if payload.methodology not in {"CUSTOM", "MEDDPICC", "BANT", "SPICED", "HYBRID"}:
            raise ValueError("资格框架方法论不受支持")
        if not 0 <= float(payload.minimum_score) <= 1:
            raise ValueError("最低得分必须在 0 到 1 之间")
        if not 0 <= float(payload.minimum_completeness) <= 1:
            raise ValueError("最低完整度必须在 0 到 1 之间")
        if not payload.criteria:
            raise ValueError("资格框架至少需要一个评估项")

        criteria: list[dict] = []
        criterion_keys: set[str] = set()
        for item in payload.criteria:
            key = item.key.strip().lower()
            if not _CRITERION_PATTERN.fullmatch(key):
                raise ValueError("评估项 key 必须为 2 到 64 位小写字母、数字或下划线")
            if key in criterion_keys:
                raise ValueError(f"评估项 key 重复：{key}")
            weight = float(item.weight)
            if weight <= 0 or weight > 100:
                raise ValueError(f"评估项 {key} 权重必须大于 0 且不超过 100")
            criterion_keys.add(key)
            criteria.append(
                {
                    "key": key,
                    "label": self._required_text(item.label, field=f"评估项 {key} 名称", max_length=255),
                    "weight": weight,
                    "required": bool(item.required),
                }
            )

        blocker_rules: list[dict] = []
        blocker_codes: set[str] = set()
        for rule in payload.hard_blocker_rules:
            criterion_key = rule.criterion_key.strip().lower()
            if criterion_key not in criterion_keys:
                raise ValueError(f"硬阻断规则引用了不存在的评估项：{criterion_key}")
            code = rule.code.strip().upper()
            if not _KEY_PATTERN.fullmatch(code):
                raise ValueError("硬阻断 code 必须为 2 到 64 位大写字母、数字或下划线")
            if code in blocker_codes:
                raise ValueError(f"硬阻断 code 重复：{code}")
            if rule.when_status not in _STATUS_SCORE:
                raise ValueError(f"硬阻断状态不受支持：{rule.when_status}")
            blocker_codes.add(code)
            blocker_rules.append(
                {
                    "criterion_key": criterion_key,
                    "code": code,
                    "message": self._required_text(rule.message, field=f"硬阻断 {code} 说明", max_length=1000),
                    "when_status": rule.when_status,
                }
            )
        return {
            "framework_key": framework_key,
            "name": name,
            "methodology": payload.methodology,
            "criteria": criteria,
            "hard_blocker_rules": blocker_rules,
            "minimum_score": round(float(payload.minimum_score), 6),
            "minimum_completeness": round(float(payload.minimum_completeness), 6),
        }

    def _linked_claims(
        self,
        *,
        workspace_id: UUID,
        hypothesis_id: UUID,
        claim_ids: set[UUID],
    ) -> dict[UUID, Claim]:
        if not claim_ids:
            return {}
        claims = (
            self._db.query(Claim)
            .join(OpportunityHypothesisClaim, OpportunityHypothesisClaim.claim_id == Claim.id)
            .filter(
                OpportunityHypothesisClaim.hypothesis_id == hypothesis_id,
                Claim.workspace_id == workspace_id,
                Claim.id.in_(claim_ids),
            )
            .all()
        )
        result = {claim.id: claim for claim in claims}
        missing = claim_ids - result.keys()
        if missing:
            raise ValueError("资格评估只能引用当前商机假设已绑定的 Claim")
        return result

    @staticmethod
    def _validate_evidence_status(
        key: str,
        status: str,
        claim_ids: tuple[UUID, ...],
        claims: dict[UUID, Claim],
    ) -> None:
        if status == "UNKNOWN" and claim_ids:
            raise ValueError(f"未知评估项 {key} 不得引用 Claim")
        if status == "CUSTOMER_CONFIRMED" and not any(
            claims[claim_id].status == "CUSTOMER_CONFIRMED" for claim_id in claim_ids
        ):
            raise ValueError(f"评估项 {key} 缺少 CUSTOMER_CONFIRMED Claim")
        if status == "SUPPORTED" and not any(
            claims[claim_id].status in {"SUPPORTED", "CUSTOMER_CONFIRMED"}
            for claim_id in claim_ids
        ):
            raise ValueError(f"评估项 {key} 缺少有效支持 Claim")

    @staticmethod
    def _required_text(value: str, *, field: str, max_length: int) -> str:
        text = value.strip()
        if not text or len(text) > max_length:
            raise ValueError(f"{field}必须为 1 到 {max_length} 个字符")
        return text

    @staticmethod
    def _optional_text(value: str, *, field: str, max_length: int) -> str:
        text = value.strip()
        if len(text) > max_length:
            raise ValueError(f"{field}不得超过 {max_length} 个字符")
        return text

    @staticmethod
    def _default_summary(
        *, gate_result: str, score: float, completeness: float, blockers: int, missing: int
    ) -> str:
        return (
            f"资格结果 {gate_result}；得分 {score:.1%}；信息完整度 {completeness:.1%}；"
            f"硬阻断 {blockers} 项；缺失必填项 {missing} 项。"
        )

    @staticmethod
    def _hash(payload: dict) -> bytes:
        return sha256(
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).digest()
