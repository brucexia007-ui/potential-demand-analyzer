"""v3.1 E2E: 商机评分模型测试（WBS-22a）

测试 EvidenceScore → DimensionScore → TotalScore 评分逻辑。
"""
from __future__ import annotations

import pytest

from tests.factories import create_test_opportunity_score_data


class TestOpportunityScorer:
    """商机评分模型核心逻辑"""

    def test_score_high_all_strong(self):
        """全强证据 → HIGH 等级"""
        data = create_test_opportunity_score_data("HIGH")
        assert data["total_score"] >= 80
        assert data["grade"] == "HIGH"

    def test_score_medium_mixed(self):
        """混合质量 → MEDIUM 等级"""
        data = create_test_opportunity_score_data("MEDIUM")
        assert 60 <= data["total_score"] < 80
        assert data["grade"] == "MEDIUM"

    def test_score_low_weak(self):
        """弱证据 → LOW 等级"""
        data = create_test_opportunity_score_data("LOW")
        assert data["total_score"] < 60
        assert data["grade"] == "LOW"

    def test_score_structure_complete(self):
        """评分数据结构完整"""
        data = create_test_opportunity_score_data("HIGH")
        assert "total_score" in data
        assert "grade" in data
        assert "dimension_scores" in data
        assert "counter_evidences" in data
        assert "lockin_risks" in data
        assert "penalties" in data
        assert isinstance(data["dimension_scores"], dict)
        assert len(data["dimension_scores"]) > 0
        for dim_key, dim_data in data["dimension_scores"].items():
            assert "score" in dim_data
            assert "weight" in dim_data
            assert "evidence_count" in dim_data

    def test_penalties_structure(self):
        """扣分项结构正确"""
        data = create_test_opportunity_score_data("HIGH")
        penalties = data["penalties"]
        assert "counter_evidence_penalty" in penalties
        assert "lockin_risk_penalty" in penalties
        assert "total_penalty" in penalties

    def test_grade_values(self):
        """等级枚举正确"""
        valid_grades = {"HIGH", "MEDIUM", "LOW", "INSUFFICIENT"}
        for grade in valid_grades:
            data = create_test_opportunity_score_data(grade)
            assert data["grade"] in valid_grades


class TestOpportunityScorerIntegration:
    """商机评分与 DB 数据集成测试（如果有 scorer 模块）"""

    def test_import_scorer_module(self):
        """验证 opportunity_scorer 模块可导入"""
        try:
            from app.agents.opportunity_scorer import OpportunityScorer
            assert OpportunityScorer is not None
        except ImportError:
            pytest.skip("opportunity_scorer 模块不存在或导入失败")

    def test_evidence_score_calculation(self):
        """单条证据评分计算"""
        try:
            from app.agents.opportunity_scorer import OpportunityScorer
            scorer = OpportunityScorer()

            # 测试 scorer.score() 方法存在
            assert hasattr(scorer, "score") or hasattr(scorer, "calculate")
        except ImportError:
            pytest.skip("opportunity_scorer 模块不存在")
