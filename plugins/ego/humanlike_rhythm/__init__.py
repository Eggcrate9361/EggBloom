# -*- coding: utf-8 -*-
"""
humanlike_rhythm 插件：给回复加拟人节奏，不修改本体。
仅在 before_reply / after_reply 钩子里做轻量延迟与配置注入。
"""
import json
import os
import random
import time
from typing import Any, Dict, Optional

PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(PLUGIN_DIR, "data")
CONFIG_FILE = os.path.join(DATA_DIR, "config.json")

PLUGIN_META = {
    "name": "humanlike_rhythm",
    "version": "0.1.0",
    "description": "拟人节奏：可配置延迟区间，before_reply 加轻度停顿，after_reply 记录节奏统计。",
    "author": "EggBloom",
    "hooks": ["before_reply", "after_reply"],
    "priority": 80,
    "enabled": True,
    "tags": ["说话节奏"],
    "page": "plugins/humanlike_rhythm/ui.html",
    "config_schema": {
        "enabled": {"type": "bool", "default": True},
        "min_delay": {"type": "float", "default": 0.2},
        "max_delay": {"type": "float", "default": 1.2},
        # ★ 2026-10-24 删掉两项假配置：
        #   typing_indicator / per_sentence_delay 在这里声明过，但**全仓库没有任何
        #   读取方** —— before_reply 只做 random.uniform(min,max) 后 sleep，
        #   两者都不参与。UI 上却各占一张卡片，看起来像能调，实际拨不动。
        #   真正的打字节奏在 bot_core：reply_typing_speed（字/秒，默认 6.0）
        #   算首句前的停顿，句间延迟按句长 0.3~1.0 秒。要调那个，不在这个插件里。
        #   删掉声明是本模块能做的全部 —— 与其留一个永远为 False 的开关，
        #   不如让它根本不存在，用户就不会以为自己调过。
    },
}

os.makedirs(DATA_DIR, exist_ok=True)


def _load_json(path: str, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _save_json(path: str, data):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[HumanlikeRhythm] 保存失败 {path}: {e}")


class HumanlikeRhythm:
    def __init__(self):
        self.config: Dict[str, Any] = _load_json(CONFIG_FILE, {})
        for key, schema in PLUGIN_META.get("config_schema", {}).items():
            if key not in self.config:
                self.config[key] = schema.get("default", False)
        self.stats: Dict[str, int] = _load_json(
            os.path.join(DATA_DIR, "stats.json"), {"delayed": 0, "skipped": 0}
        )

    def get_config(self) -> Dict[str, Any]:
        return dict(self.config)

    def set_config(self, data: Dict[str, Any]) -> Dict[str, Any]:
        for key, value in data.items():
            if key in PLUGIN_META.get("config_schema", {}):
                self.config[key] = value
        _save_json(CONFIG_FILE, self.config)
        return self.get_config()

    def get_stats(self) -> Dict[str, int]:
        return dict(self.stats)

    def _delay_range(self) -> tuple:
        try:
            min_d = float(self.config.get("min_delay", 0.2))
            max_d = float(self.config.get("max_delay", 1.2))
            if max_d < min_d:
                max_d = min_d
            return min_d, max_d
        except (TypeError, ValueError):
            return 0.2, 1.2

    def before_reply(self, message: str = "", group_id: str = "", context: Optional[Dict[str, Any]] = None, **kwargs) -> Optional[Dict[str, Any]]:
        if not self.config.get("enabled", True):
            return None

        min_d, max_d = self._delay_range()
        delay = random.uniform(min_d, max_d)
        time.sleep(delay)
        self.stats["delayed"] += 1
        _save_json(os.path.join(DATA_DIR, "stats.json"), self.stats)
        return None

    def after_reply(self, message: str = "", reply: str = "", context: Optional[Dict[str, Any]] = None, **kwargs) -> Optional[Dict[str, Any]]:
        # 轻量统计，不阻塞主流程
        return None


_rhythm = HumanlikeRhythm()


def on_load():
    print("[HumanlikeRhythm] 已加载")


def on_unload():
    print("[HumanlikeRhythm] 已卸载")


def set_ai_client(client):
    pass


def get_config():
    return _rhythm.get_config()


def set_config(data):
    return _rhythm.set_config(data)


def get_stats():
    return _rhythm.get_stats()


def before_reply(**kwargs):
    return _rhythm.before_reply(**kwargs)


def after_reply(**kwargs):
    return _rhythm.after_reply(**kwargs)

# ── 通用插件通道动作（WebUI plugin_id='ego.humanlike_rhythm'） ──
PLUGIN_ACTIONS = {
    "get_config": lambda params: get_config(),
    "set_config": lambda params: set_config(params or {}),
    "get_stats": lambda params: get_stats(),
}
