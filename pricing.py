# -*- coding: utf-8 -*-
"""pricing.py —— 按厂商估算调用成本（2026-10-30）。

## 为什么不是「总 token × 一个系数」

真机日志里一直用 `total * 0.5 / 1e6` 估算，和账单对不上：

- **DeepSeek** 是分批计价的（官方 2026-10-30）：缓存命中 / 未命中 / 输出 三个价，
  而且**高峰时段贵一倍**（周一至周五 9:00–12:00、14:00–18:00，北京时间）；
- 其他厂商多数是「输入一个价、输出一个价」，没有缓存价、没有时段价；
- 还有按请求次数计费的（DeepSeek Search/Fetch ¥5/1000 次），不在这层。

所以这一层只做一件事：**按厂商规则把 usage 折成钱**，不认识的就退回一个
可配置的平铺价，并标记 `estimated=True`（日志里写明"估算"，不冒充真实账单）。

## 用法

    from pricing import estimate
    r = estimate("deepseek", "deepseek-flash", usage)
    r["cost"]       # 元
    r["tier"]       # peak / offpeak / flat
    r["estimated"]  # True = 用的兜底价，不是厂商真实报价

## 价格来源

DeepSeek 官方价（2026-10-30 抓取，单位：元/百万 tokens）：

| 模型 | 输入·命中 | 输入·未命中 | 输出 |
|---|---|---|---|
| deepseek-flash 空闲 | 0.02 | 1 | 4 |
| deepseek-flash 高峰 | 0.04 | 2 | 8 |
| deepseek-v4-pro 空闲 | 0.15 | 4.5 | 13.5 |
| deepseek-v4-pro 高峰 | 0.30 | 9 | 27 |

价格会变；配置里 `pricing_overrides` 可覆盖任意厂商（见 `set_overrides`）。
"""
from __future__ import annotations

import datetime
from typing import Dict, Optional

#: 兜底平铺价（元/百万 tokens）：不认识厂商时用，标记 estimated
DEFAULT_FLAT = {"input": 0.5, "output": 0.5}

#: DeepSeek 官方价：(空闲, 高峰)，单位元/百万 tokens
_DEEPSEEK = {
    "flash": {
        "cache_hit": (0.02, 0.04),
        "cache_miss": (1.0, 2.0),
        "output": (4.0, 8.0),
    },
    "pro": {
        "cache_hit": (0.15, 0.30),
        "cache_miss": (4.5, 9.0),
        "output": (13.5, 27.0),
    },
}

#: 配置覆盖：{provider: {"input": x, "output": y} 或 {cache_hit, cache_miss, output}}
_OVERRIDES: Dict[str, dict] = {}
_LOADED = False


def _load_overrides_from_config() -> None:
    """首次计价时从 bot_config.json 读 `pricing_overrides`（没有就跳过）。"""
    global _LOADED
    if _LOADED:
        return
    _LOADED = True
    try:
        from config import load_config
        d = load_config() or {}
        ov = d.get("pricing_overrides")
        if isinstance(ov, dict) and ov:
            set_overrides(ov)
    except Exception:
        pass


def set_overrides(d: Optional[dict]) -> None:
    """从配置注入自定义价格（覆盖内置表）。"""
    global _OVERRIDES
    _OVERRIDES = {str(k).lower(): v for k, v in (d or {}).items() if isinstance(v, dict)}


def provider_from_url(url: str) -> str:
    """从 base_url 猜厂商（配置里没写 provider 时用）。"""
    u = str(url or "").lower()
    for key in ("deepseek", "openai", "anthropic", "gemini", "dashscope", "moonshot", "zhipu"):
        if key in u:
            return key
    return ""


def model_family(model: str) -> str:
    """模型族：deepseek 只有 flash / pro 两档。"""
    m = str(model or "").lower()
    if "pro" in m:
        return "pro"
    return "flash"


def is_peak(now: Optional[datetime.datetime] = None) -> bool:
    """是否高峰时段：周一至周五 9:00–12:00、14:00–18:00（本地时间 = 北京时间）。"""
    t = now or datetime.datetime.now()
    if t.weekday() >= 5:
        return False
    h = t.hour
    return 9 <= h < 12 or 14 <= h < 18


def rates(provider: str, model: str, now: Optional[datetime.datetime] = None) -> dict:
    """返回本轮单价 `{cache_hit, cache_miss, output, tier, estimated}`（元/百万）。"""
    _load_overrides_from_config()
    p = str(provider or "").lower()
    if p in _OVERRIDES:
        o = _OVERRIDES[p]
        hit = float(o.get("cache_hit", o.get("input", DEFAULT_FLAT["input"])))
        miss = float(o.get("cache_miss", o.get("input", DEFAULT_FLAT["input"])))
        out = float(o.get("output", DEFAULT_FLAT["output"]))
        return {"cache_hit": hit, "cache_miss": miss, "output": out,
                "tier": "override", "estimated": False}
    if p == "deepseek":
        fam = model_family(model)
        table = _DEEPSEEK.get(fam) or _DEEPSEEK["flash"]
        peak = is_peak(now)
        idx = 1 if peak else 0
        return {
            "cache_hit": table["cache_hit"][idx],
            "cache_miss": table["cache_miss"][idx],
            "output": table["output"][idx],
            "tier": "peak" if peak else "offpeak",
            "estimated": False,
        }
    return {"cache_hit": DEFAULT_FLAT["input"], "cache_miss": DEFAULT_FLAT["input"],
            "output": DEFAULT_FLAT["output"], "tier": "flat", "estimated": True}


def estimate(provider: str, model: str, usage: Optional[dict],
             now: Optional[datetime.datetime] = None) -> dict:
    """把一次调用折成钱。usage 缺字段就按 0 算（不猜）。"""
    u = usage if isinstance(usage, dict) else {}
    r = rates(provider, model, now)
    try:
        hit = int(u.get("prompt_cache_hit_tokens") or u.get("cache_hit_tokens") or 0)
    except Exception:
        hit = 0
    try:
        miss = int(u.get("prompt_cache_miss_tokens") or 0)
    except Exception:
        miss = 0
    try:
        prompt = int(u.get("prompt_tokens") or 0)
    except Exception:
        prompt = 0
    if miss <= 0 and prompt > 0:
        miss = max(0, prompt - hit)
    try:
        out = int(u.get("completion_tokens") or 0)
    except Exception:
        out = 0
    cost = (hit * r["cache_hit"] + miss * r["cache_miss"] + out * r["output"]) / 1_000_000.0
    return {"cost": round(cost, 6), "tier": r["tier"], "estimated": r["estimated"],
            "rates": {k: r[k] for k in ("cache_hit", "cache_miss", "output")},
            "tokens": {"hit": hit, "miss": miss, "output": out}}


def label(tier: str) -> str:
    return {"peak": "高峰", "offpeak": "空闲", "override": "自定义",
            "flat": "估算"}.get(str(tier), str(tier))


__all__ = ["DEFAULT_FLAT", "set_overrides", "provider_from_url", "model_family",
           "is_peak", "rates", "estimate", "label"]
