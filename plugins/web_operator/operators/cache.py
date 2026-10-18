# -*- coding: utf-8 -*-
"""
Action Cache - 动作缓存
========================
记录 (domain|action|role|name) -> 稳定 selector，ref 解析失败时先查缓存，
命中即直接按 selector 执行；成功后回写更新。TTL 到期自动失效（页面可能改版）。
"""

import json
import time
from pathlib import Path
from typing import Optional

_TTL_SECONDS = 7 * 24 * 3600


class ActionCache:
    def __init__(self, path: Path, enabled: bool = True):
        self.path = Path(path)
        self.enabled = enabled
        self._data = {}
        self._loaded = False

    def _load(self):
        if self._loaded or not self.enabled:
            return
        try:
            if self.path.exists():
                self._data = json.loads(self.path.read_text(encoding="utf-8")) or {}
        except Exception:
            self._data = {}
        self._loaded = True

    def _save(self):
        if not self.enabled:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    def get(self, key: str) -> Optional[str]:
        if not self.enabled:
            return None
        self._load()
        item = self._data.get(key)
        if not isinstance(item, dict):
            return None
        ts = float(item.get("ts") or 0)
        if time.time() - ts > _TTL_SECONDS:
            self._data.pop(key, None)
            self._save()
            return None
        return item.get("selector") or None

    def put(self, key: str, selector: str):
        if not self.enabled or not selector:
            return
        self._load()
        self._data[key] = {"selector": selector, "ts": time.time()}
        # 轻量上限，避免无限增长
        if len(self._data) > 2000:
            try:
                oldest = sorted(self._data.items(), key=lambda kv: kv[1].get("ts", 0))[:500]
                for k, _ in oldest:
                    self._data.pop(k, None)
            except Exception:
                pass
        self._save()

    def clear(self):
        self._data = {}
        self._loaded = True
        self._save()
