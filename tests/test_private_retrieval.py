# -*- coding: utf-8 -*-
"""tests/test_private_retrieval.py —— 私聊检索层单独收口（2026-11-06）

## 为什么单开这一刀

上一刀把 history 窗口放宽到 32 条，但**超过窗口**的旧事靠什么想起来？
查下来是两条路，而且互相打架：

  · `memory_api.recall()`（自称"记忆唯一读口"）的私聊分支走
    `get_memory_prompt_block(light=True)` —— **light=True 直接跳过检索层**，
    只剩一行元信息。旧注释还写着「沙漏里没有私聊数据」，但沙漏一直在写私聊
    用户行 `[昵称(uid)] 正文`，`_scope_match(("private", uid))` 也一直支持。
  · bot_core 私聊路径另起炉灶，自己调 `memory_context.build_block`（builder
    第一步就做日期召回），又调一次旧层（也做日期召回）→ **同源双注**。

结果：唯一读口没在检索，真正干活的那个没预算、没日志、还注两遍。

## 现在

  私聊检索 → memory_api._retrieval → memory_context.build_block(
      scope="private", scope_id=uid, sources=("sand","sm"))
  bot_core 的 pm_ctx 只留画像/元信息（skip_search + skip_date）
  开关 private_memory_retrieval_enable（默认开）

判据：
  [A] 私聊走 builder，且 scope / scope_id / sources 正确（不串别人）
  [B] 开关关掉 → 零检索，builder 一次都不调
  [C] builder 抛异常 → 返回 None，不编
  [D] recall() 把检索计入并打 `[记忆] ... 检索` 日志
  [E] skip_date=True 真的不调日期召回，skip_search=True 不调关键词搜索
  [F] bot_core 源码里不再自己调 builder（防双注回退）
  [G] 端到端：检索到的旧事在 prompt 里**只出现一次**
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from _sandbox import sandbox_neurobase  # noqa: E402

sandbox_neurobase()

from harness import memory_api as ma  # noqa: E402
from harness import memory_context as mc  # noqa: E402
import bot_core as bc  # noqa: E402
import config as cfg  # noqa: E402
import sandglass_bridge as sb  # noqa: E402

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
    orig_build = mc.build_block
    orig_cfg = dict(cfg.load_config())
    calls = []

    try:
        print("=== A) 私聊走 builder，参数不许串人 ===")
        mc.build_block = lambda msg, **kw: (
            calls.append((msg, kw)), "【检索到的旧事】\n- (0.82) 上周你说想去上海")[1]
        rt = ma._retrieval("上周我说要去哪", "private", "", True, uid="2011257385")
        check("返回 (检索, 块)", bool(rt) and rt[0] == "检索", rt)
        check("builder 被调了一次", len(calls) == 1, calls)
        kw = calls[0][1] if calls else {}
        check("scope=private", kw.get("scope") == "private", kw)
        check("scope_id=对方 uid", kw.get("scope_id") == "2011257385", kw)
        check("两个源都开", set(kw.get("sources") or ()) == {"sand", "sm"}, kw)
        check("query 是本轮原话", calls[0][0] == "上周我说要去哪", calls)

        print("\n=== B) 开关关掉 → 零检索 ===")
        # memory_api._cfg() 走 config.load_config()，直接打桩它
        orig_ma_cfg = ma._cfg
        ma._cfg = lambda: {"private_memory_retrieval_enable": False}
        calls.clear()
        rt = ma._retrieval("想起点什么", "private", "", True, uid="2011257385")
        check("返回 None", rt is None, rt)
        check("builder 没被调", calls == [], calls)
        ma._cfg = orig_ma_cfg

        print("\n=== C) builder 挂了不编 ===")
        calls.clear()
        mc.build_block = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("挂了"))
        rt = ma._retrieval("想起点什么", "private", "", True, uid="2011257385")
        check("返回 None 而不是抛", rt is None, rt)

        print("\n=== D) recall 把检索计入并留日志 ===")
        mc.build_block = orig_build
        lines = []
        ma._log = lambda m: lines.append(str(m))
        # 只测装填路径：直接打桩四个来源里的检索
        ma._retrieval = lambda *a, **k: ("检索", "【检索到的旧事】\n- (0.82) 上周你说想去上海")
        out = ma.recall("2011257385", scene="private", query="上周我说要去哪",
                        private=True, budget=600)
        check("检索块进了 recall 结果", "上周你说想去上海" in out, out)
        check("日志记了检索字数", any("检索" in x for x in lines), lines)
        import importlib
        importlib.reload(ma)

        print("\n=== E) skip_date 真的不调日期召回 ===")
        hits = []
        orig = (sb._enabled, sb.is_available, sb.recall_by_date,
                sb._search_context_for_message)
        try:
            sb._enabled = True
            sb.is_available = lambda: True
            sb.recall_by_date = lambda *a, **k: (hits.append("date"), "")[1]
            sb._search_context_for_message = lambda *a, **k: (hits.append("search"), "")[1]
            sb.get_memory_prompt_block("我一天前说过什么", light=True,
                                       skip_search=True, skip_date=True,
                                       scope=("private", "2011257385"))
            check("两层都没跑", hits == [], hits)
            sb.get_memory_prompt_block("我一天前说过什么", light=True,
                                       skip_search=True, skip_date=False,
                                       scope=("private", "2011257385"))
            check("skip_date=False 时日期层照跑", "date" in hits, hits)
        finally:
            (sb._enabled, sb.is_available, sb.recall_by_date,
             sb._search_context_for_message) = orig

        print("\n=== F) 防双注：bot_core 不再自己调 builder ===")
        src = open(bc.__file__, encoding="utf-8", errors="replace").read()
        seg = src[src.find("pm_ctx = \"\""):src.find("pm_ctx = \"\"") + 1400]
        check("pm_ctx 段里没有 build_block", "build_block" not in seg, seg[:200])
        check("pm_ctx 用了 skip_date=True", "skip_date=True" in seg, seg[:200])

        print("\n=== G) 端到端：旧事只出现一次 ===")
        cap = {}
        orig_post = bc._post_chat
        saved_hist = dict(bc.chat_history)
        import importlib
        importlib.reload(ma)
        try:
            lines2 = []
            ma._log = lambda m: lines2.append(str(m))
            ma._retrieval = lambda *a, **k: (
                "检索", "【检索到的旧事】\n" + "- (0.82) 上周你说想去上海看展\n" * 12)
            out = ma.recall("2011257385", scene="private", query="上周我说要去哪",
                            private=True)
            check("★ 长检索块没被整段丢掉", "上周你说想去上海看展" in out, out[:80])
            check("日志没有『超预算丢弃：检索』",
                  not any("超预算丢弃" in x and "检索" in x for x in lines2), lines2)
            importlib.reload(ma)
        finally:
            importlib.reload(ma)

        try:
            from harness import memory_api as ma2
            ma2._retrieval = lambda *a, **k: ("检索", "【旧事】上周你说想去上海看展")
            bc._post_chat = lambda url, payload, **k: (
                cap.setdefault("messages", payload.get("messages")),
                {"choices": [{"message": {"content": "（桩）"}}], "usage": {}})[1]
            bc.chat_history.clear()
            bc.chat_history["2011257385"] = []
            bc._call_deepseek_sync("上周我说要去哪", target_qq="2011257385",
                                   sender="2011257385")
        except Exception as e:
            print("  （调用异常不影响抓包）", type(e).__name__)
        finally:
            bc._post_chat = orig_post
            bc.chat_history.clear()
            bc.chat_history.update(saved_hist)
        msgs = cap.get("messages") or []
        n = sum(str(m.get("content") or "").count("上周你说想去上海看展") for m in msgs)
        check("旧事在 prompt 里恰好一次", n == 1, n)
    finally:
        mc.build_block = orig_build
        cfg.load_config()
        import importlib
        importlib.reload(ma)

    print("\n" + "=" * 56)
    print(f"通过 {PASS} / 失败 {FAIL}")
    for x in FAILURES:
        print(f"  FAIL  {x}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
