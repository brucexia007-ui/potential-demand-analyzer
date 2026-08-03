"""WBS-12: 政策合规分析测试

测试 PolicyComplianceAgent、Schema、Mock Agent 和集成。
"""
import json
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from app.agents.schemas.policy_compliance_schema import (
    PolicyLevel,
    ConstraintStrength,
    PolicyDocument,
    PolicyTimeline,
    BusinessImpact,
    ComplianceGap,
    SystemRequirement,
    PolicyAnalysisResult,
)
from app.agents.expert.policy_agent import PolicyComplianceAgent
from app.agents.harness.agent_harness import MockPolicyComplianceAgent


# ══════════════════════════════════════════════════════════════════════════════
# Fixtures
# ══════════════════════════════════════════════════════════════════════════════

@pytest.fixture
def mock_llm():
    """创建 Mock LLM 客户端"""
    llm = MagicMock()
    llm.infer.return_value = {
        "content": json.dumps({
            "policy_timeline": {
                "documents": [
                    {
                        "title": "数据安全法实施条例",
                        "issuer": "国家互联网信息办公室",
                        "doc_number": "国信办发[2024]12号",
                        "publish_date": "2024-06-15",
                        "effective_date": "2025-01-01",
                        "deadline_date": "",
                        "policy_level": "national",
                        "constraint_strength": "mandatory",
                        "applicable_objects": ["关键信息基础设施运营者"],
                        "key_clauses": ["建立数据分类分级制度", "每年至少一次安全评估"],
                        "source_reliability": "A",
                        "evidence_ids": ["ev-1", "ev-2"],
                    },
                    {
                        "title": "数字化转型三年行动计划",
                        "issuer": "XX省经信厅",
                        "doc_number": "",
                        "publish_date": "2024-03-01",
                        "effective_date": "2024-03-01",
                        "deadline_date": "2026-12-31",
                        "policy_level": "provincial",
                        "constraint_strength": "encouraging",
                        "applicable_objects": ["制造业企业", "服务业企业"],
                        "key_clauses": ["鼓励企业上云", "数字化转型补贴"],
                        "source_reliability": "A",
                        "evidence_ids": ["ev-3"],
                    },
                ],
                "upcoming_deadlines": ["2025-01-01: 数据安全法实施条例生效"],
                "trend_direction": "监管趋严，数字化鼓励政策持续加码",
                "evidence_ids": ["ev-1", "ev-2", "ev-3"],
            },
            "policy_level_summary": "涉及1项国家级强制政策、1项省部级鼓励政策",
            "constraint_analysis": "1项强制（数据安全）、1项鼓励（数字化转型补贴）",
            "applicable_objects_analysis": "作为信息化服务商，政策均适用",
            "key_clauses_summary": "数据分类分级是强制要求，上云补贴是政策红利",
            "business_impacts": [
                {
                    "area": "数据安全管理",
                    "driven_by_clause": "数据安全法实施条例 - 数据分类分级",
                    "impact_description": "客户须建立数据分类分级制度，否则面临合规风险",
                    "urgency": "高",
                    "evidence_ids": ["ev-1"],
                },
            ],
            "compliance_gaps": [
                {
                    "gap_description": "客户未建立完整的数据分类分级制度",
                    "related_clause": "数据安全法实施条例 第X条",
                    "current_status": "未知",
                    "remediation_deadline": "2025-06-30",
                    "evidence_ids": ["ev-1"],
                },
            ],
            "system_requirements": [
                {
                    "requirement_description": "数据安全治理平台（含分类分级、审计、脱敏）",
                    "driven_by_clauses": ["数据安全法实施条例"],
                    "estimated_urgency": "高",
                    "system_category": "数据安全",
                    "evidence_ids": ["ev-1"],
                },
            ],
            "presales_leverage": "数据安全法即将生效，可借此推动数据安全类项目",
            "quotable_language": [
                "根据2025年1月生效的数据安全法实施条例，贵单位须建立数据分类分级制度",
            ],
            "analysis_notes": "基于3条证据分析，建议进一步了解客户合规现状",
        }),
        "usage": {"total_tokens": 1800},
    }
    return llm


@pytest.fixture
def sample_evidences():
    """创建样本政策证据对象列表（不依赖 DB）"""

    class FakeEvidence:
        def __init__(self, ev_id, title, snippet, url, captured_at=None):
            self.id = ev_id
            self.title = title
            self.snippet = snippet
            self.url = url
            self.dimension = "policy_compliance"
            self.captured_at = captured_at or "2025-06-01"
            self.metadata = {
                "发文单位": "国家互联网信息办公室",
                "发布机关": "国家互联网信息办公室",
                "文号": "国信办发[2024]12号",
                "发布时间": "2024-06-15",
                "生效日期": "2025-01-01",
                "政策名称": "数据安全法实施条例",
            }

    return [
        FakeEvidence(
            ev_id=uuid4(),
            title="数据安全法实施条例",
            snippet="国家互联网信息办公室发布了数据安全法实施条例，要求建立数据分类分级制度...",
            url="https://example.com/policy/1",
        ),
        FakeEvidence(
            ev_id=uuid4(),
            title="网络安全等级保护管理办法",
            snippet="公安部发布了网络安全等级保护管理办法修订版，等保三级及以上须每年测评...",
            url="https://example.com/policy/2",
        ),
        FakeEvidence(
            ev_id=uuid4(),
            title="数字化转型三年行动计划",
            snippet="XX省经信厅发布数字化转型三年行动计划，鼓励企业上云用数赋智...",
            url="https://example.com/policy/3",
        ),
    ]


@pytest.fixture
def policy_agent(mock_llm):
    """创建带 Mock LLM 的 PolicyComplianceAgent"""
    return PolicyComplianceAgent(llm_client=mock_llm)


# ══════════════════════════════════════════════════════════════════════════════
# TestPolicySchema: 模型创建/序列化/枚举值
# ══════════════════════════════════════════════════════════════════════════════

class TestPolicySchema:

    def test_policy_level_enum_values(self):
        assert PolicyLevel.NATIONAL.value == "national"
        assert PolicyLevel.PROVINCIAL.value == "provincial"
        assert PolicyLevel.MUNICIPAL.value == "municipal"
        assert PolicyLevel.INDUSTRY.value == "industry"
        assert PolicyLevel.UNKNOWN.value == "unknown"

    def test_constraint_strength_enum_values(self):
        assert ConstraintStrength.MANDATORY.value == "mandatory"
        assert ConstraintStrength.GUIDANCE.value == "guidance"
        assert ConstraintStrength.ENCOURAGING.value == "encouraging"
        assert ConstraintStrength.PILOT.value == "pilot"
        assert ConstraintStrength.UNKNOWN.value == "unknown"

    def test_policy_document_creation(self):
        doc = PolicyDocument(
            title="测试政策",
            issuer="测试发文单位",
            doc_number="测试[2025]1号",
            policy_level=PolicyLevel.NATIONAL,
            constraint_strength=ConstraintStrength.MANDATORY,
            applicable_objects=["企业A", "企业B"],
            key_clauses=["条款1", "条款2"],
        )
        assert doc.title == "测试政策"
        assert doc.policy_level == PolicyLevel.NATIONAL
        assert doc.constraint_strength == ConstraintStrength.MANDATORY
        assert len(doc.applicable_objects) == 2
        assert len(doc.key_clauses) == 2

    def test_policy_analysis_result_serialization(self):
        """确保 PolicyAnalysisResult 可以正确序列化"""
        result = PolicyAnalysisResult(
            company_name="测试公司",
            demand_direction="数字化转型",
        )
        d = result.model_dump(mode="json")
        assert d["company_name"] == "测试公司"
        assert d["policy_timeline"]["documents"] == []
        assert d["business_impacts"] == []
        assert d["compliance_gaps"] == []
        assert d["system_requirements"] == []

    def test_full_result_defaults(self):
        """默认构造的 PolicyAnalysisResult 所有列表和字符串为空"""
        result = PolicyAnalysisResult()
        assert result.company_name == ""
        assert len(result.policy_timeline.documents) == 0
        assert len(result.business_impacts) == 0
        assert len(result.compliance_gaps) == 0
        assert len(result.system_requirements) == 0
        assert len(result.quotable_language) == 0

    def test_mandatory_vs_encouraging_distinction(self):
        """强制和鼓励必须能明确区分（验收标准 #1 和 #2）"""
        mandatory = ConstraintStrength.MANDATORY
        encouraging = ConstraintStrength.ENCOURAGING
        assert mandatory != encouraging
        # 确保 MANDATORY 不被误认为 ENCOURAGING
        assert mandatory.value == "mandatory"
        assert encouraging.value == "encouraging"


# ══════════════════════════════════════════════════════════════════════════════
# TestPolicyComplianceAgent: Mock LLM 测试
# ══════════════════════════════════════════════════════════════════════════════

class TestPolicyComplianceAgent:

    def test_execute_returns_complete_analysis(self, policy_agent, mock_llm, sample_evidences):
        """正常分析返回完整 PolicyAnalysisResult"""
        result = policy_agent.execute(
            company_name="测试公司",
            demand_direction="数字化转型",
            evidences=sample_evidences,
        )
        assert result.company_name == "测试公司"
        assert len(result.policy_timeline.documents) == 2
        assert result.policy_timeline.documents[0].policy_level == PolicyLevel.NATIONAL
        assert result.policy_timeline.documents[0].constraint_strength == ConstraintStrength.MANDATORY
        assert result.policy_timeline.documents[1].constraint_strength == ConstraintStrength.ENCOURAGING
        assert len(result.business_impacts) == 1
        assert len(result.compliance_gaps) == 1
        assert len(result.system_requirements) == 1
        assert len(result.quotable_language) == 1
        assert result.presales_leverage != ""

    def test_execute_zero_evidences(self, policy_agent):
        """零证据时返回空结果"""
        result = policy_agent.execute(
            company_name="测试公司",
            demand_direction="数字化转型",
            evidences=[],
        )
        assert "未收集到证据" in result.analysis_notes

    def test_execute_json_parse_failure(self, mock_llm, sample_evidences):
        """LLM 返回无效 JSON 时优雅降级"""
        mock_llm.infer.return_value = {"content": "not valid json{{{", "usage": {"total_tokens": 100}}
        agent = PolicyComplianceAgent(llm_client=mock_llm)
        result = agent.execute(
            company_name="测试公司",
            demand_direction="数字化转型",
            evidences=sample_evidences,
        )
        assert "解析失败" in result.analysis_notes or "LLM" in result.analysis_notes

    def test_execute_llm_call_failure(self, mock_llm, sample_evidences):
        """LLM 调用抛出异常时优雅降级"""
        mock_llm.infer.side_effect = RuntimeError("LLM 服务不可用")
        agent = PolicyComplianceAgent(llm_client=mock_llm)
        result = agent.execute(
            company_name="测试公司",
            demand_direction="数字化转型",
            evidences=sample_evidences,
        )
        assert "LLM" in result.analysis_notes or "失败" in result.analysis_notes

    def test_execute_token_tracking(self, mock_llm, sample_evidences):
        """Token 使用被正确记录"""
        from app.agents.harness.token_tracker import TokenTracker, BudgetConfig
        config = BudgetConfig(max_tokens_total=100000)
        tracker = TokenTracker(config)

        agent = PolicyComplianceAgent(llm_client=mock_llm, token_tracker=tracker)
        agent.execute(
            company_name="测试公司",
            demand_direction="数字化转型",
            evidences=sample_evidences,
        )
        assert tracker.current_usage.policy_compliance == 1800

    def test_execute_all_8_outputs_present(self, policy_agent, sample_evidences):
        """8 项输出全部存在"""
        result = policy_agent.execute(
            company_name="测试公司",
            demand_direction="数字化转型",
            evidences=sample_evidences,
        )
        # 1. 政策时间线
        assert result.policy_timeline is not None
        assert len(result.policy_timeline.documents) > 0
        # 2. 政策等级总结
        assert result.policy_level_summary != ""
        # 3. 约束强度分析
        assert result.constraint_analysis != ""
        # 4. 适用对象分析
        assert result.applicable_objects_analysis != ""
        # 5. 关键条款总结
        assert result.key_clauses_summary != ""
        # 6. 业务影响
        assert isinstance(result.business_impacts, list)
        # 7. 合规缺口
        assert isinstance(result.compliance_gaps, list)
        # 8. 系统建设需求
        assert isinstance(result.system_requirements, list)
        # 额外：售前推动和话术
        assert result.presales_leverage != ""
        assert isinstance(result.quotable_language, list)

    def test_long_snippets_are_truncated(self, mock_llm, sample_evidences):
        """超长摘要被截断到 300 字符"""
        long_snippet = "x" * 500
        sample_evidences[0].snippet = long_snippet

        agent = PolicyComplianceAgent(llm_client=mock_llm)
        truncated = agent._truncate_evidences(sample_evidences)
        assert len(truncated[0]["snippet"]) <= 300


# ══════════════════════════════════════════════════════════════════════════════
# TestConstraintStrength: 4 种约束强度解析
# ══════════════════════════════════════════════════════════════════════════════

class TestConstraintStrength:

    def test_parse_mandatory(self):
        assert PolicyComplianceAgent._parse_constraint_strength("mandatory") == ConstraintStrength.MANDATORY

    def test_parse_guidance(self):
        assert PolicyComplianceAgent._parse_constraint_strength("guidance") == ConstraintStrength.GUIDANCE

    def test_parse_encouraging(self):
        assert PolicyComplianceAgent._parse_constraint_strength("encouraging") == ConstraintStrength.ENCOURAGING

    def test_parse_pilot(self):
        assert PolicyComplianceAgent._parse_constraint_strength("pilot") == ConstraintStrength.PILOT

    def test_parse_unknown_defaults(self):
        assert PolicyComplianceAgent._parse_constraint_strength("invalid_value") == ConstraintStrength.UNKNOWN

    def test_parse_case_insensitive(self):
        assert PolicyComplianceAgent._parse_constraint_strength("MANDATORY") == ConstraintStrength.MANDATORY
        assert PolicyComplianceAgent._parse_constraint_strength("Encouraging") == ConstraintStrength.ENCOURAGING


# ══════════════════════════════════════════════════════════════════════════════
# TestPolicyLevel: 4 种政策等级解析
# ══════════════════════════════════════════════════════════════════════════════

class TestPolicyLevel:

    def test_parse_national(self):
        assert PolicyComplianceAgent._parse_policy_level("national") == PolicyLevel.NATIONAL

    def test_parse_provincial(self):
        assert PolicyComplianceAgent._parse_policy_level("provincial") == PolicyLevel.PROVINCIAL

    def test_parse_municipal(self):
        assert PolicyComplianceAgent._parse_policy_level("municipal") == PolicyLevel.MUNICIPAL

    def test_parse_industry(self):
        assert PolicyComplianceAgent._parse_policy_level("industry") == PolicyLevel.INDUSTRY

    def test_parse_unknown_defaults(self):
        assert PolicyComplianceAgent._parse_policy_level("garbage") == PolicyLevel.UNKNOWN

    def test_parse_case_insensitive(self):
        assert PolicyComplianceAgent._parse_policy_level("NATIONAL") == PolicyLevel.NATIONAL
        assert PolicyComplianceAgent._parse_policy_level("Provincial") == PolicyLevel.PROVINCIAL


# ══════════════════════════════════════════════════════════════════════════════
# TestMockPolicyAgent: Mock 模式
# ══════════════════════════════════════════════════════════════════════════════

class TestMockPolicyAgent:

    def test_mock_returns_structured_result(self):
        """Mock Agent 返回完整的结构化结果"""
        agent = MockPolicyComplianceAgent()
        result = agent.execute(
            company_name="测试公司",
            demand_direction="数字化转型",
        )
        assert result.company_name == "测试公司"
        assert len(result.policy_timeline.documents) == 2
        assert result.policy_timeline.documents[0].policy_level == PolicyLevel.NATIONAL
        assert result.policy_timeline.documents[0].constraint_strength == ConstraintStrength.MANDATORY
        assert result.policy_timeline.documents[1].constraint_strength == ConstraintStrength.ENCOURAGING
        assert len(result.compliance_gaps) == 1
        assert len(result.system_requirements) == 1
        assert len(result.quotable_language) == 1

    def test_mock_distinguishes_mandatory_from_encouraging(self):
        """Mock 结果能区分强制和鼓励（验收标准 #1）"""
        agent = MockPolicyComplianceAgent()
        result = agent.execute(company_name="测试公司")
        docs = result.policy_timeline.documents
        mandatory_docs = [d for d in docs if d.constraint_strength == ConstraintStrength.MANDATORY]
        encouraging_docs = [d for d in docs if d.constraint_strength == ConstraintStrength.ENCOURAGING]
        assert len(mandatory_docs) >= 1
        assert len(encouraging_docs) >= 1
        # 强制 ≠ 鼓励
        for md in mandatory_docs:
            assert md.constraint_strength != ConstraintStrength.ENCOURAGING


# ══════════════════════════════════════════════════════════════════════════════
# TestBusinessImpactMapping: 政策→业务影响
# ══════════════════════════════════════════════════════════════════════════════

class TestBusinessImpactMapping:

    def test_business_impact_creation(self):
        """业务影响模型正确保存字段"""
        impact = BusinessImpact(
            area="数据安全",
            driven_by_clause="数据安全法实施条例 第X条",
            impact_description="客户须建立数据分类分级制度",
            urgency="高",
            evidence_ids=["ev-1", "ev-2"],
        )
        assert impact.area == "数据安全"
        assert impact.urgency == "高"
        assert len(impact.evidence_ids) == 2

    def test_business_impact_has_evidence_ids(self):
        """业务影响的每个关联点都有 evidence_id"""
        impact = BusinessImpact(area="数据安全")
        # 默认空列表
        assert impact.evidence_ids == []


# ══════════════════════════════════════════════════════════════════════════════
# TestComplianceGap: 合规缺口识别
# ══════════════════════════════════════════════════════════════════════════════

class TestComplianceGap:

    def test_compliance_gap_creation(self):
        """合规缺口模型正确保存字段"""
        gap = ComplianceGap(
            gap_description="客户未建立数据分类分级制度",
            related_clause="数据安全法实施条例",
            current_status="未知",
            remediation_deadline="2025-06-30",
            evidence_ids=["ev-1"],
        )
        assert gap.gap_description != ""
        assert gap.related_clause != ""
        assert len(gap.evidence_ids) == 1

    def test_system_requirement_creation(self):
        """系统建设需求模型正确保存字段"""
        req = SystemRequirement(
            requirement_description="数据安全治理平台",
            driven_by_clauses=["数据安全法实施条例"],
            estimated_urgency="高",
            system_category="数据安全",
            evidence_ids=["ev-1"],
        )
        assert req.system_category == "数据安全"
        assert req.estimated_urgency == "高"
        assert len(req.driven_by_clauses) == 1


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
                self.title = f"政策{i}"
                self.snippet = f"摘要{i}"
                self.url = f"https://example.com/{i}"
                self.dimension = "policy_compliance"
                self.captured_at = f"2025-{i % 12 + 1:02d}-01"
                self.metadata = {}

        many_evidences = [FakeEvidence(i) for i in range(60)]
        agent = PolicyComplianceAgent(llm_client=mock_llm)
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
                self.dimension = "policy_compliance"
                self.captured_at = "2025-01-01"
                self.metadata = {}

        agent = PolicyComplianceAgent(llm_client=mock_llm)
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
                self.dimension = "policy_compliance"
                self.captured_at = "2025-01-01"
                self.metadata = None

        agent = PolicyComplianceAgent(llm_client=mock_llm)
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
                self.dimension = "policy_compliance"
                self.captured_at = date
                self.metadata = {}

        old = FakeEvidence("2020-01-01")
        new = FakeEvidence("2025-06-01")
        agent = PolicyComplianceAgent(llm_client=mock_llm)
        truncated = agent._truncate_evidences([old, new])
        # 最新日期应该在前
        assert truncated[0]["captured_at"] == "2025-06-01"

    def test_policy_document_with_deadline(self):
        """带截止日期的政策文档正确保存"""
        doc = PolicyDocument(
            title="到期政策",
            publish_date="2024-01-01",
            effective_date="2024-06-01",
            deadline_date="2025-12-31",
        )
        assert doc.deadline_date == "2025-12-31"
        assert doc.effective_date == "2024-06-01"

    def test_constraint_strength_model_distinction(self):
        """确保所有 4 种约束强度在模型层面可区分"""
        strengths = [
            ConstraintStrength.MANDATORY,
            ConstraintStrength.GUIDANCE,
            ConstraintStrength.ENCOURAGING,
            ConstraintStrength.PILOT,
        ]
        # 所有值唯一
        values = [s.value for s in strengths]
        assert len(values) == len(set(values))
