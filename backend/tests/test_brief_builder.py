"""WBS-7: ResearchBriefBuilder 单元测试

测试核心能力：
- build_domain_context 纯数据转换
- interpret LLM 解析（mock）
- plan 执行计划建议（mock）
- 边界情况（空输入、None、部分字段）
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from app.advisor.brief_builder import ResearchBriefBuilder


class TestBuildDomainContext:
    """build_domain_context 纯数据转换"""

    def test_full_brief_yields_full_context(self):
        """完整 brief → 完整 domain_context"""
        brief = {
            "industry": "信息技术",
            "region": "华东",
            "business_goal": "了解政府采购意向",
            "depth": "deep",
            "focus_modules": ["招标", "政策"],
            "time_range": "2024-2025",
            "known_clues": [{"description": "某项目已公示", "source": "ccgp.gov.cn"}],
            "user_constraints": {"exclude_domains": ["zhihu.com"]},
            "report_profile": "sales",
            "website": "https://example.com",
            "enable_field_agent": True,
        }
        ctx = ResearchBriefBuilder.build_domain_context(brief)

        assert ctx["industry"] == "信息技术"
        assert ctx["region"] == "华东"
        assert ctx["business_goal"] == "了解政府采购意向"
        assert ctx["depth"] == "deep"
        assert ctx["focus_modules"] == ["招标", "政策"]
        assert ctx["time_range"] == "2024-2025"
        assert len(ctx["known_clues"]) == 1
        assert ctx["user_constraints"]["exclude_domains"] == ["zhihu.com"]
        assert ctx["report_profile"] == "sales"
        assert ctx["website"] == "https://example.com"
        assert ctx["enable_field_agent"] is True

    def test_empty_brief_yields_empty_context(self):
        """空 brief → 仅有默认值的 context"""
        brief = {}
        ctx = ResearchBriefBuilder.build_domain_context(brief)

        assert ctx["industry"] is None
        assert ctx["region"] is None
        assert ctx["depth"] == "standard"
        assert ctx["focus_modules"] == []
        assert ctx["known_clues"] == []
        assert ctx["report_profile"] == "sales"
        assert ctx["enable_field_agent"] is False

    def test_none_brief_yields_empty_dict(self):
        """None brief → 空 dict"""
        ctx = ResearchBriefBuilder.build_domain_context(None)
        assert ctx == {}

    def test_partial_brief_fills_defaults(self):
        """部分字段 brief → 缺失字段用默认值"""
        brief = {"industry": "医疗"}
        ctx = ResearchBriefBuilder.build_domain_context(brief)

        assert ctx["industry"] == "医疗"
        assert ctx["region"] is None
        assert ctx["depth"] == "standard"  # 默认值
        assert ctx["focus_modules"] == []  # 默认值

    def test_depth_none_uses_default(self):
        """depth=None → "standard" """
        brief = {"depth": None}
        ctx = ResearchBriefBuilder.build_domain_context(brief)
        assert ctx["depth"] == "standard"

    def test_focus_modules_none_uses_empty_list(self):
        """focus_modules=None → [] """
        brief = {"focus_modules": None}
        ctx = ResearchBriefBuilder.build_domain_context(brief)
        assert ctx["focus_modules"] == []

    def test_known_clues_truncated_to_context(self):
        """多条已知线索 → 全部进入 domain_context"""
        clues = [
            {"description": "线索1", "source": "url1"},
            {"description": "线索2", "source": "url2"},
            {"description": "线索3", "source": "url3"},
        ]
        brief = {"known_clues": clues}
        ctx = ResearchBriefBuilder.build_domain_context(brief)
        assert len(ctx["known_clues"]) == 3


class TestInterpret:
    """interpret 自然语言解析（mock LLM）"""

    @pytest.fixture
    def mock_llm(self):
        """创建 mock LLM 客户端"""
        return MagicMock()

    @pytest.fixture
    def builder(self, mock_llm):
        """创建带 mock LLM 的 builder"""
        return ResearchBriefBuilder(llm_client=mock_llm)

    def test_interpret_success(self, builder, mock_llm):
        """正常解析 → 返回结构化字段"""
        mock_llm.infer.return_value = {
            "content": json.dumps({
                "company_name": "华为技术有限公司",
                "demand_direction": "云计算采购",
                "industry": "信息技术",
                "region": "华南",
                "business_goal": "了解华为在云计算方面的采购意向",
                "time_range": "2024-2025",
                "suggested_skill": "bidding",
                "confidence": 0.9,
                "missing_fields": ["depth"],
            })
        }

        result = builder.interpret("华为在云计算方面的政府采购需求，重点关注2024-2025年")

        assert result["company_name"] == "华为技术有限公司"
        assert result["demand_direction"] == "云计算采购"
        assert result["industry"] == "信息技术"
        assert result["region"] == "华南"
        assert result["confidence"] == 0.9
        assert result["suggested_skill"] == "bidding"
        assert "depth" in result["missing_fields"]

    def test_interpret_with_hints_passes_to_llm(self, builder, mock_llm):
        """带 hints 时 → LLM prompt 包含 hints"""
        mock_llm.infer.return_value = {
            "content": json.dumps({
                "company_name": "华为",
                "demand_direction": "云计算",
                "industry": None,
                "region": None,
                "business_goal": None,
                "time_range": None,
                "suggested_skill": None,
                "confidence": 0.8,
                "missing_fields": [],
            })
        }

        hints = {"industry": "信息技术"}
        result = builder.interpret("华为云计算", hints=hints)

        # hints 被传入 LLM prompt
        call_args = mock_llm.infer.call_args
        prompt_text = call_args[1]["prompt"]
        assert "已填写的字段" in prompt_text
        assert "信息技术" in prompt_text

    def test_interpret_json_parse_error_returns_empty(self, builder, mock_llm):
        """LLM 返回非法 JSON → 降级结果"""
        mock_llm.infer.return_value = {"content": "not valid json"}

        result = builder.interpret("some input")

        assert result["company_name"] == ""
        assert result["demand_direction"] == ""
        assert result["confidence"] == 0.0
        assert "_error" in result

    def test_interpret_llm_error_returns_empty(self, builder, mock_llm):
        """LLM 调用抛异常 → 降级结果"""
        mock_llm.infer.side_effect = RuntimeError("LLM 不可用")

        result = builder.interpret("some input")

        assert result["company_name"] == ""
        assert result["confidence"] == 0.0
        assert "_error" in result


class TestPlan:
    """plan 执行计划建议（mock LLM）"""

    @pytest.fixture
    def mock_llm(self):
        return MagicMock()

    @pytest.fixture
    def builder(self, mock_llm):
        return ResearchBriefBuilder(llm_client=mock_llm)

    def test_plan_success(self, builder, mock_llm):
        """正常计划 → 返回商业目标与决策问题预览"""
        mock_llm.infer.return_value = {
            "content": json.dumps({
                "analysis_objective": "判断是否值得投入售前资源",
                "decision_questions": ["客户为什么买", "为什么现在买", "如何进入"],
                "suggested_depth": "deep",
                "candidate_focus": ["采购动力", "竞争阻力"],
                "suggested_complexity": "high",
                "reasoning": "该案例涉及政府采购，建议深度分析",
            })
        }

        brief = {
            "company_name": "华为",
            "demand_direction": "云计算",
            "industry": "信息技术",
        }
        result = builder.plan(brief)

        assert result["suggested_depth"] == "deep"
        assert result["suggested_complexity"] == "high"
        assert result["analysis_objective"] == "判断是否值得投入售前资源"
        assert len(result["decision_questions"]) == 3
        assert result["budget_guardrails"]["max_search_queries"] == 28

    def test_plan_json_parse_error_fails_without_template_fallback(self, builder, mock_llm):
        """LLM 返回非法 JSON → 显式失败"""
        mock_llm.infer.return_value = {"content": "not json"}

        with pytest.raises(RuntimeError, match="规划结果不是合法JSON"):
            builder.plan({"company_name": "test"})

    def test_plan_llm_error_fails_without_defaults(self, builder, mock_llm):
        """LLM 调用失败 → 显式失败"""
        mock_llm.infer.side_effect = RuntimeError("LLM down")

        with pytest.raises(RuntimeError, match="LLM规划失败"):
            builder.plan({})

    def test_plan_empty_brief_uses_llm_judgement(self, builder, mock_llm):
        """空 brief → 仍由LLM判断，不使用固定维度"""
        mock_llm.infer.return_value = {
            "content": json.dumps({
                "analysis_objective": "确认研究对象和商业目标",
                "decision_questions": ["研究对象是什么"],
                "suggested_depth": "quick",
                "candidate_focus": [],
                "suggested_complexity": "low",
                "reasoning": "信息不足，建议快速扫描",
            })
        }

        result = builder.plan({})
        assert result["suggested_depth"] == "quick"
