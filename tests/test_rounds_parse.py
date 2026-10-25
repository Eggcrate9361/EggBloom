# -*- coding: utf-8 -*-
"""tests/test_rounds_parse.py —— 日志页轮次解析（notice 事件）

2026-10-25：`collect_rounds` 原来只认消息与群回复，戳一戳、撤回、成员增减、
禁言整类不可见。这个文件直接喂日志文本，锁住四件事：

  1. 一次戳一戳（事件 → 反戳 → 回复 → 表情包）收成一轮，reply / tools 齐备。
  2. 群标签带群名时（`群[远望酒庄(991461217)]`）scope 归一成群号，
     会话列表不出现「群 远望酒庄(991461217)」「群 991461217」两项。
  3. 撤回 / 入群 / 退群 / 禁言 / 解除禁言 各自成轮。
  4. 私聊戳一戳归到 `pm`。

样本照抄真机日志（`AppData/Local/EggBloom_v3/qq_bot.log`），不自己编格式。

运行：python tests/test_rounds_parse.py
"""
import os
import sys
import tempfile
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import webui.webui_server as ws  # noqa: E402

PASS = 0
FAIL = 0
FAILURES = []


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK]   {name}")
    else:
        FAIL += 1
        FAILURES.append(name + (f"  → {extra}" if extra else ""))
        print(f"  [FAIL] {name}  {extra}")


class _TempLog:
    """把 `_log_paths` 指到临时文件，退出时还原。"""

    def __init__(self, text):
        self.text = text
        self._orig = None
        self.dir = None

    def __enter__(self):
        self.dir = tempfile.mkdtemp(prefix="eggbloom_rounds_")
        p = Path(self.dir) / "qq_bot.log"
        p.write_text(self.text, encoding="utf-8")
        self._orig = ws._log_paths
        ws._log_paths = lambda: [p]
        return self

    def __exit__(self, *exc):
        ws._log_paths = self._orig
        return False


def _rounds_for(text, limit=50):
    with _TempLog(text):
        return ws.collect_rounds(limit)


def test_poke_round():
    print("\n[1] 戳一戳：事件 → 反戳 → 回复 → 表情包收成一轮")
    text = (
        "[14:47:33] [事件] 收到: post_type=notice msg_type= uid=(/ω＼)(3150007406)"
        " gid=远望酒庄(991461217)\n"
        "[14:47:34] [事件] 戳一戳: user_id=小明(10086001) group_id=远望酒庄(991461217)\n"
        "[14:47:34] [事件] 反戳 send_poke user=小明(10086001)"
        " group=远望酒庄(991461217) retcode=0\n"
        "[14:47:34] 群[远望酒庄(991461217)] 戳一戳触发会话模式\n"
        "[14:47:41] 群[远望酒庄(991461217)] 戳一戳回复: 手闲得慌就来找我聊天嘛\n"
        "[14:47:42] 群[远望酒庄(991461217)] 戳一戳表情包: 暴躁\n"
    )
    r = _rounds_for(text)
    check("只产出一轮", r["total"] == 1, r["total"])
    if not r["rounds"]:
        check("有轮次可断言", False)
        return
    row = r["rounds"][0]
    check("scope 归一群号", row.get("scope") == "991461217", row.get("scope"))
    check("who 是戳的人", row.get("who") == "小明", row.get("who"))
    check("ask 是戳事件", row.get("ask") == "（戳了戳你）", row.get("ask"))
    check("route 记了链路", "反戳" in (row.get("route") or ""), row.get("route"))
    check("reply 取到回复", row.get("reply") == "手闲得慌就来找我聊天嘛", row.get("reply"))
    memes = [x for x in row.get("tools") or [] if x.get("name") == "表情包"]
    check("表情包进 tools", len(memes) == 1, row.get("tools"))
    check("表情包带上情绪", memes and memes[0].get("q") == "暴躁", memes)
    check("反戳进 decisions",
          any("反戳" in d and "send_poke" in d for d in row.get("decisions") or []),
          row.get("decisions"))
    labels = [s["label"] for s in r["scopes"]]
    check("会话列表只有一种群标签",
          sum(1 for x in labels if "991461217" in x) == 1, labels)


def test_poke_ignore_not_a_round():
    print("\n[2] 目标是别人（群友互戳）的行不进轮次")
    text = (
        "[13:58:38] [事件] 戳一戳忽略: 目标非 Bot"
        " (target=(/ω＼)(3150007406)) user=(/ω＼)(3150007406)\n"
    )
    r = _rounds_for(text)
    check("没有轮次", r["total"] == 0, r["total"])


def test_private_poke():
    print("\n[3] 私聊戳一戳归到 pm")
    text = (
        "[13:45:06] [事件] 戳一戳: user_id=(/ω＼)(3150007406) group_id=私聊\n"
        "[13:45:06] [事件] 反戳 user=(/ω＼)(3150007406) group=私聊 retcode=0\n"
        "[13:45:10] 私聊戳一戳回复: 手还挺欠，一下哪够\n"
    )
    r = _rounds_for(text)
    check("一轮", r["total"] == 1, r["total"])
    if r["rounds"]:
        row = r["rounds"][0]
        check("scope=pm", row.get("scope") == "pm", row.get("scope"))
        check("who 剥掉括号里的号", row.get("who") == "/ω＼", row.get("who"))
        check("回复取到", row.get("reply") == "手还挺欠，一下哪够", row.get("reply"))


def test_notice_events():
    print("\n[4] 撤回 / 入群 / 退群 / 禁言 / 解除禁言 各自成轮")
    text = (
        "[10:00:01] [事件] 群成员增加: group=829854136 user=新人(1001)"
        " sub=invite operator=1\n"
        "[10:00:02] [事件] 群成员减少: group=829854136 user=旧人(1002)"
        " sub=leave operator=1\n"
        "[10:00:03] [事件] 群解除禁言: group=829854136 user=0 操作者=x\n"
        "[16:56:59] [事件] 群禁言: group=829854136 user=0 时长=600秒"
        " 操作者=AAA无理由退货 (3865661256)\n"
        "[23:54:31] [事件] 撤回: type=group_recall user_id=碱式碳酸大脑(2047271391)"
        " group_id=829854136 msg_id=133752479\n"
    )
    r = _rounds_for(text)
    check("五轮", r["total"] == 5, r["total"])
    asks = [x.get("ask") for x in r["rounds"]]
    routes = [x.get("route") for x in r["rounds"]]
    check("入群成轮", "进群" in asks, asks)
    check("退群成轮", "退群" in asks, asks)
    check("解除禁言成轮", "解除禁言" in asks, asks)
    check("禁言成轮并带时长",
          any(a.startswith("被禁言") and "600" in a for a in asks), asks)
    check("撤回成轮并带 msg_id",
          any("133752479" in (a or "") for a in asks), asks)
    check("route 写事件名", "撤回" in routes and "群成员增加" in routes, routes)
    check("事件轮没有回复", all(not x.get("reply") for x in r["rounds"]),
          [x.get("reply") for x in r["rounds"]])
    check("scope 全是群号", {x.get("scope") for x in r["rounds"]} == {"829854136"},
          {x.get("scope") for x in r["rounds"]})


def test_scope_key():
    print("\n[5] _scope_key 的三种写法")
    check("纯群号", ws._scope_key("829854136") == "829854136")
    check("群名+群号", ws._scope_key("远望酒庄(991461217)") == "991461217")
    check("私聊", ws._scope_key("私聊") == "pm")
    check("空值", ws._scope_key("") == "pm")


def main():
    test_poke_round()
    test_poke_ignore_not_a_round()
    test_private_poke()
    test_notice_events()
    test_scope_key()

    print("\n" + "=" * 56)
    print(f"通过 {PASS} / 失败 {FAIL}")
    if FAILURES:
        print("失败项：")
        for f in FAILURES:
            print("  -", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
