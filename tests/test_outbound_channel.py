# -*- coding: utf-8 -*-
"""tests/test_outbound_channel.py —— outbound.set_channel() 的默认关闭与优先级（2026-11-08）

`harness/outbound.py` 只加了一个可选入口 `set_channel()`，本用例锁四件事：

  · 默认关闭：不调 set_channel 时，发送逐字节走原有 `_SENDER`；
  · 设置后：Channel.send 成为 sender 来源，legacy `_SENDER` 不被调用；
  · 取消 / 传坏对象：回到 `_SENDER`，且 `_SENDER=None` + 有 Channel 仍能发；
  · 五个既有函数 `send/send_text/send_segment/submit/set_sender` 的**签名没改**。

全离线：假 Channel + 假 sender，outbox 落盘库用临时目录隔离。

运行：python tests/test_outbound_channel.py
"""
import inspect
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

# 落盘库隔离：必须在 import harness.outbound 之前设
os.environ.setdefault("EGGBLOOM_OUTBOX_DB",
                      os.path.join(tempfile.mkdtemp(prefix="eggobch_"), "outbox.sqlite3"))

from harness import outbound as ob   # noqa: E402
from harness import channel as ch    # noqa: E402

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name if not extra else f"{name}  → {extra}")


class FakeChannel(ch.Channel):
    def __init__(self):
        self.sent = []

    @property
    def id(self):
        return "fake"

    def capabilities(self):
        return frozenset(("text", "image"))

    def send(self, kind, target, payload=None, is_group=False):
        self.sent.append((kind, str(target), bool(is_group)))
        return {"retcode": 0, "ok": True, "via": "fake"}

    def receive(self, limit=1):
        return []

    def health(self):
        return {"ok": True, "id": self.id, "sent": len(self.sent)}


def reset():
    ob.reset_stats()
    ob.set_config(lambda: {})
    ob.set_logger(lambda m: None)
    ob._LAST_SENT.clear()
    ob._RECENT.clear()
    ob.set_channel(None)
    ob.set_sender(None)


def _params(fn):
    return list(inspect.signature(fn).parameters)


def main():
    legacy = []
    fake = FakeChannel()

    def _legacy(kind, target, payload, is_group):
        legacy.append((kind, str(target), bool(is_group)))
        return {"retcode": 0}

    try:
        # ── [A] 既有签名没改 ────────────────────────────────────────
        print("[A] 五个既有函数签名没改")
        check("send 签名未变",
              _params(ob.send) == ["kind", "target", "payload", "is_group",
                                  "suffix", "timeout"], _params(ob.send))
        check("send_text 签名未变",
              _params(ob.send_text) == ["text", "target", "is_group",
                                       "suffix", "timeout"], _params(ob.send_text))
        check("send_segment 签名未变",
              _params(ob.send_segment) == ["segments", "target", "is_group",
                                          "kind", "suffix", "timeout"],
              _params(ob.send_segment))
        check("submit 签名未变",
              _params(ob.submit) == ["kind", "target", "payload",
                                    "is_group", "suffix"], _params(ob.submit))
        check("set_sender 签名未变", _params(ob.set_sender) == ["fn"],
              _params(ob.set_sender))
        check("set_channel 已进 __all__", "set_channel" in ob.__all__)

        # ── [B] 默认关闭：走原有 _SENDER ────────────────────────────
        print("\n[B] 默认关闭（不调 set_channel）")
        reset()
        check("★ 默认 _CHANNEL 是 None（关闭）", ob._CHANNEL is None, ob._CHANNEL)
        ob.set_sender(_legacy)
        legacy.clear()
        ok = ob.send_text("默认这条", "10001")
        check("默认发送成功", ok is True, ok)
        check("★ 默认走 _SENDER", legacy == [("text", "10001", False)], legacy)
        check("默认没碰 Channel", fake.sent == [], fake.sent)

        # ── [C] 设置 Channel：成为 sender 来源 ──────────────────────
        print("\n[C] set_channel(fake) → Channel 优先")
        reset()
        ob.set_sender(_legacy)
        ob.set_channel(fake)
        legacy.clear()
        fake.sent.clear()
        check("_CHANNEL 已指向 fake", ob._CHANNEL is fake)
        check("★ _resolve_sender() 返回 Channel.send",
              ob._resolve_sender() == fake.send)
        ok1 = ob.send_text("走通道", "20002")
        ok2 = ob.send_text("群里走通道", "888", is_group=True)
        ok3 = ob.send_segment([{"type": "image", "data": {"file": "x"}}],
                              "99", kind="image")
        check("三条都成功", ok1 and ok2 and ok3, (ok1, ok2, ok3))
        check("★ legacy _SENDER 一次没调", legacy == [], legacy)
        check("★ 文本 / 群文本 / 图片都进了 Channel.send",
              fake.sent == [("text", "20002", False),
                            ("text", "888", True),
                            ("image", "99", False)], fake.sent)

        # ── [D] Channel 可以当唯一 sender（_SENDER=None 也能发）────
        print("\n[D] 只给 Channel、不给 _SENDER 也能发")
        reset()
        ob.set_sender(None)
        ob.set_channel(fake)
        fake.sent.clear()
        ok = ob.send_text("只有通道", "30003")
        check("★ 没有底层 _SENDER 时仍成功（Channel 顶上）", ok is True, ok)
        check("确实只走了 Channel", fake.sent == [("text", "30003", False)],
              fake.sent)

        # ── [E] 取消 / 坏对象 → 回到 _SENDER ────────────────────────
        print("\n[E] set_channel(None) / 坏对象 → 回到 _SENDER")
        reset()
        ob.set_sender(_legacy)
        ob.set_channel(fake)
        ob.set_channel(None)
        check("取消后 _CHANNEL 回到 None", ob._CHANNEL is None, ob._CHANNEL)
        check("取消后 _resolve_sender() 回到 _SENDER",
              ob._resolve_sender() == _legacy)
        legacy.clear()
        fake.sent.clear()
        ok = ob.send_text("取消后", "40004")
        check("取消后发送成功", ok is True, ok)
        check("★ 取消后走回 _SENDER", legacy == [("text", "40004", False)], legacy)
        check("★ 取消后不再碰 Channel", fake.sent == [], fake.sent)

        ob.set_channel(object())          # 没有可调用 .send → 按关闭处理
        check("★ 传没有 send 的对象按关闭处理", ob._CHANNEL is None, ob._CHANNEL)
        legacy.clear()
        ok = ob.send_text("坏对象后", "50005")
        check("坏对象后仍走 _SENDER 且成功",
              ok is True and legacy == [("text", "50005", False)], (ok, legacy))

        # ── [F] set_sender 行为不变 ─────────────────────────────────
        print("\n[F] set_sender 行为不变")
        reset()
        ob.set_sender(None)
        ok = ob.send_text("没注入发送器", "60006")
        check("set_sender(None) → 丢弃返回 False（旧行为）", ok is False, ok)
    finally:
        ob.set_channel(None)
        ob.set_sender(None)
        ob.shutdown()

    print("\n" + "=" * 56)
    print(f"通过 {len(PASS)} / 失败 {len(FAIL)}")
    for n in FAIL:
        print(f"  FAIL  {n}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
