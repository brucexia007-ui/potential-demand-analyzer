import json
import logging
import os
from openai import APIError, RateLimitError, Timeout
from app.agents.state import AgentState
from app.db.session import SessionLocal
from app.db.models import Report, Evidence
from app.agents.claim_reference_validator import validate_claim_references, _extract_claims_from_markdown, build_evidence_index_from_evidences
from app.llm.gateway_client import get_gateway_client

# WBS-10: 审计管线
from app.agents.schemas.claim_schema import Severity, AuditFindings
from app.agents.audit_severity import triage_aggregate
from app.agents.audit_persistence import persist_evidence_audits, persist_claim_audits

from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

logger = logging.getLogger(__name__)

_gateway_client = get_gateway_client()

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type((RateLimitError, Timeout, APIError)),
    reraise=True
)
def _extract_llm_synthesize(system_prompt: str, prompt: str) -> str:
    response = _gateway_client.infer(
        prompt=prompt,
        system_prompt=system_prompt
    )
    return response["content"]

def synthesize_node(state: AgentState) -> dict:
    company = state.get("company_name", "未知公司")
    direction = state.get("demand_direction", "未知需求")
    task_id = state.get("task_id")
    evidences = state.get("evidences", [])
    findings = state.get("findings", {})

    logs = []
    logs.append({"level": "INFO", "message": f"[Synthesizer] 开始为 {company} 的 {direction} 生成最终报告..."})

    # 构建结构化证据 JSON
    evidence_items = build_evidence_index_from_evidences(evidences)
    evidence_json = json.dumps(evidence_items, ensure_ascii=False, indent=2)
    prompt = (
        f"公司: {company}\n需求方向: {direction}\n\n"
        f"以下是结构化证据数据 (JSON):\n{evidence_json}\n\n"
        f"请基于上述证据生成 Markdown 报告。每条关键结论必须引用 [ev:uuid]。"
    )

    # 加载系统提示词
    prompt_path = os.path.join(os.path.dirname(__file__), "..", "prompts", "synthesizer.md")
    system_prompt = ""
    if os.path.exists(prompt_path):
        with open(prompt_path, "r", encoding="utf-8") as f:
            system_prompt = f.read()
    else:
        system_prompt = "你是报告汇总代理。请基于输入的证据生成 Markdown 报告。关键结论需附上证据 ID。"

    system_prompt += (
        "\n\n请直接输出 Markdown 格式的报告内容，包含总览、各维度分析和结论。"
        "\n每条关键结论必须在行尾标注证据引用，格式为 [ev:evidence_uuid]。"
        "\n没有证据支持的推断性内容请标注 [推测] 前缀。"
    )

    report_content = "暂无内容生成。"
    if evidences:
        try:
            report_content = _extract_llm_synthesize(system_prompt, prompt)
            logs.append({"level": "INFO", "message": "[Synthesizer] 成功生成报告内容"})
        except Exception as e:
            logs.append({"level": "ERROR", "message": f"[Synthesizer] 报告生成 LLM 调用失败: {e}"})
            report_content = f"报告生成失败：{str(e)}"
    else:
        logs.append({"level": "WARNING", "message": "[Synthesizer] 没有任何证据，生成降级空报告"})
        report_content = f"# {company} - {direction} 潜在需求分析报告\n\n> 提示：当前未收集到任何相关证据，无法生成有效分析。\n"

    # 构建 evidence_index 并校验
    extracted_claims = _extract_claims_from_markdown(report_content, {})
    evidence_index = {
        "count": len(evidences),
        "ids": [ev.get("id") for ev in evidences],
        "claims": extracted_claims,
        "evidence_items": evidence_items.get("items", []),
    }

    # 校验报告
    validation_passed = True
    if task_id and evidences:
        validation = validate_claim_references(task_id, report_content, evidence_index)
        validation_passed = validation.passed
        if not validation.passed:
            logs.append({"level": "WARNING", "message": f"[Synthesizer] 报告校验未通过: {validation.to_dict()}"})
            # 重试一次
            retry_prompt = (
                prompt + "\n\n[系统指令] 上次报告校验问题: "
                + "; ".join(v["reason"] for v in validation.violations[:3])
                + "\n请重新生成，确保每条关键结论引用 evidence_id。"
            )
            try:
                report_content = _extract_llm_synthesize(system_prompt, retry_prompt)
                extracted_claims = _extract_claims_from_markdown(report_content, {})
                evidence_index["claims"] = extracted_claims
                validation2 = validate_claim_references(task_id, report_content, evidence_index)
                validation_passed = validation2.passed
                logs.append({"level": "INFO", "message": f"[Synthesizer] 重试后校验: {validation2.to_dict()}"})
            except Exception as e:
                logs.append({"level": "ERROR", "message": f"[Synthesizer] 报告重试失败: {e}"})

    # 落库 Report（不重复保存 evidence，extractor 已入库）
    if task_id:
        db = SessionLocal()
        try:
            existing = db.query(Report).filter(Report.task_id == task_id).first()
            if existing:
                existing.content_md = report_content
                existing.evidence_index = evidence_index
                report = existing
            else:
                report = Report(
                    task_id=task_id,
                    content_md=report_content,
                    raw_data=findings,
                    evidence_index=evidence_index,
                )
                db.add(report)
            db.flush()  # 获取 report.id

            # WBS-10: 审计管线（LangGraph 路径 — 简化版，无 Re-Plan）
            db_evidences = db.query(Evidence).filter(Evidence.task_id == task_id).all()
            if db_evidences and extracted_claims:
                try:
                    audit_findings = _run_audit_pipeline_simple(
                        db=db,
                        task_id=task_id,
                        report_id=report.id,
                        report_content=report_content,
                        extracted_claims=extracted_claims,
                        db_evidences=db_evidences,
                        company_name=company,
                        demand_direction=direction,
                    )
                    if audit_findings.severity != Severity.ACCEPTABLE:
                        logs.append({
                            "level": "WARNING",
                            "message": (
                                f"[Synthesizer] 审计发现问题: severity={audit_findings.severity.value}, "
                                f"fatal={len(audit_findings.fatal_claims)}, "
                                f"major={len(audit_findings.major_claims)}"
                            ),
                        })
                        report_content = _apply_degraded_expression_simple(
                            report_content, audit_findings
                        )
                        report.content_md = report_content
                        evidence_index["audit"] = audit_findings.model_dump()
                        report.evidence_index = evidence_index
                except Exception as e:
                    logs.append({
                        "level": "ERROR",
                        "message": f"[Synthesizer] 审计管线失败（非致命）: {e}",
                    })

            db.commit()
            logs.append({"level": "INFO", "message": f"[Synthesizer] 报告已落库，claims={len(extracted_claims)}, validation_passed={validation_passed}"})
        except Exception as e:
            db.rollback()
            logs.append({"level": "ERROR", "message": f"[Synthesizer] 报告落库失败: {e}"})
        finally:
            db.close()

    return {
        "logs": logs,
        "findings": {
            "synthesizer": {
                "status": "COMPLETED",
                "summary": "最终报告已生成并入库."
            }
        }
    }


# ══════════════════════════════════════════════════════════════════════════════
# WBS-10: 审计管线辅助函数（LangGraph 路径简化版）
# ══════════════════════════════════════════════════════════════════════════════


def _run_audit_pipeline_simple(
    db,
    task_id: str,
    report_id,
    report_content: str,
    extracted_claims: list[dict],
    db_evidences: list,
    company_name: str = "",
    demand_direction: str = "",
) -> AuditFindings:
    """LangGraph 路径的审计管线（简化版，不触发 Re-Plan）。"""
    from app.agents.agents.auditor_agent import EvidenceAuditorAgent
    from app.agents.agents.skeptic_agent import SkepticAgent
    from app.agents.schemas.claim_schema import ClaimWithEvidence

    logger.info(
        f"[SynthesizerAudit] 开始审计: task={task_id}, "
        f"evidences={len(db_evidences)}, claims={len(extracted_claims)}"
    )

    # Step 1: 构建 claim → evidence 映射
    ev_to_claims: dict[str, list[str]] = {}
    for claim in extracted_claims:
        for ev_id in claim.get("evidence_ids", []):
            ev_str = str(ev_id)
            if ev_str not in ev_to_claims:
                ev_to_claims[ev_str] = []
            ev_to_claims[ev_str].append(claim.get("claim", "")[:200])

    # Step 2: EvidenceAuditorAgent
    auditor = EvidenceAuditorAgent(model=None)
    evidence_dicts: list[dict] = []
    claim_contexts: dict[str, str] = {}
    for ev in db_evidences:
        ev_id_str = str(ev.id)
        ev_dict = {
            "id": ev_id_str,
            "title": ev.title or "",
            "snippet": ev.snippet or "",
            "url": ev.url or "",
            "source_reliability": getattr(ev, "source_reliability", "UNKNOWN") or "UNKNOWN",
            "published_at": ev.captured_at.isoformat() if getattr(ev, "captured_at", None) else "",
        }
        evidence_dicts.append(ev_dict)
        related = ev_to_claims.get(ev_id_str, [])
        claim_contexts[ev_id_str] = "；".join(related) if related else f"{company_name} - {demand_direction}"

    evidence_audits = auditor.audit_all(
        evidences=evidence_dicts,
        claim_contexts=claim_contexts,
        task_context=f"{company_name} - {demand_direction}",
    )
    try:
        persist_evidence_audits(db, evidence_audits)
    except Exception as e:
        logger.error(f"[SynthesizerAudit] evidence_audits 落库失败: {e}")

    # Step 3: SkepticAgent
    claims_with_evidence: list[ClaimWithEvidence] = []
    for claim in extracted_claims:
        claim_ev_ids = claim.get("evidence_ids", [])
        claim_ev_strs = [str(eid) for eid in claim_ev_ids]
        claim_audit_results = [ear for ear in evidence_audits if str(ear.evidence_id) in claim_ev_strs]
        evidence_summaries = [
            {"title": ev.title or "", "snippet": (ev.snippet or "")[:300]}
            for ev in db_evidences if str(ev.id) in claim_ev_strs
        ]
        claims_with_evidence.append(ClaimWithEvidence(
            claim_id=claim.get("claim_id", "unknown"),
            claim_text=claim.get("claim", "")[:500],
            evidence_ids=[eid for eid in claim_ev_ids],
            evidence_summaries=evidence_summaries,
            evidence_audit_results=claim_audit_results,
        ))

    skeptic = SkepticAgent(model=None)
    claim_audits = skeptic.audit_claims(
        claims=claims_with_evidence,
        company_name=company_name,
        demand_direction=demand_direction,
    )
    try:
        persist_claim_audits(db, report_id, claim_audits)
    except Exception as e:
        logger.error(f"[SynthesizerAudit] claim_audits 落库失败: {e}")

    # Step 4: Severity
    severity, fatal_list, major_list, minor_list = triage_aggregate(claim_audits)
    re_plan_suggestions = ""
    if fatal_list:
        re_plan_suggestions = "fatal claims: " + "; ".join(c.claim_text[:100] for c in fatal_list)

    return AuditFindings(
        task_id=task_id,
        report_id=report_id,
        evidence_audits=evidence_audits,
        claim_audits=claim_audits,
        severity=severity,
        fatal_claims=fatal_list,
        major_claims=major_list,
        minor_claims=minor_list,
        re_plan_suggestions=re_plan_suggestions,
    )


def _apply_degraded_expression_simple(
    report_content: str,
    findings: AuditFindings,
) -> str:
    """WBS-10.8: 在报告中标记置信度（LangGraph 路径）。"""
    all_problematic = findings.fatal_claims + findings.major_claims + findings.minor_claims
    for claim in all_problematic:
        claim_text = claim.claim_text.strip()
        if not claim_text or claim_text not in report_content:
            continue
        if claim_text.startswith("[置信度:"):
            continue
        if claim in findings.fatal_claims:
            marker = "[置信度: 低 - 证据不足] "
        elif claim in findings.major_claims:
            marker = "[置信度: 中低 - 证据偏弱] "
        else:
            marker = "[置信度: 中低] "
        report_content = report_content.replace(claim_text, marker + claim_text)
    return report_content
