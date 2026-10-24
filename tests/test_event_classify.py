# -*- coding: utf-8 -*-
"""tests/test_event_classify.py —— 日志页事件分类回归

背景：日志页原来直接把日志文件整行铺出来，一条「小明: 今天去不去」和一串
「loaded module ego」长得一样，用户读不出来发生了什么。现在服务端
`webui_server.collect_events` 把每条归成六类（message / decision / tool /
cost / error / debug），日志页只画前五类，调试行去控制台。

2026-10-24 晚重写分类器。第一版凭**关键词表**猜（"决策放行"/"建议插话"/
"工具调用"…），在 25186 行真实日志里命中 0 次 —— 那些字样是想象的，
bot 实际打的是 `[事件] 收到:` / `[发送]` / `AI 回复` 这套。结果是除了
error 全判成 debug，**页面只剩错误**（用户实拍：日志页一片红）。

这个文件的样本全部抄自真实日志（AppData/Local/EggBloom_v3/qq_bot.log），
不自己编格式。锁死六件事：

  1. 六类各自的真实行判对
  2. 噪音行（心跳/启动冷却/初始化）→ debug，且量最大
  3. level 是权威判据，不凭正文再猜一遍
  4. 聊天记录仍优先按 direction 判（正文带「失败」也算消息）
  5. who / scope 从真实行里拆得出，昵称带括号也能拆
  6. 一段真实日志切片里，非 error 的类别必须都非空（"只显示错误"的回归）
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import webui.webui_server as ws  # noqa: E402

PASS = 0
FAIL = 0
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


def classify(text, **kw):
    d = {"text": text, "time": "23:44:30"}
    d.update(kw)
    return ws._classify_event(d)


# ── 真实日志样本（原文照抄，不改格式）──────────────────────────────
REAL = {
    "message_in_private":
        "[事件] 收到: post_type=message msg_type=private uid=(/ω＼)(3150007406) gid=",
    "message_in_notice":
        "[事件] 收到: post_type=notice msg_type= uid=(/ω＼)(3150007406) gid=",
    "message_in_group":
        "[事件] 收到: post_type=message msg_type=group uid=小明(10001) gid=829854136",
    "message_poke":
        "[事件] 戳一戳: user_id=(/ω＼)(3150007406) group_id=私聊",
    "message_poke_back":
        "[事件] 反戳 send_poke user=(/ω＼)(3150007406) group=私聊 retcode=0",
    "message_send_private":
        "[发送] 私聊 → (/ω＼)(3150007406): retcode=0 msg=",
    "message_send_group":
        "[发送] 群[1] → 小明(10001): retcode=0",
    "message_reply_text":
        "私聊戳一戳回复: 大半夜戳我，是想我了还是睡不着",
    "message_emoji":
        "私聊戳一戳表情包: 炸毛",
    "message_image":
        "发送图片成功: zhenxun_rl_22625868_014.gif → (/ω＼)(3150007406)",
    "message_merge":
        "群[829854136] 合并消息: [小明] [引用:小红] [图片]",
    "decision_no_interrupt":
        "群[_test_pacing_] [观察] 暂不插话，300s 后再看一眼 · 测试",
    "decision_skip":
        "群[333] 不参与：测试",
    "tool_catalog":
        "sandglass_recent、sandglass_search",
    "cost_ai":
        "AI 回复 (4411 tokens, ￥0.002205, 缓存命中 0 / 未命中 4253, 场景 private)",
    "error_onebot":
        "OneBot API 失败 [get_stranger_info]: <urlopen error [WinError 10061] "
        "由于目标计算机积极拒绝，无法连接。>",
    "error_crash":
        "[CRASH] 未捕获异常:",
    # 噪音：量最大，必须挡住，否则"消息"分类被 30 秒一次的心跳淹掉
    "noise_heartbeat":
        "[事件] 收到: post_type=meta_event msg_type= uid= gid=",
    "noise_cooldown":
        "启动冷却中… 5s/30s（此期间抓取消息但不回复）",
    "noise_cooldown_done":
        "启动冷却完毕，开始监听消息",
    "noise_nick_cache":
        "昵称缓存总计: 27人",
    "noise_init":
        "[Sandglass] 记忆系统初始化完成",
    "noise_init2":
        "[Sandglass] 后台初始化中...",
    "noise_tools_ready":
        "工具注册表就绪: {'core': 1, 'desktop': 8}",
    "noise_debug":
        "[调试] PLUGIN_OK=True",
    "noise_emoji_skip":
        "[表情] 无情绪，跳过",
    "noise_reload":
        "[配置热重载] AI_TEMPERATURE: 0.7 → 0.55 生效",
}


def main():
    print("[1] 六类各自的真实行")
    for key, want in (
        ("message_in_private", "message"),
        ("message_in_notice", "message"),
        ("message_in_group", "message"),
        ("message_poke", "message"),
        ("message_poke_back", "message"),
        ("message_send_private", "message"),
        ("message_send_group", "message"),
        ("message_reply_text", "message"),
        ("message_emoji", "message"),
        ("message_image", "message"),
        ("message_merge", "message"),
        ("decision_no_interrupt", "decision"),
        ("decision_skip", "decision"),
        ("cost_ai", "cost"),
        ("error_onebot", "error"),
        ("error_crash", "error"),
    ):
        check(f"{key} → {want}", classify(REAL[key]) == want,
              classify(REAL[key]))

    print("\n[2] 噪音行一律 → debug（量最大，不挡会淹掉消息）")
    for key in [k for k in REAL if k.startswith("noise_")]:
        check(f"{key} → debug", classify(REAL[key]) == "debug", classify(REAL[key]))

    print("\n[3] level 是权威判据，不凭正文再猜")
    check("level=err 的任意行 → error（哪怕正文没有错误词）",
          classify("协议层挂了", level="err") == "error")
    check("level=ai → cost", classify("AI 回复 (1 tokens)", level="ai") == "cost")
    check("level=send → message",
          classify("[发送] 群[1] → 小明(10001): retcode=0", level="send") == "message")
    # 反向：正文带「失败」但 level 不是 err，也不能凭正文判 error
    check("正文带「失败」但 level=info → 不是 error",
          classify("写盘失败（已跳过本轮）", level="info") != "error")
    check("[CHAT] 记录优先按 direction 判，不看正文",
          classify("这次考试又失败了", direction="in") == "message")
    check("[CHAT] out 也是 message",
          classify("他说决策放行了", direction="out") == "message")

    print("\n[4] who / scope：昵称带括号也要拆得出")
    m = ws._norm_event({"text": REAL["message_in_private"], "time": "23:44:23"}, "message")
    check("私聊收到：who=(/ω＼)", m["who"] == "(/ω＼)", m)
    check("私聊收到：scope=私聊", m["scope"] == "私聊", m)
    check("私聊收到：dir=in", m["dir"] == "in", m)
    m = ws._norm_event({"text": REAL["message_in_group"], "time": "23:44:23"}, "message")
    check("群收到：who=小明", m["who"] == "小明", m)
    check("群收到：scope 带群号", m["scope"] == "群 829854136", m)
    m = ws._norm_event({"text": REAL["message_send_private"], "time": "23:44:30"}, "message")
    check("私聊发出：dir=out", m["dir"] == "out", m)
    check("私聊发出：who=(/ω＼)（不带尾部冒号）", m["who"] == "(/ω＼)", m)
    m = ws._norm_event({"text": REAL["message_poke"], "time": "23:44:28"}, "message")
    check("戳一戳：who=(/ω＼)", m["who"] == "(/ω＼)", m)
    check("戳一戳：scope=私聊", m["scope"] == "私聊", m)
    # 取不到就不猜，不能编一个
    m = ws._norm_event({"text": REAL["message_reply_text"], "time": "23:44:30"}, "message")
    check("回复正文行：who 为空（行里没有，不猜）", m["who"] == "", m)

    print("\n[5] collect_events：形状与计数")
    rows = [{"text": REAL[k], "time": "23:44:30"} for k in
            ("message_in_private", "decision_no_interrupt", "cost_ai",
             "error_crash", "noise_heartbeat", "noise_debug")]
    orig = ws._collect_logs
    ws._collect_logs = lambda limit=400: rows
    try:
        out = ws.collect_events(400)
    finally:
        ws._collect_logs = orig
    check("调试行进不去事件列表", len(out["events"]) == 4,
          [e["kind"] for e in out["events"]])
    check("counts.debug 记 2", out["counts"]["debug"] == 2, out["counts"])
    check("counts 有 cost 这一类", "cost" in out["counts"], out["counts"])
    check("total 是原始行数（含调试）", out["total"] == 6, out["total"])

    print("\n[6] ★ 回归：一段真实日志切片里，非 error 类别必须都非空")
    # 这条就是"只显示错误"的守护神。分类器一旦又退回到只认错误，
    # 这里立刻红。
    rows2 = [{"text": REAL[k], "time": "23:44:30"} for k in
             ("message_in_private", "message_send_private", "message_poke",
              "decision_no_interrupt", "cost_ai", "error_crash",
              "noise_heartbeat", "noise_cooldown")]
    ws._collect_logs = lambda limit=400: rows2
    try:
        out2 = ws.collect_events(400)
    finally:
        ws._collect_logs = orig
    c = out2["counts"]
    for kind in ("message", "decision", "cost", "error"):
        check(f"{kind} 非空（页面不该只剩错误）", c.get(kind, 0) > 0, c)
    check("debug 把噪音挡住了", c["debug"] == 2, c)

    print("\n[7] 真日志文件（存在才查，不依赖机器状态）")
    real_log = os.path.join(os.path.expanduser("~"), "AppData", "Local",
                            "EggBloom_v3", "qq_bot.log")
    if os.path.exists(real_log):
        try:
            r = ws.collect_events(400)
            rc = r["counts"]
            check("真日志里 message 非空", rc.get("message", 0) > 0, rc)
            check("真日志里 error 不是唯一非空类",
                  sum(1 for k in ("message", "decision", "tool", "cost")
                      if rc.get(k, 0) > 0) >= 1, rc)
            print(f"       真日志 counts: {rc}")
        except Exception as e:
            check("读真日志不抛异常", False, e)
    else:
        print("       （跳过：本机没有该日志文件）")

    print("\n" + "=" * 56)
    print(f"通过 {PASS} / 失败 {FAIL}")
    if FAILURES:
        print("失败项：")
        for f in FAILURES:
            print("  -", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())