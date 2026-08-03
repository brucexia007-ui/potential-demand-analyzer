"""运行时配置加载器 —— 从 DB 读取含解密 API Key 的配置

供 GatewayClient / SearchClient / ModelRouter 在运行时调用。
与 WBS-1 服务层（provider_config.py / search_config.py）不同：
  - 服务层返回脱敏数据（masked_api_key），面向 Web API
  - 本模块返回解密后的明文 API Key，仅供内部运行时使用

核心约定：
  - 所有 load_*_from_db 函数在 DB 无数据时返回 None
  - 调用方检查 None → 回退到环境变量 / model_settings.json
  - DB 连接/操作失败（OperationalError/InterfaceError）→ log ERROR，返回 None（基础设施故障允许 env 兜底）
  - 数据解析/解密异常 → raise ConfigCorruptionError（配置损坏不应静默绕过 DB 配置）
"""
import logging
from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy.exc import OperationalError, InterfaceError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.db.models import LLMProvider, SearchProvider, ModelRoute
from app.config_center.encryption import decrypt_secret
from app.config_center import readiness

logger = logging.getLogger(__name__)


class ConfigCorruptionError(Exception):
    """配置损坏异常 —— 表示 DB 配置存在但无法正常解析/解密。

    调用方不应 catch 此异常并 fallback 到 env，而应向上传播让任务失败。
    """
    pass


# ── 运行时配置 dataclass ──────────────────────────────────────────────

@dataclass
class RuntimeLLMProvider:
    """运行时 LLM Provider 配置（含解密后的 API Key，仅供内部使用）"""
    name: str
    base_url: str
    api_key: str                          # 明文，已解密
    models: list[str] = field(default_factory=list)
    default_model: Optional[str] = None
    fallback_models: list[str] = field(default_factory=list)
    priority: int = 100
    timeout_seconds: int = 60
    retry_count: int = 2
    db_id: int | None = None              # DB 主键（env fallback 时为 None）


@dataclass
class RuntimeSearchProvider:
    """运行时搜索 Provider 配置（含解密后的 API Key，仅供内部使用）"""
    name: str
    provider_type: str                    # bocha / bing / tavily / duckduckgo / custom
    api_key: str                          # 明文，已解密
    base_url: Optional[str] = None
    appcode: str = ""                     # 阿里云 APPCODE（明文，已解密）
    app_key: str = ""                     # 阿里云 AppKey（明文，已解密）
    app_secret: str = ""                  # 阿里云 AppSecret（明文，已解密）
    priority: int = 100
    timeout_seconds: int = 30
    db_id: int | None = None              # DB 主键（env fallback 时为 None）


# ── 内部工具 ──────────────────────────────────────────────────────────

def _json_to_list(value) -> list[str]:
    """读取唯一的 Provider 模型数组契约；配置损坏时显式失败。"""
    if value is None:
        return []
    if isinstance(value, list):
        if any(not isinstance(item, str) or not item.strip() for item in value):
            raise ConfigCorruptionError("Provider 模型数组成员必须为非空字符串")
        return list(dict.fromkeys(item.strip() for item in value))
    raise ConfigCorruptionError("Provider models_json 必须为 JSON 数组")


# ── 运行时加载函数 ─────────────────────────────────────────────────────

def load_llm_providers_from_db(db: Session) -> Optional[list[RuntimeLLMProvider]]:
    """从 DB 加载启用的 LLM Provider，含解密后的 API Key。

    Returns:
        list[RuntimeLLMProvider] — 有启用记录且至少一个含有效 models
        [] — 有启用记录但所有 models 均为空（配置不完整）
        None — DB 无启用记录
    """
    try:
        providers = (
            db.query(LLMProvider)
            .filter(LLMProvider.enabled == True)
            .order_by(LLMProvider.priority.desc(), LLMProvider.id.asc())
            .all()
        )

        if not providers:
            return None

        verification_rank = {
            "PASSED": 0,
            "UNTESTED": 1,
            "STALE": 2,
            "FAILED": 3,
        }
        providers.sort(
            key=lambda provider: (
                verification_rank[readiness.verification_status(provider)],
                -provider.priority,
                provider.id,
            )
        )

        result: list[RuntimeLLMProvider] = []
        for p in providers:
            models = _json_to_list(p.models_json)
            if not models:
                logger.debug(
                    f"[RuntimeConfig] 跳过 LLM Provider '{p.name}'：models 为空"
                )
                continue

            api_key = decrypt_secret(p.api_key_encrypted) or ""

            result.append(RuntimeLLMProvider(
                name=p.name,
                base_url=p.base_url or "",
                api_key=api_key,
                models=models,
                default_model=p.default_model,
                fallback_models=_json_to_list(p.fallback_models_json),
                priority=p.priority,
                timeout_seconds=p.timeout_seconds,
                retry_count=p.retry_count,
                db_id=p.id,
            ))

        if not result:
            # 有启用的 provider 但所有 models 都为空 → 返回空列表
            # （与 None 区分：None = 无启用记录，[] = 有记录但配置不完整）
            return []

        logger.debug(f"[RuntimeConfig] 从 DB 加载 {len(result)} 个 LLM Provider")
        return result

    except SQLAlchemyError as e:
        # DB 连接/操作异常：基础设施故障，允许 env fallback 维持服务
        logger.error(f"[RuntimeConfig] DB 异常，fallback 到 env: {e}")
        return None
    except ConfigCorruptionError:
        raise
    except Exception as e:
        # 数据解析/解密异常：配置损坏，不应静默绕过 DB 配置
        logger.error(f"[RuntimeConfig] DB LLM Provider 配置损坏，拒绝 fallback: {e}")
        raise ConfigCorruptionError(
            f"LLM Provider 配置损坏: {e}"
        ) from e


def load_search_providers_from_db(db: Session) -> Optional[list[RuntimeSearchProvider]]:
    """从 DB 加载启用的搜索 Provider，含解密后的 API Key。

    Returns:
        list[RuntimeSearchProvider] 或 None（DB 无启用记录时）
    """
    try:
        providers = (
            db.query(SearchProvider)
            .filter(SearchProvider.enabled == True)
            .order_by(SearchProvider.priority.desc())
            .all()
        )

        if not providers:
            return None

        result: list[RuntimeSearchProvider] = []
        for p in providers:
            api_key = decrypt_secret(p.api_key_encrypted) or ""
            appcode = decrypt_secret(p.appcode_encrypted) or ""
            app_key = decrypt_secret(p.app_key_encrypted) or ""
            app_secret = decrypt_secret(p.app_secret_encrypted) or ""

            result.append(RuntimeSearchProvider(
                name=p.name,
                provider_type=p.provider_type,
                api_key=api_key,
                base_url=p.base_url,
                appcode=appcode,
                app_key=app_key,
                app_secret=app_secret,
                priority=p.priority,
                timeout_seconds=p.timeout_seconds,
                db_id=p.id,
            ))

        logger.debug(f"[RuntimeConfig] 从 DB 加载 {len(result)} 个 Search Provider")
        return result

    except (OperationalError, InterfaceError) as e:
        # DB 连接/操作异常：基础设施故障，允许 env fallback 维持服务
        logger.error(f"[RuntimeConfig] DB 连接异常，fallback 到 env: {e}")
        return None
    except ConfigCorruptionError:
        raise
    except Exception as e:
        # 数据解析/解密异常：配置损坏，不应静默绕过 DB 配置
        logger.error(f"[RuntimeConfig] DB Search Provider 配置损坏，拒绝 fallback: {e}")
        raise ConfigCorruptionError(
            f"Search Provider 配置损坏: {e}"
        ) from e


def load_model_routes_from_db(db: Session) -> Optional[dict]:
    """从 DB 加载模型路由配置。

    Returns:
        dict 格式: {agent_role: {complexity_level: model_name}}
        或 None（DB 无路由记录时）
    """
    try:
        routes = db.query(ModelRoute).all()

        if not routes:
            return None

        config: dict = {}
        for r in routes:
            if r.agent_role not in config:
                config[r.agent_role] = {}
            config[r.agent_role][r.complexity_level] = r.model_name

        logger.debug(
            f"[RuntimeConfig] 从 DB 加载 {len(routes)} 条模型路由"
        )
        return config

    except (OperationalError, InterfaceError) as e:
        # DB 连接/操作异常：基础设施故障，允许 env fallback 维持服务
        logger.error(f"[RuntimeConfig] DB 连接异常: {e}")
        return None
    except ConfigCorruptionError:
        raise
    except Exception as e:
        # 数据异常：配置损坏，不应静默绕过 DB 配置
        logger.error(f"[RuntimeConfig] DB 模型路由配置损坏，拒绝 fallback: {e}")
        raise ConfigCorruptionError(
            f"模型路由配置损坏: {e}"
        ) from e
