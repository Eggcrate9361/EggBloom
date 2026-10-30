# -*- coding: utf-8 -*-
"""自主性插件的策略层单测（plugins/autonomy/policy.py，2026-10-30）。

钉死四件事：
  1. 未登记动作按 L3 拒绝（漏登记不会变成"悄悄放开"）
  2. 等级不足 → deny；L3 未确认 → need_confirm；确认后才 allow
  3. 提示词里的等级与 guard 同源（改一个数两边都变）
  4. 模型输出解析失败一律 none，绝不猜

运行：python tests/test_autonomy_policy.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "plugins", "autonomy"))

import policy as P  # noqa: E402

PASS = []
FAIL = []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name if not extra else f"{name}  {extra}")


# ── 1. 等级表 ───────────────────────────────────────────────────
check("L0 动作就是 L0", P.level_of("fs.read") == 0, P.level_of("fs.read"))
check("L2 动作就是 L2", P.level_of("msg.send") == 2)
check("L3 动作就是 L3", P.level_of("publish.video") == 3)
check("未登记动作按 L3", P.level_of("rm_rf") == 3, P.level_of("rm_rf"))
check("空动作按 L3", P.level_of("") == 3)

# ── 2. judge ────────────────────────────────────────────────────
r = P.guard("msg.send", 2)
check("同级放行", r["verdict"] == "allow", r)
r = P.guard("publish.video", 2)
check("越级拒绝", r["verdict"] == "deny", r)
check("拒绝理由带等级", "L3" in r["reason"] and "L2" in r["reason"], r["reason"])
r = P.guard("publish.video", 3)
check("L3 未确认 → 挂起", r["verdict"] == "need_confirm", r)
r = P.guard("publish.video", 3, confirmed=True)
check("L3 已确认 → 放行", r["verdict"] == "allow", r)
r = P.guard("rm_rf", 3, confirmed=True)
check("未登记动作即使确认也拒", r["verdict"] == "deny", r)
r = P.guard("", 3)
check("空动作拒", r["verdict"] == "deny")
r = P.guard("fs.read", 0)
check("L0 能读", r["verdict"] == "allow")

# ── 3. 提示词与 guard 同源 ─────────────────────────────────────
p2 = P.permission_prompt(2)
check("提示词标了 L2", "L2" in p2, p2)
check("提示词列了能做的", "msg.send" in p2, p2)
check("提示词列了不能做的", "publish.video" in p2, p2)
check("提示词说越界会被拒", "拒绝" in p2, p2)
p0 = P.permission_prompt(0)
check("L0 提示词不含 L2 动作", "msg.send" not in p0.split("不能做")[0], p0)
# 同源判据：提示词里"能做"的动作，guard 必须 allow
for act in P.LEVEL_ACTIONS[2]:
    if P.level_of(act) <= 2 and P.level_of(act) < 3:
        check(f"提示词放行集合与 guard 一致: {act}",
              P.guard(act, 2)["verdict"] == "allow", P.guard(act, 2))

# ── 4. 心跳提示词 ──────────────────────────────────────────────
hb = P.heartbeat_prompt({"now": "14:30（周二）", "silent_for": "3 小时 12 分",
                         "last_line": "今天好累", "pending": "问他体检结果"}, 2)
check("心跳带时间", "14:30" in hb, hb)
check("心跳带沉默时长", "3 小时 12 分" in hb, hb)
check("心跳带最近一句", "今天好累" in hb, hb)
check("心跳带惦记的事", "体检" in hb, hb)
check("心跳要求默认不动", "默认不动" in hb, hb)
check("心跳限一个动作", "最多 1 个动作" in hb, hb)
check("心跳标了等级", "L2" in hb, hb)

# ── 5. 决策解析 ────────────────────────────────────────────────
d = P.parse_decision('{"act":"none"}')
check("none 解析", d["act"] == "none" and not d["error"], d)
d = P.parse_decision('好的 {"act":"say","text":"在吗","why":"想聊"}')
check("带前缀也能解析", d["act"] == "say" and d["text"] == "在吗", d)
d = P.parse_decision('{"act":"do","action":"memory.search","args":{"q":"体检"}}')
check("do 解析", d["act"] == "do" and d["action"] == "memory.search"
      and d["args"].get("q") == "体检", d)
d = P.parse_decision("我觉得今天不用说话")
check("没有 JSON → none", d["act"] == "none" and "JSON" in d["error"], d)
d = P.parse_decision('{"act":"say"}')
check("say 缺 text → none", d["act"] == "none" and "text" in d["error"], d)
d = P.parse_decision('{"act":"hack"}')
check("未知 act → none", d["act"] == "none" and "act" in d["error"], d)
d = P.parse_decision('{"act":"do","action":"","args":{}}')
check("do 缺 action → none", d["act"] == "none" and "action" in d["error"], d)
d = P.parse_decision('{"act":"do","action":"fs.read","args":{}}', max_actions=0)
check("预算为 0 → 不动作", d["act"] == "none" and d["error"], d)
d = P.parse_decision('{"act":"do","action":"fs.read","args":"not-a-dict"}')
check("args 非 dict → 空 dict", d["act"] == "do" and d["args"] == {}, d)

print(f"通过 {len(PASS)} / 失败 {len(FAIL)}")
for n in FAIL:
    print(f"  FAIL  {n}")
sys.exit(1 if FAIL else 0)
