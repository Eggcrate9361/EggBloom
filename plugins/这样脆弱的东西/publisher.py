# -*- coding: utf-8 -*-
"""发布：把蒸馏出的注入卡接到机器人身上。

这是整个蒸馏系统的最后一环 —— 之前所有产出（人格文件、注入卡、账本）
都停在插件自己的目录里，**bot 说话用的还是那份手写 personality.txt**。
没有这一步，蒸馏再好也只是离线实验。

接入方式（不改主程序一行代码）：

  bot_core 的加载链是「EGO 人格引擎优先，personality.txt 兜底」，
  而 EGO 也是插件。所以发布 = 写一份**发布文件**，再由 ego 的
  get_personality() 优先返回它（那一小段在 ego 插件里，见其 __init__.py）。

  关键细节：bot_core 按 **personality.txt 的 mtime** 缓存人设文本，
  所以发布后必须 touch 一下 personality.txt，否则 bot 会一直用旧缓存
  直到重启。这个坑在实现时踩过一次（发布成功但 bot 不刷新）。

发布内容 = **组合，不是替换**：
  蒸馏卡（量化参数 + 例句 + 结论）
  + 手写人设里**模型学不到的硬约束**（【禁忌】【当被问是不是AI】——
    这些是防 AI 腔的规矩，蒸馏语料里没有对应物，丢了会出事）

安全机制：
  - 快照：每次发布把上一份存进 history/（保留最近 5 份），一键回滚
  - 可下线：disable 删除启用标记，bot 立即回到手写人设
  - 发布物自带水印头（来源、目标、时间），出问题能一眼认出来源
"""
import os
import re
import shutil
import time

from .config import get_config
from . import persona_store

PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
PUBLISHED_DIR = os.path.join(PLUGIN_DIR, "data", "published")
HISTORY_DIR = os.path.join(PUBLISHED_DIR, "history")
STATE_PATH = os.path.join(PUBLISHED_DIR, "state.json")

# 手写人设里要**保留**的部分：蒸馏语料推不出来的硬规矩
# （用包含匹配而不是全等——节标题里可能有空格，如「【当被问是不是 AI】」）
PRESERVE_SECTIONS = ("当被问是不是", "禁忌", "绝对不能说")

_PERSONALITY_TXT = os.path.join(os.path.dirname(os.path.dirname(PLUGIN_DIR)),
                                "personality.txt")


def _read(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def _write(path, content):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def latest_target():
    """最近蒸馏过的目标（发布时缺省用它）。"""
    root = persona_store.STORE_DIR
    best, best_t = "", 0
    if os.path.isdir(root):
        for name in os.listdir(root):
            m = os.path.join(root, name, "manifest.json")
            if os.path.exists(m):
                t = os.path.getmtime(m)
                if t > best_t:
                    best, best_t = name, t
    return best


def _preserve_block():
    """从手写人设里抠出要保留的硬约束 section。"""
    txt = _read(_PERSONALITY_TXT)
    if not txt:
        return ""
    out = []
    lines = txt.splitlines()
    cur, keep = None, []
    for line in lines:
        if line.startswith("【"):
            if cur and any(k in cur for k in PRESERVE_SECTIONS):
                out.append((cur, "\n".join(keep).strip()))
            cur, keep = line.strip(), []
        elif cur is not None:
            keep.append(line)
    if cur and any(k in cur for k in PRESERVE_SECTIONS):
        out.append((cur, "\n".join(keep).strip()))
    parts = [f"{head}\n{body}" for head, body in out if body]
    return ("\n\n" + "\n\n".join(parts)) if parts else ""


def _touch_personality():
    """bot_core 按 personality.txt 的 mtime 缓存 —— 必须 touch 才会刷新。

    （实现时踩过的坑：发布成功但 bot 一直说旧人设，就是这个原因。）
    """
    try:
        if os.path.exists(_PERSONALITY_TXT):
            os.utime(_PERSONALITY_TXT, None)
            return True
    except OSError:
        pass
    return False


def publish(target_id=None, enabled=True):
    """把注入卡发布给 bot。返回状态描述。"""
    target = str(target_id or "").strip() or latest_target()
    if not target:
        return {"error": "还没有任何蒸馏产物，先跑一次蒸馏"}
    card = persona_store.load_layer(target, "card")
    if not card.strip():
        return {"error": f"{target} 没有可发布的注入卡，先蒸馏一次"}

    if not enabled:
        return disable()

    composed = (
        f"【蒸馏人格 · 来源：这样脆弱的东西 · 目标 {target} · "
        f"发布于 {time.strftime('%Y-%m-%d %H:%M')}】\n"
        f"（本段由真实聊天记录蒸馏生成；下方保留的【禁忌】等硬约束"
        f"来自手写人设，两者同时生效）\n"
        f"\n{card.strip()}\n"
    )
    preserved = _preserve_block()
    if preserved:
        composed += preserved + "\n"

    # 快照上一份（保留最近 5 份）
    prev = os.path.join(PUBLISHED_DIR, "active.persona.txt")
    if os.path.exists(prev):
        os.makedirs(HISTORY_DIR, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        shutil.copy2(prev, os.path.join(HISTORY_DIR, f"{stamp}.persona.txt"))
        olds = sorted(os.listdir(HISTORY_DIR))
        for old in olds[:-5]:
            try:
                os.remove(os.path.join(HISTORY_DIR, old))
            except OSError:
                pass

    _write(prev, composed)
    # token 用量报告（只警告不截断，用户决定）
    from . import personality_model as _pm
    report = _pm.injection_token_report(composed)

    state = {
        "target_id": target,
        "published_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "bytes": len(composed.encode("utf-8")),
        "tokens": report["tokens"],
        "token_warn": report["warn"],
        "preserved_sections": list(PRESERVE_SECTIONS),
        "enabled": True,
        "source": "persona_store card.md",
    }
    _write(STATE_PATH, __import__("json").dumps(state, ensure_ascii=False, indent=1))

    touched = _touch_personality()
    notes = ["bot 将在下一次用人设时读到新内容（无需重启）" if touched
             else "⚠ 未能 touch personality.txt，bot 可能要重启才生效"]
    if report["warn"]:
        notes.append("⚠ " + report["message"])
    return {"success": True, "target_id": target, "path": prev,
            "bytes": state["bytes"], "tokens": report["tokens"],
            "token_warning": report["message"] if report["warn"] else "",
            "preserved": bool(preserved),
            "bot_cache_refreshed": touched,
            "note": "；".join(notes)}


def disable():
    """下线：删掉启用标记与发布文件，bot 立即回到手写人设。"""
    state = status()
    removed = []
    prev = os.path.join(PUBLISHED_DIR, "active.persona.txt")
    if os.path.exists(prev):
        os.makedirs(HISTORY_DIR, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        shutil.move(prev, os.path.join(HISTORY_DIR, f"disabled-{stamp}.persona.txt"))
        removed.append(prev)
    if os.path.exists(STATE_PATH):
        os.remove(STATE_PATH)
        removed.append(STATE_PATH)
    _touch_personality()
    return {"success": True, "disabled": True, "removed": removed,
            "was": state}


def status():
    """当前发布状态（UI / CLI 都用这份）。"""
    import json
    st = {}
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            st = json.load(f)
    except (OSError, ValueError):
        return {"enabled": False}
    prev = os.path.join(PUBLISHED_DIR, "active.persona.txt")
    st["file_exists"] = os.path.exists(prev)
    st["enabled"] = bool(st.get("enabled")) and st["file_exists"]
    st["history_count"] = len(os.listdir(HISTORY_DIR)) if os.path.isdir(HISTORY_DIR) else 0
    return st


def rollback():
    """回滚到上一份快照（不改变 enabled 状态）。"""
    if not os.path.isdir(HISTORY_DIR):
        return {"error": "没有历史快照可回滚"}
    olds = sorted(f for f in os.listdir(HISTORY_DIR)
                  if f.endswith(".persona.txt") and not f.startswith("disabled-"))
    if not olds:
        return {"error": "没有可回滚的快照"}
    src = os.path.join(HISTORY_DIR, olds[-1])
    prev = os.path.join(PUBLISHED_DIR, "active.persona.txt")
    shutil.copy2(src, prev)
    _touch_personality()
    st = status()
    st["rolled_back_to"] = olds[-1]
    return st
