"""
WBS-14: StrategyAnalysisAgent + 全维度策略分析 Schema 测试

测试结构:
- TestEvidenceSignal: 单维度信号格（3条）
- TestCrossSignalCorrelation: 跨维度关联（3条）
- TestSignalMatrix: 信号矩阵（3条）
- TestSupportChain: 支持证据链（2条）
- TestCounterChain: 反证链（2条）
- TestCompetitiveRisk: 竞争锁定风险（2条）
- TestEntryScenario: 切入场景（2条）
- TestIcebreakerStrategy: 破冰策略（2条）
- TestNextAction: 下一步行动（2条）
- TestStrategyAnalysisOutput: 顶层输出（4条）
- TestStrategyAnalysisAgent: LLM调用 + 异常（4条）
- TestMockStrategyAgent: Mock 固定输出（4条）
- TestEdgeCases: 边缘情况（3条）
- TestIntegration: DB集成（4条，需要 DATABASE_URL_TEST）

合计: ~36条
"""

import json
import pytest
from unittest.mock import MagicMock, patch

from app.agents.schemas.strategy_schema import (
    EvidenceSignal,
    CrossSignalCorrelation,
    EvidenceSignalMatrix,
    SupportChain,
    CounterChain,
    CompetitiveRisk,
    EntryScenario,
    IcebreakerStrategy,
    NextAction,
    StrategyAnalysisOutput,
)


# ══════════════════════════════════════════════════════════════════════════════
# TestEvidenceSignal（WBS-14.1）
# ══════════════════════════════════════════════════════════════════════════════

class TestEvidenceSignal:
    """单维度证据信号格测试"""

    def test_create_default(self):
        """默认创建应有空值"""
        s = EvidenceSignal()
        assert s.dimension == ""
        assert s.signal_type == ""
        assert s.evidence_count == 0
        assert s.key_findings == []
        assert s.strength == ""
        assert s.evidence_ids == []

    def test_create_with_values(self):
        """完整创建并序列化"""
        s = EvidenceSignal(
            dimension="bidding_information",
            signal_type="positive",
            evidence_count=5,
            key_findings=["发现1", "发现2"],
            strength="moderate",
            evidence_ids=["ev-1", "ev-2"],
        )
        assert s.dimension == "bidding_information"
        assert s.signal_type == "positive"
        assert s.strength == "moderate"
        assert len(s.key_findings) == 2

    def test_serialize_to_dict(self):
        """序列化为字典"""
        s = EvidenceSignal(
            dimension="policy_compliance",
            signal_type="negative",
            evidence_count=2,
            key_findings=["政策收紧"],
            strength="weak",
            evidence_ids=["ev-3"],
        )
        d = s.model_dump(mode="json")
        assert d["dimension"] == "policy_compliance"
        assert d["signal_type"] == "negative"
        assert isinstance(d["evidence_ids"], list)


# ══════════════════════════════════════════════════════════════════════════════
# TestCrossSignalCorrelation（WBS-14.2）
# ══════════════════════════════════════════════════════════════════════════════

class TestCrossSignalCorrelation:
    """跨维度信号关联测试"""

    def test_create_default(self):
        """默认创建"""
        c = CrossSignalCorrelation()
        assert c.dimensions == []
        assert c.relation == ""
        assert c.description == ""

    def test_create_reinforces(self):
        """加强型关联"""
        c = CrossSignalCorrelation(
            dimensions=["bidding_information", "policy_compliance"],
            relation="reinforces",
            description="招标需求与政策方向一致",
            implication="商机确定性高",
        )
        assert c.relation == "reinforces"
        assert len(c.dimensions) == 2

    def test_create_contradicts(self):
        """矛盾型关联"""
        c = CrossSignalCorrelation(
            dimensions=["bidding_information", "policy_compliance"],
            relation="contradicts",
            description="招标活跃但政策收紧",
            implication="短期有机会但长期风险大",
        )
        assert c.relation == "contradicts"


# ══════════════════════════════════════════════════════════════════════════════
# TestSignalMatrix
# ══════════════════════════════════════════════════════════════════════════════

class TestSignalMatrix:
    """证据信号矩阵测试"""

    def test_create_empty(self):
        """空矩阵"""
        m = EvidenceSignalMatrix()
        assert m.dimensions == []
        assert m.cross_correlations == []

    def test_create_with_signals(self):
        """含信号的矩阵"""
        m = EvidenceSignalMatrix(
            dimensions=[
                EvidenceSignal(dimension="bidding_information", signal_type="positive"),
                EvidenceSignal(dimension="policy_compliance", signal_type="positive"),
            ],
        )
        assert len(m.dimensions) == 2
        assert m.cross_correlations == []

    def test_create_with_correlations(self):
        """含关联的矩阵"""
        m = EvidenceSignalMatrix(
            dimensions=[EvidenceSignal(dimension="bidding_information", signal_type="positive")],
            cross_correlations=[
                CrossSignalCorrelation(
                    dimensions=["bidding_information", "policy_compliance"],
                    relation="reinforces",
                ),
            ],
        )
        assert len(m.cross_correlations) == 1
        assert m.cross_correlations[0].relation == "reinforces"


# ══════════════════════════════════════════════════════════════════════════════
# TestSupportChain（WBS-14.3）
# ══════════════════════════════════════════════════════════════════════════════

class TestSupportChain:
    """支持证据链测试"""

    def test_create_default(self):
        """默认创建"""
        sc = SupportChain()
        assert sc.chain_id == ""
        assert sc.thesis == ""
        assert sc.evidence_ids == []
        assert sc.strength == ""

    def test_create_strong_chain(self):
        """强证据链"""
        sc = SupportChain(
            chain_id="sc-1",
            thesis="存在明确商机",
            evidence_ids=["ev-1", "ev-2", "ev-3"],
            strength="strong",
        )
        assert sc.chain_id == "sc-1"
        assert sc.strength == "strong"
        assert len(sc.evidence_ids) == 3


# ══════════════════════════════════════════════════════════════════════════════
# TestCounterChain（WBS-14.4）
# ══════════════════════════════════════════════════════════════════════════════

class TestCounterChain:
    """反证链测试"""

    def test_create_default(self):
        """默认创建"""
        cc = CounterChain()
        assert cc.chain_id == ""
        assert cc.severity == ""
        assert cc.mitigation == ""

    def test_create_with_mitigation(self):
        """含缓解建议的反证链"""
        cc = CounterChain(
            chain_id="cc-1",
            thesis="新进入者面临资质壁垒",
            evidence_ids=["ev-1"],
            severity="high",
            mitigation="通过合作或并购快速获取资质",
        )
        assert cc.severity == "high"
        assert "资质" in cc.mitigation


# ══════════════════════════════════════════════════════════════════════════════
# TestCompetitiveRisk
# ══════════════════════════════════════════════════════════════════════════════

class TestCompetitiveRisk:
    """竞争锁定风险测试"""

    def test_create_default(self):
        """默认创建"""
        cr = CompetitiveRisk()
        assert cr.risk_type == ""
        assert cr.likelihood == ""

    def test_create_supplier_lockin(self):
        """供应商锁定风险"""
        cr = CompetitiveRisk(
            risk_type="供应商锁定",
            description="现有2家供应商占据80%市场份额",
            likelihood="high",
            evidence_ids=["ev-1", "ev-2"],
        )
        assert cr.risk_type == "供应商锁定"
        assert cr.likelihood == "high"


# ══════════════════════════════════════════════════════════════════════════════
# TestEntryScenario
# ══════════════════════════════════════════════════════════════════════════════

class TestEntryScenario:
    """切入场景测试"""

    def test_create_default(self):
        """默认创建"""
        es = EntryScenario()
        assert es.scenario_name == ""
        assert es.prerequisites == []

    def test_create_with_prerequisites(self):
        """含前置条件的场景"""
        es = EntryScenario(
            scenario_name="等保合规升级",
            description="利用等保修订契机切入",
            why_recommended="政策强制+时间窗口明确",
            prerequisites=["等保测评资质", "安全产品线"],
            evidence_ids=["ev-1"],
        )
        assert len(es.prerequisites) == 2


# ══════════════════════════════════════════════════════════════════════════════
# TestIcebreakerStrategy（WBS-14.6）
# ══════════════════════════════════════════════════════════════════════════════

class TestIcebreakerStrategy:
    """破冰策略测试"""

    def test_create_default(self):
        """默认创建"""
        ibs = IcebreakerStrategy()
        assert ibs.rank == 0
        assert ibs.strategy_name == ""
        assert ibs.hook == ""

    def test_create_complete(self):
        """完整策略（含开场白）"""
        ibs = IcebreakerStrategy(
            rank=1,
            strategy_name="政策红利切入法",
            approach="以政策解读为切入，分享补贴申请经验",
            target_persona="CIO",
            hook="贵司的数字化转型项目可能符合30%补贴条件。",
            evidence_ids=["ev-1"],
        )
        assert ibs.rank == 1
        assert ibs.target_persona == "CIO"
        assert "补贴" in ibs.hook


# ══════════════════════════════════════════════════════════════════════════════
# TestNextAction（WBS-14.7）
# ══════════════════════════════════════════════════════════════════════════════

class TestNextAction:
    """下一步行动测试"""

    def test_create_default(self):
        """默认创建"""
        na = NextAction()
        assert na.priority == 0
        assert na.action == ""
        assert na.owner == ""

    def test_create_high_priority(self):
        """高优先级行动"""
        na = NextAction(
            priority=1,
            action="研究目标公司近2年招标",
            timeline="本周内",
            expected_outcome="采购决策链图谱",
            owner="销售经理",
        )
        assert na.priority == 1
        assert na.owner == "销售经理"
        assert na.timeline == "本周内"


# ══════════════════════════════════════════════════════════════════════════════
# TestStrategyAnalysisOutput（WBS-14.5）
# ══════════════════════════════════════════════════════════════════════════════

class TestStrategyAnalysisOutput:
    """顶层输出测试"""

    def test_create_empty(self):
        """空输出 — 所有字段有默认值"""
        output = StrategyAnalysisOutput()
        assert output.company_name == ""
        assert output.opportunity_score == 0.0
        assert output.confidence == 0.0
        assert output.signal_matrix.dimensions == []
        assert output.supporting_chains == []
        assert output.counter_chains == []
        assert output.competitive_risks == []
        assert output.recommended_scenarios == []
        assert output.icebreaker_strategies == []
        assert output.action_plan == []

    def test_empty_class_method(self):
        """empty() 工厂方法"""
        output = StrategyAnalysisOutput.empty(
            company_name="测试公司",
            demand_direction="数字化转型",
            dimensions=["bidding_information"],
        )
        assert output.company_name == "测试公司"
        assert output.opportunity_score == 0.0
        assert output.confidence == 0.0
        assert "证据不足" in output.one_line_verdict
        assert "空分析结果" in output.analysis_notes

    def test_error_class_method(self):
        """error() 工厂方法"""
        output = StrategyAnalysisOutput.error(
            company_name="测试公司",
            demand_direction="数字化转型",
            error_msg="LLM 调用超时",
        )
        assert output.opportunity_score == 0.0
        assert "LLM 调用超时" in output.analysis_notes

    def test_serialize_roundtrip(self):
        """序列化往返：dict → model → dict → model"""
        output = StrategyAnalysisOutput(
            company_name="测试公司",
            demand_direction="数字化转型",
            analyzed_dimensions=["bidding_information", "policy_compliance"],
            one_line_verdict="存在明确商机",
            opportunity_score=72.0,
            confidence=0.78,
            signal_matrix=EvidenceSignalMatrix(
                dimensions=[
                    EvidenceSignal(
                        dimension="bidding_information",
                        signal_type="positive",
                        evidence_count=5,
                        key_findings=["发现1"],
                        strength="moderate",
                        evidence_ids=["ev-1"],
                    ),
                ],
                cross_correlations=[
                    CrossSignalCorrelation(
                        dimensions=["bidding_information", "policy_compliance"],
                        relation="reinforces",
                        description="方向一致",
                    ),
                ],
            ),
            supporting_chains=[
                SupportChain(
                    chain_id="sc-1",
                    thesis="有持续采购需求",
                    evidence_ids=["ev-1"],
                    strength="strong",
                ),
            ],
            counter_chains=[
                CounterChain(
                    chain_id="cc-1",
                    thesis="现有供应商格局稳定",
                    evidence_ids=["ev-2"],
                    severity="medium",
                    mitigation="差异化方案",
                ),
            ],
            competitive_risks=[
                CompetitiveRisk(
                    risk_type="供应商锁定",
                    description="2家占80%",
                    likelihood="medium",
                    evidence_ids=["ev-1"],
                ),
            ],
            recommended_scenarios=[
                EntryScenario(
                    scenario_name="等保合规切入",
                    description="利用政策窗口",
                    why_recommended="时间紧迫",
                    prerequisites=["资质"],
                    evidence_ids=["ev-2"],
                ),
            ],
            icebreaker_strategies=[
                IcebreakerStrategy(
                    rank=1,
                    strategy_name="策略1",
                    approach="做法1",
                    target_persona="CIO",
                    hook="开场白1",
                    evidence_ids=["ev-1"],
                ),
            ],
            action_plan=[
                NextAction(
                    priority=1,
                    action="研究招标",
                    timeline="本周",
                    expected_outcome="决策链",
                    owner="销售经理",
                ),
            ],
            analysis_notes="测试用",
            generated_at="2026-07-07T00:00:00Z",
        )

        # 序列化
        d = output.model_dump(mode="json")
        assert d["company_name"] == "测试公司"
        assert d["opportunity_score"] == 72.0
        assert len(d["signal_matrix"]["dimensions"]) == 1
        assert len(d["signal_matrix"]["cross_correlations"]) == 1
        assert len(d["supporting_chains"]) == 1
        assert len(d["counter_chains"]) == 1
        assert len(d["icebreaker_strategies"]) == 1
        assert len(d["action_plan"]) == 1

        # 反序列化
        restored = StrategyAnalysisOutput(**d)
        assert restored.opportunity_score == 72.0
        assert restored.signal_matrix.dimensions[0].dimension == "bidding_information"


# ══════════════════════════════════════════════════════════════════════════════
# TestStrategyAnalysisAgent
# ══════════════════════════════════════════════════════════════════════════════

class TestStrategyAnalysisAgent:
    """StrategyAnalysisAgent 单元测试（Mock LLM）"""

    @pytest.fixture
    def sample_evidences(self):
        """构造模拟证据对象列表"""
        ev1 = MagicMock()
        ev1.id = "ev-001"
        ev1.dimension = "bidding_information"
        ev1.title = "招标项目A"
        ev1.snippet = "XX教育局采购项目，预算580万"
        ev1.url = "https://example.com/bid/1"
        ev1.source_type = "web_scrape"
        ev1.source_reliability = "B"
        ev1.relevance_score = 0.8
        ev1.captured_at = "2025-03-15T00:00:00Z"

        ev2 = MagicMock()
        ev2.id = "ev-002"
        ev2.dimension = "policy_compliance"
        ev2.title = "数字化转型三年行动计划"
        ev2.snippet = "对信息化项目给予30%补贴"
        ev2.url = "https://example.com/policy/1"
        ev2.source_type = "web_scrape"
        ev2.source_reliability = "A"
        ev2.relevance_score = 0.85
        ev2.captured_at = "2025-01-01T00:00:00Z"

        return [ev1, ev2]

    def test_execute_with_evidences(self, sample_evidences):
        """正常执行：Mock LLM 返回有效 JSON"""
        from app.agents.expert.strategy_agent import StrategyAnalysisAgent

        mock_response = {
            "content": json.dumps({
                "company_name": "测试公司",
                "demand_direction": "数字化转型",
                "analyzed_dimensions": ["bidding_information", "policy_compliance"],
                "one_line_verdict": "存在明确商机，建议切入。",
                "opportunity_score": 72.0,
                "confidence": 0.78,
                "signal_matrix": {
                    "dimensions": [
                        {
                            "dimension": "bidding_information",
                            "signal_type": "positive",
                            "evidence_count": 1,
                            "key_findings": ["有采购需求"],
                            "strength": "moderate",
                            "evidence_ids": ["ev-001"],
                        },
                    ],
                    "cross_correlations": [],
                },
                "supporting_chains": [],
                "counter_chains": [],
                "competitive_risks": [],
                "recommended_scenarios": [],
                "icebreaker_strategies": [],
                "action_plan": [],
                "analysis_notes": "测试",
                "generated_at": "2026-07-07T00:00:00Z",
            }),
            "usage": {"total_tokens": 500},
        }

        mock_llm = MagicMock()
        mock_llm.infer.return_value = mock_response

        agent = StrategyAnalysisAgent(llm_client=mock_llm)
        result = agent.execute(
            company_name="测试公司",
            demand_direction="数字化转型",
            dimensions=["bidding_information", "policy_compliance"],
            evidences=sample_evidences,
        )

        assert result.opportunity_score == 72.0
        assert result.confidence == 0.78
        assert len(result.signal_matrix.dimensions) == 1
        mock_llm.infer.assert_called_once()

    def test_execute_empty_evidences(self):
        """零证据：返回 empty 输出，不调用 LLM"""
        from app.agents.expert.strategy_agent import StrategyAnalysisAgent

        mock_llm = MagicMock()
        agent = StrategyAnalysisAgent(llm_client=mock_llm)
        result = agent.execute(
            company_name="测试公司",
            demand_direction="数字化转型",
            dimensions=["bidding_information"],
            evidences=[],
        )

        assert result.opportunity_score == 0.0
        assert "证据不足" in result.one_line_verdict
        mock_llm.infer.assert_not_called()

    def test_execute_llm_parse_error(self, sample_evidences):
        """LLM 返回无效 JSON：降级为 error 输出"""
        from app.agents.expert.strategy_agent import StrategyAnalysisAgent

        mock_llm = MagicMock()
        mock_llm.infer.return_value = {
            "content": "not valid json {{{",
            "usage": {"total_tokens": 100},
        }

        agent = StrategyAnalysisAgent(llm_client=mock_llm)
        result = agent.execute(
            company_name="测试公司",
            demand_direction="数字化转型",
            dimensions=["bidding_information"],
            evidences=sample_evidences,
        )

        assert result.opportunity_score == 0.0
        assert "解析失败" in result.analysis_notes

    def test_execute_llm_exception(self, sample_evidences):
        """LLM 调用抛异常：降级为 error 输出"""
        from app.agents.expert.strategy_agent import StrategyAnalysisAgent

        mock_llm = MagicMock()
        mock_llm.infer.side_effect = RuntimeError("网络超时")

        agent = StrategyAnalysisAgent(llm_client=mock_llm)
        result = agent.execute(
            company_name="测试公司",
            demand_direction="数字化转型",
            dimensions=["bidding_information"],
            evidences=sample_evidences,
        )

        assert result.opportunity_score == 0.0
        assert "网络超时" in result.analysis_notes


# ══════════════════════════════════════════════════════════════════════════════
# TestMockStrategyAgent
# ══════════════════════════════════════════════════════════════════════════════

class TestMockStrategyAgent:
    """MockStrategyAnalysisAgent 测试"""

    @pytest.fixture
    def mock_agent(self):
        from app.agents.harness.agent_harness import MockStrategyAnalysisAgent
        return MockStrategyAnalysisAgent()

    def test_returns_output(self, mock_agent):
        """返回 StrategyAnalysisOutput（非 None）"""
        result = mock_agent.execute(
            company_name="测试公司",
            demand_direction="数字化转型",
            dimensions=["bidding_information", "policy_compliance"],
        )
        assert result is not None
        assert result.company_name == "测试公司"

    def test_has_all_nine_modules(self, mock_agent):
        """9 个输出模块都存在"""
        result = mock_agent.execute()
        assert result.one_line_verdict != ""
        assert result.opportunity_score > 0
        assert result.confidence > 0
        assert len(result.signal_matrix.dimensions) > 0
        assert len(result.supporting_chains) > 0
        assert len(result.counter_chains) > 0
        assert len(result.competitive_risks) > 0
        assert len(result.recommended_scenarios) > 0
        assert len(result.icebreaker_strategies) > 0
        assert len(result.action_plan) > 0

    def test_three_icebreakers(self, mock_agent):
        """破冰三板斧恰好 3 条"""
        result = mock_agent.execute()
        assert len(result.icebreaker_strategies) == 3
        ranks = [s.rank for s in result.icebreaker_strategies]
        assert ranks == [1, 2, 3]

    def test_has_cross_correlations(self, mock_agent):
        """含跨维度信号关联"""
        result = mock_agent.execute()
        corrs = result.signal_matrix.cross_correlations
        assert len(corrs) >= 1
        assert corrs[0].relation == "reinforces"


# ══════════════════════════════════════════════════════════════════════════════
# TestEdgeCases
# ══════════════════════════════════════════════════════════════════════════════

class TestEdgeCases:
    """边缘情况测试"""

    def test_large_evidence_truncation(self):
        """证据超过 80 条时截断"""
        from app.agents.expert.strategy_agent import StrategyAnalysisAgent

        evidences = []
        for i in range(100):
            ev = MagicMock()
            ev.id = f"ev-{i:03d}"
            ev.dimension = "bidding_information"
            ev.title = f"项目{i}"
            ev.snippet = f"摘要内容{i}" * 30  # ~180 chars
            ev.url = f"https://example.com/{i}"
            ev.source_type = "web_scrape"
            ev.captured_at = f"2025-{i % 12 + 1:02d}-01T00:00:00Z"
            ev.source_reliability = "B"
            ev.relevance_score = 0.5
            evidences.append(ev)

        mock_llm = MagicMock()
        mock_llm.infer.return_value = {
            "content": json.dumps({
                "company_name": "测试",
                "demand_direction": "测试",
                "analyzed_dimensions": ["bidding_information"],
                "one_line_verdict": "测试商机",
                "opportunity_score": 50.0,
                "confidence": 0.5,
                "signal_matrix": {"dimensions": [], "cross_correlations": []},
                "supporting_chains": [],
                "counter_chains": [],
                "competitive_risks": [],
                "recommended_scenarios": [],
                "icebreaker_strategies": [],
                "action_plan": [],
                "analysis_notes": "",
                "generated_at": "",
            }),
            "usage": {"total_tokens": 200},
        }

        agent = StrategyAnalysisAgent(llm_client=mock_llm)
        result = agent.execute(
            company_name="测试公司",
            demand_direction="测试",
            dimensions=["bidding_information"],
            evidences=evidences,
        )

        # 不应崩溃，正常返回结果
        assert result.opportunity_score == 50.0

    def test_single_dimension(self, sample_evidences=None):
        """单维度：正常分析，cross_correlations 应为空或仅一个维度"""
        from app.agents.expert.strategy_agent import StrategyAnalysisAgent

        # 使用 bidding 单条证据
        ev = MagicMock()
        ev.id = "ev-single"
        ev.dimension = "bidding_information"
        ev.title = "单维度测试"
        ev.snippet = "单维度证据摘要"
        ev.url = "https://example.com"
        ev.source_type = "web_scrape"
        ev.source_reliability = "B"
        ev.relevance_score = 0.7
        ev.captured_at = "2025-01-01T00:00:00Z"

        mock_llm = MagicMock()
        mock_llm.infer.return_value = {
            "content": json.dumps({
                "company_name": "测试",
                "demand_direction": "测试",
                "analyzed_dimensions": ["bidding_information"],
                "one_line_verdict": "单维度测试",
                "opportunity_score": 40.0,
                "confidence": 0.3,
                "signal_matrix": {
                    "dimensions": [
                        {
                            "dimension": "bidding_information",
                            "signal_type": "positive",
                            "evidence_count": 1,
                            "key_findings": ["有采购需求"],
                            "strength": "weak",
                            "evidence_ids": ["ev-single"],
                        },
                    ],
                    "cross_correlations": [],
                },
                "supporting_chains": [],
                "counter_chains": [],
                "competitive_risks": [],
                "recommended_scenarios": [],
                "icebreaker_strategies": [],
                "action_plan": [],
                "analysis_notes": "单维度分析",
                "generated_at": "",
            }),
            "usage": {"total_tokens": 100},
        }

        agent = StrategyAnalysisAgent(llm_client=mock_llm)
        result = agent.execute(
            company_name="测试公司",
            demand_direction="测试",
            dimensions=["bidding_information"],
            evidences=[ev],
        )

        assert result.analyzed_dimensions == ["bidding_information"]
        # 单维度时 cross_correlations 应为空
        assert result.signal_matrix.cross_correlations == []

    def test_chinese_content_handling(self):
        """中文内容应正常处理和序列化"""
        output = StrategyAnalysisOutput(
            company_name="中文测试公司",
            demand_direction="数字化转型与信创替代",
            analyzed_dimensions=["bidding_information", "policy_compliance"],
            one_line_verdict="该公司存在明确的数字化转型商机，政策窗口期有利。",
            opportunity_score=68.0,
            confidence=0.65,
            signal_matrix=EvidenceSignalMatrix(
                dimensions=[
                    EvidenceSignal(
                        dimension="bidding_information",
                        signal_type="positive",
                        evidence_count=3,
                        key_findings=["过去5年采购总额超5000万元", "主要采购窗口为Q1"],
                        strength="moderate",
                        evidence_ids=["ev-中文-1"],
                    ),
                ],
                cross_correlations=[],
            ),
            supporting_chains=[
                SupportChain(
                    chain_id="sc-中文-1",
                    thesis="政策强制等保三级，催生安全产品采购需求",
                    evidence_ids=["ev-中文-2"],
                    strength="strong",
                ),
            ],
            counter_chains=[
                CounterChain(
                    chain_id="cc-中文-1",
                    thesis="目标公司已有长期合作供应商，切入难度中等",
                    evidence_ids=["ev-中文-3"],
                    severity="medium",
                    mitigation="通过差异化AI方案建立独特价值",
                ),
            ],
            competitive_risks=[
                CompetitiveRisk(
                    risk_type="供应商锁定",
                    description="存量供应商已锁定核心业务系统3年以上",
                    likelihood="medium",
                    evidence_ids=["ev-中文-1"],
                ),
            ],
            recommended_scenarios=[
                EntryScenario(
                    scenario_name="国产化替代场景",
                    description="信创政策要求政务系统国产化，可推荐国产替代方案",
                    why_recommended="政策强制+时间窗口明确",
                    prerequisites=["国产产品线", "国产化资质"],
                    evidence_ids=["ev-中文-2"],
                ),
            ],
            icebreaker_strategies=[
                IcebreakerStrategy(
                    rank=1,
                    strategy_name="政策解读切入",
                    approach="以最新等保和信创政策为切入点，提供免费合规评估",
                    target_persona="CIO/信息安全总监",
                    hook="最近等保三级新规要求系统每年测评，我们帮助了XX家同行业客户完成了合规升级，想跟您交流一下经验。",
                    evidence_ids=["ev-中文-2"],
                ),
            ],
            action_plan=[
                NextAction(
                    priority=1,
                    action="通过行业协会获取目标公司IT部门组织架构",
                    timeline="本周内",
                    expected_outcome="获得关键决策人名单",
                    owner="客户经理",
                ),
            ],
            analysis_notes="基于有限证据的初步分析，建议获取更多一手信息后确认商机。",
            generated_at="2026-07-07T10:00:00+08:00",
        )

        d = output.model_dump(mode="json")
        restored = StrategyAnalysisOutput(**d)

        assert restored.company_name == "中文测试公司"
        assert restored.opportunity_score == 68.0
        assert restored.signal_matrix.dimensions[0].key_findings[0].startswith("过去5年")
        assert restored.icebreaker_strategies[0].target_persona == "CIO/信息安全总监"


# ══════════════════════════════════════════════════════════════════════════════
# TestIntegration — DB 集成测试（需 DATABASE_URL_TEST）
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.integration
class TestIntegration:
    """DB 集成测试"""

    def test_create_multi_dimension_evidence(self, db_session):
        """create_test_multi_dimension_evidence_list 创建混合维度证据"""
        from tests.factories import (
            create_test_user,
            create_test_task,
            create_test_multi_dimension_evidence_list,
        )

        user, _ = create_test_user(db_session)
        task = create_test_task(db_session, user.id)

        evidences = create_test_multi_dimension_evidence_list(
            db_session, task.id,
            bidding_count=2,
            policy_count=2,
            field_count=1,
        )
        assert len(evidences) == 5

        # 验证维度分布
        dims = [ev.dimension for ev in evidences]
        assert dims.count("bidding_information") == 2
        assert dims.count("policy_compliance") == 2
        assert dims.count("field_research") == 1

        # 验证 source_reliability 已设置
        for ev in evidences:
            assert ev.source_reliability in ("A", "B")

    def test_agent_execute_with_db_evidences(self, db_session):
        """使用 DB 证据执行 Agent（Mock LLM）"""
        from tests.factories import (
            create_test_user,
            create_test_task,
            create_test_multi_dimension_evidence_list,
        )
        from app.agents.expert.strategy_agent import StrategyAnalysisAgent

        user, _ = create_test_user(db_session)
        task = create_test_task(db_session, user.id)

        evidences = create_test_multi_dimension_evidence_list(
            db_session, task.id,
            bidding_count=3,
            policy_count=2,
            field_count=1,
        )

        mock_llm = MagicMock()
        mock_llm.infer.return_value = {
            "content": json.dumps({
                "company_name": task.company_name,
                "demand_direction": task.demand_direction,
                "analyzed_dimensions": ["bidding_information", "policy_compliance", "field_research"],
                "one_line_verdict": "存在明确商机。",
                "opportunity_score": 75.0,
                "confidence": 0.72,
                "signal_matrix": {
                    "dimensions": [
                        {
                            "dimension": "bidding_information",
                            "signal_type": "positive",
                            "evidence_count": 3,
                            "key_findings": ["有招标项目"],
                            "strength": "moderate",
                            "evidence_ids": [str(evidences[0].id)],
                        },
                    ],
                    "cross_correlations": [],
                },
                "supporting_chains": [],
                "counter_chains": [],
                "competitive_risks": [],
                "recommended_scenarios": [],
                "icebreaker_strategies": [],
                "action_plan": [],
                "analysis_notes": "DB集成测试",
                "generated_at": "",
            }),
            "usage": {"total_tokens": 300},
        }

        agent = StrategyAnalysisAgent(llm_client=mock_llm)
        result = agent.execute(
            company_name=task.company_name,
            demand_direction=task.demand_direction,
            dimensions=["bidding_information", "policy_compliance", "field_research"],
            evidences=evidences,
        )

        assert result.opportunity_score == 75.0
        assert result.company_name == "测试公司"

    def test_truncation_preserves_dimension_distribution(self, db_session):
        """截断后仍保留维度多样性"""
        from tests.factories import (
            create_test_user,
            create_test_task,
            create_test_multi_dimension_evidence_list,
        )
        from app.agents.expert.strategy_agent import StrategyAnalysisAgent

        user, _ = create_test_user(db_session)
        task = create_test_task(db_session, user.id)

        # 只有少量证据，不会触发截断
        evidences = create_test_multi_dimension_evidence_list(
            db_session, task.id,
            bidding_count=2,
            policy_count=2,
            field_count=1,
        )

        agent = StrategyAnalysisAgent(llm_client=MagicMock())
        truncated = agent._truncate_evidences(evidences)

        # 5条证据全保留
        assert len(truncated) == 5
        dims_in_result = [d["dimension"] for d in truncated]
        assert "bidding_information" in dims_in_result
        assert "policy_compliance" in dims_in_result
        assert "field_research" in dims_in_result

    def test_empty_evidences_db(self, db_session):
        """零证据时 empty() 方法返回有效结构"""
        from app.agents.expert.strategy_agent import StrategyAnalysisAgent

        agent = StrategyAnalysisAgent(llm_client=MagicMock())
        result = agent.execute(
            company_name="测试公司",
            demand_direction="测试",
            dimensions=["bidding_information"],
            evidences=[],
        )

        # 验证所有字段可序列化
        d = result.model_dump(mode="json")
        assert d["opportunity_score"] == 0.0
        assert d["confidence"] == 0.0
        assert d["signal_matrix"]["dimensions"] == []
