"""为招投标候选 Fixture v4 生成待人工复核的第一轮业务标注。

该工具只处理 ``bidding_information``，不会读取历史引用标签来推断质量，
也不会把初标稿标记为 ``completed``。输出必须经业务专家二次复核后，才能
由 POC 运行器读取。
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Mapping


_CORE_TERMS = (
    "智能客服", "客服系统", "客服中心", "客户服务中心", "呼叫中心",
    "话务平台", "客服机器人", "对话机器人", "智能语音", "语音外呼",
    "智能外呼", "在线客服", "视频客服", "e电话", "电话销售系统",
    "电销录音", "坐席", "智问平台", "客户服务软件",
)
_ADJACENT_TERMS = (
    "电话系统", "ip电话", "语音系统", "智能排班", "电话回访",
    "投诉中心", "问答对话", "客户行为模拟", "智能推荐",
)
_PROCUREMENT_TERMS = (
    "采购", "招标", "中标", "成交", "征集", "磋商", "竞价", "比选",
    "候选人", "供应商", "项目公告", "更正公告", "招募", "流标", "废标",
)
_UNRELATED_TERMS = (
    "慰问品", "装修", "空调", "消防", "印刷", "广告宣传", "媒体资源",
    "媒体系列", "培训服务", "办公用房", "门禁", "安防", "标识安装",
    "食堂", "物业", "会议系统", "商务活动", "营销活动", "权益平台",
)
_LOW_QUALITY_DOMAINS = (
    "b2b168.com", "51sole.com", "fang.com", "docin.com", "doc88.com",
    "book118.com", "shangxueba.com",
)
_OFFICIAL_DOMAIN_PARTS = (
    "ccgp.gov.cn", ".gov.cn", "cpic.com.cn", "chinapost.com.cn", "bosc.cn",
)
_STATUS_NOISE = (
    "招标公告", "采购公告", "征集公告", "方案征集公告", "中标公告",
    "中标结果公告", "中标候选人公示", "成交公告", "成交结果公告",
    "成交结果公示", "候选人公示", "结果公示", "项目公告", "第二次",
    "重新征集", "延期公告",
)


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(term.lower() in lowered for term in terms)


def _company_aliases(company_name: str) -> tuple[str, ...]:
    aliases = {company_name.strip()}
    for suffix in ("股份有限公司", "有限责任公司", "有限公司"):
        aliases.add(company_name.replace(suffix, "").strip())
    if "中国邮政储蓄银行" in company_name:
        aliases.update(("中国邮政储蓄银行", "邮政储蓄银行", "邮储银行"))
    if "中国太平洋保险" in company_name:
        aliases.update(("中国太平洋保险", "太平洋保险", "中国太保", "太保"))
    return tuple(sorted((alias for alias in aliases if len(alias) >= 4), key=len, reverse=True))


def _target_scope(text: str, company_name: str) -> str:
    """区分目标主体、总行/集团、兄弟分支和行业案例。"""
    if "中国邮政储蓄银行" in company_name and "上海分行" in company_name:
        exact_aliases = {company_name, "邮政储蓄银行上海分行", "邮储银行上海分行"}
        for suffix in ("股份有限公司", "有限责任公司", "有限公司"):
            exact_aliases.add(company_name.replace(suffix, "").strip())
        if any(alias in text for alias in exact_aliases):
            return "target"
        family_aliases = ("中国邮政储蓄银行", "邮政储蓄银行", "邮储银行")
        if any(alias in text for alias in family_aliases):
            if re.search(r"(?:银行)?[^，。；\s]{0,8}分行", text):
                return "sibling"
            return "parent"
        return "industry"
    return "target" if any(alias in text for alias in _company_aliases(company_name)) else "industry"


def _is_low_quality_domain(domain: str) -> bool:
    return any(domain == item or domain.endswith(f".{item}") for item in _LOW_QUALITY_DOMAINS)


def _is_official_domain(domain: str) -> bool:
    return any(part in domain for part in _OFFICIAL_DOMAIN_PARTS)


def _event_signature(title: str) -> str:
    normalized = title.lower()
    for phrase in _STATUS_NOISE:
        normalized = normalized.replace(phrase.lower(), "")
    normalized = re.sub(r"[-—_\s:：;；,，。·()（）\[\]【】\"'“”‘’/\\]", "", normalized)
    normalized = re.sub(r"\d{4}年|\d+年度", "", normalized)
    return normalized


def _same_event(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    left_signature = _event_signature(str(left.get("title") or ""))
    right_signature = _event_signature(str(right.get("title") or ""))
    if min(len(left_signature), len(right_signature)) < 8:
        return False
    if left_signature in right_signature or right_signature in left_signature:
        return True
    return SequenceMatcher(None, left_signature, right_signature).ratio() >= 0.82


def _initial_assessment(candidate: Mapping[str, Any], company_name: str) -> dict[str, str]:
    title = str(candidate.get("title") or "")
    snippet = str(candidate.get("snippet") or "")
    text = f"{title}\n{snippet}"
    domain = str(candidate.get("domain") or "").lower()
    target_scope = _target_scope(text, company_name)
    core_in_title = _contains_any(title, _CORE_TERMS)
    core = core_in_title or _contains_any(snippet, _CORE_TERMS)
    adjacent = _contains_any(text, _ADJACENT_TERMS)
    procurement = _contains_any(title, _PROCUREMENT_TERMS)
    generic_title = len(_event_signature(title)) < 8
    if generic_title:
        procurement = procurement or _contains_any(snippet, _PROCUREMENT_TERMS)
    unrelated = _contains_any(title, _UNRELATED_TERMS)
    low_quality = _is_low_quality_domain(domain)

    if unrelated and not core:
        return {"kind": "irrelevant", "confidence": "high", "reason": "UNRELATED_PROCUREMENT"}
    if low_quality and target_scope == "industry":
        return {"kind": "irrelevant", "confidence": "high", "reason": "LOW_QUALITY_GENERIC_PAGE"}
    if core and procurement:
        strength = "core" if core_in_title or generic_title else "adjacent"
        reason_scope = "TARGET" if target_scope == "target" else "PARENT" if target_scope == "parent" else "SIBLING" if target_scope == "sibling" else "INDUSTRY"
        return {
            "kind": "positive", "scope": target_scope,
            "strength": strength,
            "confidence": "high" if strength == "core" and (target_scope in {"target", "parent"} or _is_official_domain(domain)) else "medium",
            "reason": f"{reason_scope}_{strength.upper()}_BIDDING",
        }
    if adjacent and procurement:
        return {
            "kind": "positive", "scope": target_scope,
            "strength": "adjacent", "confidence": "medium",
            "reason": "TARGET_ADJACENT_BIDDING" if target_scope == "target" else "PARENT_ADJACENT_BIDDING" if target_scope == "parent" else "SIBLING_ADJACENT_BIDDING" if target_scope == "sibling" else "INDUSTRY_ADJACENT_BIDDING",
        }
    if core or adjacent:
        return {"kind": "uncertain", "confidence": "low", "reason": "CORE_SIGNAL_WITHOUT_BIDDING_PROOF"}
    return {
        "kind": "irrelevant", "confidence": "high" if procurement or unrelated else "medium",
        "reason": "NO_CUSTOMER_SERVICE_SIGNAL",
    }


def _positive_clusters(
    candidates: list[dict[str, Any]],
    assessments: Mapping[str, Mapping[str, str]],
) -> list[list[dict[str, Any]]]:
    positives = [candidate for candidate in candidates if assessments[str(candidate["candidate_id"])]["kind"] == "positive"]
    parent = list(range(len(positives)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    for left_index, left in enumerate(positives):
        left_assessment = assessments[str(left["candidate_id"])]
        for right_index in range(left_index + 1, len(positives)):
            right = positives[right_index]
            right_assessment = assessments[str(right["candidate_id"])]
            if left_assessment.get("strength") == right_assessment.get("strength") and _same_event(left, right):
                union(left_index, right_index)

    clusters: dict[int, list[dict[str, Any]]] = {}
    for index, candidate in enumerate(positives):
        clusters.setdefault(find(index), []).append(candidate)
    return list(clusters.values())


def _canonical_candidate(cluster: list[dict[str, Any]]) -> dict[str, Any]:
    return sorted(
        cluster,
        key=lambda candidate: (
            not _is_official_domain(str(candidate.get("domain") or "").lower()),
            _is_low_quality_domain(str(candidate.get("domain") or "").lower()),
            str(candidate["candidate_id"]),
        ),
    )[0]


def _group_name(cluster: list[dict[str, Any]]) -> str:
    signature = _event_signature(str(_canonical_candidate(cluster).get("title") or ""))
    digest = hashlib.sha1(signature.encode("utf-8")).hexdigest()[:10]
    return f"evidence_{digest}"


def preannotate_fixture(fixture: Mapping[str, Any]) -> dict[str, Any]:
    """生成 ``pending_review`` 初标稿；不使用历史引用字段参与判断。"""
    if fixture.get("schema_version") != "task-screening-fixture/v5":
        raise ValueError("只支持 task-screening-fixture/v5")
    if fixture.get("annotation_status") != "pending":
        raise ValueError("输入 Fixture annotation_status 必须为 pending")
    if fixture.get("dimension") != "bidding_information":
        raise ValueError("当前初标规则只支持 bidding_information")
    candidates = fixture.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise ValueError("Fixture 不包含 candidates")
    company_name = str((fixture.get("screening_context") or {}).get("company_name") or "").strip()
    if not company_name:
        target_entity_names = fixture.get("target_entity_names")
        if isinstance(target_entity_names, list):
            company_name = next((str(item).strip() for item in target_entity_names if str(item).strip()), "")
    if not company_name:
        raise ValueError("screening_context.company_name 不能为空")

    result = copy.deepcopy(dict(fixture))
    output_candidates: list[dict[str, Any]] = result["candidates"]
    ids = [str(candidate.get("candidate_id") or "") for candidate in output_candidates]
    if any(not candidate_id for candidate_id in ids) or len(set(ids)) != len(ids):
        raise ValueError("candidate_id 必须非空且唯一")
    assessments = {
        str(candidate["candidate_id"]): _initial_assessment(candidate, company_name)
        for candidate in output_candidates
    }
    for candidate in output_candidates:
        candidate.pop("evidence_group", None)
        assessment = assessments[str(candidate["candidate_id"])]
        candidate["preannotation_confidence"] = assessment["confidence"]
        candidate["preannotation_reason_code"] = assessment["reason"]
        candidate.pop("active_until", None)
        candidate["procurement_lifecycle"] = "not_applicable"
        if assessment["kind"] == "uncertain":
            candidate["business_label"] = "uncertain"
            candidate["evidence_role"] = "uncertain"
        elif assessment["kind"] == "irrelevant":
            candidate["business_label"] = "irrelevant"
            candidate["evidence_role"] = "out_of_scope"

    for cluster in _positive_clusters(output_candidates, assessments):
        canonical = _canonical_candidate(cluster)
        canonical_id = str(canonical["candidate_id"])
        canonical_assessment = assessments[canonical_id]
        unique_target_core = (
            len(cluster) == 1 and canonical_assessment.get("scope") in {"target", "parent"}
            and canonical_assessment.get("strength") == "core"
        )
        if unique_target_core:
            canonical["business_label"] = "must_keep"
            canonical["evidence_role"] = "target_procurement"
            canonical["procurement_lifecycle"] = "historical_or_unknown"
            canonical["preannotation_reason_code"] = "UNIQUE_TARGET_CORE_BIDDING"
            continue
        group_name = _group_name(cluster)
        for candidate in cluster:
            candidate["evidence_group"] = group_name
            assessment = assessments[str(candidate["candidate_id"])]
            candidate["evidence_role"] = (
                "target_procurement"
                if assessment.get("scope") in {"target", "parent", "sibling"}
                else "industry_capability_intelligence"
            )
            candidate["procurement_lifecycle"] = "historical_or_unknown"
            if candidate is canonical:
                candidate["business_label"] = "relevant"
            else:
                candidate["business_label"] = "acceptable_alternative"
                candidate["preannotation_reason_code"] = "DUPLICATE_EVENT_ALTERNATIVE"
                candidate["preannotation_confidence"] = "medium"

    label_counts = Counter(candidate["business_label"] for candidate in output_candidates)
    confidence_counts = Counter(candidate["preannotation_confidence"] for candidate in output_candidates)
    result["annotation_status"] = "pending_review"
    result["preannotation"] = {
        "method": "bidding-rule-preannotation/v1",
        "requires_human_review": True,
        "quality_gold_uses_historical_references": False,
        "label_counts": dict(sorted(label_counts.items())),
        "confidence_counts": dict(sorted(confidence_counts.items())),
        "human_review_scope": [
            "all_must_keep", "all_relevant_and_acceptable_alternative", "all_uncertain",
            "all_medium_or_low_confidence", "high_confidence_irrelevant_spot_check",
        ],
    }
    candidate_map = {
        str(candidate["candidate_id"]): candidate
        for candidate in output_candidates
    }
    for cluster in result.get("candidate_identity_clusters", []):
        candidate = candidate_map.get(str(cluster.get("representative_id") or ""))
        if candidate is None:
            raise ValueError("candidate_identity_clusters 包含不存在的代表候选")
        cluster["annotation_resolution"] = {
            "status": "resolved",
            "business_label": candidate["business_label"],
            "evidence_role": candidate["evidence_role"],
            "procurement_lifecycle": candidate["procurement_lifecycle"],
        }
    return result


def write_preannotation(fixture: Mapping[str, Any], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        raise FileExistsError(f"输出文件已存在：{output_path}")
    output_path.write_text(json.dumps(fixture, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="生成 Fixture v3 招投标候选第一轮标注")
    parser.add_argument("fixture", type=Path, help="annotation_status=pending 的 Fixture v3")
    parser.add_argument("--output", required=True, type=Path, help="待人工复核的输出文件")
    args = parser.parse_args()
    fixture = json.loads(args.fixture.read_text(encoding="utf-8"))
    annotated = preannotate_fixture(fixture)
    write_preannotation(annotated, args.output)
    print(f"初标完成，等待人工二次复核：{args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
