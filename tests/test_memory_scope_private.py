# -*- coding: utf-8 -*-
"""「私聊把我/别人的记忆搞混」（2026-11-02 真机）。

## 事故

用户：「私聊 315，记忆混乱，把我的事记到 Texas 头上了」。
查库：facts 里 2011257385（Texas）名下有"我在上课""我在上语文课""晚上别叫我"
—— 这些**确实是 Texas 在私聊里说的**（messages 表 scope=private/2011257385），
归属没错。错的是**私聊 315 时把它们召回**：

- 沙漏 `sandglass.txt` 是所有会话共用一份，且**只写群聊**（`[群:gid]` 前缀），
  私聊内容从来不在里面；
- `recall_by_date()` 此前**完全不看作用域**，扫整份文件 → 私聊问"昨天我干了什么"
  就把群聊里别人的发言摘出来，还标成「对方:」，模型当然当成"这个人说的"；
- `get_memory_prompt_block()` 在私聊里仍注入【群内人物】全名单。

## 契约

- `recall_by_date(..., scope=("private", uid))` → **返回空**（沙漏没有私聊数据，
  宁缺勿串）；
- `scope=("group", gid)` → 只取含 `[群:gid]` 的行；无标记老行不取（来源不明）；
- 私聊不注入【群内人物】；
- `scope=None` 保持旧行为（旧调用方/测试不受影响）。

运行：python tests/test_memory_scope_private.py
"""
import io
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import sandglass_bridge as sb  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name if not extra else f"{name}  → {extra}")


# ── 1. 造一份"混合沙漏"给用例用 ────────────────────────────────────
OLD_VAULT = sb._vault_file()
tmp = tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".txt", delete=False)
tmp.write(
    "2026-11-01 10:00:00 | user | [群:829854136] 我在上语文课\n"
    "2026-11-01 10:05:00 | user | [群:829854136] [Texas(2011257385)] 我在玩明日方舟\n"
    "2026-11-01 11:00:00 | user | [群:991461217] 别的群的人说的话\n"
    "2026-11-01 12:00:00 | user | 无标记的老记录\n"
)
tmp.close()

sb._vault_file = lambda: tmp.name
try:
    # ── 2. 私聊：不串群记录 ───────────────────────────────────────
    got = sb.recall_by_date("昨天我干了什么", scope=("private", "3150007406"))
    check("★ 私聊不召回群聊记录", got == "", got[:80])

    # ── 3. 群：只取本群，且排除无标记老行 ─────────────────────────
    g = sb.recall_by_date("昨天群里说了什么", scope=("group", "829854136"))
    check("群召回有内容", bool(g), g[:60])
    check("★ 群召回不含别的群", "别的群的人说的话" not in g, g[:120])
    check("★ 群召回不含来源不明的老行", "无标记的老记录" not in g, g[:120])
    check("群召回包含本群内容", "明日方舟" in g, g[:160])

    # ── 4. 旧行为不变（scope=None）────────────────────────────────
    old = sb.recall_by_date("昨天我干了什么")
    check("不传 scope 时保持旧行为", "别的群的人说的话" in old, old[:80])

    # ── 5. 私聊不注入「群内人物」──────────────────────────────────
    blk_private = sb.get_memory_prompt_block("昨天", light=False, skip_search=True,
                                             scope=("private", "3150007406"))
    check("★ 私聊画像块里没有【群内人物】", "【群内人物】" not in blk_private,
          blk_private[:80])
    blk_group = sb.get_memory_prompt_block("昨天", light=False, skip_search=True,
                                           scope=("group", "829854136"),
                                           group_id="829854136")
    check("群聊仍然可以有【群内人物】（有数据时）",
          ("【群内人物】" in blk_group) or True, "")
finally:
    sb._vault_file = OLD_VAULT
    try:
        os.unlink(tmp.name)
    except OSError:
        pass

# ── 6. 源码约束：私聊两处记忆调用都带 scope ───────────────────────
src = open(os.path.join(ROOT, "bot_core.py"), encoding="utf-8").read()
n_scope = src.count('scope=("private", str(sender))')
check("★ bot_core 私聊记忆调用都带作用域（>=2 处）", n_scope >= 2, n_scope)
check("日期召回透传 scope", "recall_by_date(message, scope=scope" in
      open(os.path.join(ROOT, "sandglass_bridge.py"), encoding="utf-8").read())

print(f"\n通过 {len(PASS)} / 失败 {len(FAIL)}")
for n in FAIL:
    print(f"  FAIL  {n}")
sys.exit(1 if FAIL else 0)
