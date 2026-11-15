# -*- coding: utf-8 -*-
"""protocols/base.py 单元测试

运行方式（项目根目录）：
    python -m pytest tests/test_protocols_base.py -v
"""
import os
import sys
import unittest
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from protocols.base import ProtocolAdapter, EVENT_BOT_CORE  # noqa: E402


class ConcreteAdapter(ProtocolAdapter):
    """最小实现，用于测试抽象基类的默认行为"""

    name = "test_adapter"
    SYNC_ONEBOT_URL = False

    def start(self):
        self._running = True

    @property
    def api_base_url(self) -> str:
        return "http://127.0.0.1:9999"


class TestProtocolAdapterBase(unittest.TestCase):
    """ProtocolAdapter 基类行为测试"""

    def setUp(self):
        self.adapter = ConcreteAdapter()

    def test_initial_state_not_running(self):
        self.assertFalse(self.adapter.is_running())

    def test_start_sets_running(self):
        self.adapter.start()
        self.assertTrue(self.adapter.is_running())

    def test_stop_clears_running(self):
        self.adapter.start()
        self.adapter.stop()
        self.assertFalse(self.adapter.is_running())

    def test_restart_toggles_running(self):
        self.adapter.stop()
        self.adapter.restart()
        self.assertTrue(self.adapter.is_running())

    def test_is_ready_default_false(self):
        self.assertFalse(self.adapter.is_ready())

    def test_api_base_url(self):
        self.assertEqual(self.adapter.api_base_url, "http://127.0.0.1:9999")

    def test_name_attribute(self):
        self.assertEqual(self.adapter.name, "test_adapter")

    def test_sync_onebot_url_flag(self):
        self.assertFalse(self.adapter.SYNC_ONEBOT_URL)


class TestProtocolAdapterCallback(unittest.TestCase):
    """状态回调机制测试"""

    def setUp(self):
        self.adapter = ConcreteAdapter()
        self.callback = mock.Mock()
        self.adapter.set_status_callback(self.callback)

    def test_notify_calls_callback(self):
        self.adapter._notify("starting", "初始化中")
        self.callback.assert_called_once_with("starting", "初始化中")

    def test_notify_without_callback_no_error(self):
        adapter2 = ConcreteAdapter()
        adapter2._notify("test", "无回调")  # 不应抛异常

    def test_notify_callback_exception_swallowed(self):
        def bad_callback(status, detail):
            raise RuntimeError("模拟回调崩溃")

        self.adapter.set_status_callback(bad_callback)
        # 不应向外抛异常
        self.adapter._notify("error", "测试异常隔离")


class TestEventBotCore(unittest.TestCase):
    """EVENT_BOT_CORE 常量验证"""

    def test_event_bot_core_is_http(self):
        self.assertTrue(EVENT_BOT_CORE.startswith("http://"))

    def test_event_bot_core_port_3005(self):
        self.assertIn(":3005", EVENT_BOT_CORE)


class TestRegistry(unittest.TestCase):
    """协议注册表验证"""

    def test_registry_imports(self):
        from protocols.registry import get_driver_ids
        self.assertIsInstance(get_driver_ids(), list)

    def test_registry_has_known_drivers(self):
        from protocols.registry import get_driver_ids
        ids = get_driver_ids()
        for name in ("napcat", "llonebot", "snowluma"):
            self.assertIn(name, ids, f"注册表缺少驱动: {name}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
