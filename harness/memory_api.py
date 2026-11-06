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
import re
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

#: 一次 recall 的默认字数预算（FIXPLAN §4 原为 600；2026-11-06 提到 900）
#: 为什么：私聊检索收口进来之后，顺序是「身份 → 称呼 → 跨场景 → 检索」。
#: 检索块本身最多 400 字 + 抬头，而前面三段真机合计约 230 字 ——
#: 600 的旧预算会把**最后装的检索整段丢掉**（`_pack` 是整段丢，不裁中间），
#: 表现成"检索跑了但 prompt 里一条都没有"。900 才能让四段都装得下。
#: 这是**上限**不是固定开销：没检索到东西时仍然只注入实际有的几段。
DEFAULT_BUDGET = 900

#: 判"同一行"时的最短长度。太短的行（"【核心记忆】"、"- 1"）只做全等比较，
#: 不做包含判断 —— 否则一个 `"嗯"` 能把别人整段吞掉。
DEDUPE_MIN_LEN = 12


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


def _retrieval(query, scene, group_id, private, uid=""):
    """检索：私聊与群聊**共用一个 builder**，作用域不同。

    ★ 2026-11-06（单独动私聊检索层）：私聊原来走
      `get_memory_prompt_block(light=True, scope=("private", uid))` ——
      `light=True` 把整个检索层跳过了，只剩一行元信息。旧注释写的
      「沙漏里没有私聊数据 → 直接返回空，宁缺勿串」是**过时的**：
      私聊用户行本来就写进沙漏（`[昵称(uid)] 正文`），
      `_scope_match` 的 `("private", uid)` 也一直支持按 uid 过滤。
      于是超过 history 窗口的旧事，能不能想起来全看另一处 `pm_ctx` 碰运气。

      现在收口：私聊也走 `memory_context.build_block`，带
        · scope=("private", uid)（跨人隔离，不串别人的私聊）
        · sand + sm 两个源（沙漏情景 + super_memory 向量）
        · 日期类召回、τ 阈值、字数预算（builder 自带）
        · `[记忆] ... 检索 N 字` 日志（recall() 统一打）
      开关 `private_memory_retrieval_enable`（默认开），关掉即零检索；
      builder 抛异常返回 None，绝不编。
    """
    if not query:
        return None
    if private:
        try:
            if not bool(_cfg().get("private_memory_retrieval_enable", True)):
                return None
            from harness import memory_context as _mc
            blk = _mc.build_block(query, scope="private", scope_id=str(uid or ""),
                                  sources=("sand", "sm"))
            if blk:
                return ("检索", blk)
        except Exception:
            pass
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
    rt = _retrieval(query, scene, gid, private, uid)
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
      · event             → super_memory 的 `events` 表（2026-11-01 补上；
                            之前这里明确返回 False，导致"Bot 自己做的事"没有落点）
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

    # event：事件型记忆（Bot 自己做过的事 / 值得记住的发生）
    # 2026-11-01：以前这里直接 return False（"没有存储层，不装作成功"）。
    # 现在落到 super_memory 的 `events` 表（按 user_id 隔离，见其 _user_scope_filter），
    # 这样"什么时候存了什么表情包"隔天问起来也能被召回。
    if kind == "event":
        meta = meta or {}
        try:
            from plugins.ego import super_memory as _sm
        except Exception:
            try:
                import plugins.ego.super_memory as _sm  # noqa: F811
            except Exception as e:
                _log(f"[记忆] event 落库不可用: {e}")
                return False
        try:
            return bool(_sm.add_action_event(
                text,
                scope=str(meta.get("scope") or scene or "group"),
                scope_id=str(meta.get("scope_id") or group_id or uid or ""),
                user_id=str(meta.get("user_id") or (uid if not group_id else "")),
                event_type=str(meta.get("event_type") or "event"),
                importance=float(meta.get("importance") or 0.6),
            ))
        except Exception as e:
            _log(f"[记忆] event 落库失败: {e}")
            return False

    return False


# ── 注入侧：两条通道合成一条（P1-3「同一件事不会来自两处」）──────────────
#
# 现状（2026-11-06 核）：prompt 里确实有**两条注入通道** ——
#   ① 本模块 `recall()`：身份 / 称呼 / 跨场景 / 检索（沙漏）/ 群画像；
#   ② EGO 插件的 `before_reply`：super_memory 的【核心记忆】+ 长期人格 + 好感。
#
# 两者**存的东西重叠**：沙漏记全部对话原文，super_memory 的 `messages` 表也记
# user_msg + bot_reply。群聊侧靠 `memory_context.collect(sources=("sand",))`
# 刻意只取沙漏来避重，但私聊侧两边都能召回同一句话 —— 一条事实在 prompt 里
# 出现两遍，模型会更用力地按条目交付（汇报腔的放大器），也白烧 token。
#
# 所以收敛点放在**最后一个出口**：`_inject_ego_context()`（bot_core 里唯一
# 往 dynamic_parts 塞 ego 文本的函数）在拼接前把"已经注入过的行"剔掉。
# 不合并存储、不改召回逻辑、不改注入顺序 —— 没有重复时输出逐字节不变。

_LINE_PREFIX_RE = re.compile(r"^[\s\-–—·•*　]+")


def _norm_line(line) -> str:
    """归一化一行：去掉列表符号、折叠空白。只用于比较，不改原文本。"""
    t = _LINE_PREFIX_RE.sub("", str(line or ""))
    t = re.sub(r"[ \t\u3000]+", " ", t).strip()
    return t


def filter_duplicate_lines(existing, incoming, *, min_len=DEDUPE_MIN_LEN):
    """把 `incoming` 里与 `existing` 重复的行去掉。

    参数
      existing : 已经注入过的文本。可以是字符串，也可以是字符串列表
                 （`dynamic_parts` 那种一段一个元素的形状）。
      incoming : 待注入的文本（多行）。
      min_len  : 判"包含关系"的最短长度；短于此只做全等比较。

    返回 (过滤后的文本, stats)。stats = {"kept": n, "dropped": n, "samples": [...]}
    —— `samples` 只留前两条被丢掉的行（截 30 字），用于日志与测试，不落盘。

    判重规则（保守，宁留勿删）：
      · 归一化后全等 → 重复；
      · 长度 ≥ min_len 且是某条已有行的子串 / 包含某条已有行 → 重复；
      · 其余一律保留。**绝不因为"看起来像"就删** —— 记忆是可以重复措辞的。
    """
    if isinstance(existing, str):
        ex_text = existing
    else:
        ex_text = "\n".join(str(x or "") for x in (existing or []))
    ex_lines = [n for n in (_norm_line(ln) for ln in ex_text.splitlines()) if n]
    ex_set = set(ex_lines)
    ex_long = [n for n in ex_lines if len(n) >= min_len]
    ex_joined = "\n".join(ex_lines)

    kept, dropped = [], []
    for raw in str(incoming or "").splitlines():
        norm = _norm_line(raw)
        if not norm:
            # 空行保留（块之间本来就有空行，删了会粘成一坨）
            kept.append(raw)
            continue
        dup = norm in ex_set
        if not dup and len(norm) >= min_len:
            if norm in ex_joined or any(n in norm for n in ex_long):
                dup = True
        if dup:
            dropped.append(norm[:30])
        else:
            kept.append(raw)
    text = "\n".join(kept).strip("\n")
    return text, {"kept": len(kept) - sum(1 for k in kept if not _norm_line(k)),
                  "dropped": len(dropped), "samples": dropped[:2]}
