"""配置中心 API 路由 —— CRUD + 连接测试"""
from fastapi import APIRouter, Depends, HTTPException, Body
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from typing import Any

from app.db.session import get_db
from app.db.models import User, LLMProvider, ModelRoute, Setting
from app.config_center.readiness import verification_status
from app.api.auth import get_current_user
from app.config_center.status import get_config_status, mark_setup_completed
from app.config_center import provider_config as provider_svc
from app.config_center import search_config as search_svc
from app.config_center.connection_test import test_llm_connection, test_search_connection
from app.config_center import budget_config as budget_svc
from app.config_center import crawler_config as crawler_svc
from app.config_center import retention_config as retention_svc
from app.config_center import security_config as security_svc

router = APIRouter(tags=["config"])


# ═══════════════════════════════════════════════════════════════════════
# Pydantic 请求体
# ═══════════════════════════════════════════════════════════════════════

class CreateProviderRequest(BaseModel):
    name: str = Field(..., min_length=1, description="Provider 名称")
    provider_type: str = Field(default="openai_compatible", description="类型标识")
    base_url: str | None = None
    api_key: str | None = None
    models: list[str] | None = None
    default_model: str | None = None
    fallback_models: list[str] | None = None
    enabled: bool = True
    priority: int = 100
    timeout_seconds: int | None = None
    retry_count: int = 2


class UpdateProviderRequest(BaseModel):
    name: str | None = None
    provider_type: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    models: list[str] | None = None
    default_model: str | None = None
    fallback_models: list[str] | None = None
    enabled: bool | None = None
    priority: int | None = None
    timeout_seconds: int | None = None
    retry_count: int | None = None


class CreateSearchProviderRequest(BaseModel):
    name: str = Field(..., min_length=1)
    provider_type: str = Field(..., description="bocha / bing / tavily / duckduckgo / custom")
    api_key: str | None = None
    base_url: str | None = None
    appcode: str | None = Field(default=None, description="阿里云 APPCODE（bocha 类型可选）")
    app_key: str | None = Field(default=None, description="阿里云 AppKey（bocha 阿里云 API 签名鉴权可选）")
    app_secret: str | None = Field(default=None, description="阿里云 AppSecret（bocha 阿里云 API 签名鉴权可选）")
    enabled: bool = True
    priority: int = 100
    daily_limit: int | None = None
    per_task_limit: int | None = None
    timeout_seconds: int = 30


class UpdateSearchProviderRequest(BaseModel):
    name: str | None = None
    provider_type: str | None = None
    api_key: str | None = None
    base_url: str | None = None
    appcode: str | None = Field(default=None, description="阿里云 APPCODE（bocha 类型可选）")
    app_key: str | None = Field(default=None, description="阿里云 AppKey（bocha 阿里云 API 签名鉴权可选）")
    app_secret: str | None = Field(default=None, description="阿里云 AppSecret（bocha 阿里云 API 签名鉴权可选）")
    enabled: bool | None = None
    priority: int | None = None
    daily_limit: int | None = None
    per_task_limit: int | None = None
    timeout_seconds: int | None = None


class ModelRouteItem(BaseModel):
    model_config = {"protected_namespaces": ()}

    agent_role: str = Field(..., min_length=1)
    complexity_level: str = Field(..., min_length=1)
    model_name: str = Field(..., min_length=1)


class SetupCompleteRequest(BaseModel):
    mode: str = Field(..., pattern="^(READY|BROWSE_ONLY)$")


# ═══════════════════════════════════════════════════════════════════════
# 配置状态
# ═══════════════════════════════════════════════════════════════════════

@router.get("/config/status")
def config_status(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取系统引导状态与真实执行就绪状态。"""
    return get_config_status(db)


# ═══════════════════════════════════════════════════════════════════════
# LLM Provider CRUD + Test
# ═══════════════════════════════════════════════════════════════════════

@router.get("/config/providers")
def list_providers(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """列出所有 LLM Provider（脱敏）"""
    return provider_svc.list_providers(db)


@router.post("/config/providers", status_code=201)
def create_provider(
    body: CreateProviderRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """创建 LLM Provider，API Key 自动加密保存"""
    try:
        return provider_svc.create_provider(db, **body.model_dump())
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.put("/config/providers/{provider_id}")
def update_provider(
    provider_id: int,
    body: UpdateProviderRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """更新 LLM Provider。api_key 不传或为空时保留旧值"""
    try:
        return provider_svc.update_provider(
            db, provider_id, **body.model_dump(exclude_none=True)
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.delete("/config/providers/{provider_id}")
def delete_provider(
    provider_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """删除 LLM Provider"""
    ok = provider_svc.delete_provider(db, provider_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Provider 不存在")
    return {"ok": True}


@router.post("/config/providers/{provider_id}/test")
def test_provider(
    provider_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """测试 LLM Provider 连接 —— 用真实 API Key 发起一次 models.list() 请求"""
    return test_llm_connection(db, provider_id)


# ═══════════════════════════════════════════════════════════════════════
# Search Provider CRUD + Test
# ═══════════════════════════════════════════════════════════════════════

@router.get("/config/search")
def list_search_providers(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """列出所有搜索 Provider（脱敏）"""
    return search_svc.list_search_providers(db)


@router.post("/config/search", status_code=201)
def create_search_provider(
    body: CreateSearchProviderRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """创建搜索 Provider，API Key 自动加密保存"""
    try:
        return search_svc.create_search_provider(db, **body.model_dump())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.put("/config/search/{provider_id}")
def update_search_provider(
    provider_id: int,
    body: UpdateSearchProviderRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """更新搜索 Provider"""
    try:
        return search_svc.update_search_provider(
            db, provider_id, **body.model_dump(exclude_none=True)
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.delete("/config/search/{provider_id}")
def delete_search_provider(
    provider_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """删除搜索 Provider"""
    ok = search_svc.delete_search_provider(db, provider_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Search Provider 不存在")
    return {"ok": True}


@router.post("/config/search/{provider_id}/test")
def test_search_provider(
    provider_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """测试搜索 Provider 连接 —— 用真实 API Key 发起一次搜索请求"""
    return test_search_connection(db, provider_id)


# ═══════════════════════════════════════════════════════════════════════
# Model Routes CRUD
# ═══════════════════════════════════════════════════════════════════════

@router.get("/config/model-routes")
def list_model_routes(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """列出所有模型路由规则"""
    routes = (
        db.query(ModelRoute)
        .order_by(ModelRoute.agent_role, ModelRoute.complexity_level)
        .all()
    )
    return [
        {
            "id": r.id,
            "agent_role": r.agent_role,
            "complexity_level": r.complexity_level,
            "model_name": r.model_name,
            "fallback_model_name": r.fallback_model_name,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "updated_at": r.updated_at.isoformat() if r.updated_at else None,
        }
        for r in routes
    ]


@router.put("/config/model-routes")
def update_model_routes(
    body: list[ModelRouteItem],
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """全量替换模型路由规则。

    先删除所有现有规则，再按 body 批量插入。
    传入空列表表示清空所有路由。
    """
    db.query(ModelRoute).delete()
    for item in body:
        db.add(ModelRoute(
            agent_role=item.agent_role,
            complexity_level=item.complexity_level,
            model_name=item.model_name,
        ))
    db.commit()
    return {"ok": True, "count": len(body)}


# ═══════════════════════════════════════════════════════════════════════
# Provider 健康状态
# ═══════════════════════════════════════════════════════════════════════

@router.get("/config/health")
def get_providers_health(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """获取所有 LLM 和搜索 Provider 的健康状态。"""
    from app.config_center.provider_health import ProviderHealthService

    svc = ProviderHealthService()
    return {
        "llm": svc.get_all_health(db, "llm"),
        "search": svc.get_all_health(db, "search"),
    }


# ═══════════════════════════════════════════════════════════════════════
# v3.1: 预算与限流配置
# ═══════════════════════════════════════════════════════════════════════

@router.get("/config/budget")
def get_budget(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """获取预算与限流配置"""
    return budget_svc.get_budget_config(db)


@router.put("/config/budget")
def update_budget(
    body: dict[str, Any] = Body(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """更新预算与限流配置（部分更新）"""
    return budget_svc.update_budget_config(db, body)


# ═══════════════════════════════════════════════════════════════════════
# v3.1: 抓取与外部 Agent 配置
# ═══════════════════════════════════════════════════════════════════════

@router.get("/config/crawler")
def get_crawler(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """获取抓取与外部 Agent 配置"""
    return crawler_svc.get_crawler_config(db)


@router.put("/config/crawler")
def update_crawler(
    body: dict[str, Any] = Body(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """更新抓取与外部 Agent 配置（部分更新）"""
    return crawler_svc.update_crawler_config(db, body)


# ═══════════════════════════════════════════════════════════════════════
# v3.1: 数据保留策略配置
# ═══════════════════════════════════════════════════════════════════════

@router.get("/config/data-retention")
def get_data_retention(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """获取数据保留策略配置"""
    return retention_svc.get_retention_config(db)


@router.put("/config/data-retention")
def update_data_retention(
    body: dict[str, Any] = Body(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """更新数据保留策略配置（部分更新）"""
    return retention_svc.update_retention_config(db, body)


# ═══════════════════════════════════════════════════════════════════════
# v3.1: 安全配置
# ═══════════════════════════════════════════════════════════════════════

@router.get("/config/security")
def get_security(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """获取安全配置（SSRF 黑白名单等）"""
    return security_svc.get_security_config(db)


@router.put("/config/security")
def update_security(
    body: dict[str, Any] = Body(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """更新安全配置（部分更新）"""
    return security_svc.update_security_config(db, body)


# ═══════════════════════════════════════════════════════════════════════
# v3.1: 配置导出/导入
# ═══════════════════════════════════════════════════════════════════════

@router.get("/config/export")
def export_config(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """导出完整配置（不含密钥）"""
    return {
        "budget": budget_svc.get_budget_config(db),
        "crawler": crawler_svc.get_crawler_config(db),
        "data_retention": retention_svc.get_retention_config(db),
        "security": security_svc.get_security_config(db),
        "exported_at": None,  # 由前端或调用方自行记录时间
    }


@router.post("/config/import")
def import_config(
    body: dict[str, Any] = Body(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """导入配置（仅导入非敏感配置，不含密钥）"""
    imported: list[str] = []
    if "budget" in body and isinstance(body["budget"], dict):
        budget_svc.update_budget_config(db, body["budget"])
        imported.append("budget")
    if "crawler" in body and isinstance(body["crawler"], dict):
        crawler_svc.update_crawler_config(db, body["crawler"])
        imported.append("crawler")
    if "data_retention" in body and isinstance(body["data_retention"], dict):
        retention_svc.update_retention_config(db, body["data_retention"])
        imported.append("data_retention")
    if "security" in body and isinstance(body["security"], dict):
        security_svc.update_security_config(db, body["security"])
        imported.append("security")
    return {"ok": True, "imported": imported}


# ═══════════════════════════════════════════════════════════════════════
# v3.1: 全量连接测试
# ═══════════════════════════════════════════════════════════════════════

@router.post("/config/test-all")
def test_all_connections(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """对所有已启用的 LLM 和搜索 Provider 执行连接测试，返回聚合结果"""
    from app.db.models import LLMProvider as LLMP, SearchProvider as SearchP

    results: dict[str, list[dict[str, Any]]] = {"llm": [], "search": []}

    llm_providers = db.query(LLMP).filter(LLMP.enabled == True).all()
    for p in llm_providers:
        try:
            r = test_llm_connection(db, p.id)
            results["llm"].append({"id": p.id, "name": p.name, "success": r.get("success", False), **r})
        except Exception as e:
            results["llm"].append({"id": p.id, "name": p.name, "success": False, "error": str(e)})

    search_providers = db.query(SearchP).filter(SearchP.enabled == True).all()
    for p in search_providers:
        try:
            r = test_search_connection(db, p.id)
            results["search"].append({"id": p.id, "name": p.name, "success": r.get("success", False), **r})
        except Exception as e:
            results["search"].append({"id": p.id, "name": p.name, "success": False, "error": str(e)})

    all_ok = all(r.get("success", False) for r in results["llm"] + results["search"])
    return {"all_passed": all_ok, "results": results}


# ═══════════════════════════════════════════════════════════════════════
# v3.1: Setup Wizard 完成标记
# ═══════════════════════════════════════════════════════════════════════

@router.post("/config/setup-complete")
def mark_setup_complete(
    body: SetupCompleteRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """完成设置向导；READY 模式必须已经具备执行能力。"""
    status = get_config_status(db)
    if body.mode == "READY" and not status["execution_ready"]:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "SYSTEM_NOT_READY",
                "message": "系统执行能力尚未就绪",
                "blocking_items": status["blocking_items"],
            },
        )
    mark_setup_completed(db, body.mode)
    return {"ok": True, "mode": body.mode}


# ═══════════════════════════════════════════════════════════════════════
# v3.1: 模型路由预设
# ═══════════════════════════════════════════════════════════════════════

MODEL_ROUTES_PRESET_CATEGORY = "system"
MODEL_ROUTES_PRESET_KEY = "model_routes_preset"


def _create_default_routes_for_verified_provider(db: Session) -> tuple[int, str | None]:
    """首次引导将预设转换为可执行的默认模型路由。

    预设本身只表达成本/质量偏好；真实执行仍必须有可解析的
    ``agent_role × complexity_level → model`` 记录。首次配置时使用
    用户已验证 Provider 的默认模型建立三档默认路由，避免在没有
    明确模型能力元数据时擅自替换用户选择的模型。
    """
    existing_count = db.query(ModelRoute).count()
    if existing_count:
        return existing_count, None

    providers = (
        db.query(LLMProvider)
        .filter(LLMProvider.enabled.is_(True))
        .order_by(LLMProvider.priority.asc(), LLMProvider.id.asc())
        .all()
    )
    provider = next(
        (
            item
            for item in providers
            if verification_status(item) == "PASSED"
            and item.models_json
            and (item.default_model or item.models_json[0])
        ),
        None,
    )
    if provider is None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "NO_VERIFIED_LLM_PROVIDER",
                "message": "请先完成至少一个 LLM Provider 的连接测试",
            },
        )

    model_name = provider.default_model or provider.models_json[0]
    for complexity_level in ("low", "medium", "high"):
        db.add(ModelRoute(
            agent_role="default",
            complexity_level=complexity_level,
            provider_id=provider.id,
            model_name=model_name,
        ))
    return 3, model_name


@router.get("/config/model-routes-preset")
def get_model_routes_preset(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """获取模型路由预设"""
    entry = db.query(Setting).filter(
        Setting.key == MODEL_ROUTES_PRESET_KEY,
        Setting.category == MODEL_ROUTES_PRESET_CATEGORY,
    ).first()
    if entry and entry.value_json:
        return entry.value_json
    return {"preset": "balanced"}  # 默认值


@router.put("/config/model-routes-preset")
def update_model_routes_preset(
    body: dict[str, Any] = Body(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """保存模型路由预设（cheap / balanced / quality）"""
    preset = body.get("preset", "balanced")
    if preset not in ("cheap", "balanced", "quality"):
        raise HTTPException(status_code=400, detail="preset 必须为 cheap / balanced / quality")
    route_count, selected_model = _create_default_routes_for_verified_provider(db)

    entry = db.query(Setting).filter(
        Setting.key == MODEL_ROUTES_PRESET_KEY,
        Setting.category == MODEL_ROUTES_PRESET_CATEGORY,
    ).first()
    if entry:
        entry.value_json = {"preset": preset}
    else:
        entry = Setting(
            key=MODEL_ROUTES_PRESET_KEY,
            category=MODEL_ROUTES_PRESET_CATEGORY,
            value_json={"preset": preset},
        )
        db.add(entry)
    db.commit()
    return {
        "ok": True,
        "preset": preset,
        "route_count": route_count,
        "selected_model": selected_model,
    }
