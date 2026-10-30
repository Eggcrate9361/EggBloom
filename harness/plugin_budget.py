# -*- coding: utf-8 -*-
"""harness/plugin_budget.py —— 插件注入的排序与预算（1.5.10 F3/F4，2026-10-30）。

## 修的是什么

`before_reply` 收集到的 `{"action": "inject"}` 以前是这样拼的：

    inject_parts.sort(key=lambda x: x[0])        # 只看 priority
    merged_inject = "\\n".join(part for _, part in inject_parts)

两个后果，都是线上真出现过的形态：

1. **没有上限**。一个插件话多（一条搜索结果、一段记忆）就能把消息尾撑到几百字，
   prompt 变贵、模型开始按条目交付（汇报腔）。
2. **顺序不稳定**。`sort` 只对 priority 一个键，同优先级插件之间取决于
   `_hook_candidates` 的注册顺序 —— 同一段对话换个加载顺序就换一种拼法。
   前缀缓存命中和"复现一个坏回复"都依赖这个顺序。

## F4：排序

先按**来源类别**（决策 > 人格 > 状态 > 记忆 > 术语 > 搜索 > 其它），
同类别再按 manifest 的 priority，最后按插件名兜底 —— 三元组全序，
同样的输入永远拼出同样的字符串。

## F3：预算

    plugin_inject_max_chars    80    一次回复里，所有插件注入**合计**上限（字）
    plugin_inject_daily_chars  600   每个插件**每天**累计上限（字，本地日期）

为什么两个维度不一样：单条是"prompt 体积"的闸，天然是合计的；
每天是"防某个插件刷屏"的闸，天然是按插件分开算的。两条都设 0 = 不限。

超出预算**不静默丢**：截断到句末/换行处，整段放不下就整段跳过，
两条路都会进 report，由调用方记一行日志。静默丢内容是本仓库最难查的 bug 形态。

## 为什么每天那份账本要落盘

bot 一天重启七八次（实测 10-29 有 9 次）。只放内存的"每天预算"每次重启清零，
等于没有。账本落 `data/plugin_budget.json`，按本地日期自动换天。

纯逻辑 + 一个小 JSON 账本，不 import bot_core，可单测。
"""
import io
import json
import os
import re
import time
from typing import Dict, List, Optional, Tuple

#: 单条注入的总字数上限（配置项 `plugin_inject_max_chars` 覆盖）。0 = 不限。
DEFAULT_MAX_CHARS = 80

#: 单个插件每天累计上限（配置项 `plugin_inject_daily_chars` 覆盖）。0 = 不限。
DEFAULT_DAILY_CHARS = 600

#: 截断后剩这么少就当没这段 —— 半句话比没有更糟（模型会去圆它）。
MIN_KEEP_CHARS = 8

#: 来源类别固定档位（数值小 = 先占预算）。不在表里的插件用 DEFAULT_RANK。
SOURCE_RANK = {
    # 现场/决策：这一轮该不该说、说什么，最该先占位置
    "group_chat_decider": 10,
    # 身份：谁在说话
    "ego": 20,
    "personality_engine": 20,
    # 语气/节奏/状态：怎么说
    "chat_style": 30,
    "humanlike_rhythm": 30,
    "mood_engine": 30,
    "vitals": 30,
    # 记忆与画像
    "super_memory": 40,
    "sandglass": 45,
    # 术语/网络用语
    "slang_learner": 50,
    # 外部资料：最后
    "web_search": 60,
    "web_operator": 60,
    "qzone": 60,
}
DEFAULT_RANK = 55

#: 空白不计字数（注入内容是给模型读的，"字"按可见字符算）。
_WS = re.compile(r"\s+")

#: 截断点：优先切在这些字符**之后**（句子/段落边界）。
_CUT_AFTER = "。！？；\n.!?;:：、，, "


# ── F4：排序 ────────────────────────────────────────────────────────
def rank_of(name: str) -> int:
    return SOURCE_RANK.get(str(name or ""), DEFAULT_RANK)


def sort_key(name: str, priority: int = 50) -> Tuple[int, int, str]:
    """全序键：(类别档, manifest priority, 插件名)。

    ★ 第三个键不是装饰：两个插件同类别同 priority 时，没有它顺序就仍取决于
      注册顺序 —— 那正是这次要消灭的不确定性。
    """
    try:
        pri = int(priority)
    except Exception:
        pri = 50
    return (rank_of(name), pri, str(name or ""))


def order(items: List[Tuple[str, int, str]]) -> List[Tuple[str, int, str]]:
    """[(name, priority, text)] → 按 sort_key 排好的新列表（不改入参）。"""
    return sorted(list(items or []), key=lambda it: sort_key(it[0], it[1]))


# ── 字数 ────────────────────────────────────────────────────────────
def text_len(s: str) -> int:
    """可见字数：去掉所有空白。"""
    return len(_WS.sub("", str(s or "")))


def clip(s: str, limit: int) -> Tuple[str, bool]:
    """把文本裁到 `limit` 可见字以内，尽量切在句子/段落边界。

    返回 (文本, 是否发生过截断)。裁不出 MIN_KEEP_CHARS 的调用方应当整段丢掉。
    """
    t = str(s or "")
    limit = int(limit)
    if limit <= 0 or text_len(t) <= limit:
        return t, False
    # 先按可见字数走一遍，记住"到第 limit 个可见字"落在原串的哪个位置
    seen = 0
    cut = len(t)
    for i, ch in enumerate(t):
        if ch.isspace():
            continue
        seen += 1
        if seen > limit:
            cut = i
            break
    head = t[:cut]
    # 往回找一个边界切：太靠前就不要了（宁愿不切在句子中间）
    best = -1
    for i in range(len(head) - 1, max(-1, len(head) - 31), -1):
        if head[i] in _CUT_AFTER:
            best = i
            break
    if best >= 0 and text_len(head[:best + 1]) >= MIN_KEEP_CHARS:
        return head[:best + 1].rstrip(), True
    return head.rstrip(), True


# ── 配置 ────────────────────────────────────────────────────────────
def _as_int(v, default: int) -> int:
    try:
        n = int(v)
    except (TypeError, ValueError):
        return default
    return n if n >= 0 else default


def config_of(cfg) -> Dict[str, object]:
    """从配置字典取预算参数。缺键/脏值都退回默认，绝不让预算把注入搞崩。"""
    cfg = cfg if isinstance(cfg, dict) else {}

    def _get(key, default):
        try:
            return cfg.get(key, default)
        except Exception:
            return default

    return {
        "enabled": bool(_get("plugin_inject_budget_enabled", True)),
        "max_chars": _as_int(_get("plugin_inject_max_chars", DEFAULT_MAX_CHARS),
                             DEFAULT_MAX_CHARS),
        "daily_chars": _as_int(_get("plugin_inject_daily_chars", DEFAULT_DAILY_CHARS),
                               DEFAULT_DAILY_CHARS),
    }


# ── 账本 ────────────────────────────────────────────────────────────
def default_path() -> str:
    """`data/plugin_budget.json`。data 目录拿不到就退回程序目录（仍可用）。

    ★ 2026-10-30：先认环境变量 `EGGBLOOM_PLUGIN_BUDGET`。
      测试会跑大量 `_plan_plugin_inject`，以前直接写生产账本 —— 实测一天下来
      ego 594/600 被测试刷满，随后 `test_private_profile` 因为"注入被预算丢掉"
      假红。测试入口 `tests/_sandbox.py` 现在把它指到临时目录。
    """
    env = os.environ.get("EGGBLOOM_PLUGIN_BUDGET")
    if env:
        return env
    try:
        from data_store import get_data_dir
        return os.path.join(str(get_data_dir()), "plugin_budget.json")
    except Exception:
        return os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "data", "plugin_budget.json")


def _today(now: Optional[float] = None) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(time.time() if now is None else now))


class Ledger:
    """每个插件当天的累计注入字数。跨重启累计，换天自动清零。

    写失败绝不抛：预算是省钱的闸，不该因为磁盘问题把回复拦住。
    """

    def __init__(self, path: Optional[str] = None, now: Optional[float] = None):
        self.path = str(path or default_path())
        self.date = _today(now)
        self.used: Dict[str, int] = {}
        self._dirty = False
        self.load()

    def load(self) -> None:
        try:
            with io.open(self.path, encoding="utf-8") as f:
                rec = json.load(f)
        except Exception:
            return
        if not isinstance(rec, dict):
            return
        if str(rec.get("date") or "") != self.date:
            return                      # 隔天的账本 = 空账本
        used = rec.get("used")
        if isinstance(used, dict):
            for k, v in used.items():
                try:
                    self.used[str(k)] = max(0, int(v))
                except (TypeError, ValueError):
                    continue

    def roll(self, now: Optional[float] = None) -> bool:
        """换天检查。返回是否发生了换天（True = 账本已清空）。"""
        d = _today(now)
        if d == self.date:
            return False
        self.date, self.used, self._dirty = d, {}, True
        return True

    def used_today(self, name: str) -> int:
        return int(self.used.get(str(name), 0))

    def remaining(self, name: str, daily: int) -> int:
        """今天还能注入多少字。`daily <= 0` 表示不限（返回 -1）。"""
        daily = int(daily or 0)
        if daily <= 0:
            return -1
        return max(0, daily - self.used_today(name))

    def charge(self, name: str, n: int) -> None:
        n = int(n or 0)
        if n <= 0:
            return
        k = str(name)
        self.used[k] = self.used_today(k) + n
        self._dirty = True

    def save(self) -> bool:
        if not self._dirty:
            return False
        try:
            d = os.path.dirname(self.path)
            if d:
                os.makedirs(d, exist_ok=True)
            tmp = self.path + ".tmp"
            with io.open(tmp, "w", encoding="utf-8") as f:
                json.dump({"date": self.date, "used": self.used}, f,
                          ensure_ascii=False, indent=1)
            os.replace(tmp, self.path)
            self._dirty = False
            return True
        except Exception:
            return False


# ── 预算分配 ────────────────────────────────────────────────────────
def plan(items: List[Tuple[str, int, str]], budget: Dict[str, object],
         ledger: Optional[Ledger] = None,
         now: Optional[float] = None) -> Tuple[str, Dict[str, object]]:
    """按 F4 排好序的注入项 → 预算内的合并文本 + 报告。

    `items` 是 `[(name, priority, text)]`（`order()` 的输出，顺序即优先级）。
    未启用预算时原样拼起来（但仍然排序 —— 顺序与预算无关）。

    报告：
        total     实际注入的可见字数
        kept      [(name, 字数)]
        dropped   [(name, 原因)]
        trimmed   [(name, 原字数, 新字数)]
    """
    budget = budget if isinstance(budget, dict) else {}
    enabled = bool(budget.get("enabled", True))
    max_chars = int(budget.get("max_chars") or 0)
    daily_chars = int(budget.get("daily_chars") or 0)
    if ledger is not None:
        ledger.roll(now)

    kept: List[str] = []
    rep: Dict[str, object] = {"total": 0, "kept": [], "dropped": [], "trimmed": []}
    left_msg = max_chars if (enabled and max_chars > 0) else -1

    for name, _priority, text in (items or []):
        body = str(text or "").strip()
        if not body:
            continue
        n = text_len(body)
        allowed = -1
        if enabled:
            day_left = -1
            if ledger is not None and daily_chars > 0:
                day_left = ledger.remaining(name, daily_chars)
            lefts = [x for x in (left_msg, day_left) if x >= 0]
            allowed = min(lefts) if lefts else -1
            # ★ 剩余额度连一句话都放不下（< MIN_KEEP_CHARS）时就当"用完"：
            #   硬塞半句话比不注入更糟 —— 模型会去圆那半句。
            if 0 <= allowed < MIN_KEEP_CHARS:
                _tight_day = day_left >= 0 and (left_msg < 0 or day_left <= left_msg)
                rep["dropped"].append(
                    (name, "今日预算用完" if _tight_day else "单条预算用完"))
                continue

        cut = body
        trimmed = False
        if allowed > 0 and n > allowed:
            cut, trimmed = clip(body, allowed)
            if text_len(cut) < MIN_KEEP_CHARS:
                rep["dropped"].append((name, "截断后过短"))
                continue

        used = text_len(cut)
        kept.append(cut)
        rep["kept"].append((name, used))
        rep["total"] = int(rep["total"]) + used
        if trimmed:
            rep["trimmed"].append((name, n, used))
        if left_msg > 0:
            left_msg = max(0, left_msg - used)
        if ledger is not None and daily_chars > 0:
            ledger.charge(name, used)

    return "\n".join(kept), rep


def describe(rep: Dict[str, object]) -> str:
    """一行日志。没丢没截就返回空串（调用方据此决定要不要记）。"""
    rep = rep if isinstance(rep, dict) else {}
    dropped = rep.get("dropped") or []
    trimmed = rep.get("trimmed") or []
    if not dropped and not trimmed:
        return ""
    bits = []
    if trimmed:
        bits.append("截断 " + "、".join(f"{n}:{a}→{b}" for n, a, b in trimmed))
    if dropped:
        bits.append("跳过 " + "、".join(f"{n}({r})" for n, r in dropped))
    return f"[插件预算] 共 {rep.get('total', 0)} 字｜" + "；".join(bits)


__all__ = ["DEFAULT_MAX_CHARS", "DEFAULT_DAILY_CHARS", "MIN_KEEP_CHARS",
           "SOURCE_RANK", "DEFAULT_RANK", "rank_of", "sort_key", "order",
           "text_len", "clip", "config_of", "default_path", "Ledger", "plan",
           "describe"]
