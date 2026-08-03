"""目标企业主数据的服务输入契约。"""
from __future__ import annotations

from pydantic import BaseModel, Field


class TargetAccountCreateInput(BaseModel):
    """企业名称为唯一必填项，其余字段仅用于主体消歧。"""

    input_name: str = Field(min_length=1, max_length=255)
    official_name: str | None = Field(default=None, max_length=255)
    website: str | None = None
    credit_code: str | None = Field(default=None, max_length=64)
    industry: str | None = Field(default=None, max_length=100)
    region: str | None = Field(default=None, max_length=100)
    stock_code: str | None = Field(default=None, max_length=64)
    parent_id: str | None = None
