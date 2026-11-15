# -*- coding: utf-8 -*-
"""harness/channel.py —— 真 Channel 抽象 + 注册表（2026-11-08）

## 这一层解决什么

`harness/source_kind.py` 只是**来源形状**的数据字典（`build()` 里 `kind` 写死
`"qq"`、`SCENE_DESKTOP` 恒 False），`harness/outbound.py` 是**单队列**、没有
Channel 类，`protocols/base.py` 的 `ProtocolAdapter` 是**协议驱动**（三个实现都是
QQ 适配器）。也就是说：手机 / 内容平台 / MC 这些「非 QQ 通道」目前**零代码**，
主链里连一个能问「这个通道能不能发图」的对象都没有。

本模块补一个真正的 `Channel` ABC 和注册表，把「通道」从「形状字典 / 协议驱动」
里独立出来：

```
Channel(ABC)  id / capabilities() / send() / receive() / health()
   ├─ QQChannel   只读复用 source_kind.build() 的形状（不改 source_kind.py）
   ├─ NullChannel 占位（可注册、不真发）
   └─ Phone / ContentPlatform / MC  仅接口位，NOT_IMPLEMENTED，不承诺可用
register() / get() / list() / require() / clear() / placeholders()
```

## 纪律

- **不 import bot_core / protocols.base**：离线可跑（沿用 source_kind 的做法）；
- **只读复用 `source_kind.build()`**：QQChannel 包它产出的 SourceKind dict，
  不复制、不改写、不 import 它的私有实现细节；
- `Channel.send` 的签名刻意与 `outbound._SENDER` 一致
  `fn(kind, target, payload, is_group) -> dict`（返回按 `retcode` 判定），
  这样 `outbound.set_channel()` 能把一个 Channel 直接当 sender 用；
- 未实现通道**不静默吞**：`ChannelNotImplemented` 明确拒绝，`health()` 明写
  `status=NOT_IMPLEMENTED`，`register()` 也拒收。

## 落地边界（重要）

手机 / 内容平台 / MC 本轮**只有接口位**：有 `id`、能查 `capabilities()`（空集）、
`health()` 明写 `NOT_IMPLEMENTED`，但 `send` / `receive` 一律抛
`ChannelNotImplemented`，且**不允许注册进可用注册表**。它们不接任何真实平台，
不承诺可用；要落地得各自实现驱动（协议/API/鉴权/事件链路），那是单独的活。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Dict

# 只读复用现有「来源形状」模块（不改它、不 import protocols.base）
try:
    from . import source_kind as _sk
except Exception:                       # 单文件加载时没有包上下文
    import source_kind as _sk


#: 未实现通道的统一标记（health / 文档 / 占位类共用）。
NOT_IMPLEMENTED = "NOT_IMPLEMENTED"


class ChannelNotImplemented(RuntimeError):
    """使用 / 注册一个「接口位」通道时抛出 —— 明示不承诺可用，绝不静默成功。"""

    def __init__(self, channel_id):
        self.channel_id = str(channel_id or "")
        super().__init__(
            f"通道 {self.channel_id!r} 仅为接口位（{NOT_IMPLEMENTED}），"
            "尚未实现，不可使用 / 注册")


# ── Channel ABC ────────────────────────────────────────────────────

class Channel(ABC):
    """一个可收发的外部通道。

    五个成员：
      `id`            稳定标识（小写），注册表按它索引
      `capabilities()`能力名集合（复用 source_kind.CAP_* 的名字）
      `send(...)`     出站；签名与 outbound._SENDER 一致，返回带 `retcode` 的 dict
      `receive(...)`  入站（拉取式；推送式通道可返回空列表并如实说明）
      `health()`      健康快照，返回 dict（至少含 `ok`）

    `implemented` 类属性：默认 True；接口位覆盖成 False，`register()` 据此拒收。
    """

    implemented = True

    @property
    @abstractmethod
    def id(self) -> str:
        ...

    @abstractmethod
    def capabilities(self):
        """返回 frozenset（或可迭代）的能力名。"""
        ...

    @abstractmethod
    def send(self, kind, target, payload=None, is_group=False) -> dict:
        """出站一次。返回 dict，`retcode == 0` 视为成功（与 outbound 一致）。"""
        ...

    @abstractmethod
    def receive(self, limit=1):
        """拉取入站消息，返回列表；拿不到 / 推送式返回空列表。"""
        ...

    @abstractmethod
    def health(self) -> dict:
        ...

    def can(self, cap) -> bool:
        """能力查询的小helper：这个通道声明了某能力吗。"""
        try:
            return str(cap or "").strip() in self.capabilities()
        except Exception:
            return False


# ── QQ：只读复用 source_kind.build() 的形状 ─────────────────────────

class QQChannel(Channel):
    """把现有 `source_kind.build(adapter)` 产出的 SourceKind 包成 Channel。

    只读复用：`self.source_kind` 暴露的就是 build() 的原 dict（不复制字段、
    不改写），`send()` 走它 `channel["send"]` 的闭包，`capabilities()` 读它的
    `channel["allowed"]`。这样加 Channel 抽象不需要动 source_kind.py 一行。
    """

    def __init__(self, adapter, *, driver_id=""):
        if adapter is None:
            raise ValueError("QQChannel 需要 adapter（source_kind.build 会校验）")
        self._adapter = adapter
        self._src = _sk.build(adapter, driver_id=driver_id)

    @property
    def id(self) -> str:
        return "qq"

    @property
    def source_kind(self) -> dict:
        """只读复用的 SourceKind 形状（供核对，调用方不应改它）。"""
        return self._src

    def capabilities(self):
        return frozenset(self._src["channel"]["allowed"])

    def send(self, kind, target, payload=None, is_group=False) -> dict:
        cap = str(kind or _sk.CAP_TEXT).strip()
        scene = _sk.SCENE_GROUP if is_group else _sk.SCENE_PRIVATE
        try:
            res = self._src["channel"]["send"](cap, str(target or ""),
                                               payload or {}, scene=scene)
        except Exception as e:
            return {"retcode": -1, "ok": False,
                    "reason": f"{type(e).__name__}: {e}"}
        ok = bool(res.get("ok")) and not bool(res.get("skipped"))
        return {
            "retcode": 0 if ok else 1,
            "ok": ok,
            "via": res.get("via", ""),
            "degraded": bool(res.get("degraded")),
            "skipped": bool(res.get("skipped")),
            "reason": str(res.get("reason") or ""),
            "data": res.get("data"),
        }

    def receive(self, limit=1):
        # QQ 入站是 OneBot 事件推送（source_kind 的 channel.link.post_event），
        # 不是轮询拉取；这里不造数据，如实返回空列表。
        return []

    def health(self) -> dict:
        ok = self._adapter is not None
        return {
            "ok": ok,
            "id": self.id,
            "kind": self._src.get("kind", ""),
            "driver": str(self._src.get("driver") or ""),
            "detail": "只读复用 source_kind.build() 形状" if ok else "无适配器",
        }


# ── Null：占位（安全空实现，可注册、不真发）─────────────────────────

class NullChannel(Channel):
    """占位用的空对象：注册它不会崩、也不会真的往任何平台发消息。"""

    @property
    def id(self) -> str:
        return "null"

    def capabilities(self):
        return frozenset()

    def send(self, kind, target, payload=None, is_group=False) -> dict:
        return {"retcode": 1, "ok": False, "noop": True, "skipped": True,
                "reason": "NullChannel 仅占位，不实际发送"}

    def receive(self, limit=1):
        return []

    def health(self) -> dict:
        return {"ok": True, "id": self.id, "noop": True,
                "detail": "占位通道：对象可用但 send 一律不发送"}


# ── 接口位：手机 / 内容平台 / MC（NOT_IMPLEMENTED，不承诺可用）────────

_PLACEHOLDER_NOTES: Dict[str, str] = {
    "phone": "手机通道（短信 / 推送 / App）—— 仅接口位，未实现",
    "content_platform": "内容平台（B站 / 小红书 / 公众号等）—— 仅接口位，未实现",
    "mc": "Minecraft 通道 —— 仅接口位，未实现",
}

#: 三个保留 id：它们只是接口位，不允许注册进可用注册表。
RESERVED_PLACEHOLDERS = tuple(_PLACEHOLDER_NOTES)


class _PlaceholderChannel(Channel):
    """接口位基类：能查询、能报健康，但 send/receive 一律明确拒绝。"""

    implemented = False
    _cid = ""
    _note = ""

    @property
    def id(self) -> str:
        return self._cid

    def capabilities(self):
        return frozenset()                 # 没有任何已实现的能力

    def send(self, kind, target, payload=None, is_group=False) -> dict:
        raise ChannelNotImplemented(self._cid)

    def receive(self, limit=1):
        raise ChannelNotImplemented(self._cid)

    def health(self) -> dict:
        return {"ok": False, "id": self._cid, "implemented": False,
                "status": NOT_IMPLEMENTED, "detail": self._note}


class PhoneChannel(_PlaceholderChannel):
    """手机通道 —— 接口位（NOT_IMPLEMENTED）。"""

    _cid = "phone"
    _note = _PLACEHOLDER_NOTES["phone"]


class ContentPlatformChannel(_PlaceholderChannel):
    """内容平台通道 —— 接口位（NOT_IMPLEMENTED）。"""

    _cid = "content_platform"
    _note = _PLACEHOLDER_NOTES["content_platform"]


class MCChannel(_PlaceholderChannel):
    """Minecraft 通道 —— 接口位（NOT_IMPLEMENTED）。"""

    _cid = "mc"
    _note = _PLACEHOLDER_NOTES["mc"]


# ── 注册表 ─────────────────────────────────────────────────────────

_REGISTRY: Dict[str, Channel] = {}


def register(channel) -> Channel:
    """注册一个**可用**通道，返回它本身（便于链式）。

    明确拒绝：id 为空 / 声明 `implemented=False`（接口位）/ 没有可调用 send。
    """
    cid = str(getattr(channel, "id", "") or "").strip().lower()
    if not cid:
        raise ValueError("Channel.id 不能为空")
    if not bool(getattr(channel, "implemented", True)):
        raise ChannelNotImplemented(cid)
    if not callable(getattr(channel, "send", None)):
        raise ValueError(f"通道 {cid!r} 没有可调用的 send()")
    _REGISTRY[cid] = channel
    return channel


def get(channel_id):
    """按 id 取已注册通道；取不到（含接口位）返回 None，不抛异常。"""
    return _REGISTRY.get(str(channel_id or "").strip().lower())


def list():
    """已注册（可用）通道 id 的有序列表。接口位不在其中。"""
    return sorted(_REGISTRY)


def require(channel_id) -> Channel:
    """取可用通道；接口位与未注册都**明确拒绝**（不是返回 None 让人踩坑）。"""
    cid = str(channel_id or "").strip().lower()
    ch = _REGISTRY.get(cid)
    if ch is not None:
        return ch
    if cid in RESERVED_PLACEHOLDERS:
        raise ChannelNotImplemented(cid)
    raise KeyError(f"未注册的通道：{channel_id!r}")


def clear() -> None:
    """清空注册表（测试隔离 / 关机用）。"""
    _REGISTRY.clear()


def placeholders() -> dict:
    """返回三个接口位的 {id: 说明}，供页面 / 日志查询「留了哪些位」。"""
    return {k: v for k, v in _PLACEHOLDER_NOTES.items()}


__all__ = [
    "Channel", "ChannelNotImplemented", "NOT_IMPLEMENTED",
    "QQChannel", "NullChannel",
    "PhoneChannel", "ContentPlatformChannel", "MCChannel",
    "RESERVED_PLACEHOLDERS",
    "register", "get", "list", "require", "clear", "placeholders",
]
