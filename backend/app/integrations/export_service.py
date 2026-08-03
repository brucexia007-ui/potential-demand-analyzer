"""客户级业务导出；敏感原文和执行数据不进入合同。"""
from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from io import StringIO
from typing import Literal
from uuid import UUID

from sqlalchemy.orm import Session

from app.db.models import (
    Claim,
    NextBestAction,
    Opportunity,
    OpportunityHypothesis,
    OpportunityQualificationCard,
    TargetAccount,
    Task,
)
from app.integrations.schema import (
    BUSINESS_EXPORT_SCHEMA_VERSION,
    CSV_COLUMNS,
    AccountExport,
    ActionExport,
    BusinessExportArtifact,
    BusinessExportBundle,
    ClaimExport,
    HypothesisExport,
    OpportunityExport,
    QualificationExport,
)


class BusinessExportService:
    def __init__(self, session: Session):
        self.session = session

    def build_bundle(
        self,
        *,
        workspace_id: UUID,
        target_account_id: UUID,
        generated_at: datetime | None = None,
    ) -> BusinessExportBundle:
        account = (
            self.session.query(TargetAccount)
            .filter(
                TargetAccount.id == target_account_id,
                TargetAccount.workspace_id == workspace_id,
            )
            .one_or_none()
        )
        if account is None:
            raise ValueError("目标客户不存在或不属于当前 Workspace")

        claims = (
            self.session.query(Claim)
            .join(Task, Task.id == Claim.task_id)
            .filter(
                Claim.workspace_id == workspace_id,
                Task.target_account_id == target_account_id,
            )
            .order_by(Claim.created_at.asc(), Claim.id.asc())
            .all()
        )
        hypotheses = (
            self.session.query(OpportunityHypothesis)
            .filter(
                OpportunityHypothesis.workspace_id == workspace_id,
                OpportunityHypothesis.target_account_id == target_account_id,
            )
            .order_by(OpportunityHypothesis.created_at.asc(), OpportunityHypothesis.id.asc())
            .all()
        )
        hypothesis_ids = [item.id for item in hypotheses]
        qualifications = []
        actions = []
        if hypothesis_ids:
            qualifications = (
                self.session.query(OpportunityQualificationCard)
                .filter(
                    OpportunityQualificationCard.workspace_id == workspace_id,
                    OpportunityQualificationCard.hypothesis_id.in_(hypothesis_ids),
                )
                .order_by(
                    OpportunityQualificationCard.hypothesis_id.asc(),
                    OpportunityQualificationCard.assessment_no.asc(),
                )
                .all()
            )
            actions = (
                self.session.query(NextBestAction)
                .filter(
                    NextBestAction.workspace_id == workspace_id,
                    NextBestAction.hypothesis_id.in_(hypothesis_ids),
                )
                .order_by(NextBestAction.created_at.asc(), NextBestAction.id.asc())
                .all()
            )
        opportunities = (
            self.session.query(Opportunity)
            .filter(
                Opportunity.workspace_id == workspace_id,
                Opportunity.target_account_id == target_account_id,
            )
            .order_by(Opportunity.created_at.asc(), Opportunity.id.asc())
            .all()
        )

        return BusinessExportBundle(
            generated_at=generated_at or datetime.now(timezone.utc),
            workspace_id=workspace_id,
            account=AccountExport.model_validate(account, from_attributes=True),
            claims=[ClaimExport.model_validate(item, from_attributes=True) for item in claims],
            hypotheses=[HypothesisExport.model_validate(item, from_attributes=True) for item in hypotheses],
            qualifications=[
                QualificationExport.model_validate(item, from_attributes=True) for item in qualifications
            ],
            actions=[ActionExport.model_validate(item, from_attributes=True) for item in actions],
            opportunities=[OpportunityExport.model_validate(item, from_attributes=True) for item in opportunities],
        )

    def export(
        self,
        *,
        workspace_id: UUID,
        target_account_id: UUID,
        format: Literal["json", "csv"],
        generated_at: datetime | None = None,
    ) -> BusinessExportArtifact:
        bundle = self.build_bundle(
            workspace_id=workspace_id,
            target_account_id=target_account_id,
            generated_at=generated_at,
        )
        stem = f"business-export-{target_account_id}"
        if format == "json":
            content = json.dumps(
                bundle.model_dump(mode="json"),
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
            return BusinessExportArtifact(
                format="json",
                media_type="application/json; charset=utf-8",
                filename=f"{stem}.json",
                content=content,
            )
        if format == "csv":
            return BusinessExportArtifact(
                format="csv",
                media_type="text/csv; charset=utf-8",
                filename=f"{stem}.csv",
                content=self._csv_content(bundle),
            )
        raise ValueError("不支持的导出格式")

    @staticmethod
    def _csv_content(bundle: BusinessExportBundle) -> bytes:
        output = StringIO(newline="")
        writer = csv.DictWriter(output, fieldnames=CSV_COLUMNS, extrasaction="raise")
        writer.writeheader()
        for row in BusinessExportService._csv_rows(bundle):
            writer.writerow({column: row.get(column, "") for column in CSV_COLUMNS})
        return output.getvalue().encode("utf-8-sig")

    @staticmethod
    def _csv_rows(bundle: BusinessExportBundle):
        common = {
            "schema_version": BUSINESS_EXPORT_SCHEMA_VERSION,
            "generated_at": bundle.generated_at.isoformat(),
            "workspace_id": str(bundle.workspace_id),
            "target_account_id": str(bundle.account.id),
            "target_account_name": bundle.account.official_name or bundle.account.input_name,
        }

        yield {
            **common,
            "entity_type": "ACCOUNT",
            "entity_id": str(bundle.account.id),
            "title": bundle.account.official_name or bundle.account.input_name,
            "status": bundle.account.status,
            "description": " | ".join(
                value
                for value in (bundle.account.industry, bundle.account.region, bundle.account.website)
                if value
            ),
            "created_at": bundle.account.created_at.isoformat(),
            "updated_at": bundle.account.updated_at.isoformat(),
        }
        for item in bundle.claims:
            yield {
                **common,
                "entity_type": "CLAIM",
                "entity_id": str(item.id),
                "status": item.status,
                "description": item.claim_text,
                "claim_type": item.claim_type,
                "opportunity_effect": item.opportunity_effect,
                "confidence": item.confidence,
                "created_at": item.created_at.isoformat(),
                "updated_at": item.updated_at.isoformat(),
            }
        for item in bundle.hypotheses:
            yield {
                **common,
                "entity_type": "HYPOTHESIS",
                "entity_id": str(item.id),
                "title": item.title,
                "status": item.status,
                "confidence": item.confidence,
                "information_completeness": item.information_completeness,
                "customer_problem_hypothesis": item.customer_problem_hypothesis,
                "business_impact_hypothesis": item.business_impact_hypothesis,
                "trigger_event": item.trigger_event,
                "counter_evidence_summary": item.counter_evidence_summary,
                "hard_blockers_json": BusinessExportService._json_cell(item.hard_blockers),
                "created_at": item.created_at.isoformat(),
                "updated_at": item.updated_at.isoformat(),
            }
        for item in bundle.qualifications:
            yield {
                **common,
                "entity_type": "QUALIFICATION",
                "entity_id": str(item.id),
                "parent_entity_id": str(item.hypothesis_id),
                "status": item.gate_result,
                "description": item.summary,
                "information_completeness": item.information_completeness,
                "hard_blockers_json": BusinessExportService._json_cell(item.hard_blockers),
                "missing_fields_json": BusinessExportService._json_cell(item.missing_fields),
                "gate_result": item.gate_result,
                "score": item.score,
                "framework_key": item.framework_key,
                "framework_version": item.framework_version,
                "assessment_no": item.assessment_no,
                "created_at": item.created_at.isoformat(),
            }
        for item in bundle.actions:
            yield {
                **common,
                "entity_type": "ACTION",
                "entity_id": str(item.id),
                "parent_entity_id": str(item.hypothesis_id),
                "title": item.objective,
                "status": item.status,
                "target_role": item.target_role or "",
                "recommended_channel": item.recommended_channel or "",
                "talking_point": item.talking_point,
                "suggested_questions_json": BusinessExportService._json_cell(item.suggested_questions),
                "prerequisites_json": BusinessExportService._json_cell(item.prerequisites),
                "expected_outcome": item.expected_outcome,
                "due_at": item.due_at.isoformat() if item.due_at else "",
                "created_at": item.created_at.isoformat(),
                "updated_at": item.updated_at.isoformat(),
            }
        for item in bundle.opportunities:
            yield {
                **common,
                "entity_type": "OPPORTUNITY",
                "entity_id": str(item.id),
                "parent_entity_id": str(item.source_hypothesis_id),
                "title": item.title,
                "status": item.stage,
                "amount": str(item.amount) if item.amount is not None else "",
                "currency": item.currency or "",
                "amount_source": item.amount_source,
                "probability": item.probability,
                "expected_close_date": item.expected_close_date.isoformat() if item.expected_close_date else "",
                "closed_at": item.closed_at.isoformat() if item.closed_at else "",
                "close_reason": item.close_reason or "",
                "created_at": item.created_at.isoformat(),
                "updated_at": item.updated_at.isoformat(),
            }

    @staticmethod
    def _json_cell(value: object) -> str:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
