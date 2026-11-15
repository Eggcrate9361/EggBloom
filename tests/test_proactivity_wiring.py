# -*- coding: utf-8 -*-
"""tests/test_proactivity_wiring.py —— 接线与配置页（离线）

判据：
  [A] bot.replied 载荷补了 user_id / scene（私聊观测的前提）
  [B] 插件 on_event 同步只更新状态，不调模型、不发消息
  [C] 撤回事件进即时候选队列
  [D] ④ 撤回未交棒时 tick 不发（防双回）
  [E] 投递成功才记账；发送失败不记账
  [F] 配置页滑条：fieldmeta ctl=range + 0..3 档位 + 渲染/保存分支齐全
  [G] 四条行为默认态（三条开、撤回关）
"""
import io
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from _sandbox import sandbox_neurobase  # noqa: E402

sandbox_neurobase()

import plugins.proactivity as P  # noqa: E402
from harness.proactivity import arbitrator as A  # noqa: E402

PASS, FAIL = 0, 0
FAILURES = []


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK]   {name}")
    else:
        FAIL += 1
        FAILURES.append(name + (f"  → {extra}" if extra else ""))
        print(f"  [FAIL] {name}  {extra}")


class FakeCtx:
    def __init__(self, cfg=None):
        self.cfg = cfg or {"proactivity_enable": True, "proactivity_budget_level": 1,
                           "proactivity_quiet_hours": "", "recall_feedback_by": "main",
                           "proactivity_idle_contact_enable": False}
        self.pokes = []
        self.logs = []
        self.sent = []
        self.ai_calls = 0
        self.send_ok = True
        self.state = {}

    def get_config(self, key=None, default=None):
        return self.cfg if key is None else self.cfg.get(key, default)

    def get_state(self, key=None, default=None):
        return self.state.get(key, default)

    def set_state(self, key, value):
        self.state[key] = value
        return True

    def log(self, msg, level="info"):
        self.logs.append(str(msg))

    def ai_chat(self, messages, max_tokens=160):
        self.ai_calls += 1
        return '{"act":"say","text":"轻轻补一句"}'

    def send_text(self, text, target=None, group_id=None):
        self.sent.append((str(text), str(target or ""), str(group_id or "")))
        return self.send_ok

    def api_call(self, action, params=None):
        if action == "send_poke":
            self.pokes.append((action, dict(params or {})))
            return {"status": "ok"}
        return {}

    def ai_chat_as(self, act):
        self.ai_chat = lambda messages, max_tokens=160: (
            '{"act":"%s","text":"","reason":"很久没互动"}' % act)


def main():
    print("=== A) 事件载荷 ===")
    src = io.open(os.path.join(ROOT, "bot_core.py"), encoding="utf-8").read()
    check("bot.replied 带 user_id", '"user_id": str((data or {}).get("user_id") or "")' in src)
    check("bot.replied 带 scene", '"scene": "group" if _gid else "private"' in src)

    print("\n=== B/C) 插件事件接线 ===")
    d = tempfile.mkdtemp(prefix="pro_wiring_")
    ctx = FakeCtx()
    orig_path = P._data_path
    orig_start = P._start_thread
    P._data_path = lambda: os.path.join(d, "ledger.json")
    # 线程启动用 spy 顶掉：判据要证明「on_load 就会起」，但测试里不能真放一个
    # 30s tick 线程在后台改状态。e2e 段手动调 _tick_once() 即可。
    started = {"n": 0}
    P._start_thread = lambda: started.__setitem__("n", started["n"] + 1)
    try:
        P.on_load(ctx)
        check("★ on_load 就起 tick（不是等用户手动启用）", started["n"] == 1, started)
        P._start_thread = lambda: None
        P._pending.clear()
        P._last_sent.clear()
        P._sent_hourly.clear()
        P.on_event(ctx, "bot.replied", {
            "scene": "private", "user_id": "1000000006", "text": "醒得挺早啊你",
            "ts": 1000.0, "trace_id": "t1"})
        st = P._ledger.get("private", "1000000006")
        check("bot.replied → pending=True", st.get("pending") is True, st)
        P.on_event(ctx, "message.in", {
            "group_id": "", "sender": "1000000006", "text": "你怎么知道我醒来了",
            "at_bot": False, "ts": 1010.0})
        st2 = P._ledger.get("private", "1000000006")
        check("message.in → pending 清空", st2.get("pending") is False, st2)
        check("同步阶段没调模型", ctx.ai_calls == 0, ctx.ai_calls)
        check("同步阶段没发消息", ctx.sent == [], ctx.sent)
        P.on_event(ctx, "notice.recall", {
            "group_id": "", "operator": "1000000006", "message_id": "99",
            "notice_type": "friend_recall", "ts": 1020.0})
        check("撤回进即时候选队列",
              any(o.get("kind") == "recall" for o in P._pending), P._pending)
        check("撤回也写了 notice 留痕",
              len(P._ledger.get("private", "1000000006").get("notices") or []) == 1)

        print("\n=== D) 撤回未交棒不发 ===")
        P._tick_once()
        check("未交棒时没有投递", ctx.sent == [], ctx.sent)
        check("跳过留痕写明防双回",
              any("交棒" in x or "双回" in x or "未启用" in x for x in ctx.logs), ctx.logs)

        print("\n=== E) 投递记账 ===")
        P._pending.clear()
        action = {"obs": {"kind": "no_reply", "scene": "private",
                          "target": "1000000006", "dedupe_key": "k1"},
                  "text": "轻轻补一句"}
        P._deliver(action)
        check("私聊用 target 发送", ctx.sent and ctx.sent[0][1] == "1000000006", ctx.sent)
        check("成功后记 last_sent",
              bool(P._last_sent.get("private:1000000006")), P._last_sent)
        check("成功后写去重", P._ledger.already_fired("k1") is True)
        ctx.send_ok = False
        P._deliver({"obs": {"kind": "no_reply", "scene": "private",
                            "target": "1000000006", "dedupe_key": "k2"},
                    "text": "第二句"})
        check("发送失败不写去重", P._ledger.already_fired("k2") is False)
        check("发送失败留痕", any("发送失败" in x for x in ctx.logs), ctx.logs)

        print("\n=== E2) 端到端：发完没人回 → 判断 → 主动补一句 ===")
        import time as _t
        ctx2 = FakeCtx({"proactivity_enable": True, "proactivity_budget_level": 2,
                        "proactivity_quiet_hours": "", "recall_feedback_by": "main",
                        "proactivity_no_reply_enable": True,
                        "proactivity_idle_contact_enable": False})
        P.on_load(ctx2)
        # 前几段往同一个临时 ledger 里写过假时间戳（1000/1010/1020），
        # 对真实 now 来说那是「很久以前」→ 会误触发冷场候选。只留本段的目标。
        P._ledger._states.clear()
        P._pending.clear()
        P._last_sent.clear()
        P._sent_hourly.clear()
        P._inflight.clear()
        past = _t.time() - 700          # 11 分钟前发的，对方一直没回
        P.on_event(ctx2, "bot.replied", {
            "scene": "private", "user_id": "1000000005",
            "text": "醒得挺早啊你", "ts": past, "trace_id": "e2e-1"})
        P._tick_once()                  # 真实后台线程投递，等一下
        for _ in range(30):
            if ctx2.sent:
                break
            _t.sleep(0.05)
        check("整条链真的发出了一句", len(ctx2.sent) == 1, ctx2.sent)
        check("发的是模型给的文案",
              ctx2.sent and ctx2.sent[0][0] == "轻轻补一句", ctx2.sent)
        check("同步阶段仍不调模型（判断在后台）", ctx2.ai_calls == 1, ctx2.ai_calls)
        check("预算计数 +1", sum(ctx2_sent for ctx2_sent in P._sent_hourly.values()) >= 1,
              P._sent_hourly)
        n_before = len(ctx2.sent)
        P._tick_once()
        _t.sleep(0.2)
        check("同一轮不会重复发（去重 + 冷却）", len(ctx2.sent) == n_before, ctx2.sent)

        print("\n=== H) 两个老插件合并：poke 成为可选动作、老心跳让位、卡片合一 ===")
        import plugins.proactive_poke as PP
        import plugins.proactive_talk as PT
        cfg_take = {"proactivity_enable": True, "proactivity_budget_level": 2,
                    "proactivity_quiet_hours": "", "recall_feedback_by": "main",
                    "proactivity_takeover_legacy": True,
                    "proactivity_idle_contact_enable": True}
        ctx3 = FakeCtx(cfg_take)
        P.on_load(ctx3)
        P._pending.clear()
        P._inflight.clear()
        P._deliver({"obs": {"kind": "idle_contact", "scene": "private",
                            "target": "1000000005", "dedupe_key": "poke-1"},
                    "act": "poke", "text": ""})
        check("★ poke 走 api_call(send_poke)",
              len(ctx3.pokes) == 1 and ctx3.pokes[0][1].get("user_id") == "1000000005",
              ctx3.pokes)
        check("★ poke 不发文字", ctx3.sent == [], ctx3.sent)
        P._deliver({"obs": {"kind": "idle_contact", "scene": "group",
                            "target": "900000005", "dedupe_key": "poke-g"},
                    "act": "poke", "text": ""})
        check("群聊 poke 不落到发送口", len(ctx3.pokes) == 1, ctx3.pokes)
        check("模型动作词含 poke", "poke" in A.ALLOWED_ACTS, A.ALLOWED_ACTS)
        check("群聊模型判 poke 被改成 skip",
              A.parse_model_decision('{"act":"poke","text":""}', scene="group")["act"] == "skip")
        check("私聊模型判 poke 保留",
              A.parse_model_decision('{"act":"poke","text":""}', scene="private")["act"] == "poke")
        check("⑤ 冷场行为已登记", "idle_contact" in A.BEHAVIORS, sorted(A.BEHAVIORS))

        r_take = PP.poke_once(ctx3)
        check("★ 老戳插件自动让位",
              r_take.get("skipped") and "接管" in str(r_take.get("reason")), r_take)
        r_back = PP.poke_once(FakeCtx(dict(cfg_take, proactivity_takeover_legacy=False)))
        check("关掉让位开关后老插件照常判定", "接管" not in str(r_back.get("reason")), r_back)
        _orig_pt_ctx = PT._ctx
        PT._ctx = ctx3
        try:
            r_talk = PT._proactive_once(force=False)
            check("★ 老开口插件自动让位",
                  r_talk.get("skipped") and "接管" in str(r_talk.get("reason")), r_talk)
        except Exception as e:
            check("★ 老开口插件自动让位", False, repr(e))
        finally:
            PT._ctx = _orig_pt_ctx

        html_all = io.open(os.path.join(ROOT, "webui", "webui_artistic_v4.html"),
                           encoding="utf-8").read()
        check("插件页按 hidden 过滤老卡", "_manifest.hidden" in html_all)
        check("插件页显示已并入说明", "已并入「主动性」" in html_all)
        for n in ("proactive_talk", "proactive_poke"):
            m = json.load(io.open(os.path.join(ROOT, "plugins", n, "plugin.json"),
                                  encoding="utf-8"))
            check("%s 标了 merged_into/hidden" % n,
                  m.get("merged_into") == "proactivity" and m.get("hidden") is True, m)

        print("\n=== I) P3 六条：事件接线与面板 ===")
        P._ledger._states.clear()
        P._pending.clear()
        ctx4 = FakeCtx({"proactivity_enable": True, "proactivity_budget_level": 2,
                        "proactivity_quiet_hours": "", "recall_feedback_by": "main",
                        "proactivity_idle_contact_enable": False})
        P.on_load(ctx4)
        now_i = _t.time()
        P.on_event(ctx4, "message.in", {"group_id": "", "sender": "1000000006",
                                        "text": "在吗", "ts": now_i - 5})
        P.on_event(ctx4, "job.done", {"job_id": "job-x1", "label": "视频抽帧",
                                      "ok": True, "returncode": 0,
                                      "tail": "frames=48", "ts": now_i})
        check("⑨ job.done 进候选队列",
              any(o.get("kind") == "autonomy_done" for o in P._pending), P._pending)
        job_obs = next((o for o in P._pending if o.get("kind") == "autonomy_done"), {})
        check("⑨ 汇报目标是最近活跃会话", job_obs.get("target") == "1000000006", job_obs)
        P._pending.clear()
        P.on_event(ctx4, "notice.group_name", {"group_id": "900000005",
                                               "name": "新群名", "ts": now_i})
        st_obs = next((o for o in P._pending if o.get("kind") == "state_change"), {})
        check("⑫ 改群名进候选队列", bool(st_obs), P._pending)
        check("⑫ 目标是群", st_obs.get("target") == "900000005"
              and st_obs.get("scene") == "group", st_obs)
        P._pending.clear()
        P.on_event(ctx4, "notice.friend_add", {"user_id": "1000000005", "ts": now_i})
        check("⑫ 加好友进候选队列",
              any(o.get("kind") == "state_change" for o in P._pending), P._pending)

        html_p3 = io.open(os.path.join(ROOT, "plugins", "proactivity", "ui.html"),
                          encoding="utf-8").read()
        for k in ("proactivity_persona", "proactivity_personality",
                  "proactivity_behavior_style", "proactivity_reply_style",
                  "proactivity_style_pool"):
            check("面板/CFG 有人格键 %s" % k, k in P.CFG_KEYS and
                  (k in html_p3 or k in ("proactivity_personality",)), k)
        for k in ("proactivity_user_burst_enable", "proactivity_image_only_enable",
                  "proactivity_name_dropped_enable", "proactivity_resume_enable",
                  "proactivity_job_enable", "proactivity_state_enable"):
            check("面板有开关 %s" % k, 'data-c="%s"' % k in html_p3)
        check("面板标签按原清单编号",
              all(x in html_p3 for x in ("⑤ 连发没人接", "⑥ 只发图没说话",
                                         "⑦ 提到名字没 @", "⑧ 隔很久回来",
                                         "⑨ 忙完顺口提一句", "⑫ 群里有点变化",
                                         "⑬ 记着对方的事", "⑭ 说说自己的状态")),
              "标签缺失")
        for k in ("proactivity_burst_count", "proactivity_burst_min",
                  "proactivity_image_min", "proactivity_name_min",
                  "proactivity_resume_gap_h", "proactivity_resume_min"):
            check("CFG_KEYS 含 %s" % k, k in P.CFG_KEYS)
        check("CFG_KEYS 共 34 个", len(P.CFG_KEYS) == 34, len(P.CFG_KEYS))

        import plugins.vitals as V
        _orig_v = V.PLUGIN_ACTIONS["get_vitals"]
        V.PLUGIN_ACTIONS["get_vitals"] = lambda _p=None: {"vitals": {"loneliness": 0.8}}
        try:
            c = P._care_candidate(now_i, ctx4.cfg)
            check("⑭ 关心候选能取到", c and c.get("kind") == "care", c)
            check("⑭ 关心走最近活跃会话", c and c.get("target") == "1000000006", c)
        finally:
            V.PLUGIN_ACTIONS["get_vitals"] = _orig_v
        V.PLUGIN_ACTIONS["get_vitals"] = lambda _p=None: {"vitals": {"loneliness": 0.2}}
        try:
            check("⑭ 状态正常时不出候选", P._care_candidate(now_i, ctx4.cfg) is None)
        finally:
            V.PLUGIN_ACTIONS["get_vitals"] = _orig_v
    finally:
        P._data_path = orig_path
        P._start_thread = orig_start
        P.on_disable(ctx)

    print("\n=== F/G) 入口：人格与阈值只留插件面板，配置中心只留长期主动三键 ===")
    meta = json.load(io.open(os.path.join(ROOT, "scripts", "_fieldmeta.json"),
                             encoding="utf-8"))["fields"]
    mine = [k for k, v in meta.items() if v.get("panel") == "proactivity"]
    # 11-07 撤掉主动性分组时写作 `mine == []`：那时配置页不该出现任何人格预设 /
    # 阈值滑条。11-08 长期主动（drive.py）加了三个产品态开关，明确要放在
    # 「互动 → 主动性」，所以这里改成白名单：只允许这三个，旧的 proactivity_*
    # 一个都不许借这个口子回流到配置中心。
    DRIVE_KEYS = {"drive_enable", "drive_interval_min", "drive_outreach_enable"}
    check("配置中心主动性分组只留长期主动三键",
          set(mine) == DRIVE_KEYS, sorted(set(mine) ^ DRIVE_KEYS))
    check("旧主动性阈值与人格字段仍不进配置中心",
          not [k for k in mine if k.startswith("proactivity_")],
          [k for k in mine if k.startswith("proactivity_")][:5])
    check("预算档位也不在配置页", "proactivity_budget_level" not in meta)
    ui = io.open(os.path.join(ROOT, "plugins", "proactivity", "ui.html"),
                 encoding="utf-8").read()
    check("插件面板自己还有档位滑条",
          'type="range" min="0" max="3"' in ui)
    check("插件面板有实时档位名", "lv-label" in ui)
    check("面板不暴露「男娘」选项", 'value="tsundere"' not in ui)
    check("面板只留四个人格选项",
          ui.count("<option value=") == 4, ui.count("<option value="))
    check("① 默认开", A.BEHAVIORS["no_reply"]["enabled"] is True)
    check("② 默认开", A.BEHAVIORS["at_dropped"]["enabled"] is True)
    check("③ 默认开", A.BEHAVIORS["pending_topic"]["enabled"] is True)
    check("④ 默认关（防双回）", A.BEHAVIORS["recall"]["enabled"] is False)

    print("\n" + "=" * 56)
    print(f"通过 {PASS} / 失败 {FAIL}")
    for x in FAILURES:
        print(f"  FAIL  {x}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
