"""
Harness Worker - Celery 任务定义

职责：
1. 执行 Harness 任务
2. 支持断点续传
3. 实时状态更新
4. 报告合成与入库
"""

import json
import logging
import os
import time
from typing import Optional

from app.worker.celery_app import celery_app
from app.agents.harness.spec import TaskSpec, BudgetConfig, DimensionStatus
from app.agents.harness.agent_harness import AgentHarness
from app.agents.memory.experience_memory import ExperienceMemory
from app.db.session import SessionLocal
from app.db.models import Evidence as DBEvidence, Report
from app.llm.gateway_client import get_gateway_client
from app.api.task_store import update_task_status, finalize_task_status, append_task_log
from app.agents.claim_reference_validator import validate_claim_references, build_evidence_index_from_evidences
from app.services.notification_service import NotificationService
from app.evidence.snapshot_service import SnapshotService
from app.evidence.source_reliability import score_source_reliability
from app.core.task_execution_metrics import task_execution_metrics

# WBS-10: 审计管线
from app.agents.schemas.claim_schema import (
    Severity,
    AuditFindings,
    ClaimWithEvidence,
    EvidenceAuditResult,
    ClaimAuditResult,
)
from app.agents.audit_severity import triage_aggregate
from app.agents.audit_persistence import (
    load_reusable_evidence_audits,
    persist_evidence_audits,
    persist_claim_audits,
    count_claim_retries,
    count_dimension_retries,
)

logger = logging.getLogger(__name__)
_gateway_client = get_gateway_client()


def _dimension_task_progress_update(
    *,
    dimension: str,
    result_status: DimensionStatus,
) -> tuple[str, str, int]:
    """把单维执行结果写为任务过程状态，禁止在此处结束整个任务。"""
    return (
        "RUNNING",
        f"harness_{dimension}_{result_status.value.lower()}",
        90,
    )


# ── WBS-4: 并发容量检查 ──────────────────────────────────────────────────

def _check_concurrency_before_task(task_id: str) -> None:
    """任务开始前检查 Provider 并发容量，熔断时记录告警日志但不阻塞任务。

    此函数遵循 fail-open 原则：任何异常都不阻塞任务继续执行，
    仅记录告警供运维参考。
    """
    try:
        from app.config_center.adaptive_concurrency import AdaptiveConcurrencyService
        db = SessionLocal()
        try:
            svc = AdaptiveConcurrencyService(db)
            cap = svc.get_capacity()
            if cap.is_throttled:
                logger.warning(
                    f"[HarnessWorker] 任务 {task_id} 在限速状态下启动: "
                    f"{cap.throttle_reason}，当前安全并发数={cap.max_concurrent_tasks}，"
                    f"LLM并发={cap.max_concurrent_llm_calls}，Search并发={cap.max_concurrent_search_calls}"
                )
            else:
                logger.info(
                    f"[HarnessWorker] 任务 {task_id} 并发容量正常: "
                    f"安全并发数={cap.max_concurrent_tasks}"
                )
        finally:
            db.close()
    except Exception as e:
        logger.warning(f"[HarnessWorker] 并发容量检查失败（任务继续执行）: {e}")


# ── 原有辅助函数 ────────────────────────────────────────────────────────


def _resolve_task_user_id(task_id: str) -> str | None:
    """从 DB 查询任务所属用户 ID"""
    db = SessionLocal()
    try:
        from app.db.models import Task as DBTask
        task = db.query(DBTask).filter(DBTask.id == task_id).first()
        return str(task.user_id) if task else None
    except Exception:
        return None
    finally:
        db.close()


def _extract_website_from_evidences(evidences: list) -> str | None:
    """从证据列表中提取公司官网 URL。

    策略:
    1. 优先从 metadata._raw_content 中找 URL 模式
    2. 从 url 字段中提取可能的官网域名
    3. 返回最可能的官网 URL 或 None
    """
    import re
    from urllib.parse import urlparse

    candidates: list[tuple[str, int]] = []  # (url, score)

    for ev in (evidences or []):
        # 策略 1: 从 metadata 中找
        meta = getattr(ev, "meta_data", {}) or {}
        raw_content = meta.get("_raw_content", "")
        if raw_content:
            urls = re.findall(r'https?://[^\s<>"\'）】)]+', str(raw_content))
            for u in urls:
                parsed = urlparse(u)
                # 优先取看起来像官网的 URL（不含 /search /login 等）
                path = parsed.path.lower()
                if any(x in path for x in ["search", "login", "admin", "api"]):
                    candidates.append((u, 1))
                else:
                    candidates.append((u, 3))

        # 策略 2: 从 evidence.url 自身提取域名
        ev_url = getattr(ev, "url", "") or ""
        if ev_url:
            parsed = urlparse(ev_url)
            if parsed.hostname:
                # 构造根 URL
                root_url = f"{parsed.scheme}://{parsed.hostname}"
                candidates.append((root_url, 2))

    if not candidates:
        return None

    # 按分数降序排列，返回最高分
    candidates.sort(key=lambda x: x[1], reverse=True)

    # 去重（同域名只保留最高分）
    seen_domains: set[str] = set()
    best: str | None = None
    for url, score in candidates:
        domain = urlparse(url).hostname or ""
        if domain not in seen_domains:
            seen_domains.add(domain)
            if best is None:
                best = url

    return best


@celery_app.task(name="tasks.execute_harness")
def execute_harness(
    task_id: str,
    company_name: str,
    demand_direction: str,
    domain_context: dict,
    dimension: str,
    template_id: str = "bidding",  # WBS-8: 传入实际 template_id
    use_mock_agents: bool = False,
    send_notification: bool = True,
    complexity_level: str = "medium",
    harness_config: Optional[dict] = None,
) -> dict:
    """
    执行 Harness 任务

    Args:
        task_id: 任务 ID
        company_name: 公司名称
        demand_direction: 需求方向
        domain_context: 领域上下文
        dimension: 维度名称
        template_id: 模板/Skill 标识符（WBS-8: 不再硬编码为 "bidding"）
        use_mock_agents: 是否使用 Mock 智能体
        complexity_level: 算力复杂度 (low/medium/high)，用于动态路由
        harness_config: 前端传入的 Harness 配置 (max_iterations, quality_threshold, allow_human_intervention 等)

    Returns:
        dict: 执行结果
    """
    dimension_started_at = time.perf_counter()
    dimension_status = "FAILED"
    harness = None
    db_session = None
    try:
        task_execution_metrics.bind_gateway_client(_gateway_client)
        # 创建 DB session 用于经验记忆
        db_session = SessionLocal()
        experience_memory = ExperienceMemory(db_session)

        # 构建任务规约
        from app.agents.harness.spec import DimensionGoal

        ctx_str = domain_context if isinstance(domain_context, str) else json.dumps(domain_context, ensure_ascii=False)

        # WBS-7: 从 domain_context 提取上下文信息丰富 goal 描述
        ctx_dict = (
            domain_context if isinstance(domain_context, dict)
            else (json.loads(domain_context) if isinstance(domain_context, str) and domain_context not in ("", "{}") else {})
        )
        goal_parts = [f"分析 {company_name} 的 {demand_direction} 相关{dimension}信息"]
        if ctx_dict:
            extras = []
            if ctx_dict.get("industry"):
                extras.append(f"行业={ctx_dict['industry']}")
            if ctx_dict.get("region"):
                extras.append(f"地区={ctx_dict['region']}")
            if ctx_dict.get("business_goal"):
                extras.append(f"业务目标={ctx_dict['business_goal']}")
            if ctx_dict.get("time_range"):
                extras.append(f"时间范围={ctx_dict['time_range']}")
            if ctx_dict.get("known_clues"):
                clues_summary = "; ".join(
                    c.get("description", str(c)) for c in ctx_dict["known_clues"][:3]
                )
                if clues_summary:
                    extras.append(f"已知线索={clues_summary}")
            if extras:
                goal_parts.append("（上下文：" + "，".join(extras) + "）")
        enriched_goal = " ".join(goal_parts)

        dimension_goals = {
            dimension: DimensionGoal(
                goal=enriched_goal,
                must_extract=["title", "snippet", "source", "date"],
                success_criteria=["至少提取 3 条有效证据", "证据与目标相关"],
                complexity_level=complexity_level,
            )
        }

        # 从 harness_config 提取 Harness 运行参数
        cfg = harness_config or {}
        task_spec_kwargs = dict(
            task_id=task_id,
            company_name=company_name,
            demand_direction=demand_direction,
            template_id=template_id,  # WBS-8: 使用实际 template_id
            domain_context=ctx_str,
            dimension_goals=dimension_goals,
        )
        if "max_iterations" in cfg:
            task_spec_kwargs["max_iterations"] = cfg["max_iterations"]
        if "quality_threshold" in cfg:
            task_spec_kwargs["quality_threshold"] = cfg["quality_threshold"]
        if "allow_human_intervention" in cfg:
            task_spec_kwargs["allow_human_intervention"] = cfg["allow_human_intervention"]
        if "max_suspended_minutes" in cfg:
            task_spec_kwargs["max_suspended_minutes"] = cfg["max_suspended_minutes"]
        task_spec = TaskSpec(**task_spec_kwargs)

        # 更新任务状态
        update_task_status(task_id, "RUNNING", current_stage=f"harness_{dimension}", progress=10)
        append_task_log(
            task_id,
            f"harness_{dimension}",
            f"Harness 开始执行：{dimension}",
            "INFO"
        )

        # 进度回调：实时更新任务状态用于 ETA 估算
        def _on_progress(tid: str, stage: str, pct: int) -> None:
            update_task_status(tid, "RUNNING", current_stage=f"harness_{dimension}_{stage}", progress=pct)

        # 初始化 Harness
        harness = AgentHarness(
            task_spec=task_spec,
            dimension=dimension,
            use_mock_agents=use_mock_agents,
            experience_memory=experience_memory,
            progress_callback=_on_progress,
        )

        # 执行 Harness。ContextVar 会把当前 Gateway 调用关联到 task/dimension，
        # 不记录 Prompt 或网页正文。
        harness_started_at = time.perf_counter()
        with task_execution_metrics.model_call_context(
            task_id=task_id,
            dimension=dimension,
            stage="harness_execution",
        ):
            result = harness.execute()

        # 映射 DimensionStatus 到返回结果的大写状态；任务本身仍保持 RUNNING，
        # 等全部维度、报告生成和必要审计完成后才由多维入口写入终态。
        _status_to_db = {
            DimensionStatus.COMPLETED: "COMPLETED",
            DimensionStatus.FAILED: "FAILED",
            DimensionStatus.INSUFFICIENT: "FAILED",
            DimensionStatus.SUSPENDED: "RUNNING",
        }
        db_status = _status_to_db.get(result.status, result.status.value.upper())
        dimension_status = db_status
        task_execution_metrics.record_stage_duration(
            task_id=task_id,
            dimension=dimension,
            stage="harness_execution",
            duration_seconds=time.perf_counter() - harness_started_at,
            status=db_status,
        )

        # 根据状态设置 error_message
        error_message = None
        if result.status == DimensionStatus.INSUFFICIENT:
            error_message = "搜索数据不足：多轮迭代后仍未获取到足够证据，请检查搜索 API Key 是否有效"
        elif result.status == DimensionStatus.FAILED:
            error_message = getattr(result, "error_message", None) or "Harness 执行异常"

        task_status, current_stage, progress = _dimension_task_progress_update(
            dimension=dimension,
            result_status=result.status,
        )
        update_task_status(
            task_id,
            task_status,
            current_stage=current_stage,
            progress=progress,
            error_message=error_message,
        )

        # 记录结果
        append_task_log(
            task_id,
            f"harness_{dimension}",
            f"Harness 执行完成：status={db_status}, "
            f"score={result.final_quality_score:.2f}, evidences={len(result.evidences)}",
            "INFO"
        )

        # 保存证据到 DB（含快照和可信度评分）
        evidence_write_error = None
        evidence_persisted_count = 0
        if result.evidences and db_session:
            evidence_persistence_started_at = time.perf_counter()
            try:
                snapshot_svc = SnapshotService()
                for ev in result.evidences:
                    db_evidence = DBEvidence(
                        task_id=task_id,
                        dimension=ev.dimension,
                        title=ev.title[:500],
                        snippet=ev.snippet[:1000] if ev.snippet else "",
                        url=ev.url,
                        source_type=ev.source_type,
                        published_at=ev.published_at,
                    )
                    if ev.metadata:
                        meta = dict(ev.metadata)
                        # WBS-6: 取出原始内容用于快照，不存入 DB metadata
                        raw_content = meta.pop("_raw_content", None)
                        db_evidence.meta_data = meta

                        # source_reliability 评分
                        db_evidence.source_reliability = score_source_reliability(
                            ev.url, ev.source_type
                        ).value

                        # Snapshot 保存（失败不阻塞证据落库）
                        if raw_content:
                            try:
                                snapshot_meta = snapshot_svc.save_snapshot(
                                    evidence_id=db_evidence.id,
                                    task_id=task_id,
                                    content=raw_content,
                                    content_type="text",
                                    captured_at=ev.captured_at,
                                )
                                if snapshot_meta:
                                    db_evidence.content_hash = snapshot_meta.content_hash
                                    db_evidence.raw_text_path = snapshot_meta.relative_path
                                    db_evidence.snapshot_size = snapshot_meta.size_bytes
                                    db_evidence.snapshot_retention_until = snapshot_meta.retention_until
                                    db_evidence.fetched_at = ev.captured_at
                            except Exception as snap_err:
                                logger.warning(
                                    f"快照保存失败（证据落库继续）: ev={db_evidence.id} — {snap_err}"
                                )
                    else:
                        # 无 metadata 时仍做 source_reliability
                        db_evidence.source_reliability = score_source_reliability(
                            ev.url, ev.source_type
                        ).value

                    db_session.add(db_evidence)
                db_session.commit()
                evidence_persisted_count = len(result.evidences)
            except Exception as e:
                db_session.rollback()
                logger.error(
                    f"[HarnessWorker] 证据落库失败: task_id={task_id}, "
                    f"dimension={dimension}, evidence_count={len(result.evidences)}, "
                    f"error={e}",
                    exc_info=True,
                )
                append_task_log(
                    task_id,
                    f"harness_{dimension}",
                    f"证据落库失败: {len(result.evidences)} 条证据未写入 DB，原因: {e}",
                    "ERROR",
                )
                # 将失败信息追加到返回结果中
                evidence_write_error = f"证据落库失败: {e}"
            finally:
                task_execution_metrics.record_stage_duration(
                    task_id=task_id,
                    dimension=dimension,
                    stage="evidence_persistence",
                    duration_seconds=time.perf_counter() - evidence_persistence_started_at,
                    status="success" if evidence_write_error is None else "failed",
                )

        state = harness.state
        task_execution_metrics.record_funnel(
            task_id=task_id,
            dimension=dimension,
            candidate_found=len(state.search_results),
            candidate_fetched=sum(1 for item in state.search_results if item.raw_content),
            evidence_produced=len(result.evidences),
            evidence_persisted=evidence_persisted_count,
        )
        task_execution_metrics.record_token_usage(
            task_id=task_id,
            dimension=dimension,
            token_breakdown=harness.token_tracker.get_status().get("breakdown", {}),
        )

        return {
            "task_id": task_id,
            "dimension": dimension,
            "status": db_status,
            "quality_score": result.final_quality_score,
            "evidences_count": len(result.evidences),
            "error_message": evidence_write_error,
            "token_usage": harness.token_tracker.get_status()
        }

    except Exception as e:
        logger.error(f"[HarnessWorker] 执行失败：{e}", exc_info=True)
        update_task_status(
            task_id,
            "RUNNING",
            current_stage=f"harness_{dimension}_failed",
            progress=90,
            error_message=str(e)
        )
        append_task_log(task_id, f"harness_{dimension}", f"Harness 失败：{e}", "ERROR")
        raise
    finally:
        task_execution_metrics.record_stage_duration(
            task_id=task_id,
            dimension=dimension,
            stage="dimension_total",
            duration_seconds=time.perf_counter() - dimension_started_at,
            status=dimension_status,
        )
        if db_session:
            db_session.close()


# ══════════════════════════════════════════════════════════════════════════════
# WBS-10: 审计管线辅助函数
# ══════════════════════════════════════════════════════════════════════════════

def _run_audit_pipeline(
    db,
    task_id: str,
    report_id,
    report_content: str,
    extracted_claims: list[dict],
    db_evidences: list,
    company_name: str = "",
    demand_direction: str = "",
) -> AuditFindings:
    """执行完整的审计管线：EvidenceAuditor → SkepticAgent → Severity Triage。

    Returns:
        AuditFindings — 聚合审计结果
    """
    unpersisted_indexes = [
        index for index, evidence in enumerate(db_evidences)
        if not getattr(evidence, "id", None)
    ]
    if unpersisted_indexes:
        raise ValueError(
            "审计输入包含尚未落库的 Evidence UUID: "
            + ",".join(str(index) for index in unpersisted_indexes)
        )

    from app.agents.agents.auditor_agent import EvidenceAuditorAgent
    from app.agents.agents.skeptic_agent import SkepticAgent
    from app.agents.audit_selection import select_report_audit_context

    logger.info(f"[AuditPipeline] 开始审计: task={task_id}, evidences={len(db_evidences)}, claims={len(extracted_claims)}")

    # ── Step 1: 构建 claim → evidence 映射 ──────────────────────────────
    # 收集每个 evidence 被哪些 claim 引用
    ev_to_claims: dict[str, list[str]] = {}
    for claim in extracted_claims:
        for ev_id in claim.get("evidence_ids", []):
            ev_str = str(ev_id)
            if ev_str not in ev_to_claims:
                ev_to_claims[ev_str] = []
            ev_to_claims[ev_str].append(claim.get("claim", "")[:200])

    # ── Step 2: EvidenceAuditorAgent ────────────────────────────────────
    auditor = EvidenceAuditorAgent(model=None)  # 使用默认模型

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

        # 构建证据的结论上下文
        related_claims = ev_to_claims.get(ev_id_str, [])
        if related_claims:
            claim_contexts[ev_id_str] = "；".join(related_claims)
        else:
            claim_contexts[ev_id_str] = f"{company_name} - {demand_direction}"

    selection = select_report_audit_context(
        evidence_items=evidence_dicts,
        claims=extracted_claims,
    )
    if selection.missing_evidence_ids:
        raise ValueError(
            "报告引用了不存在的 Evidence，审计已拒绝执行: "
            + ",".join(selection.missing_evidence_ids)
        )
    selected_ids = {item["id"] for item in selection.evidence_items}
    selected_evidence_dicts = [
        evidence for evidence in evidence_dicts
        if evidence["id"] in selected_ids
    ]
    selected_claim_contexts = {
        evidence_id: claim_contexts[evidence_id]
        for evidence_id in selected_ids
        if evidence_id in claim_contexts
    }
    evidence_audits = []
    configured_model_version = auditor.configured_model_version
    for start in range(0, len(selected_evidence_dicts), 8):
        batch = selected_evidence_dicts[start:start + 8]
        reusable = load_reusable_evidence_audits(
            db,
            [item["id"] for item in batch],
            audit_policy_version=auditor.policy_version,
            model_version=configured_model_version,
        )
        pending_batch = [item for item in batch if item["id"] not in reusable]
        batch_results = dict(reusable)
        if pending_batch:
            batch_result = auditor.audit_referenced_batch(
                evidences=pending_batch,
                claim_contexts=selected_claim_contexts,
                task_context=f"{company_name} - {demand_direction}",
            )
            provider = batch_result.provider.strip()
            model = batch_result.model.strip()
            actual_model_version = f"{provider}:{model}" if provider and model else None
            persist_evidence_audits(
                db,
                list(batch_result.results),
                audit_policy_version=auditor.policy_version,
                model_version=actual_model_version,
            )
            batch_results.update({str(result.evidence_id): result for result in batch_result.results})
        evidence_audits.extend(batch_results[item["id"]] for item in batch)

    # ── Step 3: SkepticAgent ───────────────────────────────────────────
    # 构建 ClaimWithEvidence 列表
    claims_with_evidence: list[ClaimWithEvidence] = []
    for claim in extracted_claims:
        claim_ev_ids = claim.get("evidence_ids", [])
        claim_ev_strs = [str(eid) for eid in claim_ev_ids]

        # 找到该 claim 引用的证据审计结果
        claim_audit_results = [
            ear for ear in evidence_audits
            if str(ear.evidence_id) in claim_ev_strs
        ]

        # 构建证据摘要
        evidence_summaries: list[dict] = []
        for ev in db_evidences:
            if str(ev.id) in claim_ev_strs:
                evidence_summaries.append({
                    "title": ev.title or "",
                    "snippet": (ev.snippet or "")[:300],
                })

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

    # ── Step 4: Severity Triage ────────────────────────────────────────
    severity, fatal_list, major_list, minor_list = triage_aggregate(claim_audits)

    # WBS-20a: 构建 severity_map 供持久化
    severity_map: dict[str, str] = {}
    for ca in fatal_list:
        severity_map[ca.claim_id] = Severity.FATAL.value
    for ca in major_list:
        severity_map[ca.claim_id] = Severity.MAJOR.value
    for ca in minor_list:
        severity_map[ca.claim_id] = Severity.MINOR.value

    # 落库 claim_audits（含 severity）
    try:
        persist_claim_audits(db, report_id, claim_audits, severity_map=severity_map)
    except Exception as e:
        logger.error(f"[AuditPipeline] claim_audits 落库失败: {e}")

    # 构建 Re-Plan 建议
    re_plan_suggestions = ""
    if fatal_list:
        fatal_texts = [c.claim_text[:100] for c in fatal_list]
        re_plan_suggestions = "fatal claims: " + "; ".join(fatal_texts)
    elif major_list:
        major_notes = [c.skeptic_notes[:100] for c in major_list]
        re_plan_suggestions = "major issues: " + "; ".join(major_notes)

    findings = AuditFindings(
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

    logger.info(
        f"[AuditPipeline] 审计完成: severity={severity.value}, "
        f"fatal={len(fatal_list)}, major={len(major_list)}, minor={len(minor_list)}"
    )
    return findings


def _apply_degraded_expression(
    report_content: str,
    findings: AuditFindings,
) -> str:
    """WBS-10.8: 在报告中有问题的 claim 前插入置信度标记。

    Args:
        report_content: 原始报告 Markdown 文本
        findings: 审计管线输出的 AuditFindings

    Returns:
        修改后的报告文本
    """
    all_problematic = findings.fatal_claims + findings.major_claims + findings.minor_claims

    for claim in all_problematic:
        claim_text = claim.claim_text.strip()
        if not claim_text or claim_text not in report_content:
            continue

        if claim in findings.fatal_claims:
            marker = "[置信度: 低 - 证据不足] "
        elif claim in findings.major_claims:
            marker = "[置信度: 中低 - 证据偏弱] "
        else:
            marker = "[置信度: 中低] "

        # 避免重复标记
        if claim_text.startswith("[置信度:"):
            continue

        report_content = report_content.replace(claim_text, marker + claim_text)

    return report_content


# ══════════════════════════════════════════════════════════════════════════════
# WBS-20a: Re-Plan 闭环辅助函数
# ══════════════════════════════════════════════════════════════════════════════

_MAX_CLAIM_REPLAN = 2   # 同一 claim 最多 Re-Plan 次数
_MAX_DIM_REPLAN = 3     # 同一报告最多维度级 Re-Plan 轮次


def _generate_replan_queries(
    claim_text: str,
    skeptic_notes: str,
    suggested_revision: str,
    company_name: str,
    demand_direction: str,
) -> list[str]:
    """WBS-20a: 为证据不足的 claim 生成定向补充检索词。

    使用 LLM 分析 claim 的证据缺口，生成 2-4 个高度定向的搜索查询。
    """
    prompt = (
        f"## 任务：为证据不足的结论生成定向补充检索词\n\n"
        f"**目标企业**: {company_name}\n"
        f"**需求方向**: {demand_direction}\n\n"
        f"**原结论**: {claim_text[:300]}\n\n"
        f"**审计问题**: {skeptic_notes[:300]}\n\n"
        f"**建议修正**: {suggested_revision[:300] if suggested_revision else '无'}\n\n"
        f"请根据上述审计发现，生成 2-4 个高度定向的搜索查询词，"
        f"专门用于补充该结论缺失的证据。"
        f"搜索词应具体、可验证，包含关键实体名称、时间范围或数据点。"
        f"直接输出为 JSON 数组字符串，不要包含其他内容。"
    )

    try:
        response = _gateway_client.infer(
            prompt=prompt,
            system_prompt="你是搜索策略专家。请生成精确的定向检索词。仅输出 JSON 字符串数组。",
            model=None,
            response_format={"type": "json_object"},
            temperature=0.3,
        )
        import json as _json
        parsed = _json.loads(response["content"])
        queries = parsed if isinstance(parsed, list) else parsed.get("queries", [])
        if not queries and isinstance(parsed, dict):
            queries = [v for v in parsed.values() if isinstance(v, str)]
        logger.info(f"[RePlan] 生成 {len(queries)} 个定向检索词: {queries[:3]}")
        return queries[:4]
    except Exception as e:
        logger.error(f"[RePlan] 生成检索词失败: {e}")
        # 降级：使用 claim 原文前 200 字符构建简单查询
        fallback = f"{company_name} {claim_text[:200]}"
        return [fallback]


def _run_supplementary_search(
    task_id: str,
    company_name: str,
    demand_direction: str,
    claims_to_replan: list,
    db,
) -> list:
    """WBS-20a: 为需要 Re-Plan 的 claims 执行补充搜索。

    对每条 claim 生成定向检索词 → 搜索 → 抓取 → 提取 → 落库。
    返回新落库的 Evidence ORM 对象列表。
    """
    from app.tools.search_client import SearchClient
    from app.tools.fetch_client import FetchClient
    from app.agents.agents.extractor_agent import ExtractorAgent

    search_client = SearchClient()
    fetch_client = FetchClient()
    extractor = ExtractorAgent(llm_client=_gateway_client)

    all_new_evidences: list = []

    for claim in claims_to_replan:
        claim_text = claim.claim_text if hasattr(claim, "claim_text") else str(claim)
        skeptic_notes = getattr(claim, "skeptic_notes", "") or ""
        suggested_revision = getattr(claim, "suggested_revision", "") or ""

        queries = _generate_replan_queries(
            claim_text=claim_text,
            skeptic_notes=skeptic_notes,
            suggested_revision=suggested_revision,
            company_name=company_name,
            demand_direction=demand_direction,
        )

        for query in queries[:2]:  # 每个 claim 最多用前 2 个检索词
            try:
                search_results = search_client.search(query, limit=3)
                if not search_results:
                    continue

                # 抓取搜索结果内容
                for sr in search_results[:3]:
                    try:
                        fetched = fetch_client.fetch(sr.url)
                        if fetched and fetched.get("content"):
                            sr.raw_content = fetched["content"]
                    except Exception:
                        pass

                # 提取证据
                evidences = extractor.execute(
                    search_results,
                    must_extract=["title", "snippet", "source", "date"],
                    dimension="supplementary",
                )

                # 落库
                from app.evidence.snapshot_service import SnapshotService
                from app.evidence.source_reliability import score_source_reliability
                snapshot_svc = SnapshotService()

                for ev in evidences:
                    db_ev = DBEvidence(
                        task_id=task_id,
                        dimension="supplementary",
                        title=ev.title[:500],
                        snippet=ev.snippet[:1000] if ev.snippet else "",
                        url=ev.url,
                        source_type=ev.source_type or "supplementary",
                        published_at=ev.published_at,
                        source_reliability=score_source_reliability(
                            ev.url, ev.source_type or "supplementary"
                        ).value,
                    )
                    if ev.metadata:
                        meta = dict(ev.metadata)
                        raw_content = meta.pop("_raw_content", None)
                        db_ev.meta_data = meta
                        if raw_content:
                            try:
                                snap_meta = snapshot_svc.save_snapshot(
                                    evidence_id=db_ev.id,
                                    task_id=task_id,
                                    content=raw_content,
                                    content_type="text",
                                    captured_at=ev.captured_at,
                                )
                                if snap_meta:
                                    db_ev.content_hash = snap_meta.content_hash
                                    db_ev.raw_text_path = snap_meta.relative_path
                                    db_ev.snapshot_size = snap_meta.size_bytes
                            except Exception:
                                pass
                    db.add(db_ev)
                    all_new_evidences.append(db_ev)

                    logger.info(
                        f"[RePlan] 补充证据落库: {ev.title[:60]}... "
                        f"来自: {ev.url[:80]}"
                    )
            except Exception as e:
                logger.warning(f"[RePlan] 搜索/提取失败 (query={query[:60]}): {e}")

    if all_new_evidences:
        try:
            db.flush()
            logger.info(
                f"[RePlan] 补充搜索完成: {len(all_new_evidences)} 条新证据, "
                f"覆盖 {len(claims_to_replan)} 条有问题的 claim"
            )
        except Exception as e:
            logger.error(f"[RePlan] 补充证据 flush 失败: {e}")

    return all_new_evidences


def _run_replan_cycle(
    db,
    task_id: str,
    report_id,
    report_content: str,
    extracted_claims: list[dict],
    db_evidences: list,
    company_name: str,
    demand_direction: str,
    initial_findings: AuditFindings,
) -> tuple[str, AuditFindings, int]:
    """WBS-20a: 对审计发现的问题执行 Re-Plan 循环。

    流程:
    1. 筛选需要 Re-Plan 的 claims (fatal + major)
    2. 检查 Re-Plan 预算 (per-claim ≤ 2, per-dim ≤ 3)
    3. 定向补充搜索 → 提取新证据
    4. 用新证据重新合成受影响段落 → 重新审计
    5. 重复直到无限或预算耗尽

    Returns:
        (更新后的 report_content, 最终 AuditFindings, Re-Plan 轮数)
    """
    replan_round = 0
    current_findings = initial_findings
    current_report = report_content
    current_claims = list(extracted_claims)
    all_evidences = list(db_evidences)

    while replan_round < _MAX_DIM_REPLAN:
        # 筛选需要 Re-Plan 的 claims
        claims_to_replan = (
            list(current_findings.fatal_claims)
            + list(current_findings.major_claims)
        )

        if not claims_to_replan:
            logger.info(f"[RePlan] 第 {replan_round + 1} 轮: 无问题 claim，退出循环")
            break

        # 检查 per-claim Re-Plan 预算
        replannable = []
        skipped = 0
        for ca in claims_to_replan:
            retry_count = count_claim_retries(db, report_id, ca.claim_text)
            if retry_count < _MAX_CLAIM_REPLAN:
                replannable.append(ca)
            else:
                skipped += 1
                logger.warning(
                    f"[RePlan] claim '{ca.claim_text[:60]}...' "
                    f"已达 Re-Plan 上限 ({_MAX_CLAIM_REPLAN})，降级处理"
                )

        if skipped > 0:
            logger.info(f"[RePlan] {skipped} 条 claim 超过 Re-Plan 上限，跳过")

        if not replannable:
            logger.info(f"[RePlan] 第 {replan_round + 1} 轮: 无可 Re-Plan 的 claim，退出循环")
            break

        replan_round += 1
        logger.info(
            f"[RePlan] 第 {replan_round}/{_MAX_DIM_REPLAN} 轮: "
            f"对 {len(replannable)} 条 claim 执行补充搜索"
        )

        # 执行补充搜索
        new_evidences = _run_supplementary_search(
            task_id=task_id,
            company_name=company_name,
            demand_direction=demand_direction,
            claims_to_replan=replannable,
            db=db,
        )

        if not new_evidences:
            logger.warning(
                f"[RePlan] 第 {replan_round} 轮补充搜索未找到新证据，退出循环"
            )
            break

        # 将新证据加入集合
        all_evidences = list(db_evidences) + [
            e for e in new_evidences
            if not any(
                getattr(ee, "id", None) and getattr(e, "id", None) and
                str(ee.id) == str(e.id)
                for ee in db_evidences
            )
        ]

        # 用新证据重新生成受影响段落的报告
        new_evidence_texts = []
        for ev in new_evidences:
            new_evidence_texts.append(
                f"- [ID: {getattr(ev, 'id', 'new')}] [补充证据] {getattr(ev, 'title', '')}\n"
                f"  来源: {getattr(ev, 'url', '')}\n"
                f"  摘要: {getattr(ev, 'snippet', '')[:300]}"
            )

        replan_prompt = (
            f"## 补充证据（Re-Plan 第 {replan_round} 轮）\n\n"
            f"以下是为了补齐以下结论的证据缺口而专门检索的新证据：\n\n"
            + "\n".join(new_evidence_texts) + "\n\n"
            f"## 需要修正的结论\n"
        )
        for ca in replannable:
            replan_prompt += (
                f"- {ca.claim_text[:200]}\n"
                f"  审计意见: {ca.skeptic_notes[:150] if ca.skeptic_notes else '证据不足'}\n"
            )
        replan_prompt += (
            f"\n请基于新证据更新报告中受影响的部分。"
            f"如果新证据仍然不足，请在结论前标注 [置信度: 低 - 补充检索后仍证据不足]。"
            f"直接输出更新后的 Markdown 报告全文。"
        )

        try:
            prompt_path = os.path.join(
                os.path.dirname(__file__), "..", "agents", "prompts", "synthesizer.md"
            )
            system_prompt = ""
            if os.path.exists(prompt_path):
                with open(prompt_path, "r", encoding="utf-8") as f:
                    system_prompt = f.read()
            else:
                system_prompt = "你是报告汇总代理。请基于补充证据更新报告。输出 Markdown。"

            response = _gateway_client.infer(
                prompt=current_report + "\n\n---\n\n" + replan_prompt,
                system_prompt=system_prompt,
            )
            current_report = response["content"]
            logger.info(
                f"[RePlan] 第 {replan_round} 轮报告更新完成: "
                f"{len(current_report)} 字符"
            )
        except Exception as e:
            logger.error(f"[RePlan] 报告更新失败: {e}，保留原报告")
            break

        # 重新提取 claims 并审计
        from app.agents.claim_reference_validator import _extract_claims_from_markdown
        current_claims = _extract_claims_from_markdown(current_report, {})

        # 重新审计
        current_findings = _run_audit_pipeline(
            db=db,
            task_id=task_id,
            report_id=report_id,
            report_content=current_report,
            extracted_claims=current_claims,
            db_evidences=all_evidences,
            company_name=company_name,
            demand_direction=demand_direction,
        )

        if current_findings.severity == Severity.ACCEPTABLE:
            logger.info(f"[RePlan] 第 {replan_round} 轮审计通过，退出循环")
            break
        elif current_findings.severity == Severity.MINOR:
            logger.info(f"[RePlan] 第 {replan_round} 轮仅剩 minor 问题，退出循环")
            break

    if replan_round >= _MAX_DIM_REPLAN and (
        current_findings.severity in (Severity.FATAL, Severity.MAJOR)
    ):
        logger.warning(
            f"[RePlan] 维度级 Re-Plan 次数已达上限 ({_MAX_DIM_REPLAN})，"
            f"剩余问题将降级标记"
        )
        # 对剩余 unrepaired claims 应用降级表达
        current_report = _apply_degraded_expression(current_report, current_findings)

    return current_report, current_findings, replan_round


def _synthesize_harness_report(
    task_id: str,
    company_name: str,
    demand_direction: str,
    dimensions: list[str],
    results: dict,
    model: Optional[str] = None,
    domain_context: dict | None = None,
    harness_config: Optional[dict] = None,
) -> None:
    """
    合成 Harness 执行报告并写入数据库

    从 DB 读取所有已保存的 Evidence，调用 LLM 生成 Markdown 报告，
    写入 reports 表，供 GET /api/reports/{task_id} 返回。
    """
    db = SessionLocal()
    try:
        # 1. 从 DB 读取证据
        db_evidences = db.query(DBEvidence).filter(DBEvidence.task_id == task_id).all()

        # 2. 构建 LLM 提示词（结构化 JSON，包含 evidence id 供引用）
        evidence_text_parts = []
        evidence_items_for_prompt = []
        for ev in db_evidences:
            evidence_text_parts.append(
                f"- [ID: {ev.id}] [{ev.dimension}] {ev.title}\n  来源: {ev.url}\n  摘要: {ev.snippet[:800]}"
            )
            evidence_items_for_prompt.append({
                "id": str(ev.id),
                "dimension": ev.dimension,
                "title": ev.title[:200],
                "snippet": ev.snippet[:500] if ev.snippet else "",
                "url": ev.url or "",
                "captured_at": ev.captured_at.isoformat() if ev.captured_at else "",
            })

        evidence_json = json.dumps(evidence_items_for_prompt, ensure_ascii=False, indent=2)

        dimension_summary = []
        for dim, result in results.items():
            status = result.get("status", "unknown")
            score = result.get("quality_score", 0)
            ev_count = result.get("evidences_count", 0)
            error_msg = result.get("error_message", "")
            parts = [f"- {dim}: 状态={status}, 评分={score:.2f}, 证据数={ev_count}"]
            if error_msg:
                parts.append(f", 错误信息={error_msg[:200]}")
            dimension_summary.append("".join(parts))

        failed_dims = [
            dim for dim, r in results.items() if r.get("status") == "FAILED"
        ]
        data_shortage_note = ""
        if failed_dims:
            dim_list = "\n".join(f"- {d}" for d in failed_dims)
            data_shortage_note = (
                "\n\n**注意: 以下维度数据不足, 请在报告中标注[数据不足, 无法给出确定结论]:**\n"
                + dim_list +
                "\n对于数据不足的维度, 不要编造结论."
            )

        prompt = (
            f"公司: {company_name}\n"
            f"需求方向: {demand_direction}\n"
            f"分析维度: {', '.join(dimensions)}\n\n"
            f"各维度执行情况:\n" + "\n".join(dimension_summary) + "\n"
            f"{data_shortage_note}\n\n"
            f"结构化证据数据 (JSON, 共{len(db_evidences)}条):\n{evidence_json}"
        )

        # WBS-11: 招标投标战略分析（仅 bidding_information 维度触发）
        bidding_analysis = None
        raw_data = {"results": {k: v for k, v in results.items()}}
        if "bidding_information" in dimensions:
            bidding_evidences = [
                ev for ev in db_evidences
                if ev.dimension == "bidding_information"
            ]
            if bidding_evidences:
                try:
                    from app.agents.expert.bidding_agent import BiddingAnalysisAgent
                    analyst = BiddingAnalysisAgent(
                        llm_client=_gateway_client,
                        model=model,
                    )
                    bidding_analysis = analyst.execute(
                        company_name=company_name,
                        demand_direction=demand_direction,
                        evidences=bidding_evidences,
                    )
                    raw_data["bidding_analysis"] = bidding_analysis.model_dump(mode="json")
                    # 注入分析结果到合成 prompt
                    prompt += (
                        f"\n\n招标战略分析结果 (JSON):\n"
                        f"{bidding_analysis.model_dump_json(indent=2)}"
                    )
                    logger.info(
                        f"[HarnessWorker] BiddingAnalysis 完成: "
                        f"opportunity={bidding_analysis.opportunity_type.value}, "
                        f"projects={len(bidding_analysis.recent_projects)}, "
                        f"suppliers={len(bidding_analysis.supplier_landscape)}, "
                        f"risks={len(bidding_analysis.lockin_risks)}"
                    )
                except Exception as e:
                    logger.error(f"[HarnessWorker] BiddingAnalysisAgent 失败: {e}", exc_info=True)
                    # 非致命：报告合成继续，无招标分析增强

        # WBS-12: 政策合规战略分析（仅 policy_compliance 维度触发）
        policy_analysis = None
        if "policy_compliance" in dimensions:
            policy_evidences = [
                ev for ev in db_evidences
                if ev.dimension == "policy_compliance"
            ]
            if policy_evidences:
                try:
                    from app.agents.expert.policy_agent import PolicyComplianceAgent
                    analyst = PolicyComplianceAgent(
                        llm_client=_gateway_client,
                        model=model,
                    )
                    policy_analysis = analyst.execute(
                        company_name=company_name,
                        demand_direction=demand_direction,
                        evidences=policy_evidences,
                    )
                    raw_data["policy_analysis"] = policy_analysis.model_dump(mode="json")
                    # 注入分析结果到合成 prompt
                    prompt += (
                        f"\n\n政策合规分析结果 (JSON):\n"
                        f"{policy_analysis.model_dump_json(indent=2)}"
                    )
                    logger.info(
                        f"[HarnessWorker] PolicyAnalysis 完成: "
                        f"docs={len(policy_analysis.policy_timeline.documents)}, "
                        f"impacts={len(policy_analysis.business_impacts)}, "
                        f"gaps={len(policy_analysis.compliance_gaps)}, "
                        f"sys_reqs={len(policy_analysis.system_requirements)}"
                    )
                except Exception as e:
                    logger.error(f"[HarnessWorker] PolicyComplianceAgent 失败: {e}", exc_info=True)
                    # 非致命：报告合成继续，无政策分析增强

        # WBS-13: PlaywrightFieldAgent 只读网页体验
        # 触发条件: dimensions 含 field_research 或 harness_config.enable_field_agent 为 True
        should_run_field = "field_research" in dimensions or (
            harness_config and harness_config.get("enable_field_agent")
        )
        field_observation = None
        if should_run_field:
            try:
                from app.agents.expert.field_agent import PlaywrightFieldAgent
                from app.agents.schemas.field_agent_schema import ExternalTaskPackage
                from app.evidence.snapshot_service import SnapshotService

                # 从 domain_context 或 evidence 中提取目标 URL
                target_url = None
                if isinstance(domain_context, dict):
                    target_url = domain_context.get("website", "")
                if not target_url:
                    target_url = _extract_website_from_evidences(db_evidences)

                if target_url:
                    agent = PlaywrightFieldAgent(snapshot_service=SnapshotService())
                    task_pkg = ExternalTaskPackage(
                        target_url=target_url,
                        company_name=company_name,
                        task_description=f"调查 {company_name} 的官网，寻找服务入口和产品信息",
                        max_pages=3,
                    )
                    field_observation = agent.execute(task_pkg, db_session=db, task_id=task_id)
                    raw_data["field_observation"] = field_observation.model_dump(mode="json")

                    # 观察产物转 Evidence 并落库
                    if field_observation.status in ("OK", "EMPTY") and field_observation.pages:
                        evidence_objs = agent.to_evidence_list(field_observation, task_id)
                        for ev_obj in evidence_objs:
                            db.add(ev_obj)
                        db.flush()
                        logger.info(
                            f"[HarnessWorker] FieldAgent Evidence 落库: "
                            f"{len(evidence_objs)} 条"
                        )
                        # 刷新 db_evidences 以包含新落库的证据
                        db_evidences = db.query(DBEvidence).filter(
                            DBEvidence.task_id == task_id
                        ).all()

                    # 截断 text_content 避免 prompt 过大（evidence 已独立落库，完整内容可查）
                    if field_observation.pages:
                        for page in field_observation.pages:
                            if len(page.text_content) > 6000:
                                page.text_content = (
                                    page.text_content[:3000]
                                    + "\n[...内容过长已截断...]\n"
                                    + page.text_content[-3000:]
                                )

                    # 注入观察结果到合成 prompt
                    prompt += (
                        f"\n\n网页体验观察结果 (JSON):\n"
                        f"{field_observation.model_dump_json(indent=2)}"
                    )
                    logger.info(
                        f"[HarnessWorker] FieldAgent 完成: status={field_observation.status}, "
                        f"pages={len(field_observation.pages)}, "
                        f"steps={len(field_observation.click_path)}"
                    )
                else:
                    logger.info(f"[HarnessWorker] field_research 维度无可用 URL，跳过 PlaywrightFieldAgent")
            except Exception as e:
                logger.error(f"[HarnessWorker] PlaywrightFieldAgent 失败: {e}", exc_info=True)
                # 非致命：报告合成继续，无网页体验增强

        # WBS-14: 全维度策略分析（跨维度综合）
        strategy_analysis = None
        if db_evidences:
            try:
                from app.agents.expert.strategy_agent import StrategyAnalysisAgent

                dimension_analyses = {}
                if bidding_analysis:
                    dimension_analyses["bidding_analysis"] = bidding_analysis
                if policy_analysis:
                    dimension_analyses["policy_analysis"] = policy_analysis
                if field_observation:
                    dimension_analyses["field_observation"] = field_observation

                strategist = StrategyAnalysisAgent(
                    llm_client=_gateway_client,
                    model=model,
                )
                strategy_analysis = strategist.execute(
                    company_name=company_name,
                    demand_direction=demand_direction,
                    dimensions=dimensions,
                    evidences=db_evidences,
                    dimension_analyses=dimension_analyses,
                )
                raw_data["strategy_analysis"] = strategy_analysis.model_dump(mode="json")

                # 注入策略分析结果到合成 prompt
                prompt += (
                    f"\n\n全维度策略分析结果 (JSON):\n"
                    f"{strategy_analysis.model_dump_json(indent=2)}"
                )
                logger.info(
                    f"[HarnessWorker] StrategyAnalysis 完成: "
                    f"score={strategy_analysis.opportunity_score:.0f}, "
                    f"confidence={strategy_analysis.confidence:.2f}, "
                    f"signals={len(strategy_analysis.signal_matrix.dimensions)}, "
                    f"correlations={len(strategy_analysis.signal_matrix.cross_correlations)}, "
                    f"supports={len(strategy_analysis.supporting_chains)}, "
                    f"counters={len(strategy_analysis.counter_chains)}, "
                    f"actions={len(strategy_analysis.action_plan)}"
                )
            except Exception as e:
                logger.error(f"[HarnessWorker] StrategyAnalysisAgent 失败: {e}", exc_info=True)
                # 非致命：报告合成继续，无策略分析增强

        # 3. 加载合成提示词
        prompt_path = os.path.join(
            os.path.dirname(__file__), "..", "agents", "prompts", "synthesizer.md"
        )
        system_prompt = ""
        if os.path.exists(prompt_path):
            with open(prompt_path, "r", encoding="utf-8") as f:
                system_prompt = f.read()
        else:
            system_prompt = "你是报告汇总代理。请基于输入的证据生成 Markdown 格式的潜在需求分析报告。"

        # WBS-22b: Profile 裁剪 + 破冰三板斧
        report_profile = domain_context.get("report_profile", "presales_standard") if isinstance(domain_context, dict) else "presales_standard"

        # 按 Profile 定义章节结构
        profile_chapters: dict[str, list[str]] = {
            "sales_brief": [
                "## 商机摘要", "## 关键发现（3 条以内）", "## 破冰话术", "## 下一步建议",
            ],
            "presales_standard": [
                "## 执行摘要", "## 各维度分析", "## 竞争格局", "## 破冰三板斧",
                "## 竞争锁定风险", "## 商机评估与建议",
            ],
            "technical_deep": [
                "## 执行摘要", "## 技术需求深度分析", "## 各维度详细分析",
                "## 系统架构与集成评估", "## 竞品技术对比", "## 合规与安全要求",
                "## 破冰三板斧", "## 竞争锁定风险", "## 商机评估与实施路线图",
            ],
            "management_summary": [
                "## 管理层摘要", "## 商机评分", "## 战略建议", "## 破冰三板斧",
                "## 风险提示", "## 下一步行动",
            ],
        }
        chapters = profile_chapters.get(report_profile, profile_chapters["presales_standard"])

        system_prompt += (
            "\n\n请直接输出 Markdown 格式的报告内容。"
            f"\n报告 Profile: {report_profile}，必须包含以下章节（按顺序）："
            + "".join(f"\n  {i+1}. {ch}" for i, ch in enumerate(chapters))
            + "\n\n每条关键结论必须在行尾标注证据引用，格式为 [ev:evidence_uuid]。"
            "\n没有证据支持的推断性内容请标注 [推测] 前缀。"
            "\n数据不足的维度请明确说明'该维度数据不足, 无法给出确定结论'。"
            "\n\n## 破冰三板斧要求："
            "\n必须生成三节破冰话术："
            "\n1. **Why Change** — 客户为什么需要改变现状（引用证据说明痛点/机会）"
            "\n2. **Why Us** — 为什么选择我们（差异化优势、行业案例、资质）"
            "\n3. **Call to Action** — 具体下一步行动（目标角色 + 开场话术 + 预期结果）"
            "\n\n## 竞争锁定风险要求："
            "\n如果证据中存在竞争锁定信号（供应商锁定/技术锁定/独家参数等），"
            "\n必须在报告中单独列出风险等级和应对策略。"
        )

        # 4. 调用 LLM 合成
        report_content = ""
        if db_evidences:
            try:
                response = _gateway_client.infer(
                    prompt=prompt,
                    system_prompt=system_prompt,
                    model=model,
                )
                report_content = response["content"]
                logger.info(f"[HarnessWorker] 报告合成成功，{len(report_content)} 字符")
            except Exception as e:
                logger.error(f"[HarnessWorker] 报告合成 LLM 调用失败: {e}")
                report_content = (
                    f"# {company_name} - {demand_direction} 潜在需求分析报告\n\n"
                    f"> 报告生成时 LLM 调用失败: {e}\n\n"
                    f"## 执行摘要\n\n"
                    + "\n".join(dimension_summary) + "\n\n"
                    f"## 收集到的证据 ({len(db_evidences)} 条)\n\n"
                    + "\n".join(evidence_text_parts[:50])
                )
        else:
            # 零证据诊断：区分搜索0、提取0、落库失败、任务中断
            diagnosis_lines = []
            all_zero_evidence = True
            for dim, result in results.items():
                dim_ev_count = result.get("evidences_count", 0)
                dim_status = result.get("status", "unknown")
                dim_error = result.get("error_message", "")

                if dim_ev_count > 0:
                    all_zero_evidence = False
                    diagnosis_lines.append(
                        f"- **{dim}**: 证据提取成功 ({dim_ev_count} 条) 但未落库，"
                        f"请检查证据落库日志"
                    )
                    if dim_error:
                        diagnosis_lines.append(f"  - 落库错误详情: {dim_error[:200]}")
                elif dim_status == "FAILED":
                    reason = dim_error[:200] if dim_error else "未获取到具体错误信息"
                    diagnosis_lines.append(
                        f"- **{dim}**: 执行失败，原因: {reason}"
                    )
                elif dim_status == "COMPLETED":
                    diagnosis_lines.append(
                        f"- **{dim}**: 执行完成但未产生证据，"
                        f"建议调整搜索词或扩展搜索源"
                    )
                else:
                    diagnosis_lines.append(
                        f"- **{dim}**: 状态={dim_status}，未产生证据，"
                        f"可能任务被中断或数据源无匹配结果"
                    )

            # 所有维度零证据时，追加排查指引
            troubleshooting = ""
            if all_zero_evidence and diagnosis_lines:
                troubleshooting = (
                    "\n\n## 排查建议\n\n"
                    "1. **检查搜索 API Key 配置**：确认 `.env` 中 `BOCHA_API_KEY`、`BING_API_KEY` "
                    "或 `TAVILY_API_KEY` 至少有一个有效的 API Key\n"
                    "2. **检查网络连通性**：确认服务器可访问外网（DuckDuckGo 作为最后回退需要外网）\n"
                    "3. **查看 Task Log**：访问 `GET /api/tasks/{task_id}/logs` 查看搜索执行详情\n"
                    "4. **尝试更换搜索源**：在 `.env` 中设置 `SEARCH_PROVIDER=bing` 切换主搜索源\n"
                    "5. **验证 LLM 网关**：确认命名 Provider 的 `LLM_PROVIDER_<NAME>_BASE_URL` 可正常访问，PlannerAgent 依赖 LLM 生成搜索词\n"
                )

            diagnosis_block = (
                "\n\n## 诊断信息\n\n"
                + "\n".join(diagnosis_lines) + "\n"
                + troubleshooting
            ) if diagnosis_lines else ""

            report_content = (
                f"# {company_name} - {demand_direction} 潜在需求分析报告\n\n"
                f"> 本次分析未收集到有效证据。\n\n"
                f"## 执行摘要\n\n" + "\n".join(dimension_summary) + "\n"
                f"{diagnosis_block}"
            )

        # 5. 构建结构化 evidence_index 并校验
        evidence_items = [
            {
                "id": str(ev.id),
                "dimension": ev.dimension,
                "title": ev.title,
                "snippet": ev.snippet,
                "url": ev.url,
                "captured_at": ev.captured_at.isoformat() if ev.captured_at else "",
            }
            for ev in db_evidences
        ]
        evidence_index_base = build_evidence_index_from_evidences(evidence_items)

        # 从报告内容提取 claims
        from app.agents.claim_reference_validator import _extract_claims_from_markdown
        extracted_claims = _extract_claims_from_markdown(report_content, {})

        final_evidence_index = {
            "count": len(db_evidences),
            "ids": [str(ev.id) for ev in db_evidences],
            "claims": extracted_claims,
            "evidence_items": evidence_index_base.get("items", []),
        }

        # 校验
        validation = validate_claim_references(task_id, report_content, final_evidence_index)
        if not validation.passed and db_evidences:
            # 重试一次 synthesis
            logger.warning(f"[HarnessWorker] 报告校验未通过，重试: {validation.to_dict()}")
            retry_prompt = (
                prompt + "\n\n[系统指令] 上次报告的问题是: "
                + "; ".join(v["reason"] for v in validation.violations[:3])
                + "\n请重新生成，确保每条关键结论都引用具体 evidence_id。"
            )
            try:
                retry_response = _gateway_client.infer(
                    prompt=retry_prompt,
                    system_prompt=system_prompt,
                    model=model,
                )
                report_content = retry_response["content"]
                extracted_claims = _extract_claims_from_markdown(report_content, {})
                final_evidence_index["claims"] = extracted_claims
                validation = validate_claim_references(task_id, report_content, final_evidence_index)
                logger.info(f"[HarnessWorker] 重试后校验: {validation.to_dict()}")
            except Exception as e:
                logger.error(f"[HarnessWorker] 报告重试失败: {e}")

        # 重试后仍校验失败：生成降级报告（仅当有证据时才覆盖，零证据保留诊断报告）
        if not validation.passed and db_evidences:
            logger.warning(
                f"[HarnessWorker] 报告校验最终未通过，生成降级报告: {validation.to_dict()}"
            )
            violation_reasons = "; ".join(
                v["reason"] for v in validation.violations[:5]
            )
            report_content = (
                f"# {company_name} - {demand_direction} 潜在需求分析报告\n\n"
                f"> **注意：本报告未通过证据校验，以下内容仅供参考。**\n"
                f"> 校验失败原因: {violation_reasons}\n\n"
                f"## 执行摘要\n\n"
                + "\n".join(dimension_summary) + "\n\n"
                f"## 收集到的证据 ({len(db_evidences)} 条)\n\n"
                + "\n".join(evidence_text_parts[:50]) + "\n\n"
                f"## 校验详情\n\n"
                f"- 总 claims: {validation.claims_total}\n"
                f"- 有效 claims: {validation.claims_valid}\n"
                f"- 违规项: {len(validation.violations)}\n"
            )
            final_evidence_index["validation"] = validation.to_dict()

        # 6. 入库（先创建/获取 report 以获取 report_id 供审计使用）
        existing = db.query(Report).filter(Report.task_id == task_id).first()
        if existing:
            existing.content_md = report_content
            existing.evidence_index = final_evidence_index
            report = existing
        else:
            report = Report(
                task_id=task_id,
                content_md=report_content,
                raw_data=raw_data,
                evidence_index=final_evidence_index,
            )
            db.add(report)
        db.flush()  # 获取 report.id 供审计使用

        # 7. WBS-10 + WBS-20a: 证据审计管线 + Re-Plan 闭环
        audit_findings = None
        if db_evidences and extracted_claims:
            try:
                # 初始审计
                audit_findings = _run_audit_pipeline(
                    db=db,
                    task_id=task_id,
                    report_id=report.id,
                    report_content=report_content,
                    extracted_claims=extracted_claims,
                    db_evidences=db_evidences,
                    company_name=company_name,
                    demand_direction=demand_direction,
                )

                if audit_findings.severity in (Severity.FATAL, Severity.MAJOR):
                    logger.warning(
                        f"[HarnessWorker] 审计发现问题: severity={audit_findings.severity.value}, "
                        f"fatal={len(audit_findings.fatal_claims)}, "
                        f"major={len(audit_findings.major_claims)}, "
                        f"minor={len(audit_findings.minor_claims)}, "
                        f"→ 启动 Re-Plan 闭环"
                    )

                    # WBS-20a: 执行 Re-Plan 循环
                    report_content, audit_findings, replan_rounds = _run_replan_cycle(
                        db=db,
                        task_id=task_id,
                        report_id=report.id,
                        report_content=report_content,
                        extracted_claims=extracted_claims,
                        db_evidences=db_evidences,
                        company_name=company_name,
                        demand_direction=demand_direction,
                        initial_findings=audit_findings,
                    )
                    logger.info(
                        f"[HarnessWorker] Re-Plan 完成: {replan_rounds} 轮, "
                        f"最终 severity={audit_findings.severity.value}"
                    )

                elif audit_findings.severity == Severity.MINOR:
                    # Minor 问题仅标记，不触发 Re-Plan
                    logger.info(
                        f"[HarnessWorker] 审计发现 minor 问题: "
                        f"{len(audit_findings.minor_claims)} 条，仅标记"
                    )
                    report_content = _apply_degraded_expression(report_content, audit_findings)

                # 更新报告和审计结果
                report.content_md = report_content
                final_evidence_index["audit"] = audit_findings.model_dump()
                report.evidence_index = final_evidence_index

            except Exception as e:
                logger.error(
                    f"[HarnessWorker] 强制审计管线/Re-Plan 失败，拒绝完成任务: {e}",
                    exc_info=True,
                )
                raise

        # 8. WBS-22a: 商机评分
        if db_evidences:
            try:
                from app.agents.opportunity_scorer import (
                    OpportunityScorer,
                    CounterEvidence,
                    CompetitionLockinRisk,
                )

                # 构建 counter_evidences（从审计管线获取）
                counter_list: list = []
                if audit_findings is not None:
                    af_dict = audit_findings if isinstance(audit_findings, dict) else audit_findings.model_dump()
                    for fc in af_dict.get("fatal_claims", []):
                        counter_list.append(CounterEvidence(
                            claim_text=str(fc.get("claim_text", ""))[:200],
                            severity="fatal",
                        ))
                    for mc in af_dict.get("major_claims", []):
                        counter_list.append(CounterEvidence(
                            claim_text=str(mc.get("claim_text", ""))[:200],
                            severity="major",
                        ))

                # 构建 lockin_risks（从 strategy_analysis 获取）
                lockin_list: list = []
                strategy_data = raw_data.get("strategy_analysis", {})
                if isinstance(strategy_data, dict):
                    for risk in strategy_data.get("competitive_risks", []):
                        if isinstance(risk, dict):
                            lockin_list.append(CompetitionLockinRisk(
                                risk_type=str(risk.get("risk_type", "")),
                                description=str(risk.get("description", "")),
                                likelihood=str(risk.get("likelihood", "medium")),
                            ))

                scorer = OpportunityScorer()
                sev_value = "acceptable"
                if audit_findings is not None:
                    sev_str = (
                        audit_findings.get("severity")
                        if isinstance(audit_findings, dict)
                        else getattr(audit_findings, "severity", None)
                    )
                    sev_value = (
                        sev_str.value
                        if hasattr(sev_str, "value")
                        else str(sev_str or "acceptable")
                    )

                score_result = scorer.score(
                    evidences=evidence_items,
                    counter_evidences=counter_list,
                    lockin_risks=lockin_list,
                    audit_severity=sev_value,
                )

                final_evidence_index["opportunity_score"] = {
                    "total_score": score_result.total_score,
                    "grade": score_result.grade.value,
                    "dimension_scores": [
                        {
                            "dimension": ds.dimension,
                            "weight": ds.weight,
                            "weighted_score": round(ds.weighted_score, 3),
                            "top_score": round(ds.top_score, 3),
                            "evidence_count": len(ds.evidence_scores),
                        }
                        for ds in score_result.dimension_scores
                    ],
                    "counter_penalty": score_result.counter_penalty,
                    "lockin_penalty": score_result.lockin_penalty,
                    "evidence_count": score_result.evidence_count,
                    "dimension_count": score_result.dimension_count,
                }
                report.evidence_index = final_evidence_index
                report.raw_data["opportunity_score"] = final_evidence_index["opportunity_score"]

                logger.info(
                    f"[HarnessWorker] 商机评分: score={score_result.total_score}, "
                    f"grade={score_result.grade.value}, "
                    f"counter_penalty={score_result.counter_penalty}, "
                    f"lockin_penalty={score_result.lockin_penalty}"
                )
            except Exception as e:
                logger.error(
                    f"[HarnessWorker] 商机评分失败（非致命，报告正常入库）: {e}",
                    exc_info=True,
                )

        db.commit()
        logger.info(
            f"[HarnessWorker] 报告已入库: task_id={task_id}, "
            f"evidences={len(db_evidences)}, claims={len(extracted_claims)}, "
            f"validation={validation.to_dict()}"
        )

    except Exception as e:
        db.rollback()
        logger.error(f"[HarnessWorker] 报告入库失败: {e}", exc_info=True)
    finally:
        db.close()


@celery_app.task(name="tasks.execute_multi_dimension_harness")
def execute_multi_dimension_harness(
    task_id: str,
    company_name: str,
    demand_direction: str,
    skill_id: str,
    domain_context: dict | None = None,
) -> dict:
    """
    执行多维度 Harness 任务（顺序执行，单维失败不阻断全局）

    Args:
        task_id: 任务 ID
        company_name: 公司名称
        demand_direction: 需求方向
        domain_context: 领域上下文（可选，默认空 dict）
        skill_id: 标准 SKILL.md 目录标识

    Returns:
        dict: 各维度执行结果汇总
    """
    # TEO-08-08：该入口仅负责创建持久化运行并投递工作单元；不再执行旧 Harness 引擎。
    from app.worker.execution_worker import start_task_execution

    started = start_task_execution(
        task_id=task_id,
        company_name=company_name,
        demand_direction=demand_direction,
        skill_id=skill_id,
        domain_context=domain_context,
    )
    return {
        "status": "QUEUED",
        "task_id": task_id,
        "run_id": str(started.run_id),
        "queued_unit_count": len(started.queued_units),
    }
