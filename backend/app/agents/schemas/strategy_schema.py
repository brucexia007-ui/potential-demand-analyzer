"""
WBS-14: 全维度策略分析 Schema

定义 StrategyAnalysisAgent 输入输出的 Pydantic 模型。
包含 12 个模型：EvidenceGraph/CrossSignalCorrelation 最小版 +
支持/反证链 + 商机评分 + 破冰三板斧 + 行动建议。
"""

from datetime import datetime, timezone

from pydantic import BaseModel, Field


# ══════════════════════════════════════════════════════════════════════════════
# WBS-14.1: EvidenceGraph 最小版 — 证据信号矩阵
# ══════════════════════════════════════════════════════════════════════════════

class EvidenceSignal(BaseModel):
    """证据信号矩阵中的单维度信号格

    每个分析维度产生一个 EvidenceSignal，标记该维度证据整体
    对商机是正面/负面/中性。
    """
    dimension: str = ""
    signal_type: str = ""           # positive（支持商机）| negative（削弱商机）| neutral（中性信息）
    evidence_count: int = 0         # 该维度证据条数
    key_findings: list[str] = Field(default_factory=list)   # 关键发现（≤3条）
    strength: str = ""              # strong | moderate | weak
    evidence_ids: list[str] = Field(default_factory=list)   # 支撑该信号的证据 ID


# ══════════════════════════════════════════════════════════════════════════════
# WBS-14.2: CrossSignalCorrelation 最小版 — 跨维度信号关联
# ══════════════════════════════════════════════════════════════════════════════

class CrossSignalCorrelation(BaseModel):
    """跨维度信号关联

    描述两个或多个维度的信号之间的关系：
    - reinforces: 相互加强（如招标需求 + 政策鼓励）
    - contradicts: 相互矛盾（如招标活跃但政策收紧）
    - independent: 各自独立
    """
    dimensions: list[str] = Field(default_factory=list)  # 关联的维度名
    relation: str = ""              # reinforces | contradicts | independent
    description: str = ""           # 关联描述
    implication: str = ""           # 对商机判断的含义


class EvidenceSignalMatrix(BaseModel):
    """跨维度证据信号矩阵

    汇总所有维度的信号 + 跨维度关联关系。
    """
    dimensions: list[EvidenceSignal] = Field(default_factory=list)
    cross_correlations: list[CrossSignalCorrelation] = Field(default_factory=list)


# ══════════════════════════════════════════════════════════════════════════════
# WBS-14.3: 支持证据链
# ══════════════════════════════════════════════════════════════════════════════

class SupportChain(BaseModel):
    """支持商机的证据链

    一条论证链：thesis（为什么是商机）← 证据ID链。
    """
    chain_id: str = ""              # 如 "sc-1"
    thesis: str = ""                # 论点（为什么这是商机）
    evidence_ids: list[str] = Field(default_factory=list)  # 支撑证据 ID 链
    strength: str = ""              # strong | moderate | weak


# ══════════════════════════════════════════════════════════════════════════════
# WBS-14.4: 反证链
# ══════════════════════════════════════════════════════════════════════════════

class CounterChain(BaseModel):
    """削弱商机的反证链

    一条反证：thesis（为什么可能不是商机/存在风险）← 证据ID链。
    附带缓解建议。
    """
    chain_id: str = ""              # 如 "cc-1"
    thesis: str = ""                # 论点（为什么可能不是商机）
    evidence_ids: list[str] = Field(default_factory=list)  # 支撑证据 ID 链
    severity: str = ""              # high | medium | low（对商机的削弱程度）
    mitigation: str = ""            # 缓解建议


# ══════════════════════════════════════════════════════════════════════════════
# 竞争锁定风险
# ══════════════════════════════════════════════════════════════════════════════

class CompetitiveRisk(BaseModel):
    """竞争锁定风险

    识别可能阻碍切入的竞争因素。
    """
    risk_type: str = ""             # 供应商锁定 | 技术锁定 | 关系锁定 | 资质壁垒 | 价格战
    description: str = ""           # 风险描述
    likelihood: str = ""            # high | medium | low
    evidence_ids: list[str] = Field(default_factory=list)


# ══════════════════════════════════════════════════════════════════════════════
# WBS-14.7: 推荐切入场景
# ══════════════════════════════════════════════════════════════════════════════

class EntryScenario(BaseModel):
    """推荐切入场景

    基于证据推荐的业务切入路径。
    """
    scenario_name: str = ""         # 场景名称
    description: str = ""           # 场景描述
    why_recommended: str = ""       # 推荐原因
    prerequisites: list[str] = Field(default_factory=list)  # 前置条件
    evidence_ids: list[str] = Field(default_factory=list)


# ══════════════════════════════════════════════════════════════════════════════
# WBS-14.6: 破冰三板斧
# ══════════════════════════════════════════════════════════════════════════════

class IcebreakerStrategy(BaseModel):
    """单条破冰策略

    精确 3 条组成"三板斧"，每条含目标角色 + 开场白。
    """
    rank: int = 0                   # 1 / 2 / 3
    strategy_name: str = ""         # 策略名称（≤20字）
    approach: str = ""              # 具体做法（≤150字）
    target_persona: str = ""        # 目标角色（如 CIO / 采购处长 / 技术总监）
    hook: str = ""                  # 破冰钩子（一句话开场白）
    evidence_ids: list[str] = Field(default_factory=list)


# ══════════════════════════════════════════════════════════════════════════════
# 下一步行动建议
# ══════════════════════════════════════════════════════════════════════════════

class NextAction(BaseModel):
    """下一步行动

    含优先级、时间线和负责人建议。
    """
    priority: int = 0               # 1=最高, 2=次要, 3=后续
    action: str = ""                # 行动描述
    timeline: str = ""              # 建议时间（如"本周内"）
    expected_outcome: str = ""      # 预期产出
    owner: str = ""                 # 建议负责角色


# ══════════════════════════════════════════════════════════════════════════════
# WBS-14.5: 顶层输出 — 全维度策略分析结果
# ══════════════════════════════════════════════════════════════════════════════

class StrategyAnalysisOutput(BaseModel):
    """StrategyAnalysisAgent 完整输出 — 9 项策略洞察

    输出项:
    1. one_line_verdict — 一句话商机判断
    2. opportunity_score + confidence — 商机评分与置信度
    3. signal_matrix — 关键证据信号矩阵 (WBS-14.1 + WBS-14.2)
    4. supporting_chains — 支持商机的证据链 (WBS-14.3)
    5. counter_chains — 削弱商机的反证链 (WBS-14.4)
    6. competitive_risks — 竞争锁定风险
    7. recommended_scenarios — 推荐切入场景
    8. icebreaker_strategies — 破冰三板斧 (WBS-14.6)
    9. action_plan — 下一步行动计划 (WBS-14.7)
    """
    company_name: str = ""
    demand_direction: str = ""
    analyzed_dimensions: list[str] = Field(default_factory=list)

    # 1. 一句话商机判断
    one_line_verdict: str = ""

    # 2. 商机评分与置信度 (WBS-14.5)
    opportunity_score: float = Field(default=0.0, ge=0.0, le=100.0)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)

    # 3. 关键证据信号矩阵 (WBS-14.1 + WBS-14.2)
    signal_matrix: EvidenceSignalMatrix = Field(default_factory=EvidenceSignalMatrix)

    # 4. 支持商机的证据链 (WBS-14.3)
    supporting_chains: list[SupportChain] = Field(default_factory=list)

    # 5. 削弱商机的反证链 (WBS-14.4)
    counter_chains: list[CounterChain] = Field(default_factory=list)

    # 6. 竞争锁定风险
    competitive_risks: list[CompetitiveRisk] = Field(default_factory=list)

    # 7. 推荐切入场景
    recommended_scenarios: list[EntryScenario] = Field(default_factory=list)

    # 8. 破冰三板斧 (WBS-14.6) — 精确 3 条
    icebreaker_strategies: list[IcebreakerStrategy] = Field(default_factory=list)

    # 9. 下一步行动计划 (WBS-14.7)
    action_plan: list[NextAction] = Field(default_factory=list)

    # 元信息
    analysis_notes: str = ""        # 分析局限性说明 / 数据不足原因
    generated_at: str = ""          # ISO 时间戳

    @classmethod
    def empty(cls, company_name: str = "", demand_direction: str = "",
              dimensions: list[str] | None = None) -> "StrategyAnalysisOutput":
        """创建空分析结果（LLM 不可用或证据不足时使用）。"""
        return cls(
            company_name=company_name,
            demand_direction=demand_direction,
            analyzed_dimensions=dimensions or [],
            one_line_verdict="证据不足，无法做出确定商机判断。",
            opportunity_score=0.0,
            confidence=0.0,
            signal_matrix=EvidenceSignalMatrix(),
            supporting_chains=[],
            counter_chains=[],
            competitive_risks=[],
            recommended_scenarios=[],
            icebreaker_strategies=[],
            action_plan=[],
            analysis_notes="LLM 调用失败或无可用证据，生成空分析结果。",
            generated_at=datetime.now(timezone.utc).isoformat(),
        )

    @classmethod
    def error(cls, company_name: str = "", demand_direction: str = "",
              dimensions: list[str] | None = None, error_msg: str = "") -> "StrategyAnalysisOutput":
        """创建错误分析结果（LLM 调用异常时使用）。"""
        result = cls.empty(company_name, demand_direction, dimensions)
        result.analysis_notes = f"策略分析失败: {error_msg}"
        return result
