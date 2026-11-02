# -*- coding: utf-8 -*-
"""统一出站队列（1.6.0 刀 5）。

## 契约

1. 所有出站（文本/图片/戳一戳）都进同一个队列，**单 worker 串行**发送；
2. 一条消息在日志里**只出现一次** `[发送]`；
3. 失败自动重试一次；两次都失败返回 False，绝不假装成功；
4. 可选：`outbound_dedupe_sec` 去重、`outbound_min_gap_ms` 限频（默认都关，
   避免改变现有节奏）；
5. 没注入底层发送器时"丢弃并记日志"，不抛异常、不卡死。

运行：python tests/test_outbound.py
"""
import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from harness import outbound as ob  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name if not extra else f"{name}  → {extra}")


def reset(cfg=None):
    ob.reset_stats()
    ob.set_config((lambda: cfg or {}))
    ob._LAST_SENT.clear()
    ob._RECENT.clear()


# ── 1. 串行 + 日志只打一次 ──────────────────────────────────────────
reset()
sent = []
logs = []
ob.set_logger(lambda m: logs.append(m))
order = []


def sender(kind, target, payload, is_group):
    order.append((kind, target, is_group))
    time.sleep(0.05)                      # 模拟网络
    sent.append(payload.get("text"))
    return {"retcode": 0}


ob.set_sender(sender)
ok = ob.send_text("你好啊", "10001")
check("发一条成功", ok is True, ok)
check("真的发出去了", sent == ["你好啊"], sent)
check("★ 日志只有一行 [发送]", len([m for m in logs if m.startswith("[发送]")]) == 1, logs)
check("日志里标明私聊与目标", any("私聊" in m and "10001" in m for m in logs), logs)

# ── 2. 多线程并发 → 串行（不交错）──────────────────────────────────
reset()
ob.set_logger(lambda m: None)
active = {"now": 0, "max": 0}
lock = threading.Lock()


def slow_sender(kind, target, payload, is_group):
    with lock:
        active["now"] += 1
        active["max"] = max(active["max"], active["now"])
    time.sleep(0.03)
    with lock:
        active["now"] -= 1
    return {"retcode": 0}


ob.set_sender(slow_sender)
ths = [threading.Thread(target=ob.send_text, args=(f"m{i}", "10002")) for i in range(6)]
[t.start() for t in ths]
[t.join() for t in ths]
check("★ 6 个线程并发发送，同时只有一个在发", active["max"] == 1, active["max"])

# ── 3. 失败重试一次 ────────────────────────────────────────────────
reset()
ob.set_logger(lambda m: None)
calls = {"n": 0}


def flaky(kind, target, payload, is_group):
    calls["n"] += 1
    if calls["n"] == 1:
        return {"retcode": 100, "msg": "boom"}
    return {"retcode": 0}


ob.set_sender(flaky)
ok = ob.send_text("第一次失败", "10003")
check("★ 失败后重试一次成功", ok is True and calls["n"] == 2, (ok, calls["n"]))
check("统计里记了重试", ob.stats()["retried"] == 1, ob.stats())

# ── 4. 两次都失败 → 如实 False，日志记失败 ────────────────────────
reset()
logs = []
ob.set_logger(lambda m: logs.append(m))
ob.set_sender(lambda *a: {"retcode": 102, "msg": "bad"})
ok = ob.send_text("怎么都发不出去", "10004")
check("★ 两次失败返回 False（不假装成功）", ok is False, ok)
check("失败也进统计", ob.stats()["failed"] == 1, ob.stats())
check("失败有日志", any("retcode=102" in m for m in logs), logs)

# ── 5. 去重窗口（默认关；开了才生效）───────────────────────────────
reset({"outbound_dedupe_sec": 5})
ob.set_logger(lambda m: None)
n = {"c": 0}
ob.set_sender(lambda *a: (n.__setitem__("c", n["c"] + 1), {"retcode": 0})[1])
ob.send_text("重复的话", "10005")
ob.send_text("重复的话", "10005")
check("★ 开了去重：同 target 同内容只发一次", n["c"] == 1, n["c"])
ob.send_text("不一样的话", "10005")
check("内容不同照发", n["c"] == 2, n["c"])
check("去重计数可查", ob.stats()["deduped"] == 1, ob.stats())

reset()
ob.set_logger(lambda m: None)
n2 = {"c": 0}
ob.set_sender(lambda *a: (n2.__setitem__("c", n2["c"] + 1), {"retcode": 0})[1])
ob.send_text("同一句", "10006")
ob.send_text("同一句", "10006")
check("默认不去重（行为不变）", n2["c"] == 2, n2["c"])

# ── 6. 没有发送器 → 丢弃但不崩 ────────────────────────────────────
reset()
ob.set_logger(lambda m: None)
ob.set_sender(None)
ok = ob.send_text("没有底层", "10007")
check("★ 没注入发送器时返回 False 且不抛", ok is False, ok)

# ── 7. 图片走同一队列 ─────────────────────────────────────────────
reset()
ob.set_logger(lambda m: None)
kinds = []
ob.set_sender(lambda k, t, p, g: (kinds.append((k, t, g)), {"retcode": 0})[1])
ok = ob.send_segment([{"type": "image", "data": {"file": "base64://x"}}], "10008", kind="image")
check("图片也能发（走队列）", ok is True and kinds == [("image", "10008", False)], kinds)
ok2 = ob.send_segment([{"type": "image", "data": {"file": "base64://x"}}], "99", is_group=True, kind="image")
check("群图 is_group 传对", ok2 is True and kinds[-1] == ("image", "99", True), kinds)

# ── 8. 验收：直连出口只剩适配器一处 ────────────────────────────────
def scan_direct(path_list):
    """返回 [(文件, 端点)] —— 真的调用了 api_call("send_*_msg") 的地方。

    用 AST 找**真实调用**：注释、文档字符串里提到端点名不算
    （`harness/outbound.py` 的说明就写着那几个名字）。
    """
    import ast

    bad = []
    for p in path_list:
        try:
            s = open(p, encoding="utf-8", errors="replace").read()
            tree = ast.parse(s)
        except Exception:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            if getattr(fn, "id", None) != "api_call":
                continue
            if not node.args or not isinstance(node.args[0], ast.Constant):
                continue
            if node.args[0].value in ("send_group_msg", "send_private_msg"):
                bad.append((os.path.basename(p), node.args[0].value))
    return bad


plugin_files, proto_files, harness_files = [], [], []
for base, sink in (("plugins", plugin_files), ("protocols", proto_files),
                   ("harness", harness_files)):
    for root, _dirs, files in os.walk(os.path.join(ROOT, base)):
        if "__pycache__" in root:
            continue
        for f in files:
            if f.endswith(".py"):
                sink.append(os.path.join(root, f))

check("★ 插件层不再直连 OneBot 发送端", scan_direct(plugin_files) == [],
      scan_direct(plugin_files))
check("★ 协议层不再直连 OneBot 发送端", scan_direct(proto_files) == [],
      scan_direct(proto_files))
check("★ harness 层不直连（队列自己也不发）", scan_direct(harness_files) == [],
      scan_direct(harness_files))

core = open(os.path.join(ROOT, "bot_core.py"), encoding="utf-8").read()
check("★ bot_core 里没有裸 api_call(\"send_*_msg\")", scan_direct([os.path.join(ROOT, "bot_core.py")]) == [],
      scan_direct([os.path.join(ROOT, "bot_core.py")]))
check("唯一出口 _outbound_low_send 存在且拿到两个端点",
      "def _outbound_low_send(" in core and '"send_group_msg"' in core
      and '"send_private_msg"' in core)
check("主链发送函数都过队列",
      core.count("_ob_send_text(") >= 3 and core.count("_ob_send_msg(") >= 5,
      (core.count("_ob_send_text("), core.count("_ob_send_msg(")))

abi = open(os.path.join(ROOT, "plugins", "abi.py"), encoding="utf-8").read()
check("★ 插件 ABI 的 send_group/send_private 走宿主 send_text",
      'self.send_text(' in abi and abi.count('api_call("send_') == 0)

print(f"\n通过 {len(PASS)} / 失败 {len(FAIL)}")
for n in FAIL:
    print(f"  FAIL  {n}")
sys.exit(1 if FAIL else 0)
