# -*- coding: utf-8 -*-
"""tests/test_source_kind.py —— 来源形状 SourceKind（2026-11-03）

执行总表 §7.2 第 4 条 + §8.1 #2 的验收原话：

> 新增一个"假协议"（只实现 send_text）→ 群里能用（表情/poke 自动降级），不改核心一行

所以本用例的核心是 [C]：用**只声明 `text` 的假适配器**跑一遍群聊，
发文字要成功、发表情/戳要**降级成"不发"并且一次协议调用都不发生**。

其余锁六件事：
  A. 形状三块齐全（scope / channel / threadContext），describe() 能直接 json 化
  B. 能力声明三种写法都认；**不声明 = 全能力**（现有四个驱动的兼容层）
  C. ★ 假协议：群里能说话，表情/poke 自动降级、零协议调用
  D. 降级策略分四种：skip / empty / fallback(@ 退化成文字) / fail
  E. 候选动作按顺序试（私聊戳：send_poke → friend_poke），抄的是真机那张实测表
  F. threadContext 的 chat_key 与 outbound 的 `g:` / `p:` 同一把钥匙
  G. 两个源各用各的适配器（别用模块级变量串源）
  H. 连文字都不会发的源，标记 usable=False、两个场景全关
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from harness import source_kind as sk  # noqa: E402

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


class FakeAdapter:
    """最小适配器：只有 name + api_call。可以声明能力、可以指定哪些动作会失败。"""

    name = "fake"

    def __init__(self, caps=None, fail=(), name="fake"):
        self.calls = []
        self.fail = set(fail)
        self.name = name
        if caps is not None:
            self.CAPABILITIES = set(caps)

    def api_call(self, endpoint, data=None, timeout=15):
        self.calls.append((endpoint, dict(data or {})))
        if endpoint in self.fail:
            return {"retcode": 100, "data": None}
        return {"retcode": 0, "data": {"endpoint": endpoint}}


class MethodCapsAdapter(FakeAdapter):
    """用方法声明能力的那种写法。"""

    def capabilities(self):
        return ["text", "image"]


class BareAdapter(FakeAdapter):
    """什么都不声明 —— 走兼容层（现有驱动就是这样）。"""


def main():
    print("[A] 形状：三块齐全")
    s = sk.build(FakeAdapter(), driver_id="fakeone")
    check("scope / channel / threadContext 都在",
          {"scope", "channel", "threadContext"} <= set(s), list(s))
    check("channel 里是 allowed / can / send / link",
          {"allowed", "can", "send", "link"} <= set(s["channel"]), list(s["channel"]))
    check("能发文字的源 usable=True", s["usable"] is True)
    d = sk.describe(s)
    check("describe 能直接 json 化", isinstance(json.dumps(d, ensure_ascii=False), str))
    check("describe 带 driver", d["driver"] == "fakeone", d)
    check("适配器为空要抛 ValueError",
          _raises(lambda: sk.build(None)))

    print("\n[B] 能力声明：三种写法 + 兼容层")
    a1 = FakeAdapter(caps=[sk.CAP_TEXT])
    check("CAPABILITIES 属性",
          sk.declared_capabilities(a1) == frozenset({sk.CAP_TEXT}),
          sk.declared_capabilities(a1))
    a2 = MethodCapsAdapter()
    check("capabilities() 方法",
          sk.declared_capabilities(a2) == frozenset({"text", "image"}),
          sk.declared_capabilities(a2))
    a3 = BareAdapter()
    check("★ 不声明 = 全能力（兼容层，现有驱动不改也能跑）",
          sk.declared_capabilities(a3) == sk.DEFAULT_CAPABILITIES)

    print("\n[C] ★ 假协议只实现 send_text：群里能用，表情/poke 自动降级")
    fake = FakeAdapter(caps=[sk.CAP_TEXT])
    src = sk.build(fake)
    r_text = src["channel"]["send"](sk.CAP_TEXT, "123", {"text": "在吗"}, scene="group")
    check("群里能发文字", r_text["ok"] is True and r_text["via"] == "send_group_msg", r_text)
    check("文字没被标记降级", r_text["degraded"] is False)
    n0 = len(fake.calls)
    r_img = sk.send(src, sk.CAP_IMAGE, "123", {"file": "a.gif"}, scene="group")
    check("★ 表情：降级为不发", r_img["ok"] is False and r_img["skipped"] is True, r_img)
    check("★ 表情：标了 degraded", r_img["degraded"] is True)
    check("★ 表情：一次协议调用都没发生", len(fake.calls) == n0, fake.calls)
    r_poke = sk.send(src, sk.CAP_POKE, "456", {"user_id": "456"}, scene="private")
    check("★ 戳一戳：同样降级为不发", r_poke["skipped"] is True and r_poke["degraded"] is True, r_poke)
    check("★ 戳一戳：也没调协议", len(fake.calls) == n0)
    check("降级要带人能看懂的原因", "不支持" in r_img["reason"], r_img["reason"])
    check("can() 对没声明的能力返回假", src["channel"]["can"](sk.CAP_IMAGE) is False)
    check("can() 对声明的能力返回真", src["channel"]["can"](sk.CAP_TEXT) is True)

    print("\n[D] 降级策略：skip / empty / fallback / fail")
    r_hist = sk.send(src, sk.CAP_HISTORY, "123", {"count": 5}, scene="group")
    check("拉历史 → 空集（不是报错）", r_hist["ok"] is True and r_hist["data"] == [], r_hist)
    src_at = sk.build(FakeAdapter(caps=[sk.CAP_TEXT]))
    r_at = sk.send(src_at, sk.CAP_AT, "123", {"text": "看这个"}, scene="group")
    check("★ @ 不支持 → 退化成文字发出去",
          r_at["ok"] is True and r_at["fallback_to"] == sk.CAP_TEXT, r_at)
    check("降级信息保留（cap 仍是 at）", r_at["cap"] == sk.CAP_AT and r_at["degraded"] is True)
    src_none = sk.build(FakeAdapter(caps=[]))
    r_fail = sk.send(src_none, sk.CAP_TEXT, "123", {"text": "hi"}, scene="private")
    check("连文字都不支持 → 直接失败", r_fail["ok"] is False and r_fail["degraded"] is True, r_fail)

    print("\n[E] 候选动作按顺序试（抄真机那张表）")
    ap = FakeAdapter(caps=list(sk.CAPABILITIES), fail={"send_poke"})
    sp = sk.build(ap)
    rp = sk.send(sp, sk.CAP_POKE, "456", {"user_id": "456"}, scene="private")
    check("★ 私聊戳：send_poke 失败后换 friend_poke", rp["ok"] is True and rp["via"] == "friend_poke", rp)
    check("确实是按顺序试的",
          [c[0] for c in ap.calls] == ["send_poke", "friend_poke"], [c[0] for c in ap.calls])
    ag = FakeAdapter(caps=list(sk.CAPABILITIES), fail={"send_poke"})
    sg = sk.build(ag)
    rg = sk.send(sg, sk.CAP_POKE, "123", {"user_id": "9"}, scene="group")
    check("群聊只有一个候选，失败就报失败", rg["ok"] is False and "send_poke" in rg["reason"], rg)

    print("\n[F] threadContext：与 outbound 同一把钥匙")
    # 合成号：真实的群号/QQ 号不进仓库（提交规范 §七）
    t = sk.thread_context(sk.SCENE_GROUP, group_id=1000000003, user_id=1, message_id="m1")
    check("群：chat_key = g:<群号>", t["chat_key"] == "g:1000000003", t["chat_key"])
    check("群：thread_id", t["thread_id"] == "group:1000000003", t["thread_id"])
    check("带 reply_to", t["reply_to"] == "m1")
    p = sk.thread_context(sk.SCENE_PRIVATE, user_id=1000000002)
    check("私聊：chat_key = p:<QQ>", p["chat_key"] == "p:1000000002", p["chat_key"])
    check("缺 group_id 要报错", sk.thread_context(sk.SCENE_GROUP)["ok"] is False)
    check("缺 user_id 要报错", sk.thread_context(sk.SCENE_PRIVATE)["ok"] is False)
    check("不认识的场景要报错", sk.thread_context("telepathy", user_id="1")["ok"] is False)

    print("\n[G] 两个源各用各的适配器")
    aA = FakeAdapter(caps=[sk.CAP_TEXT], name="A")
    aB = FakeAdapter(caps=[sk.CAP_TEXT], name="B")
    sA = sk.build(aA, driver_id="A")
    sB = sk.build(aB, driver_id="B")
    sA["channel"]["send"](sk.CAP_TEXT, "1", {"text": "x"}, scene="private")
    check("★ 建了第二个源之后，第一个还走自己的适配器",
          len(aA.calls) == 1 and len(aB.calls) == 0, (len(aA.calls), len(aB.calls)))

    print("\n[H] 不可用的源要标出来")
    sX = sk.build(FakeAdapter(caps=[sk.CAP_IMAGE]))
    check("★ 不会发文字 → usable=False", sX["usable"] is False)
    check("两个场景都关", sX["scope"]["group"] is False and sX["scope"]["private"] is False)
    sY = sk.build(FakeAdapter(caps=[sk.CAP_TEXT]))
    check("会发文字 → 两个场景都开",
          sY["scope"]["group"] is True and sY["scope"]["private"] is True)
    check("不认识的能力名要被挡下",
          sk.send(sY, "teleport", "1", {}).get("ok") is False)

    print("\n" + "=" * 60)
    print(f"通过 {PASS} / 失败 {FAIL}")
    if FAILURES:
        print("失败项：")
        for f in FAILURES:
            print("  -", f)
    return 0 if FAIL == 0 else 1


def _raises(fn):
    try:
        fn()
    except Exception:
        return True
    return False


if __name__ == "__main__":
    sys.exit(main())
