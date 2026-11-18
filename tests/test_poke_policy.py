# -*- coding: utf-8 -*-
"""戳一戳反射层：稳定动作、低把握升级和按人反馈。"""
import os
import ast
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import poke_policy
from harness import poke_state

PASS = []
FAIL = []
TMP = tempfile.mkdtemp(prefix="poke-policy-")
os.environ["EGGBLOOM_POKE_STATE"] = os.path.join(TMP, "state.json")


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name if not extra else f"{name}  {extra}")
    print(f"  [{'OK' if cond else 'FAIL'}] {name}" + (f"  {extra}" if extra else ""))


def decision(**updates):
    facts = {
        "scene": "group",
        "relationship": "ordinary",
        "mood": "neutral",
        "persona": "playful",
        "active_conversation": True,
        "serious_topic": False,
        "night": False,
        "poke_count": 1,
        "seconds_since_previous_poke": None,
        "last_action": "",
        "seconds_since_last_action": None,
        "feedback": {},
    }
    facts.update(updates)
    return poke_policy.decide(facts)


def test_reflex_actions():
    print("\n[1] 高把握直接动作")
    src = open(os.path.join(ROOT, "poke_policy.py"), encoding="utf-8").read()
    imports = []
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.append(node.module or "")
    check("反射层是纯模块，不 import bot_core",
          "bot_core" not in imports)
    d = decision()
    check("普通关系第一次 → skip", d["action"] == "skip" and d["confidence"] >= 0.9, d)
    d = decision(mood="positive", active_conversation=True)
    check("普通关系但气氛正热 → 偶尔交给模型", d["action"] == "model", d)

    d = decision(relationship="very_close")
    check("很熟、轻松 → poke", d["action"] == "poke", d)

    d = decision(relationship="close", poke_count=2,
                 seconds_since_previous_poke=30)
    check("熟人第二次 → meme", d["action"] == "meme", d)

    d = decision(poke_count=4)
    check("普通关系连续狂戳 → skip", d["action"] == "skip", d)

    d = decision(relationship="very_close", serious_topic=True)
    check("严肃话题覆盖亲密关系 → skip", d["action"] == "skip", d)

    d = decision(relationship="very_close", mood="negative")
    check("情绪低落不反戳", d["action"] != "poke", d)

    d = decision(relationship="very_close", last_action="poke",
                 seconds_since_last_action=10)
    check("刚反戳过一分钟内不再反戳", d["action"] != "poke", d)

    d = decision(persona="tired", relationship="close")
    check("疲惫人格不玩反戳", d["action"] == "skip", d)


def test_low_confidence_to_model():
    print("\n[2] 边界模糊交给主模型")
    d = decision(relationship="ordinary", poke_count=2,
                 active_conversation=False)
    check("普通关系第二次、上下文冷 → model", d["action"] == "model", d)
    check("模糊决策置信度 < 0.5", d["confidence"] < 0.5, d["confidence"])


def test_marker_parser():
    print("\n[3] 主模型动作标记")
    clean, wanted = poke_policy.parse_model_action("[POKE]")
    check("英文标记", clean == "" and wanted is True, (clean, wanted))
    clean, wanted = poke_policy.parse_model_action("懒得动 [戳一戳]")
    check("中文标记", clean == "懒得动" and wanted is True, (clean, wanted))


def test_state_store():
    print("\n[4] 按群 + 用户保存")
    g1 = poke_state.scope_key("group", "10001", "20001")
    g2 = poke_state.scope_key("group", "10001", "20002")
    p1 = poke_state.scope_key("private", "10001")
    check("不同群不共享", g1 != g2, (g1, g2))
    check("私聊独立", p1 != g1, (p1, g1))

    s1 = poke_state.begin_event(g1, now=1000.0, event_id="evt-1")
    s2 = poke_state.begin_event(g1, now=1000.2, event_id="evt-1")
    check("短时间重复事件被识别", s1["duplicate"] is False and s2["duplicate"] is True, (s1, s2))

    poke_state.record_action(g1, "poke", now=1010.0, reason="test")
    check("一分钟内禁止再次反戳",
          poke_state.can_poke(g1, now=1030.0) is False)
    check("一分钟后允许反戳",
          poke_state.can_poke(g1, now=1071.0) is True)

    poke_state.observe_message(g1, "哈哈哈哈继续", now=1011.0)
    snap = poke_state.snapshot(g1, now=1011.0)
    check("对方接梗后提高动作偏好",
          float(snap["feedback"].get("poke") or 0) > 0, snap)

    p1s = poke_state.begin_event(p1, now=1000.0)
    check("另一个 scope 不受影响", p1s["poke_count"] == 1, p1s)

    g3 = poke_state.scope_key("group", "30003", "40004")
    poke_state.record_action(g3, "meme", now=2000.0, emotion="嫌弃")
    for i in range(4):
        poke_state.observe_group_activity(
            "40004", "other-user", f"别的消息{i}", now=2001.0 + i)
    s3 = poke_state.snapshot(g3, now=2005.0)
    check("群内其他消息也推进反馈窗口",
          float(s3["feedback"].get("meme") or 0) == 0, s3)
    poke_state.observe_group_activity(
        "40004", "other-user", "第五条", now=2006.0)
    s3 = poke_state.snapshot(g3, now=2006.0)
    check("五条没人接 → 动作偏好轻微下调",
          float(s3["feedback"].get("meme") or 0) < 0, s3)

    g4 = poke_state.scope_key("group", "50005", "60006")
    poke_state.record_action(g4, "speak", now=3000.0)
    poke_state.observe_group_activity(
        "60006", "50005", "别戳了，烦", now=3001.0)
    s4 = poke_state.snapshot(g4, now=3001.0)
    check("目标明确反感 → 永久降低该动作偏好",
          float(s4["feedback"].get("speak") or 0) < 0, s4)


def main():
    test_reflex_actions()
    test_low_confidence_to_model()
    test_marker_parser()
    test_state_store()
    print(f"\n通过 {len(PASS)} / 失败 {len(FAIL)}")
    for x in FAIL:
        print("  FAIL", x)
    return 1 if FAIL else 0


if __name__ == "__main__":
    try:
        code = main()
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(code)
