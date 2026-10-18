# -*- coding: utf-8 -*-
"""这样脆弱的东西 · 命令行入口。

存在的理由：定时任务、批处理、CI 里跑蒸馏，不该依赖点网页。

    python cli.py sources                          看有哪些数据源、状态如何
    python cli.py scan   --target 10003        扫描数据量（含缺口）
    python cli.py distill --target X --level L3 --consent [--approve] [--watch]
    python cli.py ledger --target X [--json]
    python cli.py export --target X [--out DIR]
    python cli.py turing --target X [--rounds 5]
    python cli.py forget --target X --eid "linguistics::高频词"
    python cli.py selftest                          跑内置自检

约定：
  - 蒸馏前必须显式给 `--consent`，对应伦理协议里的"已获得知情同意"。
  - `--approve` 会跳过人工审核直接落账，并记为 operator=cli（审计留痕）。
    自动化流程请自己判断是否愿意承担这一步。
  - 所有命令都支持 `--json`，方便脚本解析。
"""
import argparse
import importlib
import json
import os
import subprocess
import sys
import time
import types

PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.dirname(os.path.dirname(PLUGIN_DIR))
PKG = "persona_distill_cli"

if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)


def _load():
    """把插件目录装成独立包，避开 plugins/__init__（它会拉起整个插件管理器）。"""
    if PKG not in sys.modules:
        pkg = types.ModuleType(PKG)
        pkg.__path__ = [PLUGIN_DIR]
        sys.modules[PKG] = pkg

    def m(name):
        return importlib.import_module(f"{PKG}.{name}")

    return {
        "engine": m("distill_engine"),
        "api": m("api_server"),
        "rm": m("review_manager"),
        "ledger": m("ledger"),
        "dc": m("data_collector"),
        "cfg": m("config"),
        "pm": m("personality_model"),
        "lv": m("level_classifier"),
        "sec": m("sections"),
    }


def _dump(obj, as_json=False):
    if as_json:
        print(json.dumps(obj, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(obj, ensure_ascii=False, indent=2)
              if isinstance(obj, (dict, list)) else obj)


# ── 子命令 ──────────────────────────────────────────────

def cmd_sources(a):
    P = _load()
    out = []
    for src, label in P["dc"].SOURCE_LABEL.items():
        out.append({"source": src, "label": label})
    facts = [{"kind": k, "label": v} for k, v in P["dc"].FACT_LABEL.items()]
    if a.json:
        _dump({"message_sources": out, "fact_kinds": facts}, True)
        return 0
    print("可用的发言数据源：")
    for o in out:
        print(f"  {o['source']:16s} {o['label']}")
    print("\n可用的平台事实（不是发言，但是硬数据）：")
    for o in facts:
        print(f"  {o['kind']:8s} {o['label']}")
    return 0


def cmd_scan(a):
    P = _load()
    s = P["dc"].scan(a.target)
    # 用去重后的条数判等级 —— 落沙里同一个短句会重复很多次，
    # 按原始条数判定会把等级虚高
    s["suggested_level"] = P["lv"].classify_level(
        s.get("distinct_count") or s["total_count"])
    led = P["ledger"].load(a.target)
    delta, total, first_run = P["ledger"].split_delta(led, [])
    s["ledger"] = P["ledger"].summary(led)
    s["ai_available"] = P["api"].deep_ai_available()
    s["deep_channel"] = P["api"]._get_deep_client()[1]
    if a.json:
        _dump(s, True)
        return 0
    print(f"目标 {a.target}")
    print(f"  独立发言 {s.get('distinct_count', s['total_count'])} 条"
          f"（原始记录 {s['total_count']} 条）→ 建议等级 {s['suggested_level']}")
    print(f"  平台事实 {s['facts_count']} 条"
          + (f"（{'、'.join(f'{k} {v}' for k, v in s['facts_by_kind'].items())}）"
             if s["facts_by_kind"] else ""))
    print("\n数据源状态：")
    for n in s["notes"]:
        print("  " + n)
    if s["missing"]:
        print("\n缺失的数据源（会影响完整性）：")
        for m in s["missing"]:
            print(f"  ⚠ {m['source']}：{m['reason']}")
            print(f"     影响：{m['impact']}")
    ls = s["ledger"]
    if ls["runs"]:
        print(f"\n人格账本：已完成 {ls['runs']} 轮，结论 {ls['entries']} 条"
              f"（{'、'.join(f'{k} {v}' for k, v in ls['by_state'].items())}）")
    else:
        print("\n人格账本：还没有记录（下一轮将是第 1 轮全量推导）")
    print(f"深挖通道：{s['deep_channel']}")
    return 0


def cmd_distill(a):
    P = _load()
    if not a.consent:
        print("拒绝执行：缺少 --consent。\n"
              "按伦理协议（ETHICS.md），蒸馏前必须确认已获得被蒸馏者的知情同意。\n"
              "确认后请加上 --consent 重新运行。", file=sys.stderr)
        return 2
    eng = P["engine"]
    r = eng.start(target_id=a.target, target_name=a.name or "", level=a.level,
                  sources=a.sources.split(",") if a.sources else None,
                  external_text="", consent=True, mode=a.mode)
    if not r.get("success"):
        print(f"启动失败：{r.get('error')}", file=sys.stderr)
        return 1
    print(f"已启动（目标 {a.target}，等级 {a.level or '自动'}，方式 {a.mode}）")

    last = 0
    while True:
        p = eng.get_progress()
        log = p.get("log") or []
        while last < len(log):
            print("  ·", log[last])
            last += 1
        if p.get("error"):
            print(f"失败：{p['error']}", file=sys.stderr)
            return 1
        if p.get("done") or not p.get("running"):
            break
        if not a.watch and p.get("progress", 0) >= 100:
            break
        time.sleep(2)

    q = p.get("quality") or {}
    print(f"\n完成：模式 {q.get('mode')} ｜ LLM {q.get('llm', {}).get('calls', 0)} 次调用"
          f" ｜ 覆盖 {(q.get('coverage') or {}).get('ratio', 1) if q.get('coverage') else 1:.0%}")
    if (q.get("coverage") or {}).get("missed"):
        print(f"未覆盖：{q['coverage']['missed']}（已记入账本，下轮优先补）")

    if a.approve:
        res = P["rm"].approve()
        if not res.get("success"):
            print(f"落账失败：{res.get('error')}", file=sys.stderr)
            return 1
        print(f"已落账（跳过人工审核）：{res['persona_path']}")
        if res.get("appendix_path"):
            print(f"证据附卷：{res['appendix_path']}")
        if res.get("ledger_note"):
            print(res["ledger_note"])
    else:
        print("结果已进入待审核。查看：python cli.py export --target "
              f"{a.target}")
    if a.json:
        _dump({"progress": p}, True)
    return 0


def cmd_ledger(a):
    P = _load()
    led = P["ledger"].load(a.target)
    if a.json:
        _dump({"summary": P["ledger"].summary(led),
               "entries": led.get("entries") or {},
               "journal": led.get("journal") or []}, True)
        return 0
    s = P["ledger"].summary(led)
    if not s["entries"]:
        print(f"{a.target}：账本还是空的，先跑一次蒸馏。")
        return 0
    print(f"账本 — {a.target}")
    print(f"  轮次 {s['runs']} ｜ 结论 {s['entries']} 条 ｜ "
          f"状态 {'、'.join(f'{k} {v}' for k, v in s['by_state'].items())}")
    print(f"  平均置信度 {s['avg_confidence']:.0%} ｜ 水位 {s['seen_messages']} 条 ｜ "
          f"建账 {str(s['created_at'])[:16]} ｜ 更新 {str(s['updated_at'])[:16]}")
    if led.get("pending_sections"):
        print(f"  ⏳ 待补层：{led['pending_sections']}")
    print()
    for sec, items in (led.get("entries") or {}).items():
        for e in items:
            n = e.get("confirmations", 1)
            print(f"  [{e.get('state', 'active'):8s}] {sec}.{e.get('label')}"
                  f"　{e.get('confidence')}　确认{n}次　沉默{e.get('silent_runs', 0)}轮")
            print(f"      {str(e.get('value'))[:90]}")
    print(f"\n最近变更：{P['ledger'].diff_text(led)}")
    return 0


def cmd_export(a):
    P = _load()
    rm = P["rm"]
    rid = a.id
    if not rid:
        pending = rm.get_pending()
        if pending:
            rid = pending["id"]
        else:
            hist = rm.get_history()
            rid = hist[0]["id"] if hist else ""
    if not rid:
        print("没有可导出的蒸馏记录。", file=sys.stderr)
        return 1
    rec = rm.get_record(rid)
    if not rec:
        print(f"找不到记录 {rid}", file=sys.stderr)
        return 1
    out_dir = a.out or os.path.join(os.getcwd(), "persona_export")
    os.makedirs(out_dir, exist_ok=True)
    base = f"{rec['target_id']}_{rec['level']}"

    persona = rm.get_persona_content(rid)
    p1 = os.path.join(out_dir, f"{base}.md")
    with open(p1, "w", encoding="utf-8") as f:
        f.write(persona)
    p2 = os.path.join(out_dir, f"{base}.prompt.md")
    with open(p2, "w", encoding="utf-8") as f:
        f.write(rm.get_persona_prompt(rid))
    p3 = os.path.join(out_dir, f"{base}.ledger.json")
    with open(p3, "w", encoding="utf-8") as f:
        json.dump({"record": rec,
                   "ledger": P["ledger"].load(rec["target_id"])},
                  f, ensure_ascii=False, indent=2)
    made = [p1, p2, p3]
    if rec.get("appendix_path") and os.path.exists(rec["appendix_path"]):
        import shutil
        p4 = os.path.join(out_dir, f"{base}.evidence.md")
        shutil.copy2(rec["appendix_path"], p4)
        made.append(p4)
    if a.json:
        _dump({"files": made, "id": rid}, True)
        return 0
    print(f"已导出记录 {rid} 到 {out_dir}")
    for p in made:
        print(f"  {os.path.basename(p)}  ({os.path.getsize(p)} 字节)")
    return 0


def cmd_turing(a):
    P = _load()
    hist = P["rm"].get_history()
    rid = a.id or (hist[0]["id"] if hist else "")
    if not rid:
        print("没有可测的蒸馏记录。", file=sys.stderr)
        return 1
    print(f"对 {rid} 做图灵测试（{a.rounds} 轮，真实 LLM，会占用上游额度）…")
    res = P["api"].turing_test({"id": rid, "rounds": a.rounds})
    if a.json:
        _dump(res, True)
        return 0
    print(f"状态 {res.get('status')} ｜ 得分 {res.get('score')} ｜ "
          f"方式 {res.get('method')}")
    print(res.get("detail") or res.get("error") or "")
    for r in (res.get("rounds") or [])[:5]:
        print(f"  刺激：{str(r.get('prompt'))[:60]}")
        print(f"    真实：{str(r.get('real'))[:60]}")
        print(f"    模仿：{str(r.get('generated'))[:60]}"
              + (f"　← {r.get('reason')}" if r.get("reason") else ""))
    return 0


def cmd_test(a):
    """跑 tests/ 里的全部回归套件（独立子进程，任一失败退出码非零）。"""
    runner = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "tests", "run_all.py")
    proc = subprocess.run([sys.executable, runner],
                          env=dict(os.environ, PYTHONIOENCODING="utf-8"))
    return proc.returncode


def cmd_publish(a):
    """发布 / 下线 / 回滚 蒸馏注入卡给 bot。"""
    P = _load()
    params = {"target_id": a.target}
    if a.action:
        params["action"] = a.action
    res = P["api"].publish(params)
    if res.get("error"):
        print(res["error"], file=sys.stderr)
        return 1
    if res.get("disabled"):
        print("已下线：bot 回到手写人设")
        return 0
    if res.get("rolled_back_to"):
        print(f"已回滚到 {res['rolled_back_to']}")
        return 0
    print(f"已发布 {res['target_id']} 的注入卡（{res['bytes']} 字节 ≈ "
          f"{res.get('tokens', '?')} tokens，"
          f"硬约束保留={'是' if res.get('preserved') else '否'}）")
    if res.get("token_warning"):
        print(f"  ⚠ {res['token_warning']}")
    print(f"  {res.get('note', '')}")
    return 0


def cmd_render(a):
    """零调用重印：直接用账本重新出人格文件（含确定性修正与洞察）。"""
    P = _load()
    res = P["api"].render({"target_id": a.target, "level": a.level})
    if res.get("error"):
        print(res["error"], file=sys.stderr)
        return 1
    if a.json:
        _dump(res, True)
        return 0
    print(f"已重印（0 次 LLM 调用）：{res['persona_path']}")
    if res.get("appendix_path"):
        print(f"证据附卷：{res['appendix_path']}")
    for w in res.get("naming_warnings") or []:
        print(f"  ⚠ 名字方向存疑：「{w['name']}」{'；'.join(w['where'])}"
              f" → 可信度 {w['from']:.0%} → {w['to']:.0%}")
    return 0


def cmd_forget(a):
    P = _load()
    res = P["api"].forget({"target_id": a.target, "eid": a.eid})
    if res.get("error"):
        print(res["error"], file=sys.stderr)
        return 1
    print(f"已抹除：{res.get('removed')}")
    return 0


def cmd_selftest(a):
    """不依赖引擎的自检：状态机 / 证据链 / 编号稳定性。"""
    P = _load()
    fails = []

    def check(name, cond, detail=""):
        print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  {detail}" if detail else ""))
        if not cond:
            fails.append(name)

    L, EV, DE, SEC = P["ledger"], None, P["engine"], P["sec"]
    EV = importlib.import_module(f"{PKG}.evidence")

    print("=== 证据编号跨轮稳定 ===")
    raw = [{"text": "你好", "timestamp": "2026-08-05 15:46", "source": "chat"},
           {"text": "在干嘛", "timestamp": "2026-08-06 01:16", "source": "chat"}]
    m1, i1, map1, nxt = EV.normalize(raw)
    before = sorted(i1)
    m2, i2, map2, nxt2 = EV.normalize(
        raw + [{"text": "新的", "timestamp": "2026-08-20 10:00", "source": "chat"}],
        id_map=map1, start=nxt)
    after = sorted(x["mid"] for x in i2.values() if x["text"] in ("你好", "在干嘛"))
    check("老消息编号不变", before == after, f"{before} vs {after}")
    check("新消息续号", len(set(i2) - set(i1)) == 1)

    print("=== 平台事实与发言分开计数 ===")
    st = EV.stats([{"text": "a", "kind": "message", "mid": "M0001", "date": "2026-01-01"},
                   {"text": "b", "kind": "fact", "mid": "M0002", "date": "2026-01-01"}])
    check("发言数只算发言", st["unique"] == 1, str(st["unique"]))
    check("事实单独计数", st["fact_count"] == 1, str(st["fact_count"]))

    print("=== 预算降级不乱来 ===")
    tight = SEC.plan_calls(["S1", "S2", "S3"], chunks=40, cfg={"max_llm_calls": 3,
                                                             "sections_per_call": 1})
    check("块太多时会合并块而不是丢语料", tight["chunks"] < 40, str(tight["chunks"]))
    check("合并块后仍不超预算", tight["estimated"] <= tight["budget"],
          f"{tight['estimated']}>{tight['budget']}")
    check("降级有留痕", bool(tight["degraded"]), str(tight["degraded"][:1]))
    ok = SEC.plan_calls(["S1"], chunks=1, cfg={"max_llm_calls": 24})
    check("预算够时不降级", not ok["degraded"] and not ok["uncovered"])
    starve = SEC.plan_calls(["S1", "S2", "S3"], chunks=1,
                            cfg={"max_llm_calls": 1, "sections_per_call": 1,
                                 "critic_enabled": False})
    check("预算只够一次时仍然覆盖全部层",
          starve["uncovered"] == [] and len(starve["batches"]) == 1,
          str(starve["batches"]))

    print("=== 状态机：沉默不等于否定 ===")
    led = L._empty("__cli_selftest__")
    obs = {"behavior": [{"label": "习惯X", "value": "每次都说好", "confidence": 0.7,
                         "evidence": ["M0001"]}]}
    p1 = L.plan(led, obs, {"mode": "full", "first_run": True})
    L.commit(led, p1, level="L1")
    # 只有"真的来了新对话"才算沉默：每轮都带 delta_messages
    for _ in range(int(P["cfg"].get_config().get("suspect_after", 3))):
        p = L.plan(led, {}, {"mode": "incremental", "delta_messages": 5})
        L.commit(led, p, level="L1")
    e = led["entries"]["behavior"][0]
    check("沉默到阈值转存疑（而不是直接推翻）", e["state"] == "suspect", e["state"])
    for _ in range(int(P["cfg"].get_config().get("dormant_after", 8))):
        p = L.plan(led, {}, {"mode": "incremental", "delta_messages": 5})
        L.commit(led, p, level="L1")
    e = led["entries"]["behavior"][0]
    check("继续沉默只到沉眠，绝不推翻", e["state"] == "dormant", e["state"])
    # 全量重读同一份语料：不该因为"没被重新提到"而降级
    before = led["entries"]["behavior"][0]["silent_runs"]
    p = L.plan(led, {}, {"mode": "full", "delta_messages": 0})
    check("全量重读无新数据时不推进沉默",
          p["entries"]["behavior"][0]["silent_runs"] == before,
          f"{before} → {p['entries']['behavior'][0]['silent_runs']}")
    p = L.plan(led, obs, {"mode": "incremental", "delta_messages": 3})
    check("重新拿到证据就复活",
          p["entries"]["behavior"][0]["state"] == "active")
    p = L.plan(led, {"behavior": [{"label": "习惯X", "value": "每次都说好",
                                   "confidence": 0.9, "evidence": ["M0002"],
                                   "counter": ["M0003"]}]},
               {"mode": "incremental", "delta_messages": 2})
    check("反证才能推翻",
          p["entries"]["behavior"][0]["state"] == "retired")

    p = L._path("__cli_selftest__")
    if os.path.exists(p):
        os.remove(p)

    print("\n" + "=" * 40)
    print("结果：全部通过" if not fails else f"失败 {fails}")
    return 1 if fails else 0


# ── 入口 ────────────────────────────────────────────────

def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="cli.py", description="这样脆弱的东西 · 命令行入口")
    ap.add_argument("--json", action="store_true", help="以 JSON 输出结果")
    sub = ap.add_subparsers(dest="cmd")

    sp = sub.add_parser("sources", help="列出可用数据源")
    sp.set_defaults(func=cmd_sources)

    sp = sub.add_parser("scan", help="扫描数据量与缺口")
    sp.add_argument("--target", required=True)
    sp.set_defaults(func=cmd_scan)

    sp = sub.add_parser("distill", help="运行蒸馏")
    sp.add_argument("--target", required=True)
    sp.add_argument("--name", default="")
    sp.add_argument("--level", default=None, help="L0-L4，留空自动判定")
    sp.add_argument("--mode", default="auto", choices=["auto", "full", "incremental"])
    sp.add_argument("--sources", default="", help="逗号分隔，留空=全部")
    sp.add_argument("--consent", action="store_true", help="确认已获知情同意（必须）")
    sp.add_argument("--approve", action="store_true",
                    help="跳过人工审核直接落账（会记为 operator=cli）")
    sp.add_argument("--watch", action="store_true", help="持续打印进度")
    sp.set_defaults(func=cmd_distill)

    sp = sub.add_parser("ledger", help="查看人格账本")
    sp.add_argument("--target", required=True)
    sp.set_defaults(func=cmd_ledger)

    sp = sub.add_parser("export", help="导出人格文件/注入卡/账本/证据附卷")
    sp.add_argument("--target", default="")
    sp.add_argument("--id", default="")
    sp.add_argument("--out", default="")
    sp.set_defaults(func=cmd_export)

    sp = sub.add_parser("turing", help="跑图灵盲测")
    sp.add_argument("--target", default="")
    sp.add_argument("--id", default="")
    sp.add_argument("--rounds", type=int, default=5)
    sp.set_defaults(func=cmd_turing)

    sp = sub.add_parser("publish", help="把蒸馏注入卡发布给 bot（--action disable/rollback）")
    sp.add_argument("--target", default=None)
    sp.add_argument("--action", default=None, help="disable=下线, rollback=回滚上一份")
    sp.set_defaults(func=cmd_publish)

    sp = sub.add_parser("test", help="跑 tests/ 全部回归套件")
    sp.set_defaults(func=cmd_test)

    sp = sub.add_parser("render", help="零 LLM 调用，用账本重印人格文件")
    sp.add_argument("--target", required=True)
    sp.add_argument("--level", default=None)
    sp.set_defaults(func=cmd_render)

    sp = sub.add_parser("forget", help="人工抹除账本里的一条结论")
    sp.add_argument("--target", required=True)
    sp.add_argument("--eid", required=True)
    sp.set_defaults(func=cmd_forget)

    sp = sub.add_parser("selftest", help="内置自检（不调 LLM）")
    sp.set_defaults(func=cmd_selftest)

    a = ap.parse_args(argv)
    if not getattr(a, "func", None):
        ap.print_help()
        return 0
    return a.func(a)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    sys.exit(main())
