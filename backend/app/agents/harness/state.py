"""
Harness 执行状态定义

定义 ExecutionState 和 EvaluationResult 等运行时状态数据结构
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional
from enum import Enum

from .spec import DimensionStatus, TaskStatus
from app.agents.schemas.candidate_schema import CandidateSet


@dataclass
class Evidence:
    """
    证据对象

    属性:
        id: 证据 ID
        dimension: 维度名称
        title: 标题
        snippet: 摘要/片段
        url: 来源 URL
        source_type: 来源类型
        metadata: 元数据
        published_at: 发布时间（可选）
        captured_at: 抓取时间
    """
    dimension: str
    title: str
    snippet: str
    url: str
    source_type: str = "web_scrape"
    metadata: dict = field(default_factory=dict)
    id: Optional[str] = None
    published_at: Optional[datetime] = None
    captured_at: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> dict:
        """转换为字典（用于序列化）"""
        return {
            "id": self.id,
            "dimension": self.dimension,
            "title": self.title,
            "snippet": self.snippet,
            "url": self.url,
            "source_type": self.source_type,
            "metadata": self.metadata,
            "published_at": self.published_at.isoformat() if self.published_at else None,
            "captured_at": self.captured_at.isoformat()
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Evidence":
        """从字典创建"""
        published_at = data.get("published_at")
        if isinstance(published_at, str):
            published_at = datetime.fromisoformat(published_at)

        captured_at = data.get("captured_at")
        if isinstance(captured_at, str):
            captured_at = datetime.fromisoformat(captured_at)

        return cls(
            id=data.get("id"),
            dimension=data.get("dimension", ""),
            title=data.get("title", ""),
            snippet=data.get("snippet", ""),
            url=data.get("url", ""),
            source_type=data.get("source_type", "web_scrape"),
            metadata=data.get("metadata", {}),
            published_at=published_at,
            captured_at=captured_at or datetime.now()
        )


@dataclass
class EvaluationResult:
    """
    评估结果：每个环节的质量评估

    属性:
        stage: 评估阶段 ("planning" | "research" | "extraction")
        passed: 是否通过
        score: 评分 (0-1)
        feedback: 具体反馈
        suggestions: 改进建议
    """
    stage: str
    passed: bool
    score: float
    feedback: str = ""
    suggestions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """转换为字典"""
        return {
            "stage": self.stage,
            "passed": self.passed,
            "score": self.score,
            "feedback": self.feedback,
            "suggestions": self.suggestions
        }

    @classmethod
    def from_dict(cls, data: dict) -> "EvaluationResult":
        """从字典创建"""
        return cls(
            stage=data.get("stage", ""),
            passed=data.get("passed", False),
            score=data.get("score", 0.0),
            feedback=data.get("feedback", ""),
            suggestions=data.get("suggestions", [])
        )


@dataclass
class SearchResult:
    """
    搜索结果

    属性:
        title: 标题
        url: 链接
        snippet: 摘要
        source: 来源
        date: 发布日期（可选）
        raw_content: 抓取到的完整内容（可选）
        is_relevant: 是否相关（评估后设置）
        relevance_reason: 相关性原因（评估后设置）
    """
    title: str
    url: str
    snippet: str
    source: str = ""
    date: Optional[datetime] = None
    raw_content: Optional[str] = None
    is_relevant: bool = True
    relevance_reason: str = ""

    def to_dict(self) -> dict:
        """转换为字典"""
        return {
            "title": self.title,
            "url": self.url,
            "snippet": self.snippet,
            "source": self.source,
            "date": self.date.isoformat() if self.date else None,
            "raw_content": self.raw_content,
            "is_relevant": self.is_relevant,
            "relevance_reason": self.relevance_reason
        }

    @classmethod
    def from_dict(cls, data: dict) -> "SearchResult":
        """从字典创建"""
        date = data.get("date")
        if isinstance(date, str):
            date = datetime.fromisoformat(date)

        return cls(
            title=data.get("title", ""),
            url=data.get("url", ""),
            snippet=data.get("snippet", ""),
            source=data.get("source", ""),
            date=date,
            raw_content=data.get("raw_content"),
            is_relevant=data.get("is_relevant", True),
            relevance_reason=data.get("relevance_reason", "")
        )


@dataclass
class ExecutionState:
    """
    执行状态：Harness 追踪的运行时状态

    属性:
        dimension: 当前维度名称
        status: 当前状态
        iteration: 当前迭代次数
        search_queries_generated: 已生成的搜索词列表
        search_results: 搜索结果列表
        pages_evaluated: 已评估的网页数量
        evidences_collected: 收集到的证据列表
        evaluation_results: 评估结果列表
        reflections: 反思记录列表
        current_quality_score: 当前质量评分
        token_usage: Token 使用统计
        error_message: 错误消息（如果有）
        started_at: 开始时间
        updated_at: 更新时间
    """
    dimension: str
    status: DimensionStatus = DimensionStatus.PENDING

    # 迭代追踪
    iteration: int = 0
    search_queries_generated: list[str] = field(default_factory=list)

    # 搜索结果
    search_results: list[SearchResult] = field(default_factory=list)
    candidate_set: Optional[CandidateSet] = None
    pages_evaluated: int = 0

    # 证据收集
    evidences_collected: list[Evidence] = field(default_factory=list)

    # 评估与反思
    evaluation_results: list[EvaluationResult] = field(default_factory=list)
    reflections: list[str] = field(default_factory=list)

    # 质量评分
    current_quality_score: float = 0.0

    # Token 使用统计 (planning, research, extraction, evaluation, reflection)
    token_usage: dict[str, int] = field(default_factory=lambda: {
        "planning": 0,
        "research": 0,
        "extraction": 0,
        "evaluation": 0,
        "reflection": 0
    })

    # 错误追踪
    error_message: Optional[str] = None

    # 时间追踪
    started_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)

    def __post_init__(self):
        """初始化时设置时间"""
        if not self.started_at:
            self.started_at = datetime.now()
        self.updated_at = datetime.now()

    def update_timestamp(self):
        """更新时间戳"""
        self.updated_at = datetime.now()

    def add_search_query(self, query: str):
        """添加搜索词"""
        if query not in self.search_queries_generated:
            self.search_queries_generated.append(query)
            self.update_timestamp()

    def add_search_result(self, result: SearchResult):
        """添加搜索结果"""
        self.search_results.append(result)
        self.update_timestamp()

    def set_candidate_set(self, candidate_set: CandidateSet):
        """记录当前维度的 CandidateSet；生产链路将在 TEO-01-04 接入。"""
        if candidate_set.dimension != self.dimension:
            raise ValueError("CandidateSet dimension 必须与 ExecutionState 一致")
        self.candidate_set = candidate_set
        self.update_timestamp()

    def add_evidence(self, evidence: Evidence):
        """添加证据"""
        self.evidences_collected.append(evidence)
        self.update_timestamp()

    def add_evaluation(self, result: EvaluationResult):
        """添加评估结果"""
        self.evaluation_results.append(result)
        self.update_timestamp()

    def add_reflection(self, reflection: str):
        """添加反思记录"""
        self.reflections.append(reflection)
        self.update_timestamp()

    def record_token_usage(self, stage: str, tokens: int):
        """记录 Token 使用"""
        if stage in self.token_usage:
            self.token_usage[stage] += tokens
        self.update_timestamp()

    @property
    def total_tokens_used(self) -> int:
        """计算总 Token 使用量"""
        return sum(self.token_usage.values())

    @property
    def last_evaluation(self) -> Optional[EvaluationResult]:
        """获取最后一次评估结果"""
        if self.evaluation_results:
            return self.evaluation_results[-1]
        return None

    @property
    def last_reflection(self) -> Optional[str]:
        """获取最后一次反思记录"""
        if self.reflections:
            return self.reflections[-1]
        return None

    def to_dict(self) -> dict:
        """转换为字典（用于序列化）"""
        return {
            "dimension": self.dimension,
            "status": self.status.value,
            "iteration": self.iteration,
            "search_queries_generated": self.search_queries_generated,
            "search_results": [r.to_dict() for r in self.search_results],
            "candidate_set": self.candidate_set.to_dict() if self.candidate_set else None,
            "pages_evaluated": self.pages_evaluated,
            "evidences_collected": [e.to_dict() for e in self.evidences_collected],
            "evaluation_results": [e.to_dict() for e in self.evaluation_results],
            "reflections": self.reflections,
            "current_quality_score": self.current_quality_score,
            "token_usage": self.token_usage,
            "error_message": self.error_message,
            "started_at": self.started_at.isoformat(),
            "updated_at": self.updated_at.isoformat()
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ExecutionState":
        """从字典创建"""
        started_at = data.get("started_at")
        if isinstance(started_at, str):
            started_at = datetime.fromisoformat(started_at)

        updated_at = data.get("updated_at")
        if isinstance(updated_at, str):
            updated_at = datetime.fromisoformat(updated_at)

        status = data.get("status", "pending")
        if isinstance(status, str):
            status = DimensionStatus(status)

        search_results = [
            SearchResult.from_dict(r) for r in data.get("search_results", [])
        ]
        raw_candidate_set = data.get("candidate_set")
        if raw_candidate_set is not None and not isinstance(raw_candidate_set, dict):
            raise ValueError("candidate_set 必须为对象或 null")
        candidate_set = CandidateSet.from_dict(raw_candidate_set) if raw_candidate_set else None
        evidences = [
            Evidence.from_dict(e) for e in data.get("evidences_collected", [])
        ]
        evaluations = [
            EvaluationResult.from_dict(ev) for ev in data.get("evaluation_results", [])
        ]

        return cls(
            dimension=data.get("dimension", ""),
            status=status,
            iteration=data.get("iteration", 0),
            search_queries_generated=data.get("search_queries_generated", []),
            search_results=search_results,
            candidate_set=candidate_set,
            pages_evaluated=data.get("pages_evaluated", 0),
            evidences_collected=evidences,
            evaluation_results=evaluations,
            reflections=data.get("reflections", []),
            current_quality_score=data.get("current_quality_score", 0.0),
            token_usage=data.get("token_usage", {
                "planning": 0,
                "research": 0,
                "extraction": 0,
                "evaluation": 0,
                "reflection": 0
            }),
            error_message=data.get("error_message"),
            started_at=started_at,
            updated_at=updated_at
        )


@dataclass
class DimensionResult:
    """
    维度执行结果

    属性:
        dimension: 维度名称
        status: 最终状态
        evidences: 收集到的证据
        evaluation_history: 评估历史记录
        reflections: 反思记录
        final_quality_score: 最终质量评分
        total_iterations: 总迭代次数
        total_tokens_used: 总 Token 消耗
        error_message: 错误消息
    """
    dimension: str
    status: DimensionStatus
    evidences: list[Evidence] = field(default_factory=list)
    evaluation_history: list[EvaluationResult] = field(default_factory=list)
    reflections: list[str] = field(default_factory=list)
    final_quality_score: float = 0.0
    total_iterations: int = 0
    total_tokens_used: int = 0
    error_message: Optional[str] = None

    def to_dict(self) -> dict:
        """转换为字典"""
        return {
            "dimension": self.dimension,
            "status": self.status.value,
            "evidences": [e.to_dict() for e in self.evidences],
            "evaluation_history": [e.to_dict() for e in self.evaluation_history],
            "reflections": self.reflections,
            "final_quality_score": self.final_quality_score,
            "total_iterations": self.total_iterations,
            "total_tokens_used": self.total_tokens_used,
            "error_message": self.error_message
        }

    @classmethod
    def from_state(cls, state: ExecutionState, force_finish: bool = False) -> "DimensionResult":
        """从 ExecutionState 创建 DimensionResult"""
        return cls(
            dimension=state.dimension,
            status=state.status,
            evidences=state.evidences_collected,
            evaluation_history=state.evaluation_results,
            reflections=state.reflections,
            final_quality_score=state.current_quality_score,
            total_iterations=state.iteration,
            total_tokens_used=state.total_tokens_used,
            error_message=state.error_message
        )
