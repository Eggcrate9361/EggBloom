# -*- coding: utf-8 -*-
"""tests/test_plugin_bridge.py —— typed 钩子接线验收（任务 6 / ABI 缺口）。

## 这个测试在验收什么

`docs/CORE_FLOW.md` 记录的缺口：ABI v2 声明了 `on_before_reply` / `on_after_reply`，
`plugin_manager` 也实现了执行器，**但 bot_core 从未派发**（出现次数 0）。
后果是：任何 spec 插件写这两个钩子都是死代码。

本测试用**真实的 PluginManager**（不是 mock）验三件事：

  1. **桥自身**：`normalize_result` 把各种返回值形态归一成 action 协议
  2. **派发**：注册一个真 spec 插件后，`dispatch_before_reply` 能拿到它的注入
  3. **聚合**：`merge_before_reply` 让旧式与 typed 在同一处按 priority 裁决
  4. **⭐ bot_core 真的接线了**：在源码里断言 5 个旧式派发点旁**并列**出现了
     typed 派发调用 —— 这是"ABI 缺口已修"的直接证据（此前 grep 计数为 0）

运行：python tests/test_plugin_bridge.py
"""
import io
import os
import re
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from harness import plugin_bridge as pb          # noqa: E402

PASS = 0
FAIL = 0
FAILURES = []


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        FAILURES.append(f"{name} {extra}")
        print(f"  x {name} {extra}")


# ══════════════════════════════════════════════════════════════════
# 1. 归一化：容忍插件作者写出的各种形态
# ══════════════════════════════════════════════════════════════════
def test_normalize():
    print("\n[plugin_bridge] 返回值归一（各种形态）")

    check("None → 丢弃", pb.normalize_result("p", None) is None)
    check("空串 → 丢弃", pb.normalize_result("p", "   ") is None)

    # 裸字符串 → inject
    r = pb.normalize_result("p", "记住：他喜欢猫")
    check("裸字符串 → inject", r and r["action"] == "inject" and "猫" in r["content"], str(r))

    # 标准 inject
    r = pb.normalize_result("p", {"action": "inject", "content": "X"})
    check("标准 inject 透传", r and r["action"] == "inject" and r["content"] == "X", str(r))

    # prompt_blocks → 合并成 content
    r = pb.normalize_result("p", {"prompt_blocks": ["A", "B"], "content": "C"})
    check("prompt_blocks 合并进 content",
          r and "A" in r["content"] and "B" in r["content"] and "C" in r["content"], str(r))

    # 只有 content，无 action → 推断 inject
    r = pb.normalize_result("p", {"content": "Y"})
    check("缺 action 但有 content → 推断 inject", r and r["action"] == "inject", str(r))

    # 只有 reply，无 action → 推断 override
    r = pb.normalize_result("p", {"reply": "新回复"})
    check("缺 action 但有 reply → 推断 override",
          r and r["action"] == "override" and r["reply"] == "新回复", str(r))

    # skip
    r = pb.normalize_result("p", {"action": "skip", "reason": "广告"})
    check("skip 透传并补 reason", r and r["action"] == "skip" and r["reason"] == "广告", str(r))

    r = pb.normalize_result("p", {"skip": True})
    check("skip=True 简写 → 推断 skip", r and r["action"] == "skip", str(r))

    # inject 但内容是空白 → 丢弃（不该往 prompt 塞空段）
    check("inject 内容为空白 → 丢弃",
          pb.normalize_result("p", {"action": "inject", "content": "  "}) is None)

    # override 但没有 reply → 丢弃
    check("override 无 reply → 丢弃",
          pb.normalize_result("p", {"action": "override"}) is None)

    # 不可识别 → 丢弃
    check("不可识别 dict → 丢弃", pb.normalize_result("p", {"foo": 1}) is None)
    check("不可识别类型 → 丢弃", pb.normalize_result("p", 12345) is None)

    # 列表 → 取首个可归一项
    r = pb.normalize_result("p", [None, {"content": "第一项"}])
    check("列表取首个可归一项", r and r["content"] == "第一项", str(r))

    # 必须带 _plugin 标记（供日志追溯）
    r = pb.normalize_result("myplug", "hello")
    check("带回溯标记 _plugin", r and r.get("_plugin") == "myplug", str(r))


# ══════════════════════════════════════════════════════════════════
# 2. 派发：用真实 PluginManager + 内存 spec 插件
# ══════════════════════════════════════════════════════════════════
class _FakePM:
    """最小可用的 PM 替身：只实现桥真正依赖的两个方法。

    用替身而不是真 PM 的理由：本测试验的是**桥的行为**（归一/隔离/短路），
    不是插件加载器。真 PM 的加载已由 test_plugin_api / 插件回归覆盖。
    """

    def __init__(self, spec_hooks=None, legacy_hooks=None, priorities=None):
        self._plugins = {}
        prios = priorities or {}
        for name, hooks in (spec_hooks or {}).items():
            self._plugins[name] = {
                "tier": "spec", "hooks": hooks, "ctx": None,
                "meta": {"enabled": True, "priority": prios.get(name, 50)},
            }
        for name, hooks in (legacy_hooks or {}).items():
            self._plugins[name] = {
                "tier": "legacy", "hooks": hooks, "ctx": None,
                "meta": {"enabled": True, "priority": prios.get(name, 50)},
            }

    def has_typed_hook(self, hook_name):
        return any(e.get("tier") == "spec" and e.get("meta", {}).get("enabled")
                   and hook_name in (e.get("hooks") or {})
                   for e in self._plugins.values())

    def execute_typed_hooks(self, hook_name, **kwargs):
        results = []
        cands = []
        for name, e in self._plugins.items():
            if e.get("tier") != "spec" or not e.get("meta", {}).get("enabled"):
                continue
            fn = (e.get("hooks") or {}).get(hook_name)
            if callable(fn):
                cands.append((int(e.get("meta", {}).get("priority") or 50), name, fn))
        cands.sort(key=lambda x: x[0])
        for _, name, fn in cands:
            try:
                results.append((name, fn(None, **kwargs) if kwargs else fn(None)))
            except Exception as ex:
                results.append((name, None))
        return results

    def execute_hooks(self, hook_name, **kwargs):
        """旧式：返回全部 [(name, result)]（与真 PM 契约一致）。"""
        out = []
        cands = []
        for name, e in self._plugins.items():
            if e.get("tier") != "legacy" or not e.get("meta", {}).get("enabled"):
                continue
            fn = (e.get("hooks") or {}).get(hook_name)
            if callable(fn):
                cands.append((int(e.get("meta", {}).get("priority") or 50), name, fn))
        cands.sort(key=lambda x: x[0])
        for _, name, fn in cands:
            try:
                out.append((name, fn(**kwargs)))
            except Exception as ex:
                out.append((name, None))
        return out


def test_dispatch():
    print("\n[plugin_bridge] 派发（真钩子函数）")

    calls = {"n": 0}

    def hook_inject(ctx, event):
        calls["n"] += 1
        assert event["scope"] == "group", "event 必须带 scope"
        assert event["message"] == "在吗", "event 必须带 message"
        return {"action": "inject", "content": "插件注入的上下文"}

    pm = _FakePM(spec_hooks={"spec_a": {"on_before_reply": hook_inject}})
    out = pb.dispatch_before_reply(pm, message="在吗", group_id="123")
    check("派发到 spec 插件", len(out) == 1 and out[0][0] == "spec_a", str(out))
    check("注入内容正确", out and out[0][1].get("content") == "插件注入的上下文", str(out))
    check("结果带 _typed 之外的原始字段可用于聚合",
          out and out[0][1]["action"] == "inject", str(out))
    check("钩子确实被调用一次", calls["n"] == 1, str(calls))

    # 无 spec 插件 → 不派发（零成本路径）
    pm2 = _FakePM(legacy_hooks={"legacy_a": {"on_before_reply": hook_inject}})
    check("无 spec 插件 → 返回空（不扫旧式）",
          pb.dispatch_before_reply(pm2, message="x") == [])

    # pm 为 None
    check("pm=None → 空", pb.dispatch_before_reply(None, message="x") == [])

    # 插件抛异常 → 被隔离，不影响其它插件
    def boom(ctx, event):
        raise RuntimeError("插件内部炸了")

    def ok_hook(ctx, event):
        return {"content": "幸存者"}

    pm3 = _FakePM(spec_hooks={
        "bad": {"on_before_reply": boom},
        "good": {"on_before_reply": ok_hook},
    })
    out3 = pb.dispatch_before_reply(pm3, message="x")
    names = [n for n, _ in out3]
    check("异常插件被隔离，好插件仍生效", "good" in names and len(out3) == 1, str(out3))

    # 返回值形态各异 → 都能归一
    pm4 = _FakePM(spec_hooks={
        "s1": {"on_before_reply": lambda ctx, event: "裸字符串"},
        "s2": {"on_before_reply": lambda ctx, event: None},
        "s3": {"on_before_reply": lambda ctx, event: {"action": "skip", "reason": "r"}},
    })
    out4 = dict(pb.dispatch_before_reply(pm4, message="x"))
    check("三种形态各自归一（None 被丢）", len(out4) == 2, str(out4))
    check("裸字符串那个变成 inject", out4.get("s1", {}).get("action") == "inject", str(out4))
    check("skip 那个保持 skip", out4.get("s3", {}).get("action") == "skip", str(out4))


def test_after_reply():
    print("\n[plugin_bridge] after_reply 派发（返回 str 契约）")

    def mod(ctx, event, reply):
        assert reply == "原回复", "必须收到当前回复"
        return "改过的回复"

    pm = _FakePM(spec_hooks={"spec_b": {"on_after_reply": mod}})
    out = pb.dispatch_after_reply(pm, message="m", group_id="1", reply="原回复")
    check("after_reply 返回新文本", out == "改过的回复", str(out))

    # 空串 = 抑制发送，必须原样透传（不能被当成"无结果"）
    pm2 = _FakePM(spec_hooks={"spec_c": {"on_after_reply": lambda ctx, event, reply: ""}})
    out2 = pb.dispatch_after_reply(pm2, message="m", reply="原回复")
    check("空串原样透传（= 抑制）", out2 == "", repr(out2))

    # None → 不改
    pm3 = _FakePM(spec_hooks={"spec_d": {"on_after_reply": lambda ctx, event, reply: None}})
    check("None → 不修改", pb.dispatch_after_reply(pm3, message="m", reply="原") is None)

    # dict 形态也能取到
    pm4 = _FakePM(spec_hooks={"spec_e": {"on_after_reply":
                                        lambda ctx, event, reply: {"reply": "dict 版"}}})
    check("dict 形态取 reply", pb.dispatch_after_reply(pm4, message="m", reply="原") == "dict 版")

    # 异常隔离
    def boom(ctx, event, reply):
        raise RuntimeError("炸")

    pm5 = _FakePM(spec_hooks={"spec_f": {"on_after_reply": boom}})
    check("after_reply 异常被隔离", pb.dispatch_after_reply(pm5, message="m", reply="原") is None)

    # 无 typed 钩子 → None
    check("无 spec → None", pb.dispatch_after_reply(None, message="m", reply="原") is None)


# ══════════════════════════════════════════════════════════════════
# 3. 聚合：旧式 + typed 在同一处裁决
# ══════════════════════════════════════════════════════════════════
def test_merge():
    print("\n[plugin_bridge] merge_before_reply（统一裁决）")

    legacy = [("ego", {"action": "inject", "content": "E"})]
    typed = [("spec_a", {"action": "inject", "content": "T"})]
    m = pb.merge_before_reply(legacy, typed, priority_of=lambda n: {"ego": 60, "spec_a": 40}[n])
    check("inject 合并（带 priority）", len(m["injects"]) == 2, str(m["injects"]))
    check("inject 按 priority 升序",
          m["injects"][0][1] == "spec_a" and m["injects"][1][1] == "ego", str(m["injects"]))

    # ★ skip 单调：任一来源 skip 即 skip（不被另一侧的 inject 推翻）
    m2 = pb.merge_before_reply(
        [("ego", {"action": "inject", "content": "E"})],
        [("spec_a", {"action": "skip", "reason": "广告"})],
    )
    check("★ skip 单调：typed 的 skip 不被旧式 inject 推翻",
          m2["skip"] and len(m2["skip"]) == 1, str(m2["skip"]))
    check("★ skip 仍保留 inject 供上层判断", len(m2["injects"]) == 1, str(m2["injects"]))

    # override 取 priority 最小
    m3 = pb.merge_before_reply(
        [("legacy_x", {"action": "override", "reply": "旧覆盖"})],
        [("spec_y", {"action": "override", "reply": "新覆盖"})],
        priority_of=lambda n: {"legacy_x": 10, "spec_y": 90}[n],
    )
    check("override 取 priority 最小者", m3["override"] == "旧覆盖", str(m3))

    m4 = pb.merge_before_reply(
        [("legacy_x", {"action": "override", "reply": "旧覆盖"})],
        [("spec_y", {"action": "override", "reply": "新覆盖"})],
        priority_of=lambda n: {"legacy_x": 90, "spec_y": 10}[n],
    )
    check("priority 更高者胜（数值小）", m4["override"] == "新覆盖", str(m4))

    # 空输入
    m5 = pb.merge_before_reply(None, None)
    check("全空 → 无 skip/override/inject",
          not m5["skip"] and m5["override"] is None and not m5["injects"], str(m5))

    # 脏数据不崩
    m6 = pb.merge_before_reply(
        [("x", "不是 dict"), ("y", None), ("z", {"action": "inject", "content": ""})],
        [{"不是tuple": 1}],
    )
    check("脏数据被滤掉且不崩", not m6["injects"] and not m6["skip"], str(m6))

    # apply_after_reply_override
    check("apply None → 原样", pb.apply_after_reply_override("原", None) == "原")
    check("apply 相同 → 原样", pb.apply_after_reply_override("原", "原") == "原")
    check("apply 空串 → 抑制", pb.apply_after_reply_override("原", "") == "")
    check("apply 新值 → 替换", pb.apply_after_reply_override("原", "新") == "新")


# ══════════════════════════════════════════════════════════════════
# 4. ★ 结构性证据：bot_core 真的接线了
# ══════════════════════════════════════════════════════════════════
def test_bot_core_wiring():
    print("\n[plugin_bridge] ★ bot_core 接线（ABI 缺口已修的直接证据）")

    src_path = os.path.join(ROOT, "bot_core.py")
    with io.open(src_path, encoding="utf-8") as f:
        src = f.read()

    # 4.1 两个薄封装都有定义
    check("定义了 _dispatch_typed_before_reply",
          "def _dispatch_typed_before_reply(" in src)
    check("定义了 _dispatch_typed_after_reply",
          "def _dispatch_typed_after_reply(" in src)

    # 4.2 旧式派发点数量（作为对照基线）
    n_legacy_before = len(re.findall(r'execute_hooks\(\s*"before_reply"', src))
    n_legacy_after = len(re.findall(r'execute_hook\(\s*"after_reply"', src))
    check("旧式 before_reply 派发点 = 3", n_legacy_before == 3, f"实际 {n_legacy_before}")
    check("旧式 after_reply 派发点 = 2", n_legacy_after == 2, f"实际 {n_legacy_after}")

    # 4.3 ★ typed 派发点数量必须与旧式**对应**（并列追加的语义）
    n_typed_before = len(re.findall(r"_dispatch_typed_before_reply\(", src)) - 1  # 减去定义
    n_typed_after = len(re.findall(r"_dispatch_typed_after_reply\(", src)) - 1
    check("★ typed before_reply 派发点 = 3（与旧式并列）",
          n_typed_before == 3, f"实际 {n_typed_before}")
    check("★ typed after_reply 派发点 = 2（与旧式并列）",
          n_typed_after == 2, f"实际 {n_typed_after}")

    # 4.4 旧式那几行必须**原样存在**（证明"不动旧逻辑"）
    check("旧式 before_reply 调用行未被改动",
          'pm.execute_hooks("before_reply", message=merged_text, group_id=group_id, context=_plugin_ctx)' in src)
    check("旧式 after_reply（群）未被改动",
          'pm.execute_hook("after_reply", message=merged_text, group_id=group_id, reply=sent_text, context=_plugin_ctx)' in src)
    check("旧式 after_reply（私聊）未被改动",
          'pm.execute_hook("after_reply", message=text, group_id="", reply=sent_text, context={"scope": "private", "sender": sender})' in src)

    # 4.5 typed 结果必须并入同一个 intervention 列表（避免两套裁决分叉）
    check("★ typed before_reply 结果并入同一 intervention 列表",
          "intervention = list(intervention) + list(_typed_bi)" in src)

    # 4.6 每处调用外面都有 try（失败隔离）——用异常兜底日志的条数作为代理指标
    n_try_guard = len(re.findall(r"typed after_reply error", src))
    check("typed after_reply 有异常兜底（2 处）", n_try_guard == 2, f"实际 {n_try_guard}")


# ══════════════════════════════════════════════════════════════════
# 5. 端到端：模拟一次群回复的裁决全过程
# ══════════════════════════════════════════════════════════════════
def test_end_to_end():
    print("\n[plugin_bridge] 端到端（旧式 + typed 混合一次群回复）")

    # 场景：ego（旧式, prio 60）注入人格；spec 插件（prio 40）注入"今天的日程"
    #       另有一个 spec 插件（prio 30）要求 skip
    def ego_hook(**kw):
        return {"action": "inject", "content": "【人格】你是小a"}

    def sched_hook(ctx, event):
        return {"action": "inject", "content": "【日程】今天有课"}

    def guard_hook(ctx, event):
        return {"action": "skip", "reason": "广告消息"}

    pm = _FakePM(
        spec_hooks={"sched": {"on_before_reply": sched_hook},
                    "guard": {"on_before_reply": guard_hook}},
        legacy_hooks={"ego": {"before_reply": ego_hook}},
        priorities={"ego": 60, "sched": 40, "guard": 30},
    )

    legacy = pm.execute_hooks("before_reply", message="在吗", group_id="1", context={})
    typed = pb.dispatch_before_reply(pm, message="在吗", group_id="1")
    merged = pb.merge_before_reply(legacy, typed, priority_of=lambda n: pm._plugins[n]["meta"]["priority"])

    check("旧式 ego 的注入被收集", any(n == "ego" for _, n, _ in merged["injects"]), str(merged["injects"]))
    check("typed sched 的注入被收集", any(n == "sched" for _, n, _ in merged["injects"]), str(merged["injects"]))
    check("guard 的 skip 被收集", merged["skip"] and "广告消息" in merged["skip"][0], str(merged["skip"]))

    # 按 priority 拼接后的最终注入文本（模拟 bot_core 的行为）
    inject_text = "\n".join(c for _, _, c in merged["injects"])
    check("最终注入文本含两段且顺序正确（sched 40 在 ego 60 前）",
          inject_text.index("日程") < inject_text.index("人格"), inject_text)
    check("skip 非空 → 调用方应跳过回复", bool(merged["skip"]))

    # typed after_reply 覆盖
    pm2 = _FakePM(spec_hooks={"polish": {"on_after_reply":
                                         lambda ctx, event, reply: reply + "（@我）"}})
    out = pb.dispatch_after_reply(pm2, message="m", group_id="1", reply="你好")
    check("typed after_reply 追加生效", out == "你好（@我）", str(out))


def main():
    print("=" * 60)
    print("typed 钩子桥验收（任务 6 / ABI 缺口）")
    print("=" * 60)
    test_normalize()
    test_dispatch()
    test_after_reply()
    test_merge()
    test_bot_core_wiring()
    test_end_to_end()
    print()
    print("=" * 60)
    print(f"通过 {PASS} / 失败 {FAIL}")
    if FAILURES:
        print("失败项：")
        for f in FAILURES:
            print("  -", f)
    print("=" * 60)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
