# -*- coding: utf-8 -*-
"""people_alias.py —— 跨场景的「称呼」与「约定」（P0-4 的存储层，2026-10-30）。

## 真机现象（10-29 18:13–18:22）

    18:18 你现在暂时叫我拉普兰德
    18:18 「拉普兰德？行吧，你爱叫啥叫啥」      ← 嘴上答应
    18:18 「等会儿在那边艾特我，我张口就来」    ← 承诺了跨场景行为
    18:22 「唉，你还是不记得呢」                ← 一次都没用过

以及：

    18:13 等会儿在群里戳你，艾特你说"天王盖地虎"，你答"小a炖蘑菇"
    18:13 「行啊，暗号我记住了」
    18:22 暗号测试 → 失败

根因不是模型不听话，是**答应之后没有任何写回动作**：`chat_history` 按会话分键，
群里另起一份，私聊里说的别名与暗号从来没有进入群聊的 prompt。

## 这一层只做两件事

1. 从话里认出「让我叫你 X」「说 A 就答 B」，写进 `data/people_aliases.json`。
2. 把存下来的东西变成一句 prompt（`prompt_line`），私聊群聊都用同一个出口。

过期策略：称呼 7 天、约定 30 天 —— 「暂时」不该变成永久人设，
但也不该明天就忘。过期条目在读取时忽略，写入时顺手清掉。

**不做**（写清楚）：不做语义理解、不猜暗示。只认显式说法，认不出就不存 ——
宁可少记，也别把「别叫我老板」记成「叫我老板」。
"""
from __future__ import annotations

import json
import os
import re
import time
from typing import Dict, List, Optional, Tuple

#: 运行态数据（与 data/people.json 同级）。测试可用 `set_store_path` 改。
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
STORE_NAME = "people_aliases.json"

ALIAS_TTL = 7 * 24 * 3600          # 「暂时叫我 X」的合理寿命
AGREEMENT_TTL = 30 * 24 * 3600
MAX_ALIAS_LEN = 12
MAX_TRIGGER_LEN = 16
MAX_AGREEMENTS = 5

_LOCK = None
_STORE_PATH = os.path.join(DATA_DIR, STORE_NAME)
_MEM: Optional[Dict[str, dict]] = None

# ── 解析 ────────────────────────────────────────────────────────
#: 「（以后/暂时/就）叫我 X」。X 到空白或标点为止，允许被引号包着。
_ALIAS_RE = re.compile(
    r"(?:以后|暂时|暫且|先|就|可以|都)?\s*(?:叫我|喊我|称呼我)\s*"
    r"[「\"“'『]?\s*([^\s，。,.!?！？、；;：「」\"“”'『』]{1,12})"
)
#: 否定：「别叫我 X」「不要叫我 X」—— 命中就不存（方向正好相反）。
_ALIAS_NEG_RE = re.compile(r"(?:别|不要|不用|别在|别再|甭)\s*(?:叫我|喊我|称呼我)")

#: 「说 A，你答 B」（A/B 带引号或明文）
_PAIR_RE = re.compile(
    r"(?:说|讲|喊)\s*[「\"“'『]?\s*([^「」\"“”'『』\s，。,.!?！？]{1,16})\s*[」\"”'』]?"
    r"\s*[，,、]?\s*(?:你|bot|它)?\s*(?:就)?\s*(?:答|说|回|接)\s*"
    r"[「\"“'『]?\s*([^「」\"“”'『』\s，。,.!?！？]{1,16})\s*[」\"”'』]?"
)
#: 「暗号是 X」—— 只知道触发词，回答待定
_SECRET_RE = re.compile(
    r"暗号\s*(?:是|就是|：|:)\s*[「\"“'『]?\s*"
    r"([^\s，。,.!?！？、；;：「」\"“”'『』]{1,16})"
)

_STOP_ALIAS = {"我", "你", "他", "她", "它", "什么", "啥", "个", "嘛", "吧"}


def parse_alias(text: str) -> str:
    """从话里认出「让我叫你 X」。认不出返回空串。

    宁可漏判：`别叫我老板` 是明确的反向意愿，绝不能变成「称呼=老板」。
    """
    t = str(text or "").strip()
    if not t or _ALIAS_NEG_RE.search(t):
        return ""
    m = _ALIAS_RE.search(t)
    if not m:
        return ""
    name = m.group(1).strip().strip("「」\"“”'『』")
    # 句尾语气词不是名字的一部分（「叫我小a吧」→ 小a）
    while len(name) > 1 and name[-1] in "吧啊呀哦呢啦嘛哈":
        name = name[:-1]
    if not name or name in _STOP_ALIAS:
        return ""
    if len(name) > MAX_ALIAS_LEN:
        return ""
    return name


def parse_agreement(text: str) -> Tuple[str, str]:
    """认出「说 A 就答 B」或「暗号是 X」。返回 (触发词, 回答)；没有返回 ("","")."""
    t = str(text or "").strip()
    if not t:
        return "", ""
    m = _PAIR_RE.search(t)
    if m:
        trig = m.group(1).strip().strip("「」\"“”'『』")
        reply = m.group(2).strip().strip("「」\"“”'『』")
        if trig and reply and trig != reply and len(trig) <= MAX_TRIGGER_LEN:
            return trig, reply
    m = _SECRET_RE.search(t)
    if m:
        trig = m.group(1).strip().strip("「」\"“”'『』")
        if trig and len(trig) <= MAX_TRIGGER_LEN:
            return trig, ""
    return "", ""


# ── 存储 ────────────────────────────────────────────────────────
def _lock():
    global _LOCK
    if _LOCK is None:
        import threading
        _LOCK = threading.Lock()
    return _LOCK


def set_store_path(path: str) -> None:
    """换存储文件（测试用）。会清内存缓存。"""
    global _STORE_PATH, _MEM
    _STORE_PATH = str(path)
    _MEM = None


def store_path() -> str:
    return _STORE_PATH


def _load() -> Dict[str, dict]:
    global _MEM
    if _MEM is not None:
        return _MEM
    d: Dict[str, dict] = {}
    try:
        with open(_STORE_PATH, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if isinstance(raw, dict):
            for k, v in raw.items():
                if isinstance(v, dict):
                    d[str(k)] = v
    except Exception:
        d = {}
    _MEM = d
    return d


def _save() -> bool:
    d = _load()
    tmp = _STORE_PATH + ".tmp"
    try:
        os.makedirs(os.path.dirname(_STORE_PATH) or ".", exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=1)
        try:
            os.replace(tmp, _STORE_PATH)
        except Exception:
            import shutil
            shutil.copyfile(tmp, _STORE_PATH)
            os.remove(tmp)
        return True
    except Exception:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass
        return False


def _prune(rec: dict, now: float) -> dict:
    """丢掉过期项（读的时候顺手清，写的时候再落盘）。"""
    alias_at = float(rec.get("alias_at") or 0)
    if rec.get("alias") and alias_at and (now - alias_at) > ALIAS_TTL:
        rec.pop("alias", None)
        rec.pop("alias_at", None)
    ags = []
    for a in (rec.get("agreements") or []):
        if not isinstance(a, dict):
            continue
        at = float(a.get("at") or 0)
        if at and (now - at) > AGREEMENT_TTL:
            continue
        ags.append(a)
    if ags:
        rec["agreements"] = ags[-MAX_AGREEMENTS:]
    else:
        rec.pop("agreements", None)
    return rec


def get_alias(qq, *, now: Optional[float] = None) -> str:
    n = time.time() if now is None else float(now)
    rec = _load().get(str(qq or ""))
    if not rec:
        return ""
    rec = _prune(dict(rec), n)
    alias = str(rec.get("alias") or "").strip()
    if not alias:
        return ""
    if float(rec.get("alias_at") or 0) and n - float(rec.get("alias_at")) > ALIAS_TTL:
        return ""
    return alias


def set_alias(qq, alias: str, *, now: Optional[float] = None) -> bool:
    q = str(qq or "").strip()
    a = str(alias or "").strip()
    if not q or not a or len(a) > MAX_ALIAS_LEN:
        return False
    n = time.time() if now is None else float(now)
    with _lock():
        d = _load()
        rec = _prune(dict(d.get(q) or {}), n)
        rec["alias"] = a
        rec["alias_at"] = n
        d[q] = rec
        return _save()


def agreements_for(qq, *, now: Optional[float] = None) -> List[dict]:
    n = time.time() if now is None else float(now)
    rec = _load().get(str(qq or ""))
    if not rec:
        return []
    rec = _prune(dict(rec), n)
    return [a for a in (rec.get("agreements") or []) if isinstance(a, dict)]


def add_agreement(qq, trigger: str, reply: str = "",
                  *, now: Optional[float] = None) -> bool:
    q = str(qq or "").strip()
    trig = str(trigger or "").strip()
    if not q or not trig or len(trig) > MAX_TRIGGER_LEN:
        return False
    n = time.time() if now is None else float(now)
    with _lock():
        d = _load()
        rec = _prune(dict(d.get(q) or {}), n)
        ags = [a for a in (rec.get("agreements") or [])
               if str(a.get("trigger") or "") != trig]
        ags.append({"trigger": trig, "reply": str(reply or "").strip()[:MAX_TRIGGER_LEN],
                    "at": n})
        rec["agreements"] = ags[-MAX_AGREEMENTS:]
        d[q] = rec
        return _save()


def prompt_line(qq, *, now: Optional[float] = None) -> str:
    """喂给模型的称呼/约定说明。没有就返回空串（不占 token）。"""
    alias = get_alias(qq, now=now)
    ags = agreements_for(qq, now=now)
    parts = []
    if alias:
        parts.append(f"他让你叫他「{alias}」——就用这个称呼，别叫成别的。")
    for a in ags:
        trig = str(a.get("trigger") or "")
        rep = str(a.get("reply") or "")
        if trig and rep:
            parts.append(f"他说「{trig}」你就答「{rep}」（这是你们约好的，别忘）。")
        elif trig:
            parts.append(f"他提过一句暗号「{trig}」，但没说你要怎么答 —— 别自己编回答。")
    if not parts:
        return ""
    return "【称呼与约定】" + "".join(parts)


__all__ = ["DATA_DIR", "STORE_NAME", "ALIAS_TTL", "AGREEMENT_TTL",
           "parse_alias", "parse_agreement", "set_store_path", "store_path",
           "get_alias", "set_alias", "agreements_for", "add_agreement", "prompt_line"]
