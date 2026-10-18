# -*- coding: utf-8 -*-
"""这样脆弱的东西 蒸馏引擎 v2.2。

流水线（每一级都在做上一级做不到的事）：

  L0 捕获  全部数据源（含群聊/平台事实）→ 跟账本对水位 → 取新增 → 跨轮稳定编号
  L1 基线  启发式量化（用全量发言算，保证统计量一直是对的）
  L2 取证  **分块**把语料全部喂给 LLM（每批 × 每块一次），逐层捞观察
  L3 定稿  逐批合并 → 跨批合并 → 重估置信度
  L4 对账  把结论送回原文交终审，抓编造引用与过度推断并补漏
  洞察     确定性算出力所不及的部分：热力图 / 薄弱区 / 矛盾 / 锚点
  记账     账本状态机决定每条结论的生老病死
  构建     渲染 Persona-File + 人格注入卡 + 证据附卷

和上一版的区别（都是"看起来做到了其实没做"的地方）：

  1. **语料不再被静默截断**。以前超 6 万字符就砍掉多余的，
     现在按块全量投喂，每批每块各一次调用，一块都不少。
  2. **预算不够会明说**。以前超预算就中途停下，用户不知道少了什么；
     现在 `sections.plan_calls` 先算账，按优先级降级，
     并在产出里如实报告"这轮覆盖了哪些层、哪些没跑、下轮补"。
  3. **断点续跑**。没跑完的层记进账本，下一轮自动排到最前面。
  4. **平台事实是一等证据**。好感度、群黑话、群情绪、对话链都有编号，
     可以像发言一样被引用。
  5. **S8 不再永远是空的**。可推导的部分（热力图/薄弱区/一致性/锚点）
     在任何等级都会算出来。
"""
import threading
import time
import uuid

from . import data_collector, feature_extractor, pattern_analyzer
from . import level_classifier, review_manager, audit_logger, evidence, sections
from . import insight as insight_mod
from . import ledger as ledger_mod
from .config import get_config
from .llm import LLM, BudgetExceeded

# ── 进度（模块级，进程内共享；UI 每秒轮询） ──────────────
_PROGRESS = {
    "running": False,
    "progress": 0,
    "stage": "",
    "data_count": 0,
    "tokens_used": 0,
    "confidence": 0,
    "log": [],
    "error": None,
    "done": False,
    "result_id": None,
    "stages": [],      # [{sid, title, status, entries}]
    "quality": {},     # 质量与覆盖报告
    "mode": "",
}
_LOCK = threading.Lock()


def get_progress():
    with _LOCK:
        return dict(_PROGRESS)


def _set(**kwargs):
    with _LOCK:
        _PROGRESS.update(kwargs)


def _log(line):
    with _LOCK:
        _PROGRESS["log"] = (_PROGRESS["log"] + [line])[-300:]
        _PROGRESS["stage"] = line


def _stage(sid, title, status, entries=0):
    with _LOCK:
        stages = list(_PROGRESS.get("stages") or [])
        for s in stages:
            if s["sid"] == sid:
                s.update(status=status, entries=entries)
                break
        else:
            stages.append({"sid": sid, "title": title,
                           "status": status, "entries": entries})
        _PROGRESS["stages"] = stages


def is_running():
    with _LOCK:
        return _PROGRESS["running"]


def reset_progress():
    with _LOCK:
        _PROGRESS.update(running=False, progress=0, stage="", data_count=0,
                         tokens_used=0, confidence=0, log=[], error=None,
                         done=False, result_id=None, stages=[], quality={},
                         mode="")


def start(target_id, target_name="", level=None, sources=None,
          external_text="", consent=True, mode="auto"):
    """启动蒸馏（后台线程）。consent 必须为 True（UI 已强制确认伦理）。"""
    if is_running():
        return {"success": False, "error": "已有蒸馏任务在运行"}
    if not consent:
        return {"success": False, "error": "未确认伦理同意，拒绝蒸馏"}

    t = threading.Thread(target=_run, kwargs={
        "target_id": str(target_id).strip(),
        "target_name": target_name,
        "level": level,
        "sources": sources,
        "external_text": external_text,
        "mode": mode or "auto",
    }, daemon=True)
    _set(running=True, progress=0, stage="准备中", data_count=0, tokens_used=0,
         confidence=0, log=[], error=None, done=False, result_id=None,
         stages=[], quality={}, mode=mode or "auto")
    t.start()
    return {"success": True, "message": "蒸馏已启动"}


# ── 主流程 ──────────────────────────────────────────────

def _run(target_id, target_name, level, sources, external_text, mode="auto"):
    distillation_id = ("dist_" + time.strftime("%Y%m%d_%H%M%S")
                       + "_" + uuid.uuid4().hex[:4])
    cfg = get_config()
    audit_logger.log("distill_start", subject=target_id, level=level or "",
                     consent_status="confirmed", operator="webui", detail=mode)
    try:
        # ── L0 捕获 ──
        _set(progress=3)
        _log("L0 捕获：读取全部数据源")
        data = data_collector.collect(
            target_id, sources=sources,
            max_per_source=int(cfg.get("max_samples_per_source", 50000)))
        for note in data.get("notes") or []:
            _log(f"  源状态 {note}")
        for miss in data.get("missing") or []:
            _log(f"  ⚠ 缺 {miss['source']}：{miss['reason']}（{miss['impact']}）")

        if external_text and external_text.strip():
            for line in external_text.splitlines():
                line = line.strip()
                if not line:
                    continue
                for chunk in [line[i:i + 500] for i in range(0, len(line), 500)]:
                    data["messages"].append({
                        "text": chunk, "timestamp": "", "source": "external",
                    })
            data["counts"]["external"] = sum(
                1 for m in data["messages"] if m["source"] == "external")

        # 平台结构化事实并入证据池（编号以后可以像发言一样被引用）
        raw_all = list(data["messages"])
        for f in data.get("facts") or []:
            raw_all.append(dict(f, kind="fact"))
        if not raw_all:
            raise ValueError("未收集到任何数据，请检查目标 ID 或数据来源")

        # ── 账本：水位与编号 ──
        continuous = bool(cfg.get("continuous", True))
        led = ledger_mod.load(target_id) if continuous else ledger_mod._empty(target_id)
        delta_raw, raw_total, first_run = ledger_mod.split_delta(led, raw_all)
        has_ledger = bool(led.get("entries"))

        if continuous and has_ledger and not delta_raw and mode != "full":
            s = ledger_mod.summary(led)
            raise ValueError(
                f"没有新增数据（账本已收录全部 {s['seen_messages']} 条，"
                f"已完成 {s['runs']} 轮），这一轮跳过。"
                "想强制重算请把方式设为「全量重蒸馏」。")

        run_mode = decide_mode(cfg, led, delta_raw, mode, first_run)
        if run_mode == "inline_none":
            run_mode = "full"
        _set(mode=run_mode)

        msgs, index, full_map, next_id = evidence.normalize(
            raw_all, id_map=led.get("mid_of_fp"), start=led.get("next_mid", 1))
        if not msgs:
            raise ValueError("收集到的数据全为空内容")

        talks_all = evidence.talks(msgs)
        st = evidence.stats(msgs, raw_total=max(raw_total, len(raw_all)),
                            counts=data["counts"])
        level = level or level_classifier.classify_level(st["unique"])
        feas = level_classifier.feasibility(st["unique"], level)

        new_fps = {evidence.fingerprint(m) for m in delta_raw}
        if run_mode == "incremental":
            picked = [m for m in msgs
                      if any(fp in new_fps for fp in m.get("fps", []))]
            if picked:
                new_msgs = picked
            else:
                # 新增记录被去重合并进了老条目、或者清洗后为空 —— 定位不到，
                # 那就退回全量语料。宁可多读，不可漏读。
                new_msgs = msgs
                if delta_raw:
                    _log(f"注：新增 {len(delta_raw)} 条无法从语料中单独切出"
                         f"（可能已并入已有条目），本轮改用全量语料投喂")
        else:
            new_msgs = msgs

        _set(progress=10, data_count=st["unique"])
        if run_mode == "incremental":
            _log(f"L0 完成（连续蒸馏 · 第 {int(led.get('runs', 0)) + 1} 轮）："
                 f"新增 {len(delta_raw)} 条记录 → {len(new_msgs)} 条可蒸馏条目；"
                 f"账本累计 {st['unique']} 条发言 + {st['fact_count']} 条平台事实")
        else:
            _log(f"L0 完成（{'首次' if first_run else '全量重蒸馏'}）："
                 f"{st['raw_total']} 条原始记录 → {st['unique']} 条独立发言"
                 f" + {st['fact_count']} 条平台事实"
                 f"（折叠重复 {st['merged_duplicates']} 条），"
                 f"{st['chars']} 字，跨度 {st['span']}")
        if st["repeat_total"]:
            _log(f"检测到真实的重复发送 {st['repeat_total']} 次（同一句被反复发）")
        if st["fact_count"]:
            _log(f"平台事实：{'、'.join(f'{k} {v}' for k, v in (data.get('facts_by_kind') or {}).items())}")
        if feas["grade"] != "合适":
            _log(f"等级提示：{feas['hint']}")

        # ── L1 基线（用全量发言算，统计量必须一直正确） ──
        _set(progress=16)
        _log("L1 基线：量化特征提取")
        features = feature_extractor.extract_all(talks_all, level=level)
        patterns = pattern_analyzer.analyze(talks_all)
        for sid in sections.for_level(level):
            if sections.SECTIONS[sid]["key"]:
                _stage(sid, sections.label_for(sid), "基线",
                       len(features.get(sections.key_of(sid)) or []))
        _set(progress=24)
        _log(f"L1 完成：{sum(len(v) for v in features.values())} 条量化特征"
             f" + {len(patterns)} 个统计模式")

        # ── L2/L3/L4 深挖 ──
        quality = {
            "mode": run_mode,
            "unique_messages": st["unique"],
            "raw_messages": st["raw_total"],
            "fact_count": st["fact_count"],
            "delta_messages": len(delta_raw),
            "corpus_for_llm": len(new_msgs),
            "chars": st["chars"],
            "span": st["span"],
            "days": st["days"],
            "feasibility": feas,
            "ledger_runs_before": int(led.get("runs", 0)),
            "source_notes": data.get("notes") or [],
            "source_missing": data.get("missing") or [],
            "facts_by_kind": data.get("facts_by_kind") or {},
            "llm": {"calls": 0, "tokens": 0, "available": False},
            "critic": {"checked": 0, "dropped": 0, "weakened": 0, "added": 0},
            "citations": {"valid": 0, "invalid": 0, "dropped_entries": 0},
        }

        client, chan = _get_deep_client()
        deep_levels = cfg.get("deep_levels") or ["L2", "L3", "L4"]
        plan_sids = sections.llm_sections_for_level(level)
        coverage_note = ""

        if client is not None and cfg.get("llm_enabled", True) and level in deep_levels \
                and plan_sids:
            quality["llm"]["available"] = True
            quality["llm"]["channel"] = chan
            _log(f"深挖通道：{chan}")

            llm = LLM(client,
                      max_calls=int(cfg.get("max_llm_calls", 24)),
                      max_tokens=int(cfg.get("llm_max_tokens", 3000)),
                      rpm=int(cfg.get("llm_rpm", 3)),
                      empty_retries=int(cfg.get("empty_retries", 4)),
                      token_ceiling=int(cfg.get("llm_token_ceiling", 16000)),
                      on_log=_log)

            # 分块：语料再大也不截断，一块一块喂
            chunk_msgs = evidence.chunk(
                new_msgs, chunk_chars=int(cfg.get("chunk_chars", 12000)))
            corpora = [evidence.corpus_text(c) for c in chunk_msgs]
            resume = [s for s in (led.get("pending_sections") or []) if s in plan_sids]

            plan = sections.plan_calls(plan_sids, chunks=len(corpora),
                                       cfg=cfg, resume=resume)
            for line in plan["degraded"]:
                _log(f"⚠ 预算降级：{line}")
            _log(f"L2 取证：{len(plan['batches'])} 批 × {len(corpora)} 块语料"
                 f"（每块约 {len(corpora[0]) if corpora else 0} 字），"
                 f"预计 {plan['estimated']} 次调用（预算 {plan['budget']}）"
                 + ("，已带账本结论做增补" if run_mode == "incremental" else ""))
            if resume:
                _log(f"断点续跑：优先补上一轮未完成的 "
                     f"{'、'.join(sections.SECTIONS[s]['title'] for s in resume)}")

            digest = _digest_for(new_msgs, st, run_mode, led)
            name = target_name or target_id
            batches = plan["batches"]
            mt = int(cfg.get("llm_max_tokens", 3000))
            existing = (sections.existing_block(led.get("entries"))
                        if run_mode == "incremental" else None)

            for sid in [s for b in batches for s in b]:
                _stage(sid, sections.label_for(sid), "取证中")

            # ── L2 map：每批 × 每块 ──
            _set(progress=30)
            jobs = []
            for b, batch in enumerate(batches):
                for c, corpus in enumerate(corpora):
                    jobs.append((
                        f"map{b + 1}.{c + 1}", sections.analyst_system(),
                        sections.map_prompt(batch, name, corpus, digest, len(index),
                                            existing, chunk_hint=(c + 1, len(corpora))),
                        mt))
            try:
                mapped = llm.batch(jobs)
            except BudgetExceeded as e:
                mapped = {}
                _log(f"预算用尽，停止取证：{e}")
            _set(progress=48)

            # 汇总每批的观察
            batch_obs = []
            for b, batch in enumerate(batches):
                merged_obs = {}
                for c in range(len(corpora)):
                    got = _entries_by_section(mapped.get(f"map{b + 1}.{c + 1}"))
                    flat = got.pop("__flat__", None)
                    if flat:
                        got.setdefault(sections.key_of(batch[0]), []).extend(flat)
                    for sid in batch:
                        key = sections.key_of(sid)
                        items = got.get(key) or []
                        if items:
                            merged_obs.setdefault(key, []).extend(items)
                batch_obs.append(merged_obs)
                for sid in batch:
                    key = sections.key_of(sid)
                    n = len(merged_obs.get(key) or [])
                    _stage(sid, sections.label_for(sid), "已取证" if n else "无证据", n)

            observations = {}
            for b, obs in enumerate(batch_obs):
                if len(corpora) > 1 and obs:
                    # 多块语料：先把这一批在各块上的观察合成一次
                    try:
                        data_m, _ = llm.json(
                            sections.analyst_system(),
                            sections.reduce_prompt(batches[b], name, obs,
                                                   "\n\n".join(corpora),
                                                   digest, None,
                                                   stage="块间合并"),
                            max_tokens=mt, tag=f"merge{b + 1}")
                    except BudgetExceeded as e:
                        data_m = None
                        _log(f"预算用尽，跳过块间合并：{e}")
                    if data_m:
                        got = _entries_by_section(data_m)
                        obs = {k: (got.get(k) or v) for k, v in obs.items()}
                for key, items in obs.items():
                    observations.setdefault(key, []).extend(items)

            n_obs = sum(len(v) for v in observations.values())
            _log(f"L2 取证完成：{n_obs} 条带证据的原始观察"
                 f"（覆盖 {len(observations)} 层）")

            # ── L3 定稿 ──
            do_reduce = plan["reduce"] and run_mode == "full"
            if observations and do_reduce:
                _set(progress=62)
                _log("L3 定稿：合并去重、剔除无据、重估置信度")
                baseline = {
                    sections.SECTIONS[sid]["key"]:
                        "；".join(f"{e['label']}={e['value']}"
                                  for e in (features.get(sections.SECTIONS[sid]["key"])
                                            or [])[:8])
                    for sid in plan_sids
                }
                try:
                    data2, _ = llm.json(
                        sections.analyst_system(),
                        sections.reduce_prompt(plan_sids, name, observations,
                                               "\n\n".join(corpora), digest, baseline),
                        max_tokens=mt, tag="reduce")
                except BudgetExceeded as e:
                    data2 = None
                    _log(f"预算用尽，直接用取证结果：{e}")
                final = _entries_by_section(data2) if data2 else {}
                for sid in plan_sids:
                    key = sections.key_of(sid)
                    entries = final.get(key) or observations.get(key) or []
                    features[key] = _merge_entries(features.get(key) or [], entries,
                                                   index, protect_baseline=False)
                    _stage(sid, sections.label_for(sid), "定稿",
                           len(features.get(key) or []))
                _set(progress=74)
                _log(f"L3 定稿完成：共 {sum(len(v) for v in features.values())} 条")
            elif observations:
                for sid in plan_sids:
                    key = sections.key_of(sid)
                    features[key] = _merge_entries(
                        features.get(key) or [], observations.get(key) or [], index,
                        protect_baseline=(run_mode == "incremental"))
                    _stage(sid, sections.label_for(sid), "定稿",
                           len(features.get(key) or []))
                _set(progress=74)
                _log(f"L3 定稿跳过（由账本完成确定性合并）；本轮观察 {n_obs} 条已并入")
            else:
                _log("L3 定稿跳过：没有任何带证据的观察，保留统计基线")

            # ── L4 对账 ──
            if plan["critic"] and observations:
                _set(progress=80)
                _log("L4 对账：把结论送回原文逐条核对")
                quality["critic"] = _critic(
                    llm, features, "\n\n".join(corpora), digest, name, index,
                    [s for b in batches for s in b], mt)
                _log(f"L4 对账完成：核对 {quality['critic']['checked']} 条，"
                     f"剔除 {quality['critic']['dropped']} 条，"
                     f"降权 {quality['critic']['weakened']} 条，"
                     f"补漏 {quality['critic']['added']} 条")

            done_layers = [s for b in batches for s in b
                           if observations.get(sections.SECTIONS[s]["key"])]
            cov = insight_mod.coverage(
                plan_sids, done_layers, plan["uncovered"],
                "；".join(plan["degraded"]) or "全部覆盖")
            quality["coverage"] = cov
            coverage_note = (f"覆盖 {len(cov['done'])}/{len(cov['planned'])} 层"
                             + (f"，未覆盖：{'、'.join(cov['missed'])}" if cov["missed"] else ""))
            _log("覆盖报告：" + coverage_note)
            quality["pending_sections"] = cov["missed"]
            # 用 update 而不是整体替换：summary() 里没有 available / channel，
            # 直接赋值会把它们抹掉 —— 后面判断"这一轮 LLM 到底参没参与"就会误判
            quality["llm"].update(llm.summary())
            _log(f"LLM 用量：{llm.summary()['calls']} 次调用 / "
                 f"{llm.summary()['tokens']:,} tokens，"
                 f"耗时 {llm.summary()['elapsed']}s")
        else:
            reason = ("未接入 AI 客户端" if client is None
                      else "当前等级不使用 LLM 深挖")
            _log(f"深挖跳过（{reason}）：产出为统计 + 推导层")
            quality["coverage"] = insight_mod.coverage(
                plan_sids, [], plan_sids, "本轮未做 LLM 取证")

        # ── 证据校验（用跨轮合并的编号表，历史引用同样有效） ──
        _set(progress=86)
        known = ledger_mod.known_index(led, index)
        quality["citations"] = verify_all_citations(features, known)
        c = quality["citations"]
        _log(f"证据校验：{c['valid']} 处引用有效，{c['invalid']} 处编号不存在"
             + (f"，剔除 {c['dropped_entries']} 条纯编造条目" if c["dropped_entries"] else ""))

        # ── 记账 ──
        _set(progress=88)
        # 证据级清洗：被隔离的消息其指纹还在账本里 → 找到对应编号，
        # 引用它们的结论跟着降权/退休。否则被隔离的数据会通过旧结论
        # 继续影响人格（实测：'我喜欢吃火锅' 的 M0052 就是这样漏的）。
        quar_mids = []
        if data.get("quarantine_items") and continuous:
            fp_map = led.get("mid_of_fp") or {}
            for q in data["quarantine_items"]:
                fp = q.get("fp")
                if fp and fp in fp_map:
                    mid = fp_map[fp]
                    if mid not in quar_mids:
                        quar_mids.append(mid)
        if quar_mids and continuous:
            purged = ledger_mod.purge_quarantined(led, quar_mids)
            if purged["retired"] or purged["weakened"]:
                _log(f"证据级清洗：退休 {len(purged['retired'])} 条、"
                     f"降权 {len(purged['weakened'])} 条"
                     f"（证据被隔离的消息 {len(quar_mids)} 个编号）")
        run_meta = {
            "mode": run_mode,
            "delta_messages": len(delta_raw),
            "first_run": first_run,
            "corpus_total": st["unique"],
            "llm_calls": quality["llm"].get("calls", 0),
            "uncovered": (quality.get("coverage") or {}).get("missed") or [],
        }
        # 这一轮 LLM 到底参没参与：没参与就只能算"统计刷新"，
        # 不允许统计值去改写模型得出的结论
        stats_only = not quality["llm"].get("available")
        if stats_only:
            _log("本轮 LLM 未参与（等级未到深挖线或无客户端）："
                 "只刷新统计条目，不改写已有结论")
        run_meta["stats_only"] = stats_only
        planned = ledger_mod.plan(led, features, run_meta, cfg,
                                  stats_only=stats_only) if continuous else None
        if planned:
            ev = planned.get("events") or {}
            _log("记账：" + ("、".join(f"{ledger_mod.EVENT_LABEL.get(k, k)} {v}"
                                      for k, v in ev.items()) or "本轮无变化"))

        # ── 风格画像 + 科学测量层（确定性，不花调用） ──
        try:
            from . import style_profile as style_mod
            from . import psych as psych_mod
            style = style_mod.build(talks_all, exemplar_count=int(
                cfg.get("exemplar_count", 12)))
            style_feats = style_mod.to_features(style)
            for sec, items in style_feats.items():
                have = {e.get("label") for e in (features.get(sec) or [])}
                for e in items:
                    if e["label"] in have:
                        continue
                    features.setdefault(sec, []).append(e)
                    have.add(e["label"])
            measures = psych_mod.build(talks_all, style, data.get("facts") or [])
            psych_feats = psych_mod.to_features(measures)
            for sec, items in psych_feats.items():
                have = {e.get("label") for e in (features.get(sec) or [])}
                for e in items:
                    if e["label"] in have:
                        continue
                    features.setdefault(sec, []).append(e)
                    have.add(e["label"])
            quality["style"] = {k: v for k, v in style.items()
                                if k in ("length", "punct", "burst", "by_hour",
                                         "by_period", "by_context")}
            quality["measures"] = measures
            # 关系分桶：对不同对象说话方式不同 —— 这是关系层最缺的一块
            try:
                from . import relational as rel_mod
                from . import bot_identity
                rel = rel_mod.build(talks_all, DC.nickname_map(),
                                    bot_names=bot_identity.bot_names())
                for e in rel_mod.to_features(rel):
                    have = {x.get("label") for x in (features.get("relationship") or [])}
                    if e["label"] not in have:
                        features.setdefault("relationship", []).append(e)
                quality["relational"] = rel
                top = list(rel.get("buckets").items())[:3]
                _log("关系分桶：" + "；".join(
                    f"{info['label']} {info['n']} 条" for _, info in top))
            except Exception as _re:
                _log(f"关系分桶失败（不影响其余产出）：{_re}")
            _log(f"风格画像：平均 {style['length'].get('avg', 0)} 字 / "
                 f"中位 {style['length'].get('median', 0)} / P90 {style['length'].get('p90', 0)}；"
                 f"连发平均 {style['burst'].get('avg_size', 0)} 条；"
                 f"例句 {len(style.get('exemplars') or [])} 条")
            bf = (measures.get("psychology") or {}).get("big_five_proxies") or {}
            if bf:
                _log("科学测量层（行为代理，非诊断）：" + "、".join(
                    f"{k}={v.get('方向', '—')}" for k, v in bf.items()))
        except Exception as e:
            _log(f"风格画像/科学测量层失败（不影响其余产出）：{e}")
            style, measures = None, None

        # ── 名字方向守卫（确定性） ──
        # 必须跑在**账本最终状态**上，而不是本轮特征上：
        # 有问题的结论往往正是本轮没被重新提到的那条（它从上一轮留了下来），
        # 只查本轮特征会漏掉它 —— 这正是第一次实现时踩的坑。
        naming = []
        if planned:
            from . import bot_identity
            naming = insight_mod.naming_guard(planned.get("entries") or {}, msgs,
                                              bot_names=bot_identity.bot_names())
        else:
            from . import bot_identity
            naming = insight_mod.naming_guard(features, msgs,
                                              bot_names=bot_identity.bot_names())
        if naming:
            _log(f"名字方向守卫：{len(naming)} 条身份结论的可信度被下调"
                 f"（名字出现在他 @ 的对象位置或转发的发言者标签里）")
            for n in naming:
                _log(f"  ⚠ 「{n['name']}」{'；'.join(n['where'])}，"
                     f"{n['label']} {n['from']:.0%} → {n['to']:.0%}")

        # ── 洞察（确定性，不花调用） ──
        view = ledger_mod.view(planned) if planned else None
        entry_states = (view or {}).get("entries") or {
            k: [dict(e, state="active") for e in (v or [])]
            for k, v in features.items()}
        quality["insight"] = _insight(entry_states, level, quality, st)
        quality["insight"]["naming_warnings"] = naming

        # ── 构建 ──
        _log("构建：渲染人格文件与可注入提示词")
        payload = _payload(distillation_id, target_id, target_name, level, st,
                          data, features, patterns, known, cfg, quality,
                          view, ledger_mod.new_fingerprints(led, full_map),
                          _quotes_of(index, cfg), run_mode, len(delta_raw),
                          coverage_note, style=style, measures=measures)
        markdown = _build(payload)
        tokens_used = estimate_tokens(markdown)

        review_manager.save_pending(payload)
        conf = _avg_conf(features)
        _set(progress=100, tokens_used=tokens_used, confidence=conf, done=True,
             result_id=distillation_id, quality=quality)
        _log(f"完成：{level} 等级 · {run_mode} 模式，{len(markdown)} 字"
             f"（约 {tokens_used:,} tokens），待人工审核")
        audit_logger.log("distill_done", subject=target_id, level=level,
                         data_volume=st["unique"], token_usage=tokens_used,
                         outcome="pending_review", detail=distillation_id)
    except Exception as e:
        _set(error=str(e), running=False)
        _log(f"错误：{e}")
        audit_logger.log("distill_error", subject=target_id, level=level or "",
                         outcome="error", detail=str(e)[:300])
        return
    finally:
        _set(running=False)


# ── 辅助 ────────────────────────────────────────────────

def decide_mode(cfg, led, delta_raw, requested="auto", first_run=True):
    """决定这一轮走全量还是增量。抽成纯函数是为了能单独验证。

    规则（按优先级）：
      1. 关掉连续蒸馏、或账本还是空的 → full（第一次必须从头建立）
      2. 用户显式指定 → 听用户的
      3. 新增语料不超过 incremental_max_messages → incremental
      4. 否则 → full（数据量跳变时重新推导，免得增量拼出来的人不连贯）
    """
    if not cfg.get("continuous", True) or first_run or not (led.get("entries") or {}):
        return "full"
    if requested in ("full", "incremental"):
        return requested
    if not delta_raw:
        return "inline_none"
    if len(delta_raw) <= int(cfg.get("incremental_max_messages", 300)):
        return "incremental"
    return "full"


def _get_deep_client():
    """取蒸馏专用客户端（关掉思考的独立通道）。返回 (client, note)。"""
    try:
        from . import api_server as _api
        return _api._get_deep_client()
    except Exception as e:
        return None, f"客户端获取失败：{e}"


def _digest_for(new_msgs, st, run_mode, led):
    """给模型的原料清单。连续蒸馏时要说明「这是第几轮、新增多少」。"""
    base = evidence.digest(new_msgs)
    if run_mode != "incremental":
        return base
    runs = int(led.get("runs", 0)) + 1
    return (f"这是连续蒸馏的第 {runs} 轮，只给你看**新增语料**：\n{base}\n"
            f"（账本里已经有 {st['unique']} 条历史发言的累计认识，"
            f"下面会一并给出已有结论，你只需要做增补与修正。）")


def _insight(entry_states, level, quality, st):
    """确定性洞察：热力图 / 薄弱区 / 矛盾 / 锚点 / 触发-反应。

    刻意不把 reconstruction 放进热力图：它是前面各层的**合成**，
    不是一层独立证据。把它算进覆盖率只会得到"永远很薄"的假警报。
    """
    expected = {"identity": 6, "linguistics": 8, "behavior": 6, "cognition": 6,
                "relationship": 5, "temporal": 4, "environmental": 4}
    llm_keys = {sections.SECTIONS[s]["key"]
                for s in sections.llm_sections_for_level(level)}
    heat = insight_mod.heatmap(entry_states, expected, llm_keys)
    con = insight_mod.contradictions(entry_states)
    return {
        "heatmap": heat,
        "weak_areas": insight_mod.weak_areas(heat),
        "contradictions": con,
        "anchors": insight_mod.anchors(entry_states),
        "reflexes": insight_mod.reflexes(entry_states),
        "verdict": insight_mod.reflex_note(heat),
        "coverage": quality.get("coverage") or {},
        "counts": {"发言": st["unique"], "平台事实": st["fact_count"]},
    }


def _quotes_of(index, cfg):
    """把编号 → 原文摘出来，随 pending 一起存。

    这里存的是**较长的原文**（默认 200 字），因为证据附卷要给出可直接核对的原文；
    人格文件里为了可读才截到 60 字 —— 截断发生在渲染时，不在这里。
    """
    limit = int(cfg.get("evidence_map_limit", 5000))
    cap = int(cfg.get("quote_chars", 200))
    out = {}
    for mid, m in list(index.items())[:limit]:
        text = m.get("text") or ""
        out[mid] = {"q": (text if len(text) <= cap else text[:cap] + "…"),
                    "d": m.get("date") or "", "h": m.get("hour"),
                    "k": m.get("kind", "message")}
    return out


def _entries_by_section(data):
    """从模型返回里按层取出 entries。

    容忍两种形态：
      {"sections": {"identity": {"entries": [...]}}}   主用
      {"entries": [...]}                               扁平兜底
    """
    out = {}
    if isinstance(data, dict):
        secs = data.get("sections")
        if isinstance(secs, dict):
            for key, val in secs.items():
                items = val.get("entries") if isinstance(val, dict) else val
                if isinstance(items, list):
                    out[str(key)] = [x for x in items if isinstance(x, dict)]
        elif isinstance(data.get("entries"), list):
            out["__flat__"] = [x for x in data["entries"] if isinstance(x, dict)]
    elif isinstance(data, list):
        out["__flat__"] = [x for x in data if isinstance(x, dict)]
    return out


def _norm_ids(seq, index):
    """把各种写法的编号统一成 M0007，并分出存在 / 不存在。"""
    valid, invalid = [], []
    for raw in seq or []:
        digits = "".join(ch for ch in str(raw) if ch.isdigit())
        if not digits:
            continue
        mid = f"M{int(digits):04d}"
        if mid in index:
            if mid not in valid:
                valid.append(mid)
        elif mid not in invalid:
            invalid.append(mid)
    return valid, invalid


def _norm_entries(raw, index, source, min_conf=0.15):
    """规范化模型条目 + 校验引用。返回 (kept, invalid_count, dropped_count)。

    低于 `min_conf` 的条目直接丢掉：模型在证据不足时会产出
    「语料不足」配 0% 置信度的占位条目，那不是结论，只是把"不知道"
    写成了条目，留在文件里纯属噪声（热力图与薄弱区已经专门说这件事了）。
    """
    kept, invalid_total, dropped = [], 0, 0
    for it in raw:
        label = str(it.get("label") or "").strip()[:24]
        value = str(it.get("value") or "").strip()[:300]
        if not label or not value:
            continue
        try:
            conf = float(it.get("confidence", 0.5))
        except (TypeError, ValueError):
            conf = 0.5
        conf = max(0.0, min(1.0, conf))
        if conf < min_conf:
            dropped += 1
            continue

        ev, bad_ev = _norm_ids(it.get("evidence"), index)
        counter, bad_counter = _norm_ids(it.get("counter"), index)
        invalid_total += len(bad_ev) + len(bad_counter)

        if it.get("evidence") and not ev:
            dropped += 1
            continue
        if bad_ev:
            conf = round(conf * 0.6, 2)
        kept.append({
            "label": label, "value": value, "confidence": conf,
            "evidence": ev, "counter": counter, "source": source,
        })
    return kept, invalid_total, dropped


def _merge_entries(baseline, entries, index, protect_baseline=False):
    """合并：LLM 定稿在前，统计基线里没被覆盖的条目补在后面。

    protect_baseline（增量模式）要解决一个很实际的问题：
    统计基线是全量语料算出来的，而增量轮次的模型只看得到**新增的那一小段**。
    于是像「高频词」「语气词」这种累计型维度，模型会用新片段里的窄结论
    去"改写"掉完整的统计值，越改越薄。
    所以同一个 label 两边都有时，**谁更硬听谁的**。
    """
    llm_entries, _, _ = _norm_entries(entries, index, "llm")
    by_label = {b.get("label"): b for b in (baseline or []) if b.get("label")}
    merged, used = [], set()
    for e in llm_entries:
        b = by_label.get(e["label"])
        if b and protect_baseline and float(b.get("confidence", 0)) >= float(
                e.get("confidence", 0)):
            merged.append({
                "label": b.get("label", ""),
                "value": b.get("value", ""),
                "confidence": float(b.get("confidence", 0.5)),
                "evidence": _union_ev(b.get("evidence"), e.get("evidence")),
                "counter": list(e.get("counter") or []),
                "source": b.get("source", "heuristic"),
            })
        else:
            merged.append(e)
        used.add(e["label"])
    for b in (baseline or []):
        if b.get("label") in used:
            continue
        merged.append({
            "label": b.get("label", ""),
            "value": b.get("value", ""),
            "confidence": float(b.get("confidence", 0.5)),
            "evidence": list(b.get("evidence") or []),
            "counter": [],
            "source": b.get("source", "heuristic"),
        })
    merged.sort(key=lambda e: -float(e.get("confidence", 0)))
    return merged[:12]


def _union_ev(a, b):
    out = list(a or [])
    for x in b or []:
        if x and x not in out:
            out.append(x)
    return out


def _critic(llm, features, corpus, digest, name, index, plan, mt):
    """终检：逐条对账，砍编造、降薄弱、补遗漏。"""
    summary = {"checked": 0, "dropped": 0, "weakened": 0, "added": 0}
    lines = []
    for sid in plan:
        key = sections.key_of(sid)
        for e in features.get(key) or []:
            lines.append(f"[{key}] {e['label']}：{e['value']} "
                         f"(证据 {','.join(e.get('evidence') or []) or '无'})")
    if not lines:
        return summary
    try:
        data, _ = llm.json(sections.analyst_system(),
                           sections.critic_prompt(name, "\n".join(lines), corpus, digest),
                           max_tokens=mt, tag="critic")
    except BudgetExceeded as e:
        llm._note(f"终检跳过（预算用尽）：{e}")
        return summary
    if not isinstance(data, dict):
        llm._note("终检未返回可解析结果，跳过")
        return summary

    verdicts = {}
    for v in data.get("verdicts") or []:
        if isinstance(v, dict) and v.get("label"):
            verdicts[str(v["label"]).strip()[:24]] = \
                str(v.get("verdict") or "").strip().lower()

    for sid in plan:
        key = sections.key_of(sid)
        kept = []
        for e in features.get(key) or []:
            verdict = verdicts.get(e["label"])
            if verdict is None:
                kept.append(e)
                continue
            summary["checked"] += 1
            if verdict == "unsupported":
                summary["dropped"] += 1
                continue
            if verdict == "weak":
                summary["weakened"] += 1
                e = dict(e)
                e["confidence"] = round(float(e.get("confidence", 0.5)) * 0.7, 2)
                e["critic"] = "weak"
            kept.append(e)
        features[key] = kept

    missing = data.get("missing")
    if isinstance(missing, list):
        valid_keys = {sections.SECTIONS[s]["key"] for s in plan}
        by_key = {}
        for m in missing:
            if not isinstance(m, dict):
                continue
            key = str(m.get("section") or "").strip()
            if key in valid_keys:
                by_key.setdefault(key, []).append(m)
        for key, items in by_key.items():
            extra, _, _ = _norm_entries(items, index, "critic")
            features.setdefault(key, [])
            have = {e["label"] for e in features[key]}
            for e in extra:
                if e["label"] in have:
                    continue
                features[key].append(e)
                summary["added"] += 1
    return summary


def verify_all_citations(features, index):
    """整份结论的证据体检：统计引用有效率，剔除全编造的条目。"""
    valid = invalid = dropped = 0
    for key, entries in list(features.items()):
        kept = []
        for e in entries or []:
            ev = list(e.get("evidence") or [])
            if not ev:
                kept.append(e)
                continue
            ok, bad = [], []
            for mid in ev:
                (ok if mid in index else bad).append(mid)
            valid += len(ok)
            invalid += len(bad)
            if bad and not ok:
                dropped += 1
                continue
            if bad:
                e = dict(e)
                e["confidence"] = round(float(e.get("confidence", 0.5)) * 0.6, 2)
                e["evidence"] = ok
                e["unverified"] = len(bad)
            kept.append(e)
        features[key] = kept
    return {"valid": valid, "invalid": invalid, "dropped_entries": dropped}


def _avg_conf(features):
    items = [f for sec in features.values() for f in (sec or [])]
    if not items:
        return 0.0
    return round(sum(f.get("confidence", 0) for f in items) / len(items), 2)


def estimate_tokens(markdown):
    """粗估 token：中文 1 字 ≈ 0.7 token。"""
    return max(1, int(len(markdown or "") * 0.7))


# ── 构建入参 ────────────────────────────────────────────

def _payload(distillation_id, target_id, target_name, level, st, data,
             features, patterns, known, cfg, quality, ledger_view,
             new_fp_map, quotes, run_mode, delta_count, coverage_note,
             style=None, measures=None):
    limit = int(cfg.get("evidence_map_limit", 5000))
    return {
        "id": distillation_id,
        "target_id": str(target_id),
        "target_name": target_name or "",
        "level": level,
        "mode": run_mode,
        "data_volume": st["unique"],
        "raw_volume": st["raw_total"],
        "delta_messages": delta_count,
        "coverage_note": coverage_note,
        "stats": st,
        "counts": {k: v for k, v in (data.get("counts") or {}).items() if v},
        "facts_by_kind": data.get("facts_by_kind") or {},
        "source_notes": data.get("notes") or [],
        "source_missing": data.get("missing") or [],
        "quarantine": data.get("quarantine") or {},
        "features": features,
        "patterns": patterns,
        "style": style,
        "measures": measures,
        "relational": quality.get("relational") or {},
        "evidence_map": _trim_map(known, limit, int(cfg.get("quote_chars", 200))),
        "evidence_truncated": len(known) > limit,
        "quality": quality,
        "ledger_plan": ledger_view,
        "ledger_commit": {"id_map": new_fp_map or {}, "quotes": quotes or {}},
        "config_snapshot": {
            "continuous": cfg.get("continuous"),
            "llm_enabled": cfg.get("llm_enabled"),
            "critic_enabled": cfg.get("critic_enabled"),
            "max_llm_calls": cfg.get("max_llm_calls"),
            "llm_rpm": cfg.get("llm_rpm"),
            "sections_per_call": cfg.get("sections_per_call"),
            "chunk_chars": cfg.get("chunk_chars"),
            "evidence_quotes": cfg.get("evidence_quotes"),
            "inject_min_confidence": cfg.get("inject_min_confidence"),
        },
        "created_at": time.time(),
    }


def _trim_map(known, limit, cap=200):
    """渲染用的编号 → 原文表：本轮语料 + 账本累积的历史引证。

    带上历史引证，审核页里的旧结论才能显示原话，
    而不是只剩一个孤零零的 M0007。
    """
    out = {}
    for mid, m in list(known.items())[:limit]:
        text = m.get("text") or ""
        out[mid] = {"q": (text if len(text) <= cap else text[:cap] + "…"),
                    "d": m.get("date") or "", "h": m.get("hour"),
                    "k": m.get("kind", "message")}
    return out


def _build(payload):
    """渲染人格文件（延迟导入，避免与 review_manager 循环依赖）。"""
    from . import personality_model
    return personality_model.build_persona(payload)
