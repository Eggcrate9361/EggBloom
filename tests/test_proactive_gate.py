# -*- coding: utf-8 -*-
"""tests/test_proactive_gate.py —— Trigger → Eval → Decision（2026-11-03）

执行总表 §7.2 第 2 条：主动开口要拆成
    Trigger（谁因为什么被考虑） → Eval（该不该 / 能不能） → Decision（说、不说、稍后）
并且要有 admission（谁允许被找）与 auto 模式（只记录不执行）。

本次是**重构优先**：把 `proactive_talk._evaluate()` 的判据整块搬到
`harness/proactive_gate.py`，行为必须一模一一样。所以本用例的核心是

    [A] 等价性：`proactive_gate.evaluate()` 压平后，与**改造前那份实现**
        （本文件里原样留存的 `_legacy_evaluate`，2026-11-02 版）逐字段相等，
        覆盖 12 组配置 × 2 个随机值 × 2 种 AI 状态 × 3 种作息乘子。

基准来自改造前的代码而不是手抄的期望值 —— 与 `life_dynamic_golden.json`
同一条纪律：期望值一旦手抄，就是把 bug 抄进断言里。

其余锁五件事：
  B. Trigger 形状：不认识的 kind 归 event、target 去空白、at 有默认值
  C. admission：名单内 / 点名 / 不速之客 / 没有目标，四种规则分得清
  D. Decision：live 真发、auto 只记录、被拦不算"记录"、未知 mode 退回 live
  E. waits_for_person：我们说了最后一句且对方没回
  F. 闸门顺序：静默早于概率、概率早于 AI（顺序反了会多烧探测成本）
  G/H. 插件侧仍是旧形状；auto 模式跑完一轮不发送
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import importlib  # noqa: E402

from harness import proactive_gate as pg  # noqa: E402

pt = importlib.import_module("plugins.proactive_talk")

PASS = 0
FAIL = 0
FAILURES = []

NOW = 1_700_000_000.0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK]   {name}")
    else:
        FAIL += 1
        FAILURES.append(name + (f"  → {extra}" if extra else ""))
        print(f"  [FAIL] {name}  {extra}")


def _legacy_evaluate(cfg, state, force=False, target="", *, now, rng, quiet,
                     factor, ai_ready):
    """**改造前** `proactive_talk._evaluate()` 的原样拷贝（2026-11-02 版）。

    只把五处不确定来源换成参数：now / rng / quiet / factor / ai_ready。
    其余判据顺序、中文文案、边界算法一个字没动 —— 它就是基准。
    """
    targets = pt._resolve_targets(cfg)
    target = str(target or "").strip()
    if target and target not in targets:
        targets = [target] + targets
    if not targets:
        return {"ok": False, "blocked_by": "未配置目标 QQ（插件 target_qqs/target_qq 与主配置 target_qqs 均为空）"}
    target = target or targets[0]
    if force:
        return {"ok": True, "target": target, "blocked_by": ""}

    by = state.get("last_proactive_by") or {}
    last_pro = float((by.get(target) if isinstance(by, dict) else 0) or 0)
    if not last_pro:
        last_pro = float(state.get("last_proactive_ts") or 0)
    if last_pro and now - last_pro < float(cfg.get("min_interval_min") or 180) * 60:
        left = int((float(cfg.get("min_interval_min") or 180) * 60 - (now - last_pro)) // 60)
        return {"ok": False, "target": target,
                "blocked_by": f"距上次和 TA 说话不足最小间隔（还需 {left} 分钟）"}

    last_user = pt._last_ts_for(state, target)
    if last_user and now - last_user < float(cfg.get("active_cooldown_min") or 30) * 60:
        left = int((float(cfg.get("active_cooldown_min") or 30) * 60 - (now - last_user)) // 60)
        return {"ok": False, "target": target,
                "blocked_by": f"用户刚说过话，冷却中（还需 {left} 分钟）"}

    if quiet(cfg.get("quiet_hours")):
        return {"ok": False, "target": target,
                "blocked_by": f"处于静默时段 {cfg.get('quiet_hours')}"}

    chance = float(cfg.get("speak_chance") or 0.35)
    _bio = 1.0
    if cfg.get("bio_rhythm", True):
        try:
            _bio = factor()
            chance = min(1.0, chance * _bio)
        except Exception:
            _bio = 1.0
    if rng() >= chance:
        return {"ok": False, "target": target,
                "blocked_by": f"概率闸门未通过（chance={chance:.3f}"
                              + (f"，作息乘子={_bio:.2f}）" if _bio != 1.0 else "）")}

    if not ai_ready():
        return {"ok": False, "target": target, "blocked_by": "AI 未配置，暂不主动开口"}

    return {"ok": True, "target": target, "blocked_by": ""}


# 无状态的一组"全通"基线，各用例在它上面改一两个键
BASE_CFG = {"target_qqs": ["111", "222"], "min_interval_min": 180,
            "active_cooldown_min": 30, "speak_chance": 1.0,
            "quiet_hours": "", "bio_rhythm": False}

CASES = [
    # (名字, cfg, state, force, target)
    ("没有目标", {"target_qqs": []}, {}, False, ""),
    ("单目标 target_qq", {"target_qq": "111"}, {}, False, ""),
    ("全通", BASE_CFG, {}, False, ""),
    ("按人最小间隔内", BASE_CFG, {"last_proactive_by": {"111": NOW - 60}}, False, "111"),
    ("别人刚被搭话不影响我", BASE_CFG,
     {"last_proactive_by": {"111": NOW - 60, "222": NOW - 7200}}, False, "222"),
    ("最小间隔边界（正卡在线上）",
     dict(BASE_CFG, min_interval_min=180),
     {"last_proactive_by": {"111": NOW - 180 * 60}}, False, "111"),
    ("间隔剩余分钟取整", dict(BASE_CFG, min_interval_min=180),
     {"last_proactive_by": {"111": NOW - 30}}, False, "111"),
    ("退回全局时间戳", {"target_qqs": ["111"], "min_interval_min": 180,
                       "active_cooldown_min": 0, "speak_chance": 1.0,
                       "quiet_hours": "", "bio_rhythm": False},
     {"last_proactive_ts": NOW - 60}, False, "111"),
    ("发言冷却中", BASE_CFG, {"last_user_ts_by": {"111": NOW - 10}}, False, "111"),
    ("别人刚发言自己不受牵连", BASE_CFG,
     {"last_user_ts_by": {"222": NOW - 10}}, False, "111"),
    ("静默时段", dict(BASE_CFG, quiet_hours="2:00-7:00"), {}, False, ""),
    ("概率为零", dict(BASE_CFG, speak_chance=0.0), {}, False, ""),
    ("force 跳过全部闸门",
     dict(BASE_CFG, speak_chance=0.0, quiet_hours="2:00-7:00"), {}, True, ""),
    ("点名一个不在名单里的人", BASE_CFG, {}, False, "999"),
]


def main():
    global PASS, FAIL
    print("[A] 等价性：新实现 == 改造前那份实现")
    pt._ctx = None            # 让 `_resolve_targets` 不去问主配置，两边口径一致
    total = 0
    for name, cfg, state, force, target in CASES:
        for rng_val in (0.0, 0.999):
            for ai_ok in (True, False):
                for bio in ("off", "ok", "raise"):
                    total += 1
                    c = dict(cfg)
                    if bio == "off":
                        c["bio_rhythm"] = False
                    else:
                        c["bio_rhythm"] = True

                    def factor(_b=bio):
                        if _b == "raise":
                            raise RuntimeError("作息模块不在线")
                        return 0.5

                    def qh_new(spec, _when=None):
                        return str(spec or "") == "2:00-7:00"

                    def qh_old(spec):
                        return str(spec or "") == "2:00-7:00"

                    got = pg.project(pg.evaluate(
                        pg.make_trigger(pg.TRIGGER_MANUAL if force else pg.TRIGGER_HEARTBEAT,
                                        target=target, at=NOW),
                        c, state, force=force, now=NOW, rng=lambda: rng_val,
                        resolve=lambda cc: pt._resolve_targets(cc),
                        last_user=lambda s, t: pt._last_ts_for(s, t),
                        quiet_hours=qh_new, probability_factor=factor,
                        ai_ready=lambda: ai_ok,
                    ))
                    want = _legacy_evaluate(
                        c, state, force=force, target=target, now=NOW,
                        rng=lambda: rng_val, quiet=qh_old,
                        factor=factor, ai_ready=lambda: ai_ok,
                    )
                    check(f"{name} · rng={rng_val} ai={ai_ok} bio={bio}",
                          got == want, f"\n        新={got}\n        旧={want}")
    print(f"  （共比对 {total} 组）")

    print("\n[B] Trigger 形状")
    check("不认识的 kind 归 event", pg.make_trigger("nonsense")["kind"] == "event")
    check("target 去首尾空白", pg.make_trigger("heartbeat", target="  111 ")["target"] == "111")
    check("at 有默认值", isinstance(pg.make_trigger("heartbeat")["at"], float))
    check("四种 kind 都认", all(pg.make_trigger(k)["kind"] == k for k in pg.TRIGGER_KINDS))

    print("\n[C] admission：谁允许被找")
    check("名单内 → configured_target",
          pg.admission("111", targets=["111", "222"])["rule"] == "configured_target")
    check("点名叫的 → explicit_request",
          pg.admission("333", targets=["111"], explicit=True)["rule"] == "explicit_request")
    check("不速之客 → not_admitted",
          pg.admission("333", targets=["111"])["rule"] == "not_admitted")
    check("不速之客不给过",
          pg.admission("333", targets=["111"])["allowed"] is False)
    check("没有目标 → none", pg.admission("", targets=[])["rule"] == "none")

    print("\n[D] Decision：说 / 不说 / 只记录")
    ok_ev = {"ok": True, "target": "111", "admission": {"rule": "configured_target"}}
    check("live + 通过 → say", pg.decide(ok_ev)["act"] == "say")
    auto = pg.decide(ok_ev, mode=pg.MODE_AUTO)
    check("auto + 通过 → 不执行", auto["act"] == "none")
    check("auto + 通过 → 记为一笔", auto["recorded"] is True)
    blocked = pg.decide({"ok": False, "target": "111", "blocked_by": "槽"})
    check("被拦 → 不执行", blocked["act"] == "none")
    check("被拦不算「记录」", blocked["recorded"] is False)
    check("被拦时 reason 用 blocked_by", blocked["reason"] == "槽")
    inferred = pg.decide({"ok": True, "target": "333",
                          "admission": {"rule": "not_admitted"}})
    check("推断来的候选 → 只记录", inferred["act"] == "none" and inferred["recorded"] is True)
    check("未知 mode 退回 live", pg.decide(ok_ev, mode="???")["mode"] == pg.MODE_LIVE)
    check("waits_for_person 进结果", pg.decide(ok_ev, waits_for_person=True)["waits_for_person"] is True)
    check("auto 且在等人 → reason 点出来",
          "还没回" in pg.decide(ok_ev, mode=pg.MODE_AUTO, waits_for_person=True)["reason"])

    print("\n[E] waits_for_person")
    check("我们最后说、对方没回 → True",
          pg.waits_for_person({"last_proactive_by": {"111": 1000.0},
                               "last_user_ts_by": {"111": 900.0}}, "111") is True)
    check("对方已经回了 → False",
          pg.waits_for_person({"last_proactive_by": {"111": 1000.0},
                               "last_user_ts_by": {"111": 1100.0}}, "111") is False)
    check("没主动说过话 → False", pg.waits_for_person({}, "111") is False)

    print("\n[F] 闸门顺序（文案命中即证顺序）")
    cfg_q = dict(BASE_CFG, quiet_hours="2:00-7:00", speak_chance=0.0)
    ev_q = pg.evaluate(pg.make_trigger("heartbeat"), cfg_q, {}, now=NOW,
                       quiet_hours=lambda s, w=None: True,
                       probability_factor=lambda: 1.0, ai_ready=lambda: False)
    check("静默排在概率与 AI 之前", "静默" in ev_q["blocked_by"], ev_q["blocked_by"])
    ev_p = pg.evaluate(pg.make_trigger("heartbeat"),
                       dict(BASE_CFG, quiet_hours="", speak_chance=0.0), {}, now=NOW,
                       rng=lambda: 0.9,
                       quiet_hours=lambda s, w=None: False,
                       probability_factor=lambda: 1.0, ai_ready=lambda: False)
    check("概率排在 AI 之前", "概率" in ev_p["blocked_by"], ev_p["blocked_by"])
    ev_f = pg.evaluate(pg.make_trigger("manual"), cfg_q, {}, force=True, now=NOW,
                       quiet_hours=lambda s, w=None: True,
                       probability_factor=lambda: 1.0, ai_ready=lambda: False)
    check("force 连静默都跳", ev_f["ok"] is True and ev_f["gate"] == "force")

    print("\n[G] 插件侧仍是旧形状")
    pt._ctx = None
    d = pt._evaluate({"target_qqs": ["111"], "min_interval_min": 0,
                      "active_cooldown_min": 0, "speak_chance": 1.0,
                      "quiet_hours": "", "bio_rhythm": False}, {}, target="111")
    check("三个键不多不少", set(d.keys()) == {"ok", "target", "blocked_by"}, set(d.keys()))
    d0 = pt._evaluate({"target_qqs": []}, {}, target="")
    check("没有目标时**不带** target 键（旧形状）",
          set(d0.keys()) == {"ok", "blocked_by"}, set(d0.keys()))

    print("\n[H] auto 模式跑完一轮：只记录、不发送")
    class FakeCtx:
        def __init__(self):
            self.state = {}
            self.sent = []

        def get_config(self, k, d=None):
            return d

        def get_state(self, k="", default=None):
            return self.state if not k else self.state.get(k, default)

        def set_state(self, k, v):
            self.state[k] = v

        def send_text(self, text, target=""):
            self.sent.append((target, text))
            return True

        def log(self, msg, level="info"):
            pass

    pt._ctx = FakeCtx()
    orig = {k: getattr(pt, k) for k in
            ("_load_config", "_build_messages", "_ai_text", "_clean_bubbles",
             "_send_bubbles", "_update_pending_topic", "_ai_ready")}
    sent = []
    pt._ai_text = lambda *a, **k: "在吗"
    pt._clean_bubbles = lambda t, n: ["在吗"]
    pt._send_bubbles = lambda qq, b: (sent.append(qq) or 1)
    pt._build_messages = lambda *a, **k: []
    pt._update_pending_topic = lambda *a, **k: None
    pt._ai_ready = lambda: True
    try:
        pt._load_config = lambda: dict(BASE_CFG, min_interval_min=0,
                                       active_cooldown_min=0, mode="auto")
        r_auto = pt._proactive_once(force=False)
        check("auto → 一个字都没发", sent == [], sent)
        check("auto → 标了 recorded", r_auto.get("recorded") is True, r_auto)
        check("auto → 当成 skipped（心跳不打失败日志）", r_auto.get("skipped") is True, r_auto)

        pt._load_config = lambda: dict(BASE_CFG, min_interval_min=0,
                                       active_cooldown_min=0, mode="live")
        r_live = pt._proactive_once(force=False)
        check("live → 照常发送", sent == ["111", "222"] or len(sent) == 1, sent)
        check("live → ok", r_live.get("ok") is True, r_live)
    finally:
        for k, v in orig.items():
            setattr(pt, k, v)
        pt._ctx = None

    print("\n" + "=" * 60)
    print(f"通过 {PASS} / 失败 {FAIL}")
    if FAILURES:
        print("失败项：")
        for f in FAILURES:
            print("  -", f)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
