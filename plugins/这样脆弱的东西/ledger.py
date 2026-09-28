# -*- coding: utf-8 -*-
"""人格账本：让蒸馏变成连续的，而不是一次性的。

## 为什么需要它

一次性蒸馏有三个治不好的毛病：

1. **重复烧钱**。每次重跑都把历史语料重新喂一遍，而上游只有 3 请求/分钟。
2. **认识会丢**。跑完就覆盖文件，上一轮辛苦建立的判断，除了在备份里躺着，
   对下一轮没有任何影响。
3. **不会成长**。同一句话被不同时间点反复确认，文件上看不出"这条越来越硬了"；
   反过来，一条早就过时的判断也不会自然退场。

账本把这三件事分开：

    语料 ──摄取──> 水位（见过哪些消息）
    结论 ──累积──> 条目（每条有生老病死）
    每轮 ──追加──> 日志（只增不删）

**人格文件是账本的快照，可以随时重印；账本本身只增不删（只退休）。**

## 条目的状态机

    (新观察) ──> active ──被新证据确认──> active（确认次数 +1，置信度上调）
                  │
                  ├─ 连续 N 轮没被提及 ──> suspect（存疑）──> dormant（沉眠）
                  │                                    │
                  ├─ 被反证（反证更强）──> retired（已推翻）
                  └─ suspect/dormant/retired 再被强证据确认 ──> active（复活）

**「沉默」不等于「错了」。** 一个人这段时间没出现，不代表他变了 ——
所以沉默只能把条目推进到 suspect / dormant，**永远不能因为它 retired**。
只有反证才能推翻一条结论。这是这个账本最重要的一条规矩。

## 谁来决定什么

- LLM：只负责"这波新数据说明了什么"（产出观察条目，带证据与反例）
- 账本：负责"这对已有结论意味着什么"（状态迁移、置信度演化、历史记账）

这样分开之后，人格的演化过程是**确定性、可审计**的，而不是又一次黑箱生成。
"""
import copy
import datetime
import json
import os
from collections import Counter

from .config import DATA_DIR, get_config
from .evidence import fingerprint   # 消息身份与证据层共用一套定义

LEDGER_DIR = os.path.join(DATA_DIR, "ledger")
os.makedirs(LEDGER_DIR, exist_ok=True)

ACTIVE, SUSPECT, DORMANT, RETIRED = "active", "suspect", "dormant", "retired"

STATE_LABEL = {
    ACTIVE: "活跃",
    SUSPECT: "存疑",
    DORMANT: "沉眠",
    RETIRED: "已推翻",
}
STATE_ICON = {ACTIVE: "●", SUSPECT: "◐", DORMANT: "○", RETIRED: "✕"}

# 事件 → 中文（渲染与日志共用）
EVENT_LABEL = {
    "created": "新增",
    "confirmed": "确认",
    "revised": "改写",
    "contradicted": "遇到反证",
    "weakened": "削弱",
    "revived": "复活",
    "suspect": "转为存疑",
    "dormant": "转入沉眠",
    "retired": "推翻",
}


# ── 基础 ────────────────────────────────────────────────

def _path(target_id):
    safe = "".join(ch for ch in str(target_id) if ch.isalnum() or ch in "-_") or "unknown"
    return os.path.join(LEDGER_DIR, f"{safe}.json")


def _now():
    return datetime.datetime.now().isoformat(timespec="seconds")


def load(target_id):
    """读账本。不存在就返回一个空账本（不落盘）。"""
    p = _path(target_id)
    if os.path.exists(p):
        try:
            with open(p, "r", encoding="utf-8") as f:
                led = json.load(f)
            led.setdefault("entries", {})
            led.setdefault("journal", [])
            led.setdefault("seen", {"count": 0, "fp": []})
            led.setdefault("mid_of_fp", {})
            led.setdefault("quotes", {})
            led.setdefault("next_mid", 1)
            return led
        except Exception:
            pass
    return _empty(target_id)


def _empty(target_id):
    return {
        "target_id": str(target_id),
        "target_name": "",
        "created_at": _now(),
        "updated_at": _now(),
        "runs": 0,
        "level": "",
        "entries": {},
        "journal": [],
        "seen": {"count": 0, "fp": []},
        "mid_of_fp": {},
        "next_mid": 1,
        "quotes": {},
    }


def save(led):
    led["updated_at"] = _now()
    p = _path(led.get("target_id"))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(led, f, ensure_ascii=False, indent=2)
    return p


def exists(target_id):
    return os.path.exists(_path(target_id))


# ── 水位：这一轮到底有没有新东西 ────────────────────────

def split_delta(led, raw_messages):
    """把原始记录分成「已收录」和「本轮新增」。

    返回 (delta, total, first_run)：
      delta     本轮新增的原始记录（首次运行 = 全部）
      total     原始记录总数
      first_run 账本里还没有任何水位
    """
    known = led.get("mid_of_fp") or {}
    raw_messages = raw_messages or []
    if not known:
        return list(raw_messages), len(raw_messages), True
    delta = [m for m in raw_messages if fingerprint(m) not in known]
    return delta, len(raw_messages), False


def _remember_map(led, id_map=None, quotes=None, cap=None):
    """把这一轮新增的水位并进账本。

      mid_of_fp  指纹 → 编号      决定"这条吃过没有"和"编号跨轮稳定"
      quotes     编号 → 原文引证  让历史引用在任何一轮都能渲染出原话

    超过上限（默认 20 万条）就从最早的开始丢。丢掉只意味着很久以后再看到
    会被当成新消息再收一次，无害。
    """
    cap = int(cap or get_config().get("ledger_max_fingerprints", 200000))
    mid_of_fp = dict(led.get("mid_of_fp") or {})
    for fp, mid in (id_map or {}).items():
        if fp and mid and fp not in mid_of_fp:
            mid_of_fp[fp] = mid
    if len(mid_of_fp) > cap:
        keys = list(mid_of_fp.keys())[-cap:]
        mid_of_fp = {k: mid_of_fp[k] for k in keys}
    led["mid_of_fp"] = mid_of_fp
    led["seen"] = {"count": len(mid_of_fp)}

    merged_quotes = dict(led.get("quotes") or {})
    for mid, q in (quotes or {}).items():
        merged_quotes.setdefault(mid, q)
    if len(merged_quotes) > cap:
        merged_quotes = dict(list(merged_quotes.items())[-cap:])
    led["quotes"] = merged_quotes

    numeric = [int(k[1:]) for k in mid_of_fp.values() if k[1:].isdigit()]
    led["next_mid"] = (max(numeric) + 1) if numeric else 1


def known_index(led, current=None):
    """把账本的引证表和本轮索引合并成一个可校验的编号表。

    没有这一步，增量模式会出大问题：模型看到旧结论里写着 M0007，
    可 M0007 不在本轮语料里，会被误判成"编造"，白白扣掉置信度。
    """
    out = {}
    for mid, q in (led.get("quotes") or {}).items():
        out[mid] = {"mid": mid, "text": q.get("q", ""),
                    "date": q.get("d", ""), "hour": q.get("h")}
    out.update(current or {})
    return out


# ── 条目 ────────────────────────────────────────────────

def eid_of(section, label):
    return f"{section}::{str(label).strip()}"


def _find(items, eid):
    for e in items or []:
        if e.get("eid") == eid:
            return e
    return None


def _union(a, b):
    out = list(a or [])
    for x in b or []:
        if x and x not in out:
            out.append(x)
    return out


def _new_entry(eid, section, obs, now):
    # 首次观察不可能接近确定：它还没经过"换一轮再看还是这样"的检验。
    # 所以新条目置信度封顶 0.9，后面靠反复确认慢慢涨上去（最高 0.96）。
    conf = min(0.90, round(float(obs.get("confidence", 0.5)), 2))
    return {
        "eid": eid,
        "section": section,
        "label": str(obs.get("label") or "").strip(),
        "value": str(obs.get("value") or "").strip(),
        "confidence": conf,
        "evidence": list(obs.get("evidence") or []),
        "counter": list(obs.get("counter") or []),
        "source": obs.get("source") or "llm",
        "state": ACTIVE,
        "first_seen": now,
        "last_supported": now,
        "last_checked": now,
        "confirmations": 1,
        "silent_runs": 0,
        "history": [{"at": now, "event": "created", "to": conf, "note": "首次观察"}],
    }


def prune_weak(led, min_conf=0.15, reason="置信度低于门槛的占位条目"):
    """清掉低于门槛的条目（模型偶尔会产出「语料不足」配 0% 的占位结论）。

    返回被清掉的 [(section, label)]，方便记进日志。
    """
    removed = []
    for sec, items in (led.get("entries") or {}).items():
        keep = []
        for e in items or []:
            if float(e.get("confidence", 0)) < min_conf:
                removed.append((sec, e.get("label")))
                continue
            keep.append(e)
        led["entries"][sec] = keep
    return removed


def purge_quarantined(led, mids, now=None):
    """证据被隔离的结论，跟着降权或退休。

    污染防控把某些消息剔出去之后，**引用它们的结论不能原样留在账本里**——
    否则被隔离的数据还会通过旧结论继续影响人格。

    规则：
      - 证据**全部**被隔离 → 结论退休（state=retired），历史里写明原因
      - 证据**部分**被隔离 → 剔掉那些引用、置信度打八折
    返回 {"retired": [(section,label)], "weakened": [...]}。
    """
    now = now or _now()
    mids = set(mids or [])
    if not mids:
        return {"retired": [], "weakened": []}
    res = {"retired": [], "weakened": []}
    for sec, items in (led.get("entries") or {}).items():
        for e in items or []:
            ev = [m for m in (e.get("evidence") or []) if m not in mids]
            removed = len(e.get("evidence") or []) - len(ev)
            if not removed:
                continue
            if not ev:
                # 证据全没了 → 退休，但历史里写清楚是数据问题不是人的问题
                e["state"] = RETIRED
                e["confidence"] = round(float(e.get("confidence", 0.5)) * 0.5, 2)
                e["evidence"] = []
                e["last_checked"] = now
                e.setdefault("history", []).append({
                    "at": now, "event": "retired",
                    "note": f"证据 {removed} 条被污染防控隔离，结论退休",
                })
                res["retired"].append((sec, e.get("label")))
            else:
                e["evidence"] = ev
                e["confidence"] = round(float(e.get("confidence", 0.5)) * 0.8, 2)
                e["last_checked"] = now
                e.setdefault("history", []).append({
                    "at": now, "event": "weakened",
                    "note": f"{removed} 条证据被污染防控隔离，置信度下调",
                })
                res["weakened"].append((sec, e.get("label")))
    return res


def _push_history(e, event, old_conf, note=""):
    e.setdefault("history", []).append({
        "at": _now(), "event": event,
        "from": round(float(old_conf), 2),
        "to": round(float(e.get("confidence", 0)), 2),
        "note": note,
    })
    e["history"] = e["history"][-25:]


# ── 拟定（纯函数：算完不改账本） ────────────────────────

def plan(led, features, run_meta=None, cfg=None, stats_only=False):
    """把本轮观察套用到账本上，算出「如果通过审核会发生什么」。

    刻意做成纯函数：审核页展示的是**将要发生的变化**，而不是已经发生的变化。
    人点了通过，才由 commit() 真正落账。

    stats_only：这一轮没有 LLM 参与（例如等级没到深挖线）。
    这种情况下**只允许统计来源的条目动自己人**，绝不允许统计值改写 LLM 得出的结论 ——
    否则「他几乎不用标点」这种只有模型看得出的判断，会被「标点频率统计」顶掉，
    等于拿粗尺子量细活。
    """
    cfg = cfg or get_config()
    suspect_after = int(cfg.get("suspect_after", 3))
    dormant_after = int(cfg.get("dormant_after", 8))
    now = _now()
    entries = copy.deepcopy(led.get("entries") or {})
    events = Counter()
    changes = []
    touched = set()

    for section, items in (features or {}).items():
        for obs in items or []:
            label = str(obs.get("label") or "").strip()
            if not label:
                continue
            eid = eid_of(section, label)
            cur = _find(entries.get(section), eid)
            if cur is not None and stats_only and cur.get("source") == "llm" \
                    and obs.get("source") == "heuristic":
                continue          # 统计不覆盖模型结论
            touched.add(eid)
            if cur is None:
                e = _new_entry(eid, section, obs, now)
                entries.setdefault(section, []).append(e)
                events["created"] += 1
                changes.append({"section": section, "label": label,
                                "event": "created", "to": e["confidence"],
                                "reason": "首次观察"})
                continue
            _apply(cur, obs, now, cfg, events, changes, section)

    # 沉默处理 —— 只降级，绝不因为沉默而推翻
    #
    # 前提：**只有真的来了新对话，沉默才算数**。
    # 全量重读同一份语料时没再提到某条结论，往往只是模型换了种措辞，
    # 不是"这个人变了"。所以这种情况只更新 last_checked，不推进 silent_runs。
    meta = (run_meta or {})
    advance_silence = bool(meta.get("first_run")) or \
        int(meta.get("delta_messages") or 0) > 0
    for section, items in entries.items():
        for e in items:
            e["last_checked"] = now
            if e["eid"] in touched:
                e["silent_runs"] = 0
                continue
            if not advance_silence:
                continue
            if e.get("state") == RETIRED:
                continue
            e["silent_runs"] = int(e.get("silent_runs", 0)) + 1
            if e["state"] == ACTIVE and e["silent_runs"] >= suspect_after:
                e["state"] = SUSPECT
                events["suspect"] += 1
                changes.append({"section": section, "label": e["label"],
                                "event": "suspect",
                                "reason": f"连续 {e['silent_runs']} 轮未被新证据提及"})
            elif e["state"] == SUSPECT and e["silent_runs"] >= dormant_after:
                e["state"] = DORMANT
                events["dormant"] += 1
                changes.append({"section": section, "label": e["label"],
                                "event": "dormant",
                                "reason": f"连续 {e['silent_runs']} 轮未被提及，转入沉眠"
                                          "（仍保留在账本里，不代表已否定）"})

    return {
        "at": now,
        "run": int(led.get("runs", 0)) + 1,
        "entries": entries,
        "events": {k: v for k, v in events.items() if v},
        "changes": changes,
        "meta": run_meta or {},
        "first_run": not (led.get("entries") or {}),
    }


def view(planned):
    """给 UI / 渲染用的精简投影（剥掉 history，免得 pending JSON 膨胀）。"""
    entries = {}
    for section, items in (planned.get("entries") or {}).items():
        entries[section] = [{k: v for k, v in e.items() if k != "history"}
                            for e in items]
    return {
        "entries": entries,
        "changes": planned.get("changes") or [],
        "events": planned.get("events") or {},
        "run": planned.get("run"),
        "at": planned.get("at"),
        "meta": planned.get("meta") or {},
        "first_run": planned.get("first_run", False),
    }


def new_fingerprints(led, full_map):
    """只挑出账本里还没有的「指纹 → 编号」，避免把几十万条老水位重写一遍。"""
    old = led.get("mid_of_fp") or {}
    return {fp: mid for fp, mid in (full_map or {}).items() if old.get(fp) != mid}


def _apply(e, obs, now, cfg, events, changes, section):
    """把一条观察套到已有条目上。这里是整个账本最需要想清楚的地方。"""
    label = e["label"]
    old_conf = float(e.get("confidence", 0.5))
    old_value = e.get("value", "")
    obs_conf = float(obs.get("confidence", 0.5))
    new_ev = list(obs.get("evidence") or [])
    counter = list(obs.get("counter") or [])
    fresh = [m for m in new_ev if m not in (e.get("evidence") or [])]

    if counter:
        # ── 有反证：这是唯一能推翻一条结论的途径 ──
        e["counter"] = _union(e.get("counter"), counter)
        if obs_conf >= old_conf:
            e["state"] = RETIRED
            e["confidence"] = round(old_conf * 0.4, 2)
            events["retired"] += 1
            changes.append({"section": section, "label": label, "event": "retired",
                            "from": old_conf, "to": e["confidence"],
                            "reason": f"新证据（{','.join(counter[:3])}）强度不低于原结论"})
        else:
            e["state"] = SUSPECT
            e["confidence"] = round(old_conf * 0.6, 2)
            events["contradicted"] += 1
            changes.append({"section": section, "label": label,
                            "event": "contradicted",
                            "from": old_conf, "to": e["confidence"],
                            "reason": f"出现反例 {','.join(counter[:3])}，转为存疑"})
        _push_history(e, "contradicted", old_conf)
        e["evidence"] = _union(e.get("evidence"), new_ev)[-12:]
        e["last_checked"] = now
        e["silent_runs"] = 0
        return

    new_value = str(obs.get("value") or "").strip()
    if new_value and new_value != old_value:
        # ── 改写：不是错，是成长（或换了个说法） ──
        e["value"] = new_value
        e["confidence"] = round(old_conf * 0.5 + obs_conf * 0.5, 2)
        events["revised"] += 1
        changes.append({"section": section, "label": label, "event": "revised",
                        "from": old_conf, "to": e["confidence"],
                        "reason": f"{old_value[:30]} → {new_value[:30]}"})
        _push_history(e, "revised", old_conf, "结论被改写")
    else:
        # ── 确认：老结论又拿到新证据 ──
        bonus = min(0.10, 0.03 * len(fresh))
        e["confidence"] = round(min(0.96, old_conf * 0.7 + obs_conf * 0.3 + bonus), 2)
        e["confirmations"] = int(e.get("confirmations", 1)) + 1
        revived = e.get("state") in (SUSPECT, DORMANT, RETIRED)
        e["state"] = ACTIVE
        if revived:
            events["revived"] += 1
            changes.append({"section": section, "label": label, "event": "revived",
                            "from": old_conf, "to": e["confidence"],
                            "reason": "重新拿到证据，恢复活跃"})
            _push_history(e, "revived", old_conf, "重新拿到证据")
        else:
            events["confirmed"] += 1
            changes.append({"section": section, "label": label, "event": "confirmed",
                            "from": old_conf, "to": e["confidence"],
                            "reason": f"新增 {len(fresh)} 条证据" if fresh else "再次确认"})
            _push_history(e, "confirmed", old_conf)

    e["evidence"] = _union(e.get("evidence"), new_ev)[-12:]
    e["last_supported"] = now
    e["last_checked"] = now
    e["silent_runs"] = 0


def commit(led, planned, id_map=None, quotes=None,
           level="", target_name="", cfg=None):
    """把拟定结果真正落账（只有人工审核通过后才调用）。"""
    cfg = cfg or get_config()
    led["entries"] = planned.get("entries") or {}
    led["runs"] = int(planned.get("run") or led.get("runs", 0) + 1)
    led["level"] = level or led.get("level", "")
    if target_name:
        led["target_name"] = target_name
    # 断点续跑：这一轮没覆盖的层记下来，下一轮自动优先补
    meta = planned.get("meta") or {}
    led["pending_sections"] = [s for s in (meta.get("uncovered") or []) if s]
    _remember_map(led, id_map, quotes, cap=cfg.get("ledger_max_fingerprints"))

    entry = {
        "at": planned.get("at") or _now(),
        "run": led["runs"],
        "level": level,
        "events": planned.get("events") or {},
        "changes": (planned.get("changes") or [])[:80],
        "meta": planned.get("meta") or {},
    }
    led.setdefault("journal", []).insert(0, entry)
    led["journal"] = led["journal"][:100]
    save(led)
    return led


# ── 读取视图 ────────────────────────────────────────────

def summary(led):
    """账本概况。"""
    counts = Counter()
    total = 0
    for items in (led.get("entries") or {}).values():
        for e in items or []:
            counts[e.get("state", ACTIVE)] += 1
            total += 1
    conf = [float(e.get("confidence", 0))
            for items in (led.get("entries") or {}).values() for e in items or []]
    return {
        "target_id": led.get("target_id"),
        "runs": led.get("runs", 0),
        "level": led.get("level", ""),
        "created_at": led.get("created_at"),
        "updated_at": led.get("updated_at"),
        "entries": total,
        "by_state": {STATE_LABEL.get(k, k): v for k, v in counts.items()},
        "avg_confidence": round(sum(conf) / len(conf), 3) if conf else 0,
        "seen_messages": (led.get("seen") or {}).get("count", 0),
        "journal_entries": len(led.get("journal") or []),
    }


def stable_entries(led, min_conf=None, states=(ACTIVE,)):
    """挑出"能代表这个人"的条目：状态活跃且置信度够高。

    沉眠与已推翻的条目不会进人格注入卡 —— 它们留在文件里供人查阅，
    但不该影响模型怎么说话。
    """
    cfg = get_config()
    min_conf = float(min_conf if min_conf is not None else cfg.get("inject_min_confidence", 0.4))
    out = {}
    for section, items in (led.get("entries") or {}).items():
        picked = [e for e in (items or [])
                  if e.get("state") in states and float(e.get("confidence", 0)) >= min_conf]
        picked.sort(key=lambda e: -float(e.get("confidence", 0)))
        out[section] = picked
    return out


def diff_text(led, limit=8):
    """最近一轮变更的自然语言摘要。"""
    journal = led.get("journal") or []
    if not journal:
        return "账本还没有任何记录。"
    last = journal[0]
    ev = last.get("events") or {}
    parts = [f"{EVENT_LABEL.get(k, k)} {v}" for k, v in ev.items() if v]
    if not parts:
        return f"第 {last.get('run')} 轮：这一轮没有结论发生变化。"
    return (f"第 {last.get('run')} 轮（{last.get('at', '')[:16]}）："
            + "，".join(parts))
