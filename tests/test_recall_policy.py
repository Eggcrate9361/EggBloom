# -*- coding: utf-8 -*-
"""tests/test_recall_policy.py —— 撤回反馈判定链（2026-11-03，刀 1）

执行总表 §4.1.2 要求"整块迁进插件，判定链与现在**逐条对齐**"。
所以本用例除了枚举规则，还做两件对齐的事：

  · [B] 把 `should_fire` 与 `bot_core._recall_should_fire` 在 12 组输入上**逐条比对**
    —— 不是照着注释抄期望值，是直接问现网那份实现；
  · [F] 冷却键形状与主链的 `group_<gid>` / `private_<uid>` 完全一致（否则迁移后
    冷却表对不上，会出现"迁移当刻集体冷却失效"）。

其余锁：群/私聊两条判定链的顺序、去重、文案池与状态写回。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from plugins.recall_manager import policy as P  # noqa: E402

PASS = 0
FAIL = 0
FAILURES = []

NOW = 1_700_000_000.0
GID = "1000000003"
UID = "1000000001"


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK]   {name}")
    else:
        FAIL += 1
        FAILURES.append(name + (f"  → {extra}" if extra else ""))
        print(f"  [FAIL] {name}  {extra}")


def grp(**kw):
    ev = {"notice_type": "group_recall", "group_id": GID, "operator": UID,
          "message_id": "m1", "buffer_cancelled": False, "last_bot_msg": NOW - 30,
          "user_recent": True}
    ev.update(kw)
    return ev


def pri(**kw):
    ev = {"notice_type": "friend_recall", "group_id": "", "operator": UID,
          "message_id": "p1", "session_active": True, "idle_seconds": 10}
    ev.update(kw)
    return ev


def main():
    print("[A] 群聊判定链（顺序即优先级）")
    d = P.decide(grp(), cfg={}, now=NOW)
    check("默认放行", d["act"] == P.ACT_SAY, d)
    check("kind = 已读反馈", d["kind"] == P.KIND_SEEN, d["kind"])
    check("文案池非空", bool(d["texts"]), d["texts"])

    d = P.decide(grp(buffer_cancelled=True), cfg={}, now=NOW)
    check("★ 截断场景 → 截断反馈", d["kind"] == P.KIND_TRUNCATED, d["kind"])

    # 主链还有一道门槛：真的看到过撤回者最近的消息才接话（截断场景不需要）
    d = P.decide(grp(user_recent=False), cfg={}, now=NOW)
    check("★ 没看到用户最近的消息 → 不接话", d["act"] == P.ACT_SKIP and "没看到" in d["reason"], d)
    check("截断场景不受这条限制",
          P.decide(grp(buffer_cancelled=True, user_recent=False), cfg={}, now=NOW)["act"] == P.ACT_SAY)

    check("① 开关关掉就不回",
          P.decide(grp(), cfg={"recall_feedback_enable": False}, now=NOW)["act"] == P.ACT_SKIP)
    st = {"last_fire": {P.fire_key(P.SCENE_GROUP, GID): NOW - 10}}
    d = P.decide(grp(), cfg={}, now=NOW, state=st)
    check("② 冷却中不回", d["act"] == P.ACT_SKIP and "冷却" in d["reason"], d)
    # 开关优先于冷却：两个都拦时，报的应该是开关
    d = P.decide(grp(), cfg={"recall_feedback_enable": False}, now=NOW, state=st)
    check("★ 开关在冷却之前判", "已关闭" in d["reason"], d["reason"])

    st2 = {"seen": {f"{P.fire_key(P.SCENE_GROUP, GID)}:m1": NOW - 9999}}
    d = P.decide(grp(), cfg={}, now=NOW, state=st2)
    check("③ 同一条撤回不重复反馈", d["act"] == P.ACT_SKIP and "反馈过" in d["reason"], d)
    # 不同的 message_id 不受影响
    check("换个 message_id 仍可回",
          P.decide(grp(message_id="m2"), cfg={}, now=NOW, state=st2)["act"] == P.ACT_SAY)

    d = P.decide(grp(buffer_cancelled=False, last_bot_msg=0), cfg={}, now=NOW)
    check("④ 与 bot 无关就不回", d["act"] == P.ACT_SKIP and "无关" in d["reason"], d)
    check("★ 无关判定在文案之前（texts 为空）", d["texts"] == [], d["texts"])

    check("⑤ 自定义窗口生效",
          P.decide(grp(last_bot_msg=NOW - 50),
                   cfg={"recall_feedback_context_window": 30}, now=NOW)["act"] == P.ACT_SKIP)

    print("\n[B] should_fire 与主链实现逐条对齐")
    try:
        from _sandbox import sandbox_neurobase
        sandbox_neurobase()
        import bot_core
        cases = [
            (True, 0, 300), (True, NOW - 9999, 300),
            (False, 0, 300), (False, NOW - 5, 300), (False, NOW - 300, 300),
            (False, NOW - 301, 300), (False, NOW - 30, 30), (False, NOW - 30, 1),
            (False, NOW - 9, 3), (False, NOW - 11, 3),
            (False, NOW - 1, 0), (False, NOW - 100, 600),
        ]
        bad = []
        for buf, last, win in cases:
            a = P.should_fire(buffer_cancelled=buf, last_bot_msg=last, now=NOW, window=win)
            b = bot_core._recall_should_fire(buffer_cancelled=buf, last_bot_msg=last,
                                             now=NOW, window=win)
            if a != b:
                bad.append((buf, last, win, a, b))
        check(f"★ {len(cases)} 组输入与 bot_core._recall_should_fire 完全一致", not bad, bad)
    except Exception as e:
        check("对齐测试跑通", False, f"{type(e).__name__}: {e}")

    print("\n[C] 私聊判定链")
    d = P.decide(pri(), cfg={}, now=NOW)
    check("默认放行", d["act"] == P.ACT_SAY and d["kind"] == P.KIND_PRIVATE, d)
    check("① 无活跃会话不回",
          "会话" in P.decide(pri(session_active=False), cfg={}, now=NOW)["reason"])
    check("② 会话超时（默认 1800s）不回",
          "超时" in P.decide(pri(idle_seconds=2000), cfg={}, now=NOW)["reason"])
    check("空闲阈值可配",
          P.decide(pri(idle_seconds=2000, idle_timeout=3000), cfg={}, now=NOW)["act"] == P.ACT_SAY)
    check("③ 私聊开关独立",
          P.decide(pri(), cfg={"recall_private_enable": False}, now=NOW)["act"] == P.ACT_SKIP)
    check("  私聊开关不受群聊开关影响",
          P.decide(pri(), cfg={"recall_feedback_enable": False}, now=NOW)["act"] == P.ACT_SAY)
    stp = {"last_fire": {P.fire_key(P.SCENE_PRIVATE, UID): NOW - 10}}
    d = P.decide(pri(), cfg={}, now=NOW, state=stp)
    check("④ 私聊冷却独立", d["act"] == P.ACT_SKIP and "冷却" in d["reason"], d)
    check("撤回人无效 → 不回",
          P.decide(pri(operator="None"), cfg={}, now=NOW)["act"] == P.ACT_SKIP)

    print("\n[D] 文案池")
    check("自定义优先",
          P.texts_for(P.KIND_SEEN, {"recall_feedback_post_messages": ["自定义一句"]}) == ["自定义一句"])
    check("空配置用预置", P.texts_for(P.KIND_SEEN, {}) == list(P.PRESET_SEEN))
    check("空白项被过滤",
          P.texts_for(P.KIND_SEEN, {"recall_feedback_post_messages": [" ", "", "x"]}) == ["x"])
    check("未知类别返回空", P.texts_for("nope", {}) == [])
    check("pick 可注入 rng", P.pick(["a", "b"], rng=lambda pool: pool[1]) == "b")
    check("空池 pick 返回空串", P.pick([]) == "")

    print("\n[E] remember：写冷却 + 去重，且不改入参")
    st0 = {}
    st1 = P.remember(st0, "group_1", now=NOW, message_id="m1")
    check("last_fire 写进去了", st1["last_fire"]["group_1"] == NOW, st1)
    check("seen 写进去了", "group_1:m1" in st1["seen"], st1)
    check("★ 不改入参", st0 == {}, st0)
    for i in range(300):
        st1 = P.remember(st1, "group_1", now=NOW + i, message_id=f"x{i}", keep_seen=200)
    check("★ seen 有界（不会无限长）", len(st1["seen"]) <= 200, len(st1["seen"]))

    print("\n[F] 冷却键与主链同形")
    check("群: group_<群号>", P.fire_key(P.SCENE_GROUP, GID) == f"group_{GID}")
    check("私聊: private_<QQ>", P.fire_key(P.SCENE_PRIVATE, UID) == f"private_{UID}")

    print("\n[G] 非撤回事件一律不接")
    check("message.in 不处理", P.decide({"notice_type": "message.in"}, cfg={}, now=NOW)["act"] == P.ACT_SKIP)
    check("空事件不处理", P.decide({}, cfg={}, now=NOW)["act"] == P.ACT_SKIP)
    check("空事件给原因", bool(P.decide({}, cfg={}, now=NOW)["reason"]))

    print("\n" + "=" * 60)
    print(f"通过 {PASS} / 失败 {FAIL}")
    if FAILURES:
        print("失败项：")
        for f in FAILURES:
            print("  -", f)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
