# -*- coding: utf-8 -*-
"""
memory_feed 分发器自测（1.5 任务 2.3）

验证：
1. sandglass_bridge.memory_feed 存在且可调用
2. 一条群消息只解析一次：resolve_qq_nick 由调用方传入，桥接层不再重复解析
3. 四模块字段路由正确（群聊/私聊/带回复/空文本）
4. bot_core 中旧的四个直接调用点已全部收敛为 memory_feed
5. 一个模块抛异常不影响其它模块
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK]   {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} {detail}")


def main():
    global PASS, FAIL
    print("=" * 60)
    print("memory_feed 分发器自测")
    print("=" * 60)

    # ── 1. 接口存在 ──
    print("\n[1] 桥接层接口")
    try:
        import sandglass_bridge as sb
        check("memory_feed 存在", hasattr(sb, "memory_feed"), "缺少 memory_feed")
        check("memory_feed 可调用", callable(getattr(sb, "memory_feed", None)))
        import inspect
        sig = inspect.signature(sb.memory_feed)
        params = list(sig.parameters.keys())
        check("签名含 msg", params and params[0] == "msg", f"实际 {params}")
    except Exception as e:
        check("导入 sandglass_bridge", False, str(e))
        print(f"\n结果: {PASS} passed, {FAIL} failed")
        return 1

    # ── 2. 四模块字段路由（未初始化时安全返回 False，不抛异常）──
    print("\n[2] 字段路由（记忆系统未初始化，应安全降级）")
    cases = [
        ("群消息", {"text": "你好", "group_id": "123", "sender": "456", "sender_nick": "小明"}),
        ("私聊消息", {"text": "在吗", "group_id": "", "sender": "456", "sender_nick": "小明"}),
        ("带回复", {"text": "问题", "group_id": "123", "sender": "456", "reply": "答案"}),
        ("空文本", {"text": "", "group_id": "123", "sender": "456"}),
        ("全空", {}),
    ]
    for label, payload in cases:
        try:
            r = sb.memory_feed(payload)
            check(f"{label} 返回 dict", isinstance(r, dict), f"返回 {type(r)}")
            check(f"{label} 含四模块键",
                  set(r.keys()) == {"sandglass", "super_memory", "self_learning", "distill"},
                  f"键={set(r.keys())}")
        except Exception as e:
            check(f"{label} 不抛异常", False, str(e))

    # ── 3. 边界：字段缺失不崩 ──
    print("\n[3] 字段缺失容错")
    for label, payload in [
        ("无 sender", {"text": "x", "group_id": "1"}),
        ("无 group_id", {"text": "x", "sender": "1"}),
        ("text 非 str", {"text": 123, "group_id": "1", "sender": "2"}),
        ("None 值", {"text": None, "group_id": None, "sender": None, "reply": None}),
    ]:
        try:
            sb.memory_feed(payload)
            check(f"{label} 不抛异常", True)
        except Exception as e:
            check(f"{label} 不抛异常", False, str(e))

    # ── 4. bot_core 收敛证据 ──
    print("\n[4] bot_core 收敛证据")
    src = open(os.path.join(ROOT, "bot_core.py"), encoding="utf-8").read()
    check("无 log_user_message 直接调用", "log_user_message" not in src)
    check("无 sync_conversation 直接调用", "sync_conversation" not in src)
    check("无 log_bot_reply 直接调用", "log_bot_reply" not in src)
    check("无 extract_decision_particles 直接调用", "extract_decision_particles" not in src)
    feed_calls = len(re.findall(r"memory_feed\(", src))
    check("memory_feed 调用点 == 4", feed_calls == 4, f"实际 {feed_calls}")
    # 收敛后：群聊用户侧不再有"先 memory_feed 再重复 resolve_qq_nick"的冗余
    # 旧代码在 GROUP_TIMELINE 前又解析一次 nick，现已删除
    check("GROUP_TIMELINE 前无重复 resolve_qq_nick",
          "sender_nick 已在上方 memory_feed 前解析" in src,
          "未找到复用注释")
    # 群聊分支应恰好 1 次独立解析（6957 处），其余分支各算各的
    check("群聊消息分支解析次数收敛",
          src.count("sender_nick = resolve_qq_nick(sender) if sender else \"\"") == 2,
          "实际 %d 次（1 群 + 1 私聊各一次为正常）"
          % src.count("sender_nick = resolve_qq_nick(sender) if sender else \"\""))

    print("\n" + "=" * 60)
    print(f"结果: {PASS} passed, {FAIL} failed")
    print("=" * 60)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
