# -*- coding: utf-8 -*-
"""tests/test_channel.py —— 真 Channel 抽象 + 假平台全链路（2026-11-08）

验收原话：`tests/test_channel.py`：假平台跑通「注册 → 能力查询 → 发送 → 出站 →
健康」全链路，以及未实现通道被明确拒绝。

全离线：假适配器 / 假平台，不依赖真实网络 / 模型 API / QQ 连接。

  · [A] 注册 → 能力查询 → 发送（Channel 直发）→ 出站（outbound.set_channel）
        → 健康，一条链走通；
  · [B] QQChannel 只读复用 source_kind.build() 的形状（不改 source_kind.py）；
  · [C] NullChannel 占位：可注册、不真发；
  · [D] 手机 / 内容平台 / MC 只是接口位：NOT_IMPLEMENTED，send/receive/register
        一律明确拒绝，不进可用注册表；
  · [E] 注册表边界（抽象、拒收坏对象、require 语义）。

运行：python tests/test_channel.py
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

# 落盘库隔离：必须在 import harness.outbound 之前设（outbox._DEFAULT 是模块级常量）
os.environ.setdefault("EGGBLOOM_OUTBOX_DB",
                      os.path.join(tempfile.mkdtemp(prefix="eggch_"), "outbox.sqlite3"))

from harness import channel as ch        # noqa: E402
from harness import outbound as ob        # noqa: E402
from harness import source_kind as sk     # noqa: E402

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name if not extra else f"{name}  → {extra}")


def _raises(exc, fn):
    try:
        fn()
    except exc:
        return True
    except Exception:
        return False
    return False


class FakeAdapter:
    """最小 QQ 适配器：只有 name + api_call（与 test_source_kind.py 同款）。"""

    name = "fakeqq"

    def __init__(self):
        self.calls = []

    def api_call(self, endpoint, data=None, timeout=15):
        self.calls.append((endpoint, dict(data or {})))
        return {"retcode": 0, "data": {"endpoint": endpoint}}


class FakeChannel(ch.Channel):
    """假平台：记录每一次 send，health 如实报计数。"""

    def __init__(self, cid="fake", caps=("text", "image")):
        self._cid = cid
        self._caps = frozenset(caps)
        self.sent = []
        self.inbox = [{"text": "在吗"}]

    @property
    def id(self):
        return self._cid

    def capabilities(self):
        return self._caps

    def send(self, kind, target, payload=None, is_group=False):
        self.sent.append((kind, str(target), dict(payload or {}), bool(is_group)))
        return {"retcode": 0, "ok": True, "via": "fake"}

    def receive(self, limit=1):
        return self.inbox[:max(0, int(limit))]

    def health(self):
        return {"ok": True, "id": self._cid, "sent": len(self.sent),
                "detail": "假平台在线"}


def main():
    ch.clear()
    legacy = []
    ob.set_logger(lambda m: None)
    ob.set_config(lambda: {})
    ob.reset_stats()
    try:
        # ── [A] 假平台全链路：注册 → 能力查询 → 发送 → 出站 → 健康 ──
        print("[A] 假平台全链路")
        fake = FakeChannel()

        # ① 注册
        got = ch.register(fake)
        check("注册返回通道本身", got is fake, got)
        check("get() 能取回", ch.get("fake") is fake)
        check("list() 里有它", "fake" in ch.list(), ch.list())
        check("require() 能取回", ch.require("fake") is fake)

        # ② 能力查询
        check("capabilities 含声明的能力",
              set(fake.capabilities()) == {"text", "image"}, fake.capabilities())
        check("can('text') 为真", fake.can("text") is True)
        check("can('poke') 为假", fake.can("poke") is False)

        # ③ 发送（Channel 直发）
        r = fake.send("text", "10001", {"text": "在吗"}, False)
        check("直发 retcode=0", r.get("retcode") == 0 and r.get("ok") is True, r)
        check("直发被记录", len(fake.sent) == 1 and fake.sent[0][1] == "10001",
              fake.sent)
        check("receive 拉到入站", fake.receive() == [{"text": "在吗"}], fake.receive())

        # ④ 出站：Channel 成为 sender 来源，legacy _SENDER 不该被调用
        def _legacy(kind, target, payload, is_group):
            legacy.append((kind, target, is_group))
            return {"retcode": 0}

        ob.set_sender(_legacy)
        ob.set_channel(fake)
        ok1 = ob.send_text("你好", "20002")
        ok2 = ob.send_text("群里见", "888", is_group=True)
        check("出站两条都成功", ok1 is True and ok2 is True, (ok1, ok2))
        check("★ 走了 Channel.send，legacy 一次没调", legacy == [], legacy)
        check("★ Channel 收到私聊那条",
              any(s[0] == "text" and s[1] == "20002" and s[3] is False
                  for s in fake.sent), fake.sent)
        check("★ Channel 收到群聊那条（is_group=True）",
              any(s[1] == "888" and s[3] is True for s in fake.sent), fake.sent)
        check("出站进了统计", ob.stats()["sent"] >= 2, ob.stats())

        # ⑤ 健康
        h = fake.health()
        check("health ok=True", h.get("ok") is True, h)
        check("★ health 如实报出发送计数（直发1 + 出站2）",
              h.get("sent") == 3, h)

        # ── [B] QQChannel 只读复用 source_kind.build() ─────────────
        print("\n[B] QQChannel 复用 source_kind.build() 形状")
        ad = FakeAdapter()
        q = ch.QQChannel(ad, driver_id="fakeqq")
        check("id == 'qq'", q.id == "qq", q.id)
        check("source_kind 是 build() 的形状（三块齐全）",
              {"kind", "scope", "channel", "threadContext"} <= set(q.source_kind),
              list(q.source_kind))
        check("★ shape 里 kind 仍是 'qq'（只读复用、没另起一套）",
              q.source_kind["kind"] == "qq", q.source_kind["kind"])
        check("★ SCENE_DESKTOP 恒 False 的形状也保留",
              q.source_kind["scope"][sk.SCENE_DESKTOP] is False,
              q.source_kind["scope"])
        check("capabilities 来自 build() 的 channel.allowed",
              q.capabilities() == frozenset(q.source_kind["channel"]["allowed"]),
              q.capabilities())
        qr = q.send("text", "123", {"text": "hi", "message": "hi"}, True)
        check("★ 群发 retcode=0 且归一出 retcode",
              qr.get("retcode") == 0 and qr.get("ok") is True, qr)
        check("★ 真的走了 source_kind 的动作端点 send_group_msg",
              any(c[0] == "send_group_msg" for c in ad.calls), ad.calls)
        check("receive 如实返回空（QQ 是事件推送，不轮询）", q.receive() == [])
        check("health ok=True", q.health().get("ok") is True, q.health())
        check("QQChannel 也能注册", ch.register(q) is q and "qq" in ch.list())

        # ── [C] NullChannel 占位 ───────────────────────────────────
        print("\n[C] NullChannel 占位")
        nul = ch.NullChannel()
        check("id == 'null'", nul.id == "null", nul.id)
        check("声明零能力", nul.capabilities() == frozenset(), nul.capabilities())
        nr = nul.send("text", "1", {"text": "x"})
        check("★ send 不真发（ok=False + noop）",
              nr.get("ok") is False and nr.get("noop") is True, nr)
        check("health 标 noop", nul.health().get("noop") is True, nul.health())
        check("占位通道可注册（它是已实现的安全空对象）",
              ch.register(nul) is nul and "null" in ch.list())

        # ── [D] 手机 / 内容平台 / MC：接口位，明确拒绝 ──────────────
        print("\n[D] 未实现通道被明确拒绝")
        for cls, cid in ((ch.PhoneChannel, "phone"),
                         (ch.ContentPlatformChannel, "content_platform"),
                         (ch.MCChannel, "mc")):
            obj = cls()
            hh = obj.health()
            check(f"{cid}: health 明写 NOT_IMPLEMENTED",
                  hh.get("status") == ch.NOT_IMPLEMENTED and hh.get("ok") is False, hh)
            check(f"{cid}: 零能力", obj.capabilities() == frozenset())
            check(f"{cid}: send 抛 ChannelNotImplemented",
                  _raises(ch.ChannelNotImplemented,
                          lambda: obj.send("text", "1", {})))
            check(f"{cid}: receive 抛 ChannelNotImplemented",
                  _raises(ch.ChannelNotImplemented, lambda: obj.receive()))
            check(f"{cid}: register 明确拒收",
                  _raises(ch.ChannelNotImplemented, lambda: ch.register(obj)))
            check(f"{cid}: require 明确拒绝",
                  _raises(ch.ChannelNotImplemented, lambda: ch.require(cid)))
            check(f"{cid}: get 返回 None", ch.get(cid) is None)
            check(f"{cid}: 不在可用注册表 list() 里", cid not in ch.list(), ch.list())
        try:
            ch.PhoneChannel().send("text", "1", {})
        except ch.ChannelNotImplemented as e:
            check("异常带 channel_id 与 NOT_IMPLEMENTED 字样",
                  e.channel_id == "phone" and ch.NOT_IMPLEMENTED in str(e), str(e))
        check("placeholders() 恰好是三个接口位",
              set(ch.placeholders()) == set(ch.RESERVED_PLACEHOLDERS),
              ch.placeholders())
        check("list() 只含可用通道 fake/qq/null",
              set(ch.list()) == {"fake", "qq", "null"}, ch.list())

        # ── [E] 注册表边界 ────────────────────────────────────────
        print("\n[E] 注册表边界")
        check("Channel 是抽象的（不能直接 new）",
              _raises(TypeError, lambda: ch.Channel()))

        class _Bad:
            id = "bad"          # 没有 send

        check("register 拒收没有 send 的对象",
              _raises(ValueError, lambda: ch.register(_Bad())))
        check("require 未注册 id → KeyError",
              _raises(KeyError, lambda: ch.require("nope")))
        check("get 大小写不敏感", ch.get("FAKE") is fake)
        ch.clear()
        check("clear() 清空注册表", ch.list() == [], ch.list())
        ch.register(fake)
        check("clear 后能重新注册", ch.get("fake") is fake)
    finally:
        ob.set_channel(None)      # 恢复默认（关闭）
        ob.set_sender(None)
        ob.shutdown()
        ch.clear()

    print("\n" + "=" * 56)
    print(f"通过 {len(PASS)} / 失败 {len(FAIL)}")
    for n in FAIL:
        print(f"  FAIL  {n}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
