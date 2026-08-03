"""长耗时任务执行基线指标。

本模块同时输出两类数据：

1. 结构化日志事件：保留 task_id / dimension，供单任务回溯与 POC 导出。
2. Prometheus 聚合指标：只使用低基数标签，避免把 task_id 写入标签造成时序爆炸。

本阶段不保存 Prompt、网页正文或用户私有上下文。外部调用的持久化账本由后续
TEO-09-02 负责；这里仅提供当前 Harness 链路的可观测基线。
"""

from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator, Mapping, Optional

from prometheus_client import Counter, Histogram

logger = logging.getLogger(__name__)


_funnel_items_total = Counter(
    "task_execution_funnel_items_total",
    "任务执行漏斗中处理的候选或证据数量",
    ["outcome"],
)
_stage_duration_seconds = Histogram(
    "task_execution_stage_duration_seconds",
    "任务执行阶段耗时（秒）",
    ["stage", "status"],
)
_token_usage_total = Counter(
    "task_execution_token_usage_total",
    "任务执行消耗的 Token 数量",
    ["stage"],
)
_model_calls_total = Counter(
    "task_execution_model_calls_total",
    "任务执行期间实际发出的模型调用数",
    ["status"],
)
_model_call_latency_seconds = Histogram(
    "task_execution_model_call_latency_seconds",
    "任务执行期间模型调用延迟（秒）",
    ["status"],
)


@dataclass(frozen=True)
class TaskExecutionMetricEvent:
    """可写入结构化日志的单条基线事件。"""

    name: str
    task_id: str
    dimension: str
    stage: str
    value: float
    status: str = "unknown"
    fields: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """转换为安全的日志载荷，不包含 Prompt 或页面正文。"""
        return {
            "name": self.name,
            "task_id": self.task_id,
            "dimension": self.dimension,
            "stage": self.stage,
            "value": self.value,
            "status": self.status,
            "fields": dict(self.fields),
        }


@dataclass(frozen=True)
class _ModelCallContext:
    task_id: str
    dimension: str
    stage: str


_model_call_context: ContextVar[Optional[_ModelCallContext]] = ContextVar(
    "task_execution_model_call_context",
    default=None,
)


class TaskExecutionMetrics:
    """当前 Harness 链路的指标记录器。

    ``event_sink`` 仅用于测试或接入日志管道；默认写入标准 logging。Prometheus
    指标不包含 task_id，单任务排障应使用同名结构化日志事件。
    """

    def __init__(
        self,
        event_sink: Optional[Callable[[TaskExecutionMetricEvent], None]] = None,
        *,
        enable_prometheus: bool = True,
    ) -> None:
        self._event_sink = event_sink
        self._enable_prometheus = enable_prometheus

    def _emit(self, event: TaskExecutionMetricEvent) -> None:
        try:
            if self._event_sink:
                self._event_sink(event)
            else:
                logger.info(
                    "task_execution_metric=%s",
                    json.dumps(event.to_dict(), ensure_ascii=False, sort_keys=True),
                )
        except Exception:
            # 可观测性不得影响任务主链路。
            logger.exception("任务执行指标写入失败")

    def record_funnel(
        self,
        *,
        task_id: str,
        dimension: str,
        candidate_found: Optional[int] = None,
        candidate_fetched: Optional[int] = None,
        evidence_produced: Optional[int] = None,
        evidence_persisted: Optional[int] = None,
    ) -> None:
        """记录当前链路可获得的候选/证据漏斗数量。

        当前旧链路没有候选筛选阶段，因此不虚构 ``candidate_selected``。该字段将在
        TEO-02 候选筛选服务接入后补充。
        """
        outcomes = {
            "candidate_found": candidate_found,
            "candidate_fetched": candidate_fetched,
            "evidence_produced": evidence_produced,
            "evidence_persisted": evidence_persisted,
        }
        for outcome, count in outcomes.items():
            if count is None:
                continue
            safe_count = max(0, int(count))
            self._emit(
                TaskExecutionMetricEvent(
                    name="task_execution_funnel",
                    task_id=task_id,
                    dimension=dimension,
                    stage="candidate_funnel",
                    value=safe_count,
                    fields={"outcome": outcome},
                )
            )
            if self._enable_prometheus:
                _funnel_items_total.labels(outcome=outcome).inc(safe_count)

    def record_candidate_screening_shadow(
        self,
        *,
        task_id: str,
        dimension: str,
        status: str,
        candidate_input_count: int,
        candidate_selected_count: Optional[int] = None,
        schema_success: Optional[bool] = None,
        output_token_warning: Optional[bool] = None,
        error_code: Optional[str] = None,
    ) -> None:
        """记录影子筛选的安全摘要，不保存 Prompt、候选正文或模型原始输出。"""
        fields: dict[str, Any] = {
            "candidate_input_count": max(0, int(candidate_input_count)),
            "candidate_selected_count": (
                max(0, int(candidate_selected_count))
                if candidate_selected_count is not None else None
            ),
            "schema_success": schema_success,
            "output_token_warning": output_token_warning,
            "error_code": error_code,
        }
        self._emit(TaskExecutionMetricEvent(
            name="task_execution_candidate_screening_shadow",
            task_id=task_id,
            dimension=dimension,
            stage="candidate_screening_shadow",
            value=fields["candidate_input_count"],
            status=status,
            fields=fields,
        ))

    def record_stage_duration(
        self,
        *,
        task_id: str,
        dimension: str,
        stage: str,
        duration_seconds: float,
        status: str,
    ) -> None:
        """记录阶段耗时，负数和无效值被收敛为 0。"""
        safe_duration = max(0.0, float(duration_seconds))
        self._emit(
            TaskExecutionMetricEvent(
                name="task_execution_stage_duration",
                task_id=task_id,
                dimension=dimension,
                stage=stage,
                value=safe_duration,
                status=status,
            )
        )
        if self._enable_prometheus:
            _stage_duration_seconds.labels(stage=stage, status=status).observe(safe_duration)

    def record_token_usage(
        self,
        *,
        task_id: str,
        dimension: str,
        token_breakdown: Mapping[str, Any],
    ) -> None:
        """记录 Token 分阶段统计，只接受数值型计数。"""
        for stage, raw_tokens in token_breakdown.items():
            try:
                tokens = max(0, int(raw_tokens))
            except (TypeError, ValueError):
                continue
            self._emit(
                TaskExecutionMetricEvent(
                    name="task_execution_token_usage",
                    task_id=task_id,
                    dimension=dimension,
                    stage=str(stage),
                    value=tokens,
                )
            )
            if self._enable_prometheus:
                _token_usage_total.labels(stage=str(stage)).inc(tokens)

    def record_model_call(
        self,
        *,
        task_id: str,
        dimension: str,
        stage: str,
        model: str,
        provider: str,
        latency_ms: float,
        usage: Mapping[str, Any],
        success: bool,
        error_code: Optional[str],
    ) -> None:
        """记录实际到达 Gateway 客户端的单次 Provider 调用。"""
        status = "success" if success else "failed"
        safe_latency_seconds = max(0.0, float(latency_ms)) / 1000
        safe_usage = {
            key: max(0, int(value))
            for key, value in usage.items()
            if key in {"input_tokens", "output_tokens", "total_tokens"}
            and isinstance(value, (int, float))
        }
        self._emit(
            TaskExecutionMetricEvent(
                name="task_execution_model_call",
                task_id=task_id,
                dimension=dimension,
                stage=stage,
                value=1,
                status=status,
                fields={
                    "model": model,
                    "provider": provider,
                    "latency_ms": round(safe_latency_seconds * 1000, 3),
                    "usage": safe_usage,
                    "error_code": error_code,
                },
            )
        )
        if self._enable_prometheus:
            _model_calls_total.labels(status=status).inc()
            _model_call_latency_seconds.labels(status=status).observe(safe_latency_seconds)

    def bind_gateway_client(self, client: Any) -> None:
        """给现有 Gateway 实例附加轻量观察器。

        观察器只包装实例的 ``_log_call``，不会读取 Prompt，也不会改变返回值、重试或
        Provider 选择。ContextVar 使并发 Worker 的 task_id/dimension 彼此隔离。
        """
        if getattr(client, "_task_execution_metrics_observer", False):
            return

        original_log_call = client._log_call

        def observed_log_call(
            model: str,
            provider_name: str,
            latency_ms: float,
            usage: dict,
            error_code: Optional[str],
            success: bool,
        ) -> None:
            original_log_call(
                model,
                provider_name,
                latency_ms,
                usage,
                error_code,
                success,
            )
            context = _model_call_context.get()
            if context is not None:
                self.record_model_call(
                    task_id=context.task_id,
                    dimension=context.dimension,
                    stage=context.stage,
                    model=model,
                    provider=provider_name,
                    latency_ms=latency_ms,
                    usage=usage,
                    success=success,
                    error_code=error_code,
                )

        try:
            client._log_call = observed_log_call
            client._task_execution_metrics_observer = True
        except Exception:
            logger.exception("无法安装 Gateway 指标观察器")

    @contextmanager
    def model_call_context(
        self,
        *,
        task_id: str,
        dimension: str,
        stage: str,
    ) -> Iterator[None]:
        """绑定当前模型调用所属任务，不泄露给其他并发执行上下文。"""
        token = _model_call_context.set(
            _ModelCallContext(task_id=task_id, dimension=dimension, stage=stage)
        )
        try:
            yield
        finally:
            _model_call_context.reset(token)


task_execution_metrics = TaskExecutionMetrics()
