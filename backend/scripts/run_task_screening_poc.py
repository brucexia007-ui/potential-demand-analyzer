"""运行 Single 全量评分卡候选筛选 POC。

输入必须是完成身份归一化和全量人工标注的 ``task-screening-fixture/v5``。人工标签仅用于
离线指标，绝不会发送给模型。模型为全部候选输出紧凑评分卡，程序按固定规则
生成 TopK；运行器不写业务数据库。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import statistics
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Protocol


_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from scripts.export_task_screening_fixture import validate_screening_annotation


class ScreeningClient(Protocol):
    def infer(self, **kwargs: Any) -> Mapping[str, Any]: ...


_EVIDENCE_TYPE_PRIORITY = {
    "weak_or_irrelevant": 0,
    "industry_analog": 1,
    "target_adjacent": 2,
    "target_direct": 3,
}
_EVIDENCE_ROLE_PRIORITY = {
    "uncertain": 0,
    "out_of_scope": 0,
    "vendor_case_intelligence": 1,
    "industry_capability_intelligence": 2,
    "target_operation_signal": 3,
    "target_procurement": 4,
    "active_target_opportunity": 5,
}
_EVIDENCE_TYPE_BY_ROLE = {
    "active_target_opportunity": "target_direct",
    "target_procurement": "target_direct",
    "target_operation_signal": "target_adjacent",
    "industry_capability_intelligence": "industry_analog",
    "vendor_case_intelligence": "industry_analog",
    "out_of_scope": "weak_or_irrelevant",
    "uncertain": "weak_or_irrelevant",
}
_PROCUREMENT_LIFECYCLES = {
    "active",
    "closed_or_failed",
    "historical_or_unknown",
    "not_applicable",
}
_SUBJECT_RELATIONS = {
    "exact_target",
    "parent_entity",
    "other_branch_or_subsidiary",
    "external",
    "unknown",
}
_DEMAND_RELATIONS = {
    "core_customer_service",
    "adjacent_customer_operation",
    "unrelated",
    "uncertain",
}
_EVIDENCE_FORMS = {"procurement", "operation_signal", "vendor_case", "other"}
_LABEL_RELEVANCE = {
    "must_keep": 3,
    "relevant": 2,
    "acceptable_alternative": 1,
    "irrelevant": 0,
    "uncertain": 0,
}
_MINIMUM_ELIGIBLE_RELEVANCE = 2
_SUBSIDIARY_QUALIFIERS = (
    "分行",
    "支行",
    "分公司",
    "子公司",
    "产险",
    "财险",
    "寿险",
    "人寿",
    "养老险",
    "养老保险",
    "健康险",
    "健康保险",
    "资产管理",
)
_EXPLICIT_PROCUREMENT_TERMS = (
    "招标公告",
    "中标公告",
    "中标结果",
    "成交公告",
    "成交结果",
    "竞争性磋商",
    "询价公告",
    "比选公告",
    "单一来源",
    "采购公告",
    "征集公告",
    "废标",
    "流标",
)
_GENERIC_PROCUREMENT_TERMS = ("采购", "招标", "投标", "中标", "成交", "磋商", "询价", "比选")
_VENDOR_CASE_TERMS = ("客户案例", "成功案例", "案例实践", "案例")
_OPERATION_SIGNAL_TERMS = ("上线", "启用", "运营", "升级", "改造", "应用", "投产")
_CLOSED_PROCUREMENT_TERMS = ("中标", "成交", "终止", "废标", "流标", "失败")
_DEADLINE_TERMS = ("截止时间", "投标截止", "递交截止", "报名截止")
_DATE_EXPRESSION = (
    r"(?P<year>20\d{2})\s*(?:年|[-/.])\s*(?P<month>\d{1,2})\s*"
    r"(?:月|[-/.])\s*(?P<day>\d{1,2})\s*日?"
    r"(?:\s*(?P<hour>\d{1,2})\s*(?:时|:)(?P<minute>\d{1,2})?\s*分?)?"
)
_DEADLINE_AFTER_PATTERN = re.compile(
    rf"(?P<deadline_term>{'|'.join(map(re.escape, _DEADLINE_TERMS))})"
    rf"[^0-9]{{0,20}}(?P<date_text>{_DATE_EXPRESSION})"
)
_DEADLINE_BEFORE_PATTERN = re.compile(
    rf"(?P<date_text>{_DATE_EXPRESSION})[^\u4e00-\u9fff]{{0,12}}"
    rf"(?P<deadline_term>{'|'.join(map(re.escape, _DEADLINE_TERMS))})"
)
_CORE_DEMAND_TERMS = (
    "客服中心", "呼叫中心", "客户服务中心", "智能客服", "客服机器人", "话务",
    "坐席", "95500", "智能语音", "语音外呼", "电销", "客服录音",
)
_ADJACENT_DEMAND_TERMS = ("排班", "回访", "培训", "客服运营")
_UNRELATED_DEMAND_TERMS = ("反诈", "装修", "空调", "布线", "供应链", "福利", "媒体活动")


def _compact_candidate(candidate: Mapping[str, Any]) -> dict[str, Any]:
    """只保留允许发送给模型的字段，隔离人工标签和历史引用。"""
    return {
        "candidate_id": str(candidate.get("candidate_id", "")),
        "title": str(candidate.get("title", "")),
        "url": str(candidate.get("url", "")),
        "domain": str(candidate.get("domain", "")),
        "snippet": str(candidate.get("snippet", "")),
        "published_at": str(candidate.get("published_at", "")),
        "source": str(candidate.get("source", "")),
    }


def _compact_screening_context(context: Mapping[str, Any]) -> dict[str, Any]:
    allowed_fields = (
        "company_name",
        "demand_direction",
        "dimension",
        "industry",
        "region",
        "business_goal",
        "time_range",
        "goal",
        "target_entity_names",
        "target_parent_names",
        "target_scope_policy",
    )
    return {
        field: context[field] if isinstance(context[field], list) else str(context[field])
        for field in allowed_fields
        if context.get(field)
    }


def _alias_match(text: str, names: Iterable[str]) -> tuple[str, int] | None:
    matches = [
        (str(name).strip(), text.find(str(name).strip()))
        for name in names
        if str(name).strip() and str(name).strip() in text
    ]
    if not matches:
        return None
    return min(matches, key=lambda item: (-len(item[0]), item[1], item[0]))


def _has_non_target_qualifier(text: str, alias: str, start: int) -> str | None:
    suffix = text[start + len(alias):start + len(alias) + 14]
    for qualifier in _SUBSIDIARY_QUALIFIERS:
        if qualifier in suffix:
            return qualifier
    return None


def derive_subject_relation(
    candidate: Mapping[str, Any],
    target_entity_names: Iterable[str],
    target_parent_names: Iterable[str],
) -> tuple[str, dict[str, str]]:
    """按标题优先、摘要补充的确定性规则派生候选主体关系。"""
    target_names = tuple(str(name).strip() for name in target_entity_names if str(name).strip())
    parent_names = tuple(str(name).strip() for name in target_parent_names if str(name).strip())
    for location in ("title", "snippet"):
        text = str(candidate.get(location) or "").strip()
        if not text:
            continue
        target_match = _alias_match(text, target_names)
        if target_match:
            alias, start = target_match
            qualifier = _has_non_target_qualifier(text, alias, start)
            if qualifier:
                return "other_branch_or_subsidiary", {
                    "rule": "target_alias_extended_by_non_target_qualifier",
                    "matched_name": alias,
                    "matched_qualifier": qualifier,
                    "location": location,
                }
            return "exact_target", {
                "rule": "configured_target_name_match",
                "matched_name": alias,
                "matched_qualifier": "",
                "location": location,
            }
        parent_match = _alias_match(text, parent_names)
        if parent_match:
            alias, start = parent_match
            qualifier = _has_non_target_qualifier(text, alias, start)
            if qualifier:
                return "other_branch_or_subsidiary", {
                    "rule": "parent_alias_extended_by_branch_or_subsidiary",
                    "matched_name": alias,
                    "matched_qualifier": qualifier,
                    "location": location,
                }
            return "parent_entity", {
                "rule": "configured_parent_name_match",
                "matched_name": alias,
                "matched_qualifier": "",
                "location": location,
            }
    return "external", {
        "rule": "no_target_scope_anchor",
        "matched_name": "",
        "matched_qualifier": "",
        "location": "none",
    }


def derive_evidence_form(candidate: Mapping[str, Any]) -> tuple[str, dict[str, str]]:
    """根据候选原文中的稳定文书信号派生证据形态。"""
    title = str(candidate.get("title") or "").strip()
    snippet = str(candidate.get("snippet") or "").strip()
    for term in _EXPLICIT_PROCUREMENT_TERMS:
        if term in title:
            return "procurement", {"rule": "explicit_procurement_title_term", "matched_term": term}
    for term in _VENDOR_CASE_TERMS:
        if term in title:
            return "vendor_case", {"rule": "vendor_case_title_term", "matched_term": term}
    combined = f"{title}\n{snippet}"
    for term in _GENERIC_PROCUREMENT_TERMS:
        if term in combined:
            return "procurement", {"rule": "generic_procurement_term", "matched_term": term}
    for term in _OPERATION_SIGNAL_TERMS:
        if term in combined:
            return "operation_signal", {"rule": "operation_signal_term", "matched_term": term}
    return "other", {"rule": "no_form_signal", "matched_term": ""}


def _extract_deadline(candidate: Mapping[str, Any]) -> tuple[datetime | None, dict[str, str]]:
    china_timezone = timezone(timedelta(hours=8))
    for location in ("title", "snippet"):
        text = str(candidate.get(location) or "").strip()
        for pattern in (_DEADLINE_AFTER_PATTERN, _DEADLINE_BEFORE_PATTERN):
            match = pattern.search(text)
            if not match:
                continue
            try:
                hour_text = match.group("hour")
                minute_text = match.group("minute")
                deadline = datetime(
                    int(match.group("year")),
                    int(match.group("month")),
                    int(match.group("day")),
                    int(hour_text) if hour_text else 23,
                    int(minute_text) if minute_text else (0 if hour_text else 59),
                    0 if hour_text else 59,
                    tzinfo=china_timezone,
                )
            except ValueError:
                continue
            return deadline, {
                "rule": "explicit_year_deadline",
                "location": location,
                "matched_term": match.group("deadline_term"),
                "date_text": match.group("date_text"),
                "deadline_at": deadline.isoformat(),
            }
    return None, {
        "rule": "no_explicit_year_deadline",
        "location": "none",
        "matched_term": "",
        "date_text": "",
        "deadline_at": "",
    }


def derive_procurement_lifecycle(
    candidate: Mapping[str, Any],
    evidence_form: str,
    evaluated_at: datetime,
) -> tuple[str, dict[str, str]]:
    """仅依据明确状态词与带年份截止日期派生采购生命周期。"""
    if evidence_form != "procurement":
        return "not_applicable", {
            "rule": "non_procurement",
            "location": "none",
            "matched_term": "",
            "date_text": "",
            "deadline_at": "",
        }
    title = str(candidate.get("title") or "")
    for term in _CLOSED_PROCUREMENT_TERMS:
        if term in title:
            return "closed_or_failed", {
                "rule": "closed_title_term",
                "location": "title",
                "matched_term": term,
                "date_text": "",
                "deadline_at": "",
            }
    deadline, basis = _extract_deadline(candidate)
    comparison_time = evaluated_at
    if comparison_time.tzinfo is None:
        comparison_time = comparison_time.replace(tzinfo=timezone.utc)
    if deadline and deadline > comparison_time:
        return "active", basis
    if deadline:
        return "historical_or_unknown", {**basis, "rule": "explicit_deadline_expired"}
    return "historical_or_unknown", basis


def _demand_keyword_hints(candidate: Mapping[str, Any]) -> dict[str, list[str]]:
    text = f"{candidate.get('title') or ''}\n{candidate.get('snippet') or ''}"
    return {
        "core_hits": [term for term in _CORE_DEMAND_TERMS if term in text],
        "adjacent_hits": [term for term in _ADJACENT_DEMAND_TERMS if term in text],
        "unrelated_hits": [term for term in _UNRELATED_DEMAND_TERMS if term in text],
    }


def derive_relevance(
    subject_relation: str,
    demand_relation: str,
    evidence_form: str,
) -> int:
    if demand_relation == "unrelated":
        return 0
    if subject_relation == "unknown" or demand_relation == "uncertain":
        return 1
    if (
        subject_relation in {"exact_target", "parent_entity"}
        and demand_relation == "core_customer_service"
        and evidence_form == "procurement"
    ):
        return 4
    return 3


def _candidate_with_deterministic_hints(
    candidate: Mapping[str, Any],
    screening_context: Mapping[str, Any],
    evaluated_at: datetime,
) -> dict[str, Any]:
    compact = _compact_candidate(candidate)
    subject_relation, subject_basis = derive_subject_relation(
        candidate,
        screening_context.get("target_entity_names") or [],
        screening_context.get("target_parent_names") or [],
    )
    evidence_form, evidence_form_basis = derive_evidence_form(candidate)
    lifecycle, lifecycle_basis = derive_procurement_lifecycle(
        candidate,
        evidence_form,
        evaluated_at,
    )
    compact["deterministic_hints"] = {
        "subject_relation": subject_relation,
        "subject_relation_basis": subject_basis,
        "evidence_form": evidence_form,
        "evidence_form_basis": evidence_form_basis,
        "procurement_lifecycle": lifecycle,
        "lifecycle_basis": lifecycle_basis,
        "demand_keyword_hints": _demand_keyword_hints(candidate),
    }
    return compact


def build_screening_prompt(
    candidates: Iterable[Mapping[str, Any]],
    *,
    screening_context: Mapping[str, Any],
    evaluated_at: datetime | None = None,
) -> str:
    compact_context = _compact_screening_context(screening_context)
    common_evaluated_at = evaluated_at or datetime.now(timezone.utc)
    compact_candidates = [
        _candidate_with_deterministic_hints(candidate, compact_context, common_evaluated_at)
        for candidate in candidates
    ]
    return (
        "<instructions>\n"
        "你是企业研究候选分解评分器。候选位置不代表优先级，必须独立逐条判断全部候选，"
        "为每个 candidate_id 恰好返回一条评分。不要筛选、排序或输出解释、Markdown、代码块和思维链。\n"
        "</instructions>\n"
        "<research_context>\n"
        f"{json.dumps(compact_context, ensure_ascii=False, separators=(',', ':'))}\n"
        "</research_context>\n"
        "<classification_rules>\n"
        "demand_relation：客服中心、呼叫中心、客户服务中心、智能客服、客服机器人、话务、坐席、95500、"
        "智能语音、语音外呼、电销和客服录音系统为 core_customer_service；排班、回访、培训和客服运营为"
        " adjacent_customer_operation；反诈、装修、空调、布线、供应链、福利和媒体活动等非客服内容为 unrelated；"
        "信息不足为 uncertain。deterministic_hints 只提供程序抽取的非人工事实提示，仍须根据完整标题和摘要判断需求。"
        "source_quality和novelty为0到2整数。\n"
        "</classification_rules>\n"
        "<output_schema>\n"
        '{"scores":[{"candidate_id":"输入中的ID",'
        '"demand_relation":"core_customer_service","source_quality":2,"novelty":2}]}\n'
        "</output_schema>\n"
        "<candidates>\n"
        f"{json.dumps(compact_candidates, ensure_ascii=False, separators=(',', ':'))}\n"
        "</candidates>\n"
        "<final_output_contract>\n"
        "仅输出 JSON 对象；每个输入 ID 恰好一行。不得输出 subject_relation、evidence_form、"
        "procurement_lifecycle、active_until、relevance、evidence_role、evidence_type、reason_code 或其他字段。"
        "demand_relation 只能为 core_customer_service、adjacent_customer_operation、unrelated、uncertain；"
        "source_quality和novelty只能为0到2整数。\n"
        "</final_output_contract>"
    )


def derive_evidence_role(
    subject_relation: str,
    demand_relation: str,
    evidence_form: str,
    procurement_lifecycle: str,
) -> str:
    if demand_relation == "unrelated":
        return "out_of_scope"
    if subject_relation == "unknown" or demand_relation == "uncertain":
        return "uncertain"
    if evidence_form == "vendor_case":
        return "vendor_case_intelligence"
    if subject_relation in {"exact_target", "parent_entity"}:
        if evidence_form == "procurement":
            return (
                "active_target_opportunity"
                if procurement_lifecycle == "active"
                else "target_procurement"
            )
        return "target_operation_signal"
    return "industry_capability_intelligence"


def parse_screening_scorecards(
    content: str,
    candidates: Mapping[str, Mapping[str, Any]],
    *,
    screening_context: Mapping[str, Any],
    evaluated_at: datetime,
) -> list[dict[str, Any]]:
    """严格校验 v6 模型输出，并用确定性事实派生完整评分卡。"""
    payload = json.loads(content)
    if not isinstance(payload, Mapping) or not isinstance(payload.get("scores"), list):
        raise ValueError("响应必须是包含 scores 数组的 JSON 对象")
    if set(payload) != {"scores"}:
        raise ValueError("响应根对象包含未定义字段")

    scorecards: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    allowed_fields = {"candidate_id", "demand_relation", "source_quality", "novelty"}
    for item in payload["scores"]:
        if not isinstance(item, Mapping):
            raise ValueError("scores 必须全部为对象")
        unexpected_fields = set(item) - allowed_fields
        if unexpected_fields:
            raise ValueError(f"评分卡包含未定义字段：{','.join(sorted(unexpected_fields))}")
        candidate_id = item.get("candidate_id")
        if not isinstance(candidate_id, str) or candidate_id not in candidates:
            raise ValueError("响应包含未知 candidate_id")
        if candidate_id in seen_ids:
            raise ValueError("响应包含重复 candidate_id")

        numeric_ranges = {"source_quality": (0, 2), "novelty": (0, 2)}
        normalized: dict[str, Any] = {"candidate_id": candidate_id}
        for field, (minimum, maximum) in numeric_ranges.items():
            value = item.get(field)
            if type(value) is not int or not minimum <= value <= maximum:
                raise ValueError(f"候选 {candidate_id} 的 {field} 越界或类型非法")
            normalized[field] = value

        demand_relation = item.get("demand_relation")
        if demand_relation not in _DEMAND_RELATIONS:
            raise ValueError(f"候选 {candidate_id} 的 demand_relation 非法")
        candidate = candidates[candidate_id]
        subject_relation, subject_basis = derive_subject_relation(
            candidate,
            screening_context.get("target_entity_names") or [],
            screening_context.get("target_parent_names") or [],
        )
        evidence_form, evidence_form_basis = derive_evidence_form(candidate)
        procurement_lifecycle, lifecycle_basis = derive_procurement_lifecycle(
            candidate,
            evidence_form,
            evaluated_at,
        )
        relevance = derive_relevance(subject_relation, str(demand_relation), evidence_form)
        normalized["relevance"] = relevance

        evidence_role = derive_evidence_role(
            str(subject_relation),
            str(demand_relation),
            str(evidence_form),
            str(procurement_lifecycle),
        )
        evidence_type = _EVIDENCE_TYPE_BY_ROLE[evidence_role]
        normalized["evidence_type"] = evidence_type
        normalized["evidence_role"] = evidence_role
        normalized["subject_relation"] = subject_relation
        normalized["demand_relation"] = demand_relation
        normalized["evidence_form"] = evidence_form
        normalized["procurement_lifecycle"] = procurement_lifecycle
        normalized["subject_relation_basis"] = subject_basis
        normalized["evidence_form_basis"] = evidence_form_basis
        normalized["lifecycle_basis"] = lifecycle_basis
        if procurement_lifecycle == "active":
            normalized["active_until"] = lifecycle_basis["deadline_at"]
        normalized["reason_code"] = evidence_role.upper()
        scorecards.append(normalized)
        seen_ids.add(candidate_id)

    missing_ids = set(candidates) - seen_ids
    if missing_ids:
        raise ValueError(f"响应缺少 candidate_id：{','.join(sorted(missing_ids))}")
    return sorted(scorecards, key=lambda item: item["candidate_id"])


def _published_timestamp(value: object) -> float:
    text = str(value or "").strip()
    if not text:
        return float("-inf")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return float("-inf")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def rank_scorecards(
    scorecards: Iterable[Mapping[str, Any]],
    candidates: Mapping[str, Mapping[str, Any]],
    top_k: int,
    *,
    minimum_eligible_relevance: int = _MINIMUM_ELIGIBLE_RELEVANCE,
) -> list[str]:
    """按固定优先级生成可复现的 Top≤K，不用低质量候选硬填满。"""
    if top_k < 1:
        raise ValueError("top_k 必须大于 0")
    if not 0 <= minimum_eligible_relevance <= 4:
        raise ValueError("minimum_eligible_relevance 必须介于 0 到 4")

    def ranking_key(scorecard: Mapping[str, Any]) -> tuple[Any, ...]:
        candidate_id = str(scorecard["candidate_id"])
        candidate = candidates.get(candidate_id)
        if candidate is None:
            raise ValueError(f"评分卡引用未知候选：{candidate_id}")
        return (
            -_EVIDENCE_ROLE_PRIORITY[str(scorecard["evidence_role"])],
            -int(scorecard["relevance"]),
            -_EVIDENCE_TYPE_PRIORITY[str(scorecard["evidence_type"])],
            -int(scorecard["source_quality"]),
            -int(scorecard["novelty"]),
            -_published_timestamp(candidate.get("published_at")),
            candidate_id,
        )

    eligible = [
        scorecard
        for scorecard in scorecards
        if int(scorecard["relevance"]) >= minimum_eligible_relevance
        and scorecard["evidence_type"] != "weak_or_irrelevant"
        and scorecard["evidence_role"] not in {"out_of_scope", "uncertain"}
    ]
    ordered = sorted(eligible, key=ranking_key)
    selected_ids: list[str] = []
    seen_urls: set[str] = set()
    for scorecard in ordered:
        candidate_id = str(scorecard["candidate_id"])
        url = str(candidates[candidate_id].get("url") or "").strip().lower()
        if url and url in seen_urls:
            continue
        if url:
            seen_urls.add(url)
        selected_ids.append(candidate_id)
        if len(selected_ids) == top_k:
            break
    return selected_ids


def recommended_max_output_tokens(candidate_count: int) -> int:
    """为全量评分卡保留随候选数线性增长的物理输出空间，不作为预算拦截。"""
    if candidate_count < 1:
        raise ValueError("candidate_count 必须大于 0")
    return max(4000, candidate_count * 120)


def recommended_call_timeout_seconds(candidate_count: int) -> float:
    """按模型实际接收的代表候选数给出单次调用硬超时。"""
    if candidate_count < 0:
        raise ValueError("candidate_count 不能小于 0")
    if candidate_count <= 60:
        return 60.0
    if candidate_count <= 100:
        return 90.0
    return 120.0


def _percentile(values: list[float], percentile: int) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(len(ordered) * percentile / 100) - 1))
    return round(ordered[index], 6)


def recall_at_k(selected_ids: list[str], expected_ids: set[str], top_k: int) -> float | None:
    if not expected_ids:
        return None
    return round(len(set(selected_ids[:top_k]) & expected_ids) / len(expected_ids), 6)


def evidence_group_recall_at_k(
    selected_ids: list[str],
    evidence_groups: Mapping[str, set[str]],
    top_k: int,
) -> float | None:
    if not evidence_groups:
        return None
    selected = set(selected_ids[:top_k])
    hits = sum(bool(selected & group_ids) for group_ids in evidence_groups.values())
    return round(hits / len(evidence_groups), 6)


def research_role_precision_at_k(
    selected_ids: list[str],
    scorecards: Iterable[Mapping[str, Any]],
    expected_roles: Mapping[str, str],
    top_k: int,
) -> float | None:
    selected = selected_ids[:top_k]
    if not selected:
        return None
    predicted_roles = {
        str(scorecard["candidate_id"]): str(scorecard["evidence_role"])
        for scorecard in scorecards
    }
    return round(
        sum(predicted_roles.get(candidate_id) == expected_roles.get(candidate_id) for candidate_id in selected)
        / len(selected),
        6,
    )


def _role_evidence_groups(
    evidence_groups: Mapping[str, set[str]],
    role_by_id: Mapping[str, str],
    role: str,
) -> dict[str, set[str]]:
    return {
        group: set(candidate_ids)
        for group, candidate_ids in evidence_groups.items()
        if any(role_by_id.get(candidate_id) == role for candidate_id in candidate_ids)
    }


def judged_precision_at_k(
    selected_ids: list[str],
    positive_ids: set[str],
    irrelevant_ids: set[str],
    top_k: int,
) -> float | None:
    selected = set(selected_ids[:top_k])
    judged_selected = selected & (positive_ids | irrelevant_ids)
    if not judged_selected:
        return None
    return round(len(judged_selected & positive_ids) / len(judged_selected), 6)


def ndcg_at_k(selected_ids: list[str], relevance: Mapping[str, int], top_k: int) -> float | None:
    if not relevance or not any(relevance.values()):
        return None
    dcg = sum(
        (2 ** relevance.get(candidate_id, 0) - 1) / math.log2(index + 2)
        for index, candidate_id in enumerate(selected_ids[:top_k])
    )
    ideal_scores = sorted(relevance.values(), reverse=True)[:top_k]
    idcg = sum((2**score - 1) / math.log2(index + 2) for index, score in enumerate(ideal_scores))
    return round(dcg / idcg, 6) if idcg else None


def jaccard(left: list[str], right: list[str], top_k: int) -> float:
    left_set, right_set = set(left[:top_k]), set(right[:top_k])
    return round(len(left_set & right_set) / len(left_set | right_set), 6) if left_set or right_set else 1.0


def selected_set_overlap(left: list[str], right: list[str], top_k: int) -> float:
    """计算两个 Top≤K 集合重合率，分母取两侧较大的实际返回数。"""
    if top_k < 1:
        raise ValueError("top_k 必须大于 0")
    left_set, right_set = set(left[:top_k]), set(right[:top_k])
    denominator = max(len(left_set), len(right_set))
    if denominator == 0:
        return 1.0
    return round(len(left_set & right_set) / denominator, 6)


def build_role_diagnostics(
    scorecard_views: Iterable[Iterable[Mapping[str, Any]]],
    expected_roles: Mapping[str, str],
) -> dict[str, Any]:
    """汇总全候选角色混淆矩阵、分类指标与位置视图一致性。"""
    role_maps = [
        {
            str(scorecard["candidate_id"]): str(scorecard["evidence_role"])
            for scorecard in scorecards
        }
        for scorecards in scorecard_views
    ]
    roles = sorted(set(_EVIDENCE_ROLE_PRIORITY) | set(expected_roles.values()))
    confusion_matrix = {
        expected: {predicted: 0 for predicted in roles}
        for expected in roles
    }
    correct = 0
    total = 0
    for predicted_by_id in role_maps:
        for candidate_id, predicted in predicted_by_id.items():
            expected = expected_roles.get(candidate_id)
            if expected is None:
                continue
            confusion_matrix[expected][predicted] += 1
            total += 1
            correct += predicted == expected

    per_role: dict[str, dict[str, int | float | None]] = {}
    for role in roles:
        true_positive = confusion_matrix[role][role]
        expected_count = sum(confusion_matrix[role].values())
        predicted_count = sum(confusion_matrix[expected][role] for expected in roles)
        precision = true_positive / predicted_count if predicted_count else None
        recall = true_positive / expected_count if expected_count else None
        f1 = (
            2 * precision * recall / (precision + recall)
            if precision is not None and recall is not None and precision + recall
            else None
        )
        per_role[role] = {
            "precision": round(precision, 6) if precision is not None else None,
            "recall": round(recall, 6) if recall is not None else None,
            "f1": round(f1, 6) if f1 is not None else None,
            "support": expected_count,
            "predicted_count": predicted_count,
        }

    inconsistent_ids: list[str] = []
    consistent_count = 0
    for candidate_id in sorted(expected_roles):
        predicted_roles = [role_map.get(candidate_id) for role_map in role_maps]
        is_consistent = (
            bool(role_maps)
            and all(role is not None for role in predicted_roles)
            and len(set(predicted_roles)) == 1
        )
        if is_consistent:
            consistent_count += 1
        else:
            inconsistent_ids.append(candidate_id)

    return {
        "role_confusion_matrix": confusion_matrix,
        "role_metrics": per_role,
        "role_accuracy_all_candidates": round(correct / total, 6) if total else None,
        "role_evaluation_count": total,
        "position_role_consistency_rate": (
            round(consistent_count / len(expected_roles), 6) if expected_roles else None
        ),
        "position_role_inconsistent_candidate_ids": inconsistent_ids,
    }


def build_position_views(candidates: list[Mapping[str, Any]]) -> list[tuple[str, list[Mapping[str, Any]]]]:
    """生成三个确定性旋转视图，使同一候选分别落在前、中、后位置。"""
    if not candidates:
        return [("base", [])]
    offsets = (0, len(candidates) // 3, (2 * len(candidates)) // 3)
    return [
        (f"rotation_{offset}", candidates[offset:] + candidates[:offset])
        for offset in dict.fromkeys(offsets)
    ]


def _usage_from_response(response: Mapping[str, Any]) -> dict[str, int]:
    raw_usage = response.get("usage") or {}
    return {
        key: max(0, int(raw_usage.get(key, 0) or 0))
        for key in ("input_tokens", "output_tokens", "total_tokens")
    }


def _invoke(
    client: ScreeningClient,
    candidates: list[Mapping[str, Any]],
    *,
    top_k: int,
    max_output_tokens: int,
    output_token_warning_threshold: int,
    model: str | None,
    call_timeout_seconds: float,
    max_retries: int,
    thinking_mode: str,
    screening_context: Mapping[str, Any],
    minimum_eligible_relevance: int,
    evaluated_at: datetime,
) -> dict[str, Any]:
    started_at = time.perf_counter()
    response: Mapping[str, Any] | None = None
    response_content = ""
    try:
        response = client.infer(
            prompt=build_screening_prompt(
                candidates,
                screening_context=screening_context,
                evaluated_at=evaluated_at,
            ),
            system_prompt="只按要求返回 JSON 对象，不输出任何额外文本。",
            response_format={"type": "json_object"},
            temperature=0,
            max_tokens=max_output_tokens,
            model=model,
            timeout_seconds=call_timeout_seconds,
            max_retries=max_retries,
            thinking_mode=thinking_mode,
        )
        response_content = str(response.get("content", ""))
        candidate_by_id = {str(candidate["candidate_id"]): candidate for candidate in candidates}
        scorecards = parse_screening_scorecards(
            response_content,
            candidate_by_id,
            screening_context=screening_context,
            evaluated_at=evaluated_at,
        )
        selected_ids = rank_scorecards(
            scorecards,
            candidate_by_id,
            top_k,
            minimum_eligible_relevance=minimum_eligible_relevance,
        )
        usage = _usage_from_response(response)
        return {
            "schema_success": True,
            "scorecards": scorecards,
            "selected_ids": selected_ids,
            "latency_seconds": time.perf_counter() - started_at,
            "usage": usage,
            "token_warning": usage["output_tokens"] > output_token_warning_threshold,
            "model": response.get("model"),
            "provider": response.get("provider"),
            "finish_reason": response.get("finish_reason"),
            "response_content_length": len(response_content),
            "raw_response_content": response_content,
            "error": None,
        }
    except Exception as error:
        failed_response = response or {}
        usage = _usage_from_response(failed_response)
        return {
            "schema_success": False,
            "scorecards": [],
            "selected_ids": [],
            "latency_seconds": time.perf_counter() - started_at,
            "usage": usage,
            "token_warning": usage["output_tokens"] > output_token_warning_threshold,
            "model": failed_response.get("model"),
            "provider": failed_response.get("provider"),
            "finish_reason": failed_response.get("finish_reason"),
            "response_content_length": len(response_content),
            "raw_response_content": response_content,
            "error": f"{type(error).__name__}: {error}",
        }


def _aggregate_invocations(
    invocations: list[dict[str, Any]],
    input_price: float | None,
    output_price: float | None,
    output_token_warning_threshold: int,
) -> dict[str, Any]:
    usage = {
        key: sum(invocation["usage"][key] for invocation in invocations)
        for key in ("input_tokens", "output_tokens", "total_tokens")
    }
    cost = None
    if input_price is not None and output_price is not None:
        cost = round(
            usage["input_tokens"] * input_price / 1_000_000
            + usage["output_tokens"] * output_price / 1_000_000,
            8,
        )
    finish_reason_counts = {
        finish_reason: sum(invocation.get("finish_reason") == finish_reason for invocation in invocations)
        for finish_reason in sorted({
            str(invocation["finish_reason"])
            for invocation in invocations
            if invocation.get("finish_reason")
        })
    }
    return {
        "call_count": len(invocations),
        "models": sorted({str(item["model"]) for item in invocations if item["model"]}),
        "providers": sorted({str(item["provider"]) for item in invocations if item["provider"]}),
        "schema_success_rate": round(
            sum(item["schema_success"] for item in invocations) / len(invocations), 6
        ) if invocations else 0.0,
        "p90_latency_seconds": _percentile([item["latency_seconds"] for item in invocations], 90),
        "finish_reason_counts": finish_reason_counts,
        "token_usage": usage,
        "output_token_warning_threshold": output_token_warning_threshold,
        "output_token_warning_count": sum(item["token_warning"] for item in invocations),
        "token_budget_status": (
            "warning_exceeded" if any(item["token_warning"] for item in invocations)
            else "within_warning_threshold"
        ),
        "cost": cost,
        "cost_status": "estimated" if cost is not None else "unknown",
    }


def evaluate_quality_gates(
    aggregate: Mapping[str, Any],
    *,
    has_active_target_opportunity: bool,
    has_priority_target: bool,
    has_target_procurement_groups: bool,
) -> dict[str, Any]:
    """先评估原质量门，再评估仅授权开发和影子运行的临时门。"""
    outcomes = {
        "active_target_opportunity_recall": aggregate.get(
            "average_active_target_opportunity_recall_at_k"
        ),
        "priority_target_recall": aggregate.get("average_priority_target_recall_at_k"),
        "target_procurement_group_recall": aggregate.get(
            "average_target_procurement_group_recall_at_k"
        ),
        "minimum_research_role_precision": aggregate.get(
            "minimum_research_role_precision_at_k"
        ),
        "minimum_selected_set_overlap": aggregate.get("min_selected_set_overlap"),
        "schema_success_rate": aggregate.get("schema_success_rate"),
    }

    def build_gate(
        *,
        target_procurement_group_threshold: float,
        role_precision_threshold: float,
        selected_set_overlap_threshold: float,
    ) -> dict[str, Any]:
        thresholds = {
            "active_target_opportunity_recall": 1.0,
            "priority_target_recall": 1.0,
            "target_procurement_group_recall": target_procurement_group_threshold,
            "minimum_research_role_precision": role_precision_threshold,
            "minimum_selected_set_overlap": selected_set_overlap_threshold,
            "schema_success_rate": 0.99,
        }
        checks = {
            "active_target_opportunity_all_views": (
                not has_active_target_opportunity
                or outcomes["active_target_opportunity_recall"] == 1.0
            ),
            "priority_target_all_views": (
                has_priority_target and outcomes["priority_target_recall"] == 1.0
            ),
            "target_procurement_group_recall": (
                not has_target_procurement_groups
                or (
                    outcomes["target_procurement_group_recall"] is not None
                    and outcomes["target_procurement_group_recall"]
                    >= target_procurement_group_threshold
                )
            ),
            "research_role_precision": (
                outcomes["minimum_research_role_precision"] is not None
                and outcomes["minimum_research_role_precision"] >= role_precision_threshold
            ),
            "selected_set_overlap": (
                outcomes["minimum_selected_set_overlap"] is not None
                and outcomes["minimum_selected_set_overlap"] >= selected_set_overlap_threshold
            ),
            "schema_success": (
                outcomes["schema_success_rate"] is not None
                and outcomes["schema_success_rate"] >= 0.99
            ),
        }
        return {
            "thresholds": thresholds,
            "outcomes": dict(outcomes),
            "checks": checks,
            "passed": all(checks.values()),
        }

    original = build_gate(
        target_procurement_group_threshold=0.9,
        role_precision_threshold=0.8,
        selected_set_overlap_threshold=0.85,
    )
    provisional = build_gate(
        target_procurement_group_threshold=0.8,
        role_precision_threshold=0.7,
        selected_set_overlap_threshold=0.7,
    )
    provisional["authorization_scope"] = "development_and_shadow_only"
    provisional["production_default_enabled"] = False
    if original["passed"]:
        decision = "ORIGINAL_PASS"
    elif provisional["passed"]:
        decision = "PROVISIONAL_SHADOW_PASS"
    else:
        decision = "FAIL"
    return {
        "original": original,
        "provisional": provisional,
        "decision": decision,
        "provisional_exit_conditions": {
            "annotated_sample_count": 10,
            "shadow_task_count": 50,
            "maximum_days": 30,
            "production_requires_original_gate": True,
        },
    }


def run_poc(
    fixture: Mapping[str, Any],
    client: ScreeningClient,
    *,
    top_k: int = 20,
    max_output_tokens: int | None = None,
    output_token_warning_threshold: int = 4000,
    model: str | None = None,
    call_timeout_seconds: float | None = None,
    max_retries: int = 0,
    thinking_mode: str = "disabled",
    minimum_eligible_relevance: int = _MINIMUM_ELIGIBLE_RELEVANCE,
    input_price_per_million: float | None = None,
    output_price_per_million: float | None = None,
    progress_callback: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """运行三个位置视图的 Single 全量评分卡 POC。"""
    if top_k < 1 or output_token_warning_threshold < 1:
        raise ValueError("top_k 与 output_token_warning_threshold 必须大于 0")
    if max_output_tokens is not None and max_output_tokens < 1:
        raise ValueError("max_output_tokens 必须大于 0")
    if call_timeout_seconds is not None and call_timeout_seconds <= 0:
        raise ValueError("call_timeout_seconds 必须大于 0")
    if max_retries < 0:
        raise ValueError("max_retries 不能小于 0")
    if thinking_mode not in {"enabled", "disabled"}:
        raise ValueError("thinking_mode 必须为 enabled 或 disabled")
    if (input_price_per_million is None) != (output_price_per_million is None):
        raise ValueError("输入与输出单价必须同时提供，或都不提供")

    annotation = validate_screening_annotation(fixture)
    raw_context = fixture.get("screening_context")
    if not isinstance(raw_context, Mapping):
        raise ValueError("Fixture 不包含 screening_context")
    screening_context = _compact_screening_context(raw_context)
    screening_context.update({
        "target_entity_names": list(fixture["target_entity_names"]),
        "target_parent_names": list(fixture["target_parent_names"]),
        "target_scope_policy": fixture["target_scope_policy"],
    })
    if not screening_context.get("goal"):
        raise ValueError("Fixture screening_context.goal 不能为空")

    candidates = list(fixture["candidates"])
    evaluated_at = datetime.now(timezone.utc)
    effective_max_output_tokens = max_output_tokens or recommended_max_output_tokens(len(candidates))
    effective_call_timeout_seconds = (
        call_timeout_seconds
        if call_timeout_seconds is not None
        else recommended_call_timeout_seconds(len(candidates))
    )
    labels_by_id = {
        str(candidate["candidate_id"]): str(candidate["business_label"])
        for candidate in candidates
    }
    relevance = {
        candidate_id: _LABEL_RELEVANCE[label]
        for candidate_id, label in labels_by_id.items()
    }
    role_by_id = {
        str(candidate["candidate_id"]): str(candidate["evidence_role"])
        for candidate in candidates
    }
    active_target_ids = set(annotation["active_target_opportunity_ids"])
    priority_target_ids = {
        candidate_id
        for candidate_id in annotation["must_keep_ids"]
        if role_by_id[candidate_id] in {
            "active_target_opportunity",
            "target_procurement",
            "target_operation_signal",
        }
    }
    target_procurement_groups = _role_evidence_groups(
        annotation["evidence_groups"], role_by_id, "target_procurement"
    )

    views: list[dict[str, Any]] = []
    invocations: list[dict[str, Any]] = []
    for view_name, view_candidates in build_position_views(candidates):
        if progress_callback:
            progress_callback(f"strategy=single view={view_name} phase=scoring batch=1/1 start")
        invocation = _invoke(
            client,
            view_candidates,
            top_k=top_k,
            max_output_tokens=effective_max_output_tokens,
            output_token_warning_threshold=output_token_warning_threshold,
            model=model,
            call_timeout_seconds=effective_call_timeout_seconds,
            max_retries=max_retries,
            thinking_mode=thinking_mode,
            screening_context=screening_context,
            minimum_eligible_relevance=minimum_eligible_relevance,
            evaluated_at=evaluated_at,
        )
        invocations.append(invocation)
        completion = (
            f"strategy=single view={view_name} phase=scoring batch=1/1 done "
            f"schema_success={invocation['schema_success']}"
        )
        if not invocation["schema_success"]:
            completion += (
                f" error={str(invocation['error']).split(':', 1)[0]}"
                f" finish_reason={invocation.get('finish_reason') or 'unknown'}"
                f" output_tokens={invocation['usage']['output_tokens']}"
                f" response_chars={invocation['response_content_length']}"
            )
        if invocation["token_warning"]:
            completion += (
                " warning=output_token_soft_threshold_exceeded"
                f" threshold={output_token_warning_threshold}"
                f" output_tokens={invocation['usage']['output_tokens']}"
            )
        if progress_callback:
            progress_callback(completion)

        selected_ids = invocation["selected_ids"]
        views.append({
            "view": view_name,
            "top_k_ids": selected_ids,
            "scorecards": invocation["scorecards"],
            "scorecard_count": len(invocation["scorecards"]),
            "selected_count": len(selected_ids),
            "invocation_audit": {
                "latency_seconds": round(invocation["latency_seconds"], 6),
                "usage": invocation["usage"],
                "token_warning": invocation["token_warning"],
                "model": invocation["model"],
                "provider": invocation["provider"],
                "finish_reason": invocation["finish_reason"],
                "response_content_length": invocation["response_content_length"],
                "raw_response_content": invocation["raw_response_content"],
                "error": invocation["error"],
            },
            "must_keep_recall_at_k": recall_at_k(
                selected_ids, annotation["must_keep_ids"], top_k
            ),
            "active_target_opportunity_recall_at_k": recall_at_k(
                selected_ids, active_target_ids, top_k
            ),
            "priority_target_recall_at_k": recall_at_k(
                selected_ids, priority_target_ids, top_k
            ),
            "target_procurement_group_recall_at_k": evidence_group_recall_at_k(
                selected_ids, target_procurement_groups, top_k
            ),
            "evidence_group_recall_at_k": evidence_group_recall_at_k(
                selected_ids, annotation["evidence_groups"], top_k
            ),
            "judged_precision_at_k": judged_precision_at_k(
                selected_ids,
                annotation["positive_ids"],
                annotation["irrelevant_ids"],
                top_k,
            ),
            "research_role_precision_at_k": research_role_precision_at_k(
                selected_ids, invocation["scorecards"], role_by_id, top_k
            ),
            "ndcg_at_k": ndcg_at_k(selected_ids, relevance, top_k),
            "schema_success": invocation["schema_success"],
            "failure_count": 0 if invocation["schema_success"] else 1,
            "errors": [] if invocation["schema_success"] else [
                str(invocation["error"]).split(":", 1)[0]
            ],
        })

    pair_indices = [
        (left, right)
        for left in range(len(views))
        for right in range(left + 1, len(views))
    ]
    selected_set_overlaps = [
        selected_set_overlap(views[left]["top_k_ids"], views[right]["top_k_ids"], top_k)
        for left, right in pair_indices
    ]
    jaccards = [
        jaccard(views[left]["top_k_ids"], views[right]["top_k_ids"], top_k)
        for left, right in pair_indices
    ]
    position_pair_diagnostics = [
        {
            "left_view": views[left]["view"],
            "right_view": views[right]["view"],
            "selected_set_overlap": selected_set_overlaps[index],
            "jaccard": jaccards[index],
        }
        for index, (left, right) in enumerate(pair_indices)
    ]
    aggregate = _aggregate_invocations(
        invocations,
        input_price_per_million,
        output_price_per_million,
        output_token_warning_threshold,
    )
    role_diagnostics = build_role_diagnostics(
        (view["scorecards"] for view in views),
        role_by_id,
    )
    derivation_rule_counts: dict[str, dict[str, int]] = {
        "subject_relation": {},
        "evidence_form": {},
        "procurement_lifecycle": {},
    }
    for view in views:
        for scorecard in view["scorecards"]:
            for metric_name, basis_name in (
                ("subject_relation", "subject_relation_basis"),
                ("evidence_form", "evidence_form_basis"),
                ("procurement_lifecycle", "lifecycle_basis"),
            ):
                rule = str(scorecard[basis_name]["rule"])
                counts = derivation_rule_counts[metric_name]
                counts[rule] = counts.get(rule, 0) + 1
    must_keep_values = [view["must_keep_recall_at_k"] for view in views]
    active_target_values = [
        view["active_target_opportunity_recall_at_k"] for view in views
    ]
    priority_target_values = [view["priority_target_recall_at_k"] for view in views]
    target_procurement_group_values = [
        view["target_procurement_group_recall_at_k"] for view in views
    ]
    group_values = [view["evidence_group_recall_at_k"] for view in views]
    precision_values = [view["judged_precision_at_k"] for view in views]
    role_precision_values = [view["research_role_precision_at_k"] for view in views]
    aggregate.update({
        "strategy": "single_scorecard",
        "views": views,
        "average_must_keep_recall_at_k": round(statistics.mean(
            value for value in must_keep_values if value is not None
        ), 6) if any(value is not None for value in must_keep_values) else None,
        "average_active_target_opportunity_recall_at_k": round(statistics.mean(
            value for value in active_target_values if value is not None
        ), 6) if any(value is not None for value in active_target_values) else None,
        "average_priority_target_recall_at_k": round(statistics.mean(
            value for value in priority_target_values if value is not None
        ), 6) if any(value is not None for value in priority_target_values) else None,
        "average_target_procurement_group_recall_at_k": round(statistics.mean(
            value for value in target_procurement_group_values if value is not None
        ), 6) if any(value is not None for value in target_procurement_group_values) else None,
        "average_evidence_group_recall_at_k": round(statistics.mean(
            value for value in group_values if value is not None
        ), 6) if any(value is not None for value in group_values) else None,
        "minimum_judged_precision_at_k": min(
            value for value in precision_values if value is not None
        ) if any(value is not None for value in precision_values) else None,
        "minimum_research_role_precision_at_k": min(
            value for value in role_precision_values if value is not None
        ) if any(value is not None for value in role_precision_values) else None,
        "average_ndcg_at_k": round(statistics.mean(
            view["ndcg_at_k"] for view in views if view["ndcg_at_k"] is not None
        ), 6) if any(view["ndcg_at_k"] is not None for view in views) else None,
        "min_selected_set_overlap": (
            min(selected_set_overlaps) if selected_set_overlaps else 1.0
        ),
        "min_selected_set_jaccard": min(jaccards) if jaccards else 1.0,
        "position_pair_diagnostics": position_pair_diagnostics,
        "deterministic_derivation_rule_counts": derivation_rule_counts,
        **role_diagnostics,
    })
    aggregate["cost_complete"] = aggregate["cost_status"] == "estimated"
    gate_evaluation = evaluate_quality_gates(
        aggregate,
        has_active_target_opportunity=bool(active_target_ids),
        has_priority_target=bool(priority_target_ids),
        has_target_procurement_groups=bool(target_procurement_groups),
    )
    aggregate["gate_evaluation"] = gate_evaluation

    context_digest = hashlib.sha256(json.dumps(
        screening_context,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    return {
        "schema_version": "task-screening-poc/v6",
        "fixture_task_ref": fixture.get("task_ref"),
        "fixture_candidate_source": fixture.get("candidate_source"),
        "original_candidate_count": int(fixture["original_candidate_count"]),
        "representative_candidate_count": len(candidates),
        "identity_cluster_count": len(fixture["candidate_identity_clusters"]),
        "candidate_count": len(candidates),
        "top_k": top_k,
        "max_output_tokens": effective_max_output_tokens,
        "output_token_warning_threshold": output_token_warning_threshold,
        "token_policy": "quality_first_soft_warning/v1",
        "requested_model": model,
        "call_timeout_seconds": effective_call_timeout_seconds,
        "call_timeout_policy": (
            "explicit_cli_override"
            if call_timeout_seconds is not None
            else "dynamic_by_representative_candidate_count/v1"
        ),
        "max_retries": max_retries,
        "thinking_mode": thinking_mode,
        "evaluated_at": evaluated_at.isoformat(),
        "screening_context_sha256": context_digest,
        "screening_protocol": "deterministic_facts_demand_scorecard_top_le_k/v6",
        "prompt_profile": "demand_only_with_deterministic_hints/v1",
        "selection_policy": {
            "max_selected_count": top_k,
            "minimum_eligible_relevance": minimum_eligible_relevance,
            "exclude_evidence_types": ["weak_or_irrelevant"],
            "exclude_evidence_roles": ["out_of_scope", "uncertain"],
            "ordering": [
                "evidence_role_priority_desc",
                "relevance_desc",
                "evidence_type_priority_desc",
                "source_quality_desc",
                "novelty_desc",
                "published_at_desc",
                "candidate_id_asc",
            ],
        },
        "annotation_policy": "fixture_v5_identity_clusters/v1; is_gold_reference仅作来源审计",
        "annotation_summary": {
            "must_keep_count": len(annotation["must_keep_ids"]),
            "evidence_group_count": len(annotation["evidence_groups"]),
            "positive_count": len(annotation["positive_ids"]),
            "irrelevant_count": len(annotation["irrelevant_ids"]),
            "uncertain_count": annotation["uncertain_count"],
            "active_target_opportunity_count": len(active_target_ids),
            "priority_target_count": len(priority_target_ids),
            "target_procurement_group_count": len(target_procurement_groups),
        },
        "identity_cluster_audit": [
            {
                "identity_key": cluster["identity_key"],
                "representative_id": cluster["representative_id"],
                "alias_count": len(cluster["member_ids"]) - 1,
                "member_count": len(cluster["member_ids"]),
                "match_basis": list(cluster["match_basis"]),
            }
            for cluster in fixture["candidate_identity_clusters"]
        ],
        "strategies": {"single_scorecard": aggregate},
        "gate_evaluation": gate_evaluation,
        "decision": gate_evaluation["decision"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="运行候选筛选 Single 全量评分卡 POC")
    parser.add_argument("fixture", type=Path, help="已完成身份归一化与全量标注的 Fixture v5 JSON")
    parser.add_argument("--output", type=Path, required=True, help="POC 结果 JSON")
    parser.add_argument("--top-k", type=int, default=20)
    parser.add_argument("--max-output-tokens", type=int, help="Provider 单次输出物理上限；默认按候选数计算")
    parser.add_argument("--output-token-warning-threshold", type=int, default=4000, help="仅告警，不中断、不影响 G1")
    parser.add_argument("--model", help="显式指定已获授权的模型；默认使用运行时默认模型")
    parser.add_argument(
        "--call-timeout-seconds",
        type=float,
        help="显式覆盖动态硬超时；默认按代表候选数使用 60/90/120 秒",
    )
    parser.add_argument("--max-retries", type=int, default=0)
    parser.add_argument("--thinking-mode", choices=("enabled", "disabled"), default="disabled")
    parser.add_argument("--minimum-eligible-relevance", type=int, default=_MINIMUM_ELIGIBLE_RELEVANCE)
    parser.add_argument("--input-price-per-million", type=float)
    parser.add_argument("--output-price-per-million", type=float)
    args = parser.parse_args()
    if args.top_k < 1 or args.output_token_warning_threshold < 1:
        parser.error("top-k 与 output-token-warning-threshold 必须大于 0")
    if args.max_output_tokens is not None and args.max_output_tokens < 1:
        parser.error("max-output-tokens 必须大于 0")
    if (
        args.call_timeout_seconds is not None
        and args.call_timeout_seconds <= 0
    ) or args.max_retries < 0:
        parser.error("call-timeout-seconds 必须大于 0，max-retries 不能小于 0")
    if (args.input_price_per_million is None) != (args.output_price_per_million is None):
        parser.error("输入与输出单价必须同时提供，或都不提供")

    from app.llm.gateway_client import get_gateway_client

    fixture = json.loads(args.fixture.read_text(encoding="utf-8"))
    result = run_poc(
        fixture,
        get_gateway_client(),
        top_k=args.top_k,
        max_output_tokens=args.max_output_tokens,
        output_token_warning_threshold=args.output_token_warning_threshold,
        model=args.model,
        call_timeout_seconds=args.call_timeout_seconds,
        max_retries=args.max_retries,
        thinking_mode=args.thinking_mode,
        minimum_eligible_relevance=args.minimum_eligible_relevance,
        input_price_per_million=args.input_price_per_million,
        output_price_per_million=args.output_price_per_million,
        progress_callback=lambda message: print(message, flush=True),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"POC 完成：{args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
