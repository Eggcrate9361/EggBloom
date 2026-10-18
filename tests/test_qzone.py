# -*- coding: utf-8 -*-
"""qzone 插件自测（1.5 任务 3 验收）

覆盖：
1. ABI 完整性（PLUGIN_SPEC / PLUGIN_MANIFEST / PLUGIN_HOOKS / PLUGIN_ACTIONS + execute_action 兜底）
2. 9 个动作齐全，四层分类正确，默认全关
3. 门禁：总开关关 → 全部拒绝；层没开 → 该层动作拒绝；开了 → 放行
4. 参数校验（可读错误）：send_qzone_msg content 空 / ugc_right 非法 / 16,128 缺 target_uins
5. ★ 频率硬上限：第 1、2 条通过；第 3 条被拒绝；间隔 <4h 被拒绝；跨天恢复
6. api_call 未注入时给可读原因（不静默、不抛异常）
7. 原始 JSON 透传（run 动作返回 raw）
8. 调度器集成：on_event('qzone.publish') 走 publish（用假 ctx）
9. 聊天触发：on_message 识别"发条说说 xxx"
10. 状态持久化：set_config 后 status 读回
"""
import os
import sys
import time
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))      # D:\git\EggBloom\tests
ROOT = os.path.dirname(HERE)                            # D:\git\EggBloom
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK]   {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} {detail}")


class FakeCtx:
    """极简宿主 ctx：可注入任意 api_call 返回，并可关闭注入。"""

    def __init__(self, host=None):
        self.host = host or {}
        self.logs = []
        self.ai_reply = "测试说说的正文"

    def log(self, msg, level="info"):
        self.logs.append((level, msg))

    def api_call(self, action, params=None):
        if action not in self.host:
            return None
        r = self.host[action]
        if callable(r):
            return r(action, params)
        return r

    def ai_chat(self, messages, tools=None, max_tokens=800):
        return self.ai_reply


def _reset_state(mod):
    """每个用例前把状态重置为默认。"""
    with mod._lock:
        mod._state = dict(mod._DEFAULT_STATE)
        mod._state["layers"] = dict(mod._DEFAULT_STATE["layers"])
        mod._state["publish_log"] = []
    mod._save_state(mod._state)


def enable_all(mod, ctx):
    mod._ctx = ctx
    _reset_state(mod)
    mod._state["enabled"] = True
    for k in mod.LAYERS:
        mod._state["layers"][k] = True
    mod._save_state(mod._state)


def main():
    global PASS, FAIL
    print("=" * 62)
    print("qzone 插件自测")
    print("=" * 62)

    import plugins.qzone as Q

    # ── 1. ABI 完整性 ──
    print("\n[1] ABI 完整性")
    check("PLUGIN_SPEC == 1", getattr(Q, "PLUGIN_SPEC", None) == 1)
    mf = getattr(Q, "PLUGIN_MANIFEST", None)
    check("PLUGIN_MANIFEST 是 dict", isinstance(mf, dict))
    check("manifest.api_version == 2", mf and int(mf.get("api_version", 0)) == 2)
    check("manifest.default_enabled is False", mf and mf.get("default_enabled") is False)
    from plugins.abi import validate_manifest
    check("manifest 通过 ABI 校验", validate_manifest(mf) is None, validate_manifest(mf))
    check("PLUGIN_HOOKS 含 on_event", "on_event" in (getattr(Q, "PLUGIN_HOOKS", {}) or {}))
    check("PLUGIN_HOOKS 含 on_message", "on_message" in (getattr(Q, "PLUGIN_HOOKS", {}) or {}))
    check("execute_action 可调用", callable(getattr(Q, "execute_action", None)))
    check("execute_action 未知动作给错误",
          "error" in Q.execute_action("__nope__", {}))

    # ── 2. 动作齐全 + 分层 ──
    print("\n[2] 动作齐全与分层")
    expect = {"read": 2, "interact": 3, "write": 1, "manage": 3}
    for layer, n in expect.items():
        check(f"{layer} 层 {n} 个动作", len(Q.LAYERS[layer]) == n, Q.LAYERS[layer])
    check("共 9 个动作", len(Q.ALL_ACTIONS) == 9, len(Q.ALL_ACTIONS))
    for a in ("get_qzone_msg_list", "get_qzone_feeds", "like_qzone", "unlike_qzone",
              "comment_qzone", "send_qzone_msg", "delete_qzone_msg",
              "set_qzone_msg_right", "set_qzone_ban"):
        check(f"动作存在：{a}", a in Q.ALL_ACTIONS)
    check("默认能力层全关", all(v is False for v in Q._DEFAULT_STATE["layers"].values()))
    check("默认总开关关", Q._DEFAULT_STATE["enabled"] is False)

    # ── 3. 门禁 ──
    print("\n[3] 能力门禁")
    ctx = FakeCtx(host={"get_qzone_feeds": {"ok": 1, "data": []}})
    Q._ctx = ctx
    _reset_state(Q)
    r = Q.call("get_qzone_feeds", {})
    check("总开关关 → 拒绝", r.get("ok") is False and "未启用" in r.get("error", ""), r)
    # 只开总开关，不开层
    Q._state["enabled"] = True
    Q._save_state(Q._state)
    r = Q.call("get_qzone_feeds", {})
    check("层未开 → 拒绝且说明层名", r.get("ok") is False and "read" in r.get("error", ""), r)
    # 开 read 层
    Q._state["layers"]["read"] = True
    Q._save_state(Q._state)
    r = Q.call("get_qzone_feeds", {})
    check("开 read 层 → 放行", r.get("ok") is True, r)
    r = Q.call("send_qzone_msg", {"content": "x"})
    check("write 层未开 → 发说说拒绝", r.get("ok") is False and "write" in r.get("error", ""), r)

    # ── 4. 参数校验（可读错误）──
    print("\n[4] 参数校验")
    enable_all(Q, ctx)
    r = Q.call("send_qzone_msg", {"content": "  "})
    check("空正文 → 拒绝", r.get("ok") is False and "正文" in r.get("error", ""), r)
    r = Q.call("send_qzone_msg", {"content": "hi", "ugc_right": 7})
    check("ugc_right=7 非法 → 拒绝", r.get("ok") is False and "ugc_right" in r.get("error", ""), r)
    r = Q.call("send_qzone_msg", {"content": "hi", "ugc_right": 16})
    check("ugc_right=16 缺 target_uins → 拒绝",
          r.get("ok") is False and "target_uins" in r.get("error", ""), r)
    # 这条要验的是"参数校验通过、继续往下走"，所以换一个真的能响应发送的 ctx
    ctx_ok = FakeCtx(host={"send_qzone_msg": lambda a, p: {"status": "ok", "data": {"tid": "t9"}}})
    enable_all(Q, ctx_ok)
    r = Q.call("send_qzone_msg", {"content": "hi", "ugc_right": 128, "target_uins": [1, 2]})
    check("ugc_right=128 带 target_uins → 通过校验（进入上限检查）",
          r.get("ok") is True, r)
    r = Q.call("like_qzone", {"tid": ""})
    check("like 缺 tid → 拒绝", r.get("ok") is False and "tid" in r.get("error", ""), r)
    r = Q.call("set_qzone_ban", {"user_id": "abc"})
    check("set_qzone_ban 非整数 → 拒绝", r.get("ok") is False and "user_id" in r.get("error", ""), r)
    r = Q.call("delete_qzone_msg", {"tid": "t1"})
    check("delete 未 confirm → 拒绝", r.get("ok") is False and "confirm" in r.get("error", ""), r)
    r = Q.call("__unknown__", {})
    check("未知动作 → 拒绝并列出支持项", r.get("ok") is False and "未知动作" in r.get("error", ""), r)

    # ── 5. ★ 频率硬上限 ──
    print("\n[5] ★ 频率硬上限（每天≤2 / 间隔≥4h）")
    sent = []

    def fake_send(action, params):
        sent.append(dict(params))
        return {"status": "ok", "retcode": 0, "data": {"tid": f"tid{len(sent)}"}}

    ctx2 = FakeCtx(host={"send_qzone_msg": fake_send})
    enable_all(Q, ctx2)

    r1 = Q.call("send_qzone_msg", {"content": "第一条"})
    check("第 1 条 → 成功", r1.get("ok") is True, r1)
    check("第 1 条拿到 tid", r1.get("tid") == "tid1", r1.get("tid"))

    # 第 2、3 条：先绕过 4 小时间隔（把上一条时间往前挪）。
    # ★ 必须同时满足两个约束，否则测试在「凌晨 00:00–04:00」跑必红：
    #     ① 间隔 ≥4h  → ts 要早于 now-4h
    #     ② 仍属「今天」→ ts 要晚于今天 00:00（否则被 _today_bounds 判为隔天，
    #        每日上限就不生效了）
    #   凌晨 0–4 点这两个约束在真实时间轴上互斥 —— 所以这里把模块内的
    #   time.time() 钉到一个「当天 15:00」的合成时刻，让整段在确定的时间轴上
    #   受测，而不是依赖墙上时钟。测试结束会还原。
    #   （旧写法直接用 time.time()-MIN_INTERVAL，是测试的跨天缺陷，不是实现问题。）
    _real_time = Q.time.time
    _n = _real_time()
    _today0 = datetime.fromtimestamp(_n).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    _synth = _today0 + 15 * 3600          # 当天 15:00
    Q.time.time = lambda: _synth
    try:
        with Q._lock:
            Q._state["publish_log"][-1]["ts"] = _synth - Q.MIN_INTERVAL_SEC - 10
            Q._save_state(Q._state)
        r2 = Q.call("send_qzone_msg", {"content": "第二条"})
        check("第 2 条（已满足间隔）→ 成功", r2.get("ok") is True, r2)

        with Q._lock:
            Q._state["publish_log"][-1]["ts"] = _synth - Q.MIN_INTERVAL_SEC - 10
            Q._save_state(Q._state)
        r3 = Q.call("send_qzone_msg", {"content": "第三条"})
    finally:
        Q.time.time = _real_time
    check("★ 第 3 条 → 被拒绝", r3.get("ok") is False, r3)
    check("★ 拒绝原因含每日上限", "上限" in r3.get("error", "") or "2 条" in r3.get("error", ""), r3)
    check("★ 第 3 条确实没发出去（发送次数仍为 2）", len(sent) == 2, len(sent))

    # 间隔限制单独验：清空后连发两条
    print("\n[5b] 间隔限制单独验")
    enable_all(Q, ctx2)
    sent.clear()
    q1 = Q.call("send_qzone_msg", {"content": "a"})
    check("间隔测试：第 1 条成功", q1.get("ok") is True, q1)
    q2 = Q.call("send_qzone_msg", {"content": "b"})
    check("★ 立即发第 2 条 → 被间隔拒绝", q2.get("ok") is False, q2)
    check("★ 拒绝原因含间隔/小时", "间隔" in q2.get("error", "") or "小时" in q2.get("error", ""), q2)
    check("★ 发送次数仍为 1", len(sent) == 1, len(sent))

    # 跨天恢复
    print("\n[5c] 跨天恢复")
    enable_all(Q, ctx2)
    sent.clear()
    Q.call("send_qzone_msg", {"content": "今天"})
    # 把记录时间挪到昨天
    with Q._lock:
        for x in Q._state["publish_log"]:
            x["ts"] = time.time() - 86400 - 10
        Q._save_state(Q._state)
    lim = Q.check_publish_limit()
    check("跨天后额度恢复", lim["allowed"] is True and lim["used_today"] == 0, lim)

    # ── 6. api_call 未注入 ──
    print("\n[6] 宿主未注入时给可读原因")
    ctx3 = FakeCtx(host={})       # api_call 一律返回 None
    enable_all(Q, ctx3)
    r = Q.call("get_qzone_feeds", {})
    check("api_call 未注入 → 可读错误（不静默）",
          r.get("ok") is False and isinstance(r.get("error"), str) and r["error"], r)

    # ── 7. 原始 JSON 透传 ──
    print("\n[7] 原始 JSON 透传")
    raw = {"status": "ok", "retcode": 0, "data": {"feeds": [{"tid": "x", "content": "y"}]}}
    ctx4 = FakeCtx(host={"get_qzone_feeds": raw})
    enable_all(Q, ctx4)
    r = Q.call("get_qzone_feeds", {"page_num": 1, "count": 5})
    check("run 返回含原始 raw", r.get("ok") is True and r.get("raw") == raw, r)
    # execute_action('run') 也走试运行通道
    r2 = Q.execute_action("run", {"action": "get_qzone_feeds", "params": {"count": 5}})
    check("execute_action('run') 透传 raw", r2.get("raw") == raw, r2)
    check("execute_action('run') 非法 JSON → 可读错误",
          "error" in Q.execute_action("run", {"action": "get_qzone_feeds", "params": "{bad"}))

    # ── 8. 调度器集成 ──
    print("\n[8] 调度器 on_event 集成")
    ctx5 = FakeCtx(host={"send_qzone_msg": fake_send})
    enable_all(Q, ctx5)
    ctx5.ai_reply = "深夜的风有点凉。"
    sent.clear()
    r = Q.on_event(ctx5, "qzone.publish", {"hint": "写点夜里的感受"})
    check("qzone.publish → 生成并发布", r and r.get("ok") is True, r)
    check("发布的内容来自 AI", r and r.get("content") == "深夜的风有点凉。", r)
    check("已调用 send_qzone_msg", len(sent) == 1, len(sent))
    r = Q.on_event(ctx5, "other.event", {})
    check("非 qzone.publish 事件 → 不动作", r is None, r)

    # ── 9. 聊天触发 ──
    print("\n[9] 聊天触发 on_message")
    enable_all(Q, ctx5)
    sent.clear()
    out = Q.on_message(ctx5, {"text": "发条说说 今天天气不错", "group_id": "1", "sender": "2"})
    check("识别『发条说说 xxx』", isinstance(out, str) and "说说" in out, out)
    check("带正文时直接发该正文", sent and sent[0].get("content") == "今天天气不错", sent)
    out2 = Q.on_message(ctx5, {"text": "你好啊"})
    check("普通消息 → 不干预（None）", out2 is None, out2)
    # 关掉 write 层后不响应
    Q._state["layers"]["write"] = False
    Q._save_state(Q._state)
    out3 = Q.on_message(ctx5, {"text": "发条说说 测试"})
    check("write 层关闭 → 聊天触发不响应", out3 is None, out3)

    # ── 10. 状态持久化 ──
    print("\n[10] 状态持久化与 status")
    enable_all(Q, ctx5)
    Q.execute_action("set_config", {"persona": "测试人设", "layers": {"read": True, "write": False}})
    st = Q.execute_action("status", {})
    check("set_config 后 persona 读回", st.get("persona") == "测试人设", st.get("persona"))
    check("set_config 后 layers 读回", st.get("layers", {}).get("read") is True
          and st.get("layers", {}).get("write") is False, st.get("layers"))
    check("status 含 limit 元信息",
          st.get("limit", {}).get("max_per_day") == 2
          and st.get("limit", {}).get("min_interval_hours") == 4, st.get("limit"))
    check("status 含 ugc_options 5 项", len(st.get("ugc_options") or []) == 5)
    check("status 含 9 个动作", len(st.get("actions") or []) == 9)

    # 清理测试写入的状态文件
    _reset_state(Q)
    Q._state["enabled"] = False
    Q._save_state(Q._state)

    print("\n" + "=" * 62)
    print(f"结果: {PASS} passed, {FAIL} failed")
    print("=" * 62)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
