# -*- coding: utf-8 -*-
"""harness.memory_context —— 2.0 记忆相对注入 builder（阶段B 骨架）。

设计依据：仓库外归档的 2.0 记忆设计第二节（未随公开仓库分发）。

一句话：**多源候选 → 归一分数 → 阈值闸 → 预算封顶 → 单个包装块**。
替代此前【沙漏记忆】/【相关历史】/【用户画像】检索层各灌各的乱象
（同一份历史以两种来源重复注入、无相关性阈值、无总量预算）。

原则：
  · 本模块**不 import bot_core**，只依赖 sandglass_bridge（阶段A 的
    search_scored_context 出口）与 ego.super_memory（原生 sim/rank_score）。
  · 两源全部**惰性导入 + 独立降级**：任一源挂了不影响另一源；
    全挂返回 ""（零 token 成本，绝不吃兜底无分数据）。
  · 分数只用于闸门与排序，跨源不做绝对值硬比——两源原生分数都标定到
    [0,1] 的"相关度语义"（融合分 / 向量 cosine），量纲近似可比。
  · 语境加权（话题延续×1.3、发言者×1.2、时间衰减、防复读）是阶段D，
    本文件预留 ctx 参数位，不提前实现。
"""

import time

# ── 配置（默认值与 2.0 设计稿一致；可 set_config 覆盖，webui 后续接）──
_CFG = {
    "enabled": True,
    "tau": 0.35,          # 阈值闸：低于不注入
    "budget": 400,        # 条目行总字数封顶（不含包装抬头）
    "max_items": 4,       # 条数硬上限（预算之外的第二道闸）
    "width": 140,         # 单条截断
    "per_source_limit": 6,  # 每源候选捞取数（闸门从池子里挑，不是捞几条用几条）
}

_HEAD = ("【记忆】以下与当前话题相关，仅作背景参考；"
         "不要提及你在查记录，也不要复述原文：")

# 源注入点（测试打桩用；None = 惰性走真实桥）
_SOURCES = {"sandglass": None, "super_mem": None}
# SuperMemory 单例（None=未建, False=构造失败负缓存，避免热路径每条消息重建）
_SM_SINGLETON = None


def set_config(**kw):
    for k, v in kw.items():
        if k in _CFG:
            _CFG[k] = v


def set_sources(sandglass=None, super_mem=None):
    """测试/调用方注入自定义源。
    sandglass:  callable(message, limit) -> [(score, text), ...]
    super_mem:  callable(message, scope, scope_id, limit) -> [dict(content,sim,rank_score,type), ...]
    """
    if sandglass is not None:
        _SOURCES["sandglass"] = sandglass
    if super_mem is not None:
        _SOURCES["super_mem"] = super_mem


def _reset_sources():
    _SOURCES["sandglass"] = None
    _SOURCES["super_mem"] = None


# ── 源适配 ──────────────────────────────────────────────────────────

def _sandglass_candidates(message, limit, scope=None):
    """沙漏融合分检索。默认源 = sandglass_bridge.search_scored_context（阶段A）。

    scope=(kind,key)：会话作用域（2026-10-11 私聊泄漏群聊修复）——沙漏文件是
    群/私混写的，检索层不过滤就会把私聊旧账注进群聊 prompt（用户 22:35 当场
    抓包"怎么私聊记忆也进入群聊了"）。测试桩可能不带 scope 参数 → TypeError 回退。"""
    fn = _SOURCES["sandglass"]
    if fn is None:
        try:
            import sandglass_bridge as sb
        except Exception:
            return []
        fn = getattr(sb, "search_scored_context", None)
        if fn is None:
            return []
    try:
        try:
            rows = fn(message, limit=limit, scope=scope)
        except TypeError:
            rows = fn(message, limit=limit)
        return [(float(s), str(t)) for s, t in rows or []]
    except Exception:
        return []


def _super_mem_candidates(message, scope, scope_id, limit):
    """super_memory 向量召回。原生分：sim（向量 cosine）优先，
    无向量时退 rank_score（importance+tier，截到 1.0）。"""
    global _SM_SINGLETON
    fn = _SOURCES["super_mem"]
    sm = None
    if fn is None:
        if not scope_id:
            return []
        if _SM_SINGLETON is None:
            try:
                from plugins.ego.super_memory import SuperMemory
                _SM_SINGLETON = SuperMemory()
            except Exception:
                _SM_SINGLETON = False   # 负缓存：构造失败不每轮再试
        if _SM_SINGLETON is False or _SM_SINGLETON is None:
            return []
        sm = _SM_SINGLETON

        def fn(message, scope, scope_id, limit):
            try:
                return sm.recall_key_memories(scope=scope, scope_id=scope_id,
                                              current_msg=message, limit=limit) or []
            except Exception:
                return []
    try:
        items = fn(message=message, scope=scope, scope_id=scope_id, limit=limit) or []
    except Exception:
        return []
    out = []
    for m in items:
        if not isinstance(m, dict):
            continue
        text = str(m.get("content") or "").strip()
        if not text:
            continue
        try:
            sim = float(m.get("sim") or 0.0)
        except (TypeError, ValueError):
            sim = 0.0
        try:
            rank = min(1.0, float(m.get("rank_score") or 0.0))
        except (TypeError, ValueError):
            rank = 0.0
        score = sim if sim > 0 else rank
        out.append((score, text))
    return out


# ── 核心：池子 → 闸门 → 预算 → 块 ──────────────────────────────────

def collect(message, scope="group", scope_id="", ctx=None, sources=("sand", "sm")):
    """返回按分数降序的入选条目 [(score, text), ...]（已过 τ 闸、去重）。

    sources：源开关。群聊接线只取 ("sand",) —— super_memory 群侧已由 EGO
    before_reply 钩子注入【核心记忆】，builder 再取同一源就是双注（2.0 设计稿
    明令消灭的旧账）。私聊 ("sand","sm") 双源；决策 context_req 可指定 ("sm",)。
    ctx 预留给阶段D 语境加权（who/当前话题/是否延续），现阶段仅透传不用。"""
    if not _CFG["enabled"] or not (message or "").strip():
        return []
    lim = int(_CFG["per_source_limit"])
    pool = []
    if "sand" in sources:
        _scope = None
        if scope == "group" and scope_id:
            _scope = ("group", str(scope_id))
        elif scope == "private" and scope_id:
            _scope = ("private", str(scope_id))
        pool += [_sandglass_candidates(message, lim, scope=_scope)]
    if "sm" in sources:
        pool += [_super_mem_candidates(message, scope, scope_id, lim)]
    flat = []
    for part in pool:
        flat += [(s, t) for s, t in part]

    # 去重：跨源同文/包含关系 → 保高分
    kept = {}
    for score, text in flat:
        key = text.strip()
        merged = False
        for exist in list(kept):
            if key == exist or (len(key) > 20 and (key in exist or exist in key)):
                if score > kept[exist][0]:
                    kept[exist] = (score, text)
                merged = True
                break
        if not merged:
            kept[key] = (score, text)
    ranked = sorted(kept.values(), key=lambda x: -x[0])

    # 阈值闸
    tau = float(_CFG["tau"])
    return [(s, t) for s, t in ranked if s >= tau]


def build_block(message, scope="group", scope_id="", ctx=None, sources=("sand", "sm")):
    """产出一个包装块字符串；无可注入条目 → ""（零成本）。"""
    items = collect(message, scope=scope, scope_id=scope_id, ctx=ctx, sources=sources)
    if not items:
        return ""
    width = int(_CFG["width"])
    budget = int(_CFG["budget"])
    max_items = int(_CFG["max_items"])
    used = 0
    lines = []
    for score, text in items:
        line = "- (%.2f) %s" % (min(1.0, score), text[:width])
        if used + len(line) > budget or len(lines) >= max_items:
            break
        lines.append(line)
        used += len(line) + 1
    if not lines:
        return ""
    return _HEAD + "\n" + "\n".join(lines)
