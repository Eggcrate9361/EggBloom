# -*- coding: utf-8 -*-
"""config.py 单元测试

运行方式（项目根目录）：
    python -m pytest tests/test_config.py -v
"""
import os
import sys
import json
import tempfile
import unittest
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import config  # noqa: E402


class TestAPIProviders(unittest.TestCase):
    """API_PROVIDERS 字典的结构与内容验证"""

    def test_providers_not_empty(self):
        self.assertGreater(len(config.API_PROVIDERS), 0)

    def test_each_provider_has_required_keys(self):
        for key, provider in config.API_PROVIDERS.items():
            self.assertIn("name", provider, f"{key} 缺少 name")
            self.assertIn("url", provider, f"{key} 缺少 url")
            self.assertIn("models", provider, f"{key} 缺少 models")

    def test_provider_key_lowercase_snake(self):
        for key in config.API_PROVIDERS:
            self.assertEqual(key, key.lower().replace(" ", "_"),
                             f"Provider key '{key}' 不符合 snake_case")

    def test_deepseek_provider(self):
        ds = config.API_PROVIDERS.get("deepseek")
        self.assertIsNotNone(ds)
        self.assertIn("deepseek-flash", ds["models"])
        self.assertTrue(ds["url"].startswith("https://"))

    def test_custom_provider_has_empty_url(self):
        custom = config.API_PROVIDERS.get("custom")
        self.assertIsNotNone(custom)
        self.assertEqual(custom["url"], "")
        self.assertEqual(custom["models"], [])


class TestDefaultConfig(unittest.TestCase):
    """DEFAULT_CONFIG 的结构与类型验证"""

    def test_default_config_is_dict(self):
        self.assertIsInstance(config.DEFAULT_CONFIG, dict)

    def test_required_keys_present(self):
        required = [
            "onebot_url", "bot_qq", "target_qqs", "group_ids",
            "api_provider", "api_url", "model_name",
        ]
        for key in required:
            self.assertIn(key, config.DEFAULT_CONFIG, f"缺少必需配置项: {key}")

    def test_target_qqs_is_list(self):
        self.assertIsInstance(config.DEFAULT_CONFIG["target_qqs"], list)

    def test_group_ids_is_list(self):
        self.assertIsInstance(config.DEFAULT_CONFIG["group_ids"], list)

    def test_protocol_drivers_has_all_drivers(self):
        drivers = config.DEFAULT_CONFIG.get("protocol_drivers", {})
        for name in ("napcat", "llonebot", "snowluma"):
            self.assertIn(name, drivers, f"缺少协议驱动配置: {name}")

    def test_auto_accept_structure(self):
        aa = config.DEFAULT_CONFIG.get("auto_accept", {})
        self.assertIn("enabled", aa)
        self.assertIn("friend", aa)
        self.assertIn("group_add", aa)
        self.assertIn("group_invite", aa)
        self.assertIsInstance(aa.get("allowlist", []), list)
        self.assertIsInstance(aa.get("denylist", []), list)

    def test_ai_parameters_types(self):
        cfg = config.DEFAULT_CONFIG
        self.assertIsInstance(cfg.get("ai_temperature"), (int, float))
        self.assertIsInstance(cfg.get("ai_max_tokens"), int)
        self.assertGreater(cfg.get("ai_temperature", 0), 0)
        self.assertLessEqual(cfg.get("ai_temperature", 1), 2)

    def test_session_ttl_positive(self):
        self.assertGreater(config.DEFAULT_CONFIG.get("session_ttl", 0), 0)

    def test_de_ai_structure(self):
        de_ai = config.DEFAULT_CONFIG.get("de_ai", {})
        self.assertIn("enabled", de_ai)
        self.assertIsInstance(de_ai["enabled"], bool)


class TestConfigPaths(unittest.TestCase):
    """配置路径常量验证"""

    def test_config_file_is_path_like(self):
        """CONFIG_FILE 应为字符串或 Path 对象"""
        self.assertTrue(hasattr(config.CONFIG_FILE, '__str__'))

    def test_env_file_is_path_like(self):
        self.assertTrue(hasattr(config.ENV_FILE, '__str__'))

    def test_app_config_file_is_string(self):
        self.assertIsInstance(config.APP_CONFIG_FILE, str)

    def test_script_dir_is_string(self):
        self.assertIsInstance(config.SCRIPT_DIR, str)

    def test_config_file_ends_with_json(self):
        self.assertTrue(str(config.CONFIG_FILE).endswith("bot_config.json"))


class TestProviderSelection(unittest.TestCase):
    """API provider 选择逻辑"""

    def test_provider_key_exists(self):
        """DEFAULT_CONFIG 中的 api_provider 必须在 API_PROVIDERS 中"""
        provider = config.DEFAULT_CONFIG.get("api_provider", "")
        self.assertIn(provider, config.API_PROVIDERS)

    def test_provider_url_matches_default(self):
        """DEFAULT_CONFIG 中的 api_url 应与对应 provider 的 url 一致"""
        provider = config.DEFAULT_CONFIG.get("api_provider", "")
        expected_url = config.API_PROVIDERS.get(provider, {}).get("url", "")
        actual_url = config.DEFAULT_CONFIG.get("api_url", "")
        self.assertEqual(actual_url, expected_url)


if __name__ == "__main__":
    unittest.main(verbosity=2)
