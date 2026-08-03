"""
AgentHarness - 单维度智能体编排器

管理单个维度智能体的完整执行循环：
1. Planning → 生成搜索策略
2. Research → 执行搜索
3. Extraction → 结构化提取
4. Evaluation → 质量评估
5. Reflection → 反思与策略调整

如果评估不通过，循环回到 Planning 阶段重新规划
"""

import hashlib
import json
import logging
from collections import Counter
from datetime import datetime, timezone
from typing import Optional

from .spec import (
    TaskSpec,
    DimensionGoal,
    DimensionStatus,
)

from .state import (
    ExecutionState,
    EvaluationResult,
    DimensionResult,
    SearchResult,
    Evidence,
)

from .token_tracker import TokenTracker
from .human_intervention import InterventionManager, HumanIntervention, InterventionType

# 真实智能体导入
from app.agents.agents.planner_agent import PlannerAgent
from app.agents.agents.research_agent import ResearchAgent, ResearchBatch
from app.agents.agents.candidate_screening_agent import (
    CandidateScreeningAgent,
    CandidateScreeningAttempt,
    CandidateScreeningContext,
)
from app.agents.harness.candidate_pipeline import (
    CandidateInput,
    build_candidate_set,
    interleave_candidate_set,
)
from app.agents.agents.extractor_agent import ExtractorAgent
from app.agents.eval.evidence_sufficiency import evaluate_evidence_sufficiency
from app.agents.harness.extraction_batch import (
    ExtractionCandidatePayload,
    plan_extraction_batches,
)
from app.agents.agents.evaluator_agent import EvaluatorAgent
from app.agents.agents.reflector_agent import ReflectorAgent

# WBS-10: 审计智能体
from app.agents.agents.auditor_agent import EvidenceAuditorAgent
from app.agents.agents.skeptic_agent import SkepticAgent
from app.agents.audit_severity import triage_aggregate
from app.agents.schemas.claim_schema import Severity, ClaimWithEvidence

# WBS-11: 招标分析智能体
from app.agents.expert.bidding_agent import BiddingAnalysisAgent

# WBS-12: 政策合规分析智能体
from app.agents.expert.policy_agent import PolicyComplianceAgent

# WBS-13: Playwright 字段智能体
from app.agents.expert.field_agent import PlaywrightFieldAgent

# WBS-14: 全维度策略分析智能体
from app.agents.expert.strategy_agent import StrategyAnalysisAgent

# 工具客户端导入
from app.tools.search_client import SearchClient
from app.tools.fetch_client import FetchClient
from app.tools.playwright_fetch_client import PlaywrightFetchClient
from app.llm.gateway_client import get_gateway_client
from app.config_center.research_config import (
    DEFAULT_CANDIDATE_SCREENING_CONFIG,
    get_candidate_screening_config,
    validate_candidate_screening_config,
)
from app.core.task_execution_metrics import task_execution_metrics
from app.skills.schema import EvidencePolicy

logger = logging.getLogger(__name__)


# ============================================================================
# Mock 智能体（用于测试和降级）
# ============================================================================

class MockPlannerAgent:
    """Mock Planning Agent（用于测试）"""

    def __init__(self, experience_memory=None):
        self.experience_memory = experience_memory

    def execute(
        self,
        company: str,
        direction: str,
        goal: str,
        reflection: Optional[str] = None,
        dimension: str = "",
        domain_context: Optional[dict] = None,
    ) -> dict:
        """生成 Mock 搜索计划"""
        base_queries = [
            f"{company} {direction} 招标 中标",
            f"{company} {direction} 采购 意向",
            f"{company} {direction} 项目 公示",
        ]

        if reflection:
            base_queries.append(f"{company} {direction} 需求")

        # 查询经验（如果可用）
        if self.experience_memory:
            self.experience_memory.query_similar(
                dimension=dimension,
                company_name=company,
                demand_direction=direction,
                goal=goal,
                limit=3,
            )

        return {
            "search_queries": base_queries,
            "strategy": "多关键词覆盖搜索"
        }

class MockResearchAgent:
    """Mock Research Agent（用于测试）"""

    def execute(
        self,
        search_queries: list[str],
        *,
        dimension: str,
        seed: str,
    ) -> ResearchBatch:
        """生成 Mock 搜索结果"""
        results = []
        for i, query in enumerate(search_queries):
            results.append(SearchResult(
                title=f"Mock 搜索结果 {i + 1}: {query}",
                url=f"https://example.com/result/{i}",
                snippet=f"这是搜索关键词 '{query}' 的模拟结果摘要...",
                source="mock"
            ))
        candidate_set = interleave_candidate_set(
            build_candidate_set(
                dimension=dimension,
                inputs=[
                    CandidateInput(
                        url=result.url,
                        content_source=result.source,
                        title=result.title,
                        snippet=result.snippet,
                        source_query=query,
                        source_rank=1,
                        published_at=result.date,
                    )
                    for query, result in zip(search_queries, results)
                ],
            ),
            seed=seed,
        )
        return ResearchBatch(
            candidate_set=candidate_set,
            search_results=tuple(results),
            raw_result_count=len(results),
            invalid_candidate_count=0,
        )


class MockExtractorAgent:
    """Mock Extraction Agent（用于测试）"""

    def execute(self, results: list[SearchResult], must_extract: list[str], dimension: str) -> list[Evidence]:
        """生成 Mock 证据"""
        evidences = []
        for i, result in enumerate(results[:3]):
            evidences.append(Evidence(
                dimension="mock",
                title=result.title,
                snippet=result.snippet,
                url=result.url,
                source_type="mock",
                metadata={"mock_field": "mock_value", "source": result.source}
            ))
        return evidences


class MockEvaluatorAgent:
    """Mock Evaluator Agent（用于测试）"""

    def evaluate_plan(self, plan: dict, goal: DimensionGoal) -> EvaluationResult:
        """Mock 计划评估"""
        query_count = len(plan.get("search_queries", []))
        score = min(1.0, query_count / 5.0)
        return EvaluationResult(
            stage="planning",
            passed=score >= 0.5,
            score=score,
            feedback=f"生成{query_count}个搜索词，{'充足' if score >= 0.5 else '不足'}"
        )

    def evaluate_research(self, results: list[SearchResult], goal: DimensionGoal) -> EvaluationResult:
        """Mock 研究评估"""
        result_count = len(results)
        score = min(1.0, result_count / 5.0)
        return EvaluationResult(
            stage="research",
            passed=score >= 0.5,
            score=score,
            feedback=f"搜索到{result_count}条结果，{'充足' if score >= 0.5 else '不足'}"
        )

    def evaluate_extraction(
        self,
        evidences: list[Evidence],
        goal: DimensionGoal
    ) -> EvaluationResult:
        """Mock 提取评估"""
        evidence_count = len(evidences)
        score = min(1.0, evidence_count / 3.0)
        return EvaluationResult(
            stage="extraction",
            passed=score >= 0.5,
            score=score,
            feedback=f"提取到{evidence_count}条证据，{'充足' if score >= 0.5 else '不足'}",
            suggestions=["建议增加搜索词变体"] if score < 0.5 else []
        )


class MockReflectorAgent:
    """Mock Reflector Agent（用于测试）"""

    def reflect_on_plan(self, plan: dict, feedback: str) -> str:
        """Mock 计划反思"""
        return f"反思：搜索计划需要改进。{feedback} 建议增加更多关键词变体。"

    def reflect_on_extraction(self, evidences: list[Evidence], feedback: str) -> str:
        """Mock 提取反思"""
        return f"反思：提取结果需要改进。{feedback} 建议调整搜索策略。"


# WBS-10: Mock 审计智能体（用于测试）
class MockAuditorAgent:
    """Mock EvidenceAuditor Agent（用于测试）— 总是返回 STRONG"""

    def audit_all(
        self,
        evidences: list[dict],
        claim_contexts: dict[str, str] | None = None,
        task_context: str = "",
    ) -> list:
        """Mock 审计：所有证据都 STRONG"""
        from app.agents.schemas.claim_schema import EvidenceAuditResult, SupportLevel
        from uuid import UUID

        results = []
        for ev in evidences:
            ev_id = ev.get("id", "")
            try:
                uid = UUID(str(ev_id))
            except (ValueError, TypeError):
                uid = UUID("00000000-0000-0000-0000-000000000000")
            results.append(EvidenceAuditResult(
                evidence_id=uid,
                support_level=SupportLevel.STRONG,
                reliability_score=0.9,
                relevance_score=0.9,
                freshness_score=0.9,
                audit_notes="Mock audit: all good.",
            ))
        return results


class MockSkepticAgent:
    """Mock Skeptic Agent（用于测试）— 总是返回 SUPPORTED"""

    def audit_claims(
        self,
        claims: list[ClaimWithEvidence],
        company_name: str = "",
        demand_direction: str = "",
    ) -> list:
        """Mock 审计：所有结论都 SUPPORTED"""
        from app.agents.schemas.claim_schema import ClaimAuditResult, SupportStatus, SkepticLevel

        results = []
        for claim in claims:
            results.append(ClaimAuditResult(
                claim_id=claim.claim_id,
                claim_text=claim.claim_text,
                support_status=SupportStatus.SUPPORTED,
                evidence_ids=claim.evidence_ids,
                skeptic_level=SkepticLevel.NONE,
                skeptic_notes="Mock skeptic: all good.",
                suggested_revision="",
            ))
        return results


# WBS-11: Mock 招标分析智能体（用于测试）
class MockBiddingAnalysisAgent:
    """Mock BiddingAnalysis Agent（用于测试）— 返回固定 POTENTIAL 结果"""

    def execute(
        self,
        company_name: str = "",
        demand_direction: str = "",
        evidences: list | None = None,
        task_context: str = "",
    ):
        """Mock 分析：返回 POTENTIAL 机会"""
        from app.agents.schemas.bidding_schema import (
            BiddingAnalysisResult,
            OpportunityType,
            ProcurementProfile,
            TechnicalFingerprint,
        )
        return BiddingAnalysisResult(
            company_name=company_name,
            demand_direction=demand_direction,
            opportunity_type=OpportunityType.POTENTIAL,
            opportunity_confidence=0.6,
            procurement_profile=ProcurementProfile(
                total_projects=3,
                estimated_total_value="Mock 预估 500-1000 万元/年",
                main_categories=["Mock 品类 A", "Mock 品类 B"],
                frequency_pattern="Mock 每年 Q1 集中采购",
            ),
            budget_cycle_analysis="Mock 预算周期分析：年度采购，Q1 集中招标",
            entry_window="Mock 切入窗口：预计下季度有新的招标",
            followup_strategy="Mock 跟进策略：提前联系采购部门，准备资质材料",
            analysis_notes="Mock bidding analysis for testing.",
        )


# WBS-12: Mock 政策合规分析智能体（用于测试）
class MockPolicyComplianceAgent:
    """Mock PolicyCompliance Agent（用于测试）— 返回固定 PolicyAnalysisResult"""

    def execute(
        self,
        company_name: str = "",
        demand_direction: str = "",
        evidences: list | None = None,
        task_context: str = "",
    ):
        """Mock 分析：返回固定政策分析结果"""
        from app.agents.schemas.policy_compliance_schema import (
            PolicyAnalysisResult,
            PolicyLevel,
            ConstraintStrength,
            PolicyDocument,
            PolicyTimeline,
            ComplianceGap,
            SystemRequirement,
        )
        return PolicyAnalysisResult(
            company_name=company_name,
            demand_direction=demand_direction,
            policy_timeline=PolicyTimeline(
                documents=[
                    PolicyDocument(
                        title="Mock 数据安全法实施条例",
                        issuer="国家互联网信息办公室",
                        doc_number="国信办发[2024]12号",
                        publish_date="2024-06-15",
                        effective_date="2025-01-01",
                        policy_level=PolicyLevel.NATIONAL,
                        constraint_strength=ConstraintStrength.MANDATORY,
                        applicable_objects=["关键信息基础设施运营者"],
                        key_clauses=["建立数据分类分级制度", "每年至少一次安全评估"],
                        source_reliability="A",
                    ),
                    PolicyDocument(
                        title="Mock 数字化转型三年行动计划",
                        issuer="XX省经信厅",
                        publish_date="2024-03-01",
                        policy_level=PolicyLevel.PROVINCIAL,
                        constraint_strength=ConstraintStrength.ENCOURAGING,
                        applicable_objects=["制造业企业", "服务业企业"],
                        key_clauses=["鼓励企业上云用数赋智", "对数字化转型项目给予补贴"],
                        source_reliability="A",
                    ),
                ],
                upcoming_deadlines=["2025-01-01: 数据安全法实施条例生效"],
                trend_direction="Mock 政策趋势：监管趋严，数字化鼓励政策持续加码",
            ),
            policy_level_summary="Mock 政策等级总结：涉及1项国家级强制政策、1项省部级鼓励政策",
            constraint_analysis="Mock 约束强度分析：1项强制要求（数据安全）、1项鼓励性政策（数字化转型补贴）",
            applicable_objects_analysis="Mock 适用对象分析：作为信息化服务商，数据安全法和数字化转型政策均适用",
            key_clauses_summary="Mock 关键条款：数据分类分级制度是强制要求，上云补贴是政策红利",
            compliance_gaps=[
                ComplianceGap(
                    gap_description="Mock 合规缺口：客户尚未建立完整的数据分类分级制度",
                    related_clause="数据安全法实施条例 第X条",
                    current_status="未知，需进一步了解客户现状",
                    remediation_deadline="2025-06-30",
                ),
            ],
            system_requirements=[
                SystemRequirement(
                    requirement_description="Mock 系统需求：数据安全治理平台，含分类分级、审计、脱敏功能",
                    driven_by_clauses=["数据安全法实施条例 - 数据分类分级"],
                    estimated_urgency="高",
                    system_category="数据安全",
                ),
            ],
            presales_leverage="Mock 售前推动：数据安全法即将生效，客户如果没有按期合规将面临处罚，可借此推动数据安全类项目",
            quotable_language=[
                "Mock 话术：'根据2025年1月生效的数据安全法实施条例，贵单位作为关键信息基础设施运营者，必须建立数据分类分级制度'",
            ],
            analysis_notes="Mock policy compliance analysis for testing.",
        )


# WBS-13: Mock Playwright 字段智能体（用于测试）
class MockPlaywrightFieldAgent:
    """Mock PlaywrightFieldAgent（用于测试）— 返回固定 ObservationArtifact，不调用 browserless"""

    def execute(self, task=None):
        """Mock 执行：返回固定网页观察产物"""
        from app.agents.schemas.field_agent_schema import (
            ExternalTaskPackage,
            ObservationArtifact,
            PageObservation,
            ClickStep,
        )
        target_url = task.target_url if task else "https://mock-company.example.com"
        company_name = task.company_name if task else "Mock科技公司"

        return ObservationArtifact(
            target_url=target_url,
            company_name=company_name,
            status="OK",
            pages=[
                PageObservation(
                    url=target_url,
                    title=f"{company_name} - 首页",
                    text_content="Mock首页内容：公司简介、服务介绍、产品展示、联系我们。这是一家专注于企业数字化转型的科技公司。",
                    screenshot_path="2026/07/task_mock/mock_homepage.png",
                    nav_links=[
                        {"text": "服务与产品", "href": "/services"},
                        {"text": "关于我们", "href": "/about"},
                        {"text": "联系我们", "href": "/contact"},
                    ],
                    captured_at="2026-07-06T10:30:00Z",
                ),
                PageObservation(
                    url=f"{target_url.rstrip('/')}/services",
                    title=f"{company_name} - 服务与产品",
                    text_content="Mock服务页内容：云计算服务、数据安全解决方案、IT运维管理、数字化转型咨询。",
                    screenshot_path="2026/07/task_mock/mock_services.png",
                    nav_links=[
                        {"text": "云计算", "href": "/services/cloud"},
                        {"text": "数据安全", "href": "/services/security"},
                        {"text": "IT运维", "href": "/services/ops"},
                    ],
                    captured_at="2026-07-06T10:30:15Z",
                ),
            ],
            click_path=[
                ClickStep(
                    step=0, action="navigate", url=target_url,
                    selector="", element_text="导航到目标网站",
                    timestamp="2026-07-06T10:30:00Z",
                ),
                ClickStep(
                    step=1, action="screenshot", url=target_url,
                    selector="", element_text="首页截图",
                    timestamp="2026-07-06T10:30:02Z",
                ),
                ClickStep(
                    step=2, action="click", url=f"{target_url.rstrip('/')}/services",
                    selector="a[href='/services']", element_text="服务与产品",
                    timestamp="2026-07-06T10:30:10Z",
                ),
                ClickStep(
                    step=3, action="screenshot", url=f"{target_url.rstrip('/')}/services",
                    selector="", element_text="页面截图: 服务与产品",
                    timestamp="2026-07-06T10:30:15Z",
                ),
            ],
            summary="Mock 网页体验：成功浏览 Mock科技公司 网站，访问 2 个页面: 首页、服务与产品",
        )

    def to_evidence_list(self, artifact, task_id: str) -> list:
        """Mock 转换：返回空列表（测试中不需真实 DB 对象）"""
        return []


# WBS-14: Mock 全维度策略分析智能体（用于测试）
class MockStrategyAnalysisAgent:
    """Mock StrategyAnalysisAgent（用于测试）— 返回固定 StrategyAnalysisOutput，不调用 LLM"""

    def execute(self, company_name="", demand_direction="", dimensions=None,
                evidences=None, dimension_analyses=None):
        """Mock 执行：返回固定策略分析输出"""
        from app.agents.schemas.strategy_schema import (
            StrategyAnalysisOutput,
            EvidenceSignalMatrix,
            EvidenceSignal,
            CrossSignalCorrelation,
            SupportChain,
            CounterChain,
            CompetitiveRisk,
            EntryScenario,
            IcebreakerStrategy,
            NextAction,
        )

        return StrategyAnalysisOutput(
            company_name=company_name or "Mock公司",
            demand_direction=demand_direction or "数字化转型",
            analyzed_dimensions=dimensions or ["bidding_information", "policy_compliance"],
            one_line_verdict="Mock: 存在明确商机，建议优先切入。招标需求活跃且政策环境有利。",
            opportunity_score=72.0,
            confidence=0.78,
            signal_matrix=EvidenceSignalMatrix(
                dimensions=[
                    EvidenceSignal(
                        dimension="bidding_information",
                        signal_type="positive",
                        evidence_count=5,
                        key_findings=["近5年有持续采购需求", "年度预算约3000万+"],
                        strength="moderate",
                        evidence_ids=["mock-bid-1", "mock-bid-2"],
                    ),
                    EvidenceSignal(
                        dimension="policy_compliance",
                        signal_type="positive",
                        evidence_count=3,
                        key_findings=["数字化转型三年行动计划提供30%补贴", "等保三级要求催生安全采购"],
                        strength="strong",
                        evidence_ids=["mock-pol-1", "mock-pol-2"],
                    ),
                ],
                cross_correlations=[
                    CrossSignalCorrelation(
                        dimensions=["bidding_information", "policy_compliance"],
                        relation="reinforces",
                        description="招标需求方向与政策鼓励方向高度一致，政策补贴降低客户采购门槛",
                        implication="商机确定性高，时机有利，建议优先投入",
                    ),
                ],
            ),
            supporting_chains=[
                SupportChain(
                    chain_id="sc-1",
                    thesis="目标公司有持续信息化采购需求，且政策环境有利，存在明确销售机会",
                    evidence_ids=["mock-bid-1", "mock-bid-2", "mock-pol-1"],
                    strength="strong",
                ),
            ],
            counter_chains=[
                CounterChain(
                    chain_id="cc-1",
                    thesis="现有供应商格局已形成，新进入者可能面临资质壁垒",
                    evidence_ids=["mock-bid-3"],
                    severity="medium",
                    mitigation="通过差异化方案（如AI赋能）建立独特价值，同时积累行业资质",
                ),
            ],
            competitive_risks=[
                CompetitiveRisk(
                    risk_type="供应商锁定",
                    description="Mock描述：现有2家供应商占据80%市场份额",
                    likelihood="medium",
                    evidence_ids=["mock-bid-1"],
                ),
            ],
            recommended_scenarios=[
                EntryScenario(
                    scenario_name="等保合规升级场景",
                    description="利用《网络安全等级保护管理办法》修订契机，向CIO推荐合规升级方案",
                    why_recommended="政策强制要求，时间窗口明确，预算优先级高",
                    prerequisites=["具备等保测评资质", "有安全产品线或合作伙伴"],
                    evidence_ids=["mock-pol-2"],
                ),
            ],
            icebreaker_strategies=[
                IcebreakerStrategy(
                    rank=1,
                    strategy_name="政策红利切入法",
                    approach="以最新政策解读为切入点，向目标公司分享数字化转型补贴申请指南，建立专业信任",
                    target_persona="CIO",
                    hook="最近发布的数字化转型三年行动计划，贵司的XX项目可能符合30%补贴条件，我们有成功帮客户申请的经验，想跟您分享一下。",
                    evidence_ids=["mock-pol-1"],
                ),
                IcebreakerStrategy(
                    rank=2,
                    strategy_name="在行案例锚定法",
                    approach="引用同行业标杆客户的数字化转型成果，展示可量化ROI，引发竞争焦虑",
                    target_persona="业务副总裁",
                    hook="XX行业头部企业通过数字化转型将运营成本降低了25%，我们发现贵司在XX环节也有类似优化空间，想跟您探讨一下可能性。",
                    evidence_ids=["mock-bid-1"],
                ),
                IcebreakerStrategy(
                    rank=3,
                    strategy_name="漏斗倒推调研法",
                    approach="以外围公开招标数据为切入点，通过历史采购记录分析客户预算周期，在下一个采购窗口前布局",
                    target_persona="采购处长",
                    hook="我们分析了贵单位近5年的IT采购数据，发现每年Q1是主要采购窗口，如果方便的话想了解一下今年的计划和痛点。",
                    evidence_ids=["mock-bid-2"],
                ),
            ],
            action_plan=[
                NextAction(
                    priority=1,
                    action="深入研究目标公司近2年招标公告，确认采购决策链和关键人",
                    timeline="本周内",
                    expected_outcome="采购决策链图谱和关键人名单",
                    owner="销售经理",
                ),
                NextAction(
                    priority=2,
                    action="准备行业定制化方案PPT，重点突出政策合规和ROI",
                    timeline="下周内",
                    expected_outcome="可演示的方案PPT和客户案例集",
                    owner="解决方案架构师",
                ),
                NextAction(
                    priority=3,
                    action="通过行业会议或共同客户引荐建立初次联系",
                    timeline="两周内",
                    expected_outcome="获得一次正式拜访或电话沟通机会",
                    owner="客户经理",
                ),
            ],
            analysis_notes="Mock: 这是测试Mock数据，用于验证系统集成。",
            generated_at="2026-07-07T00:00:00Z",
        )


class AgentHarness:
    """
    单维度智能体编排器

    管理单个维度智能体的完整执行循环：
    Planning → Research → Extraction → Evaluation → (Reflection → 循环)

    属性:
        task_spec: 任务规约
        dimension: 维度名称
        dimension_goal: 维度目标
        state: 执行状态
        token_tracker: Token 追踪器
        intervention_manager: 人工介入管理器
    """

    def __init__(
        self,
        task_spec: TaskSpec,
        dimension: str,
        use_mock_agents: bool = False,
        experience_memory = None,
        progress_callback = None,
        candidate_screening_agent: Optional[CandidateScreeningAgent] = None,
        candidate_screening_config: Optional[dict] = None,
        batch_extraction_shadow_enabled: bool = False,
        evidence_policy: Optional[EvidencePolicy] = None,
        evidence_policy_critical_claim_ids: Optional[list[str]] = None,
        evidence_policy_batch_size: int = 6,
    ):
        """
        初始化 AgentHarness

        Args:
            task_spec: 任务规约
            dimension: 维度名称
            use_mock_agents: 是否使用 Mock 智能体（用于测试）
            experience_memory: 经验记忆管理器（可选）
            progress_callback: 进度回调 callable(task_id, stage, progress)（可选）
        """
        self.task_spec = task_spec
        self.dimension = dimension
        self.progress_callback = progress_callback
        self.dimension_goal = task_spec.dimension_goals.get(dimension)
        self.experience_memory = experience_memory
        self.candidate_screening_config = self._resolve_candidate_screening_config(
            candidate_screening_config,
            use_mock_agents=use_mock_agents,
        )
        self.candidate_screening_agent = candidate_screening_agent
        self.candidate_screening_shadow_attempts: list[CandidateScreeningAttempt] = []
        self.batch_extraction_shadow_enabled = bool(batch_extraction_shadow_enabled)
        self.batch_extraction_shadow_runs: list[dict] = []
        self.batch_extraction_shadow_evidences: list[Evidence] = []
        if type(evidence_policy_batch_size) is not int or evidence_policy_batch_size < 1:
            raise ValueError("evidence_policy_batch_size 必须为正整数")
        self.evidence_policy = evidence_policy
        self.evidence_policy_critical_claim_ids = tuple(evidence_policy_critical_claim_ids or ())
        self.evidence_policy_batch_size = evidence_policy_batch_size
        self.evidence_policy_assessments: list[dict] = []

        if not self.dimension_goal:
            raise ValueError(f"维度 '{dimension}' 的目标未定义")

        # 初始化执行状态
        self.state = ExecutionState(dimension=dimension)

        # 初始化 Token 追踪器
        self.token_tracker = TokenTracker(task_spec.budget_config)

        # 初始化人工介入管理器
        self.intervention_manager = InterventionManager()

        # 初始化智能体
        if use_mock_agents:
            # Mock 模式用于测试
            self.planner = MockPlannerAgent(experience_memory=self.experience_memory)
            self.researcher = MockResearchAgent()
            self.extractor = MockExtractorAgent()
            self.evaluator = MockEvaluatorAgent()
            self.reflector = MockReflectorAgent()
            # WBS-10: Mock 审计代理
            self.auditor = MockAuditorAgent()
            self.skeptic = MockSkepticAgent()
            # WBS-11: Mock 招标分析代理
            self.bidding_analyst = MockBiddingAnalysisAgent()
            # WBS-12: Mock 政策合规分析代理
            self.policy_analyst = MockPolicyComplianceAgent()
            # WBS-13: Mock Playwright 字段代理
            self.field_agent = MockPlaywrightFieldAgent()
            # WBS-14: Mock 全维度策略分析代理
            self.strategy_analyst = MockStrategyAnalysisAgent()
        else:
            # 真实智能体模式
            llm_client = get_gateway_client()

            # 动态算力路由：根据 complexity_level 为每个 Agent 解析模型
            from app.llm.model_router import ModelRouter
            router = ModelRouter.from_settings()
            complexity = self.dimension_goal.complexity_level

            self.planner = PlannerAgent(
                llm_client=llm_client,
                token_tracker=self.token_tracker,
                experience_memory=self.experience_memory,
                model=router.resolve("planner", complexity),
            )
            self.researcher = ResearchAgent(
                search_client=SearchClient(),
                fetch_client=FetchClient(),
            )
            self.extractor = ExtractorAgent(
                llm_client=llm_client,
                token_tracker=self.token_tracker,
                model=router.resolve("extractor", complexity),
            )
            self.evaluator = EvaluatorAgent(
                llm_client=llm_client,
                token_tracker=self.token_tracker,
                quality_threshold=self.task_spec.quality_threshold,
            )
            self.reflector = ReflectorAgent(
                llm_client=llm_client,
                token_tracker=self.token_tracker,
                model=router.resolve("reflector", complexity),
            )
            # WBS-10: 审计智能体（使用默认模型，不走复杂路由）
            self.auditor = EvidenceAuditorAgent(
                llm_client=llm_client,
                token_tracker=self.token_tracker,
                model=router.resolve("auditor", complexity),
            )
            self.skeptic = SkepticAgent(
                llm_client=llm_client,
                token_tracker=self.token_tracker,
                model=router.resolve("skeptic", complexity),
            )
            # WBS-11: 招标分析智能体
            self.bidding_analyst = BiddingAnalysisAgent(
                llm_client=llm_client,
                token_tracker=self.token_tracker,
                model=router.resolve("bidding_analyst", complexity),
            )
            # WBS-12: 政策合规分析智能体
            self.policy_analyst = PolicyComplianceAgent(
                llm_client=llm_client,
                token_tracker=self.token_tracker,
                model=router.resolve("policy_analyst", complexity),
            )
            # WBS-13: Playwright 字段智能体（不依赖 LLM，不需要 token_tracker/model）
            self.field_agent = PlaywrightFieldAgent()
            # WBS-14: 全维度策略分析智能体
            self.strategy_analyst = StrategyAnalysisAgent(
                llm_client=llm_client,
                token_tracker=self.token_tracker,
                model=router.resolve("strategy_analyst", complexity),
            )
            if (
                self.candidate_screening_agent is None
                and self.candidate_screening_config["shadow_enabled"]
            ):
                self.candidate_screening_agent = CandidateScreeningAgent(
                    llm_client=llm_client,
                )

        self.use_mock_agents = use_mock_agents
        logger.info(f"[AgentHarness] 初始化：{dimension} (mock={use_mock_agents})")

    @staticmethod
    def _resolve_candidate_screening_config(
        provided_config: Optional[dict],
        *,
        use_mock_agents: bool,
    ) -> dict:
        """真实 Harness 从配置中心读取影子开关；读取异常时安全关闭。"""
        if provided_config is not None:
            return validate_candidate_screening_config(provided_config)
        if use_mock_agents:
            return validate_candidate_screening_config(DEFAULT_CANDIDATE_SCREENING_CONFIG)
        try:
            from app.db.session import SessionLocal

            db = SessionLocal()
            try:
                return get_candidate_screening_config(db)
            finally:
                db.close()
        except Exception as error:
            logger.warning(
                "[AgentHarness] 候选筛选影子配置读取失败，安全关闭: %s",
                type(error).__name__,
            )
            return validate_candidate_screening_config(DEFAULT_CANDIDATE_SCREENING_CONFIG)

    def _report_progress(self, stage: str) -> None:
        """向外部报告执行进度"""
        if not self.progress_callback:
            return
        span = 80  # 10% → 90%
        max_iter = max(1, self.task_spec.max_iterations)
        iter_progress = int((self.state.iteration / max_iter) * span)

        stage_offset = {
            "planning": 0,
            "research": int(span / max_iter * 0.3),
            "extraction": int(span / max_iter * 0.6),
            "reflection": int(span / max_iter * 0.85),
        }
        pct = min(90, 10 + iter_progress + stage_offset.get(stage, 0))
        try:
            self.progress_callback(self.task_spec.task_id, stage, pct)
        except Exception:
            pass

    def execute(self) -> DimensionResult:
        """
        执行完整的 Harness 循环

        Returns:
            DimensionResult - 维度执行结果
        """
        logger.info(f"[AgentHarness] 开始执行：{self.dimension}")
        self.state.status = DimensionStatus.PLANNING
        consecutive_empty_searches = 0

        while self.state.iteration < self.task_spec.max_iterations:
            logger.info(
                f"[AgentHarness] Iteration {self.state.iteration + 1}/{self.task_spec.max_iterations}"
            )

            # === Step 1: Planning ===
            self._report_progress("planning")
            plan_result = self._execute_planning()
            if not plan_result:
                logger.warning(f"[AgentHarness] Planning 失败，终止执行")
                break

            # Evaluate Planning
            plan_eval = self.evaluator.evaluate_plan(plan_result, self.dimension_goal)
            self.state.add_evaluation(plan_eval)
            if not plan_eval.passed:
                # 规划质量差 → 反思后重新规划
                logger.info(f"[AgentHarness] Planning 评估未通过 (score={plan_eval.score:.2f})")
                self._report_progress("reflection")
                reflection = self.reflector.reflect_on_plan(plan_result, plan_eval.feedback)
                self.state.add_reflection(reflection)
                continue  # 重试 Planning

            # === Step 2: Research ===
            self.state.status = DimensionStatus.RESEARCHING
            self._report_progress("research")
            research_batch = self.researcher.execute(
                plan_result["search_queries"],
                dimension=self.dimension,
                seed=f"{self.task_spec.task_id}:{self.dimension}",
            )
            if not isinstance(research_batch, ResearchBatch):
                raise TypeError("ResearchAgent.execute 必须返回 ResearchBatch")
            research_results = list(research_batch.search_results)
            self.state.set_candidate_set(research_batch.candidate_set)
            self._record_candidate_shadow_metrics(research_batch)
            self._run_candidate_screening_shadow(research_batch)
            self.state.search_results.extend(research_results)

            # Evaluate Research
            research_eval = self.evaluator.evaluate_research(research_results, self.dimension_goal)
            self.state.add_evaluation(research_eval)
            if len(research_results) == 0:
                consecutive_empty_searches += 1
                if consecutive_empty_searches >= 2:
                    logger.warning(
                        f"[AgentHarness] 连续 {consecutive_empty_searches} 轮搜索无结果，"
                        f"搜索服务可能不可用，提前终止"
                    )
                    self.state.status = DimensionStatus.INSUFFICIENT
                    return self._synthesize_result(force_finish=True)
                # 搜索结果完全为空 → 反思+重试
                logger.warning(
                    f"[AgentHarness] Research 返回 0 条结果 (score={research_eval.score:.2f})，触发反思重试"
                )
                self._report_progress("reflection")
                reflection = self.reflector.reflect_on_extraction([], research_eval.feedback)
                self.state.add_reflection(reflection)
                self.state.iteration += 1
                continue
            else:
                # 有结果 → 重置连续空搜索计数器
                consecutive_empty_searches = 0

            if not research_eval.passed:
                # 有结果但质量评分低 → 记录警告，仍然进入 Extraction
                logger.warning(
                    f"[AgentHarness] Research 评估未通过 (score={research_eval.score:.2f})，"
                    f"但仍有 {len(research_results)} 条结果，继续进入 Extraction"
                )

            # === Step 3: Extraction ===
            self.state.status = DimensionStatus.EXTRACTING
            self._report_progress("extraction")
            self._run_batch_extraction_shadow(research_batch)
            evidences = self._execute_extraction_with_evidence_policy(research_results)
            for evidence in evidences:
                evidence.dimension = self.dimension
                self.state.add_evidence(evidence)

            # Evaluate Extraction
            extract_eval = self.evaluator.evaluate_extraction(
                evidences,
                self.dimension_goal
            )
            self.state.add_evaluation(extract_eval)
            self.state.current_quality_score = extract_eval.score
            # === Step 4: 决策 ===
            if extract_eval.passed:
                # WBS-10: 审计门控 — 检查证据是否真正支撑结论
                audit_feedback = self._maybe_run_audit()
                if audit_feedback:
                    logger.info(
                        f"[AgentHarness] 审计发现问题，触发 Re-Plan: {self.dimension}"
                    )
                    self.state.add_reflection(audit_feedback)
                    self.state.iteration += 1
                    continue  # 回到 Planning 重新搜索

                # 质量达标 → 完成
                logger.info(
                    f"[AgentHarness] 执行完成：{self.dimension} "
                    f"(score={extract_eval.score:.2f}, evidences={len(evidences)})"
                )
                self.state.status = DimensionStatus.COMPLETED
                return self._synthesize_result()
            else:
                # 质量不达标 → 反思后继续下一轮迭代
                logger.info(
                    f"[AgentHarness] Extraction 评估未通过 (score={extract_eval.score:.2f})"
                )
                reflection = self.reflector.reflect_on_extraction(
                    evidences,
                    extract_eval.feedback
                )
                self.state.add_reflection(reflection)
                self.state.iteration += 1

        # 达到最大迭代次数 → 强制输出当前最佳结果
        return self._synthesize_result(force_finish=True)

    def _run_batch_extraction_shadow(self, research_batch: ResearchBatch) -> None:
        """旁路验证选择性抓取和批量提取，绝不改变基线 Evidence 或最终报告。"""
        if not self.batch_extraction_shadow_enabled:
            return
        if not hasattr(self.researcher, "fetch_selected_candidates") or not hasattr(
            self.extractor, "execute_batch_with_minimal_retry"
        ):
            logger.warning("[AgentHarness] 批提取影子模式缺少所需 Agent 能力，已跳过")
            return
        try:
            ranked_ids = [candidate.candidate_id for candidate in research_batch.candidate_set.candidates]
            fetched = self.researcher.fetch_selected_candidates(
                research_batch.candidate_set,
                ranked_ids,
            )
            payloads = tuple(
                ExtractionCandidatePayload(
                    candidate_id=item.candidate_id,
                    title=next(
                        candidate.title
                        for candidate in research_batch.candidate_set.candidates
                        if candidate.candidate_id == item.candidate_id
                    ),
                    content=item.content or "[无可用正文或摘要]",
                )
                for item in fetched.items
            )
            plan = plan_extraction_batches(payloads)
            result_by_candidate_id = {
                item.candidate_id: SearchResult(
                    title=next(
                        candidate.title
                        for candidate in research_batch.candidate_set.candidates
                        if candidate.candidate_id == item.candidate_id
                    ),
                    url=item.url,
                    snippet=item.content,
                    raw_content=item.content,
                )
                for item in fetched.items
            }
            fetch_by_candidate_id = {item.candidate_id: item for item in fetched.items}
            batch_evidence_count = 0
            rejection_count = 0
            for batch in plan.batches:
                self._report_batch_progress(batch.index, len(plan.batches))
                extraction = self.extractor.execute_batch_with_minimal_retry(
                    batch,
                    self.dimension_goal.must_extract,
                )
                rejection_count += len(extraction.rejected_by_candidate_id)
                for candidate_id, item in extraction.items_by_candidate_id.items():
                    fetch_item = fetch_by_candidate_id[candidate_id]
                    evidence = self.extractor.convert_batch_item_to_evidence(
                        item,
                        dimension=self.dimension,
                        result=result_by_candidate_id[candidate_id],
                        candidate_id=candidate_id,
                        fetch_content_quality=fetch_item.content_quality,
                        fetch_confidence=fetch_item.confidence,
                    )
                    if evidence:
                        self.batch_extraction_shadow_evidences.append(evidence)
                        batch_evidence_count += 1
            self.batch_extraction_shadow_runs.append({
                "candidate_count": len(payloads),
                "batch_count": len(plan.batches),
                "evidence_count": batch_evidence_count,
                "rejection_count": rejection_count,
            })
        except Exception as error:
            logger.exception("[AgentHarness] 批提取影子运行失败，不影响基线链路: %s", error)
            self.batch_extraction_shadow_runs.append({"error_type": type(error).__name__})

    def _execute_extraction_with_evidence_policy(
        self,
        research_results: list[SearchResult],
    ) -> list[Evidence]:
        """策略开启时按梯队扩展；默认继续使用原有全量基线提取。"""
        if self.evidence_policy is None:
            return self.extractor.execute(
                research_results,
                self.dimension_goal.must_extract,
                self.dimension,
            )

        evidences: list[Evidence] = []
        consecutive_low_gain_batches = 0
        for start in range(0, len(research_results), self.evidence_policy_batch_size):
            result_batch = research_results[start:start + self.evidence_policy_batch_size]
            batch_evidences = self.extractor.execute(
                result_batch,
                self.dimension_goal.must_extract,
                self.dimension,
            )
            evidences.extend(batch_evidences)
            assessment = evaluate_evidence_sufficiency(
                policy=self.evidence_policy,
                evidences=evidences,
                latest_batch=batch_evidences,
                required_fields=self.dimension_goal.must_extract,
                critical_claim_ids=self.evidence_policy_critical_claim_ids,
                consecutive_low_gain_batches=consecutive_low_gain_batches,
            )
            if assessment.batch_novelty_ratio < 0.20:
                consecutive_low_gain_batches += 1
            else:
                consecutive_low_gain_batches = 0
            target_reached = (
                assessment.is_sufficient
                and assessment.evidence_count >= self.evidence_policy.target_evidence_count
            )
            low_gain_stop = (
                assessment.batch_novelty_ratio < 0.20
                and consecutive_low_gain_batches >= self.evidence_policy.max_low_gain_batches
                and not assessment.mandatory_gaps
            )
            self.evidence_policy_assessments.append({
                "candidate_batch_start": start,
                "candidate_batch_size": len(result_batch),
                "evidence_count": assessment.evidence_count,
                "mandatory_gaps": assessment.mandatory_gaps,
                "batch_novelty_ratio": assessment.batch_novelty_ratio,
                "target_reached": target_reached,
                "low_gain_stop": low_gain_stop,
            })
            if target_reached or low_gain_stop:
                break
        return evidences

    def _report_batch_progress(self, index: int, total: int) -> None:
        """批处理旁路进度，供前端在开关开启后显示“批次 m/n”。"""
        if not self.progress_callback:
            return
        progress = min(90, 35 + int(index / max(total, 1) * 35))
        try:
            self.progress_callback(
                self.task_spec.task_id,
                f"extraction_batch_{index}_of_{total}",
                progress,
            )
        except Exception:
            pass

    def _record_candidate_shadow_metrics(self, batch: ResearchBatch) -> None:
        """记录不含查询文本、标题、URL 或摘要的候选影子指标。"""
        source_counts: Counter[str] = Counter()
        query_counts: Counter[str] = Counter()
        for candidate in batch.candidate_set.candidates:
            for trace in candidate.source_traces:
                source_counts[trace.content_source] += 1
                query_counts[trace.source_query] += 1

        normalized_input_count = batch.candidate_set.source_result_count
        candidate_count = len(batch.candidate_set.candidates)
        deduplicated_count = max(0, normalized_input_count - candidate_count)
        deduplication_rate = (
            deduplicated_count / normalized_input_count
            if normalized_input_count
            else 0.0
        )
        payload = {
            "name": "candidate_set_shadow",
            "task_id": self.task_spec.task_id,
            "dimension": self.dimension,
            "raw_result_count": batch.raw_result_count,
            "normalized_input_count": normalized_input_count,
            "candidate_count": candidate_count,
            "deduplicated_count": deduplicated_count,
            "deduplication_rate": round(deduplication_rate, 6),
            "invalid_candidate_count": batch.invalid_candidate_count,
            "query_result_counts": {
                hashlib.sha256(query.encode("utf-8")).hexdigest()[:12]: count
                for query, count in sorted(query_counts.items())
            },
            "source_counts": dict(sorted(source_counts.items())),
        }
        logger.info(
            "candidate_shadow_metric=%s",
            json.dumps(payload, ensure_ascii=False, sort_keys=True),
        )

    def _run_candidate_screening_shadow(self, batch: ResearchBatch) -> None:
        """旁路运行候选筛选；任何失败都不得改变或阻断基线研究链路。"""
        if (
            not self.candidate_screening_config["shadow_enabled"]
            or self.candidate_screening_agent is None
        ):
            return
        context = CandidateScreeningContext(
            company_name=self.task_spec.company_name,
            demand_direction=self.task_spec.demand_direction,
            dimension=self.dimension,
            target_entity_names=(self.task_spec.company_name,),
        )
        try:
            attempt = self.candidate_screening_agent.execute_with_audit(
                batch.candidate_set,
                context,
                config=self.candidate_screening_config,
                evaluated_at=datetime.now(timezone.utc),
            )
            self.candidate_screening_shadow_attempts.append(attempt)
            if attempt.result is not None:
                task_execution_metrics.record_candidate_screening_shadow(
                    task_id=self.task_spec.task_id,
                    dimension=self.dimension,
                    status="success",
                    candidate_input_count=len(batch.candidate_set.candidates),
                    candidate_selected_count=len(attempt.result.selected_candidate_ids),
                    schema_success=True,
                    output_token_warning=attempt.result.output_token_warning,
                )
                return
            failure_audit = attempt.failure_audit
            task_execution_metrics.record_candidate_screening_shadow(
                task_id=self.task_spec.task_id,
                dimension=self.dimension,
                status="schema_failed",
                candidate_input_count=len(batch.candidate_set.candidates),
                schema_success=False,
                error_code=failure_audit.error_code if failure_audit else "missing_failure_audit",
            )
        except Exception as error:
            logger.warning(
                "[AgentHarness] 候选筛选影子调用失败，不影响基线研究: %s",
                type(error).__name__,
            )
            task_execution_metrics.record_candidate_screening_shadow(
                task_id=self.task_spec.task_id,
                dimension=self.dimension,
                status="failed",
                candidate_input_count=len(batch.candidate_set.candidates),
                schema_success=False,
                error_code=type(error).__name__,
            )

    def _execute_planning(self) -> Optional[dict]:
        """执行 Planning 阶段"""
        try:
            self.state.status = DimensionStatus.PLANNING

            plan = self.planner.execute(
                company=self.task_spec.company_name,
                direction=self.task_spec.demand_direction,
                goal=self.dimension_goal.goal,
                reflection=self.state.last_reflection,
                dimension=self.dimension,
                domain_context=self._planning_domain_context(),
            )

            # 记录生成的搜索词
            for query in plan.get("search_queries", []):
                self.state.add_search_query(query)

            self._last_plan = plan

            logger.info(
                f"[AgentHarness] Planning: 生成{len(plan.get('search_queries', []))}个搜索词"
            )
            return plan

        except Exception as e:
            logger.error(f"[AgentHarness] Planning 失败：{e}")
            self.state.error_message = str(e)
            self.state.status = DimensionStatus.FAILED
            return None

    def _planning_domain_context(self) -> dict:
        raw_context = self.task_spec.domain_context
        if isinstance(raw_context, dict):
            return raw_context
        if not isinstance(raw_context, str) or not raw_context.strip():
            return {}
        try:
            parsed = json.loads(raw_context)
        except json.JSONDecodeError:
            return {"notes": raw_context.strip()}
        if not isinstance(parsed, dict):
            raise ValueError("TaskSpec.domain_context 必须是 JSON 对象")
        return parsed

    def _synthesize_result(self, force_finish: bool = False) -> DimensionResult:
        """
        合成执行结果

        Args:
            force_finish: 是否强制结束（达到最大迭代次数）

        Returns:
            DimensionResult
        """
        # 同步 Token 统计到 ExecutionState（TokenTracker 与 State 独立维护）
        self.state.token_usage["planning"] = self.token_tracker.current_usage.planning
        self.state.token_usage["research"] = self.token_tracker.current_usage.research
        self.state.token_usage["extraction"] = self.token_tracker.current_usage.extraction
        self.state.token_usage["reflection"] = self.token_tracker.current_usage.reflection

        if force_finish:
            if self.state.current_quality_score < self.task_spec.quality_threshold:
                # 质量不达标且已达最大迭代 → SUSPENDED 或 INSUFFICIENT
                if self.task_spec.allow_human_intervention:
                    self.state.status = DimensionStatus.SUSPENDED
                    self._request_human_intervention()
                else:
                    self.state.status = DimensionStatus.INSUFFICIENT
            else:
                self.state.status = DimensionStatus.COMPLETED
        else:
            # 成功完成 → 保存经验
            self._save_experience()

        return DimensionResult.from_state(self.state, force_finish=force_finish)

    def _maybe_run_audit(self) -> Optional[str]:
        """WBS-10: 对收集到的证据和草稿结论运行审计。

        仅在非 Mock 模式下运行。
        如果审计发现 fatal/major 问题，返回反思文本供下一轮迭代使用。

        Returns:
            反思文本（需要 Re-Plan 时），或 None（放行）
        """
        if self.use_mock_agents:
            return None

        from app.agents.claim_reference_validator import _extract_claims_from_markdown

        # 检查重试预算
        max_dimension_retries = 3
        if self.state.iteration >= max_dimension_retries:
            logger.warning(
                f"[AgentHarness] 维度 {self.dimension} 已达最大审计重试次数 "
                f"({max_dimension_retries})，跳过审计"
            )
            return None

        # 收集证据信息
        evidence_dicts: list[dict] = []
        for ev in self.state.evidences_collected:
            evidence_dicts.append({
                "id": ev.id or "",
                "title": ev.title,
                "snippet": ev.snippet,
                "url": ev.url,
                "source_reliability": ev.metadata.get("source_reliability", "UNKNOWN"),
                "published_at": ev.captured_at.isoformat() if ev.captured_at else "",
            })

        if not evidence_dicts:
            logger.info(f"[AgentHarness] 无证据可审计: {self.dimension}")
            return None

        # 从 plan 构建草稿结论上下文
        goal_text = self.dimension_goal.goal if self.dimension_goal else ""
        task_context = f"{self.task_spec.company_name} - {self.task_spec.demand_direction}"

        claim_contexts: dict[str, str] = {}
        for ev in evidence_dicts:
            ev_id = ev["id"]
            claim_contexts[ev_id] = f"{task_context}\n维度目标: {goal_text}"

        logger.info(f"[AgentHarness] 运行审计: {self.dimension}, {len(evidence_dicts)} 条证据")

        try:
            # Step 1: EvidenceAuditor
            evidence_audits = self.auditor.audit_all(
                evidences=evidence_dicts,
                claim_contexts=claim_contexts,
                task_context=task_context,
            )

            # 检查是否有严重证据问题
            from app.agents.schemas.claim_schema import SupportLevel
            refuted_count = sum(1 for ea in evidence_audits if ea.support_level == SupportLevel.REFUTED)
            if refuted_count > 0:
                reflection = (
                    f"[审计] 维度 {self.dimension}: 发现 {refuted_count} 条证据存在矛盾/反证。"
                    f"需要重新规划搜索策略，排除错误来源。"
                )
                logger.warning(f"[AgentHarness] {reflection}")
                return reflection

            # Step 2: 构建临时 claim 用于 Skeptic 审计
            draft_claims: list[ClaimWithEvidence] = []
            for ev in evidence_dicts:
                claim_text = f"{self.task_spec.company_name} - {goal_text}"
                draft_claims.append(ClaimWithEvidence(
                    claim_id=ev["id"],
                    claim_text=claim_text[:200],
                    evidence_ids=([ev["id"]] if ev["id"] else []),
                    evidence_summaries=[{
                        "title": ev["title"],
                        "snippet": ev["snippet"][:300],
                    }],
                    evidence_audit_results=[
                        ea for ea in evidence_audits if str(ea.evidence_id) == ev["id"]
                    ],
                ))

            # Step 3: SkepticAgent
            claim_audits = self.skeptic.audit_claims(
                claims=draft_claims,
                company_name=self.task_spec.company_name,
                demand_direction=self.task_spec.demand_direction,
            )

            # Step 4: Severity
            severity, fatal_list, major_list, _minor_list = triage_aggregate(claim_audits)

            if severity in (Severity.FATAL, Severity.MAJOR):
                notes = [c.skeptic_notes[:150] for c in (fatal_list + major_list)[:3]]
                reflection = (
                    f"[审计] 维度 {self.dimension}: "
                    f"严重度={severity.value}, "
                    f"问题: {'; '.join(notes)}"
                )
                logger.warning(f"[AgentHarness] {reflection}")
                return reflection

        except Exception as e:
            logger.error(f"[AgentHarness] 审计失败（非致命）: {e}", exc_info=True)

        return None

    def _save_experience(self):
        """保存成功经验到长期记忆"""
        if self.experience_memory is None:
            return

        try:
            plan = getattr(self, '_last_plan', None)
            search_queries = (
                plan.get("search_queries", [])
                if plan else self.state.search_queries_generated
            )
            strategy = plan.get("strategy", "") if plan else ""

            self.experience_memory.save_experience(
                task_id=self.task_spec.task_id,
                dimension=self.dimension,
                company_name=self.task_spec.company_name,
                demand_direction=self.task_spec.demand_direction,
                goal=self.dimension_goal.goal,
                search_queries=search_queries,
                strategy=strategy,
                quality_score=self.state.current_quality_score,
                iteration_count=self.state.iteration + 1,
                token_used=self.token_tracker.current_usage.total,
            )
            logger.info(
                f"[AgentHarness] 经验已保存：{self.dimension} "
                f"(score={self.state.current_quality_score:.2f})"
            )
        except Exception as e:
            logger.warning(f"[AgentHarness] 经验保存失败（非致命）：{e}")

    def _request_human_intervention(self):
        """请求人工介入"""
        ai_context = (
            f"维度 '{self.dimension}' 已达到最大迭代次数 ({self.task_spec.max_iterations})，"
            f"但质量评分 ({self.state.current_quality_score:.2f}) 仍低于及格线 "
            f"({self.task_spec.quality_threshold})。\n\n"
            f"反思记录:\n" + "\n".join(self.state.reflections)
        )

        self.intervention_manager.request_intervention(
            task_id=self.task_spec.task_id,
            dimension=self.dimension,
            intervention_type=InterventionType.QUERY_MODIFICATION,
            ai_context=ai_context,
            suggestions=self.state.search_queries_generated
        )

        logger.info(
            f"[AgentHarness] 请求人工介入：{self.task_spec.task_id}/{self.dimension}"
        )

    def get_status(self) -> dict:
        """获取当前执行状态"""
        return {
            "dimension": self.dimension,
            "status": self.state.status.value,
            "iteration": self.state.iteration,
            "quality_score": self.state.current_quality_score,
            "evidences_count": len(self.state.evidences_collected),
            "token_usage": self.token_tracker.get_status(),
            "pending_interventions": len(
                self.intervention_manager.get_pending_interventions(self.task_spec.task_id)
            )
        }
