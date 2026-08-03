"""竞争作战服务：客户侧判断与我方内部差异化使用不同证据域。"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from uuid import UUID

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db.models import (
    CapabilityKnowledgeDocument,
    Claim,
    CompetitiveBattlecard,
    Opportunity,
    OpportunityCompetitor,
    Task,
)
from app.opportunities.competitive_schema import CompetitiveBattlecardInput, CompetitorInput


_COMPETITOR_TYPES = {
    "COMMERCIAL_VENDOR", "INCUMBENT_VENDOR", "CUSTOMER_SELF_BUILD",
    "STATUS_QUO", "DELAY", "NO_INVESTMENT",
}
_TRUTH = {"PUBLIC_EVIDENCE", "SALES_JUDGMENT", "CUSTOMER_CONFIRMED"}
_CUSTOMER_SECTIONS = {
    "competitor_strengths",
    "competitor_weaknesses",
    "customer_decision_criteria",
    "must_win_metrics",
}
_INTERNAL_SECTIONS = {"our_differentiators", "our_risks", "ecosystem_partners"}


@dataclass(frozen=True)
class BattlecardResult:
    battlecard: CompetitiveBattlecard
    created: bool


class OpportunityCompetitiveService:
    def __init__(self, db: Session) -> None:
        self._db = db

    def create_competitor(
        self,
        *,
        workspace_id: UUID,
        opportunity_id: UUID,
        created_by: UUID,
        payload: CompetitorInput,
    ) -> OpportunityCompetitor:
        opportunity = self._opportunity(workspace_id, opportunity_id)
        if payload.competitor_type not in _COMPETITOR_TYPES:
            raise ValueError("竞争类型不受支持")
        if payload.truth_status not in _TRUTH:
            raise ValueError("竞争判断真实性状态不受支持")
        name = self._optional(payload.name, 255, "竞争对象名称")
        if payload.competitor_type in {"COMMERCIAL_VENDOR", "INCUMBENT_VENDOR"} and name is None:
            raise ValueError("商业竞品或现有供应商必须填写名称")
        claim = self._claim(
            workspace_id=workspace_id,
            account_id=opportunity.target_account_id,
            claim_id=payload.source_claim_id,
            required=payload.truth_status != "SALES_JUDGMENT",
        )
        if payload.truth_status == "CUSTOMER_CONFIRMED" and (
            claim is None or claim.status != "CUSTOMER_CONFIRMED"
        ):
            raise ValueError("客户确认的竞争判断必须引用 CUSTOMER_CONFIRMED Claim")
        if payload.truth_status == "PUBLIC_EVIDENCE" and (
            claim is None or claim.status not in {"SUPPORTED", "CUSTOMER_CONFIRMED"}
        ):
            raise ValueError("公开竞争判断必须引用已支持的 Claim")
        now = datetime.now(timezone.utc)
        competitor = OpportunityCompetitor(
            workspace_id=workspace_id,
            opportunity_id=opportunity.id,
            competitor_type=payload.competitor_type,
            name=name,
            truth_status=payload.truth_status,
            source_claim_id=claim.id if claim is not None else None,
            status="ACTIVE",
            created_by=created_by,
            created_at=now,
            updated_at=now,
        )
        self._db.add(competitor)
        self._db.flush()
        return competitor

    def dismiss_competitor(
        self,
        *,
        workspace_id: UUID,
        competitor_id: UUID,
    ) -> OpportunityCompetitor:
        competitor = self._competitor(workspace_id, competitor_id, lock=True)
        competitor.status = "DISMISSED"
        competitor.updated_at = datetime.now(timezone.utc)
        self._db.flush()
        return competitor

    def list_competitors(
        self,
        *,
        workspace_id: UUID,
        opportunity_id: UUID,
        include_dismissed: bool = False,
    ) -> list[OpportunityCompetitor]:
        self._opportunity(workspace_id, opportunity_id)
        query = self._db.query(OpportunityCompetitor).filter(
            OpportunityCompetitor.workspace_id == workspace_id,
            OpportunityCompetitor.opportunity_id == opportunity_id,
        )
        if not include_dismissed:
            query = query.filter(OpportunityCompetitor.status == "ACTIVE")
        return query.order_by(OpportunityCompetitor.created_at.asc()).all()

    def create_battlecard(
        self,
        *,
        workspace_id: UUID,
        competitor_id: UUID,
        created_by: UUID,
        payload: CompetitiveBattlecardInput,
    ) -> BattlecardResult:
        competitor = self._competitor(workspace_id, competitor_id, lock=True)
        if competitor.status != "ACTIVE":
            raise ValueError("已排除的竞争对象不能创建新作战卡")
        opportunity = self._opportunity(workspace_id, competitor.opportunity_id)
        normalized = self._normalize_battlecard(
            workspace_id=workspace_id,
            account_id=opportunity.target_account_id,
            payload=payload,
        )
        input_hash = self._hash(normalized)
        existing = (
            self._db.query(CompetitiveBattlecard)
            .filter(
                CompetitiveBattlecard.competitor_id == competitor.id,
                CompetitiveBattlecard.input_hash == input_hash,
            )
            .one_or_none()
        )
        if existing is not None:
            return BattlecardResult(existing, False)
        version_no = (
            self._db.query(func.max(CompetitiveBattlecard.version_no))
            .filter(CompetitiveBattlecard.competitor_id == competitor.id)
            .scalar()
            or 0
        ) + 1
        card = CompetitiveBattlecard(
            workspace_id=workspace_id,
            competitor_id=competitor.id,
            version_no=version_no,
            input_hash=input_hash,
            created_by=created_by,
            created_at=datetime.now(timezone.utc),
            **normalized,
        )
        self._db.add(card)
        self._db.flush()
        return BattlecardResult(card, True)

    def list_battlecards(
        self,
        *,
        workspace_id: UUID,
        competitor_id: UUID,
    ) -> list[CompetitiveBattlecard]:
        self._competitor(workspace_id, competitor_id, lock=False)
        return (
            self._db.query(CompetitiveBattlecard)
            .filter(
                CompetitiveBattlecard.workspace_id == workspace_id,
                CompetitiveBattlecard.competitor_id == competitor_id,
            )
            .order_by(CompetitiveBattlecard.version_no.desc())
            .all()
        )

    def _normalize_battlecard(
        self,
        *,
        workspace_id: UUID,
        account_id: UUID,
        payload: CompetitiveBattlecardInput,
    ) -> dict:
        contract_status = payload.current_contract.status
        if contract_status not in {"UNKNOWN", "ACTIVE", "EXPIRED", "RENEWAL_WINDOW", "NO_CONTRACT"}:
            raise ValueError("合同状态不受支持")
        contract_claims = tuple(dict.fromkeys(payload.current_contract.source_claim_ids))
        if contract_status != "UNKNOWN" and not contract_claims:
            raise ValueError("合同或无合同判断必须引用 Claim")
        for claim_id in contract_claims:
            self._claim(
                workspace_id=workspace_id,
                account_id=account_id,
                claim_id=claim_id,
                required=True,
            )
        normalized: dict = {
            "current_contract": {
                "status": contract_status,
                "summary": self._text(payload.current_contract.summary, 2000, "当前合同摘要"),
                "source_claim_ids": [str(value) for value in contract_claims],
            },
            "switching_cost_assessment": self._text(
                payload.switching_cost_assessment, 4000, "切换成本判断"
            ),
            "prohibited_commitments": self._strings(
                payload.prohibited_commitments, 1000, "禁止承诺项"
            ),
            "discovery_questions": self._strings(
                payload.discovery_questions, 1000, "竞争性发现问题"
            ),
        }
        for section in _CUSTOMER_SECTIONS | _INTERNAL_SECTIONS:
            normalized[section] = self._evidence_items(
                workspace_id=workspace_id,
                account_id=account_id,
                section=section,
                items=getattr(payload, section),
            )
        return normalized

    def _evidence_items(
        self,
        *,
        workspace_id: UUID,
        account_id: UUID,
        section: str,
        items: tuple,
    ) -> list[dict]:
        result: list[dict] = []
        seen: set[tuple[str, str, str]] = set()
        for item in items:
            text = self._required(item.text, 2000, f"{section} 内容")
            if section in _CUSTOMER_SECTIONS and item.source_domain == "internal":
                raise ValueError(f"{section} 不能使用内部能力资料证明客户侧事实")
            if section in _INTERNAL_SECTIONS and item.source_domain != "internal":
                raise ValueError(f"{section} 必须引用内部能力资料")
            if item.source_domain == "internal":
                document = self._db.get(CapabilityKnowledgeDocument, item.source_id)
                if document is None or document.workspace_id != workspace_id or document.status != "READY":
                    raise ValueError("内部差异化只能引用当前 Workspace 已就绪的能力资料")
            else:
                self._claim(
                    workspace_id=workspace_id,
                    account_id=account_id,
                    claim_id=item.source_id,
                    required=True,
                )
            key = (text, item.source_domain, str(item.source_id))
            if key in seen:
                continue
            seen.add(key)
            result.append({
                "text": text,
                "source_domain": item.source_domain,
                "source_id": str(item.source_id),
            })
        return result

    def _opportunity(self, workspace_id: UUID, opportunity_id: UUID) -> Opportunity:
        opportunity = self._db.get(Opportunity, opportunity_id)
        if opportunity is None:
            raise LookupError("正式商机不存在")
        if opportunity.workspace_id != workspace_id:
            raise PermissionError("正式商机不属于当前 Workspace")
        return opportunity

    def _competitor(
        self,
        workspace_id: UUID,
        competitor_id: UUID,
        *,
        lock: bool,
    ) -> OpportunityCompetitor:
        query = self._db.query(OpportunityCompetitor).filter(
            OpportunityCompetitor.id == competitor_id,
            OpportunityCompetitor.workspace_id == workspace_id,
        )
        competitor = (query.with_for_update() if lock else query).one_or_none()
        if competitor is None:
            if self._db.get(OpportunityCompetitor, competitor_id) is not None:
                raise PermissionError("竞争对象不属于当前 Workspace")
            raise LookupError("竞争对象不存在")
        return competitor

    def _claim(
        self,
        *,
        workspace_id: UUID,
        account_id: UUID,
        claim_id: UUID | None,
        required: bool,
    ) -> Claim | None:
        if claim_id is None:
            if required:
                raise ValueError("竞争判断必须引用 Claim")
            return None
        claim = (
            self._db.query(Claim)
            .join(Task, Task.id == Claim.task_id)
            .filter(
                Claim.id == claim_id,
                Claim.workspace_id == workspace_id,
                Task.workspace_id == workspace_id,
                Task.target_account_id == account_id,
            )
            .one_or_none()
        )
        if claim is None:
            raise ValueError("竞争判断只能引用当前目标企业的 Claim")
        return claim

    @staticmethod
    def _optional(value: str | None, limit: int, label: str) -> str | None:
        text = (value or "").strip()
        if len(text) > limit:
            raise ValueError(f"{label}不得超过 {limit} 个字符")
        return text or None

    @staticmethod
    def _required(value: str, limit: int, label: str) -> str:
        text = value.strip()
        if not text or len(text) > limit:
            raise ValueError(f"{label}必须为 1 到 {limit} 个字符")
        return text

    @staticmethod
    def _text(value: str, limit: int, label: str) -> str:
        text = value.strip()
        if len(text) > limit:
            raise ValueError(f"{label}不得超过 {limit} 个字符")
        return text

    @classmethod
    def _strings(cls, values: tuple[str, ...], limit: int, label: str) -> list[str]:
        result: list[str] = []
        for value in values:
            text = cls._required(value, limit, label)
            if text not in result:
                result.append(text)
        return result

    @staticmethod
    def _hash(payload: dict) -> bytes:
        return sha256(json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")).digest()
