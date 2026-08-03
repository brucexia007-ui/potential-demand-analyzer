"""价值工程计算器：只执行白名单公式，缺参数时不输出伪精确收益。"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from uuid import UUID

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db.models import Claim, Opportunity, OpportunityValueHypothesis, Task
from app.opportunities.value_schema import ValueHypothesisInput


_KEY = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
_SOURCES = {"CUSTOMER_PROVIDED", "INDUSTRY_BENCHMARK", "USER_ASSUMPTION"}
_OPERATIONS = {"SUM", "DIFFERENCE", "PRODUCT", "RATIO"}


@dataclass(frozen=True)
class ValueHypothesisResult:
    hypothesis: OpportunityValueHypothesis
    created: bool


class OpportunityValueService:
    def __init__(self, db: Session) -> None:
        self._db = db

    def calculate(
        self,
        *,
        workspace_id: UUID,
        opportunity_id: UUID,
        created_by: UUID,
        payload: ValueHypothesisInput,
    ) -> ValueHypothesisResult:
        opportunity = (
            self._db.query(Opportunity)
            .filter(
                Opportunity.id == opportunity_id,
                Opportunity.workspace_id == workspace_id,
            )
            .with_for_update()
            .one_or_none()
        )
        if opportunity is None:
            if self._db.get(Opportunity, opportunity_id) is not None:
                raise PermissionError("正式商机不属于当前 Workspace")
            raise LookupError("正式商机不存在")
        normalized = self._normalize(
            workspace_id=workspace_id,
            account_id=opportunity.target_account_id,
            payload=payload,
        )
        input_hash = self._hash(normalized)
        existing = (
            self._db.query(OpportunityValueHypothesis)
            .filter(
                OpportunityValueHypothesis.opportunity_id == opportunity.id,
                OpportunityValueHypothesis.input_hash == input_hash,
            )
            .one_or_none()
        )
        if existing is not None:
            return ValueHypothesisResult(existing, False)
        version_no = (
            self._db.query(func.max(OpportunityValueHypothesis.version_no))
            .filter(OpportunityValueHypothesis.opportunity_id == opportunity.id)
            .scalar()
            or 0
        ) + 1
        result = OpportunityValueHypothesis(
            workspace_id=workspace_id,
            opportunity_id=opportunity.id,
            version_no=version_no,
            input_hash=input_hash,
            created_by=created_by,
            created_at=datetime.now(timezone.utc),
            **normalized,
        )
        self._db.add(result)
        self._db.flush()
        return ValueHypothesisResult(result, True)

    def list_versions(
        self,
        *,
        workspace_id: UUID,
        opportunity_id: UUID,
    ) -> list[OpportunityValueHypothesis]:
        opportunity = self._db.get(Opportunity, opportunity_id)
        if opportunity is None:
            raise LookupError("正式商机不存在")
        if opportunity.workspace_id != workspace_id:
            raise PermissionError("正式商机不属于当前 Workspace")
        return (
            self._db.query(OpportunityValueHypothesis)
            .filter(
                OpportunityValueHypothesis.workspace_id == workspace_id,
                OpportunityValueHypothesis.opportunity_id == opportunity_id,
            )
            .order_by(OpportunityValueHypothesis.version_no.desc())
            .all()
        )

    def _normalize(self, *, workspace_id: UUID, account_id: UUID, payload: ValueHypothesisInput) -> dict:
        if payload.status not in {"NEEDS_VALIDATION", "CUSTOMER_CONFIRMED", "REJECTED"}:
            raise ValueError("价值假设状态不受支持")
        currency = (payload.currency or "").strip().upper() or None
        if currency is not None and (
            len(currency) != 3 or any(character < "A" or character > "Z" for character in currency)
        ):
            raise ValueError("币种必须为三位大写字母代码")
        if payload.time_horizon_months is not None and payload.time_horizon_months <= 0:
            raise ValueError("价值计算周期必须大于 0")
        if not payload.inputs:
            raise ValueError("价值假设至少需要一个输入参数")
        if not payload.formulas:
            raise ValueError("价值假设至少需要一个受限公式")

        inputs: list[dict] = []
        values: dict[str, Decimal | None] = {}
        missing: list[str] = []
        assumptions: list[dict] = []
        all_customer_confirmed = True
        for item in payload.inputs:
            key = self._key(item.key, "输入参数")
            if key in values:
                raise ValueError(f"输入参数重复：{key}")
            if item.source_type not in _SOURCES:
                raise ValueError(f"输入参数 {key} 的来源不受支持")
            label = self._required(item.label, 255, f"输入参数 {key} 名称")
            unit = self._required(item.unit, 32, f"输入参数 {key} 单位")
            value = self._decimal(item.value, f"输入参数 {key}")
            claim = self._claim(
                workspace_id=workspace_id,
                account_id=account_id,
                claim_id=item.source_claim_id,
                required=item.source_type != "USER_ASSUMPTION",
            )
            if item.source_type == "CUSTOMER_PROVIDED" and (
                claim is None or claim.status != "CUSTOMER_CONFIRMED"
            ):
                raise ValueError(f"客户提供参数 {key} 必须引用 CUSTOMER_CONFIRMED Claim")
            if item.source_type == "INDUSTRY_BENCHMARK" and (
                claim is None or claim.status not in {"SUPPORTED", "CUSTOMER_CONFIRMED"}
            ):
                raise ValueError(f"行业基准参数 {key} 必须引用已支持 Claim")
            if value is None:
                missing.append(key)
            if item.source_type != "CUSTOMER_PROVIDED":
                all_customer_confirmed = False
                assumptions.append({
                    "key": key,
                    "source_type": item.source_type,
                    "value": self._decimal_string(value),
                    "unit": unit,
                    "source_claim_id": str(claim.id) if claim is not None else None,
                })
            values[key] = value
            inputs.append({
                "key": key,
                "label": label,
                "value": self._decimal_string(value),
                "unit": unit,
                "source_type": item.source_type,
                "source_claim_id": str(claim.id) if claim is not None else None,
            })

        formulas: list[dict] = []
        outputs: list[dict] = []
        for item in payload.formulas:
            key = self._key(item.key, "公式")
            if key in values:
                raise ValueError(f"公式 key 与既有输入或公式重复：{key}")
            if item.operation not in _OPERATIONS:
                raise ValueError(f"公式 {key} 的运算不受支持")
            operands = tuple(value.strip() for value in item.operands)
            self._validate_operands(key, item.operation, operands, values)
            value = self._calculate(item.operation, operands, values)
            unit = self._required(item.unit, 32, f"公式 {key} 单位")
            label = self._required(item.label, 255, f"公式 {key} 名称")
            formulas.append({
                "key": key,
                "label": label,
                "operation": item.operation,
                "operands": list(operands),
                "unit": unit,
            })
            outputs.append({
                "key": key,
                "label": label,
                "value": self._decimal_string(value),
                "unit": unit,
                "is_complete": value is not None,
            })
            values[key] = value

        scenarios = []
        for scenario in payload.sensitivity_scenarios:
            name = self._required(scenario.name, 255, "敏感性场景名称")
            overrides: dict[str, Decimal] = {}
            for key, raw_value in scenario.overrides:
                normalized_key = key.strip()
                if normalized_key not in {item["key"] for item in inputs}:
                    raise ValueError(f"敏感性场景只能覆盖输入参数：{normalized_key}")
                if normalized_key in overrides:
                    raise ValueError(f"敏感性场景参数重复：{normalized_key}")
                value = self._decimal(raw_value, f"敏感性参数 {normalized_key}")
                if value is None:
                    raise ValueError("敏感性覆盖值不能为空")
                overrides[normalized_key] = value
            scenario_values = {
                item["key"]: overrides.get(item["key"], self._decimal(item["value"], item["key"]))
                for item in inputs
            }
            scenario_outputs: list[dict] = []
            for formula in formulas:
                result = self._calculate(
                    formula["operation"],
                    tuple(formula["operands"]),
                    scenario_values,
                )
                scenario_values[formula["key"]] = result
                scenario_outputs.append({
                    "key": formula["key"],
                    "value": self._decimal_string(result),
                    "unit": formula["unit"],
                    "is_complete": result is not None,
                })
            scenarios.append({
                "name": name,
                "overrides": {key: self._decimal_string(value) for key, value in sorted(overrides.items())},
                "outputs": scenario_outputs,
            })

        if payload.status == "CUSTOMER_CONFIRMED" and (missing or not all_customer_confirmed):
            raise ValueError("客户确认的价值假设必须参数完整，且全部参数均由客户确认")
        return {
            "status": payload.status,
            "currency": currency,
            "time_horizon_months": payload.time_horizon_months,
            "inputs": inputs,
            "formulas": formulas,
            "outputs": outputs,
            "sensitivity_scenarios": scenarios,
            "assumptions": assumptions,
            "missing_parameters": missing,
        }

    def _claim(
        self,
        *,
        workspace_id: UUID,
        account_id: UUID,
        claim_id: UUID | None,
        required: bool,
    ) -> Claim | None:
        if claim_id is None:
            if required:
                raise ValueError("客户参数或行业基准必须引用 Claim")
            return None
        claim = (
            self._db.query(Claim)
            .join(Task, Task.id == Claim.task_id)
            .filter(
                Claim.id == claim_id,
                Claim.workspace_id == workspace_id,
                Task.workspace_id == workspace_id,
                Task.target_account_id == account_id,
            )
            .one_or_none()
        )
        if claim is None:
            raise ValueError("价值参数只能引用当前目标企业的 Claim")
        return claim

    @staticmethod
    def _validate_operands(
        key: str,
        operation: str,
        operands: tuple[str, ...],
        values: dict[str, Decimal | None],
    ) -> None:
        if operation in {"DIFFERENCE", "RATIO"} and len(operands) != 2:
            raise ValueError(f"公式 {key} 的 {operation} 必须且只能有两个操作数")
        if operation in {"SUM", "PRODUCT"} and len(operands) < 2:
            raise ValueError(f"公式 {key} 至少需要两个操作数")
        unknown = [operand for operand in operands if operand not in values]
        if unknown:
            raise ValueError(f"公式 {key} 引用了尚未定义的参数：{', '.join(unknown)}")

    @staticmethod
    def _calculate(
        operation: str,
        operands: tuple[str, ...],
        values: dict[str, Decimal | None],
    ) -> Decimal | None:
        numbers = [values[operand] for operand in operands]
        if any(value is None for value in numbers):
            return None
        complete = [value for value in numbers if value is not None]
        if operation == "SUM":
            return sum(complete, Decimal("0"))
        if operation == "DIFFERENCE":
            return complete[0] - complete[1]
        if operation == "PRODUCT":
            result = Decimal("1")
            for value in complete:
                result *= value
            return result
        if complete[1] == 0:
            raise ValueError("RATIO 公式的除数不得为 0")
        return complete[0] / complete[1]

    @staticmethod
    def _key(value: str, label: str) -> str:
        key = value.strip().lower()
        if not _KEY.fullmatch(key):
            raise ValueError(f"{label} key 必须为 2 到 64 位小写字母、数字或下划线")
        return key

    @staticmethod
    def _required(value: str, limit: int, label: str) -> str:
        text = value.strip()
        if not text or len(text) > limit:
            raise ValueError(f"{label}必须为 1 到 {limit} 个字符")
        return text

    @staticmethod
    def _decimal(value: Decimal | str | None, label: str) -> Decimal | None:
        if value is None:
            return None
        try:
            result = Decimal(str(value))
        except (InvalidOperation, ValueError) as error:
            raise ValueError(f"{label}数值无效") from error
        if not result.is_finite():
            raise ValueError(f"{label}必须为有限数值")
        return result

    @staticmethod
    def _decimal_string(value: Decimal | None) -> str | None:
        if value is None:
            return None
        normalized = value.normalize()
        return format(normalized, "f")

    @staticmethod
    def _hash(payload: dict) -> bytes:
        return sha256(json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")).digest()
