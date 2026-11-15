# -*- coding: utf-8 -*-
"""称呼与约定：跨场景人档案（P0-4，2026-10-30）。

## 真机证据
    18:18 你现在暂时叫我拉普兰德 →「行吧，你爱叫啥叫啥」→ 之后一次没用过
    18:13 艾特我说"天王盖地虎"你答"小a炖蘑菇" →「记住了」→ 18:22 测试失败

根因：答应了没有写回动作，`chat_history` 又按会话分键 → 私聊的约定进不了群聊。

## 钉死
  1. 解析只认显式说法；「别叫我老板」绝不能存成「老板」
  2. 存储按 QQ 分人，过期（称呼 7 天 / 约定 30 天）自动失效
  3. 同一份数据同时喂私聊和群聊（prompt_line），群聊的说话人标签也用别名
  4. 存储坏了不影响回复

运行：python tests/test_people_alias.py
"""
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from _sandbox import sandbox_neurobase  # noqa: E402

sandbox_neurobase()

import people_alias as pa  # noqa: E402

PASS = []
FAIL = []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name if not extra else f"{name}  {extra}")


# ── 1. 解析 ─────────────────────────────────────────────────────
check("以后叫我X", pa.parse_alias("以后叫我拉普兰德") == "拉普兰德",
      pa.parse_alias("以后叫我拉普兰德"))
check("暂时叫我X", pa.parse_alias("你现在暂时叫我拉普兰德") == "拉普兰德",
      pa.parse_alias("你现在暂时叫我拉普兰德"))
check("语气词不是名字", pa.parse_alias("你就叫我小a吧") == "小a",
      pa.parse_alias("你就叫我小a吧"))
check("否定不存", pa.parse_alias("别叫我老板") == "", pa.parse_alias("别叫我老板"))
check("没名字不存", pa.parse_alias("叫我") == "", pa.parse_alias("叫我"))
check("自我介绍不算", pa.parse_alias("我叫小明") == "", pa.parse_alias("我叫小明"))

check("成对约定", pa.parse_agreement('艾特你说"天王盖地虎"，你答"小a炖蘑菇"')
      == ("天王盖地虎", "小a炖蘑菇"),
      pa.parse_agreement('艾特你说"天王盖地虎"，你答"小a炖蘑菇"'))
# ★ 2026-10-30 真机说法（14:35）：中间隔着逗号，旧正则漏了
_m_real = "那我等一下在另一个群戳你一下，然后艾特你说一句，天王盖地虎，你再回答我小a炖蘑菇"
check("真机说法：说一句，A，你再回答我 B",
      pa.parse_agreement(_m_real) == ("天王盖地虎", "小a炖蘑菇"),
      pa.parse_agreement(_m_real))
check("口语碎片不成约定", pa.parse_agreement("你要是这样的话") == ("", ""),
      pa.parse_agreement("你要是这样的话"))
check("只说暗号", pa.parse_agreement("暗号是青龙偃月") == ("青龙偃月", ""),
      pa.parse_agreement("暗号是青龙偃月"))
check("没有约定", pa.parse_agreement("今天好累") == ("", ""),
      pa.parse_agreement("今天好累"))

# ── 2. 存储与过期 ───────────────────────────────────────────────
tmp = tempfile.mkdtemp(prefix="people_alias_test_")
_orig = pa.store_path()
try:
    p = os.path.join(tmp, "aliases.json")
    pa.set_store_path(p)

    check("还没存 → 空", pa.get_alias("10001") == "", pa.get_alias("10001"))
    check("set_alias 成功", pa.set_alias("10001", "拉普兰德", now=1000.0) is True)
    check("get_alias 读得回", pa.get_alias("10001", now=1001.0) == "拉普兰德",
          pa.get_alias("10001", now=1001.0))
    check("按人分开", pa.get_alias("10002", now=1001.0) == "")

    # 重新读盘（清内存缓存）
    pa.set_store_path(p)
    check("落盘后重启仍读得到", pa.get_alias("10001", now=1002.0) == "拉普兰德",
          pa.get_alias("10001", now=1002.0))

    # 过期
    expired = 1000.0 + pa.ALIAS_TTL + 5
    check("称呼 7 天后失效", pa.get_alias("10001", now=expired) == "",
          pa.get_alias("10001", now=expired))

    # 约定
    check("add_agreement 成功", pa.add_agreement("10001", "天王盖地虎", "小a炖蘑菇", now=2000.0))
    ags = pa.agreements_for("10001", now=2001.0)
    check("约定读得回", len(ags) == 1 and ags[0]["reply"] == "小a炖蘑菇", ags)
    check("约定 30 天后失效",
          pa.agreements_for("10001", now=2000.0 + pa.AGREEMENT_TTL + 5) == [])

    # prompt_line
    pa.set_alias("10001", "拉普兰德", now=3000.0)
    pa.add_agreement("10001", "天王盖地虎", "小a炖蘑菇", now=3000.0)
    line = pa.prompt_line("10001", now=3001.0)
    check("prompt 带称呼", "拉普兰德" in line, line)
    check("prompt 带约定", "天王盖地虎" in line and "小a炖蘑菇" in line, line)
    check("没有就空串", pa.prompt_line("999999", now=3001.0) == "")

    # 坏文件不崩
    with open(p, "w", encoding="utf-8") as f:
        f.write("{坏掉的 JSON")
    pa.set_store_path(p)
    check("坏 JSON 当没有", pa.get_alias("10001") == "", pa.get_alias("10001"))
    check("坏 JSON 仍能写入", pa.set_alias("10001", "小a") is True)

    # ── 3. bot_core 接线 ────────────────────────────────────────
    import bot_core
    _orig_log = bot_core.log
    bot_core.log = lambda _m: None
    try:
        bot_core._people_alias_capture("900001", "以后叫我拉普兰德吧")
        check("bot_core 认得出并落盘", pa.get_alias("900001") == "拉普兰德吧" or
              pa.get_alias("900001") == "拉普兰德", pa.get_alias("900001"))
        check("群聊说话人标签用别名", bot_core._display_name("900001", "777") == "拉普兰德",
              bot_core._display_name("900001", "777"))
        pl = bot_core._people_alias_prompt("900001")
        check("注入行带称呼", "拉普兰德" in pl, pl)

        bot_core._people_alias_capture("900001", '暗号是"天王盖地虎"')
        check("约定也进了注入行", "天王盖地虎" in bot_core._people_alias_prompt("900001"),
              bot_core._people_alias_prompt("900001"))
        # 没说过的人不受影响
        check("别人不受影响", bot_core._display_name("900002", "777") != "拉普兰德")
    finally:
        bot_core.log = _orig_log
finally:
    pa.set_store_path(_orig)
    shutil.rmtree(tmp, ignore_errors=True)

print(f"通过 {len(PASS)} / 失败 {len(FAIL)}")
for n in FAIL:
    print(f"  FAIL  {n}")
sys.exit(1 if FAIL else 0)
