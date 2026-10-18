# -*- coding: utf-8 -*-
"""tests/test_decider_degrade.py —— 决策 AI 失败时的降级治理回归

用户 2026-10-12 报："决策的一个问题是 AI 请求失败导致氛围未知，跳过回复"。
此前五种失败统一走 `_ai_fallback_allow()`：规则判 skip 就**静默闭嘴**、氛围给"未知"，
于是"AI 偶发抽风"和"AI 判断这轮别说话"在日志里长得一模一样。

本测试锁死新的三层治理：
  ① 重试：偶发失败（超时/空/非 JSON）先自愈，且**不算**失败
  ② 单次失败 → fail-open：交主模型自裁（should_reply=True + 注入里写明"不适合就 [不说话]"），
     氛围沿用上次成功决策看到的（带"沿用上次"标记），不再是"未知"
  ③ 连续失败 ≥ 阈值 → 断路器打开：转保守规则（宁当哑巴不乱插嘴）

全程不发真实请求：_get_ai_client 被替换成脚本化的桩客户端。
"""
import os
import sys
import time
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import plugins.group_chat_decider as G  # noqa: E402
from plugins.group_chat_decider import decide  # noqa: E402

PASS = 0
FAIL = 0
FAILURES = []
GID = "_test_degrade_group_"


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK]   {name}")
    else:
        FAIL += 1
        FAILURES.append(f"{name} :: {extra}")
        print(f"  [FAIL] {name} :: {extra}")


class StubClient:
    """脚本化决策客户端：按次序返回预设结果。

    'timeout' → 睡 5s：外层 future.result(timeout=...) 会在 0.5s 判定超时
    （测试把 DECISION_TIMEOUT 调到 0.5s），且**不会**等于在途调用跑完
    —— 这正是 2026-10-12 修掉的"超时还被 with 块拖住"的回归点。
    """

    def __init__(self, script):
        self.script = list(script)
        self.calls = 0

    def call(self, messages, tools=None, stream=False, max_tokens=300):
        self.calls += 1
        item = self.script.pop(0) if self.script else ""
        if item == "timeout":
            time.sleep(5)
        return (item, None, None)


GOOD_JSON = ('{"reply": true, "reason": "在聊作业", '
             '"atmosphere": "闲聊", "atmosphere_confidence": 0.8, '
             '"strategy": "轻松接话", "strategy_detail": "顺着接"}')
SKIP_JSON = ('{"reply": false, "reason": "别人互聊", '
             '"atmosphere": "安静", "atmosphere_confidence": 0.9, '
             '"strategy": "不回复", "strategy_detail": "别插嘴"}')


def reset_state():
    G._AI_FAIL_TIMES.clear()
    G._LAST_ATMOSPHERE.clear()
    try:
        G._decision_cache.clear()
    except Exception:
        pass
    G._ai_client_cache.pop("client", None)


def install_client(script):
    c = StubClient(script)
    G._get_ai_client = lambda: c
    return c


def ctx(msg_sender):
    return {"recent_msgs": [(time.time() - 5, msg_sender, "在吗")],
            "bot_replies": [], "sender": msg_sender, "user_id": msg_sender,
            "history": [], "group_id": GID, "bot_qq": "10000", "bot_name": "小a"}


def run_decide(msg, sender="10003"):
    return decide(msg, GID, ctx(sender))


def main():
    orig_get = G._get_ai_client
    # 决策超时调小：让"超时"这条路径在测试里 0.5s 就能触发（生产是 30s）
    import plugins.group_chat_decider.config as _cfg
    _orig_timeout = _cfg.DECISION_TIMEOUT
    _cfg.DECISION_TIMEOUT = 0.5

    # ── ① 重试：第一次超时、第二次正常 → 应成功且**不记失败** ──
    print("\n=== ① 偶发失败先重试（不自愈就降级）===")
    reset_state()
    install_client(["timeout", GOOD_JSON])
    G._LAST_ATMOSPHERE.clear()
    t0 = time.time()
    d = run_decide("你们作业写完了吗")
    dt = time.time() - t0
    check("重试后拿到决策", d is not None and "闲聊" in (d.atmosphere or ""), str(getattr(d, "atmosphere", None)))
    check("重试未记为失败（断路器不误开）", len(G._AI_FAIL_TIMES) == 0, str(G._AI_FAIL_TIMES))
    check("★ 超时不再被在途调用拖住（< 3s，旧实现要等满 5s）", dt < 3.0, f"{dt:.1f}s")
    check("成功决策记住了氛围", GID in G._LAST_ATMOSPHERE, str(list(G._LAST_ATMOSPHERE.keys())))

    # ── ② 单次失败（两次都不行）→ fail-open：交主模型自裁 + 氛围沿用 ──
    print("\n=== ② 单次失败→交主模型自裁（不静默、氛围不丢）===")
    reset_state()
    G._LAST_ATMOSPHERE[GID] = ("闲聊", 0.8, time.time())   # 上次成功看到的氛围
    install_client(["timeout", "timeout"])
    d2 = run_decide("你们作业写完了吗")
    check("★ 不再静默（should_reply=True）", bool(d2.should_reply), f"should_reply={d2.should_reply}")
    check("★ 标记为交主模型自裁", "交主模型自裁" in d2.reason, d2.reason)
    check("★ 注入里写明'不适合就 [不说话]'", "[不说话]" in d2.strategy_detail, d2.strategy_detail)
    check("★ 氛围沿用上次（不再是'未知'）", "闲聊" in d2.atmosphere and "沿用上次" in d2.atmosphere, d2.atmosphere)
    check("沿用氛围置信度减半", abs(d2.atmosphere_confidence - 0.4) < 0.01, str(d2.atmosphere_confidence))
    check("失败被记一次", len(G._AI_FAIL_TIMES) == 1, str(len(G._AI_FAIL_TIMES)))

    # 无历史氛围时 → 老实给"未知"（不编造）
    reset_state()
    G._LAST_ATMOSPHERE.clear()
    install_client(["", ""])           # 返回空
    d3 = run_decide("你们作业写完了吗")
    check("没有历史氛围时给'未知'（不编造）", d3.atmosphere == "未知", d3.atmosphere)

    # ── ③ 断路器：连续失败达阈值 → 转保守 ──
    print("\n=== ③ 连续失败→断路器打开→转保守 ===")
    reset_state()
    for _ in range(3):
        install_client(["timeout", "timeout"])
        run_decide("你们作业写完了吗")
    check("失败计数达阈值", len(G._AI_FAIL_TIMES) >= 3, str(len(G._AI_FAIL_TIMES)))
    check("断路器已打开", G._breaker_open() is True, "")
    install_client(["timeout", "timeout"])
    d4 = run_decide("你们作业写完了吗")
    check("★ 转保守（不再交自裁）", "转保守" in d4.reason, d4.reason)
    check("保守时氛围置信度归零", d4.atmosphere_confidence == 0.0, str(d4.atmosphere_confidence))

    # ── ④ 客户端缺失 = 配置态 → 直接保守，且不污染失败统计 ──
    print("\n=== ④ 客户端缺失→直接保守（配置态，不计失败）===")
    reset_state()
    G._get_ai_client = lambda: None
    d5 = run_decide("你们作业写完了吗")
    check("★ 客户端缺失直接保守", "转保守" in d5.reason or "保守" in (d5.strategy or ""), f"{d5.reason} / {d5.strategy}")
    check("配置态不计入失败统计", len(G._AI_FAIL_TIMES) == 0, str(len(G._AI_FAIL_TIMES)))

    # ── ⑤ 规则判定"该跳过"时，单次失败仍不静默（这是用户报的主诉）──
    print("\n=== ⑤ 用户互聊 + AI 单次失败：不静默 ===")
    reset_state()
    install_client(["timeout", "timeout"])
    msgs = [(time.time() - 30, "10000005", "你昨天那个代码跑通没"),
            (time.time() - 10, "10000003", "还没 卡在依赖上")]
    c = {"recent_msgs": msgs, "bot_replies": [], "sender": "10000003", "user_id": "10000003",
         "history": [], "group_id": GID, "bot_qq": "10000", "bot_name": "小a"}
    d6 = decide("还没 卡在依赖上", GID, c)
    check("★ 别人互聊也不会因 AI 失败而静默", bool(d6.should_reply), f"should_reply={d6.should_reply} reason={d6.reason}")

    G._get_ai_client = orig_get
    _cfg.DECISION_TIMEOUT = _orig_timeout
    print("\n" + "=" * 56)
    print(f"通过 {PASS} / 失败 {FAIL}")
    if FAILURES:
        print("失败项：")
        for f in FAILURES:
            print("  -", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
