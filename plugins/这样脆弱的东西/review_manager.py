# -*- coding: utf-8 -*-
"""人工审核管理 + 记录持久化。

蒸馏完成不直接落盘，先进入待审核（pending_review.json）；
人看过、改过、认可之后，才写进 data/distills/ 并进入历史。

存储分工：
  data/pending_review.json          本次待审核结果（不含大块证据表）
  data/evidence/{id}.json           编号 → 原文引证（体积大，单独放）
  data/distills/{目标}_{等级}.md     人格文件正式版（带 .1 .2 备份）
  data/history.json                 历史索引 + 审核后的特征（供 UI 详情/对比）
"""
import json
import os
import shutil
import time
import sys
import traceback

from .config import DATA_DIR
from . import personality_model

DISTILLS_DIR = os.path.join(DATA_DIR, "distills")
EVIDENCE_DIR = os.path.join(DATA_DIR, "evidence")
PENDING_FILE = os.path.join(DATA_DIR, "pending_review.json")
HISTORY_FILE = os.path.join(DATA_DIR, "history.json")
BACKUP_COUNT = 5

os.makedirs(DISTILLS_DIR, exist_ok=True)
os.makedirs(EVIDENCE_DIR, exist_ok=True)


# ── 基础读写 ────────────────────────────────────────────

def _read_json(path, default):
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return default


def _write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _evidence_path(distillation_id):
    return os.path.join(EVIDENCE_DIR, f"{distillation_id}.json")


def get_evidence_map(distillation_id):
    return _read_json(_evidence_path(distillation_id), {}) or {}


# ── 待审核 ──────────────────────────────────────────────

def save_pending(payload):
    """保存待审核结果。payload 为 distill_engine 产出的完整记录。"""
    pid = payload["id"]
    emap = payload.pop("evidence_map", {}) or {}
    if emap:
        _write_json(_evidence_path(pid), emap)
    payload["evidence_path"] = _evidence_path(pid) if emap else ""
    _write_json(PENDING_FILE, payload)


def get_pending():
    return _read_json(PENDING_FILE, None)


def has_pending():
    return os.path.exists(PENDING_FILE)


def with_evidence(record):
    """把证据表挂回记录，供渲染引证片段。"""
    if not record:
        return record
    path = record.get("evidence_path")
    if path and os.path.exists(path):
        record = dict(record)
        record["evidence_map"] = _read_json(path, {}) or {}
    return record


# 兼容旧名字（api_server 早期版本用过下划线开头）
_with_evidence = with_evidence


def _auto_turing(record_id, rounds):
    """后台自动盲测：通过审核 → 用当前人格卡生成 → 与真实发言逐维度对比。

    结果由 api_server.turing_test 写回账本（verdict + mismatches），
    下一次 render 时人格文件里就会出现「哪里不像」那张表。
    刻意放 daemon 线程：盲测要花几分钟额度，不能卡住审核请求。
    """
    try:
        import time as _t
        _t.sleep(2)          # 等审核的文件落盘稳定
        from . import api_server
        res = api_server.turing_test(
            {"distillation_id": record_id, "rounds": rounds})
        status = res.get("status")
        verdict = res.get("verdict", "")
        audit_logger.log("turing_auto", detail=record_id,
                         outcome=str(status),
                         extra={"verdict": verdict})
    except Exception as e:
        try:
            audit_logger.log("turing_auto_error", detail=record_id,
                             outcome=str(e))
        except Exception:
            pass


def approve(features=None):
    """审核通过：合并审核后的特征 → 记入账本 → 生成人格文件 → 落盘 → 进历史。

    记账放在这里而不是蒸馏时，是刻意的：账本是"人认可过的认识"。
    被丢弃的蒸馏不该污染它 —— 人工审核就是那道闸门。
    """
    from .config import get_config
    from . import ledger as ledger_mod

    pending = get_pending()
    if not pending:
        return {"success": False, "error": "没有待审核的蒸馏结果"}

    merged = json.loads(json.dumps(pending.get("features") or {}))
    for sec, items in (features or {}).items():
        merged[sec] = items
    pending["features"] = merged

    cfg = get_config()
    ledger_note = ""
    if cfg.get("continuous", True) and pending.get("ledger_commit") is not None:
        led = ledger_mod.load(pending["target_id"])
        meta = (pending.get("ledger_plan") or {}).get("meta") or {}
        stats_only = not ((pending.get("quality") or {}).get("llm") or {}).get("available")
        planned = ledger_mod.plan(led, merged, meta, cfg, stats_only=stats_only)
        commit = pending.get("ledger_commit") or {}
        ledger_mod.commit(led, planned, id_map=commit.get("id_map"),
                          quotes=commit.get("quotes"), level=pending["level"],
                          target_name=pending.get("target_name", ""), cfg=cfg)
        pending["ledger_plan"] = ledger_mod.view(planned)
        pending["ledger_journal"] = led.get("journal") or []
        # 历史引用也要能渲染出原话：把账本累积的引证并进来
        ev = dict(pending.get("evidence_map") or {})
        for mid, q in (led.get("quotes") or {}).items():
            ev.setdefault(mid, q)
        pending["evidence_map"] = ev
        s = ledger_mod.summary(led)
        ledger_note = (f"账本第 {s['runs']} 轮："
                       f"{'、'.join(f'{k} {v}' for k, v in s['by_state'].items())}")

    record = with_evidence(pending)
    markdown = personality_model.build_persona(record)
    actual_tokens = personality_model.estimate_tokens(markdown)

    path = os.path.join(DISTILLS_DIR,
                        f"{pending['target_id']}_{pending['level']}.md")
    _backup(path)
    with open(path, "w", encoding="utf-8") as f:
        f.write(markdown)

    # 证据附卷：每条结论引用到的原文（较长版本），便于独立核对
    appendix_path = ""
    if cfg.get("evidence_appendix", True):
        try:
            appendix_path = os.path.join(
                DISTILLS_DIR,
                f"{pending['target_id']}_{pending['level']}.evidence.md")
            with open(appendix_path, "w", encoding="utf-8") as f:
                f.write(personality_model.build_evidence_appendix(record))
        except Exception:
            appendix_path = ""

    # 分层存储：为大体量人格做的结构优化（manifest + 分层 + 证据分片）。
    # 卡片是热层（常驻），各层与证据是 O(1) 寻址 —— 人格再大也不用整体加载。
    store_note = ""
    try:
        from . import persona_store
        card = personality_model.build_injection_prompt(record)
        m = persona_store.write(
            pending["target_id"], persona_md=markdown, card_md=card,
            evidence_md=personality_model.build_evidence_appendix(record)
            if appendix_path else "",
            features=merged, measures=pending.get("measures"),
            style=pending.get("style"), relational=pending.get("relational"),
            evidence_map=pending.get("evidence_map") or {},
            stats=pending.get("stats") or {},
            ledger_view=pending.get("ledger_plan") or {},
            level=pending["level"], mode=pending.get("mode", ""))
        store_note = (f"分层存储已更新（{m.get('bytes_total', 0)} 字节 / "
                      f"{len(m.get('sections') or {})} 层 / "
                      f"{len(m.get('evidence_shards') or [])} 个证据分片）")
    except Exception as e:
        store_note = f"分层存储写入失败（不影响其余产出）：{e}"

    entry = {
        "id": pending["id"],
        "target_id": pending["target_id"],
        "target_name": pending.get("target_name", ""),
        "level": pending["level"],
        "mode": pending.get("mode", "full"),
        "timestamp": time.time(),
        "data_volume": pending.get("data_volume", 0),
        "raw_volume": pending.get("raw_volume", 0),
        "delta_messages": pending.get("delta_messages", 0),
        "tokens_used": actual_tokens,
        "confidence": _avg_confidence(merged),
        "path": path,
        "appendix_path": appendix_path,
        "evidence_path": pending.get("evidence_path", ""),
        "features": merged,
        "stats": pending.get("stats", {}),
        "counts": pending.get("counts", {}),
        "quality": pending.get("quality", {}),
        "coverage_note": pending.get("coverage_note", ""),
        "ledger_plan": pending.get("ledger_plan"),
        "ledger_journal": pending.get("ledger_journal"),
        "ledger_note": ledger_note,
        "fingerprint": personality_model._soul.fingerprint(markdown),
    }
    _append_history(entry)
    if os.path.exists(PENDING_FILE):
        os.remove(PENDING_FILE)
    # 图灵自动闭环（配置 turing_auto，默认关）：后台跑盲测，结果回写账本，
    # 下一次 render 的人格文件里就会出现「哪里不像」那张表
    if cfg.get("turing_auto") and \
            ((pending.get("quality") or {}).get("llm") or {}).get("available"):
        import threading
        threading.Thread(target=_auto_turing,
                         args=(pending["id"],
                               int(cfg.get("turing_rounds", 5))),
                         daemon=True, name="auto-turing").start()

    return {"success": True, "persona_path": path,
            "appendix_path": appendix_path,
            "store_note": store_note,
            "actual_tokens": actual_tokens,
            "confidence_overall": entry["confidence"],
            "fingerprint": entry["fingerprint"],
            "ledger_note": ledger_note}


def discard():
    pending = get_pending()
    if pending:
        pid = pending.get("id")
        for p in (PENDING_FILE, _evidence_path(pid)):
            try:
                if p and os.path.exists(p):
                    os.remove(p)
            except OSError:
                pass
    return {"success": True}


def _backup(path):
    if not os.path.exists(path):
        return
    for i in range(BACKUP_COUNT - 1, 0, -1):
        old = f"{path}.{i}"
        if os.path.exists(old):
            try:
                shutil.copy2(old, f"{path}.{i + 1}")
            except OSError:
                pass
    try:
        shutil.copy2(path, f"{path}.1")
    except OSError:
        pass


def _avg_confidence(features):
    items = [f for sec in (features or {}).values() for f in (sec or [])]
    if not items:
        return 0.0
    return round(sum(float(f.get("confidence", 0)) for f in items) / len(items), 2)


# ── 历史 ────────────────────────────────────────────────

def _append_history(entry):
    history = get_history()
    history.insert(0, entry)
    try:
        _write_json(HISTORY_FILE, history[:200])
    except Exception as e:
        print(f"[PersonaDistill] 写历史失败: {e}")


def get_history():
    return _read_json(HISTORY_FILE, []) or []


def get_record(distillation_id):
    for r in get_history():
        if r.get("id") == distillation_id:
            return r
    return None


def render_from_ledger(target_id, level=None):
    """不花任何 LLM 调用，直接用账本里的结论重新印一份人格文件。

    用途有两个：
      1. 想换个等级看同一份认识、或只想重排版式时，不必再烧一次额度
      2. 确定性后处理（比如名字方向守卫）改动了结论之后，把成品同步过来

    这里会重新收集一次语料 —— 那是纯本地读取，不消耗额度，
    但守卫和洞察需要它（要核对名字出现在什么位置）。
    """
    from . import ledger as ledger_mod
    from . import insight as insight_mod
    from . import data_collector, evidence

    led = ledger_mod.load(target_id)
    if not led.get("entries"):
        return {"error": f"{target_id} 还没有账本，先跑一次蒸馏"}
    hist = [r for r in get_history() if str(r.get("target_id")) == str(target_id)]
    if not hist:
        return {"error": "找不到该目标的历史记录（渲染需要统计数据与证据表）"}
    rec = dict(hist[0])
    level = level or rec.get("level") or led.get("level") or "L2"
    rec["level"] = level

    # 账本条目就是权威状态
    features = {}
    for sec, items in (led.get("entries") or {}).items():
        features[sec] = [{k: v for k, v in e.items() if k != "history"}
                         for e in (items or [])]
    rec["features"] = features

    # 守卫 + 洞察 + 风格画像 + 科学测量层全量重算（纯确定性，0 次调用）
    from .config import get_config
    try:
        data = data_collector.collect(target_id)
        raw = list(data["messages"]) + [dict(f, kind="fact") for f in data["facts"]]
        msgs, _, _, _ = evidence.normalize(raw, id_map=led.get("mid_of_fp"),
                                           start=led.get("next_mid", 1))
    except Exception:
        msgs, data = [], None
    from . import bot_identity
    warnings = insight_mod.naming_guard(features, msgs,
                                        bot_names=bot_identity.bot_names())

    # 风格画像与科学测量层是语料的确定性函数 —— 重印时直接重算，
    # 不用为了一次重排版再烧一遍额度
    style, measures = None, None
    try:
        from . import style_profile as style_mod
        from . import psych as psych_mod
        talks = evidence.talks(msgs)
        style = style_mod.build(talks, exemplar_count=int(
            get_config().get("exemplar_count", 12)))
        for sec, items in style_mod.to_features(style).items():
            have = {e.get("label") for e in (features.get(sec) or [])}
            for e in items:
                if e["label"] not in have:
                    features.setdefault(sec, []).append(e)
                    have.add(e["label"])
        measures = psych_mod.build(talks, style, data.get("facts") if data else [])
        for sec, items in psych_mod.to_features(measures).items():
            have = {e.get("label") for e in (features.get(sec) or [])}
            for e in items:
                if e["label"] not in have:
                    features.setdefault(sec, []).append(e)
                    have.add(e["label"])
        # 关系分桶（对不同对象说话方式不同）
        from . import relational as rel_mod
        rel = rel_mod.build(talks, data_collector.nickname_map(),
                            bot_names=bot_identity.bot_names())
        for e in rel_mod.to_features(rel):
            have = {x.get("label") for x in (features.get("relationship") or [])}
            if e["label"] not in have:
                features.setdefault("relationship", []).append(e)
        rec["relational"] = rel
    except Exception:
        style, measures = None, None
        _render_error = traceback.format_exc(limit=3)
        print("[render] 风格画像/科学测量层计算失败：\n" + _render_error,
              file=sys.stderr)

    quality = dict(rec.get("quality") or {})
    quality["insight"] = {
        "heatmap": insight_mod.heatmap(features, {
            "identity": 6, "linguistics": 8, "behavior": 6, "cognition": 6,
            "relationship": 5, "temporal": 4, "environmental": 4}),
        "contradictions": insight_mod.contradictions(features),
        "anchors": insight_mod.anchors(features),
        "reflexes": insight_mod.reflexes(features),
        "naming_warnings": warnings,
        "counts": (quality.get("insight") or {}).get("counts") or {},
        "verdict": (quality.get("insight") or {}).get("verdict") or "",
        "coverage": quality.get("coverage") or {},
    }
    quality["insight"]["weak_areas"] = insight_mod.weak_areas(
        quality["insight"]["heatmap"])
    rec["quality"] = quality
    rec["style"] = style
    rec["measures"] = measures
    rec["quarantine"] = (data or {}).get("quarantine") or {}
    # 图灵反馈：账本里存过盲测结果的话，人格文件里要能看到"哪里不像"
    turing = led.get("turing") or {}
    if turing.get("mismatches"):
        rec["turing_feedback"] = {
            "rounds": turing.get("rounds", 0),
            "real_samples": turing.get("rounds", 0),
            "generated_samples": turing.get("rounds", 0),
            "verdict": turing.get("verdict", ""),
            "mismatches": [{"维度": w, "真实": "—", "生成": "—",
                            "判定": "历史盲测中不像", "该调": "注入卡对应参数"}
                           for w in turing.get("mismatches")],
        }
    rec["ledger_plan"] = {
        "entries": features, "changes": [], "events": {},
        "run": led.get("runs"), "at": ledger_mod._now(), "meta": {},
    }
    rec["ledger_journal"] = led.get("journal") or []

    record = with_evidence(rec)
    markdown = personality_model.build_persona(record)
    card = personality_model.build_injection_prompt(record)
    appendix_md = personality_model.build_evidence_appendix(record)
    path = os.path.join(DISTILLS_DIR, f"{target_id}_{level}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(markdown)
    appendix = ""
    try:
        appendix = os.path.join(DISTILLS_DIR, f"{target_id}_{level}.evidence.md")
        with open(appendix, "w", encoding="utf-8") as f:
            f.write(appendix_md)
    except Exception:
        appendix = ""

    # 同步分层存储（0 次调用）：卡片热层 + 各层 JSON + 证据分片
    try:
        from . import persona_store
        persona_store.write(target_id, persona_md=markdown, card_md=card,
                            evidence_md=appendix_md if appendix else "",
                            features=features, measures=measures, style=style,
                            relational=rec.get("relational"),
                            evidence_map=record.get("evidence_map") or {},
                            stats=rec.get("stats") or {},
                            ledger_view=rec.get("ledger_plan") or {},
                            level=level, mode="render")
    except Exception:
        pass
    return {"success": True, "persona_path": path, "appendix_path": appendix,
            "tokens": personality_model.estimate_tokens(markdown),
            "naming_warnings": warnings,
            "llm_calls": 0}


def get_persona_content(distillation_id):
    """读人格文件正文。"""
    record = get_record(distillation_id)
    if not record or not record.get("path") or not os.path.exists(record["path"]):
        return ""
    with open(record["path"], "r", encoding="utf-8") as f:
        return f.read()


def get_persona_prompt(distillation_id):
    """取该记录对应的人格注入卡（可直接当 system prompt）。"""
    record = get_record(distillation_id)
    if not record:
        return ""
    return personality_model.build_injection_prompt(record)


def delete_record(distillation_id):
    history = [r for r in get_history() if r.get("id") != distillation_id]
    _write_json(HISTORY_FILE, history)
    try:
        ev = _evidence_path(distillation_id)
        if os.path.exists(ev):
            os.remove(ev)
    except OSError:
        pass
    return {"success": True}


def set_seal(distillation_id, checksum, fingerprint):
    """把封印校验和/指纹写进历史记录。"""
    history = get_history()
    for r in history:
        if r.get("id") == distillation_id:
            r["sealed_checksum"] = checksum
            r["fingerprint"] = fingerprint
            r["sealed_at"] = time.time()
    try:
        _write_json(HISTORY_FILE, history)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}
