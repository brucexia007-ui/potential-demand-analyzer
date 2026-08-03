"""
动态算力路由 — 根据 agent_role × complexity_level 解析模型名。

配置格式（model_settings.json 中的 routing 键 或 DB model_routes 表）:
{
  "routing": {
    "default": {"low": "qwen-plus", "medium": "qwen-max", "high": "qwen-max"},
    "extractor": {"low": "qwen-plus", "high": "qwen-max"}
  }
}

解析优先级:
1. Agent 专属覆盖 (routing["extractor"]["high"])
2. 默认 tier (routing["default"]["high"])
3. 返回 None → 调用方使用 GatewayClient 的默认模型（自动从 Provider 选取）
"""
import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

SETTINGS_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "model_settings.json"


class ModelRouter:
    """根据 agent_role + complexity_level 解析应该使用哪个模型。"""

    def __init__(self, routing_config: Optional[dict] = None):
        self._config = routing_config or {}

    def resolve(self, role: str, complexity_level: str) -> Optional[str]:
        """
        返回解析后的模型名，或 None 表示使用 GatewayClient 默认模型。

        Args:
            role: agent 角色名 (planner / extractor / reflector / synthesizer / auditor / skeptic / bidding_analyst / policy_analyst)
            complexity_level: 复杂度 (low / medium / high)

        Returns:
            模型名字符串，或 None
        """
        # 1. Agent 专属覆盖
        agent_cfg = self._config.get(role)
        if isinstance(agent_cfg, dict):
            model = agent_cfg.get(complexity_level)
            if model:
                return model

        # 2. 默认 tier
        default_cfg = self._config.get("default", {})
        if isinstance(default_cfg, dict):
            model = default_cfg.get(complexity_level)
            if model:
                return model

        # 3. 未配置 → 回退到 GatewayClient 默认模型
        return None

    @classmethod
    def from_settings(cls) -> "ModelRouter":
        """加载路由配置 —— 优先 DB，fallback 到 model_settings.json。

        DB 中 model_routes 表有数据时使用 DB 配置（免重启生效），
        DB 无数据或不可用时回退到 JSON 文件。
        """
        # 1. 优先从 DB 加载
        try:
            from app.db.session import SessionLocal
            from app.config_center.runtime_config_loader import load_model_routes_from_db

            db = SessionLocal()
            try:
                db_routes = load_model_routes_from_db(db)
                if db_routes:
                    logger.debug("[ModelRouter] 从 DB 加载路由配置")
                    return cls(db_routes)
            finally:
                db.close()
        except Exception as e:
            logger.warning(f"[ModelRouter] DB 路由加载失败，回退到文件: {e}")

        # 2. Fallback 到 model_settings.json
        try:
            if SETTINGS_FILE.exists():
                data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
                routing_config = data.get("routing", {})
                return cls(routing_config)
        except Exception as e:
            logger.warning(f"加载路由配置失败，使用空配置: {e}")
        return cls({})
