# -*- coding: utf-8 -*-
"""tests/test_outgoing_audit.py —— 出站文本按场景脱敏（学 qq-bridge 的回复审计）

qq-bridge 的做法：agent 回复里出现本机路径或凭据特征 → **整条拦截不发**。
我们改成两级，因为它有真实用途冲突：
  · 私聊里我们本来就要报路径（"包在 E:\\...\\dist 发你了"）→ 只脱凭据；
  · 群里报本机路径等于泄露目录结构 → 路径一起脱。

用户 2026-10-13：「我们的凭据是发不出来的，你可以试试」——所以这是防患，
不是拦路虎：本测试锁死"正常中文/时间/URL 不被误伤"与"两类敏感内容确实被脱"。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import bot_core  # noqa: E402

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
        FAILURES.append(f"{name} :: {extra}")
        print(f"  [FAIL] {name} :: {extra}")


def main():
    print("=== ① 凭据：任何场景都脱 ===")
    cred_cases = [
        ("我的 key 是 sk-abcdefghijklmnopqrstuvwxyz123", "api-key"),
        ("Authorization: Bearer abcdefghijklmnop123456", "auth-token"),
        ("api_key=AbCdEf123456789", "cred-assign"),
        ("password: hunter2xyz", "cred-assign"),
    ]
    for t, expect in cred_cases:
        for g in (False, True):
            out, f = bot_core.audit_outgoing(t, is_group=g)
            check(f"{expect} 脱敏（群={g}）", "[凭据已隐藏]" in out and any(expect in x for x in f),
                  f"{out[:50]!r} {f}")

    print("\n=== ② 本机路径：群里脱、私聊保留（我们本来要报路径）===")
    win = "文件在 E:\\EggBloom水星\\dist\\EggBloom-Full.zip"
    out_g, f_g = bot_core.audit_outgoing(win, is_group=True)
    check("群里：Windows 路径被脱", "[本机路径已隐藏]" in out_g, f"{out_g[:60]!r}")
    out_p, f_p = bot_core.audit_outgoing(win, is_group=False)
    check("私聊：Windows 路径保留（要告诉用户文件在哪）", "E:\\EggBloom水星" in out_p and not f_p,
          f"{out_p[:60]!r}")
    unix = "配置在 /home/administrator/.dsh/settings.json 里"
    out_u, f_u = bot_core.audit_outgoing(unix, is_group=True)
    check("群里：Unix 路径被脱", "[本机路径已隐藏]" in out_u, f"{out_u[:60]!r}")
    out_u2, _ = bot_core.audit_outgoing(unix, is_group=False)
    check("私聊：Unix 路径保留", "/home/administrator" in out_u2, out_u2[:60])

    print("\n=== ③ 不能误伤正常内容 ===")
    safe = [
        "今天群里挺热闹的哈哈哈哈",
        "现在是 3:05，我看到了",
        "这个 bug 复现率大概 2:30 左右",
        "看这个 https://example.com/a/b/c 就行",
        "他说 e.g. 这样",
        "C++ 和 Python 你更熟哪个",
        "[CQ:at,qq=10000] 在吗",
        "版本 v1.5.4 已发布",
    ]
    for t in safe:
        for g in (False, True):
            out, f = bot_core.audit_outgoing(t, is_group=g)
            check(f"不误伤：{t[:24]!r}（群={g}）", out == t and not f, f"{out[:50]!r} {f}")

    print("\n=== ④ 发送出口真的走审计 ===")
    sent = []
    orig_api = bot_core.api_call
    orig_chatlog = bot_core.chat_log
    try:
        bot_core.api_call = lambda ep, data=None, timeout=15: (
            sent.append((ep, data.get("message"))) or {"retcode": 0})
        bot_core.chat_log = lambda *a, **k: None
        sent.clear()
        bot_core.send_message("key 是 sk-abcdefghijklmnopqrstuvwxyz123", target_qq="10001")
        check("私聊出口：凭据已脱敏后才发出", "[凭据已隐藏]" in str(sent[0][1]), str(sent[0][1])[:70])
        sent.clear()
        bot_core.send_message("包在 E:\\dist\\Full.zip 我发你", target_qq="10001")
        check("私聊出口：路径照旧保留（有用信息）", "E:\\dist\\Full.zip" in str(sent[0][1]),
              str(sent[0][1])[:70])
        sent.clear()
        bot_core.send_group_message("20001", "文件在 E:\\dist\\Full.zip")
        payload = sent[0][1]
        flat = str(payload)
        check("群出口：本机路径已脱敏", "[本机路径已隐藏]" in flat, flat[:80])
    finally:
        bot_core.api_call = orig_api
        bot_core.chat_log = orig_chatlog

    print("\n=== ⑤ 引用回复与审计叠加不冲突 ===")
    sent = []
    orig_api = bot_core.api_call
    orig_chatlog = bot_core.chat_log
    try:
        bot_core.api_call = lambda ep, data=None, timeout=15: (
            sent.append((ep, data.get("message"))) or {"retcode": 0})
        bot_core.chat_log = lambda *a, **k: None
        sent.clear()
        bot_core.send_group_message("20001", "在 E:\\dist 的那个包我发你", reply_id=12345)
        segs = sent[0][1]
        ok = (isinstance(segs, list) and segs[0].get("type") == "reply"
              and any("[本机路径已隐藏]" in str(x) for x in segs))
        check("带引用时审计照常生效", ok, str(segs)[:110])
    finally:
        bot_core.api_call = orig_api
        bot_core.chat_log = orig_chatlog

    print("\n" + "=" * 58)
    print(f"通过 {PASS} / 失败 {FAIL}")
    if FAILURES:
        print("失败项：")
        for f in FAILURES:
            print("  -", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
