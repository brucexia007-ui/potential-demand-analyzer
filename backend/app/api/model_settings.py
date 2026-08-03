"""模型配置 API 路由"""
import os
import json
import re
from pathlib import Path
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field
from typing import Optional
import logging

from app.api.auth import get_current_user
from app.db.models import User

logger = logging.getLogger(__name__)

router = APIRouter(tags=["models"])

SETTINGS_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "model_settings.json"


def _collect_provider_models() -> list[str]:
    """从环境变量 LLM_PROVIDER_*_MODELS 收集所有可用模型"""
    models: list[str] = []
    seen: set[str] = set()
    pattern = re.compile(r'^LLM_PROVIDER_(.+)_MODELS$')
    for key in sorted(os.environ.keys()):
        m = pattern.match(key)
        if not m:
            continue
        models_str = os.environ[key]
        for model in models_str.split(','):
            model = model.strip()
            if model and model not in seen:
                seen.add(model)
                models.append(model)

    if models:
        return models

    # 回退到硬编码列表
    return [
        "qwen3.5-plus",
        "qwen-max",
        "qwen-turbo",
        "deepseek-v3",
        "deepseek-r1",
        "claude-sonnet-4-6",
        "claude-opus-4-7",
    ]


class RoutingTierConfig(BaseModel):
    """单组复杂度 → 模型映射"""
    low: str = ""
    medium: str = ""
    high: str = ""


class AgentRoutingOverride(BaseModel):
    """Agent 专属路由覆盖，所有字段可选"""
    low: Optional[str] = None
    medium: Optional[str] = None
    high: Optional[str] = None


class RoutingConfig(BaseModel):
    """动态算力路由配置"""
    default: RoutingTierConfig = Field(default_factory=RoutingTierConfig)
    planner: Optional[AgentRoutingOverride] = None
    extractor: Optional[AgentRoutingOverride] = None
    reflector: Optional[AgentRoutingOverride] = None
    synthesizer: Optional[AgentRoutingOverride] = None


class ModelConfig(BaseModel):
    default_model: str = Field(default="")
    temperature: float = Field(default=0.2, ge=0.0, le=1.0)
    timeout_seconds: int = Field(default=60, ge=5, le=600)
    max_retries: int = Field(default=2, ge=0, le=10)
    fallback_providers: list[str] = Field(default_factory=list)
    fallback_models: list[str] = Field(default_factory=list)
    routing: Optional[RoutingConfig] = None


class AvailableModelsResponse(BaseModel):
    models: list[str]
    default: str


def _load_settings() -> ModelConfig:
    if SETTINGS_FILE.exists():
        try:
            data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            return ModelConfig(**data)
        except Exception:
            pass
    return ModelConfig()


def get_default_model_settings() -> dict:
    """返回带 env var 覆盖的默认配置字典，供 GatewayClient 使用"""
    config = _load_settings()
    result = {
        "default_model": os.getenv("DEFAULT_MODEL", config.default_model),
        "temperature": config.temperature,
        "timeout_seconds": config.timeout_seconds,
        "max_retries": config.max_retries,
        "fallback_providers": config.fallback_providers,
        "fallback_models": config.fallback_models,
    }
    if config.routing:
        result["routing"] = config.routing.model_dump()
    return result


def _save_settings(config: ModelConfig) -> None:
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(
        config.model_dump_json(indent=2), encoding="utf-8"
    )
    # 同步路由配置到 DB model_routes 表
    _sync_routes_to_db(config)


def _sync_routes_to_db(config: ModelConfig) -> None:
    """将路由配置同步到 DB model_routes 表，使 ModelRouter 优先使用 DB 配置"""
    if not config.routing:
        return
    try:
        from app.db.session import SessionLocal
        from app.db.models import ModelRoute

        db = SessionLocal()
        try:
            routing_dict = config.routing.model_dump(exclude_none=True)
            # 先清理旧路由，再写入新路由
            db.query(ModelRoute).delete()
            count = 0
            for agent_role, tiers in routing_dict.items():
                if tiers is None:
                    continue
                for complexity_level, model_name in tiers.items():
                    if not model_name:
                        continue
                    db.add(ModelRoute(
                        agent_role=agent_role,
                        complexity_level=complexity_level,
                        model_name=model_name,
                    ))
                    count += 1
            db.commit()
            logger.info(f"[ModelSettings] 已同步 {count} 条路由到 DB model_routes 表")
        except Exception as e:
            db.rollback()
            logger.warning(f"[ModelSettings] DB 路由同步失败（非致命）: {e}")
        finally:
            db.close()
    except Exception as e:
        logger.warning(f"[ModelSettings] DB 路由同步失败（非致命）: {e}")


@router.get("/models")
async def get_model_config(current_user: User = Depends(get_current_user)) -> ModelConfig:
    """获取当前模型配置"""
    try:
        return _load_settings()
    except Exception as e:
        logger.error(f"加载模型配置失败: {e}")
        return ModelConfig()


@router.put("/models")
async def update_model_config(config: ModelConfig, current_user: User = Depends(get_current_user)) -> ModelConfig:
    """更新模型配置"""
    try:
        _save_settings(config)
        return config
    except Exception as e:
        logger.error(f"保存模型配置失败: {e}")
        raise HTTPException(status_code=500, detail=f"保存失败: {str(e)}")


@router.get("/models/available")
async def get_available_models(current_user: User = Depends(get_current_user)) -> AvailableModelsResponse:
    """获取可用模型列表（从环境变量 LLM_PROVIDER_*_MODELS 动态收集）"""
    default_model = os.getenv("DEFAULT_MODEL", "qwen3.5-plus")
    models = _collect_provider_models()
    return AvailableModelsResponse(models=models, default=default_model)
