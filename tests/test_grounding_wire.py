# -*- coding: utf-8 -*-
"""tests/test_grounding_wire.py —— 事实围栏接在主回复上（2026-11-04）

围栏本身在 `test_grounding.py`（27 条）。这里只管**接线**：
私聊与群聊的回复是不是真的过了一遍，以及三种模式（off/log/drop）行为对不对。

现实依据：今天四次编造里三次出在主回复，只接主动说话不够。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from _sandbox import sandbox_neurobase  # noqa: E402

sandbox_neurobase()

import bot_core as bc  # noqa: E402

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


def main():
    old = bc._config.get("grounding_guard")
    ev = ["上课呢高数", "讲微分呢", "不跟你说这个"]

    try:
        print("[A] drop 模式：删掉无据的句子")
        bc._config["grounding_guard"] = "drop"
        out = bc._apply_grounding_guard("刚说别找你，这会儿又听不到了", ev, where="测")
        check("★ 捏造句被删空", out == "", out)

        out2 = bc._apply_grounding_guard("行，闭嘴\n我听到你那边有声响", ev, where="测")
        check("★ 只删无据那句", out2.strip() == "行，闭嘴", out2)

        out3 = bc._apply_grounding_guard("微分啊，dy 跟 dx 一除就完事", ev, where="测")
        check("★ 正常回复一字不动", out3 == "微分啊，dy 跟 dx 一除就完事", out3)

        print("\n[B] log 模式：只记日志，文本不变")
        bc._config["grounding_guard"] = "log"
        src = "刚说别找你"
        out4 = bc._apply_grounding_guard(src, ev, where="测")
        check("★ 文本原样返回", out4 == src, out4)

        print("\n[C] off 模式：不做")
        bc._config["grounding_guard"] = "off"
        out5 = bc._apply_grounding_guard("我听到你那边有声响", ev, where="测")
        check("★ 关掉后不删", out5 == "我听到你那边有声响", out5)

        print("\n[D] 异常不影响发送")
        bc._config["grounding_guard"] = "drop"
        out6 = bc._apply_grounding_guard("随便一句话", object(), where="测")
        check("★ 证据是怪对象也不抛", isinstance(out6, str), type(out6))

        print("\n[E] 接线：私聊与群聊都调了")
        srcf = open(os.path.join(ROOT, "bot_core.py"), encoding="utf-8", errors="replace").read()
        # 2026-11-04：接线收敛成 _guard_outbound 之后，_apply_grounding_guard
        # 只剩定义 + 统一入口里那一次调用；三处业务（私聊/群聊/插件）都走 _guard_outbound
        n = srcf.count("_apply_grounding_guard(")
        check("定义 + 统一入口里各一次", n >= 1, n)
        n2 = srcf.count("_guard_outbound(")
        check("★ 至少 3 处走统一出口", n2 >= 3, n2)
        check("私聊在 smart_send 之前",
              srcf.find("_apply_grounding_guard(sent_text, _ev_p") < srcf.find("ok = smart_send(sent_text, sender)"))
        check("群聊在去重闸之前",
              srcf.find("_apply_grounding_guard(sent_text, _ev_g") < srcf.find("def _rough(s):"))
        check("★ 私聊删空退「嗯」", "整条无据，退成「嗯」" in srcf)
        check("★ 群聊删空退「嗯」", "整条无据，退成「嗯」" in srcf)
        # 2026-11-04：证据拼装搬进 _guard_outbound（按 scene 取 history）
        check("★ 统一出口按场景取 history",
              "group_chat_history" in srcf and "chat_history" in srcf)

        print("\n[F] 默认是 drop（不是关着）")
        bc._config.pop("grounding_guard", None)
        out7 = bc._apply_grounding_guard("我听到你那边有声响", ev, where="测")
        check("★ 缺配置时默认拦截", out7 == "", out7)

        print("\n[G] 时长判据的尺子：统一出口从 history 的 ts 算")
        check("★ 出口里读了 h['ts']", 'h.get("ts")' in srcf)
        check("★ 出口把 now/since 传下去了",
              "now=_now" in srcf and "since=_since" in srcf)
        check("★ 只看最近 24 小时（不然永远不触发）", "24 * 3600" in srcf)

        # 真机形态：私聊 6 小时前开始，「都十五个小时了」应当被删空
        import time as _t
        _hist_bak = getattr(bc, "chat_history", None)
        try:
            bc.chat_history = {"t_e2e": [
                {"role": "user", "content": "在吗", "ts": _t.time() - 6 * 3600},
                {"role": "assistant", "content": "在", "ts": _t.time() - 5.9 * 3600},
            ]}
            bc._config["grounding_guard"] = "drop"
            out8 = bc._guard_outbound("都十五个小时了", scene="private", target="t_e2e",
                                      where="测")
            check("★ 6 小时说成 15 小时 → 删空", out8 == "", out8)
            out9 = bc._guard_outbound("聊了一个小时，困", scene="private", target="t_e2e",
                                      where="测")
            check("★ 说 1 小时不误杀", out9 == "聊了一个小时，困", out9)
        finally:
            if _hist_bak is None:
                try:
                    del bc.chat_history
                except Exception:
                    pass
            else:
                bc.chat_history = _hist_bak

    finally:
        if old is None:
            bc._config.pop("grounding_guard", None)
        else:
            bc._config["grounding_guard"] = old

    print("\n" + "=" * 60)
    print(f"通过 {PASS} / 失败 {FAIL}")
    if FAILURES:
        print("失败项：")
        for x in FAILURES:
            print("  -", x)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
