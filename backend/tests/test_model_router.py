"""
ModelRouter 测试：动态算力路由解析逻辑
"""
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from app.llm.model_router import ModelRouter


# ── 基础解析测试 ──────────────────────────────────────────────────────

class TestResolve:
    """resolve() 方法的单元测试"""

    def test_resolve_agent_override_takes_priority(self):
        """Agent 专属覆盖优先于 default tier"""
        config = {
            "default": {"low": "cheap-model", "medium": "mid-model", "high": "best-model"},
            "extractor": {"high": "extractor-premium"},
        }
        router = ModelRouter(config)
        assert router.resolve("extractor", "high") == "extractor-premium"
        # 未覆盖的仍走 default
        assert router.resolve("extractor", "low") == "cheap-model"

    def test_resolve_default_tier_when_no_agent_override(self):
        """无 agent override 时走 default tier"""
        config = {
            "default": {"low": "cheap-model", "medium": "mid-model", "high": "best-model"},
        }
        router = ModelRouter(config)
        assert router.resolve("planner", "low") == "cheap-model"
        assert router.resolve("reflector", "medium") == "mid-model"
        assert router.resolve("synthesizer", "high") == "best-model"

    def test_resolve_returns_none_for_unconfigured_complexity(self):
        """未配置的 complexity_level 返回 None"""
        config = {
            "default": {"low": "cheap-model", "high": "best-model"},
        }
        router = ModelRouter(config)
        assert router.resolve("planner", "medium") is None

    def test_resolve_empty_config_returns_none(self):
        """空 dict 全部返回 None"""
        router = ModelRouter({})
        assert router.resolve("planner", "low") is None
        assert router.resolve("extractor", "high") is None

    def test_resolve_none_config_returns_none(self):
        """ModelRouter(None) 全部返回 None"""
        router = ModelRouter(None)
        assert router.resolve("planner", "low") is None
        assert router.resolve("extractor", "high") is None

    def test_resolve_partial_agent_override(self):
        """Agent override 只配了部分 tier，其余走 default"""
        config = {
            "default": {"low": "d-low", "medium": "d-mid", "high": "d-high"},
            "extractor": {"low": "e-low"},
        }
        router = ModelRouter(config)
        assert router.resolve("extractor", "low") == "e-low"
        assert router.resolve("extractor", "medium") == "d-mid"
        assert router.resolve("extractor", "high") == "d-high"


# ── from_settings 测试 ─────────────────────────────────────────────────

class TestFromSettings:
    """from_settings() 类方法测试"""

    def test_from_settings_loads_routing(self):
        """正确从 model_settings.json 加载 routing 配置"""
        settings = {
            "default_model": "gpt-3.5-turbo",
            "routing": {
                "default": {"low": "qwen-turbo", "medium": "qwen-plus", "high": "deepseek-v4"},
                "extractor": {"low": "qwen-turbo"},
            },
        }
        with patch.object(Path, "exists", return_value=True), patch.object(
            Path, "read_text", return_value=json.dumps(settings)
        ):
            router = ModelRouter.from_settings()
            assert router.resolve("planner", "medium") == "qwen-plus"
            assert router.resolve("extractor", "low") == "qwen-turbo"

    def test_from_settings_no_routing_key_returns_none(self):
        """settings 无 routing key 时全返回 None（向后兼容）"""
        settings = {
            "default_model": "gpt-3.5-turbo",
            "temperature": 0.2,
        }
        with patch.object(Path, "exists", return_value=True), patch.object(
            Path, "read_text", return_value=json.dumps(settings)
        ):
            router = ModelRouter.from_settings()
            assert router.resolve("planner", "low") is None
            assert router.resolve("extractor", "high") is None

    def test_from_settings_file_missing_returns_none(self):
        """settings 文件不存在时全返回 None"""
        with patch.object(Path, "exists", return_value=False):
            router = ModelRouter.from_settings()
            assert router.resolve("planner", "low") is None

    def test_from_settings_corrupted_file_returns_none(self):
        """settings 文件损坏时优雅降级"""
        with patch.object(Path, "exists", return_value=True), patch.object(
            Path, "read_text", return_value="not valid json {{{"
        ):
            router = ModelRouter.from_settings()
            assert router.resolve("planner", "low") is None


# ── 边界情况 ────────────────────────────────────────────────────────────

class TestEdgeCases:
    """边界情况测试"""

    def test_unknown_role_uses_default(self):
        """未知 agent role 走 default tier"""
        config = {
            "default": {"low": "d-low", "medium": "d-mid", "high": "d-high"},
        }
        router = ModelRouter(config)
        assert router.resolve("unknown_agent", "medium") == "d-mid"

    def test_invalid_complexity_string(self):
        """非标准 complexity_level 返回 None"""
        config = {
            "default": {"low": "d-low", "medium": "d-mid", "high": "d-high"},
        }
        router = ModelRouter(config)
        assert router.resolve("planner", "critical") is None
        assert router.resolve("planner", "") is None

    def test_agent_override_with_none_values(self):
        """Agent override 中 None 值的字段走 default"""
        config = {
            "default": {"low": "d-low", "medium": "d-mid", "high": "d-high"},
            "extractor": {"low": None, "medium": None, "high": "e-high"},
        }
        router = ModelRouter(config)
        # None 值被跳过，走 default
        assert router.resolve("extractor", "low") == "d-low"
        assert router.resolve("extractor", "medium") == "d-mid"
        assert router.resolve("extractor", "high") == "e-high"
