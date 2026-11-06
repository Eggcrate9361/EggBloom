# -*- coding: utf-8 -*-
"""tests/test_self_actions_wiring.py —— 行为账本要覆盖"真正发出去的东西"（2026-11-06）

## 用户原话

「bot 不知道自己干了什么说了什么」—— 真机 08:02：它主动发了两条，被引用后回
「我发过这句？别往我头上安」。查下来有两层：

  ① prompt 里「你上一条说的」只取了**一条**（已修，见 test_self_consistency）；
  ② **行为账本 `self_actions` 里根本没有这条** —— 它只有两个写入点：
     私聊/群聊的**回复侧** + `meme_save`。插件发出去的话、发出去的文件，
     一条都没记。所以"我做过什么"这个块永远是空的，问它只能否认。

这一条钉的是 ②：记录点必须放在**唯一的出口**上，而不是每个调用方各自记得记。

判据：
  [A] 插件发送 → 账本里有 say（私聊/群聊的目标各自正确）
  [B] 发送失败 → 不记（不能把没发生的事写进账本）
  [C] 发文件成功 → 账本里有 file，且带文件名
  [D] 发文件失败 → 不记
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from _sandbox import sandbox_neurobase  # noqa: E402

sandbox_neurobase()

import bot_core as bc  # noqa: E402
import self_actions as sa  # noqa: E402

PASS = 0
FAIL = 0
FAILURES = []
CALLS = []


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK]   {name}")
    else:
        FAIL += 1
        FAILURES.append(name + (f"  → {extra}" if extra else ""))
        print(f"  [FAIL] {name}  {extra}")


def main():
    global CALLS
    orig_record = sa.record
    orig_smart = bc.smart_send
    orig_smart_g = bc.smart_send_group
    orig_evt = getattr(bc, "save_event_log", None)
    orig_guard = getattr(bc, "_guard_outbound", None)
    orig_block = bc._send_file_blocking
    sent_state = {"ok": True}
    try:
        sa.record = lambda kind, **kw: (CALLS.append((kind, kw)), {"kind": kind})[1]
        bc.save_event_log = lambda *a, **k: None
        bc._guard_outbound = lambda text, **kw: text
        bc.smart_send = lambda text, target=None: sent_state["ok"]
        bc.smart_send_group = lambda gid, text: sent_state["ok"]

        print("=== A) 插件发送进账本 ===")
        CALLS = []
        bc._plugin_send_text("醒得挺早啊你", target="2011257385")
        kinds = [k for k, _ in CALLS]
        check("记了一条", len(CALLS) == 1, CALLS)
        check("kind=say", kinds[:1] == ["say"], kinds)
        kw = CALLS[0][1] if CALLS else {}
        check("scene=private", kw.get("scene") == "private", kw)
        check("target 是对方 QQ", kw.get("target") == "2011257385", kw)
        check("detail 带原话", "醒得挺早" in str(kw.get("detail") or ""), kw)

        CALLS = []
        bc._plugin_send_text("群里那句", group_id="829854136")
        kw2 = CALLS[0][1] if CALLS else {}
        check("群聊 scene=group", kw2.get("scene") == "group", kw2)
        check("群聊 target 是群号", kw2.get("target") == "829854136", kw2)

        print("\n=== B) 发送失败不记 ===")
        CALLS = []
        sent_state["ok"] = False
        r = bc._plugin_send_text("发不出去的话", target="2011257385")
        check("返回 False", r is False, r)
        check("账本里没有这条", CALLS == [], CALLS)
        sent_state["ok"] = True

        print("\n=== C) 发文件成功进账本 ===")
        td = tempfile.mkdtemp(prefix="sa_file_")
        fp = os.path.join(td, "报告.zip")
        open(fp, "wb").write(b"x" * 16)
        bc._send_file_blocking = lambda path, target: ("ok", "ok")
        CALLS = []
        ok = bc.send_file(fp, "2011257385")
        check("返回 True", ok is True, ok)
        kinds = [k for k, _ in CALLS]
        check("kind=file", kinds[:1] == ["file"], kinds)
        kw3 = CALLS[0][1] if CALLS else {}
        check("detail 是文件名", kw3.get("detail") == "报告.zip", kw3)
        check("target 是对方", kw3.get("target") == "2011257385", kw3)

        print("\n=== D) 发文件失败不记 ===")
        bc._send_file_blocking = lambda path, target: ("fail", "拒绝了")
        CALLS = []
        ok = bc.send_file(fp, "2011257385")
        check("返回 False", ok is False, ok)
        check("账本里没有这条", CALLS == [], CALLS)
    finally:
        sa.record = orig_record
        bc.smart_send = orig_smart
        bc.smart_send_group = orig_smart_g
        if orig_evt is not None:
            bc.save_event_log = orig_evt
        if orig_guard is not None:
            bc._guard_outbound = orig_guard
        bc._send_file_blocking = orig_block

    print("\n" + "=" * 56)
    print(f"通过 {PASS} / 失败 {FAIL}")
    for n in FAILURES:
        print(f"  FAIL  {n}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
