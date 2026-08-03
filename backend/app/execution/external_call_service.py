"""TEO-09-02：外部调用账本与幂等执行包装。"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable
from uuid import UUID

from sqlalchemy.orm import Session

from app.execution.asset_repository import ExecutionAssetRepository
from app.execution.budget_service import BudgetService


@dataclass(frozen=True)
class ExternalCallResult:
    attempt_id: UUID
    response: dict[str, Any] | None
    reused: bool


class ExternalCallService:
    """先提交 STARTED，再执行外部调用；同一幂等键永不重复发起请求。"""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._assets = ExecutionAssetRepository(session)

    def invoke(
        self,
        *,
        task_id: UUID,
        run_id: UUID | None,
        stage_run_id: UUID | None,
        provider: str,
        model: str | None,
        operation: str,
        idempotency_key: str,
        request_metadata: dict[str, Any],
        execute: Callable[[], dict[str, Any]],
        dimension: str | None = None,
        estimated_amount: Decimal | float | int = Decimal("0"),
        estimated_tokens: int | None = None,
        actual_amount: Callable[[dict[str, Any]], Decimal | float | int] | None = None,
        task_limit: Decimal | float | int | None = None,
        currency: str = "USD",
    ) -> ExternalCallResult:
        if not idempotency_key or len(idempotency_key) > 128:
            raise ValueError("idempotency_key 必须为 1～128 个字符")
        if not provider or not operation:
            raise ValueError("provider 和 operation 不能为空")
        request_hash = hashlib.sha256(
            json.dumps(request_metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).digest()
        attempt, created = self._assets.get_or_create_call(
            idempotency_key=idempotency_key,
            task_id=task_id,
            run_id=run_id,
            stage_run_id=stage_run_id,
            provider=provider,
            model=model,
            operation=operation,
            request_hash=request_hash,
            status="STARTED",
            billing_outcome="PENDING",
        )
        if not created:
            return ExternalCallResult(attempt_id=attempt.id, response=None, reused=True)

        BudgetService(self._session).reserve(
            task_id=task_id,
            run_id=run_id,
            stage_run_id=stage_run_id,
            external_call_attempt_id=attempt.id,
            dimension=dimension,
            call_key=idempotency_key,
            estimated_amount=estimated_amount,
            estimated_tokens=estimated_tokens,
            currency=currency,
            task_limit=task_limit,
        )
        self._session.commit()  # 崩溃窗口保留为 STARTED，恢复器可收敛为 UNKNOWN。
        started = time.perf_counter()
        try:
            response = execute()
        except TimeoutError as error:
            self._finish_failure(attempt, "TIMED_OUT", error, billing_outcome="UNKNOWN")
            raise
        except Exception as error:
            self._finish_failure(attempt, "FAILED", error, billing_outcome="UNKNOWN")
            raise

        usage = response.get("usage") if isinstance(response, dict) else None
        attempt.status = "SUCCEEDED"
        attempt.billing_outcome = "SETTLED" if isinstance(usage, dict) else "NOT_BILLABLE"
        attempt.response_hash = hashlib.sha256(
            json.dumps(response, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":")).encode("utf-8")
        ).digest()
        attempt.input_tokens = int(usage.get("input_tokens", 0)) if isinstance(usage, dict) else None
        attempt.output_tokens = int(usage.get("output_tokens", 0)) if isinstance(usage, dict) else None
        attempt.latency_ms = int((time.perf_counter() - started) * 1000)
        attempt.finished_at = datetime.now(timezone.utc)
        actual_token_count = int(usage.get("total_tokens", 0)) if isinstance(usage, dict) else None
        settled_amount = actual_amount(response) if actual_amount is not None else Decimal("0")
        BudgetService(self._session).settle(
            task_id=task_id,
            run_id=run_id,
            stage_run_id=stage_run_id,
            external_call_attempt_id=attempt.id,
            dimension=dimension,
            call_key=idempotency_key,
            reserved_amount=estimated_amount,
            actual_amount=settled_amount,
            actual_tokens=actual_token_count,
            currency=currency,
            task_limit=task_limit,
        )
        self._session.commit()
        return ExternalCallResult(attempt_id=attempt.id, response=response, reused=False)

    def _finish_failure(self, attempt, status: str, error: Exception, *, billing_outcome: str) -> None:
        attempt.status = status
        attempt.billing_outcome = billing_outcome
        attempt.error_class = type(error).__name__
        attempt.error_message = str(error)[:2000]
        attempt.finished_at = datetime.now(timezone.utc)
        self._session.commit()
