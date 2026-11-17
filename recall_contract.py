# -*- coding: utf-8 -*-
"""撤回反馈的共享契约：唯一负责人、默认文案与旧配置迁移。

消息链路会先把 ``notice.recall`` 广播给主动性和插件，再由主链决定是否让位。
如果每一方各自解释 ``recall_feedback_by``，任何一个字符串分歧都会造成双回。
负责人判定、默认文案和旧默认值迁移因此集中在这里。
"""
from __future__ import annotations

from typing import Any, Iterable

OWNER_CORE = "core"
OWNER_PLUGIN = "plugin"
OWNER_PROACTIVITY = "proactivity"
OWNER_ROUTES = frozenset((OWNER_CORE, OWNER_PLUGIN, OWNER_PROACTIVITY))


def normalize_owner(value: Any) -> str:
    """配置值归一化；未知值按主链处理，与历史缺省行为一致。"""
    owner = str(value or "").strip().lower()
    return owner if owner in OWNER_ROUTES else OWNER_CORE


def core_owns(value: Any) -> bool:
    return normalize_owner(value) == OWNER_CORE


def plugin_owns(value: Any) -> bool:
    return normalize_owner(value) == OWNER_PLUGIN


def proactivity_owns(value: Any) -> bool:
    return normalize_owner(value) == OWNER_PROACTIVITY


#: 默认反馈只确认“消息已经作废/被看到”，不追问、不评价、不制造压力。
TRUNCATED_PRESET = (
    "好，那我不回了。",
    "行，撤了就当没说。",
    "收到，这页翻过去。",
    "好，我当没看见。",
    "嗯，作废。",
)

SEEN_PRESET = (
    "看到了，没事。",
    "嗯，看到过。",
    "好，我知道了。",
    "没事，撤吧。",
    "行，我不提。",
)

PRIVATE_PRESET = (
    "嗯，没事。",
    "好，知道了。",
    "行，撤吧。",
    "没关系。",
)


def _plain_copy(items: Iterable[Any]) -> tuple:
    out = []
    for item in items or ():
        if isinstance(item, str):
            text = item.strip()
        elif isinstance(item, dict) and not item.get("disabled"):
            text = str(item.get("text") or "").strip()
        else:
            text = ""
        if text:
            out.append(text)
    return tuple(out)


#: 旧版预置。只有配置完整等于这些列表时才迁移，用户自己改过的文案原样保留。
LEGACY_TRUNCATED_PRESET = _plain_copy((
    "？", "你撤回了一条消息", "还好你撤了 我差点就回了",
    "撤回了？行 那我也不用回了", "还没看呢 你就撤了",
    "手速可以啊 我还没来得及回", "撤了就撤了吧 反正我也没打算回",
    "哎哟 差点就点发送了", "你撤回的速度比我打字快",
    "还好还好 还没发出去", "撤回+1 这条我当没看见",
    "不回了不回了 你都撤了我还回啥", "撤得好 我正愁怎么接",
    "你撤回 我省事", "这波属于是你跑得快", "撤都撤了 那就算了",
    "还没读呢就撤了 算你识相", "行吧 本AI的回复可以省了",
    "撤回=跳过 这局不算", "差点白打字了 幸好你撤了",
    "你撤你的 我当无事发生", "撤回成功 已为你拦截一条AI回复",
))

LEGACY_SEEN_PRESET = _plain_copy((
    "看到了 晚了", "看到了哦 不说", "太慢了 我已经看到了",
    "来不及了 嘿嘿", "撤回慢了 已截获", "晚了 我看到了",
    "回复已经发出去了 哈哈", "不好意思 你撤的时候我已经看到了",
    "看到了 不过我不说", "来不及啦 我已经读过了", "晚啦 眼睛比你手快",
    "撤回无效 本人已阅", "看到了看到了 别费劲了",
    "我看到了 你撤了个寂寞", "撤晚了 我已经知道了",
    "不巧 我刚好在看", "被我看到了吧 嘿嘿",
    "你撤回的速度还是不够快啊", "看到了 但我会装作没看到",
    "太迟了 消息已送达我脑子里",
))

LEGACY_PRIVATE_PRESET = _plain_copy((
    "撤回了？我还没看到", "什么情况 手滑了？",
    "你撤回的速度比打字快多了", "心虚了吧",
    "好家伙 说撤就撤", "别撤回啊 发都发了",
))


_COPY_MIGRATIONS = {
    "recall_feedback_pre_messages": (LEGACY_TRUNCATED_PRESET, TRUNCATED_PRESET),
    "recall_feedback_post_messages": (LEGACY_SEEN_PRESET, SEEN_PRESET),
    "recall_private_post_messages": (LEGACY_PRIVATE_PRESET, PRIVATE_PRESET),
}


def migrate_legacy_copy(cfg: dict, file_keys: set | None = None) -> dict:
    """把完整等于旧预置的配置迁移到新预置；自定义列表保持不变。

    只处理用户文件里明确写过的键，避免把运行时临时配置误判为用户设置。
    返回同一个 dict，便于 ``load_config`` 在原地完成迁移。
    """
    keys = file_keys if isinstance(file_keys, set) else set(cfg or {})
    for key, (legacy, replacement) in _COPY_MIGRATIONS.items():
        if key not in keys or key not in cfg:
            continue
        if _plain_copy(cfg.get(key)) == legacy:
            cfg[key] = list(replacement)
    return cfg


__all__ = [
    "OWNER_CORE", "OWNER_PLUGIN", "OWNER_PROACTIVITY", "OWNER_ROUTES",
    "TRUNCATED_PRESET", "SEEN_PRESET", "PRIVATE_PRESET",
    "LEGACY_TRUNCATED_PRESET", "LEGACY_SEEN_PRESET", "LEGACY_PRIVATE_PRESET",
    "normalize_owner", "core_owns", "plugin_owns", "proactivity_owns",
    "migrate_legacy_copy",
]
