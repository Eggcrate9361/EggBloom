# -*- coding: utf-8 -*-
"""harness/memory_api.py —— 记忆的唯一读口 / 写口（FIXPLAN 1.5.11 P0-3）

## 为什么要有它

1.5.10 之前，bot_core 里有**五处**地方各自去读记忆：`people.card`、
`people_alias.prompt_line`、`_cross_scene_block`、`get_memory_prompt_block`
（私聊）、`memory_context.build_block` + `build_people_block`（群聊）。
后果只有一个：**没人说得清这一轮 prompt 里的记忆是从哪来的、为什么是这几条**。

现在收成两个函数：

    recall(uid, ...) -> str      读：身份 → 称呼 → 跨场景 → 检索 → 群画像
    remember(kind, ...) -> bool  写：用户消息 / bot 回复 / 事实

预算与顺序在 recall() 里**一次算完**，并打一行：

    [记忆] uid=… scene=group 注入 4 段/532 字（身份 92 / 称呼 18 / 跨场景 120 / 检索 302）

"这轮为什么只注入了这些"从此可解释。

## 边界（与 FIXPLAN §4 一致）

- 沙漏：情景，群↔私聊只取本人
- `people.py`：身份/称呼/事实，私聊才带 private 事实
- 群内画像：只按群取，不含私聊内容
- 群聊检索只取沙漏源（super_memory 群侧已由 EGO 钩子注入，再取同源=双注）
- 读不到就空串，**绝不编**；任何一段异常只影响自己那一段
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

#: 一次 recall 的默认字数预算（FIXPLAN §4：budget=600）
DEFAULT_BUDGET = 600


def _log(msg):
    try:
        from utils import log as _l
        _l(msg)
    except Exception:
        pass


def _cfg():
    try:
        import config
        return config.load_config() or {}
    except Exception:
        return {}


def cross_scene_block(uid, group_id="", *, cfg=None) -> str:
    """「他最近在另一边说过」块（2026-10-30 记忆全打通，P0-3 从 bot_core 搬来）。

    ★ 两个方向都开，但**各自有边界**：
      - 私聊：取他最近在群里说的（他自己看，无泄露）；
      - 群聊：只取**说话这个人自己**私下说过的（`exclude_group` 只保留 `(uid)`
        归属的行），别人的私聊一个字都不进群。
    """
    cfg = _cfg() if cfg is None else cfg
    try:
        if not bool(cfg.get("memory_scope_share", True)):
            return ""
        from sandglass_bridge import cross_scene_recent
        if group_id:
            rows = cross_scene_recent(uid, exclude_group=str(group_id),
                                      hours=6.0, limit=2)
            lead = ("【另一边】他私下跟你说过的话（只有他在场；"
                    "能接就接一句，别把原文念给群里）：\n")
        else:
            rows = cross_scene_recent(uid, exclude_group="", hours=6.0, limit=3)
            lead = "【另一边】他最近在群里说过的话（要接得上就接，不确定就别提）：\n"
    except Exception:
        return ""
    if not rows:
        return ""
    try:
        _log(f"[跨场景] {uid} → {'群' if group_id else '私聊'} 注入 {len(rows)} 条")
    except Exception:
        pass
    return lead + "\n".join("- " + r for r in rows)


def _identity_blocks(uid, group_id, private):
    out = []
    if not uid:
        return out
    try:
        import people
        card = people.card(uid, group_id, private=private)
        if card:
            out.append(("身份", card))
    except Exception:
        pass
    try:
        import people_alias
        alias = people_alias.prompt_line(uid)
        if alias:
            out.append(("称呼", alias))
    except Exception:
        pass
    return out


def _retrieval(query, scene, group_id, private):
    """检索：私聊走沙漏 light；群聊走 memory_context builder（只取沙漏源）。"""
    if not query:
        return None
    if private:
        try:
            from sandglass_bridge import get_memory_prompt_block
            blk = get_memory_prompt_block(query, light=True)
            if blk:
                return ("检索", blk)
        except Exception:
            return None
        return None
    if not group_id:
        return None
    try:
        from harness import memory_context as _mc
        blk = _mc.build_block(query, scope="group", scope_id=str(group_id),
                              sources=("sand",))
        if blk:
            return ("检索", blk)
        return None
    except Exception:
        pass
    try:
        from sandglass_bridge import recall_history_block
        blk = recall_history_block(query, scope=("group", str(group_id)))
        if blk:
            return ("检索", blk)
    except Exception:
        pass
    return None


def _people_block(group_id):
    if not group_id:
        return None
    try:
        from sandglass_bridge import build_people_block
        blk = build_people_block(str(group_id))
        if blk and blk.strip():
            return ("群画像", blk.strip())
    except Exception:
        pass
    return None


def _pack(parts, budget):
    """按顺序装填，超预算就丢后面的段（身份永远先装）。"""
    kept, dropped, used, stats = [], [], 0, []
    for name, text in parts:
        t = str(text or "").strip()
        if not t:
            continue
        if used + len(t) > budget:
            dropped.append(name)
            continue
        kept.append(t)
        used += len(t)
        stats.append(f"{name} {len(t)}")
    return kept, dropped, used, stats


def recall(uid, *, scene="group", group_id="", query="", private=False,
           budget=DEFAULT_BUDGET, cfg=None) -> str:
    """记忆的唯一读口。返回可直接拼进 prompt 的文本块（空串 = 本轮没有记忆）。"""
    cfg = _cfg() if cfg is None else cfg
    gid = "" if private else str(group_id or "")
    parts = []
    parts += _identity_blocks(uid, gid, private)
    cs = cross_scene_block(uid, gid, cfg=cfg)
    if cs:
        parts.append(("跨场景", cs))
    rt = _retrieval(query, scene, gid, private)
    if rt:
        parts.append(rt)
    pb = _people_block(gid)
    if pb:
        parts.append(pb)

    kept, dropped, used, stats = _pack(parts, int(budget or DEFAULT_BUDGET))
    if kept:
        _log(f"[记忆] uid={uid or '-'} scene={scene} 注入 {len(kept)} 段/{used} 字"
             f"（{' / '.join(stats)}）")
    elif parts:
        _log(f"[记忆] uid={uid or '-'} scene={scene} 全部超预算，未注入")
    if dropped:
        _log(f"[记忆] 超预算丢弃：{'、'.join(dropped)}")
    return "\n\n".join(kept)


def remember(kind, *, uid="", text="", scene="", group_id="", meta=None) -> bool:
    """记忆的唯一写口。kind: user_msg / bot_msg / fact / event。

    只做**分发**，不在这里做语义判断（那是各模块自己的事）：
      · user_msg / bot_msg → 沙漏（情景）
      · fact              → people.py（身份/偏好/关系，按人）
      · event             → 目前没有独立存储，返回 False（留白不装作成功）
    """
    kind = str(kind or "").strip()
    text = str(text or "")
    if not kind or not text:
        return False

    if kind == "user_msg":
        try:
            from sandglass_bridge import log_user_message
            meta = meta or {}
            log_user_message(text, group_id=str(group_id or ""),
                             sender=str(uid or ""),
                             sender_nick=str(meta.get("nick") or ""))
            return True
        except Exception:
            return False

    if kind == "bot_msg":
        try:
            from sandglass_bridge import log_bot_reply
            log_bot_reply(text, group_id=str(group_id or ""))
            return True
        except Exception:
            return False

    if kind == "fact":
        try:
            import people
            meta = meta or {}
            people.add_fact(str(uid or ""), str(meta.get("key") or "misc"), text,
                            scope=str(meta.get("scope") or "global"))
            return True
        except Exception:
            return False

    # event：尚无存储层（super_memory 的 events 是另一条链），明确返回 False
    return False
