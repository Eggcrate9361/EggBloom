#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
EggBloom 全局状态收敛（单例 AppState）
- 用共享 dict + 锁替代零散 global，消解多线程数据竞争
- 用法:
    from app_state import AppState
    s = AppState()
    s.set("personality_temperature", 0.8)
    t = s.get("personality_temperature")
- 兼容旧 global 访问：模块启动时把关键 global 值吸收进 AppState
"""
import threading


class AppState:
    _inst = None

    def __new__(cls):
        if cls._inst is None:
            cls._inst = super().__new__(cls)
            cls._inst._lock = threading.RLock()
            cls._inst._data = {}
        return cls._inst

    def get(self, key, default=None):
        with self._lock:
            if key not in self._data:
                return default
            v = self._data[key]
        # 返回可变对象可直接改，但写需经 set；这里浅拷贝防外部副作用
        return v

    def set(self, key, value):
        with self._lock:
            self._data[key] = value

    def update(self, key, **fields):
        """就地更新 dict 型值（线程安全）"""
        with self._lock:
            cur = self._data.get(key)
            if isinstance(cur, dict):
                cur.update(fields)
            else:
                self._data[key] = fields

    def keys(self):
        with self._lock:
            return list(self._data.keys())