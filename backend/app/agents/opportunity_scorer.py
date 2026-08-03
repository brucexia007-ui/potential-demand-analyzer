"""WBS-22a: 商机评分模型

对单条证据和聚合维度进行量化评分，输出商机等级。
纯函数，无 LLM 依赖。

评分公式:
  EvidenceScore = Strength × Reliability × Freshness × Relevance
  DimensionScore = 70% × max(EvidenceScores) + 30% × mean(EvidenceScores)
  TotalScore = Σ(DimensionScore × weight) - CounterPenalty - LockinRiskPenalty

等级:
  80-100: 高潜 (HIGH)
  60-79:  中潜 (MEDIUM)
  40-59:  低潜 (LOW)
  0-39:   不足 (INSUFFICIENT)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class OpportunityGrade(str, Enum):
    HIGH = "HIGH"             # 高潜: 80-100
    MEDIUM = "MEDIUM"         # 中潜: 60-79
    LOW = "LOW"               # 低潜: 40-59
    INSUFFICIENT = "INSUFFICIENT"  # 不足: 0-39


@dataclass
class EvidenceScoreDetail:
    """单条证据的评分明细"""
    evidence_id: str
    dimension: str
    strength: float          # 证据强度 (0-1)
    reliability: float       # 来源可信度 (0-1)
    freshness: float         # 时效性 (0-1)
    relevance: float         # 相关度 (0-1)
    composite: float = 0.0   # 综合分 = S × R × F × R

    def __post_init__(self):
        if self.composite == 0.0:
            self.composite = (
                self.strength * self.reliability * self.freshness * self.relevance
            )


@dataclass
class DimensionScoreDetail:
    """单个维度的评分明细"""
    dimension: str
    weight: float = 1.0                    # 维度权重
    evidence_scores: list[EvidenceScoreDetail] = field(default_factory=list)
    top_score: float = 0.0                 # 最高单条分
    aggregate_score: float = 0.0           # 聚合分 (均值)
    weighted_score: float = 0.0            # 70/30 加权分

    def __post_init__(self):
        if self.evidence_scores and self.weighted_score == 0.0:
            composites = [e.composite for e in self.evidence_scores]
            if composites:
                self.top_score = max(composites)
                self.aggregate_score = sum(composites) / len(composites)
                self.weighted_score = 0.7 * self.top_score + 0.3 * self.aggregate_score


@dataclass
class CounterEvidence:
    """反证/矛盾证据"""
    claim_text: str
    severity: str           # fatal/major/minor
    penalty: float = 5.0    # 扣分值


@dataclass
class CompetitionLockinRisk:
    """竞争锁定风险"""
    risk_type: str          # 供应商锁定 / 独家参数 / 专利壁垒 / ...
    description: str
    likelihood: str         # high/medium/low
    penalty: float = 0.0    # 扣分值

    def __post_init__(self):
        if self.penalty == 0.0:
            self.penalty = {"high": 10.0, "medium": 6.0, "low": 3.0}.get(
                self.likelihood.lower(), 3.0
            )


@dataclass
class OpportunityScore:
    """商机评分结果"""
    total_score: float = 0.0
    grade: OpportunityGrade = OpportunityGrade.INSUFFICIENT
    dimension_scores: list[DimensionScoreDetail] = field(default_factory=list)
    counter_penalty: float = 0.0
    lockin_penalty: float = 0.0
    evidence_count: int = 0
    dimension_count: int = 0


class OpportunityScorer:
    """商机评分器

    用法:
        scorer = OpportunityScorer()
        result = scorer.score(
            evidences=[...],  # list of dicts with evidence data
            dimension_weights={"bidding_information": 1.0, ...},
            counter_evidences=[...],
            lockin_risks=[...],
        )
    """

    # 默认维度权重
    DEFAULT_WEIGHTS: dict[str, float] = {
        "bidding_information": 1.2,
        "competitor_analysis": 0.9,
        "policy_compliance": 1.0,
        "regulatory_changes": 0.8,
        "service_capability": 0.9,
        "qualification": 0.7,
        "feedback": 0.6,
        "official_pr": 0.6,
        "field_research": 0.7,
        "supplementary": 0.5,
    }

    def score(
        self,
        evidences: list[dict],
        dimension_weights: dict[str, float] | None = None,
        counter_evidences: list[CounterEvidence] | None = None,
        lockin_risks: list[CompetitionLockinRisk] | None = None,
        audit_severity: str = "acceptable",
    ) -> OpportunityScore:
        """对证据集合进行商机评分。

        Args:
            evidences: 证据列表，每条含 {id, dimension, strength?, reliability?,
                       freshness?, relevance?, ...}
            dimension_weights: 维度 → 权重映射，未指定使用默认值
            counter_evidences: 反证列表
            lockin_risks: 竞争锁定风险列表
            audit_severity: 审计严重度，影响信心调整

        Returns:
            OpportunityScore
        """
        weights = dimension_weights or self.DEFAULT_WEIGHTS

        # ── Step 1: 逐条证据评分 ───────────────────────────────────────
        evidence_scores: list[EvidenceScoreDetail] = []
        for ev in evidences:
            ev_id = str(ev.get("id", ""))
            dimension = str(ev.get("dimension", "unknown"))

            # 从 evidence 或 metadata 中提取评分因子
            meta = ev.get("meta_data", {}) or {}
            reliability_str = str(ev.get("source_reliability", "UNKNOWN") or "UNKNOWN")
            reliability = self._reliability_factor(reliability_str)

            # freshness: 基于 published_at 或 captured_at
            freshness = self._freshness_factor(ev)

            # relevance: 默认中等，有 audit 数据时调整
            relevance = float(meta.get("relevance_score", 0.5))

            # strength: 基于 source_type 启发式
            strength = self._strength_factor(ev)

            evidence_scores.append(EvidenceScoreDetail(
                evidence_id=ev_id,
                dimension=dimension,
                strength=strength,
                reliability=reliability,
                freshness=freshness,
                relevance=relevance,
            ))

        # ── Step 2: 按维度聚合 ─────────────────────────────────────────
        dim_evidence_map: dict[str, list[EvidenceScoreDetail]] = {}
        for es in evidence_scores:
            dim_evidence_map.setdefault(es.dimension, []).append(es)

        dimension_scores: list[DimensionScoreDetail] = []
        for dim, es_list in dim_evidence_map.items():
            weight = weights.get(dim, 0.5)
            dim_scores = DimensionScoreDetail(
                dimension=dim,
                weight=weight,
                evidence_scores=es_list,
            )
            dimension_scores.append(dim_scores)

        # ── Step 3: 总分 = Σ(维度分 × 权重) ────────────────────────────
        total_weight = sum(ds.weight for ds in dimension_scores) or 1.0
        total_score = sum(
            ds.weighted_score * ds.weight for ds in dimension_scores
        ) / total_weight * 100  # 转换为百分制

        # ── Step 4: 扣分项 ─────────────────────────────────────────────
        counter_penalty = sum(
            ce.penalty for ce in (counter_evidences or [])
        )
        lockin_penalty = sum(
            lr.penalty for lr in (lockin_risks or [])
        )

        # 审计严重度影响：fatal 额外扣分
        audit_penalty = {"fatal": 10.0, "major": 5.0, "minor": 2.0}.get(
            audit_severity, 0.0
        )

        total_score = max(0.0, total_score - counter_penalty - lockin_penalty - audit_penalty)

        # ── Step 5: 等级判定 ───────────────────────────────────────────
        grade = self._to_grade(total_score)

        return OpportunityScore(
            total_score=round(total_score, 1),
            grade=grade,
            dimension_scores=dimension_scores,
            counter_penalty=counter_penalty,
            lockin_penalty=lockin_penalty,
            evidence_count=len(evidence_scores),
            dimension_count=len(dimension_scores),
        )

    # ── 评分因子 ──────────────────────────────────────────────────────────

    @staticmethod
    def _reliability_factor(reliability: str) -> float:
        """来源可信度 → 0-1 分"""
        return {"S": 1.0, "A": 0.85, "B": 0.65, "C": 0.4, "D": 0.2}.get(
            reliability.upper(), 0.5
        )

    @staticmethod
    def _freshness_factor(ev: dict) -> float:
        """时效性 → 0-1 分"""
        from datetime import datetime, timezone, timedelta

        # 尝试从多个字段读取时间
        date_str = ev.get("published_at") or ev.get("captured_at")
        if isinstance(date_str, str):
            try:
                date_str = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                date_str = None

        if date_str is None:
            return 0.5  # 未知时间默认中等

        now = datetime.now(timezone.utc)
        if date_str.tzinfo is None:
            date_str = date_str.replace(tzinfo=timezone.utc)

        age_days = (now - date_str).days
        if age_days <= 90:       return 1.0   # < 3 个月
        if age_days <= 180:      return 0.9   # 3-6 个月
        if age_days <= 365:      return 0.8   # 6-12 个月
        if age_days <= 730:      return 0.6   # 1-2 年
        if age_days <= 1095:     return 0.4   # 2-3 年
        return 0.2                           # > 3 年

    @staticmethod
    def _strength_factor(ev: dict) -> float:
        """证据强度启发式评分"""
        source_type = str(ev.get("source_type", "")).lower()

        # 官方/政府来源 → 高
        if any(k in source_type for k in ("gov", "official", "policy")):
            return 0.9
        # 招标/公告 → 高
        if any(k in source_type for k in ("bid", "procurement", "tender")):
            return 0.85
        # 网页体验 → 中高（一手观察）
        if "playwright" in source_type or "field" in source_type:
            return 0.7
        # 新闻/媒体 → 中
        if any(k in source_type for k in ("news", "media", "press")):
            return 0.6
        # 搜索源 → 中（取决于具体来源）
        if any(k in source_type for k in ("search", "web", "bocha", "bing")):
            return 0.5
        # 补充搜索 → 中低
        if "supplementary" in source_type:
            return 0.45

        return 0.5  # 默认中等

    @staticmethod
    def _to_grade(score: float) -> OpportunityGrade:
        if score >= 80:
            return OpportunityGrade.HIGH
        if score >= 60:
            return OpportunityGrade.MEDIUM
        if score >= 40:
            return OpportunityGrade.LOW
        return OpportunityGrade.INSUFFICIENT
