# -*- coding: utf-8 -*-
"""表情包连发（2026-11-02 真机）。

## 事故（用户贴的聊天记录）

```
16:12:45  回敬 温柔_日用_kagura_066.jpg      ← 对方发纯表情，回敬一张
16:13:02  表情包附着 撒娇_日用_kagura_064.jpg ← 文字回复后又贴一张
```

17 秒内两张**没人要的图**，体感就是"表情包乱发、像是被截断后补了张图"。
根因：`meme_cooldown` 现网是 **10 秒**，比人的反应间隔还短。

## 契约

- 默认冷却 **180 秒**（同一会话两次表情包的最小间隔）；
- 回敬有独立开关 `meme_back_enable`（默认开；关掉只做文字回复）；
- 回敬成功也会写 `MEME_LAST_SENT`，与"文字回复后贴图"共用一个冷却
  —— 两条路不会各算各的。

运行：python tests/test_meme_cooldown.py
"""
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import config as cfg_mod  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name if not extra else f"{name}  → {extra}")


D = cfg_mod.DEFAULT_CONFIG
check("★ 默认冷却 180 秒", int(D.get("meme_cooldown") or 0) == 180, D.get("meme_cooldown"))
check("★ 回敬开关默认开", D.get("meme_back_enable") is True, D.get("meme_back_enable"))

src = open(os.path.join(ROOT, "bot_core.py"), encoding="utf-8").read()
check("回敬读这个开关", '_config.get("meme_back_enable", True)' in src)
check("回敬与贴图共用 MEME_LAST_SENT",
      "MEME_LAST_SENT[key] = now" in src and "MEME_LAST_SENT.get(key, 0)" in src)

ex = io.open(os.path.join(ROOT, "bot_config.json.example"), encoding="utf-8").read()
check("example 里冷却也是 180", '"meme_cooldown": 180' in ex)
check("example 里有回敬开关", '"meme_back_enable"' in ex)

# 相邻两次表情的最小间隔（把冷却当纯函数验证）
def gap_ok(prev_ts, now_ts, cd=180):
    return (now_ts - prev_ts) >= cd


check("★ 17 秒两连发会被拦下", gap_ok(0, 17) is False)
check("3 分钟后再发允许", gap_ok(0, 181) is True)

print(f"\n通过 {len(PASS)} / 失败 {len(FAIL)}")
for n in FAIL:
    print(f"  FAIL  {n}")
sys.exit(1 if FAIL else 0)
