"""从当前不可变报告、Claim 与 GateDecision 确定性派生业务视图。"""
from __future__ import annotations

from dataclasses import dataclass
import re
from collections.abc import Iterable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Claim, ClaimEvidenceLink, GateDecision, GateDecisionFactor, Report, ReportVersion
from app.report_workspace.view_schema import BusinessViewResult, BusinessViewSection, BusinessViewType


@dataclass(frozen=True)
class _ViewClaim:
    id: str
    claim_text: str
    status: str
    confidence: float
    opportunity_effect: str
    source_ids: tuple[str, ...]


class ReportBusinessViewService:
    """只读投影服务；不搜索、不调用模型、不持久化第二套结论。"""

    _BRIEF_KEYWORDS = ("摘要", "概览", "结论", "商机", "机会", "风险", "反证", "行动", "建议")
    _VALIDATION_ACTIONS = {
        "gap": "采购缺口：访谈客服中心/远程银行和信息科技部门，核实现役能力、服务指标与可量化痛点。",
        "trigger": "采购触发：追踪重点项目全生命周期，并确认当前政策、合同、技术或业务驱动。",
        "window": "采购窗口：核验合同与维保到期、预算编制期及供应商征集时间。",
        "fit": "产品与竞争适配：加载我方能力档案，核验在任厂商锁定、差异化优势与硬性阻断项。",
    }

    def __init__(self, session: Session) -> None:
        self._session = session

    def generate(
        self,
        *,
        report_id: UUID,
        workspace_id: UUID,
        view_type: BusinessViewType,
    ) -> BusinessViewResult:
        if view_type not in {"EXECUTIVE_30S", "ACCOUNT_BRIEF", "OPPORTUNITY_CARD", "DEEP_REPORT"}:
            raise ValueError("不支持的报告业务视图")
        report, version = self._current_report(report_id=report_id, workspace_id=workspace_id)
        decision = self._latest_gate(report=report)
        claims = self._active_claims(report=report, version=version)

        if view_type == "EXECUTIVE_30S":
            title, sections = "30 秒客户摘要", self._executive_sections(version, decision, claims)
        elif view_type == "ACCOUNT_BRIEF":
            title, sections = "一页式 Account Brief", self._account_brief_sections(version, decision)
        elif view_type == "OPPORTUNITY_CARD":
            title, sections = "商机裁决卡", self._opportunity_sections(version, decision, claims)
        else:
            title = "售前深度报告"
            sections = (BusinessViewSection(
                key="deep_report",
                title=title,
                content_md=version.content_md,
                source_ids=(str(version.id),),
            ),)

        manifest = self._source_manifest(version=version, decision=decision, sections=sections)
        return BusinessViewResult(
            view_type=view_type,
            report_id=report.id,
            version_id=version.id,
            version_no=version.version_no,
            title=title,
            content_md="\n\n".join(section.content_md.strip() for section in sections if section.content_md.strip()),
            sections=sections,
            citation_count=len({
                source_id
                for claim in self._version_claims(version)
                for source_id in claim.source_ids
            }),
            source_manifest=manifest,
        )

    def _executive_sections(
        self,
        version: ReportVersion,
        decision: GateDecision | None,
        claims: list[_ViewClaim],
    ) -> tuple[BusinessViewSection, ...]:
        decision_bluf = self._decision_bluf_section(version, decision)
        if decision_bluf is not None:
            return (decision_bluf,)
        sections = [self._gate_section(version=version, decision=decision)]
        commercial = self._commercial_objective_section(version)
        if commercial is not None:
            sections.append(commercial)
        selected = claims[:5]
        if selected:
            content = "## 关键洞察\n\n" + "\n".join(
                f"- {claim.claim_text}（{claim.status}，置信度 {claim.confidence:.0%}）" for claim in selected
            )
            sections.append(BusinessViewSection(
                key="key_insights",
                title="关键洞察",
                content_md=content,
                source_ids=self._claim_source_ids(selected),
            ))
        else:
            sections.append(BusinessViewSection(
                key="key_insights",
                title="关键洞察",
                content_md="## 关键洞察\n\n暂无已支持或客户确认的结构化 Claim，请阅读深度报告并继续验证。",
                source_ids=(str(version.id),),
            ))
        return tuple(sections)

    @staticmethod
    def _decision_bluf_section(
        version: ReportVersion,
        decision: GateDecision | None,
    ) -> BusinessViewSection | None:
        if decision is None:
            return None
        match = re.search(
            r"(?ms)^#\s+执行摘要（BLUF）\s*\n(.*?)(?=^#\s+|\Z)",
            version.content_md,
        )
        if match is None:
            return None
        content = match.group(1).strip()
        if not content:
            return None
        return BusinessViewSection(
            key="decision_bluf",
            title="执行摘要（BLUF）",
            content_md=f"# 执行摘要（BLUF）\n\n{content}",
            source_ids=(str(version.id), str(decision.id)),
        )

    def _account_brief_sections(
        self,
        version: ReportVersion,
        decision: GateDecision | None,
    ) -> tuple[BusinessViewSection, ...]:
        output = [self._gate_section(version=version, decision=decision)]
        parsed = self._markdown_sections(version.content_md)
        selected = [item for item in parsed if any(keyword in item[0] for keyword in self._BRIEF_KEYWORDS)][:6]
        if not selected:
            selected = parsed[:4]
        for index, (heading, content) in enumerate(selected):
            clipped = content if len(content) <= 2_000 else f"{content[:2_000]}…"
            output.append(BusinessViewSection(
                key=f"brief_{index + 1}",
                title=heading.lstrip("# ") or "报告摘录",
                content_md=f"{heading}\n\n{clipped}".strip(),
                source_ids=(str(version.id),),
            ))
        return tuple(output)

    def _opportunity_sections(
        self,
        version: ReportVersion,
        decision: GateDecision | None,
        claims: list[_ViewClaim],
    ) -> tuple[BusinessViewSection, ...]:
        gate = self._gate_section(version=version, decision=decision)
        positive = [claim for claim in claims if claim.opportunity_effect in {"positive", "trigger", "window"}][:5]
        counter = [claim for claim in claims if claim.opportunity_effect in {"negative", "risk", "baseline"}][:5]
        claim_sources = self._claim_source_ids((*positive, *counter)) or (str(version.id),)
        evidence = BusinessViewSection(
            key="opportunity_evidence",
            title="支持与反向证据",
            content_md=(
                "## 支持与反向证据\n\n"
                + self._claim_list("支持/窗口", positive)
                + "\n\n"
                + self._claim_list("反证/基线/风险", counter)
            ),
            source_ids=claim_sources,
        )
        missing = list((decision.summary if decision is not None else {}).get("missing_layers", []))
        next_step = BusinessViewSection(
            key="next_validation",
            title="下一验证事项",
            content_md=(
                "## 下一验证事项\n\n"
                + (
                    "\n".join(
                        f"- {self._VALIDATION_ACTIONS.get(str(item).lower(), str(item))}"
                        for item in missing
                    )
                    if missing
                    else "- 核验关键时机、客户确认状态与硬性阻断项。"
                )
            ),
            source_ids=(str(decision.id),) if decision is not None else (str(version.id),),
        )
        commercial = self._commercial_objective_section(version)
        return tuple(item for item in (gate, commercial, evidence, next_step) if item is not None)

    @staticmethod
    def _commercial_objective_section(version: ReportVersion) -> BusinessViewSection | None:
        match = re.search(
            r"(?ms)^##\s+商业判断五要素\s*\n(.*?)(?=^#{1,2}\s+|\Z)",
            version.content_md,
        )
        if match is None:
            return None
        content = match.group(1).strip()
        if not content:
            return None
        return BusinessViewSection(
            key="commercial_objective",
            title="商业判断五要素",
            content_md=f"## 商业判断五要素\n\n{content}",
            source_ids=(str(version.id),),
        )

    def _gate_section(self, *, version: ReportVersion, decision: GateDecision | None) -> BusinessViewSection:
        if decision is None:
            return BusinessViewSection(
                key="gate_decision",
                title="商机裁决",
                content_md="## 商机裁决\n\n**裁决未完成**。当前视图不得把报告中的相关信息表述为正式商机。",
                source_ids=(str(version.id),),
            )
        reasons = list(decision.summary.get("reasons", []))
        content = (
            "## 商机裁决\n\n"
            f"- 分析截止：{decision.analysis_as_of_date.isoformat()}\n"
            f"- Gate：{decision.gate_level}\n"
            f"- 结论：{decision.decision}\n"
            f"- 是否允许生成商机假设：{'是' if decision.summary.get('can_create_opportunity_hypothesis') else '否'}"
        )
        if reasons:
            content += "\n- 主要依据：" + "；".join(str(item) for item in reasons)
        return BusinessViewSection(
            key="gate_decision",
            title="商机裁决",
            content_md=content,
            source_ids=(str(decision.id),),
        )

    def _current_report(self, *, report_id: UUID, workspace_id: UUID) -> tuple[Report, ReportVersion]:
        report = self._session.execute(
            select(Report).where(Report.id == report_id, Report.workspace_id == workspace_id)
        ).scalar_one_or_none()
        if report is None:
            raise LookupError("报告不存在或不属于当前 Workspace")
        if report.current_version_id is None:
            raise LookupError("报告尚无正式版本")
        version = self._session.get(ReportVersion, report.current_version_id)
        if version is None or version.report_id != report.id:
            raise ValueError("报告当前版本指针无效")
        return report, version

    def _latest_gate(self, *, report: Report) -> GateDecision | None:
        return self._session.execute(
            select(GateDecision)
            .where(GateDecision.workspace_id == report.workspace_id, GateDecision.task_id == report.task_id)
            .order_by(GateDecision.created_at.desc(), GateDecision.id.desc())
            .limit(1)
        ).scalar_one_or_none()

    def _active_claims(self, *, report: Report, version: ReportVersion) -> list[_ViewClaim]:
        persisted = list(self._session.execute(
            select(Claim)
            .where(
                Claim.workspace_id == report.workspace_id,
                Claim.task_id == report.task_id,
                Claim.status.in_(("SUPPORTED", "CUSTOMER_CONFIRMED")),
                (Claim.report_version_id == version.id) | (Claim.report_version_id.is_(None)),
            )
            .order_by(Claim.confidence.desc(), Claim.updated_at.desc(), Claim.id.desc())
        ).scalars())
        if persisted:
            return [
                _ViewClaim(
                    id=str(claim.id),
                    claim_text=claim.claim_text,
                    status=claim.status,
                    confidence=float(claim.confidence),
                    opportunity_effect=claim.opportunity_effect,
                    source_ids=(str(claim.id),),
                )
                for claim in persisted
            ]
        return self._version_claims(version)

    def _source_manifest(
        self,
        *,
        version: ReportVersion,
        decision: GateDecision | None,
        sections: Iterable[BusinessViewSection],
    ) -> tuple[dict, ...]:
        source_ids = tuple(dict.fromkeys(source_id for section in sections for source_id in section.source_ids))
        factors = [] if decision is None else list(self._session.execute(
            select(GateDecisionFactor).where(GateDecisionFactor.gate_decision_id == decision.id)
        ).scalars())
        evidence_ids = tuple(str(factor.evidence_id) for factor in factors if factor.evidence_id is not None)
        explicit_evidence_ids = tuple(
            value.removeprefix("evidence:")
            for value in source_ids
            if value.startswith("evidence:")
            and self._is_uuid(value.removeprefix("evidence:"))
        )
        candidate_claim_ids = [
            UUID(value)
            for value in source_ids
            if self._is_uuid(value)
            and value not in {
                str(version.id),
                str(decision.id) if decision else "",
            }
        ]
        claim_ids = (
            tuple(self._session.execute(
                select(Claim.id).where(Claim.id.in_(candidate_claim_ids))
            ).scalars())
            if candidate_claim_ids
            else ()
        )
        linked_evidence = ()
        if claim_ids:
            linked_evidence = tuple(str(value) for value in self._session.execute(
                select(ClaimEvidenceLink.evidence_id).where(ClaimEvidenceLink.claim_id.in_(claim_ids))
            ).scalars())
        items = [{"source_type": "REPORT_VERSION", "source_id": str(version.id)}]
        if decision is not None:
            items.append({"source_type": "GATE_DECISION", "source_id": str(decision.id)})
        items.extend(
            {"source_type": "CLAIM", "source_id": str(value)}
            for value in claim_ids
        )
        items.extend(
            {"source_type": "EVIDENCE", "source_id": value}
            for value in dict.fromkeys(
                (*evidence_ids, *linked_evidence, *explicit_evidence_ids)
            )
        )
        return tuple(items)

    @staticmethod
    def _claim_list(label: str, claims: list[_ViewClaim]) -> str:
        if not claims:
            return f"### {label}\n\n- 暂无已验证条目。"
        return f"### {label}\n\n" + "\n".join(f"- {claim.claim_text}" for claim in claims)

    @staticmethod
    def _claim_source_ids(claims: Iterable[_ViewClaim]) -> tuple[str, ...]:
        return tuple(dict.fromkeys(
            source_id
            for claim in claims
            for source_id in claim.source_ids
        ))

    @staticmethod
    def _version_claims(version: ReportVersion) -> list[_ViewClaim]:
        raw_claims = (version.evidence_index or {}).get("claims")
        if not isinstance(raw_claims, list):
            return []
        output: list[_ViewClaim] = []
        for index, raw in enumerate(raw_claims, start=1):
            if not isinstance(raw, dict):
                continue
            text = str(raw.get("claim") or raw.get("claim_text") or "").strip()
            evidence_ids = raw.get("evidence_ids")
            if not text or not isinstance(evidence_ids, list):
                continue
            bound_ids = tuple(
                value.strip()
                for value in evidence_ids
                if isinstance(value, str)
                and ReportBusinessViewService._is_uuid(value)
            )
            if not bound_ids:
                continue
            kind = str(raw.get("fact_or_inference") or "INFERENCE").upper()
            status = {
                "FACT": "报告事实",
                "ASSUMPTION": "待核验线索",
                "INFERENCE": "报告推断",
            }.get(kind, "报告推断")
            raw_confidence = raw.get("confidence")
            confidence = (
                float(raw_confidence)
                if isinstance(raw_confidence, (int, float))
                and not isinstance(raw_confidence, bool)
                and 0 <= float(raw_confidence) <= 1
                else {
                    "FACT": 0.7,
                    "INFERENCE": 0.5,
                    "ASSUMPTION": 0.3,
                }.get(kind, 0.4)
            )
            output.append(_ViewClaim(
                id=str(raw.get("claim_id") or f"report-claim-{index}"),
                claim_text=text,
                status=status,
                confidence=confidence,
                opportunity_effect=str(raw.get("opportunity_effect") or "neutral"),
                source_ids=tuple(f"evidence:{value}" for value in bound_ids),
            ))
        return output

    @staticmethod
    def _markdown_sections(content: str) -> list[tuple[str, str]]:
        lines = content.splitlines()
        output: list[tuple[str, str]] = []
        heading = "## 报告摘要"
        body: list[str] = []
        for line in lines:
            if re.match(r"^#{1,6}\s+", line):
                if body:
                    output.append((heading, "\n".join(body).strip()))
                heading, body = line.strip(), []
            else:
                body.append(line)
        if body or not output:
            output.append((heading, "\n".join(body).strip()))
        return [(title, text) for title, text in output if title or text]

    @staticmethod
    def _is_uuid(value: str) -> bool:
        try:
            UUID(value)
        except ValueError:
            return False
        return True
