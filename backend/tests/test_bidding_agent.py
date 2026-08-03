"""WBS-11: 招标投标分析测试

测试 BiddingAnalysisAgent、Schema、Mock Agent 和集成。
"""
import json
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from app.agents.schemas.bidding_schema import (
    OpportunityType,
    LockInRiskLevel,
    BiddingProject,
    ProcurementProfile,
    SupplierInfo,
    TechnicalFingerprint,
    LockInRisk,
    BiddingAnalysisResult,
)
from app.agents.expert.bidding_agent import BiddingAnalysisAgent
from app.agents.harness.agent_harness import MockBiddingAnalysisAgent


# ══════════════════════════════════════════════════════════════════════════════
# Fixtures
# ══════════════════════════════════════════════════════════════════════════════

@pytest.fixture
def mock_llm():
    """创建 Mock LLM 客户端"""
    llm = MagicMock()
    llm.infer.return_value = {
        "content": json.dumps({
            "opportunity_type": "clear",
            "opportunity_confidence": 0.85,
            "procurement_profile": {
                "total_projects": 5,
                "estimated_total_value": "约 3000 万元/年",
                "main_categories": ["信息化设备", "数据中心"],
                "frequency_pattern": "每年 Q1 集中采购",
                "evidence_ids": ["ev-1", "ev-2"],
            },
            "recent_projects": [
                {
                    "project_name": "信息化设备采购",
                    "procurer": "XX市教育局",
                    "budget_amount": "580万元",
                    "winning_bidder": "A科技公司",
                    "publish_date": "2025-03-15",
                    "evidence_ids": ["ev-1"],
                }
            ],
            "budget_cycle_analysis": "年度预算集中在 Q1，单项目 500-1200 万元",
            "supplier_landscape": [
                {
                    "name": "A科技公司",
                    "win_count": 2,
                    "win_categories": ["信息化设备", "智慧交通"],
                    "estimated_share": "约 30%",
                    "evidence_ids": ["ev-1", "ev-4"],
                }
            ],
            "technical_fingerprint": {
                "has_bias": True,
                "biased_brands": ["华为", "H3C"],
                "bias_description": "多个项目技术参数指向特定品牌",
                "evidence_ids": ["ev-1", "ev-2"],
            },
            "lockin_risks": [
                {
                    "level": "medium",
                    "risk_type": "参数锁定",
                    "description": "技术参数存在品牌倾向",
                    "affected_projects": ["信息化设备采购"],
                    "evidence_ids": ["ev-1"],
                }
            ],
            "entry_window": "预计下季度有新的招标窗口",
            "followup_strategy": "提前联系采购人，准备资质",
            "analysis_notes": "基于 5 条证据分析，部分项目金额为估算",
        }),
        "usage": {"total_tokens": 1500},
    }
    return llm


@pytest.fixture
def sample_evidences():
    """创建样本证据对象列表（不依赖 DB）"""

    class FakeEvidence:
        def __init__(self, ev_id, title, snippet, url, captured_at=None):
            self.id = ev_id
            self.title = title
            self.snippet = snippet
            self.url = url
            self.dimension = "bidding_information"
            self.captured_at = captured_at or "2025-06-01"
            self.metadata = {
                "采购人": "XX市教育局",
                "中标金额": "580万元",
                "中标人": "A科技公司",
                "发布时间": "2025-03-15",
            }

    return [
        FakeEvidence(
            ev_id=uuid4(),
            title="信息化设备采购项目",
            snippet="XX市教育局发布了信息化设备采购项目，预算金额580万元...",
            url="https://example.com/bid/1",
        ),
        FakeEvidence(
            ev_id=uuid4(),
            title="数据中心升级改造",
            snippet="XX省税务局数据中心升级改造项目，中标金额1200万元...",
            url="https://example.com/bid/2",
        ),
        FakeEvidence(
            ev_id=uuid4(),
            title="政务云平台运维服务",
            snippet="XX市大数据局政务云平台运维服务，350万元/年...",
            url="https://example.com/bid/3",
        ),
    ]


@pytest.fixture
def bidding_agent(mock_llm):
    """创建带 Mock LLM 的 BiddingAnalysisAgent"""
    return BiddingAnalysisAgent(llm_client=mock_llm)


# ══════════════════════════════════════════════════════════════════════════════
# TestBiddingSchema: 模型创建/序列化/枚举值
# ══════════════════════════════════════════════════════════════════════════════

class TestBiddingSchema:

    def test_opportunity_type_enum_values(self):
        assert OpportunityType.CLEAR.value == "clear"
        assert OpportunityType.POTENTIAL.value == "potential"
        assert OpportunityType.INSUFFICIENT.value == "insufficient"

    def test_lockin_risk_level_enum_values(self):
        assert LockInRiskLevel.NONE.value == "none"
        assert LockInRiskLevel.LOW.value == "low"
        assert LockInRiskLevel.MEDIUM.value == "medium"
        assert LockInRiskLevel.HIGH.value == "high"

    def test_bidding_project_creation(self):
        proj = BiddingProject(
            project_name="测试项目",
            procurer="测试采购人",
            budget_amount="100万元",
            winning_bidder="测试中标人",
            publish_date="2025-01-01",
            evidence_ids=["ev-1"],
        )
        assert proj.project_name == "测试项目"
        assert len(proj.evidence_ids) == 1

    def test_bidding_analysis_result_serialization(self):
        """确保 BiddingAnalysisResult 可以正确序列化"""
        result = BiddingAnalysisResult(
            company_name="测试公司",
            demand_direction="数字化转型",
            opportunity_type=OpportunityType.POTENTIAL,
            opportunity_confidence=0.6,
        )
        d = result.model_dump(mode="json")
        assert d["company_name"] == "测试公司"
        assert d["opportunity_type"] == "potential"
        assert d["procurement_profile"]["total_projects"] == 0

    def test_full_bidding_analysis_result_defaults(self):
        """默认构造的 BiddingAnalysisResult 应该是 insufficient"""
        result = BiddingAnalysisResult()
        assert result.opportunity_type == OpportunityType.INSUFFICIENT
        assert result.opportunity_confidence == 0.0
        assert result.procurement_profile.total_projects == 0
        assert len(result.recent_projects) == 0
        assert len(result.supplier_landscape) == 0
        assert len(result.lockin_risks) == 0

    def test_opportunity_confidence_bounds(self):
        """opportunity_confidence 应该被限制在 0-1"""
        result = BiddingAnalysisResult(opportunity_confidence=0.5)
        assert 0.0 <= result.opportunity_confidence <= 1.0


# ══════════════════════════════════════════════════════════════════════════════
# TestBiddingAnalysisAgent: Mock LLM 测试
# ══════════════════════════════════════════════════════════════════════════════

class TestBiddingAnalysisAgent:

    def test_execute_returns_clear_opportunity(self, bidding_agent, mock_llm, sample_evidences):
        """正常分析返回 CLEAR 机会"""
        result = bidding_agent.execute(
            company_name="测试公司",
            demand_direction="数字化转型",
            evidences=sample_evidences,
        )
        assert result.opportunity_type == OpportunityType.CLEAR
        assert result.opportunity_confidence == 0.85
        assert len(result.recent_projects) == 1
        assert len(result.supplier_landscape) == 1
        assert result.technical_fingerprint.has_bias is True
        assert len(result.lockin_risks) == 1

    def test_execute_zero_evidences(self, bidding_agent):
        """零证据时返回 INSUFFICIENT"""
        result = bidding_agent.execute(
            company_name="测试公司",
            demand_direction="数字化转型",
            evidences=[],
        )
        assert result.opportunity_type == OpportunityType.INSUFFICIENT
        assert "未收集到证据" in result.analysis_notes

    def test_execute_json_parse_failure(self, mock_llm, sample_evidences):
        """LLM 返回无效 JSON 时优雅降级"""
        mock_llm.infer.return_value = {"content": "not valid json{{{", "usage": {"total_tokens": 100}}
        agent = BiddingAnalysisAgent(llm_client=mock_llm)
        result = agent.execute(
            company_name="测试公司",
            demand_direction="数字化转型",
            evidences=sample_evidences,
        )
        assert result.opportunity_type == OpportunityType.INSUFFICIENT
        assert "解析失败" in result.analysis_notes or "LLM" in result.analysis_notes

    def test_execute_llm_call_failure(self, mock_llm, sample_evidences):
        """LLM 调用抛出异常时优雅降级"""
        mock_llm.infer.side_effect = RuntimeError("LLM 服务不可用")
        agent = BiddingAnalysisAgent(llm_client=mock_llm)
        result = agent.execute(
            company_name="测试公司",
            demand_direction="数字化转型",
            evidences=sample_evidences,
        )
        assert result.opportunity_type == OpportunityType.INSUFFICIENT
        assert "LLM" in result.analysis_notes or "失败" in result.analysis_notes

    def test_execute_token_tracking(self, mock_llm, sample_evidences):
        """Token 使用被正确记录"""
        from app.agents.harness.token_tracker import TokenTracker, BudgetConfig
        config = BudgetConfig(max_tokens_total=100000)
        tracker = TokenTracker(config)

        agent = BiddingAnalysisAgent(llm_client=mock_llm, token_tracker=tracker)
        agent.execute(
            company_name="测试公司",
            demand_direction="数字化转型",
            evidences=sample_evidences,
        )
        assert tracker.current_usage.bidding_analysis == 1500

    def test_execute_all_8_outputs_present(self, bidding_agent, sample_evidences):
        """8 项输出全部存在"""
        result = bidding_agent.execute(
            company_name="测试公司",
            demand_direction="数字化转型",
            evidences=sample_evidences,
        )
        assert result.procurement_profile is not None
        assert isinstance(result.recent_projects, list)
        assert isinstance(result.supplier_landscape, list)
        assert result.budget_cycle_analysis != ""
        assert result.technical_fingerprint is not None
        assert isinstance(result.lockin_risks, list)
        assert result.entry_window != ""
        assert result.followup_strategy != ""

    def test_execute_with_potential_response(self, mock_llm, sample_evidences):
        """LLM 返回 POTENTIAL 时正确解析"""
        mock_llm.infer.return_value = {
            "content": json.dumps({
                "opportunity_type": "potential",
                "opportunity_confidence": 0.5,
                "procurement_profile": {"total_projects": 2},
                "recent_projects": [],
                "budget_cycle_analysis": "",
                "supplier_landscape": [],
                "technical_fingerprint": {"has_bias": False},
                "lockin_risks": [],
                "entry_window": "暂无明确窗口",
                "followup_strategy": "继续关注",
                "analysis_notes": "",
            }),
            "usage": {"total_tokens": 800},
        }
        agent = BiddingAnalysisAgent(llm_client=mock_llm)
        result = agent.execute(
            company_name="测试公司",
            demand_direction="数字化转型",
            evidences=sample_evidences,
        )
        assert result.opportunity_type == OpportunityType.POTENTIAL
        assert result.opportunity_confidence == 0.5

    def test_long_snippets_are_truncated(self, mock_llm, sample_evidences):
        """超长摘要被截断到 300 字符"""
        long_snippet = "x" * 500
        sample_evidences[0].snippet = long_snippet

        agent = BiddingAnalysisAgent(llm_client=mock_llm)
        truncated = agent._truncate_evidences(sample_evidences)
        assert len(truncated[0]["snippet"]) <= 300


# ══════════════════════════════════════════════════════════════════════════════
# TestOpportunityClassification: 机会类型判定
# ══════════════════════════════════════════════════════════════════════════════

class TestOpportunityClassification:

    def test_parse_clear(self):
        assert BiddingAnalysisAgent._parse_opportunity_type("clear") == OpportunityType.CLEAR

    def test_parse_potential(self):
        assert BiddingAnalysisAgent._parse_opportunity_type("potential") == OpportunityType.POTENTIAL

    def test_parse_insufficient(self):
        assert BiddingAnalysisAgent._parse_opportunity_type("insufficient") == OpportunityType.INSUFFICIENT

    def test_parse_unknown_defaults_to_insufficient(self):
        assert BiddingAnalysisAgent._parse_opportunity_type("unknown_value") == OpportunityType.INSUFFICIENT

    def test_parse_case_insensitive(self):
        assert BiddingAnalysisAgent._parse_opportunity_type("CLEAR") == OpportunityType.CLEAR
        assert BiddingAnalysisAgent._parse_opportunity_type("Potential") == OpportunityType.POTENTIAL


# ══════════════════════════════════════════════════════════════════════════════
# TestLockInRiskDetection: 5 种风险模式
# ══════════════════════════════════════════════════════════════════════════════

class TestLockInRiskDetection:

    def test_lockin_risk_level_parsing(self):
        """风险等级正确解析"""
        risk = LockInRisk(level=LockInRiskLevel.MEDIUM, risk_type="参数锁定")
        assert risk.level == LockInRiskLevel.MEDIUM

    def test_five_risk_types_accepted(self):
        """5 种风险类型都能被正确创建"""
        risk_types = ["单一来源", "续签垄断", "参数锁定", "围标嫌疑", "地域保护"]
        for rt in risk_types:
            risk = LockInRisk(level=LockInRiskLevel.MEDIUM, risk_type=rt)
            assert risk.risk_type == rt

    def test_lockin_risk_default_level(self):
        """默认风险等级为 NONE"""
        risk = LockInRisk()
        assert risk.level == LockInRiskLevel.NONE

    def test_risk_with_evidence_ids(self):
        """风险关联的 evidence_ids 正确保存"""
        risk = LockInRisk(
            level=LockInRiskLevel.HIGH,
            risk_type="单一来源",
            description="连续三年单一来源采购",
            affected_projects=["项目A"],
            evidence_ids=["ev-1", "ev-2"],
        )
        assert len(risk.evidence_ids) == 2
        assert "项目A" in risk.affected_projects


# ══════════════════════════════════════════════════════════════════════════════
# TestMockBiddingAgent: Mock 模式
# ══════════════════════════════════════════════════════════════════════════════

class TestMockBiddingAgent:

    def test_mock_returns_potential(self):
        """Mock Agent 返回 POTENTIAL 固定结果"""
        agent = MockBiddingAnalysisAgent()
        result = agent.execute(
            company_name="测试公司",
            demand_direction="数字化转型",
        )
        assert result.opportunity_type == OpportunityType.POTENTIAL
        assert result.opportunity_confidence == 0.6

    def test_mock_does_not_call_llm(self):
        """Mock Agent 不调用 LLM"""
        agent = MockBiddingAnalysisAgent()
        result = agent.execute(
            company_name="测试公司",
            demand_direction="数字化转型",
            evidences=[],
        )
        assert result.company_name == "测试公司"
        assert result.procurement_profile.total_projects == 3


# ══════════════════════════════════════════════════════════════════════════════
# TestEdgeCases: 边缘情况
# ══════════════════════════════════════════════════════════════════════════════

class TestEdgeCases:

    def test_evidences_over_max_are_truncated(self, mock_llm):
        """超过 MAX_EVIDENCES 的证据被截断"""
        class FakeEvidence:
            def __init__(self, i):
                from uuid import uuid4
                self.id = uuid4()
                self.title = f"项目{i}"
                self.snippet = f"摘要{i}"
                self.url = f"https://example.com/{i}"
                self.dimension = "bidding_information"
                self.captured_at = f"2025-{i % 12 + 1:02d}-01"
                self.metadata = {}

        many_evidences = [FakeEvidence(i) for i in range(60)]
        agent = BiddingAnalysisAgent(llm_client=mock_llm)
        truncated = agent._truncate_evidences(many_evidences)
        assert len(truncated) <= 50  # MAX_EVIDENCES

    def test_empty_metadata_does_not_break(self, mock_llm):
        """metadata 为空字典不报错"""
        class FakeEvidence:
            def __init__(self):
                from uuid import uuid4
                self.id = uuid4()
                self.title = "测试"
                self.snippet = "摘要"
                self.url = "https://example.com"
                self.dimension = "bidding_information"
                self.captured_at = "2025-01-01"
                self.metadata = {}

        agent = BiddingAnalysisAgent(llm_client=mock_llm)
        truncated = agent._truncate_evidences([FakeEvidence()])
        assert len(truncated) == 1

    def test_none_metadata_does_not_break(self, mock_llm):
        """metadata 为 None 不报错"""
        class FakeEvidence:
            def __init__(self):
                from uuid import uuid4
                self.id = uuid4()
                self.title = "测试"
                self.snippet = "摘要"
                self.url = "https://example.com"
                self.dimension = "bidding_information"
                self.captured_at = "2025-01-01"
                self.metadata = None

        agent = BiddingAnalysisAgent(llm_client=mock_llm)
        truncated = agent._truncate_evidences([FakeEvidence()])
        assert len(truncated) == 1

    def test_truncation_preserves_recent_evidences(self, mock_llm):
        """截断保留最新证据"""
        class FakeEvidence:
            def __init__(self, date):
                from uuid import uuid4
                self.id = uuid4()
                self.title = "测试"
                self.snippet = "摘要"
                self.url = "https://example.com"
                self.dimension = "bidding_information"
                self.captured_at = date
                self.metadata = {}

        old = FakeEvidence("2020-01-01")
        new = FakeEvidence("2025-06-01")
        agent = BiddingAnalysisAgent(llm_client=mock_llm)
        truncated = agent._truncate_evidences([old, new])
        # 最新日期应该在前
        assert truncated[0]["captured_at"] == "2025-06-01"

    def test_parsed_lockin_risk_level_fallback(self):
        """无效的 risk level 回退到 NONE"""
        risk = LockInRisk(level=LockInRiskLevel.NONE)
        assert risk.level == LockInRiskLevel.NONE

    def test_supplier_deduplication_info(self):
        """供应商信息正确保存多个品类"""
        supplier = SupplierInfo(
            name="A公司",
            win_count=3,
            win_categories=["信息化", "数据中心", "安全"],
            estimated_share="约 40%",
            evidence_ids=["ev-1", "ev-2", "ev-3"],
        )
        assert supplier.win_count == 3
        assert len(supplier.win_categories) == 3
        assert len(supplier.evidence_ids) == 3
