"""客服中心报告的证据准入、去重与可审计报告编排。"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import re
from typing import Any, Iterable
from urllib.parse import urlsplit
from uuid import UUID

from sqlalchemy.orm import Session

from app.db.models import Evidence, ResearchCandidate, TargetAccount, Task
from app.evidence.source_reliability import score_source_reliability
from app.execution.report_stage import ReportCitation, ReportDraft


_CONTACT_CENTER_TERMS = (
    "客服", "客户服务", "呼叫中心", "联络中心", "客户中心", "热线", "座席", "坐席",
    "话务", "外呼", "智能质检", "语音质检", "在线客服", "智能机器人", "数字人",
    "多语言", "ivr", "cti", "ipcc", "pbx", "ip电话", "bpo", "工单", "消保",
)
_DIRECT_RELATIONS = {
    "target_exact", "target_subsidiary", "target_parent", "target_operation",
    "target_procurement", "TARGET", "PARENT", "SUBSIDIARY",
}
_LOW_VALUE_HOSTS = {
    "doc88.com", "www.doc88.com", "docin.com", "www.docin.com",
    "wenku.baidu.com", "max.book118.com",
}
_BLOCKED_CONTENT_FARM_HOSTS = {
    "shuashuati.com",
    "ixueyi.net",
    "shangxueba.com",
    "doc88.com",
    "docin.com",
    "taodocs.com",
    "renrendoc.com",
    "book118.com",
    "m448.com",
    "qiquha.com",
    "guanlizhe.com",
    "wanyiwang.com",
    "openvsm.com",
    "ai8.com.cn",
}
_PROCUREMENT_TERMS = ("采购", "招标", "中标", "成交", "合同", "征集", "单一来源", "供应商", "项目")
_POLICY_TERMS = ("政策", "监管", "合规", "信创", "国产化", "消保", "数据安全")
_COMPLAINT_TERMS = ("投诉", "差评", "无法转人工", "等待时间", "服务态度")
_RECRUITMENT_TERMS = ("招聘", "职位", "岗位", "简历", "薪资", "客服专员")


@dataclass(frozen=True)
class CandidatePreselection:
    selected_candidate_ids: tuple[str, ...]
    scorecards: dict[str, dict[str, Any]]


def rank_candidates_for_extraction(
    candidates: Iterable[ResearchCandidate],
    *,
    target_names: tuple[str, ...],
    official_domains: tuple[str, ...] = (),
    demand_direction: str,
    max_items: int = 30,
) -> CandidatePreselection:
    """用确定性高召回排序把目标企业强信号放到提取批次前部。"""
    normalized_targets = tuple(
        dict.fromkeys(name.strip() for name in target_names if name.strip())
    )
    scored: list[tuple[int, int, str, ResearchCandidate]] = []
    scorecards: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        text = f"{candidate.title}\n{candidate.snippet or ''}".casefold()
        target_match = ReportEvidenceSelector._mentions_target(text, normalized_targets)
        relation = "target_exact" if target_match else "external"
        score = 100 if target_match else 0
        if demand_direction.strip() and demand_direction.casefold() in text:
            score += 40
        if any(term in text for term in _CONTACT_CENTER_TERMS):
            score += 30
        if any(term in text for term in _PROCUREMENT_TERMS):
            score += 20
        if any(term in text for term in _POLICY_TERMS):
            score += 10
        if candidate.published_at is not None:
            score += 10
        host = (urlsplit(candidate.canonical_url).hostname or "").lower()
        if _host_in(host, _BLOCKED_CONTENT_FARM_HOSTS):
            scorecards[candidate.candidate_id] = {
                "subject_relation": relation,
                "evidence_role": "out_of_scope",
                "deterministic_score": -1000,
                "rejection_reason": "blocked_content_farm",
            }
            continue
        source_tier = score_source_reliability(
            candidate.canonical_url,
            official_domains=official_domains,
        ).value
        if source_tier == "S":
            score += 80
        elif source_tier == "A":
            score += 60
        elif source_tier == "B":
            score += 20
        elif source_tier == "C":
            score -= 40
        signal_lane = (
            "complaint"
            if any(term in text for term in _COMPLAINT_TERMS)
            else (
                "recruitment"
                if any(term in text for term in _RECRUITMENT_TERMS)
                else "core"
            )
        )
        if signal_lane != "core":
            score -= 50
        if host in _LOW_VALUE_HOSTS:
            score -= 20
        original_rank = candidate.original_rank if candidate.original_rank is not None else 10_000
        scorecards[candidate.candidate_id] = {
            "subject_relation": relation,
            "evidence_role": (
                "target_procurement_evidence"
                if target_match and any(term in text for term in _PROCUREMENT_TERMS)
                else ("target_operation_evidence" if target_match else "contextual_evidence")
            ),
            "deterministic_score": score,
            "source_tier": source_tier,
            "signal_lane": signal_lane,
        }
        scored.append((-score, original_rank, candidate.candidate_id, candidate))
    scored.sort(key=lambda item: item[:3])
    selected_items: list[ResearchCandidate] = []
    weak_signal_count = 0
    for item in scored:
        candidate = item[3]
        lane = scorecards[candidate.candidate_id]["signal_lane"]
        if lane != "core":
            if weak_signal_count >= 1:
                continue
            weak_signal_count += 1
        selected_items.append(candidate)
        if len(selected_items) >= max_items:
            break
    selected = tuple(item.candidate_id for item in selected_items)
    return CandidatePreselection(
        selected_candidate_ids=selected,
        scorecards=scorecards,
    )


@dataclass(frozen=True)
class ReportEvidenceSelection:
    selected_evidence_ids: tuple[str, ...]
    rejected_evidence_ids: set[str]
    candidate_count: int
    duplicate_count: int
    direct_fact_count: int
    inference_count: int
    rejection_reasons: dict[str, str] = field(default_factory=dict)

    def diagnostics(self) -> dict[str, Any]:
        return {
            "candidate_count": self.candidate_count,
            "selected_count": len(self.selected_evidence_ids),
            "rejected_count": len(self.rejected_evidence_ids),
            "duplicate_count": self.duplicate_count,
            "direct_fact_count": self.direct_fact_count,
            "inference_count": self.inference_count,
            "rejection_reasons": dict(self.rejection_reasons),
        }


class ReportEvidenceSelector:
    """只让与目标主体和客服中心主题直接相关的核心证据进入正式报告。"""

    def __init__(self, session: Session, *, max_items: int = 30) -> None:
        self._session = session
        self._max_items = max_items

    def select(self, *, task_id: UUID) -> ReportEvidenceSelection:
        task = self._session.get(Task, task_id)
        if task is None:
            raise LookupError("报告证据准入对应的任务不存在")
        target = self._session.get(TargetAccount, task.target_account_id)
        if target is None:
            raise LookupError("报告证据准入对应的目标企业不存在")
        evidences = (
            self._session.query(Evidence)
            .filter(Evidence.task_id == task_id, Evidence.data_domain == "external")
            .order_by(Evidence.captured_at, Evidence.id)
            .all()
        )
        candidate_ids = {
            str((item.meta_data or {}).get("candidate_id") or "").strip()
            for item in evidences
        }
        candidate_ids.discard("")
        candidates = (
            self._session.query(ResearchCandidate)
            .filter(
                ResearchCandidate.task_id == task_id,
                ResearchCandidate.candidate_id.in_(candidate_ids),
            )
            .all()
            if candidate_ids else []
        )
        by_candidate_id = {item.candidate_id: item for item in candidates}
        aliases = tuple(dict.fromkeys(
            value.strip()
            for value in (target.input_name, target.official_name, task.company_name)
            if isinstance(value, str) and value.strip()
        ))

        eligible: list[tuple[Evidence, int]] = []
        rejected: set[str] = set()
        rejection_reasons: dict[str, str] = {}
        for evidence in evidences:
            candidate_id = str((evidence.meta_data or {}).get("candidate_id") or "").strip()
            candidate = by_candidate_id.get(candidate_id)
            failure = self._eligibility_failure(evidence, candidate=candidate, aliases=aliases)
            if failure is None:
                eligible.append((evidence, self._priority(evidence, candidate=candidate)))
            else:
                rejected.add(str(evidence.id))
                rejection_reasons[str(evidence.id)] = failure

        deduped: dict[str, tuple[Evidence, int]] = {}
        duplicate_count = 0
        for evidence, priority in eligible:
            key = self._dedupe_key(evidence)
            current = deduped.get(key)
            if current is None:
                deduped[key] = (evidence, priority)
                continue
            duplicate_count += 1
            if priority > current[1]:
                rejected.add(str(current[0].id))
                rejection_reasons[str(current[0].id)] = "与更高优先级条目内容重复"
                deduped[key] = (evidence, priority)
            else:
                rejected.add(str(evidence.id))
                rejection_reasons[str(evidence.id)] = "与更高优先级条目内容重复"

        ranked = sorted(
            deduped.values(),
            key=lambda pair: (
                -pair[1],
                -self._timestamp(pair[0].published_at),
                self._timestamp(pair[0].captured_at),
                str(pair[0].id),
            ),
        )
        selected = [item for item, _priority in ranked[:self._max_items]]
        for item, _priority in ranked[self._max_items:]:
            rejected.add(str(item.id))
            rejection_reasons[str(item.id)] = "超出报告准入上限"
        return ReportEvidenceSelection(
            selected_evidence_ids=tuple(str(item.id) for item in selected),
            rejected_evidence_ids=rejected,
            candidate_count=len(evidences),
            duplicate_count=duplicate_count,
            direct_fact_count=sum(item.fact_or_inference == "FACT" for item in selected),
            inference_count=sum(item.fact_or_inference == "INFERENCE" for item in selected),
            rejection_reasons=rejection_reasons,
        )

    @classmethod
    def _eligible(
        cls,
        evidence: Evidence,
        *,
        candidate: ResearchCandidate | None,
        aliases: tuple[str, ...],
    ) -> bool:
        return cls._eligibility_failure(evidence, candidate=candidate, aliases=aliases) is None

    @classmethod
    def _eligibility_failure(
        cls,
        evidence: Evidence,
        *,
        candidate: ResearchCandidate | None,
        aliases: tuple[str, ...],
    ) -> str | None:
        """返回准入失败原因；通过则返回 None。原因文案面向报告附录展示。"""
        metadata = dict(evidence.meta_data or {})
        host = (urlsplit(evidence.url).hostname or "").lower()
        if _host_in(host, _BLOCKED_CONTENT_FARM_HOSTS):
            return "内容农场或低价值转载源"
        if (
            evidence.fact_or_inference == "INFERENCE"
            or metadata.get("evaluation_skill")
            or evidence.source_type == "skill_evaluation"
            or evidence.url.startswith("urn:skill-evaluation:")
        ):
            return "推断或评估产出，非外部原始证据"
        if not cls._is_contact_center(evidence):
            return "非客服中心主题"
        relation = cls._subject_relation(candidate)
        if relation and relation not in _DIRECT_RELATIONS:
            return "与目标企业主体关系非直达"
        if relation in _DIRECT_RELATIONS or cls._mentions_target(
            f"{evidence.title}\n{evidence.snippet}",
            aliases,
        ):
            return None
        return "未提及目标企业主体"

    @staticmethod
    def _subject_relation(candidate: ResearchCandidate | None) -> str:
        if candidate is None:
            return ""
        metadata = dict(candidate.meta_data or {})
        screening = metadata.get("screening")
        scorecard = screening.get("scorecard") if isinstance(screening, dict) else None
        if isinstance(scorecard, dict):
            return str(scorecard.get("subject_relation") or "").strip()
        return ""

    @staticmethod
    def _mentions_target(text: str, aliases: tuple[str, ...]) -> bool:
        lowered = text.casefold()
        for alias in aliases:
            token = alias.casefold()
            without_industry_false_positive = lowered.replace(f"{token}业", "")
            if token in without_industry_false_positive:
                return True
        return False

    @staticmethod
    def _is_contact_center(evidence: Evidence) -> bool:
        metadata = dict(evidence.meta_data or {})
        text = " ".join((
            evidence.title,
            evidence.snippet,
            str(metadata.get("capability_domain") or ""),
            str(metadata.get("requirement_key") or ""),
            str(metadata.get("evaluation_skill") or ""),
        )).casefold()
        return any(term in text for term in _CONTACT_CENTER_TERMS)

    @classmethod
    def _priority(
        cls,
        evidence: Evidence,
        *,
        candidate: ResearchCandidate | None,
    ) -> int:
        metadata = dict(evidence.meta_data or {})
        score = 0
        if evidence.fact_or_inference == "INFERENCE" or metadata.get("evaluation_skill"):
            score += 25
        relation = cls._subject_relation(candidate)
        if relation in _DIRECT_RELATIONS:
            score += 40
        if evidence.source_reliability in {"S", "A"}:
            score += 20
        elif evidence.source_reliability == "B":
            score += 10
        host = (urlsplit(evidence.url).hostname or "").lower()
        if host in _LOW_VALUE_HOSTS:
            score -= 30
        if evidence.published_at:
            score += 10
        if any(
            metadata.get(field)
            for field in (
                "event_stage", "event_date", "deadline_date", "contract_end_date",
                "supplier", "capability_status", "evaluation_fields",
            )
        ):
            score += 15
        return score

    @staticmethod
    def _dedupe_key(evidence: Evidence) -> str:
        from app.evidence.procurement_event_normalizer import _normalized_project_title

        metadata = evidence.meta_data or {}
        project_key = str(metadata.get("project_key") or "").strip()
        event_stage = str(metadata.get("event_stage") or "").strip()
        if project_key and event_stage:
            return f"project-event:{project_key}:{event_stage}"
        # 标题指纹档：不同 URL/候选的同一内容（仅差公告类后缀）在此合并
        fingerprint = _normalized_project_title(evidence.title or "")
        if fingerprint != "unknown" and len(fingerprint) >= 8:
            return f"title-fp:{fingerprint}"
        candidate_id = str((evidence.meta_data or {}).get("candidate_id") or "").strip()
        if candidate_id:
            return f"candidate:{candidate_id}"
        title = re.sub(r"[\W_]+", "", evidence.title.casefold())
        return f"title:{title}"

    @staticmethod
    def _timestamp(value: datetime | None) -> float:
        if value is None:
            return 0.0
        normalized = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
        return normalized.timestamp()


def _host_in(host: str, domains: set[str]) -> bool:
    return any(host == domain or host.endswith(f".{domain}") for domain in domains)


class ContactCenterReportComposer:
    """将核心证据编排为销售可读、事实与假设分离的客服中心报告。"""

    _MISSING_LAYER_LABELS = {
        "gap": "采购缺口",
        "trigger": "采购触发",
        "window": "采购窗口",
        "fit": "产品与竞争适配",
    }

    def __init__(
        self,
        *,
        target_name: str,
        demand_direction: str,
        gate_artifact: dict[str, Any],
        report_sections: Iterable[str],
        partial_reasons: Iterable[str],
        selection_diagnostics: dict[str, Any],
        analysis_as_of: datetime,
        inference_items: Iterable[dict[str, Any]] = (),
        research_plan: dict[str, Any] | None = None,
    ) -> None:
        self._target_name = target_name
        self._demand_direction = demand_direction
        self._gate = dict(gate_artifact)
        self._sections = tuple(report_sections)
        self._partial_reasons = tuple(partial_reasons)
        self._diagnostics = dict(selection_diagnostics)
        self._analysis_as_of = analysis_as_of
        self._inferences = tuple(dict(item) for item in inference_items)
        self._research_plan = dict(research_plan or {})

    def render(self, items: list[dict[str, Any]]) -> ReportDraft:
        items = self._top_external_signals([
            item for item in items if self._is_external_report_evidence(item)
        ])
        citation_by_id = {
            item["id"]: f"E{index}"
            for index, item in enumerate(items, start=1)
        }
        blocks = [
            self._render_section(index, section, items, citation_by_id)
            for index, section in enumerate(self._sections)
        ]
        citations = tuple(
            ReportCitation(
                citation_key=citation_by_id[item["id"]],
                evidence_id=item["id"],
                section_key=self._sections[-1],
                locator=item.get("url") or "",
            )
            for item in items
        )
        claims = tuple(
            {
                "claim_id": f"claim-{index}",
                "claim": item["title"],
                "evidence_ids": [item["id"]],
                "is_critical": index <= 5,
                "fact_or_inference": (
                    item.get("fact_or_inference")
                    or (item.get("meta_data") or {}).get("fact_or_inference")
                    or "FACT"
                ),
            }
            for index, item in enumerate(items, start=1)
        )
        return ReportDraft(
            content_md="\n\n".join(blocks),
            citations=citations,
            claims=claims,
        )

    @staticmethod
    def _is_external_report_evidence(item: dict[str, Any]) -> bool:
        metadata = item.get("meta_data") or {}
        return not (
            str(item.get("fact_or_inference") or "").upper() == "INFERENCE"
            or item.get("source_type") == "skill_evaluation"
            or metadata.get("evaluation_skill")
            or str(item.get("url") or "").startswith("urn:skill-evaluation:")
        )

    def _render_section(
        self,
        index: int,
        section: str,
        items: list[dict[str, Any]],
        citation_by_id: dict[str, str],
    ) -> str:
        renderers = (
            self._decision_bluf,
            self._as_is,
            self._gap_analysis,
            self._opportunity_sizing,
            self._red_team,
            self._decision_actions,
            self._decision_appendix,
        )
        if len(self._sections) == len(renderers):
            body = renderers[index](items, citation_by_id)
        elif index == 0:
            body = self._battlecard(items, citation_by_id)
        elif index == 1:
            body = self._scope()
        elif index == len(self._sections) - 1:
            body = self._evidence_index(items, citation_by_id)
        else:
            body = self._domain_section(section, items, citation_by_id)
        return f"# {section}\n\n{body}"

    def _decision_bluf(
        self,
        items: list[dict[str, Any]],
        citation_by_id: dict[str, str],
    ) -> str:
        grade = str(self._gate.get("gate_level") or "GX")
        decision = str(self._gate.get("decision") or "INSUFFICIENT_EVIDENCE")
        sales_grade = self._sales_investment_grade(grade, decision)
        missing = {
            str(item).strip().lower()
            for item in self._gate.get("missing_layers") or []
        }
        can_advance = self._gate.get("can_create_opportunity_hypothesis") is True
        conclusion = (
            f"{self._target_name}客服中心存在可验证的候选商机，当前建议按"
            f"“{sales_grade}级”投入并以条件门禁控制售前资源。"
            if can_advance
            else f"{self._target_name}客服中心目前仅形成历史基线或待验证线索，"
            f"建议按“{sales_grade}级”低成本核验，不进入 POC 或投标准备。"
        )
        signals = self._top_external_signals(items)[:3]
        signal_rows = [
            "| # | 支撑判断 | 置信度 | 关键依据 |",
            "|---|---|---|---|",
        ]
        if signals:
            for idx, item in enumerate(signals, start=1):
                signal_rows.append(
                    f"| {idx} | {self._judgement_for_signal(item)} | "
                    f"{self._confidence_label(item)} | "
                    f"{citation_by_id[item['id']]} |"
                )
        else:
            signal_rows.append("| 1 | 尚无达到首屏门槛的外部证据 | 低 | — |")
        top_action = self._top_action(missing)
        critical_assumption = self._critical_assumption(missing)
        maximum_risk = self._maximum_risk(items)
        planning_objective_lines = self._planning_objective_lines()
        return "\n".join((
            f"> **报告类型：** B2B 销售商机决策报告（结论型）  ",
            f"> **研究对象：** {self._target_name}——客服中心/呼叫中心体系  ",
            f"> **交付状态：** {'PARTIAL' if self._partial_reasons else 'COMPLETE'}  ",
            f"> **版本/日期：** V1.0 / {self._analysis_as_of.date().isoformat()}  ",
            "> **密级：** 内部资料，仅限销售与售前团队使用",
            "",
            *planning_objective_lines,
            "",
            "## 一句话结论",
            "",
            f"**{conclusion}**",
            "",
            f"- **OIG 裁决：** {grade} / {decision}",
            f"- **销售投入等级：** {sales_grade}"
            "（A=立即投入 / B=验证后投入 / C=仅保持接触 / D=本周期放弃）",
            f"- **证据完整性：** 全维提取 {self._extracted_total()} 条，"
            f"报告级准入 {len(items)} 条（{float(self._diagnostics.get('admission_ratio') or 0):.1%}）",
            "",
            f"> **本周唯一行动项（Top Action）：** {top_action}",
            "",
            "## 三个以内支撑判断",
            "",
            *signal_rows,
            "",
            f"**关键前提：** {critical_assumption}",
            "",
            f"**最大风险：** {maximum_risk}",
        ))

    def _planning_objective_lines(self) -> tuple[str, ...]:
        goals = self._research_plan.get("goals")
        primary_goal_id = self._research_plan.get("primary_goal_id")
        if not isinstance(goals, list):
            return ()
        normalized = [
            item for item in goals
            if isinstance(item, dict)
            and isinstance(item.get("goal_id"), str)
            and isinstance(item.get("question"), str)
            and item["question"].strip()
        ]
        primary = next(
            (
                item["question"].strip()
                for item in normalized
                if item["goal_id"] == primary_goal_id
            ),
            "",
        )
        if not primary:
            return ()
        supporting = [
            item["question"].strip()
            for item in normalized
            if item["goal_id"] != primary_goal_id
        ][:5]
        return (
            "## 本报告要支持的决策",
            "",
            f"- **主目标：** {primary}",
            *(
                [f"- **关键问题：** {'；'.join(supporting)}"]
                if supporting else []
            ),
        )

    def _as_is(
        self,
        items: list[dict[str, Any]],
        citation_by_id: dict[str, str],
    ) -> str:
        topics = self._best_item_by_topic(items)
        capability_rows = [
            "| 能力域 | 当前可证实状态 | 成熟度 | 依据 | 待验证字段 |",
            "|---|---|---|---|---|",
        ]
        capabilities = (
            ("呼叫中心平台（CTI/PBX/IVR）", "voice", "厂商、版本、生产范围、并发与容灾"),
            ("在线客服与机器人", "intelligence", "厂商、上线版本、独立解决率、转人工率"),
            ("智能质检与坐席辅助", "quality", "覆盖率、准确率、使用范围、模型迭代机制"),
            ("信创国产化", "xinchuang", "适配栈、替换范围、生产状态、完成时间"),
            ("BPO 与坐席运营", "bpo", "供应商、人数、服务期限、SLA 与续约节点"),
        )
        for label, topic, unknowns in capabilities:
            item = topics.get(topic)
            if item:
                capability_rows.append(
                    f"| {label} | 已发现相关建设、采购或运营事实："
                    f"{self._cell(item['title'])} | 仅确认项目/模式，运营成熟度未知 | "
                    f"{citation_by_id[item['id']]} | {unknowns} |"
                )
            else:
                capability_rows.append(
                    f"| {label} | 公开信息不足 | 未知 | — | {unknowns} |"
                )

        vendor_facts = [item for item in items if self._vendor_name(item)]
        vendor_rows = [
            "| 系统/服务 | 在任厂商 | 合同到期 | 锁定强度 | 依据 |",
            "|---|---|---|---|---|",
        ]
        if vendor_facts:
            for item in vendor_facts[:4]:
                vendor_rows.append(
                    f"| {self._topic_label(self._topic_for_item(item))} | "
                    f"{self._vendor_name(item) or '待核验'} | "
                    f"{self._contract_end(item) or '待核验'} | "
                    f"待核验接口、数据与单一来源约束 | {citation_by_id[item['id']]} |"
                )
        else:
            vendor_rows.append(
                "| 核心客服平台与运营服务 | 未获得可确认的在任厂商证据 | "
                "待核验 | 未知 | — |"
            )

        timeline = self._timeline_items(items)
        timeline_lines = [
            f"- **{self._display_date(item)} / "
            f"{self._cell(str((item.get('meta_data') or {}).get('event_stage') or 'EVENT'))}：** "
            f"{item['title']} "
            f"[{citation_by_id[item['id']]}]"
            for item in timeline[:8]
        ] or ["- 当前没有同时满足主体、主题和日期门槛的建设事件。"]

        return "\n".join((
            "## 2.1 客服中心能力地图",
            "",
            *capability_rows,
            "",
            "## 2.2 在任厂商与合同状态",
            "",
            *vendor_rows,
            "",
            "> 厂商名称只有在中标、合同、验收或双方可交叉印证材料中才标记为在任；"
            "行业常见厂商不写入本表。",
            "",
            "## 2.3 干系人与采购模式（Buying Center）",
            "",
            "| 角色 | 关注点 | 信息策略 | 当前状态 |",
            "|---|---|---|---|",
            "| 业务部门（客服中心/运营） | 体验、效率、投诉压降 | 用服务指标和同业案例验证缺口 | 角色框架，具体人待核验 |",
            "| IT/信息科技部门 | 信创、安全、接口和数据迁移 | 用架构适配与旁路方案验证可行性 | 角色框架，具体人待核验 |",
            "| 采购与财务部门 | 预算、准入、合同和价格 | 核验预算周期、采购模式与供应商门槛 | 角色框架，具体人待核验 |",
            "",
            "## 2.4 建设时间轴与换代窗口",
            "",
            *timeline_lines,
            "",
            f"**窗口判断：** {self._window_statement(timeline)}",
        ))

    def _gap_analysis(
        self,
        items: list[dict[str, Any]],
        citation_by_id: dict[str, str],
    ) -> str:
        complaints = [
            item for item in items
            if any(token in self._item_text(item) for token in ("投诉", "评价", "不作为", "等待", "转人工"))
        ]
        pain_rows = [
            "| 痛点方向 | 当前量化结果 | 数据来源 | 可用性 |",
            "|---|---|---|---|",
        ]
        if complaints:
            refs = "、".join(citation_by_id[item["id"]] for item in complaints[:5])
            pain_rows.append(
                f"| 服务响应与问题解决体验 | 当前仅准入 {len(complaints)} 条相关公开样本；"
                "尚未完成同类聚合、去重和趋势统计 | "
                f"{refs} | 只作方向锚点，不证明系统性能力缺口 |"
            )
        else:
            pain_rows.append(
                "| 服务响应与问题解决体验 | 未获得可量化样本 | — | 待补投诉样本、时间范围和官方回应 |"
            )
        supported_inferences = self._supported_inferences(citation_by_id)
        fit_rows = [
            "| 客户痛点/缺口 | 性质 | 依据 | 业务影响 | 可切入方向 | 阻力/未知 |",
            "|---|---|---|---|---|---|",
        ]
        if supported_inferences:
            for idx, item in enumerate(supported_inferences[:5], start=1):
                refs = self._inference_refs(item, citation_by_id)
                fit_rows.append(
                    f"| {self._cell(item.get('title') or '待验证缺口')} | 推断 I{idx} | "
                    f"{refs or '外部依据不足'} | 待客户量化 | "
                    f"{self._recommended_archetype(item)} | "
                    "需核验现役系统、接口、数据权属与采购窗口 |"
                )
        else:
            fit_rows.append(
                "| 尚无达到证据门槛的已验证痛点 | 未知 | — | 无法评估 | "
                "先做业务问诊，不投入方案设计 | 缺少服务指标与客户确认 |"
            )
        trigger_rows = self._trigger_rows(items, citation_by_id)
        return "\n".join((
            "## 3.1 痛点证据（量化后呈现）",
            "",
            *pain_rows,
            "",
            "## 3.2 行业基准对照",
            "",
            "| 指标 | 目标企业现状 | 行业基准 | 当前结论 |",
            "|---|---|---|---|",
            "| 机器人独立解决率 | 待获取 | 待引入同年份、同口径可溯源基准 | 暂不可比较 |",
            "| 智能质检覆盖率 | 待获取 | 待引入同年份、同口径可溯源基准 | 暂不可比较 |",
            "| 平均等待时长/一次解决率 | 待获取 | 待引入同年份、同口径可溯源基准 | 暂不可比较 |",
            "",
            "## 3.3 采购触发评估",
            "",
            *trigger_rows,
            "",
            "## 3.4 痛点—产品能力映射",
            "",
            *fit_rows,
        ))

    def _opportunity_sizing(
        self,
        items: list[dict[str, Any]],
        citation_by_id: dict[str, str],
    ) -> str:
        missing = {
            str(item).strip().lower()
            for item in self._gate.get("missing_layers") or []
        }
        hypothesis_rows = [
            "| 假设 | 状态 | 置信度 | 证实路径 | 证伪路径 |",
            "|---|---|---|---|---|",
            f"| H1：未来 12 个月存在客服系统预算 | "
            f"{'待验证' if 'window' in missing else '部分支持'} | "
            f"{'未评分' if 'window' in missing else '中'} | "
            "客户问诊 + 采购意向/RFI/预算材料 | 确认未来两年无预算 |",
            "| H2：在任合同存在可介入窗口 | 待验证 | 未评分 | "
            "中标—合同—续约链 + 合同期限 | 已完成长期单一来源续约 |",
            "| H3：存在可量化业务缺口 | "
            f"{'待验证' if 'gap' in missing else '部分支持'} | "
            f"{'未评分' if 'gap' in missing else '中'} | "
            "业务指标与客户确认 | 指标已达标且无扩容需求 |",
            "| H4：我方存在可赢的切入形态 | "
            f"{'待验证' if 'fit' in missing else '部分支持'} | "
            f"{'未评分' if 'fit' in missing else '中'} | "
            "能力档案、资格和技术适配 | 存在硬性资格或集成阻断 |",
        ]
        grade = str(self._gate.get("gate_level") or "GX")
        sales_grade = self._sales_investment_grade(
            grade, str(self._gate.get("decision") or "")
        )
        return "\n".join((
            "## 4.1 假设—置信度矩阵",
            "",
            *hypothesis_rows,
            "",
            "## 4.2 商机规模测算",
            "",
            "**当前结论：暂不可估算。** 现有证据未同时提供目标范围、坐席/渠道规模、"
            "采购边界和可比中标金额，输出金额区间会形成伪精确判断。",
            "",
            "| 场景 | 补数公式 | 必须补齐的参数 |",
            "|---|---|---|",
            "| 旁路智能化 | 软件/订阅 + 接口实施 + 模型/算力 + 年度运维 | "
            "坐席数、并发、渠道数、部署方式、模型调用量 |",
            "| 核心平台升级 | 平台许可 + CTI/PBX/IVR 迁移 + 集成 + 容灾 + 运维 | "
            "节点数、线路、接口数、双轨周期、SLA |",
            "| BPO/运营服务 | 人数 × 单席月成本 × 服务月数 + 管理与工具费用 | "
            "坐席人数、班次、地域、服务期限、绩效条款 |",
            "",
            "## 4.3 竞争态势与破局路径",
            "",
            f"- **在任厂商：** {self._incumbent_summary(items)}",
            "- **潜在竞争者：** 未获得目标企业级证据，不把华为、科大讯飞、Genesys、"
            "Avaya 等行业常见厂商写成目标企业在任或确定竞品。",
            "- **建议形态：** 核心平台与厂商锁定未知时，优先验证旁路智能质检、"
            "坐席辅助或增量集成；只有确认 EOL、合规硬触发或合同窗口后才评估全量替换。",
            "",
            "## 4.4 商机评分卡",
            "",
            "| 维度 | 权重 | 当前得分 | 评分依据 |",
            "|---|---:|---|---|",
            "| 市场规模 | 25% | 未评分 | 缺少目标范围和金额口径 |",
            f"| 时间窗口确定性 | 25% | {'未评分' if 'window' in missing else '3/5'} | "
            f"{'合同、预算和项目阶段未知' if 'window' in missing else '已有目标企业级窗口线索'} |",
            "| 客户可达性 | 20% | 未评分 | 未输入客户关系与关键人状态 |",
            f"| 我方胜率/竞争位势 | 20% | {'未评分' if 'fit' in missing else '3/5'} | "
            f"{'产品适配和在任锁定未知' if 'fit' in missing else '产品适配已形成初步判断'} |",
            "| 交付可行性 | 10% | 未评分 | 缺少接口、数据、部署与资格信息 |",
            f"| **综合决策** | — | **{sales_grade}** | 未评分维度不以默认分补齐 |",
        ))

    def _red_team(
        self,
        items: list[dict[str, Any]],
        citation_by_id: dict[str, str],
    ) -> str:
        recent = self._latest_dated_item(items)
        recent_ref = citation_by_id[recent["id"]] if recent else "—"
        return "\n".join((
            "## 5.1 对核心结论最强的三个反驳",
            "",
            "| 编号 | 反驳 | 杀伤力 | 验证与影响 |",
            "|---|---|---|---|",
            f"| R1 | 最近相关项目可能已经包含智能化整体升级，公开材料中的缺口已被解决 | "
            f"高 | 核验项目范围与验收结果（{recent_ref}）；若属实，降级或转旁路机会 |",
            "| R2 | 集团统一建设或 BPO 打包交付，使条线公告不能代表整体架构和采购节奏 | "
            "高 | 问诊采购层级与平台所有权；若属实，改从集团或 BPO 合同切入 |",
            "| R3 | 在任厂商可能已完成长期续约且客户满意，迁移成本高于新增价值 | "
            "高 | 核验续约、SLA、接口和数据迁移权；若无旁路入口则本周期停止 |",
            "",
            "## 5.2 放弃信号（Kill Criteria）",
            "",
            "| 信号 | 动作 |",
            "|---|---|",
            "| 已确认近两年完成同类整体升级，且关键指标达标 | 停止替换型投入，转观察名单 |",
            "| 在任厂商单一来源续约覆盖未来三年以上，且无旁路接口 | 本周期放弃，合同到期前 12 个月重启 |",
            "| 客户确认未来两年无预算或无业务缺口 | 降级为 C 级，仅季度跟踪 |",
            "| 我方存在硬性资格、数据合规或核心集成阻断 | 终止产品匹配，不推进 POC |",
        ))

    def _decision_actions(
        self,
        items: list[dict[str, Any]],
        citation_by_id: dict[str, str],
    ) -> str:
        missing = {
            str(item).strip().lower()
            for item in self._gate.get("missing_layers") or []
        }
        return "\n".join((
            "## 6.1 行动分级",
            "",
            "| 级别 | 行动 | 触发/停止条件 |",
            "|---|---|---|",
            f"| **立即做（0–30 天）** | {self._top_action(missing)} | "
            "只投入桌面研究与客户核验，不启动 POC |",
            "| **条件触发做（31–60 天）** | 以旁路质检、坐席辅助或信创适配申请技术交流 | "
            "确认业务缺口和未来 12 个月窗口后启动 |",
            "| **条件触发做（61–90 天）** | 准备 POC、立项或投标材料 | "
            "预算、决策链、产品适配和采购窗口全部通过后启动 |",
            "| **本阶段不做** | 不承诺定制开发，不按推断估报价，不投入重型售前 | "
            "任一关键前提未核验即保持限制 |",
            "",
            "## 6.2 验证计划",
            "",
            "| 待验证项 | 方法 | 建议责任角色 | 时限 | 关联假设 |",
            "|---|---|---|---|---|",
            "| 在任厂商与合同到期 | 追踪中标/合同/续约链 + 客户问诊 | 客户经理/情报分析 | 30 天 | H2/R3 |",
            "| 集团或条线采购模式 | 比对集团与子公司公告 + 采购问诊 | 客户经理 | 30 天 | H1/R2 |",
            "| 质检覆盖率、转人工率、等待时长、一次解决率 | 业务与技术交流 | 售前/业务顾问 | 60 天 | H3 |",
            "| 信创、接口、数据权属和迁移约束 | 架构访谈 + 产品适配检查 | 架构师 | 60 天 | H4/R3 |",
            "",
            "## 6.3 销售问诊问题（按角色）",
            "",
            "- **问业务负责人（H3）：** 当前最希望改善的三个客服指标是什么？"
            "质检覆盖率、机器人转人工率、平均等待时长和一次解决率分别是多少？",
            "- **问 IT/架构负责人（H2/H4/R3）：** CTI/PBX、IVR、录音、质检、"
            "在线客服和工单系统分别由谁提供？接口、话务语料和知识库能否完整导出？",
            "- **问采购/财务负责人（H1/H2）：** 现有合同与维保何时到期？"
            "下一财年是否已有预算、RFI、POC 或供应商征集计划？",
            "",
            "## 6.4 复核节点",
            "",
            "- **30 天后：** 根据在任厂商、合同和采购模式更新 H1/H2，决定升级 B/A 或保持 C。",
            "- **60 天后：** 根据业务指标和技术适配更新 H3/H4，决定是否投入 POC。",
            "- **触发式复核：** 监测到相关公告、供应商变动、监管通报或服务事故后 24 小时内重验。",
        ))

    def _decision_appendix(
        self,
        items: list[dict[str, Any]],
        citation_by_id: dict[str, str],
    ) -> str:
        evidence_rows = [
            "| 编号 | 外部证据 | 等级 | 日期 | 用途 | 链接 |",
            "|---|---|---|---|---|---|",
        ]
        for item in items:
            ref = citation_by_id[item["id"]]
            evidence_rows.append(
                f"| {ref} | {self._cell(item['title'])} | "
                f"{self._cell(item.get('source_reliability') or 'UNKNOWN')} | "
                f"{self._display_date(item)} | "
                f"{self._topic_label(self._topic_for_item(item))} | "
                f"{self._link(item.get('url'))} |"
            )
        if not items:
            degraded_leads = list(self._diagnostics.get("degraded_leads") or [])[:5]
            if degraded_leads:
                evidence_rows.extend((
                    "",
                    "### 待核验线索（未达准入标准，需人工核验后方可采信）",
                    "",
                    "| 线索 | 等级 | 日期 | 被拒原因 | 链接 |",
                    "|---|---|---|---|---|",
                ))
                for lead in degraded_leads:
                    published = str(lead.get("published_at") or "")
                    evidence_rows.append(
                        f"| {self._cell(str(lead.get('title') or '未命名线索'))} | "
                        f"{self._cell(str(lead.get('source_reliability') or 'UNKNOWN'))} | "
                        f"{published[:10] or '日期未知'} | "
                        f"{self._cell(str(lead.get('rejection_reason') or '未达准入标准'))} | "
                        f"{self._link(lead.get('url'))} |"
                    )
        inference_rows = [
            "| 编号 | 推断 | 外部依据 | 置信度 | 替代解释与验证动作 |",
            "|---|---|---|---|---|",
        ]
        if self._inferences:
            for idx, item in enumerate(self._inferences, start=1):
                refs = self._inference_refs(item, citation_by_id) or "外部依据未闭合"
                confidence = (
                    self._inference_confidence(item)
                    if refs != "外部依据未闭合"
                    else "未闭合"
                )
                inference_rows.append(
                    f"| I{idx} | {self._cell(item.get('title') or item.get('snippet') or '未命名推断')} | "
                    f"{refs} | {confidence} | "
                    "可能属于不同条线/范围或已经完成建设；按验证计划核验 |"
                )
        else:
            inference_rows.append("| — | 当前没有独立登记的系统推断 | — | — | — |")
        return "\n".join((
            "## 7.1 外部证据索引",
            "",
            *evidence_rows,
            "",
            "> 仅收录外部或客户私有原始材料；evaluation Skill 输出不使用 E 编号。",
            "",
            "## 7.2 推断登记册",
            "",
            *inference_rows,
            "",
            "## 7.3 数据缺口声明",
            "",
            f"- **关键缺口：** {self._missing_layer_summary(self._gate.get('missing_layers') or [])}。",
            f"- **质量限制：** {self._quality_limit_summary()}。",
            "- **证据边界：** 未发现公开证据不等于能力不存在，也不等于商机存在。",
            "- **管线状态：** "
            f"{self._diagnostics.get('pipeline_classification') or 'UNKNOWN'}；"
            f"原始外部证据 {self._diagnostics.get('candidate_count', 0)} 条，"
            f"报告准入 {len(items)} 条，去重 {self._diagnostics.get('duplicate_count', 0)} 条。",
            "- **运行消耗：** "
            f"搜索 {self._diagnostics.get('search_queries', 0)} 次，"
            f"抓取 {self._diagnostics.get('fetched_items', 0)} 条，"
            f"提取批次 {self._diagnostics.get('extraction_batches', 0)} 个，"
            f"Token {self._diagnostics.get('total_tokens', 0)}。",
            "- **来源与时间质量：** "
            f"S/A 来源占比 {float(self._diagnostics.get('strong_source_ratio') or 0):.1%}，"
            f"未知日期占比 {float(self._diagnostics.get('unknown_date_ratio') or 0):.1%}，"
            f"内容农场进入提取占比 {float(self._diagnostics.get('content_farm_ratio') or 0):.1%}。",
        ))

    def _extracted_total(self) -> int:
        """全维提取总数（准入率的分母）；无管线指标时回退原始候选数。"""
        return int(
            self._diagnostics.get("extracted_items")
            or self._diagnostics.get("candidate_count", 0)
        )

    @staticmethod
    def _sales_investment_grade(gate_level: str, decision: str) -> str:
        normalized_gate = gate_level.strip().upper()
        normalized_decision = decision.strip().upper()
        if normalized_gate in {"G4", "G5"}:
            return "A"
        if normalized_gate == "G3":
            return "B"
        if normalized_gate in {"G1", "G2", "GX"}:
            return "C"
        if normalized_gate == "G0" or normalized_decision in {
            "NO_SIGNAL",
            "NO_OPPORTUNITY",
        }:
            return "D"
        return "C"

    @classmethod
    def _top_external_signals(
        cls,
        items: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        primary_items = []
        for item in items:
            metadata = item.get("meta_data") or {}
            scorecard = metadata.get("screening_scorecard")
            lane = (
                str(scorecard.get("signal_lane") or "core")
                if isinstance(scorecard, dict)
                else "core"
            )
            reliability = str(
                item.get("source_reliability") or "UNKNOWN"
            ).upper()
            if lane == "core" and reliability in {"S", "A", "B"}:
                primary_items.append(item)

        def priority(item: dict[str, Any]) -> tuple[int, tuple[int, int, str]]:
            text = cls._item_text(item)
            metadata = item.get("meta_data") or {}
            scorecard = metadata.get("screening_scorecard")
            evidence_role = (
                str(scorecard.get("evidence_role") or "")
                if isinstance(scorecard, dict)
                else ""
            )
            if evidence_role == "target_procurement_evidence" or any(
                token in text for token in _PROCUREMENT_TERMS
            ):
                bucket = 0
            elif any(token in text for token in ("官网", "年报", "公告", "验收", "上线")):
                bucket = 1
            elif any(token in text for token in ("投诉", "评价", "招聘", "岗位", "公众号")):
                bucket = 3
            else:
                bucket = 2
            return bucket, cls._signal_priority(item)

        return sorted(primary_items, key=priority)

    @classmethod
    def _judgement_for_signal(cls, item: dict[str, Any]) -> str:
        text = cls._item_text(item)
        title = cls._cell(item.get("title") or "未命名证据")
        if any(token in text for token in ("采购", "招标", "征集", "中标", "成交", "合同")):
            return f"已发现目标企业相关采购/项目事实：{title}"
        if any(token in text for token in ("上线", "建设", "平台", "系统")):
            return f"已发现客服能力建设或运营基线：{title}"
        if any(token in text for token in ("投诉", "评价", "等待", "转人工")):
            return f"发现服务体验样本，仅作为待验证痛点线索：{title}"
        return f"发现与客服中心相关的目标企业事实：{title}"

    @staticmethod
    def _confidence_label(item: dict[str, Any]) -> str:
        reliability = str(item.get("source_reliability") or "UNKNOWN").upper()
        return {
            "S": "高",
            "A": "高",
            "B": "中",
            "C": "低",
        }.get(reliability, "待评级")

    @staticmethod
    def _top_action(missing: set[str]) -> str:
        if "gap" in missing:
            return "由客户经理在 7 天内确认现役平台、四项核心服务指标与最痛的三个业务问题；无可量化缺口则停止升级商机。"
        if "trigger" in missing:
            return "在 7 天内追踪最近项目的中标—合同—验收—维保链，并确认是否存在政策、EOL、扩容或体验事件触发。"
        if "window" in missing:
            return "在 7 天内向采购/IT 负责人确认合同到期日、下一财年预算和未来 12 个月 RFI/RFP/POC 计划。"
        if "fit" in missing:
            return "在 7 天内完成我方能力档案与客户约束的逐项比对，确定旁路增量、扩容或全量替换中的唯一主攻形态。"
        return "在 7 天内锁定业务、技术和采购三方负责人，预约一次围绕已确认窗口的联合问诊。"

    @staticmethod
    def _critical_assumption(missing: set[str]) -> str:
        if "window" in missing:
            return "历史建设或采购尚未被后续项目完全覆盖，且未来 12 个月存在可进入的预算或合同窗口。"
        if "gap" in missing:
            return "现役客服能力确有可量化缺口，而非公开信息缺失造成的观察偏差。"
        if "fit" in missing:
            return "我方可在客户既有架构、合规与供应商准入约束内形成差异化价值。"
        return "当前触发事件能够获得业务负责人和预算负责人的共同承接。"

    @classmethod
    def _maximum_risk(cls, items: list[dict[str, Any]]) -> str:
        latest = cls._latest_dated_item(items)
        if latest is None:
            return "所有关键证据均缺少可验证日期，无法判断项目是否已经完成、失效或进入续约期。"
        return (
            "最近公开项目可能已完成建设或续约，若把历史项目误判为当前采购窗口，"
            "将导致售前资源投入方向错误。"
        )

    @classmethod
    def _best_item_by_topic(
        cls,
        items: list[dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for item in cls._top_external_signals(items):
            topic = cls._topic_for_item(item)
            result.setdefault(topic, item)
        return result

    @staticmethod
    def _vendor_name(item: dict[str, Any]) -> str:
        metadata = item.get("meta_data") or {}
        for field in (
            "incumbent_supplier",
            "supplier",
            "winning_supplier",
            "winner",
            "vendor",
            "provider",
        ):
            value = metadata.get(field)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    @staticmethod
    def _contract_end(item: dict[str, Any]) -> str:
        metadata = item.get("meta_data") or {}
        for field in (
            "contract_end_date",
            "service_end_date",
            "expiry_date",
            "maintenance_end_date",
        ):
            value = metadata.get(field)
            if value:
                return str(value)
        return ""

    @classmethod
    def _topic_for_item(cls, item: dict[str, Any]) -> str:
        text = cls._item_text(item)
        if any(token in text for token in ("投诉", "评价", "等待", "转人工")):
            return "experience"
        if any(token in text for token in ("bpo", "外包", "话务员", "人力资源服务")):
            return "bpo"
        if any(token in text for token in ("信创", "国产化", "自主可控", "国产")):
            return "xinchuang"
        if any(token in text for token in ("质检", "坐席辅助", "座席辅助")):
            return "quality"
        if any(token in text for token in ("智能", "机器人", "大模型", "数字人")):
            return "intelligence"
        if any(token in text for token in ("呼叫", "热线", "cti", "pbx", "ivr", "ipcc", "ip电话", "外呼")):
            return "voice"
        if any(token in text for token in _PROCUREMENT_TERMS):
            return "procurement"
        return "other"

    @staticmethod
    def _topic_label(topic: str) -> str:
        return {
            "voice": "呼叫平台与语音",
            "intelligence": "智能客服",
            "quality": "智能质检/坐席辅助",
            "xinchuang": "信创国产化",
            "bpo": "客服 BPO",
            "procurement": "采购与合同",
            "experience": "服务体验样本",
            "other": "其他客服线索",
        }.get(topic, "其他客服线索")

    @staticmethod
    def _item_text(item: dict[str, Any]) -> str:
        metadata = item.get("meta_data") or {}
        return " ".join((
            str(item.get("title") or ""),
            str(item.get("snippet") or ""),
            str(metadata.get("capability_domain") or ""),
            str(metadata.get("requirement_key") or ""),
            str(metadata.get("event_stage") or ""),
        )).casefold()

    @classmethod
    def _timeline_items(
        cls,
        items: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        dated = [
            item for item in items
            if cls._item_datetime(item) is not None
            and cls._topic_for_item(item) != "experience"
        ]
        ordered = sorted(
            dated,
            key=lambda item: cls._item_datetime(item) or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        by_event: dict[str, dict[str, Any]] = {}
        for item in ordered:
            metadata = item.get("meta_data") or {}
            project_key = str(metadata.get("project_key") or "").strip()
            event_stage = str(metadata.get("event_stage") or "").strip()
            key = (
                f"{project_key}:{event_stage}"
                if project_key and event_stage
                else str(item.get("id") or "")
            )
            by_event.setdefault(key, item)
        return list(by_event.values())

    @classmethod
    def _display_date(cls, item: dict[str, Any]) -> str:
        value = cls._item_datetime(item)
        return value.date().isoformat() if value is not None else "日期未知"

    @classmethod
    def _item_datetime(cls, item: dict[str, Any]) -> datetime | None:
        metadata = item.get("meta_data") or {}
        raw_value = (
            item.get("published_at")
            or metadata.get("event_date")
            or metadata.get("publish_date")
            or metadata.get("deadline_date")
        )
        if isinstance(raw_value, datetime):
            return (
                raw_value
                if raw_value.tzinfo is not None
                else raw_value.replace(tzinfo=timezone.utc)
            )
        if not raw_value:
            return None
        value = str(raw_value).strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            match = re.search(r"(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})", value)
            if not match:
                return None
            parsed = datetime(
                int(match.group(1)),
                int(match.group(2)),
                int(match.group(3)),
                tzinfo=timezone.utc,
            )
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)

    def _window_statement(self, timeline: list[dict[str, Any]]) -> str:
        if not timeline:
            return "缺少可用日期，不能推算预算或替换窗口。"
        latest_date = self._item_datetime(timeline[0])
        if latest_date is None:
            return "缺少可用日期，不能推算预算或替换窗口。"
        analysis_time = (
            self._analysis_as_of
            if self._analysis_as_of.tzinfo is not None
            else self._analysis_as_of.replace(tzinfo=timezone.utc)
        )
        age_months = max(0, (analysis_time.year - latest_date.year) * 12 + analysis_time.month - latest_date.month)
        if age_months > 36:
            return f"最近事件距分析日约 {age_months} 个月，仅作为历史基线，不构成当前采购窗口。"
        if age_months > 18:
            return f"最近事件距分析日约 {age_months} 个月，时效性已明显衰减，需核验合同与维保节点。"
        return (
            f"最近事件距分析日约 {age_months} 个月，可作为当前跟踪线索；"
            "但在获得合同到期、预算或 RFI/RFP 前不认定为采购窗口。"
        )

    def _supported_inferences(
        self,
        citation_by_id: dict[str, str],
    ) -> list[dict[str, Any]]:
        supported = []
        for item in self._inferences:
            if self._inference_refs(item, citation_by_id):
                supported.append(item)
        return supported

    @staticmethod
    def _inference_refs(
        item: dict[str, Any],
        citation_by_id: dict[str, str],
    ) -> str:
        metadata = item.get("meta_data") or {}
        raw_ids: list[Any] = []
        for field in (
            "supporting_evidence_ids",
            "evidence_ids",
            "source_evidence_ids",
        ):
            value = metadata.get(field)
            if isinstance(value, list):
                raw_ids.extend(value)
            elif value:
                raw_ids.append(value)
        refs = [
            citation_by_id[str(evidence_id)]
            for evidence_id in raw_ids
            if str(evidence_id) in citation_by_id
        ]
        return "、".join(dict.fromkeys(refs))

    @classmethod
    def _recommended_archetype(cls, item: dict[str, Any]) -> str:
        text = cls._item_text(item)
        if any(token in text for token in ("bpo", "外包", "运营")):
            return "BPO 软硬件解耦或联合运营"
        if any(token in text for token in ("eol", "eos", "停服", "全量替换")):
            return "双轨迁移后全量替换"
        return "旁路挂接/增量改造"

    @classmethod
    def _trigger_rows(
        cls,
        items: list[dict[str, Any]],
        citation_by_id: dict[str, str],
    ) -> list[str]:
        rows = [
            "| 触发类型 | 目标企业信号 | 窗口确定性 | 证据 | 销售含义 |",
            "|---|---|---|---|---|",
        ]
        trigger_terms = (
            ("合同/维保到期", ("到期", "续约", "维保"), "高"),
            ("政策与信创", ("信创", "国产化", "监管", "合规"), "中高"),
            ("技术生命周期", ("eol", "eos", "停服", "报废"), "中高"),
            ("业务扩张", ("扩容", "新设", "新增", "迁移"), "中"),
            ("体验与消保", ("投诉", "消保", "等待", "转人工", "服务事故"), "中低"),
            ("采购流程", ("征集", "招标", "采购", "rfi", "rfp", "poc"), "中高"),
        )
        matched = 0
        for label, terms, certainty in trigger_terms:
            candidates = [
                item for item in items
                if any(term in cls._item_text(item) for term in terms)
            ]
            if not candidates:
                continue
            item = sorted(candidates, key=cls._signal_priority)[0]
            rows.append(
                f"| {label} | {cls._cell(item.get('title') or '未命名信号')} | "
                f"{certainty}（待核验当前有效性） | {citation_by_id[item['id']]} | "
                "先核验责任部门、预算承接和发生时间，再决定是否升级商机 |"
            )
            matched += 1
        if not matched:
            rows.append(
                "| 未识别 | 未发现达到门槛的目标企业级触发事件 | 未知 | — | "
                "保持低成本监测，不进入 POC 或投标准备 |"
            )
        return rows

    @classmethod
    def _incumbent_summary(cls, items: list[dict[str, Any]]) -> str:
        vendors = [
            cls._vendor_name(item)
            for item in items
            if cls._vendor_name(item)
        ]
        unique_vendors = list(dict.fromkeys(vendors))
        return "、".join(unique_vendors) if unique_vendors else "未获得可确认的在任厂商证据"

    @classmethod
    def _latest_dated_item(
        cls,
        items: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        timeline = cls._timeline_items(items)
        return timeline[0] if timeline else None

    @staticmethod
    def _inference_confidence(item: dict[str, Any]) -> str:
        metadata = item.get("meta_data") or {}
        raw_value = metadata.get("confidence")
        if isinstance(raw_value, (int, float)):
            return f"{max(0.0, min(float(raw_value), 1.0)):.0%}"
        return "未评级"

    @staticmethod
    def _cell(value: Any) -> str:
        text = re.sub(r"\s+", " ", str(value or "")).strip()
        return text.replace("|", "｜") or "—"

    @staticmethod
    def _link(value: Any) -> str:
        url = str(value or "").strip()
        if not url:
            return "—"
        return f"[打开]({url})"

    def _battlecard(
        self,
        items: list[dict[str, Any]],
        citation_by_id: dict[str, str],
    ) -> str:
        grade = str(self._gate.get("gate_level") or "GX")
        decision = str(self._gate.get("decision") or "INSUFFICIENT_EVIDENCE")
        missing = [str(item) for item in self._gate.get("missing_layers") or []]
        reasons = [str(item) for item in self._gate.get("reasons") or []]
        top_signals = self._top_external_signals(items)[:3]
        signal_lines = (
            "\n".join(
                self._signal_line(item, citation_by_id[item["id"]])
                for item in top_signals
            )
            if top_signals
            else "- 未发现可支撑首屏判断的 S/A/B 级核心事实证据；弱来源仅保留在线索与待核验区。"
        )
        conclusion = (
            "存在可进入销售验证的候选窗口"
            if self._gate.get("can_create_opportunity_hypothesis") is True
            else "当前证据不足以确认可介入窗口，保留候选方向并先完成销售验证"
        )
        return "\n".join((
            f"- **目标企业：** {self._target_name}",
            f"- **分析方向：** {self._demand_direction}",
            f"- **截至日期：** {self._analysis_as_of.date().isoformat()}",
            f"- **OIG 裁决：** {grade} / {decision}",
            f"- **核心结论：** {conclusion}",
            f"- **优先关注方向：** {self._opportunity_direction(items)}",
            f"- **裁决依据：** {'；'.join(reasons) or '尚未形成完整裁决依据'}",
            f"- **待验证事项：** {self._missing_layer_summary(missing)}",
            "",
            self._commercial_objective(missing=missing, reasons=reasons),
            "",
            "## 30 秒证据摘要",
            "",
            signal_lines,
            "",
            "## 建议动作",
            "",
            "1. 向客户确认现役平台、厂商、上线时间、合同与维保到期日。",
            "2. 围绕已发现项目追踪公告—候选人—中标—合同—验收链，确认是否仍在建设或已进入升级期。",
            "3. 在未确认预算、触发事件和采购窗口前，不把历史建设事实表述为当前商机。",
        ))

    def _scope(self) -> str:
        classification = str(self._diagnostics.get("pipeline_classification") or "UNKNOWN")
        pipeline_label = {
            "HEALTHY": "证据准入正常",
            "LOW_RECALL": "检索召回不足",
            "FETCH_BLOCKED": "抓取受阻",
            "EXTRACTION_FAILED": "提取或准入失败",
            "CONTENT_FARM_DOMINATED": "内容农场或低价值转载占比过高",
            "LOW_QUALITY_SOURCES": "高可信来源不足",
            "TRUE_NO_SIGNAL": "已充分检索但未发现合格信号",
        }.get(classification, "未执行证据管线分类")
        return "\n".join((
            f"- 本报告主体限定为“{self._target_name}”，不将同名机构、行业汇总或其他企业案例归属给目标企业。",
            f"- 原始候选 {self._diagnostics.get('candidate_count', 0)} 条；准入核心证据 {self._diagnostics.get('selected_count', 0)} 条；去重 {self._diagnostics.get('duplicate_count', 0)} 条。",
            f"- 证据管线：{pipeline_label}；全维提取 {self._extracted_total()} 条，报告级准入 {self._diagnostics.get('selected_count', 0)} 条（{float(self._diagnostics.get('admission_ratio') or 0):.1%}）。",
            "- 事实、推断和待验证项分层展示；“未发现公开证据”不等于“能力不存在”。",
            f"- 交付状态：{'PARTIAL（存在质量缺口）' if self._partial_reasons else '核心证据质量门通过'}。",
        ))

    def _domain_section(
        self,
        section: str,
        items: list[dict[str, Any]],
        citation_by_id: dict[str, str],
    ) -> str:
        if "销售问诊" in section:
            return "\n".join((
                "## 建议客户经理直接询问",
                "",
                "- 当前 CTI/PBX、IVR、录音、质检、在线客服和工单系统分别由谁提供？",
                "- 现有合同与维保何时到期，是否存在单一来源续约或国产化时间表？",
                "- 智能质检覆盖率、机器人转人工率、平均等待时长和一次解决率分别是多少？",
                "- 是否已形成下一财年预算、RFI、POC 或供应商征集计划？",
            ))
        if "下一步行动" in section:
            return "\n".join((
                "## 建议节奏",
                "",
                "- **0–30 天：** 完成项目链和在任厂商核验。",
                "- **31–60 天：** 以旁路智能化、信创适配或存量扩容切入技术交流。",
                "- **61–90 天：** 仅在预算与窗口确认后推进 POC、立项材料或投标准备。",
            ))
        if "反证" in section:
            return "\n".join((
                "- **反证状态：** 当前未获得足以证明近期已完成同类升级、已续约或明确不采购的高质量直接反证。",
                f"- **未知项：** {self._missing_layer_summary(self._gate.get('missing_layers') or [])}。",
                f"- **质量限制：** {self._quality_limit_summary()}。",
                "- **替代解释：** 公开材料缺失可能来自采购未公开、集团统一建设、BPO 打包提供或项目名称未使用客服中心术语。",
            ))
        keywords = self._keywords_for_section(section)
        matched = self._matching_items(items, keywords)
        if not matched:
            return "\n".join((
                "- **当前判断：** 未发现达到报告准入标准的直接公开证据。",
                "- **结论边界：** 这表示公开信息不足，不表示目标企业没有该项能力或需求。",
                "- **待验证：** 通过客户访谈、项目链追踪或授权体验测试补齐现状、厂商、时间和效果指标。",
            ))
        facts = "\n".join(
            self._fact_line(item, citation_by_id[item["id"]])
            for item in matched[:6]
        )
        return "\n".join((
            "## 证据与判断",
            "",
            facts,
            "",
            "## 分析与边界",
            "",
            "- **推断：** 上述证据表明该主题已有建设、运营或采购痕迹；是否形成新采购需求，仍取决于缺口、触发事件和预算窗口。",
            "- **待验证：** 补齐现役厂商、部署范围、上线时间、合同到期日、关键服务指标及用户满意度。",
        ))

    def _evidence_index(
        self,
        items: list[dict[str, Any]],
        citation_by_id: dict[str, str],
    ) -> str:
        lines = []
        for item in items:
            metadata = item.get("meta_data") or {}
            date_value = (
                item.get("published_at")
                or metadata.get("event_date")
                or metadata.get("publish_date")
                or "日期未知"
            )
            reliability = item.get("source_reliability") or "UNKNOWN"
            kind_label = self._display_label(item)
            lines.append(
                f"- [{citation_by_id[item['id']]}] [{kind_label}] {item['title']}｜"
                f"来源等级 {reliability}｜{date_value}｜{item.get('url') or '无链接'}"
            )
        return "\n".join((
            "## 核心证据索引",
            "",
            *lines,
            "",
            "## 反证与限制",
            "",
            f"- 质量限制：{self._quality_limit_summary()}",
            f"- 待验证事项：{self._missing_layer_summary(self._gate.get('missing_layers') or [])}。",
            "- 未经直接证据确认的厂商、合同、预算和采购窗口均保留为待验证项。",
        ))

    def _commercial_objective(
        self,
        *,
        missing: list[str],
        reasons: list[str],
    ) -> str:
        missing_set = {item.strip().lower() for item in missing}
        reason_summary = "；".join(reasons) or "当前 Gate 尚未形成充分裁决依据"
        need_missing = "gap" in missing_set
        trigger_missing = "trigger" in missing_set
        window_missing = "window" in missing_set
        fit_missing = "fit" in missing_set

        need = (
            "未确认可量化业务缺口；需核实现役能力覆盖率、服务指标、扩容需求及已知痛点"
            if need_missing
            else f"已通过 Gate 形成缺口判断：{reason_summary}"
        )
        trigger = (
            "未确认当前有效触发；历史建设、行业趋势或通用政策不能单独证明近期采购"
            if trigger_missing
            else "已确认当前触发事件；应继续核验事件强度、责任部门和预算承接关系"
        )
        window = (
            "采购窗口未知；需核验合同/维保到期日、预算编制期、项目阶段和下一次供应商征集时间"
            if window_missing
            else "已确认当前窗口；应锁定预算节点、决策链和供应商准入截止时间"
        )
        win_strategy = (
            "未完成产品适配、在任厂商锁定和竞争突破口验证，不得估算赢率"
            if fit_missing
            else "产品适配已通过 Gate；仍需结合在任厂商锁定、采购偏好和差异化价值制定赢单策略"
        )
        action = self._next_commercial_action(missing_set)
        return "\n".join((
            "## 商业判断五要素",
            "",
            "| 商业问题 | 当前判断 | 状态 |",
            "|---|---|---|",
            f"| 采购缺口（为什么买） | {need} | {'待验证' if need_missing else '已验证'} |",
            f"| 采购触发（为何现在买） | {trigger} | {'待验证' if trigger_missing else '已验证'} |",
            f"| 采购窗口（什么时候买） | {window} | {'待验证' if window_missing else '已验证'} |",
            f"| 赢单判断（为什么选我们） | {win_strategy} | {'待验证' if fit_missing else '已验证'} |",
            f"| 下一行动（如何推进） | {action} | 立即执行 |",
        ))

    @staticmethod
    def _next_commercial_action(missing: set[str]) -> str:
        if "gap" in missing:
            return "联系客户客服中心/远程银行与信息科技部门，核实现役平台、服务指标和最痛的三个业务问题；无可量化缺口则维持基线观察"
        if "trigger" in missing:
            return "追踪重点项目的公告—中标—合同—验收—维保链，并向客户确认当前驱动；无有效触发则不升级商机"
        if "window" in missing:
            return "核验合同与维保到期日、下一财年预算和供应商征集计划；确认十二个月内窗口后再进入重点跟进"
        if "fit" in missing:
            return "加载我方产品能力档案，比较全量替换、旁路增量和扩容方案；存在硬性能力缺口则终止匹配"
        return "锁定业务负责人、预算负责人和技术决策人，准备差异化方案并按已确认窗口推进技术交流或 POC"

    @classmethod
    def _missing_layer_summary(cls, layers: Iterable[Any]) -> str:
        labels = [
            cls._MISSING_LAYER_LABELS.get(str(item).strip().lower(), str(item))
            for item in layers
        ]
        return "、".join(labels) or "无"

    @staticmethod
    def _matching_items(
        items: list[dict[str, Any]],
        keywords: tuple[str, ...],
    ) -> list[dict[str, Any]]:
        if not keywords:
            return list(items)
        result = []
        for item in items:
            metadata = item.get("meta_data") or {}
            text = " ".join((
                str(item.get("title") or ""),
                str(item.get("snippet") or ""),
                str(metadata.get("capability_domain") or ""),
                str(metadata.get("requirement_key") or ""),
            )).casefold()
            if any(keyword in text for keyword in keywords):
                result.append(item)
        return result

    @staticmethod
    def _fact_line(item: dict[str, Any], citation_key: str) -> str:
        metadata = item.get("meta_data") or {}
        date_value = (
            item.get("published_at")
            or metadata.get("event_date")
            or metadata.get("publish_date")
            or "日期未知"
        )
        label = ContactCenterReportComposer._display_label(item)
        return f"- **{label}：** {item['title']}（{date_value}）[{citation_key}]"

    @staticmethod
    def _signal_line(item: dict[str, Any], citation_key: str) -> str:
        label = ContactCenterReportComposer._display_label(item, summary=True)
        return f"- **{label}：** {item['title']} [{citation_key}]"

    @staticmethod
    def _is_inference(item: dict[str, Any]) -> bool:
        return ContactCenterReportComposer._evidence_kind(item) != "FACT"

    @staticmethod
    def _signal_priority(item: dict[str, Any]) -> tuple[int, int, str]:
        kind = ContactCenterReportComposer._evidence_kind(item)
        reliability = str(item.get("source_reliability") or "UNKNOWN").upper()
        metadata = item.get("meta_data") or {}
        scorecard = metadata.get("screening_scorecard")
        evidence_role = (
            str(scorecard.get("evidence_role") or "")
            if isinstance(scorecard, dict)
            else ""
        )
        if kind == "FACT" and reliability in {"S", "A", "B"}:
            bucket = 0
        elif kind == "ASSUMPTION" and evidence_role == "target_procurement_evidence":
            bucket = 1
        elif kind == "INFERENCE":
            bucket = 2
        elif kind == "FACT":
            bucket = 3
        else:
            bucket = 4
        deterministic_score = (
            int(scorecard.get("deterministic_score") or 0)
            if isinstance(scorecard, dict)
            else 0
        )
        return bucket, -deterministic_score, str(item.get("title") or "")

    @staticmethod
    def _evidence_kind(item: dict[str, Any]) -> str:
        metadata = item.get("meta_data") or {}
        kind = str(
            item.get("fact_or_inference")
            or metadata.get("fact_or_inference")
            or ""
        ).upper()
        if kind == "ASSUMPTION":
            return "ASSUMPTION"
        if kind == "INFERENCE" or metadata.get("evaluation_skill"):
            return "INFERENCE"
        return "FACT"

    @staticmethod
    def _display_label(item: dict[str, Any], *, summary: bool = False) -> str:
        kind = ContactCenterReportComposer._evidence_kind(item)
        if kind == "ASSUMPTION":
            return "待核验线索"
        if kind == "INFERENCE":
            return "推断"
        reliability = str(item.get("source_reliability") or "UNKNOWN").upper()
        if reliability not in {"S", "A", "B"}:
            return "事实线索（来源待评级）"
        return "已确认事实" if summary else "事实"

    @staticmethod
    def _keywords_for_section(section: str) -> tuple[str, ...]:
        mappings = (
            (("能力地图", "服务模式"), ("客服", "热线", "呼叫", "ivr", "cti", "ipcc", "座席", "坐席", "外呼")),
            (("招采", "生命周期"), _PROCUREMENT_TERMS),
            (("在任厂商", "竞争态势", "锁定"), ("供应商", "厂商", "中标", "思科", "华为", "genesys", "avaya", "ucce")),
            (("信创", "国产化"), ("信创", "国产", "自主可控")),
            (("智能化",), ("智能", "大模型", "机器人", "质检", "辅助", "多语言")),
            (("呼叫平台", "IP 电话", "全渠道"), ("呼叫", "热线", "ivr", "cti", "ipcc", "pbx", "ip电话", "外呼", "录音")),
            (("BPO", "人员", "运营模式"), ("bpo", "外包", "话务员", "坐席", "座席", "人力资源服务")),
            (("服务体验", "用户评价"), ("投诉", "评价", "消保", "等待", "转人工", "体验")),
            (("商机候选", "OIG"), ("采购", "招标", "征集", "升级", "改造", "质检", "智能", "信创", "外呼")),
        )
        for names, keywords in mappings:
            if any(name in section for name in names):
                return tuple(keywords)
        return ()

    def _quality_limit_summary(self) -> str:
        if not self._partial_reasons:
            return "无强制质量降级项"
        dimensions = {
            reason.split(":", 1)[0]
            for reason in self._partial_reasons
            if ":" in reason
        }
        problems = set()
        for reason in self._partial_reasons:
            if "field_coverage" in reason:
                problems.add("关键提取字段不完整")
            elif "timeliness" in reason:
                problems.add("发布日期或有效期不足")
            elif "source_diversity" in reason:
                problems.add("来源多样性不足")
            elif "overall_score" in reason:
                problems.add("综合质量分未达门槛")
            else:
                problems.add("存在未解决质量项")
        return f"{len(dimensions)} 个研究维度存在：" + "、".join(sorted(problems))

    @staticmethod
    def _opportunity_direction(items: list[dict[str, Any]]) -> str:
        text = " ".join(str(item.get("title") or "") for item in items).casefold()
        directions = []
        if any(term in text for term in ("信创", "国产", "自主可控")):
            directions.append("信创适配/替换")
        if any(term in text for term in ("智能", "机器人", "质检", "大模型")):
            directions.append("客服智能化升级")
        if any(term in text for term in ("呼叫", "热线", "cti", "ivr", "ipcc", "pbx", "外呼")):
            directions.append("呼叫平台与语音能力")
        if any(term in text for term in ("bpo", "外包", "话务员", "人力资源服务")):
            directions.append("客服 BPO/坐席运营")
        return "、".join(directions) or "先补齐客服中心能力基线"
