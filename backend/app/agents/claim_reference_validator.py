"""报告校验器

核心入口：
- validate_claim_references(): 校验报告结论是否引用了有效的证据 ID
- 支撑关系审计（evidence → claim 的语义支撑判断）由 WBS-10 EvidenceAuditor 负责

本模块当前仅做引用完整性校验：
1. evidence_index 必须有 claims 字段
2. 每条 claim 必须绑定至少 1 个 evidence_id
3. 每个 evidence_id 必须存在于 DB
4. 每条 evidence 必须有 snippet/url/captured_at
"""
import json
import logging
import re
from uuid import UUID

from app.db.session import SessionLocal
from app.db.models import Evidence
from app.agents.audit_selection import AuditSelection, select_report_audit_context

logger = logging.getLogger(__name__)


class ValidationResult:
    def __init__(self):
        self.passed = True
        self.claims_total = 0
        self.claims_valid = 0
        self.violations: list[dict] = []

    def add_violation(self, claim_id: str, reason: str):
        self.passed = False
        self.violations.append({"claim_id": claim_id, "reason": reason})

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "claims_total": self.claims_total,
            "claims_valid": self.claims_valid,
            "violations": self.violations,
        }


# ── 主函数 ───────────────────────────────────────────────────────────────


def validate_claim_references(
    task_id: str,
    report_content_md: str,
    evidence_index: dict,
) -> ValidationResult:
    """
    校验报告关键结论是否可追溯到证据。

    校验规则:
    1. evidence_index 必须有 claims 字段
    2. 每条 claim 必须绑定至少 1 个 evidence_id
    3. 每个 evidence_id 必须存在于 DB
    4. 每条 evidence 必须有 snippet/url/captured_at

    注意：本函数仅做引用完整性校验，不判断 evidence 是否真的支撑 claim。
    支撑关系审计由 WBS-10 EvidenceAuditor 负责。
    """
    result = ValidationResult()

    claims = evidence_index.get("claims", [])
    result.claims_total = len(claims)

    if not claims:
        # 尝试从报告中解析 evidence_id 引用
        claims = _extract_claims_from_markdown(report_content_md, evidence_index)
        result.claims_total = len(claims)

    if result.claims_total == 0:
        result.add_violation("root", "报告没有关键结论声明(claims 为空)")
        return result

    db = SessionLocal()
    try:
        for claim in claims:
            claim_id = claim.get("claim_id", "unknown")
            evidence_ids = claim.get("evidence_ids", [])

            if not evidence_ids:
                result.add_violation(claim_id, "关键结论未绑定任何证据")
                continue

            all_valid = True
            for ev_id in evidence_ids:
                try:
                    ev_uuid = UUID(str(ev_id))
                except (ValueError, TypeError):
                    result.add_violation(claim_id, f"evidence_id 格式无效: {ev_id}")
                    all_valid = False
                    continue

                evidence = db.query(Evidence).filter(
                    Evidence.id == ev_uuid,
                    Evidence.task_id == task_id,
                ).first()
                if not evidence:
                    result.add_violation(claim_id, f"evidence_id 在 DB 中不存在或不属于此任务: {ev_id}")
                    all_valid = False
                    continue

                if not evidence.snippet or not evidence.snippet.strip():
                    result.add_violation(claim_id, f"evidence {ev_id} 缺少 snippet")
                    all_valid = False
                if not evidence.url or not evidence.url.strip():
                    result.add_violation(claim_id, f"evidence {ev_id} 缺少 url")
                    all_valid = False
                if not evidence.captured_at:
                    result.add_violation(claim_id, f"evidence {ev_id} 缺少 captured_at")
                    all_valid = False

            if all_valid:
                result.claims_valid += 1

    finally:
        db.close()

    if result.claims_valid == 0 and result.claims_total > 0:
        result.passed = False

    return result


def _extract_claims_from_markdown(report_content: str, evidence_index: dict) -> list[dict]:
    """从 Markdown 报告中尝试提取带 evidence_id 引用的结论"""
    claims = []
    # 查找 evidence_id 引用模式: [ev:uuid] 或 evidence_id: uuid
    ev_id_pattern = re.compile(r'(?:\[ev:|evidence_id:\s*)([a-fA-F0-9-]{36})', re.IGNORECASE)

    # 按段落拆分
    paragraphs = report_content.split("\n\n")
    for i, para in enumerate(paragraphs):
        matches = ev_id_pattern.findall(para)
        if matches:
            claims.append({
                "claim_id": f"para_{i}",
                "claim": para[:200].strip(),
                "evidence_ids": list(set(matches)),
            })

    return claims


def build_evidence_index_from_evidences(evidences: list) -> dict:
    """从 evidence 列表构建结构化 evidence_index（供 LLM 使用的格式）"""
    return {
        "total": len(evidences),
        "items": [
            {
                "id": str(ev.get("id", "")),
                "dimension": ev.get("dimension", "unknown"),
                "title": ev.get("title", "")[:200],
                "snippet": ev.get("snippet", "")[:500],
                "url": ev.get("url", ""),
                "captured_at": ev.get("captured_at", ""),
            }
            for ev in evidences
        ],
    }


def select_claim_audit_context(
    report_content_md: str,
    evidence_index: dict,
) -> AuditSelection:
    """从报告 Claim 与 Evidence 索引生成最小审计上下文，供后续批量审计调用。"""
    claims = evidence_index.get("claims", [])
    if not claims:
        claims = _extract_claims_from_markdown(report_content_md, evidence_index)
    return select_report_audit_context(
        evidence_items=evidence_index.get("evidence_items", evidence_index.get("items", [])),
        claims=claims,
        conflict_evidence_ids=evidence_index.get("conflict_evidence_ids", []),
    )
