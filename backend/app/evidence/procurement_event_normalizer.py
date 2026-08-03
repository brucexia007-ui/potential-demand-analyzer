"""从采购材料中确定性提取项目事件字段，降低模型提取失败成本。"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
import re


_PROJECT_CODE = re.compile(
    r"(?:项目|采购|招标)(?:编号|编码|号)\s*[:：]?\s*([A-Za-z0-9][A-Za-z0-9_.\-/]{3,})",
    re.IGNORECASE,
)
_SUPPLIER = re.compile(
    r"(?:中标|成交)(?:供应商|单位|人)\s*[:：]\s*([^，。；;\n]{2,80})"
)
_AMOUNT = re.compile(
    r"(?:中标|成交|合同)?金额\s*[:：]?\s*(?:人民币)?\s*"
    r"([0-9]+(?:\.[0-9]+)?)\s*(亿元|万元|元)"
)
_DEADLINE = re.compile(
    r"(?:投标|响应|报名|征集)?截止(?:时间|日期)?\s*[:：]?\s*"
    r"(20\d{2})[年\-/.](\d{1,2})[月\-/.](\d{1,2})日?"
)
_CONTRACT_END = re.compile(
    r"(?:合同|服务|维保|维护)(?:服务)?(?:期限|期|有效期)?(?:至|截止于|截止|到期日?为?)\s*[:：]?\s*"
    r"(20\d{2})[年\-/.](\d{1,2})[月\-/.](\d{1,2})日?"
)
_STAGES = (
    ("CANCELLED", ("废标", "流标", "终止公告", "采购终止")),
    ("RENEWAL", ("续约", "续签", "续采")),
    ("MAINTAINING", ("维保", "维护服务", "运维服务")),
    ("ACCEPTED", ("验收公告", "验收结果", "通过验收")),
    ("CONTRACTED", ("合同公告", "合同签订", "采购合同")),
    ("AWARDED", ("中标公告", "中标结果", "成交公告", "成交结果")),
    ("TENDERING", ("招标公告", "采购公告", "征集公告", "竞争性磋商", "询价公告")),
)
_TITLE_NOISE = re.compile(
    r"(中标(?:结果)?公告|成交(?:结果)?公告|采购公告|招标公告|征集公告|合同公告|"
    r"验收公告|续约公告|续签公告|废标公告|流标公告)"
)


def normalize_procurement_fields(
    *,
    title: str,
    content: str,
    published_at: datetime | None,
) -> dict[str, object]:
    combined = f"{title}\n{content}"
    result: dict[str, object] = {}
    code_match = _PROJECT_CODE.search(combined)
    project_code = code_match.group(1).rstrip(".,，。；;") if code_match else ""
    if project_code:
        result["project_code"] = project_code

    stage = next(
        (name for name, terms in _STAGES if any(term in combined for term in terms)),
        "",
    )
    if stage:
        result["event_stage"] = stage

    supplier_match = _SUPPLIER.search(combined)
    if supplier_match:
        result["supplier"] = supplier_match.group(1).strip()

    amount_match = _AMOUNT.search(combined)
    if amount_match:
        multiplier = {
            "元": Decimal("1"),
            "万元": Decimal("10000"),
            "亿元": Decimal("100000000"),
        }[amount_match.group(2)]
        result["amount_yuan"] = int(
            Decimal(amount_match.group(1)) * multiplier
        )

    deadline_match = _DEADLINE.search(combined)
    if deadline_match:
        result["deadline_date"] = _date_text(deadline_match)
    contract_match = _CONTRACT_END.search(combined)
    if contract_match:
        result["contract_end_date"] = _date_text(contract_match)
    if published_at is not None:
        result["event_date"] = published_at.date().isoformat()

    result["project_key"] = (
        f"code:{project_code.casefold()}"
        if project_code
        else f"title:{_normalized_project_title(title)}"
    )
    return result


def _date_text(match: re.Match[str]) -> str:
    return f"{int(match.group(1)):04d}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"


def _normalized_project_title(title: str) -> str:
    without_stage = _TITLE_NOISE.sub("", title)
    normalized = re.sub(r"[\W_]+", "", without_stage.casefold())
    return normalized[:160] or "unknown"
