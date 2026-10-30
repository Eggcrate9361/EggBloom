# -*- coding: utf-8 -*-
"""记忆全打通：跨场景近况召回（2026-10-30）。

## 补的是什么

`memory_scope_share` 打开后，沙漏的**关键词召回**已经互通；但"他刚才在群里
说了什么"这种**没有关键词**的近况，语义检索是瞎的 —— 两边等于没通。

这里补一条确定性的路：按时间 + 归属，把他**另一个场景**最近说过的话捞出来，
在**私聊**里注入一句提示。

## 边界（写死，别扩）

- 只注入私聊：群里注入 = 把别人的私聊贴进群，那是泄露，不是记忆。
- 只取 6 小时内、最多 3 条、每条 80 字。
- 只认带 `(uid)` 归属的**用户行**；agent/assistant 行一律不取。
- `memory_scope_share=False` 时整块不注入。

运行：python tests/test_cross_scene.py
"""
import datetime
import os
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from _sandbox import sandbox_neurobase  # noqa: E402

NB = sandbox_neurobase()

import sandglass_bridge as sb  # noqa: E402

PASS = []
FAIL = []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name if not extra else f"{name}  {extra}")


def _ts(delta_sec=0):
    return datetime.datetime.fromtimestamp(time.time() + delta_sec).strftime(
        "%Y-%m-%d %H:%M:%S")


# 造一份沙漏：私聊行 / 群行 / 别人的行 / 机器人行 / 过期行
lines = [
    f"{_ts(-2 * 3600)} | user | [拉普兰德(10001)] [14:00] 我在群里说的这句",
    f"{_ts(-3 * 3600)} | user | [群:777] [拉普兰德(10001)] [13:00] 群里说的那句",
    f"{_ts(-3 * 3600)} | user | [群:777] [别人(20002)] [13:00] 别人的话",
    f"{_ts(-3 * 3600)} | agent | [群:777] [拉普兰德(10001)] [13:00] 这是bot说的",
    f"{_ts(-9 * 3600)} | user | [群:777] [拉普兰德(10001)] [08:00] 九小时前的旧话",
    f"{_ts(-1 * 3600)} | user | [群:777] [拉普兰德(10001)] [14:30] 最近一句群聊",
]
p = os.path.join(NB, "sandglass.txt")
with open(p, "w", encoding="utf-8") as f:
    f.write("\n".join(lines) + "\n")

try:
    # ── 1. 私聊视角：拿"他在群里说的" ──────────────────────────
    rows = sb.cross_scene_recent("10001")
    check("私聊视角取到群里的行", any("群里说的那句" in r for r in rows), rows)
    check("私聊视角不取私聊行", not any("我在群里说的这句" in r for r in rows), rows)
    check("不取别人的话", not any("别人的话" in r for r in rows), rows)
    check("不取 bot 自己的话", not any("这是bot说的" in r for r in rows), rows)
    check("不取 6 小时前的旧话", not any("九小时前的旧话" in r for r in rows), rows)
    check("最多 3 条", len(rows) <= 3, rows)

    # ── 2. 群聊视角：拿"他私聊说的" ────────────────────────────
    rows_g = sb.cross_scene_recent("10001", exclude_group="777")
    check("群聊视角取到私聊行", any("我在群里说的这句" in r for r in rows_g), rows_g)
    check("群聊视角不重复群里的行",
          not any("群里说的那句" in r for r in rows_g), rows_g)

    # ── 3. limit / 空输入 ──────────────────────────────────────
    check("limit=1 只回一条", len(sb.cross_scene_recent("10001", limit=1)) == 1,
          sb.cross_scene_recent("10001", limit=1))
    check("空 uid → 空", sb.cross_scene_recent("") == [])
    check("没记录的人 → 空", sb.cross_scene_recent("99999") == [])

    # ── 4. bot_core 注入 ───────────────────────────────────────
    import bot_core
    _orig_log = bot_core.log
    bot_core.log = lambda _m: None
    try:
        bot_core._config["memory_scope_share"] = True
        blk = bot_core._cross_scene_block("10001")
        check("私聊注入块带标题", "【另一边】" in blk, blk)
        check("私聊注入块带群里的原话", "群里说的那句" in blk, blk)
        check("群聊不注入（防泄露）",
              bot_core._cross_scene_block("10001", "777") == "",
              bot_core._cross_scene_block("10001", "777"))

        bot_core._config["memory_scope_share"] = False
        check("开关关了就不注入", bot_core._cross_scene_block("10001") == "")
        bot_core._config["memory_scope_share"] = True
    finally:
        bot_core.log = _orig_log
finally:
    try:
        os.remove(p)
    except OSError:
        pass

print(f"通过 {len(PASS)} / 失败 {len(FAIL)}")
for n in FAIL:
    print(f"  FAIL  {n}")
sys.exit(1 if FAIL else 0)
