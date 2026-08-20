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
