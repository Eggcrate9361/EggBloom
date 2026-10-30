# -*- coding: utf-8 -*-
"""按厂商计价（pricing.py，2026-10-30）。

钉死：
  1. DeepSeek 分缓存命中/未命中/输出三档，且高峰 = 空闲 ×2
  2. 高峰窗口：周一至周五 9–12、14–18
  3. deepseek-v4-pro 与 flash 是两套价
  4. 其他厂商走平铺价，并标记 estimated=True（不冒充真实账单）
  5. 配置可覆盖任意厂商
  6. usage 缺字段按 0 算，不猜

运行：python tests/test_pricing.py
"""
import datetime
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import pricing as P  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name if not extra else f"{name}  {extra}")


PEAK = datetime.datetime(2026, 10, 30, 10, 0)        # 周五上午
OFF = datetime.datetime(2026, 10, 30, 20, 0)         # 周五晚
WEEKEND = datetime.datetime(2026, 10, 31, 10, 0)     # 周六上午

check("周五 10 点是高峰", P.is_peak(PEAK) is True)
check("周五 20 点空闲", P.is_peak(OFF) is False)
check("周六 10 点空闲", P.is_peak(WEEKEND) is False)
check("周五 13 点空闲（午休）", P.is_peak(datetime.datetime(2026, 10, 30, 13, 0)) is False)
check("周五 18 点后空闲", P.is_peak(datetime.datetime(2026, 10, 30, 18, 0)) is False)

# 1M 命中的价格 = 单价
u = {"prompt_tokens": 1_000_000, "prompt_cache_hit_tokens": 1_000_000,
     "prompt_cache_miss_tokens": 0, "completion_tokens": 0}
r = P.estimate("deepseek", "deepseek-flash", u, now=PEAK)
check("flash 高峰 命中 1M = 0.04", abs(r["cost"] - 0.04) < 1e-9, r)
check("tier=peak", r["tier"] == "peak" and r["estimated"] is False, r)
r2 = P.estimate("deepseek", "deepseek-flash", u, now=OFF)
check("flash 空闲 命中 1M = 0.02", abs(r2["cost"] - 0.02) < 1e-9, r2)

u_miss = {"prompt_tokens": 1_000_000, "prompt_cache_hit_tokens": 0,
          "prompt_cache_miss_tokens": 1_000_000, "completion_tokens": 0}
check("flash 高峰 未命中 1M = 2",
      abs(P.estimate("deepseek", "deepseek-flash", u_miss, now=PEAK)["cost"] - 2.0) < 1e-9)
u_out = {"prompt_tokens": 0, "completion_tokens": 1_000_000}
check("flash 高峰 输出 1M = 8",
      abs(P.estimate("deepseek", "deepseek-flash", u_out, now=PEAK)["cost"] - 8.0) < 1e-9)

# pro 更贵
check("pro 高峰 未命中 1M = 9",
      abs(P.estimate("deepseek", "deepseek-v4-pro", u_miss, now=PEAK)["cost"] - 9.0) < 1e-9)

# 混合
mix = {"prompt_tokens": 1000, "prompt_cache_hit_tokens": 600,
       "prompt_cache_miss_tokens": 400, "completion_tokens": 100}
r3 = P.estimate("deepseek", "deepseek-flash", mix, now=PEAK)
want = (600 * 0.04 + 400 * 2.0 + 100 * 8.0) / 1e6
check("混合用量", abs(r3["cost"] - round(want, 6)) < 1e-9, r3)

# 其他厂商：平铺 + estimated
r4 = P.estimate("openai", "gpt-4o", {"prompt_tokens": 1_000_000, "completion_tokens": 0}, now=PEAK)
check("其他厂商走平铺", abs(r4["cost"] - 0.5) < 1e-9, r4)
check("其他厂商标记估算", r4["estimated"] is True and r4["tier"] == "flat", r4)

# 覆盖
P.set_overrides({"myprov": {"input": 3.0, "output": 12.0}})
r5 = P.estimate("myprov", "x", {"prompt_tokens": 1_000_000, "completion_tokens": 0}, now=PEAK)
check("覆盖价生效", abs(r5["cost"] - 3.0) < 1e-9, r5)
check("覆盖不算估算", r5["estimated"] is False and r5["tier"] == "override", r5)
P.set_overrides({})

# 缺字段
check("空 usage = 0 元", P.estimate("deepseek", "deepseek-flash", {}, now=PEAK)["cost"] == 0)
check("None usage = 0 元", P.estimate("deepseek", "deepseek-flash", None, now=PEAK)["cost"] == 0)

# 从 url 认厂商
check("认 deepseek", P.provider_from_url("https://api.deepseek.com/chat/completions") == "deepseek")
check("认 openai", P.provider_from_url("https://api.openai.com/v1/chat") == "openai")
check("认不出返回空", P.provider_from_url("https://example.com/x") == "")

print(f"通过 {len(PASS)} / 失败 {len(FAIL)}")
for n in FAIL:
    print(f"  FAIL  {n}")
sys.exit(1 if FAIL else 0)
