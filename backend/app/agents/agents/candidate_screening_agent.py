"""Single 全量候选筛选智能体。

本模块只提供影子运行所需的 v6 评分卡服务，不接入 Harness，不改变抓取、提取或
报告输入。模型只判断需求关系与两个 0~2 分值；主体、证据形态、生命周期、角色、
相关性和 Top≤20 均由程序从候选原文确定性派生。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping, Optional

from app.agents.schemas.candidate_schema import Candidate, CandidateSet
from app.config_center.research_config import (
    DEFAULT_CANDIDATE_SCREENING_CONFIG,
    load_candidate_screening_prompt,
    validate_candidate_screening_config,
)
from app.llm.gateway_client import GatewayClient, get_gateway_client


_DEMAND_RELATIONS = frozenset({
    "core_customer_service",
    "adjacent_customer_operation",
    "unrelated",
    "uncertain",
})
_SUBSIDIARY_QUALIFIERS = (
    "分行", "支行", "分公司", "子公司", "产险", "财险", "寿险", "人寿",
    "养老险", "养老保险", "健康险", "健康保险", "资产管理",
)
_EXPLICIT_PROCUREMENT_TERMS = (
    "招标公告", "中标公告", "中标结果", "成交公告", "成交结果", "竞争性磋商",
    "询价公告", "比选公告", "单一来源", "采购公告", "征集公告", "废标", "流标",
)
_GENERIC_PROCUREMENT_TERMS = ("采购", "招标", "投标", "中标", "成交", "磋商", "询价", "比选")
_VENDOR_CASE_TERMS = ("客户案例", "成功案例", "案例实践", "案例")
_OPERATION_SIGNAL_TERMS = ("上线", "启用", "运营", "升级", "改造", "应用", "投产")
_CLOSED_PROCUREMENT_TERMS = ("中标", "成交", "终止", "废标", "流标", "失败")
_DEADLINE_TERMS = ("截止时间", "投标截止", "递交截止", "报名截止")
_CORE_DEMAND_TERMS = (
    "客服中心", "呼叫中心", "客户服务中心", "智能客服", "客服机器人", "话务",
    "坐席", "95500", "智能语音", "语音外呼", "电销", "客服录音",
)
_ADJACENT_DEMAND_TERMS = ("排班", "回访", "培训", "客服运营")
_UNRELATED_DEMAND_TERMS = ("反诈", "装修", "空调", "布线", "供应链", "福利", "媒体活动")
_ROLE_PRIORITY = {
    "uncertain": 0,
    "out_of_scope": 0,
    "vendor_case_intelligence": 1,
    "industry_capability_intelligence": 2,
    "target_operation_signal": 3,
    "target_procurement": 4,
    "active_target_opportunity": 5,
}
_TYPE_PRIORITY = {
    "weak_or_irrelevant": 0,
    "industry_analog": 1,
    "target_adjacent": 2,
    "target_direct": 3,
}
_TYPE_BY_ROLE = {
    "active_target_opportunity": "target_direct",
    "target_procurement": "target_direct",
    "target_operation_signal": "target_adjacent",
    "industry_capability_intelligence": "industry_analog",
    "vendor_case_intelligence": "industry_analog",
    "out_of_scope": "weak_or_irrelevant",
    "uncertain": "weak_or_irrelevant",
}
_DATE_PATTERN = re.compile(
    r"(?P<year>20\d{2})\s*(?:年|[-/.])\s*(?P<month>\d{1,2})\s*"
    r"(?:月|[-/.])\s*(?P<day>\d{1,2})\s*日?"
)


class CandidateScreeningSchemaError(ValueError):
    """模型评分卡不符合 v6 单次输出契约。"""

    def __init__(self, message: str, *, code: str = "schema_invalid") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class CandidateScreeningContext:
    """不含人工标签的研究上下文。"""

    company_name: str
    demand_direction: str
    dimension: str
    target_entity_names: tuple[str, ...] = ()
    target_parent_names: tuple[str, ...] = ()
    target_scope_policy: str = "specified_entity_and_parent"

    def to_prompt_dict(self) -> dict[str, Any]:
        return {
            "company_name": _required_text(self.company_name, "company_name"),
            "demand_direction": _required_text(self.demand_direction, "demand_direction"),
            "dimension": _required_text(self.dimension, "dimension"),
            "target_entity_names": _normalized_names(self.target_entity_names),
            "target_parent_names": _normalized_names(self.target_parent_names),
            "target_scope_policy": _required_text(self.target_scope_policy, "target_scope_policy"),
        }


@dataclass(frozen=True)
class CandidateScreeningResult:
    """一次 Single 调用的结构化影子结果。"""

    scorecards: tuple[dict[str, Any], ...]
    selected_candidate_ids: tuple[str, ...]
    model: str
    provider: str
    usage: Mapping[str, Any]
    finish_reason: str
    call_timeout_seconds: int
    max_output_tokens: int
    output_token_warning: bool


@dataclass(frozen=True)
class CandidateScreeningPositionView:
    """单个旋转位置视图的独立 Single 评分结果。"""

    offset: int
    result: CandidateScreeningResult


@dataclass(frozen=True)
class CandidateScreeningPositionDiagnostics:
    """影子位置稳定性诊断，不生成新的候选选择结论。"""

    views: tuple[CandidateScreeningPositionView, ...]
    selected_set_overlap: Mapping[str, float]
    minimum_selected_set_overlap: float
    position_role_consistency_rate: float
    role_inconsistent_candidate_ids: tuple[str, ...]


@dataclass(frozen=True)
class CandidateScreeningFailureAudit:
    """结构失败的安全审计记录，不保存模型正文、Prompt 或候选摘要。"""

    error_code: str
    error_message: str
    candidate_count: int
    evaluated_at: datetime
    protocol_version: str = "candidate-screening-v6"


@dataclass(frozen=True)
class CandidateScreeningAttempt:
    """影子调用结果或结构失败审计，二者恰有一个存在。"""

    result: CandidateScreeningResult | None
    failure_audit: CandidateScreeningFailureAudit | None


class CandidateScreeningAgent:
    """按 v6 协议执行一次全量候选评分和程序化选择。"""

    def __init__(
        self,
        llm_client: Optional[GatewayClient] = None,
        *,
        model: Optional[str] = None,
        prompt_template: Optional[str] = None,
    ) -> None:
        self.llm_client = llm_client or get_gateway_client()
        self.model = model
        self._prompt_template = prompt_template or load_candidate_screening_prompt()

    def execute(
        self,
        candidate_set: CandidateSet,
        context: CandidateScreeningContext,
        *,
        config: Optional[Mapping[str, Any]] = None,
        evaluated_at: Optional[datetime] = None,
    ) -> CandidateScreeningResult:
        effective_config = validate_candidate_screening_config(
            config or DEFAULT_CANDIDATE_SCREENING_CONFIG
        )
        prompt_context = context.to_prompt_dict()
        candidate_by_id = {candidate.candidate_id: candidate for candidate in candidate_set.candidates}
        if len(candidate_by_id) != len(candidate_set.candidates):
            raise ValueError("CandidateSet 包含重复 candidate_id")
        if not candidate_by_id:
            return CandidateScreeningResult(
                scorecards=(),
                selected_candidate_ids=(),
                model="",
                provider="",
                usage={"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
                finish_reason="not_called_empty_candidate_set",
                call_timeout_seconds=_resolve_timeout_seconds(0, effective_config),
                max_output_tokens=effective_config["max_output_tokens"],
                output_token_warning=False,
            )

        common_evaluated_at = evaluated_at or datetime.now(timezone.utc)
        prompt = self._build_prompt(
            candidates=candidate_set.candidates,
            context=prompt_context,
            evaluated_at=common_evaluated_at,
        )
        timeout_seconds = _resolve_timeout_seconds(len(candidate_by_id), effective_config)
        response = self.llm_client.infer(
            prompt=prompt,
            model=self.model,
            response_format={"type": "json_object"},
            temperature=0,
            max_tokens=effective_config["max_output_tokens"],
            timeout_seconds=timeout_seconds,
            max_retries=0,
            thinking_mode="disabled",
        )
        if str(response.get("finish_reason") or "stop") == "length":
            raise CandidateScreeningSchemaError(
                "评分卡输出被 Provider 截断",
                code="finish_reason_length",
            )
        scorecards = _parse_scorecards(
            str(response.get("content") or ""),
            candidate_by_id,
            context=prompt_context,
            evaluated_at=common_evaluated_at,
        )
        selected = _rank_and_select(scorecards, top_k=effective_config["top_k"])
        usage = response.get("usage") if isinstance(response.get("usage"), Mapping) else {}
        output_tokens = usage.get("output_tokens", 0)
        output_token_warning = isinstance(output_tokens, (int, float)) and output_tokens > effective_config[
            "output_token_warning_threshold"
        ]
        return CandidateScreeningResult(
            scorecards=tuple(scorecards),
            selected_candidate_ids=tuple(card["candidate_id"] for card in selected),
            model=str(response.get("model") or ""),
            provider=str(response.get("provider") or ""),
            usage=dict(usage),
            finish_reason=str(response.get("finish_reason") or "stop"),
            call_timeout_seconds=timeout_seconds,
            max_output_tokens=effective_config["max_output_tokens"],
            output_token_warning=bool(output_token_warning),
        )

    def execute_with_audit(
        self,
        candidate_set: CandidateSet,
        context: CandidateScreeningContext,
        *,
        config: Optional[Mapping[str, Any]] = None,
        evaluated_at: Optional[datetime] = None,
    ) -> CandidateScreeningAttempt:
        """执行一次且只一次；结构失败时返回安全审计，不作补偿调用。"""
        common_evaluated_at = evaluated_at or datetime.now(timezone.utc)
        try:
            result = self.execute(
                candidate_set,
                context,
                config=config,
                evaluated_at=common_evaluated_at,
            )
        except CandidateScreeningSchemaError as error:
            return CandidateScreeningAttempt(
                result=None,
                failure_audit=CandidateScreeningFailureAudit(
                    error_code=error.code,
                    error_message=str(error),
                    candidate_count=len(candidate_set.candidates),
                    evaluated_at=common_evaluated_at,
                ),
            )
        return CandidateScreeningAttempt(result=result, failure_audit=None)

    def execute_position_diagnostics(
        self,
        candidate_set: CandidateSet,
        context: CandidateScreeningContext,
        *,
        config: Optional[Mapping[str, Any]] = None,
        evaluated_at: Optional[datetime] = None,
    ) -> CandidateScreeningPositionDiagnostics:
        """以固定旋转位置独立运行 Single，并计算集合和角色稳定性。"""
        effective_config = validate_candidate_screening_config(
            config or DEFAULT_CANDIDATE_SCREENING_CONFIG
        )
        common_evaluated_at = evaluated_at or datetime.now(timezone.utc)
        views: list[CandidateScreeningPositionView] = []
        for offset in effective_config["position_offsets"]:
            rotated_set = _rotate_candidate_set(candidate_set, offset)
            views.append(CandidateScreeningPositionView(
                offset=offset,
                result=self.execute(
                    rotated_set,
                    context,
                    config=effective_config,
                    evaluated_at=common_evaluated_at,
                ),
            ))

        overlap: dict[str, float] = {}
        for left_index, left_view in enumerate(views):
            left_ids = set(left_view.result.selected_candidate_ids)
            for right_view in views[left_index + 1:]:
                right_ids = set(right_view.result.selected_candidate_ids)
                denominator = max(len(left_ids), len(right_ids))
                key = f"{left_view.offset}:{right_view.offset}"
                overlap[key] = 1.0 if denominator == 0 else round(
                    len(left_ids & right_ids) / denominator,
                    6,
                )

        role_by_candidate: dict[str, set[str]] = {}
        for view in views:
            for scorecard in view.result.scorecards:
                role_by_candidate.setdefault(scorecard["candidate_id"], set()).add(
                    scorecard["evidence_role"]
                )
        inconsistent_ids = tuple(sorted(
            candidate_id
            for candidate_id, roles in role_by_candidate.items()
            if len(roles) > 1
        ))
        consistency_rate = (
            round((len(role_by_candidate) - len(inconsistent_ids)) / len(role_by_candidate), 6)
            if role_by_candidate else 1.0
        )
        return CandidateScreeningPositionDiagnostics(
            views=tuple(views),
            selected_set_overlap=overlap,
            minimum_selected_set_overlap=min(overlap.values()) if overlap else 1.0,
            position_role_consistency_rate=consistency_rate,
            role_inconsistent_candidate_ids=inconsistent_ids,
        )

    def _build_prompt(
        self,
        *,
        candidates: Iterable[Candidate],
        context: Mapping[str, Any],
        evaluated_at: datetime,
    ) -> str:
        compact_candidates = [
            _candidate_for_prompt(candidate, context, evaluated_at)
            for candidate in candidates
        ]
        prompt = self._prompt_template.replace(
            "{{research_context_json}}",
            json.dumps(context, ensure_ascii=False, separators=(",", ":")),
        ).replace(
            "{{candidates_json}}",
            json.dumps(compact_candidates, ensure_ascii=False, separators=(",", ":")),
        )
        if "{{" in prompt:
            raise ValueError("candidate screening Prompt 存在未替换占位符")
        return prompt


def _required_text(value: object, name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{name} 不能为空")
    return text


def _normalized_names(values: Iterable[str]) -> list[str]:
    return [str(value).strip() for value in values if str(value).strip()]


def _resolve_timeout_seconds(candidate_count: int, config: Mapping[str, Any]) -> int:
    for item in config["timeout_schedule"]:
        if candidate_count <= item["max_candidate_count"]:
            return item["seconds"]
    return config["timeout_schedule"][-1]["seconds"]


def _rotate_candidate_set(candidate_set: CandidateSet, offset: int) -> CandidateSet:
    """按固定偏移循环轮转候选，不改变集合、ID 或来源轨迹。"""
    candidates = candidate_set.candidates
    if not candidates:
        return candidate_set
    normalized_offset = offset % len(candidates)
    return CandidateSet.create(
        dimension=candidate_set.dimension,
        candidates=candidates[normalized_offset:] + candidates[:normalized_offset],
        source_result_count=candidate_set.source_result_count,
    )


def _candidate_for_prompt(
    candidate: Candidate,
    context: Mapping[str, Any],
    evaluated_at: datetime,
) -> dict[str, Any]:
    subject_relation, subject_basis = _derive_subject_relation(candidate, context)
    evidence_form, form_basis = _derive_evidence_form(candidate)
    lifecycle, lifecycle_basis = _derive_lifecycle(candidate, evidence_form, evaluated_at)
    combined = f"{candidate.title}\n{candidate.snippet}"
    return {
        "candidate_id": candidate.candidate_id,
        "title": candidate.title,
        "url": candidate.normalized_url,
        "domain": candidate.domain,
        "snippet": candidate.snippet,
        "published_at": candidate.published_at.isoformat() if candidate.published_at else "",
        "source": candidate.content_source,
        "deterministic_hints": {
            "subject_relation": subject_relation,
            "subject_relation_basis": subject_basis,
            "evidence_form": evidence_form,
            "evidence_form_basis": form_basis,
            "procurement_lifecycle": lifecycle,
            "lifecycle_basis": lifecycle_basis,
            "demand_keyword_hints": {
                "core_hits": [term for term in _CORE_DEMAND_TERMS if term in combined],
                "adjacent_hits": [term for term in _ADJACENT_DEMAND_TERMS if term in combined],
                "unrelated_hits": [term for term in _UNRELATED_DEMAND_TERMS if term in combined],
            },
        },
    }


def _alias_match(text: str, names: Iterable[str]) -> tuple[str, int] | None:
    matches = [(name, text.find(name)) for name in _normalized_names(names) if name in text]
    return min(matches, key=lambda item: (-len(item[0]), item[1], item[0])) if matches else None


def _derive_subject_relation(candidate: Candidate, context: Mapping[str, Any]) -> tuple[str, dict[str, str]]:
    for location, text in (("title", candidate.title), ("snippet", candidate.snippet)):
        for names, relation, rule in (
            (context["target_entity_names"], "exact_target", "configured_target_name_match"),
            (context["target_parent_names"], "parent_entity", "configured_parent_name_match"),
        ):
            match = _alias_match(text, names)
            if not match:
                continue
            alias, start = match
            suffix = text[start + len(alias):start + len(alias) + 14]
            qualifier = next((item for item in _SUBSIDIARY_QUALIFIERS if item in suffix), "")
            if qualifier:
                return "other_branch_or_subsidiary", {
                    "rule": "target_alias_extended_by_non_target_qualifier" if relation == "exact_target" else "parent_alias_extended_by_branch_or_subsidiary",
                    "matched_name": alias,
                    "matched_qualifier": qualifier,
                    "location": location,
                }
            return relation, {"rule": rule, "matched_name": alias, "matched_qualifier": "", "location": location}
    return "external", {"rule": "no_target_scope_anchor", "matched_name": "", "matched_qualifier": "", "location": "none"}


def _derive_evidence_form(candidate: Candidate) -> tuple[str, dict[str, str]]:
    for term in _EXPLICIT_PROCUREMENT_TERMS:
        if term in candidate.title:
            return "procurement", {"rule": "explicit_procurement_title_term", "matched_term": term}
    for term in _VENDOR_CASE_TERMS:
        if term in candidate.title:
            return "vendor_case", {"rule": "vendor_case_title_term", "matched_term": term}
    combined = f"{candidate.title}\n{candidate.snippet}"
    for term in _GENERIC_PROCUREMENT_TERMS:
        if term in combined:
            return "procurement", {"rule": "generic_procurement_term", "matched_term": term}
    for term in _OPERATION_SIGNAL_TERMS:
        if term in combined:
            return "operation_signal", {"rule": "operation_signal_term", "matched_term": term}
    return "other", {"rule": "no_form_signal", "matched_term": ""}


def _derive_lifecycle(candidate: Candidate, evidence_form: str, evaluated_at: datetime) -> tuple[str, dict[str, str]]:
    if evidence_form != "procurement":
        return "not_applicable", {"rule": "non_procurement", "deadline_at": ""}
    for term in _CLOSED_PROCUREMENT_TERMS:
        if term in candidate.title:
            return "closed_or_failed", {"rule": "closed_title_term", "matched_term": term, "deadline_at": ""}
    for text in (candidate.title, candidate.snippet):
        if not any(term in text for term in _DEADLINE_TERMS):
            continue
        match = _DATE_PATTERN.search(text)
        if not match:
            continue
        try:
            deadline = datetime(
                int(match.group("year")), int(match.group("month")), int(match.group("day")),
                23, 59, 59, tzinfo=timezone(timedelta(hours=8)),
            )
        except ValueError:
            continue
        comparison_time = evaluated_at if evaluated_at.tzinfo else evaluated_at.replace(tzinfo=timezone.utc)
        return (
            "active" if deadline > comparison_time else "historical_or_unknown",
            {"rule": "explicit_year_deadline" if deadline > comparison_time else "explicit_deadline_expired", "deadline_at": deadline.isoformat()},
        )
    return "historical_or_unknown", {"rule": "no_explicit_year_deadline", "deadline_at": ""}


def _parse_scorecards(
    content: str,
    candidates: Mapping[str, Candidate],
    *,
    context: Mapping[str, Any],
    evaluated_at: datetime,
) -> list[dict[str, Any]]:
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as error:
        raise CandidateScreeningSchemaError(
            "评分卡响应不是合法 JSON",
            code="invalid_json",
        ) from error
    if not isinstance(payload, Mapping) or set(payload) != {"scores"} or not isinstance(payload["scores"], list):
        raise CandidateScreeningSchemaError(
            "评分卡响应必须是仅包含 scores 数组的对象",
            code="invalid_root",
        )
    expected_ids = set(candidates)
    seen_ids: set[str] = set()
    scorecards: list[dict[str, Any]] = []
    for item in payload["scores"]:
        if not isinstance(item, Mapping) or set(item) != {"candidate_id", "demand_relation", "source_quality", "novelty"}:
            raise CandidateScreeningSchemaError(
                "每条评分卡只能包含 v6 的四个字段",
                code="invalid_score_fields",
            )
        candidate_id = item.get("candidate_id")
        if not isinstance(candidate_id, str) or candidate_id not in expected_ids:
            raise CandidateScreeningSchemaError(
                "评分卡包含未知 candidate_id",
                code="unknown_candidate_id",
            )
        if candidate_id in seen_ids:
            raise CandidateScreeningSchemaError(
                "评分卡包含重复 candidate_id",
                code="duplicate_candidate_id",
            )
        demand_relation = item.get("demand_relation")
        if demand_relation not in _DEMAND_RELATIONS:
            raise CandidateScreeningSchemaError(
                "评分卡 demand_relation 非法",
                code="invalid_demand_relation",
            )
        if any(type(item.get(field)) is not int or not 0 <= item[field] <= 2 for field in ("source_quality", "novelty")):
            raise CandidateScreeningSchemaError(
                "评分卡 source_quality 和 novelty 必须为 0 到 2 的整数",
                code="invalid_score_range",
            )
        candidate = candidates[candidate_id]
        subject_relation, subject_basis = _derive_subject_relation(candidate, context)
        evidence_form, form_basis = _derive_evidence_form(candidate)
        lifecycle, lifecycle_basis = _derive_lifecycle(candidate, evidence_form, evaluated_at)
        role = _derive_role(subject_relation, demand_relation, evidence_form, lifecycle)
        scorecards.append({
            "candidate_id": candidate_id,
            "subject_relation": subject_relation,
            "subject_relation_basis": subject_basis,
            "demand_relation": demand_relation,
            "evidence_form": evidence_form,
            "evidence_form_basis": form_basis,
            "procurement_lifecycle": lifecycle,
            "lifecycle_basis": lifecycle_basis,
            "relevance": _derive_relevance(subject_relation, demand_relation, evidence_form),
            "source_quality": item["source_quality"],
            "novelty": item["novelty"],
            "evidence_role": role,
            "evidence_type": _TYPE_BY_ROLE[role],
            "published_at": candidate.published_at,
        })
        seen_ids.add(candidate_id)
    if seen_ids != expected_ids:
        raise CandidateScreeningSchemaError(
            "评分卡缺少输入 candidate_id",
            code="missing_candidate_id",
        )
    return scorecards


def _derive_relevance(subject_relation: str, demand_relation: str, evidence_form: str) -> int:
    if demand_relation == "unrelated":
        return 0
    if demand_relation == "uncertain":
        return 1
    if subject_relation in {"exact_target", "parent_entity"} and demand_relation == "core_customer_service" and evidence_form == "procurement":
        return 4
    return 3


def _derive_role(subject_relation: str, demand_relation: str, evidence_form: str, lifecycle: str) -> str:
    if demand_relation == "unrelated":
        return "out_of_scope"
    if demand_relation == "uncertain":
        return "uncertain"
    if evidence_form == "vendor_case":
        return "vendor_case_intelligence"
    if subject_relation in {"exact_target", "parent_entity"}:
        if evidence_form == "procurement":
            return (
                "active_target_opportunity"
                if lifecycle == "active" and demand_relation == "core_customer_service"
                else "target_procurement"
            )
        return "target_operation_signal"
    return "industry_capability_intelligence"


def _rank_and_select(scorecards: Iterable[dict[str, Any]], *, top_k: int) -> list[dict[str, Any]]:
    def published_timestamp(card: Mapping[str, Any]) -> float:
        value = card.get("published_at")
        return value.timestamp() if isinstance(value, datetime) else float("-inf")

    ordered = sorted(
        scorecards,
        key=lambda card: (
            -_ROLE_PRIORITY[card["evidence_role"]],
            -card["relevance"],
            -_TYPE_PRIORITY[card["evidence_type"]],
            -card["source_quality"],
            -card["novelty"],
            -published_timestamp(card),
            card["candidate_id"],
        ),
    )
    return [
        card for card in ordered
        if card["relevance"] >= 2 and card["evidence_role"] not in {"out_of_scope", "uncertain"}
    ][:top_k]
