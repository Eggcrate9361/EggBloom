# -*- coding: utf-8 -*-
"""这样脆弱的东西 WebUI API 动作。

走 WebUI 通用插件通道 /api/plugins/action（plugin_id=这样脆弱的东西）。
PLUGIN_ACTIONS 在 __init__.py 汇总，这里实现具体动作。
"""
import json
import time
import os
import sys

from . import distill_engine, level_classifier, review_manager, audit_logger
from . import data_collector, personality_model, sections
from .config import get_config, set_config

# ── AI 客户端（由插件管理器经模块级 set_ai_client 注入） ──
_ai_client_cache = {}


def set_ai_client(client):
    """插件管理器 set_ai_client 传播入口（记录 bot 的客户端，作为兜底）。"""
    _ai_client_cache["bot"] = client
    _ai_client_cache.pop("client", None)
    try:
        from . import deep_client
        deep_client.reset()
    except Exception:
        pass


def _get_ai_client():
    """取 bot 那边的客户端（turing_test 等场景用）。"""
    if _ai_client_cache.get("client") is not None:
        return _ai_client_cache["client"]
    if _ai_client_cache.get("bot") is not None:
        _ai_client_cache["client"] = _ai_client_cache["bot"]
        return _ai_client_cache["client"]
    try:
        import __main__ as main_mod
        if hasattr(main_mod, "ai"):
            _ai_client_cache["client"] = main_mod.ai
            return _ai_client_cache["client"]
    except (ImportError, AttributeError):
        pass
    try:
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from config import load_config, get_provider_api_key
        from ai_client import AIClient
        cfg = load_config()
        provider = cfg.get("api_provider", "deepseek")
        api_key = get_provider_api_key(provider)
        model = cfg.get("model_name", "deepseek-flash")
        if api_key or provider == "ollama":
            client = AIClient(cfg.get("api_url", ""), api_key, model,
                              provider=provider, temperature=cfg.get("ai_temperature"))
            _ai_client_cache["client"] = client
            return client
    except Exception as e:
        print(f"[PersonaDistill] AI 客户端初始化失败: {e}")
    return None


def _get_deep_client():
    """取蒸馏专用客户端（关掉思考、独立配置）。返回 (client, note)。"""
    from . import deep_client
    return deep_client.get(injected=_ai_client_cache.get("bot"), cfg=get_config())


def deep_ai_available():
    client, _ = _get_deep_client()
    return client is not None


def deep_ai_available():
    client, _ = _get_deep_client()
    return client is not None


def ai_available():
    return _get_ai_client() is not None


def stats(params=None):
    history = review_manager.get_history()
    subjects = set(r.get("target_id") for r in history)
    total_tokens = sum(int(r.get("tokens_used", 0)) for r in history)
    confs = [r.get("confidence", 0) for r in history if r.get("confidence")]
    avg_conf = round(sum(confs) / len(confs), 2) if confs else 0
    return {
        "total_distillations": len(history),
        "unique_subjects": len(subjects),
        "total_tokens": total_tokens,
        "avg_confidence": avg_conf,
        "recent": history[:10],
    }


def scan_data(params=None):
    """扫描数据量、源状态与缺口。

    只**收一次**：以前是先 scan（全量收一遍）再 collect（又收一遍），
    五个数据源白读两遍。现在一次收集算完所有派生信息。
    """
    params = params or {}
    target_id = str(params.get("target_id") or "").strip()
    if not target_id:
        return {"error": "缺少 target_id"}
    cfg = get_config()
    sources = params.get("sources") or None
    data = data_collector.collect(
        target_id, sources=sources,
        max_per_source=int(cfg.get("max_samples_per_source", 50000)))
    total = data["total"]
    # 原始条数 vs 去重后的真实发言数 —— 落沙里同一个短句会重复很多次，
    # 只报原始条数会让人高估语料量（实测 699 → 291）
    try:
        from . import evidence as _ev
        raw_all = list(data["messages"]) + [dict(f, kind="fact")
                                           for f in data["facts"]]
        _m, _, _, _ = _ev.normalize(raw_all)
        _st = _ev.stats(_m, raw_total=len(raw_all))
        distinct = _st["unique"]
    except Exception:
        distinct = total
    out = {
        "total_count": total,
        "distinct_count": distinct,
        "counts": data["counts"],
        "facts_count": len(data["facts"]),
        "facts_by_kind": data["facts_by_kind"],
        "notes": data["notes"],
        "missing": data["missing"],
        "suggested_level": level_classifier.classify_level(distinct),
        "suggested_budget": level_classifier.level_budget(
            level_classifier.classify_level(total)),
        "ai_available": deep_ai_available(),
        "deep_channel": _get_deep_client()[1],
    }
    out["levels"] = {
        lv: {
            "budget": level_classifier.level_budget(lv),
            "sections": level_classifier.sections_for_level(lv),
            "llm_sections": level_classifier.level_info(lv)["llm_sections"],
            "expected": level_classifier.expected_entries(lv),
            "feasibility": level_classifier.feasibility(distinct, lv),
        }
        for lv in level_classifier.LEVEL_ORDER
    }

    # 连续蒸馏：账本已收录多少、这次新增多少、该走哪个模式
    from . import ledger as ledger_mod
    led = ledger_mod.load(target_id)
    delta, raw_total, first_run = ledger_mod.split_delta(led, data["messages"])
    if first_run or not led.get("entries"):
        suggested_mode = "full"
    elif not delta:
        suggested_mode = "skip"
    elif len(delta) <= int(cfg.get("incremental_max_messages", 300)):
        suggested_mode = "incremental"
    else:
        suggested_mode = "full"
    out["ledger"] = ledger_mod.summary(led)
    out["delta"] = {"new_raw": len(delta), "total_raw": raw_total,
                    "first_run": first_run}
    out["suggested_mode"] = suggested_mode
    # 预估这一轮要花多少次调用（让用户在点之前就知道代价）
    est_chunks = max(1, (data["total"] * 24) // max(1, int(
        cfg.get("chunk_chars", 12000))) + 1)
    if suggested_mode != "skip":
        out["plan"] = sections.plan_calls(
            sections.llm_sections_for_level(out["suggested_level"]),
            chunks=est_chunks, cfg=cfg,
            resume=led.get("pending_sections") or [])
    return out


def start_distill(params=None):
    params = params or {}
    target_id = str(params.get("target_id") or "").strip()
    if not target_id:
        return {"error": "缺少 target_id"}
    consent = bool(params.get("consent", False))
    if not consent:
        return {"error": "未确认伦理同意"}
    result = distill_engine.start(
        target_id=target_id,
        target_name=str(params.get("target_name") or ""),
        level=params.get("level") or None,
        sources=params.get("sources") or None,
        external_text=str(params.get("external_text") or ""),
        consent=True,
        mode=str(params.get("mode") or "auto"),
    )
    return result


def ledger(params=None):
    """查看某个对象的人格账本：条目状态、演化日志、水位。"""
    from . import ledger as ledger_mod
    params = params or {}
    target_id = str(params.get("target_id") or "").strip()
    if not target_id:
        hist = review_manager.get_history()
        target_id = str(hist[0].get("target_id")) if hist else ""
    if not target_id:
        return {"error": "缺少 target_id，且历史里没有记录"}
    led = ledger_mod.load(target_id)
    entries = {}
    for sec, items in (led.get("entries") or {}).items():
        entries[sec] = sorted(
            [{k: v for k, v in e.items() if k != "history"} for e in (items or [])],
            key=lambda e: (e.get("state") != "active", -float(e.get("confidence", 0))))
    return {
        "summary": ledger_mod.summary(led),
        "entries": entries,
        "journal": (led.get("journal") or [])[:20],
        "last_change": ledger_mod.diff_text(led),
    }


def forget(params=None):
    """抹掉账本里的一条结论（人有权推翻机器）。"""
    from . import ledger as ledger_mod
    params = params or {}
    target_id = str(params.get("target_id") or "").strip()
    eid = str(params.get("eid") or "").strip()
    if not target_id or not eid:
        return {"error": "需要 target_id 与 eid"}
    led = ledger_mod.load(target_id)
    removed = None
    for sec, items in (led.get("entries") or {}).items():
        keep = []
        for e in items or []:
            if e.get("eid") == eid:
                removed = e
                continue
            keep.append(e)
        led["entries"][sec] = keep
    if removed is None:
        return {"error": "账本里没有这条结论"}
    led.setdefault("journal", []).insert(0, {
        "at": ledger_mod._now(), "run": led.get("runs", 0),
        "level": led.get("level", ""), "events": {"retired": 1},
        "changes": [{"label": removed.get("label"), "event": "retired",
                     "reason": "由人工直接抹除"}],
        "meta": {"operator": "human"},
    })
    ledger_mod.save(led)
    audit_logger.log("ledger_forget", subject=target_id, outcome="forgotten",
                     detail=f"{eid} :: {removed.get('label')}")
    return {"success": True, "removed": removed.get("label")}


def distill_status(params=None):
    p = distill_engine.get_progress()
    p["running"] = distill_engine.is_running()
    return p


def pending_review(params=None):
    pending = review_manager.get_pending()
    if not pending:
        return {"has_pending": False}
    return {
        "has_pending": True,
        "id": pending["id"],
        "target_id": pending["target_id"],
        "target_name": pending.get("target_name", ""),
        "level": pending["level"],
        "mode": pending.get("mode", "full"),
        "delta_messages": pending.get("delta_messages", 0),
        "data_volume": pending.get("data_volume", 0),
        "raw_volume": pending.get("raw_volume", 0),
        "tokens_used": pending.get("tokens_used", 0),
        "features": pending.get("features", {}),
        "patterns": pending.get("patterns", {}),
        "stats": pending.get("stats", {}),
        "counts": pending.get("counts", {}),
        "quality": pending.get("quality", {}),
        "ledger_plan": pending.get("ledger_plan"),
    }


def approve_review(params=None):
    params = params or {}
    features = params.get("features")
    return review_manager.approve(features=features)


def discard_review(params=None):
    return review_manager.discard()


def history(params=None):
    return {"distillations": review_manager.get_history()}


def detail(params=None):
    params = params or {}
    rid = str(params.get("id") or "")
    if not rid:
        return {"error": "缺少 id"}
    record = review_manager.get_record(rid)
    if not record:
        return {"error": "未找到蒸馏记录"}
    # 预览：取前 3000 字符
    content = review_manager.get_persona_content(rid)
    record["persona_preview"] = content[:3000]
    record["provenance"] = _provenance_links(record)
    record["persona_prompt"] = review_manager.get_persona_prompt(rid)
    return {"distillation": record}


def persona(params=None):
    params = params or {}
    rid = str(params.get("id") or "")
    if not rid:
        return {"error": "缺少 id"}
    return {"persona": review_manager.get_persona_content(rid)}


def persona_prompt(params=None):
    """人格注入卡：可直接作为 system prompt 喂给任意模型。"""
    params = params or {}
    rid = str(params.get("id") or "").strip()
    if not rid:
        pending = review_manager.get_pending()
        if pending:
            record = review_manager.with_evidence(pending)
            return {"prompt": personality_model.build_injection_prompt(record),
                    "target_id": pending.get("target_id", ""),
                    "level": pending.get("level", "")}
        return {"error": "缺少 id，且当前没有待审核结果"}
    prompt = review_manager.get_persona_prompt(rid)
    if not prompt:
        return {"error": "未找到蒸馏记录"}
    record = review_manager.get_record(rid) or {}
    return {"prompt": prompt,
            "target_id": record.get("target_id", ""),
            "level": record.get("level", "")}


def delete(params=None):
    params = params or {}
    rid = str(params.get("id") or "")
    if not rid:
        return {"error": "缺少 id"}
    return review_manager.delete_record(rid)


def audit(params=None):
    return {"entries": audit_logger.get_entries(limit=100)}


def export(params=None):
    """导出内容给前端下载：人格文件 / 人格注入卡 / 证据附卷 / 账本。"""
    params = params or {}
    what = str(params.get("what") or "persona")
    rid = str(params.get("id") or "").strip()
    if not rid:
        pending = review_manager.get_pending()
        rid = pending["id"] if pending else ""
    record = review_manager.get_record(rid) if rid else None
    if record is None and review_manager.get_pending():
        record = review_manager.with_evidence(review_manager.get_pending())
    if record is None:
        return {"error": "没有可导出的记录"}

    target = record.get("target_id", "unknown")
    level = record.get("level", "L0")
    base = f"{target}_{level}"
    if what == "prompt":
        return {"filename": f"{base}.prompt.md",
                "content": personality_model.build_injection_prompt(record)}
    if what == "appendix":
        return {"filename": f"{base}.evidence.md",
                "content": personality_model.build_evidence_appendix(record)}
    if what == "ledger":
        from . import ledger as ledger_mod
        return {"filename": f"{base}.ledger.json",
                "content": json.dumps(
                    {"record": record,
                     "ledger": ledger_mod.load(target)},
                    ensure_ascii=False, indent=2)}
    if what == "markdown_raw":
        return {"filename": f"{base}.json",
                "content": json.dumps(record, ensure_ascii=False, indent=2)}
    # 默认：人格文件正文
    content = (review_manager.get_persona_content(rid) if record.get("id")
               and review_manager.get_record(record["id"])
               else personality_model.build_persona(record))
    return {"filename": f"{base}.md", "content": content}


def publish(params=None):
    """把蒸馏注入卡发布给 bot（写发布文件 + touch personality.txt 刷 bot 缓存）。

    params.action: 缺省=发布；disable=下线回手写人设；rollback=回滚上一份快照
    """
    params = params or {}
    from . import publisher
    action = params.get("action")
    if action == "disable":
        return publisher.disable()
    if action == "rollback":
        return publisher.rollback()
    if action == "status":
        return publisher.status()
    return publisher.publish(params.get("target_id"), enabled=True)


def publish_status(params=None):
    """当前发布状态（是否启用、目标、快照数）。"""
    from . import publisher
    return publisher.status()


def render(params=None):
    """零 LLM 调用，用账本重新印一份人格文件（换等级看、或同步确定性修正）。"""
    params = params or {}
    target = str(params.get("target_id") or "").strip()
    if not target:
        hist = review_manager.get_history()
        target = str(hist[0].get("target_id")) if hist else ""
    if not target:
        return {"error": "缺少 target_id"}
    return review_manager.render_from_ledger(target, params.get("level"))


def insight(params=None):
    """把这一轮的洞察（热力图/薄弱区/矛盾/锚点）单独取出来。"""
    params = params or {}
    rid = str(params.get("id") or "").strip()
    record = review_manager.get_record(rid) if rid else None
    if record is None:
        record = review_manager.get_pending()
    if not record:
        return {"error": "没有可用的蒸馏结果"}
    return {
        "insight": (record.get("quality") or {}).get("insight") or {},
        "coverage": (record.get("quality") or {}).get("coverage") or {},
        "source_notes": record.get("source_notes") or [],
        "source_missing": record.get("source_missing") or [],
        "facts_by_kind": record.get("facts_by_kind") or {},
    }


def turing_test(params=None):
    """图灵测试：用蒸馏人格生成回复，与目标本人的真实回复做盲测对比。

    方向要摆正：被蒸馏的是**目标这个人**，所以拿"对方说的话"当刺激，
    比对"这个人当时怎么回"。反过来测就成了在测机器人。
    """
    params = params or {}
    rid = str(params.get("id") or "")
    rounds = max(1, min(10, int(params.get("rounds") or 5)))
    persona_md = review_manager.get_persona_content(rid) if rid else ""
    if not persona_md:
        return {"error": "未找到人格文件"}

    samples = _real_samples(rid, limit=rounds * 3)
    if not samples:
        return {"status": "pending", "score": None,
                "detail": "没有成对的历史对话样本可做盲测对比"}

    client, chan = _get_deep_client()
    if client is None:
        return {"status": "pending", "score": None,
                "detail": "拿不到 AI 客户端：主程序会经插件管理器注入；"
                          "也可在代理配置里填好 API Key 后自动构造。"}

    from . import turing_validator, llm as _llm

    gen = _llm.LLM(client, max_calls=rounds + 3, max_tokens=500,
                   rpm=int(get_config().get("llm_rpm", 3)),
                   empty_retries=1, on_log=lambda s: None)

    def reply_fn(persona, prompt):
        return gen.text(
            "你正在扮演下面人格文件描述的那个人。以他的口吻、语气、长度习惯回复。"
            "不要解释、不要提及人格文件，直接说这句话。\n\n" + (persona or "")[:6000],
            prompt, max_tokens=500, tag="turing/gen")

    def judge_fn(items):
        """一次调用盲判全部轮次 —— 上游 3 RPM，不能一轮一次。"""
        blocks = []
        for i, it in enumerate(items):
            blocks.append(
                f"【第 {i} 轮】\n刺激（对方说的话）：{it['prompt'][:120]}\n"
                f"回复A（真实）：{it['real'][:160]}\n回复B（模仿）：{it['generated'][:160]}")
        data, _ = gen.json(
            "你是盲测裁判。下面是同一句刺激下的两份回复：A 是这个人真实说过的，"
            "B 是照着人格文件模仿出来的。判断 B 是否与 A 出自同一个人 —— "
            "看说话长度、语气词、标点习惯、用词和态度，而不是看内容是否切题。\n"
            "只输出 JSON："
            '{"verdicts":[{"index":0,"same":true,"reason":"一句话理由"}]}',
            "\n\n".join(blocks), max_tokens=1200, tag="turing/judge")
        return data if isinstance(data, dict) else {}

    result = turing_validator.validate(persona_md, test_pairs=samples,
                                       reply_fn=reply_fn, judge_fn=judge_fn,
                                       rounds=rounds)
    result["channel"] = chan
    result["llm"] = gen.summary()

    # ① 按维度报告"哪里不像"（确定性对比，不额外花调用）
    from . import turing_feedback as tf
    report = tf.dimension_report(result.get("rounds") or [])
    result["dimensions"] = report
    result["verdict"] = report.get("verdict", "")

    # ② 结果写回账本：图灵测试是衡量"像不像"的唯一硬指标，不能测完就扔
    record = review_manager.get_record(rid)
    if record:
        from . import ledger as ledger_mod
        led = ledger_mod.load(record.get("target_id"))
        if led.get("entries"):
            led["turing"] = {
                "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "status": result.get("status"),
                "score": result.get("score"),
                "method": result.get("method"),
                "rounds": len(result.get("rounds") or []),
                "matches": sum(1 for r in (result.get("rounds") or []) if r.get("same")),
                "verdict": report.get("verdict", ""),
                "mismatches": [m.get("维度") for m in report.get("mismatches") or []],
            }
            ledger_mod.save(led)
            result["ledger_saved"] = True
    return result


def _real_samples(rid, limit=10):
    """收集成对样本：prompt = 机器人当时说的话，real_reply = 目标本人的回复。"""
    record = review_manager.get_record(rid)
    if not record:
        return []
    target_id = record.get("target_id", "")
    out = []
    for db_path in data_collector._existing_db_paths():
        try:
            import sqlite3
            conn = sqlite3.connect(db_path)
            conn.text_factory = str
            rows = conn.execute(
                "SELECT bot_reply, user_msg FROM messages WHERE scope_id=? "
                "AND user_msg IS NOT NULL AND bot_reply IS NOT NULL "
                "AND user_msg != '' AND bot_reply != '' ORDER BY timestamp DESC LIMIT ?",
                (str(target_id), limit),
            ).fetchall()
            conn.close()
        except Exception:
            continue
        for bot, user in rows:
            out.append({"prompt": str(bot)[:200], "real_reply": str(user)[:200]})
        break
    return out


def _llm_text(client, persona, prompt, max_tokens=500):
    """降级用：直接用某个客户端以人格文件为背景生成一段回复。"""
    try:
        messages = [
            {"role": "system", "content": (
                "你正在扮演以下人格文件描述的人。请以这个人的口吻、语气、风格回复，"
                "不要解释、不要提及人格文件，直接说出这句话。\n\n" + persona[:4000]
            )},
            {"role": "user", "content": prompt},
        ]
        result = client.call(messages, max_tokens=max_tokens)
        if isinstance(result, tuple):
            result = result[0] if len(result) > 0 else None
        return str(result or "").strip()
    except Exception as e:
        print(f"[PersonaDistill] LLM 生成失败: {e}")
        return ""


def _provenance_links(record):
    links = []
    src_map = {
        "chat_logs": "聊天记录 Markdown",
        "super_memory": "记忆库 SQLite",
        "sandglass": "落沙捕获",
        "private_memory": "私聊记忆 JSON",
        "external": "外部文本导入",
    }
    for src, label in src_map.items():
        links.append(f"{label} ({src})")
    return links[:6]


def get_config_action(params=None):
    return get_config()


def set_config_action(params=None):
    return set_config(params or {})


def get_api_status():
    """插件管理器 API 状态检测。"""
    pending = review_manager.has_pending()
    running = distill_engine.is_running()
    return {
        "ok": True,
        "status": "运行中" if running else ("有待审核结果" if pending else "空闲"),
        "detail": f"distilling={running} pending_review={pending}",
    }


# ── 灵魂守护（脆弱度/指纹/对比/封印） ──
from . import soul as _soul


def soul_status(params=None):
    """某人（或某蒸馏记录）的脆弱度 + 指纹 + 封印状态。"""
    params = params or {}
    rid = str(params.get("id") or "").strip()
    if not rid:
        return {"error": "缺少 id"}
    record = review_manager.get_record(rid)
    if not record:
        return {"error": "未找到蒸馏记录"}
    md = review_manager.get_persona_content(rid)
    features = record.get("features") or {}
    frag = _soul.fragility(
        features,
        record.get("level", "L0"),
        int(record.get("data_volume", 0)),
        confidence=record.get("confidence"),
    )
    fp = _soul.fingerprint(md)
    sealed_checksum = record.get("sealed_checksum")
    sealed = bool(sealed_checksum)
    integrity = None
    if sealed:
        integrity = _soul.verify(md, sealed_checksum)
    return {
        "id": rid,
        "level": record.get("level"),
        "fragility": frag,
        "fingerprint": fp,
        "sealed": sealed,
        "integrity": integrity,
        "data_volume": record.get("data_volume", 0),
        "tokens_used": record.get("tokens_used", 0),
    }


def soul_diff(params=None):
    """对比两次蒸馏，返回变化摘要。"""
    params = params or {}
    a = str(params.get("id_a") or "").strip()
    b = str(params.get("id_b") or "").strip()
    if not a or not b:
        return {"error": "缺少 id_a / id_b"}
    md_a = review_manager.get_persona_content(a)
    md_b = review_manager.get_persona_content(b)
    if not md_a or not md_b:
        return {"error": "两份人格文件都需要存在"}
    return _soul.soul_diff(md_a, md_b)


def seal(params=None):
    """封印某人的人格文件：写入校验和，之后可验完整性。"""
    params = params or {}
    rid = str(params.get("id") or "").strip()
    md = review_manager.get_persona_content(rid)
    if not md:
        return {"error": "人格文件不存在"}
    sealed = _soul.seal(md)
    review_manager.set_seal(rid, sealed["checksum"], sealed["fingerprint"])
    audit_logger.log("soul_seal", subject=record_target(rid), outcome="sealed", detail=sealed["fingerprint"])
    return {"ok": True, "fingerprint": sealed["fingerprint"], "checksum": sealed["checksum"]}


def verify(params=None):
    """校验封印是否完好。"""
    params = params or {}
    rid = str(params.get("id") or "").strip()
    record = review_manager.get_record(rid)
    if not record:
        return {"error": "未找到蒸馏记录"}
    md = review_manager.get_persona_content(rid)
    sealed_checksum = record.get("sealed_checksum")
    if not sealed_checksum:
        return {"sealed": False, "intact": None, "detail": "未封印"}
    v = _soul.verify(md, sealed_checksum)
    audit_logger.log("soul_verify", subject=record_target(rid), outcome=("intact" if v["intact"] else "broken"))
    return v


def record_target(rid):
    record = review_manager.get_record(rid) or {}
    return record.get("target_id", "")


# 动作注册表（__init__.py 的 PLUGIN_ACTIONS 引用本表）
ACTIONS = {
    "stats": stats,
    "scan_data": scan_data,
    "start_distill": start_distill,
    "distill_status": distill_status,
    "pending_review": pending_review,
    "approve_review": approve_review,
    "discard_review": discard_review,
    "history": history,
    "detail": detail,
    "persona": persona,
    "persona_prompt": persona_prompt,
    "delete": delete,
    "audit": audit,
    "turing_test": turing_test,
    "soul": soul_status,
    "soul_diff": soul_diff,
    "ledger": ledger,
    "forget": forget,
    "export": export,
    "insight": insight,
    "render": render,
    "publish": publish,
    "publish_status": publish_status,
    "get_config": get_config_action,
    "set_config": set_config_action,
    "seal": seal,
    "verify": verify,
}
