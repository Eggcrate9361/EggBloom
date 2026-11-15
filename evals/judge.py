# -*- coding: utf-8 -*-
"""evals/judge.py —— 机械判分（零成本、可复现、不调模型）

判据是**数据**，不是代码：

    evals/rules.json      词表 / 配额 / 分类规则（改文件即生效，按 mtime 缓存）
    配置中心 eval_* 系列   开关与数字上限（bot_config.json，缺省见 DEFAULT_CONFIG）

之前这套词表写死在 Python 里，改一个禁用词要动代码、还要发版 —— 用户原话
「完全就是一坨硬编码」。现在 judge.py 只负责**怎么判**，不负责**判什么**：

    · 词表来自 rules.json，按组挂开关（关掉 = 这组不判，不是删词）
    · 数字上限来自配置中心，取「分类预算」与「全局配置」中更严的那个
    · 开关全关时判分退化为"只数条数"，不会静默全绿（run_eval 会把开关打出来）

口径来源：桌面 `FIXPLAN-1.5.11.md` §2 场景分类与机械判据、§8 总验收。
"""
import io
import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import reply_budget  # noqa: E402

RULES_PATH = os.path.join(_HERE, "rules.json")

#: 配置中心的默认值 —— 与 config.DEFAULT_CONFIG 必须一致（tests/test_eval_config.py 会查）
DEFAULTS = {
    "eval_enabled": True,
    "eval_ban_ai_self": True,
    "eval_ban_backstage": True,
    "eval_require_colloquial": True,
    "eval_strict_budget": True,
    "eval_live_enable": False,
    "eval_max_chars": 16,
    "eval_max_parts": 3,
    "eval_max_total": 80,
    "eval_extra_banned": "",
}

_RULES_CACHE = {"key": None, "data": None}
_CFG_CACHE = {"cfg": None}


def load_rules(path=RULES_PATH):
    """读 rules.json（按 mtime 缓存）。读不到就**直接抛** —— 判据缺失时必须响。"""
    try:
        mtime = os.path.getmtime(path)
    except OSError as e:
        raise RuntimeError(f"评测判据缺失：{path}（{e}）") from e
    key = f"{path}|{mtime}"
    if _RULES_CACHE["key"] == key and _RULES_CACHE["data"] is not None:
        return _RULES_CACHE["data"]
    with io.open(path, encoding="utf-8") as f:
        data = json.load(f)
    _RULES_CACHE["key"] = key
    _RULES_CACHE["data"] = data
    return data


def effective_config(cfg=None):
    """eval_* 的有效取值：DEFAULTS ← config.load_config() ← 显式传入。"""
    out = dict(DEFAULTS)
    if cfg is None:
        if _CFG_CACHE["cfg"] is not None:
            return _CFG_CACHE["cfg"]
        try:
            import config as _cfg_mod
            loaded = _cfg_mod.load_config() or {}
        except Exception:
            loaded = {}
        # 逐个键显式读取 —— 别改成 `for k in DEFAULTS` 的循环：
        # scripts/audit_config_parity.py 的「有键必有读取点」是静态扫描，
        # 循环里读的是变量不是字面量，会被判成"配了不生效"。
        _read = {
            "eval_enabled": loaded.get("eval_enabled"),
            "eval_ban_ai_self": loaded.get("eval_ban_ai_self"),
            "eval_ban_backstage": loaded.get("eval_ban_backstage"),
            "eval_require_colloquial": loaded.get("eval_require_colloquial"),
            "eval_strict_budget": loaded.get("eval_strict_budget"),
            "eval_live_enable": loaded.get("eval_live_enable"),
            "eval_max_chars": loaded.get("eval_max_chars"),
            "eval_max_parts": loaded.get("eval_max_parts"),
            "eval_max_total": loaded.get("eval_max_total"),
            "eval_extra_banned": loaded.get("eval_extra_banned"),
        }
        for k, v in _read.items():
            if v is not None:
                out[k] = v
        _CFG_CACHE["cfg"] = out
        return out
    for k, v in (cfg or {}).items():
        if k in DEFAULTS and v is not None:
            out[k] = v
    return out


def reset_cache():
    """测试用：清掉 rules / config / reply_budget 的缓存。"""
    _RULES_CACHE["key"] = None
    _RULES_CACHE["data"] = None
    _CFG_CACHE["cfg"] = None


def _split_words(s):
    return [w.strip() for w in re.split(r"[,，、;；\s]+", str(s or "")) if w.strip()]


def _group_words(rules, cfg):
    """按开关挑出全局禁用词。"""
    words = []
    used = []
    for gname, group in (rules.get("groups") or {}).items():
        if gname.startswith("_") or not isinstance(group, dict):
            continue
        switch = group.get("switch")
        if switch and not bool(cfg.get(switch, True)):
            continue
        ws = group.get("words") or []
        words.extend(ws)
        used.append(gname)
    return words, used


def _cat_banned(rules, rules_cat, cfg):
    """分类禁用词：直接写的 + 从公共词表引用的。"""
    banned = list(rules_cat.get("banned") or [])
    ref = rules_cat.get("banned_from")
    if ref:
        banned.extend(rules.get(ref) or [])
    return banned


def judge(text, case, cfg=None):
    """对一个回复判分。

    返回 {"ok": bool, "failures": [...], "stats": {...}, "skipped": bool}
    """
    text = str(text or "")
    cfg = effective_config(cfg)
    rules = load_rules()

    if not bool(cfg.get("eval_enabled", True)):
        return {"ok": True, "failures": [], "stats": {}, "skipped": True}

    expect = (case or {}).get("expect") or {}
    cat = (case or {}).get("category") or ""
    rules_cat = (rules.get("categories") or {}).get(cat) or {}
    failures = []

    # 1. 预算（与线上同一套口径：按行切气泡，控制标记不计字数）
    budget = dict(rules.get("budget") or {})
    budget.update(rules_cat.get("budget") or {})
    budget.update(expect.get("budget") or {})
    if bool(cfg.get("eval_strict_budget", True)) and budget:
        # 配置中心的数字取"更严"的一层；再往下 reply_budget 自己还有硬上限兜底
        for ck, bk in (("eval_max_chars", "max_chars"),
                       ("eval_max_parts", "parts_max"),
                       ("eval_max_total", "max_total")):
            try:
                v = int(cfg.get(ck) or 0)
            except (TypeError, ValueError):
                v = 0
            if v > 0:
                budget[bk] = min(int(budget.get(bk) or v), v)
        ok, why, stats = reply_budget.check(text, budget)
        if not ok:
            failures.append(f"长度/气泡：{why}")
    else:
        stats = {}

    vis = reply_budget.visible(text)
    if not vis.strip():
        failures.append("空回复（被过滤干净了）")
        return {"ok": False, "failures": failures, "stats": stats, "skipped": False}

    # 2. 禁用词：全局开关组 + 分类 + 用例 + 配置中心追加
    gwords, _used = _group_words(rules, cfg)
    banned = list(gwords) + _cat_banned(rules, rules_cat, cfg) + \
        list(expect.get("banned") or []) + _split_words(cfg.get("eval_extra_banned"))
    low = text.lower()
    for word in banned:
        if word and word.lower() in low:
            failures.append(f"禁用词：{word}")

    # 3. 禁用正则：分类 + 用例
    for pat in list(rules_cat.get("banned_regex") or ()) + list(expect.get("banned_regex") or ()):
        try:
            if re.search(pat, text):
                failures.append(f"命中禁用正则：{pat}")
        except re.error as e:
            failures.append(f"正则写错：{pat}（{e}）")

    # 4. 必含（至少一个）
    any_of = expect.get("any_of") or ()
    if any_of and not any(str(k).strip() and str(k) in text for k in any_of):
        failures.append("没接住上下文（缺关键词：" + "/".join(any_of) + "）")

    # 5. 行首时间锚
    anchor = rules.get("time_anchor") or ""
    if anchor:
        try:
            pat = re.compile(anchor)
            for line in text.split("\n"):
                if pat.search(line):
                    failures.append("抄了行首时间锚：" + line.strip()[:16])
                    break
        except re.error as e:
            failures.append(f"时间锚正则写错：{e}")

    # 6. 长度下限
    min_chars = int(expect.get("min_chars") or 0)
    if min_chars and len(vis) < min_chars:
        failures.append(f"过短：{len(vis)} 字 < {min_chars}")

    # 7. 语气类：书面语 / 口语痕迹（开关 eval_require_colloquial）
    if rules_cat.get("need_colloquial") and bool(cfg.get("eval_require_colloquial", True)):
        bookish = rules.get(rules_cat.get("bookish_from") or "bookish") or []
        hit = [w for w in bookish if w in text]
        if hit:
            failures.append(f"书面语/客服腔：{hit[0]}")
        elif len(vis) > 12 and not any(k in text for k in (rules.get("colloquial") or [])):
            failures.append("没有口语痕迹（像书面语/说明书）")

    return {"ok": not failures, "failures": failures, "stats": stats, "skipped": False}


def summary(results):
    """results: [(case, judged, ...), ...] → 按类汇总的计数（多余字段忽略）。"""
    by_cat = {}
    for item in results:
        case, res = item[0], item[1]
        c = case.get("category", "?")
        slot = by_cat.setdefault(c, {"n": 0, "ok": 0})
        slot["n"] += 1
        slot["ok"] += 1 if res["ok"] else 0
    return by_cat
