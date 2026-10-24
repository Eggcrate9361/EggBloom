# -*- coding: utf-8 -*-
"""tests/test_event_classify.py —— 日志页事件分类回归

背景：日志页原来直接把日志文件整行铺出来，一条「小明: 今天去不去」和一串
「loaded module ego」长得一样，用户读不出来发生了什么。现在服务端
`webui_server.collect_events` 把每条归成五类（message / decision / tool /
error / debug），日志页只画前四类，调试行去控制台。

这个文件锁死三件事：
  1. 五类各自的典型行判对（含中文标签、带群号前缀的写法）
  2. 聊天记录靠 direction 判，不看正文（正文里可能带「失败」这类词）
  3. 归好的事件形状：前缀与标签被剥掉、工具名拆成数组，前端不必再解析一遍
"""
import os
import sys

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


def classify(**kw):
    return ws._classify_event(kw)


def main():
    print("[1] 五类的典型行")
    check("聊天记录（direction=in）→ message",
          classify(direction="in", text="今天去不去", sender="小明") == "message")
    check("聊天记录（direction=out）→ message",
          classify(direction="out", text="去啊") == "message")
    check("决策放行 → decision",
          classify(text="群[829854136] [决策放行] 无触发消息，决策插件建议插话: 今天去不去")
          == "decision")
    check("判定不接 → decision",
          classify(text="[判定不接] 本轮已连续回 3 条") == "decision")
    check("沉默 → decision", classify(text="[沉默] 冷却中") == "decision")
    check("工具调用 → tool",
          classify(text="[工具轮次] tools=['search_web','fetch_page']") == "tool")
    check("已执行工具 → tool", classify(text="已执行工具 2 个") == "tool")
    check("普通异常 → error", classify(text="工具 search_web 执行失败：连接超时") == "error")
    check("Traceback → error", classify(text="Traceback (most recent call last):") == "error")
    check("level=err 的任意行 → error",
          classify(text="协议层挂了", level="err") == "error")
    check("模块加载 → debug", classify(text="loaded module ego.personality_engine") == "debug")
    check("空正文 → debug", classify(text="") == "debug")
    check("非 dict → debug", ws._classify_event("随便一行") == "debug")

    print("\n[2] 聊天记录优先按 direction 判，不看正文")
    check("正文里带「失败」的聊天仍是 message",
          classify(direction="in", text="这次考试又失败了") == "message")
    check("正文里带「决策放行」的聊天仍是 message",
          classify(direction="out", text="他说决策放行了") == "message")

    print("\n[3] 归一化：前缀与标签剥掉，工具名拆成数组")
    msg = ws._norm_event({"direction": "in", "text": "今天去不去", "sender": "小明",
                          "type": "group", "target": "829854136", "time": "16:01:10"},
                         "message")
    check("message 带 dir", msg["dir"] == "in", msg)
    check("message 带 who", msg["who"] == "小明", msg)
    check("message 的 scope 写明群号", msg["scope"] == "群 829854136", msg)
    check("message 的正文保持原样", msg["text"] == "今天去不去", msg)

    dec = ws._norm_event(
        {"text": "群[829854136] [决策放行] 无触发消息，决策插件建议插话: 今天去不去",
         "time": "16:01:11"}, "decision")
    check("decision 的 tag 是「决策放行」", dec.get("tag") == "决策放行", dec)
    check("decision 的正文剥掉了群号前缀与标签",
          dec["text"] == "无触发消息，决策插件建议插话: 今天去不去", dec)

    tool = ws._norm_event(
        {"text": "[工具轮次] tools=['search_web', 'fetch_page']", "time": "16:01:13"},
        "tool")
    check("tool 拆出工具名数组",
          tool.get("tools") == ["search_web", "fetch_page"], tool)
    check("tool 的 tag 统一写「工具」", tool.get("tag") == "工具", tool)
    check("tool 的正文就是工具名（不重复长原句）",
          tool["text"] == "search_web、fetch_page", tool)

    private = ws._norm_event({"direction": "in", "text": "在吗", "sender": "(/ω＼)",
                              "type": "private", "target": "3150007406"}, "message")
    check("私聊的 scope 与群区分开", private["scope"].startswith("私聊"), private)

    print("\n[4] collect_events 的形状：调试行不进事件、计数照记")
    rows = [
        {"direction": "in", "text": "今天去不去", "sender": "小明",
         "type": "group", "target": "829854136", "time": "16:01:10"},
        {"text": "群[829854136] [决策放行] 建议插话: 今天去不去", "time": "16:01:11"},
        {"text": "[工具轮次] tools=['search_web']", "time": "16:01:12"},
        {"text": "加载模块 ego", "time": "16:01:00"},
        {"text": "加载模块 harness", "time": "16:01:00"},
    ]
    orig = ws._collect_logs
    ws._collect_logs = lambda limit=400: rows
    try:
        out = ws.collect_events(400)
    finally:
        ws._collect_logs = orig
    check("事件列表只留四类", len(out["events"]) == 3,
          [e["kind"] for e in out["events"]])
    check("counts 里 debug 记 2", out["counts"]["debug"] == 2, out["counts"])
    check("counts 里 message/decision/tool 各 1",
          (out["counts"]["message"], out["counts"]["decision"], out["counts"]["tool"])
          == (1, 1, 1), out["counts"])
    check("total 是原始行数（含调试）", out["total"] == 5, out["total"])
    check("事件里没有任何 debug",
          all(e["kind"] != "debug" for e in out["events"]))

    print("\n" + "=" * 56)
    print(f"通过 {PASS} / 失败 {FAIL}")
    if FAILURES:
        print("失败项：")
        for f in FAILURES:
            print("  -", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())