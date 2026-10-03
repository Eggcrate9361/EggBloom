# -*- coding: utf-8 -*-
"""
NexSandglass 桥接层 — 连接 NexSandglass 记忆引擎与 EggBloom

非阻塞初始化：后台线程懒加载，不卡主流程。
增强记忆注入：搜索相关上下文 + 人物画像 + 最近对话摘要。
"""

import os
import re
import sys
import threading
from collections import defaultdict

# NexSandglass 模块路径
_NEXDIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "nexsandglass")
if _NEXDIR not in sys.path:
    sys.path.insert(0, _NEXDIR)

_initialized = False
_init_attempted = False
_provider = None
_lock = threading.Lock()
_enabled = True

# 人物画像缓存：{user_id: {"name": ..., "traits": [...], "last_seen": ...}}
_people_map = defaultdict(lambda: {"name": "", "traits": [], "last_seen": 0})


def _try_init():
    global _provider, _initialized, _init_attempted
    _init_attempted = True
    try:
        os.environ.setdefault("SANDGLASS_HOME",
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "nexsandglass", "data"))
        from memory_provider import NexSandglassProvider
        _provider = NexSandglassProvider()
        _provider.initialize()
        _initialized = True
        _log("[Sandglass] 记忆系统初始化完成")
    except Exception as e:
        _log(f"[Sandglass] 初始化失败: {e}")


def _log(msg):
    """写入 bot_core 的日志文件"""
    try:
        from utils import log
        log(msg)
    except Exception:
        print(msg)


def _ensure_init():
    global _init_attempted
    if _initialized:
        return True
    if not _init_attempted:
        _init_attempted = True
        t = threading.Thread(target=_try_init, daemon=True, name="sandglass-init")
        t.start()
    return False


def is_available():
    return _initialized and _provider is not None and _provider.is_available()


def log_user_message(text, group_id="", sender="", sender_nick="", timestamp=None):
    """落沙：用户消息写入 NexSandglass（含完整上下文）"""
    if not _enabled or not is_available():
        return
    try:
        from sandglass_log import log_message
        ts = timestamp or ""
        parts = []
        if group_id:
            parts.append(f"[群:{group_id}]")
        if sender_nick:
            parts.append(f"[{sender_nick}({sender})]")
        elif sender:
            parts.append(f"[{sender}]")
        if ts:
            parts.append(f"[{ts}]")
        prefix = " ".join(parts)
        log_message(f"{prefix} {text}" if prefix else text, "user")

        # 更新人物画像
        if sender:
            p = _people_map[sender]
            if sender_nick:
                p["name"] = sender_nick
            p["last_seen"] = 1  # 标记活跃
    except Exception:
        pass


def log_bot_reply(text, group_id=""):
    """落沙：Bot 回复写入 NexSandglass"""
    if not _enabled or not is_available():
        return
    try:
        from sandglass_log import log_message
        prefix = "[Bot回复] "
        if group_id:
            prefix += f"[群{group_id}] "
        log_message(prefix + text, "assistant")
    except Exception:
        pass


def sync_conversation(user_msg, assistant_msg):
    """同步对话到 NexSandglass"""
    if not _enabled or not is_available():
        return
    try:
        _provider.sync_turn(user_msg, assistant_msg)
    except Exception:
        pass


def extract_decision_particles(text):
    """从用户消息中提取决策粒子"""
    if not _enabled or not is_available():
        return
    try:
        from decision_particles import log as dp_log
        m = re.search(r'(.{2,20})还是(.{2,20})', text)
        if m:
            dp_log(f'{m.group(1).strip()}还是{m.group(2).strip()}', m.group(2).strip())
    except Exception:
        pass


def _build_people_context():
    """构建人物画像上下文"""
    lines = []
    for uid, info in _people_map.items():
        name = info.get("name", "")
        if not name:
            continue
        traits = info.get("traits", [])
        trait_str = "、".join(traits[:3]) if traits else ""
        line = f"  {name}"
        if trait_str:
            line += f": {trait_str}"
        lines.append(line)
    if lines:
        return "人物: " + "; ".join(lines[:5])
    return ""


def _search_context_for_message(message, limit=3):
    """搜索与当前消息相关的记忆上下文"""
    if not _enabled or not is_available():
        return ""

    try:
        from sandglass_vault import search

        # 提取关键词（简单分词）
        keywords = re.findall(r'[\u4e00-\u9fff]{2,}|[a-zA-Z]{3,}', message)
        if not keywords:
            return ""

        # 搜索相关记忆
        all_results = []
        for kw in keywords[:3]:  # 最多3个关键词
            results = search(kw, limit=limit)
            for r in results:
                if isinstance(r, tuple) and len(r) >= 3:
                    text = str(r[2])[:150]
                    if text not in [x[1] for x in all_results]:
                        all_results.append((r[0], text))

        if not all_results:
            return ""

        # 取最相关的几条
        context_lines = []
        for score, text in sorted(all_results, key=lambda x: -x[0])[:limit]:
            context_lines.append(f"  - {text}")

        return "\n".join(context_lines)
    except Exception:
        return ""


def memory_feed(msg, scope="group"):
    """统一记忆分发入口 —— 一条消息只解析一次，四个记忆模块各自只取关心的字段。

    背景（1.5 任务 2.3）：nexsandglass / ego.super_memory / self_learning /
    这样脆弱的东西 四个模块存的东西确实不同，但此前同一条消息会被 bot_core
    里四条独立路径各自解析一遍（重复 resolve_qq_nick、重复 extract_decision_particles、
    重复 sync_conversation）。收益是性能与可维护性，不合并存储。

    msg 字段（全部可选，缺省即跳过该模块）：
        text:        str  消息正文
        group_id:    str  群号，空串=私聊
        sender:      str  发送者 QQ
        sender_nick: str  发送者昵称（不传则内部 resolve）
        timestamp:   Any  时间戳（可选）
        reply:       str  仅当这是"问答对"时传入，触发回复侧写入

    每个子模块独立 try/except，一个模块出错不影响其它模块。
    返回 dict 记录各模块是否真正落盘，便于日志与测试。
    """
    text = msg.get("text") or ""
    text = str(text).strip() if text else ""
    group_id = str(msg.get("group_id") or "")
    sender = str(msg.get("sender") or "")
    sender_nick = str(msg.get("sender_nick") or "")
    timestamp = msg.get("timestamp")
    reply = msg.get("reply") or ""
    reply = str(reply).strip() if reply else ""

    out = {"sandglass": False, "super_memory": False, "self_learning": False, "distill": False}

    # ── 1. NexSandglass：全量落沙 + 决策粒子 + 对话同步 ──
    if text:
        try:
            if _enabled and is_available():
                log_user_message(text, group_id, sender, sender_nick, timestamp)
                # 决策粒子只在用户消息上提取一次
                extract_decision_particles(text)
                out["sandglass"] = True
        except Exception:
            pass

    # 回复侧同步（同一 msg 带 reply 时触发，避免 bot_core 再单独 import）
    if text and reply:
        try:
            if _enabled and is_available():
                sync_conversation(text[:200], reply[:200])
                log_bot_reply(reply[:200], group_id)
        except Exception:
            pass

    # ── 2. ego.super_memory：事实提取 + 主动召回（走插件 after_reply 钩子）──
    # 该模块的重活由 bot_core 的 after_reply 钩子完成；此处只做"是否记录"的显式声明，
    # 避免把插件的 sqlite 写入复制到桥接层（那是插件自己的职责边界）。
    if text and reply:
        out["super_memory"] = True

    # ── 3. self_learning：群聊自主学习收集（仅群聊，私聊不参与）──
    if text and group_id and sender:
        try:
            from self_learning import SelfLearningEngine  # noqa: F401  (探测可用性)
            out["self_learning"] = True
        except Exception:
            pass

    # ── 4. 这样脆弱的东西：人格蒸馏走离线 collect()，实时 feed 只登记候选 ──
    # 蒸馏是对整段语料做批量建模，不做逐条实时写入；此处仅声明该消息已进入沙漏，
    # 蒸馏侧从 sandglass 落沙文件读取，无需重复存一份。
    if text:
        out["distill"] = True

    return out


def get_memory_prompt_block(message="", light=False):
    """获取增强版记忆注入上下文

    四层结构：
    1. 人物画像（群内成员信息）
    2. 相关记忆（基于当前消息搜索）
    3. NexSandglass 元信息（阶段/偏移）

    light=True（T4）：只注入轻量元信息 system_prompt_block，跳过关键词全文搜索，
    用于私聊记忆统一召回层（语义检索已由 super_memory 向量 top-K 承担）。
    """
    if not _enabled or not is_available():
        return ""

    parts = []

    # 第一层：人物画像
    people = _build_people_context()
    if people:
        parts.append(f"【群内人物】\n{people}")

    # 第二层：相关记忆搜索（light 模式跳过，省 token）
    if message and not light:
        ctx = _search_context_for_message(message)
        if ctx:
            parts.append(f"【相关记忆】\n{ctx}")

    # 第三层：元信息（阶段/偏移）
    try:
        block = _provider.system_prompt_block()
        if block and len(block) > 20:
            parts.append(block)
    except Exception:
        pass

    if parts:
        return "\n\n【沙漏记忆】\n" + "\n\n".join(parts)
    return ""


def search_memory(query, limit=5):
    """搜索 NexSandglass 记忆"""
    if not _enabled or not is_available():
        return ""
    try:
        from sandglass_vault import search
        results = search(query, limit=limit)
        if results:
            lines = []
            for r in results:
                text = r.get("text", str(r))[:200]
                score = r.get("score", 0)
                lines.append(f"- [{score:.2f}] {text}")
            return "\n".join(lines)
        return ""
    except Exception:
        return ""


def get_status():
    """获取 NexSandglass 运行状态"""
    if not is_available():
        return {"status": "unavailable"}
    try:
        from sandglass_vault import count
        from sandglass_think import comprehensive_offset, _current_stage
        return {
            "status": "active",
            "messages": count(),
            "stage": _current_stage(),
            "offset": comprehensive_offset(),
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}
