# -*- coding: utf-8 -*-
"""
沙漏落沙去重回归测试（2026-10-10 记忆线·污染源头修复）

实证背景（sandglass.txt 4186–4190，21:27）：同一条群消息被记 3 遍、
Bot 回复被记 2 遍（agent + assistant），来源是回复侧 memory_feed 把
merged_text 再落一遍 + sync_conversation 又落裸文本 + 双写 Bot 行。

验证：
1. 入口 feed（无 user_pre_logged）→ 用户行恰好 1 条，格式 [群:x] [nick(uid)]
2. 回复侧 feed（user_pre_logged=True）→ 不再落用户行，只落 Bot 行 1 条（role=agent）
3. 完整一轮（入口 + 回复侧）→ user×1 + agent×1，assistant/[Bot回复] 绝迹
4. 保险丝：同一整行 2s 内双写 → 拦；同秒不同人/不同句 → 不误杀
5. 私聊路径同 3（入口带 sender、回复侧带 flag）
6. memory_feed 源码不再调用 sync_conversation（静态检查）
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

# ★ 沙漏路径隔离（2026-10-19）：打桩失效时会真写盘 → 污染生产记忆
from _sandbox import sandbox_neurobase  # noqa: E402
sandbox_neurobase()

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
    print("沙漏落沙去重回归测试")
    print("=" * 60)

    # ── 打桩：拦截真正写文件的 log_message，只记录调用 ──
    # 2026-10-19：桥的实现已切到自研 sandglass_lite（薄适配），
    # 故打桩目标从 sandglass_log 改为 sandglass_lite。
    import sandglass_bridge as sb  # noqa: E402
    import sandglass_lite  # noqa: E402

    written = []  # (sender, text)
    sandglass_lite.log_message = lambda text, sender="agent": (
        written.append((sender, text)) or True)

    # 打开桥的闸门（绕过 provider 初始化）
    sb._enabled = True
    sb._initialized = True
    sb._provider = object()
    sb.is_available = lambda: True
    # 重置保险丝状态
    sb._LAST_USER_LOG = ("", 0.0)

    # ── 1. 入口 feed ──
    print("\n[1] 入口 feed（用户行恰好 1 条）")
    written.clear()
    sb.memory_feed({"text": "你好呀", "group_id": "123",
                    "sender": "456", "sender_nick": "小明"})
    users = [t for s, t in written if s == "user"]
    check("用户行 1 条", len(users) == 1, f"实际 {len(users)}: {users}")
    check("格式 [群:123] [小明(456)]",
          users and users[0] == "[群:123] [小明(456)] 你好呀", f"实际 {users}")

    # ── 2. 回复侧 feed（flag）──
    print("\n[2] 回复侧 feed（user_pre_logged=True）")
    written.clear()
    sb.memory_feed({"text": "[小明] 你好呀", "group_id": "123",
                    "reply": "早上好", "user_pre_logged": True})
    check("不落用户行", not any(s == "user" for s, t in written), f"实际 {written}")
    bots = [t for s, t in written if s == "agent"]
    check("Bot 行恰好 1 条", len(bots) == 1, f"实际 {bots}")
    check("Bot 行无 assistant 重复",
          not any(s == "assistant" for s, t in written), f"实际 {written}")
    # ★ 2026-10-22 修正：本断言原为 `bots[0] == "早上好"`，是 2026-10-10
    #   「格式收敛」时期的写法。2026-10-19 用户明确要求「Bot 要能知道我在哪个群
    #   说过什么」，agent 行因此**加回了 `[群:{gid}]` 归属标记** —— 否则
    #   `_scope_match("group", gid)` 会把 Bot 自己的历史发言全部排除，
    #   连本群都看不到自己说过什么。
    #   所以现在正确的期望值是「带群标记、但不带旧的 [Bot回复] 前缀」：
    #   群标记是归属信息（要留），[Bot回复] 是旧格式噪声（要去）。
    check("Bot 行带群归属标记 [群:123]",
          bots and bots[0] == "[群:123] 早上好", f"实际 {bots}")
    check("Bot 行无旧格式 [Bot回复] 前缀",
          bots and "[Bot回复]" not in bots[0], f"实际 {bots}")
    r = sb.memory_feed({"text": "x", "group_id": "", "reply": "y",
                        "user_pre_logged": True})
    check("返回值仍含四模块键",
          set(r.keys()) == {"sandglass", "super_memory", "self_learning", "distill"})

    # ── 3. 完整一轮 ──
    print("\n[3] 完整一轮 = 入口 + 回复侧（治 21:27 三连）")
    written.clear()
    sb._LAST_USER_LOG = ("", 0.0)
    sb.memory_feed({"text": "你能评价一下铁路学院吗", "group_id": "20001",
                    "sender": "10003", "sender_nick": "小明"})
    sb.memory_feed({"text": "[小明] 你能评价一下铁路学院吗", "group_id": "20001",
                    "reply": "还行吧", "user_pre_logged": True})
    check("一轮总落沙 2 行（user1+agent1）", len(written) == 2, f"实际 {written}")
    check("角色序列 user→agent",
          [s for s, _ in written] == ["user", "agent"], f"实际 {written}")

    # ── 4. 保险丝 ──
    print("\n[4] 保险丝（兜底双写，不误杀不同行）")
    written.clear()
    sb._LAST_USER_LOG = ("", 0.0)
    sb.log_user_message("同一句", group_id="1", sender="9", sender_nick="甲")
    sb.log_user_message("同一句", group_id="1", sender="9", sender_nick="甲")
    check("同整行 2s 内双写 → 只记 1 次",
          len([t for s, t in written if s == "user"]) == 1, f"实际 {written}")
    written.clear()
    sb._LAST_USER_LOG = ("", 0.0)
    sb.log_user_message("同一句", group_id="1", sender="9", sender_nick="甲")
    sb.log_user_message("同一句", group_id="1", sender="8", sender_nick="乙")
    check("同秒不同人同句 → 不误杀（2 条都记）",
          len(written) == 2, f"实际 {written}")
    written.clear()
    sb._LAST_USER_LOG = ("", 0.0)
    sb.log_user_message("句子A", group_id="1", sender="9", sender_nick="甲")
    sb.log_user_message("句子B", group_id="1", sender="9", sender_nick="甲")
    check("同人不同句 → 不误杀", len(written) == 2, f"实际 {written}")

    # ── 5. 私聊一轮 ──
    print("\n[5] 私聊：入口 + 回复侧同样 user1+agent1")
    written.clear()
    sb._LAST_USER_LOG = ("", 0.0)
    sb.memory_feed({"text": "在吗", "group_id": "", "sender": "456",
                    "sender_nick": "小明"})
    sb.memory_feed({"text": "在吗", "group_id": "", "sender": "456",
                    "reply": "在的", "user_pre_logged": True})
    check("私聊一轮 2 行", [s for s, _ in written] == ["user", "agent"],
          f"实际 {written}")

    # ── 6. 静态：memory_feed 不再调 sync_conversation ──
    print("\n[6] 静态检查")
    src = open(os.path.join(ROOT, "sandglass_bridge.py"), encoding="utf-8").read()
    # 只看 memory_feed 函数体
    mf = src.split("def memory_feed", 1)[1].split("def get_memory_prompt_block", 1)[0]
    check("memory_feed 体内无 sync_conversation 调用", "sync_conversation(" not in mf,
          mf)
    check("无 assistant 落沙", 'log_message(prefix + text, "assistant")' not in src)

    print("\n" + "=" * 60)
    print(f"结果: {PASS} passed, {FAIL} failed")
    print("=" * 60)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
