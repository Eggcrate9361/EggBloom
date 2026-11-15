# -*- coding: utf-8 -*-
"""群回复流水线（2026-10-25）：同群不并发生成、在途排队、相位释放、唯一入站。

## 钉住的行为

1. 同一群任一时刻只有一轮 `_pipeline_step` 在跑（两个线程同时调也只有一个进）。
2. 在途期间到达的调用返回 `False` 并置 `pending`；本轮结束后续跑**恰好一次**。
3. `_pipeline_step` 抛异常时相位回到 `idle`，该群还能继续回。
4. 相位持有超过 `REPLY_PHASE_MAX` 视为卡死，下一个调用强制接管。
5. `_decide` 在 `forced=True` 时不调决策插件，直接回 `reply` / `reason="触发"`。
6. `_decide` 在 `act=observe` 时只回结论，不写 `GROUP_OBSERVE`（副作用留给调用点）。
7. `_observe_second_look` 在相位非 `idle` 时不覆盖待批数据。
8. `_pipeline_upsert` 的合并 / 过期 / 新建三条分支；deadline 仍取 `_batch_deadline`。
9. 过期批次已被在途线程 pop 走时，新事件新建批次，不写进已摘下的字典。

运行：python tests/test_group_pipeline.py
"""
import os
import re
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from _sandbox import sandbox_neurobase  # noqa: E402

sandbox_neurobase()

import bot_core  # noqa: E402

GID = "_test_group_pipeline_"
_fail = []


def check(name, ok, detail=""):
    if ok:
        print(f"  [OK]   {name}")
    else:
        print(f"  [FAIL] {name}  {detail}")
        _fail.append(name)


class _FakePM:
    """只回答主链会问的那几个问题；顺带记录被调用次数。"""

    def __init__(self, results):
        self.results = list(results)
        self.calls = 0

    def has_hook(self, hook_name):
        return hook_name == "before_reply"

    def is_loaded(self, name):
        return True

    def is_enabled(self, name):
        return True

    def get_meta(self, name):
        return {"priority": 5, "name": name}

    def execute_hooks(self, hook_name, **kwargs):
        self.calls += 1
        if hook_name != "before_reply":
            return []
        return [(n, dict(r)) for n, r in self.results]


def _reset():
    bot_core.GROUP_MSG_BUFFER.pop(GID, None)
    bot_core.GROUP_OBSERVE.pop(GID, None)
    bot_core.GROUP_PIPELINE.pop(GID, None)


def _timer_of(gid=GID):
    buf = bot_core.GROUP_MSG_BUFFER.get(gid) or {}
    return buf.get("timer")


def _cancel(buf):
    t = (buf or {}).get("timer")
    if t:
        try:
            t.cancel()
        except Exception:
            pass


def _deadline_of(gid=GID):
    return (bot_core.GROUP_MSG_BUFFER.get(gid) or {}).get("deadline")


def test_concurrent_and_pending():
    print("\n[1/2] 两个线程同时进 → 只跑一轮；在途那次排队，结束后正好再跑一次")
    _reset()
    entered = threading.Event()
    release = threading.Event()
    lock = threading.Lock()
    seen = {"n": 0}
    orig_step = bot_core._pipeline_step

    def fake_step(gid):
        with lock:
            seen["n"] += 1
        entered.set()
        release.wait(3)

    bot_core._pipeline_step = fake_step
    try:
        t = threading.Thread(target=bot_core.process_buffered_message, args=(GID,))
        t.start()
        entered.wait(2)
        second = bot_core.process_buffered_message(GID)
        with bot_core._pipeline_lock:
            pending = bool(bot_core.GROUP_PIPELINE[GID]["pending"])
        check("在途时第二次调用返回 False", second is False, second)
        check("在途时 pending 置真", pending, pending)
        check("此刻只进了一轮", seen["n"] == 1, seen["n"])
        release.set()
        t.join(3)
        with bot_core._pipeline_lock:
            phase = bot_core.GROUP_PIPELINE[GID]["phase"]
        check("本轮结束后恰好续跑一次（共 2 轮）", seen["n"] == 2, seen["n"])
        check("之后相位回到 idle", phase == "idle", phase)
    finally:
        bot_core._pipeline_step = orig_step
        _reset()


def test_exception_releases_phase():
    print("\n[3] 生成抛异常 → 相位回到 idle，该群还能继续回")
    _reset()
    orig_step = bot_core._pipeline_step

    def boom(gid):
        raise RuntimeError("模拟生成异常")

    bot_core._pipeline_step = boom
    raised = False
    try:
        try:
            bot_core.process_buffered_message(GID)
        except RuntimeError:
            raised = True
        check("异常照常上抛（不吞）", raised)
        with bot_core._pipeline_lock:
            phase = bot_core.GROUP_PIPELINE[GID]["phase"]
        check("异常后相位是 idle", phase == "idle", phase)

        seen = {"n": 0}

        def ok_step(gid):
            seen["n"] += 1

        bot_core._pipeline_step = ok_step
        check("同一群还能再跑一轮", bot_core.process_buffered_message(GID) is True)
        check("确实跑了", seen["n"] == 1, seen["n"])
    finally:
        bot_core._pipeline_step = orig_step
        _reset()


def test_phase_timeout_takeover():
    print("\n[4] 相位超过 REPLY_PHASE_MAX → 强制接管")
    _reset()
    orig_step = bot_core._pipeline_step
    seen = {"n": 0}

    def ok_step(gid):
        seen["n"] += 1

    bot_core._pipeline_step = ok_step
    try:
        with bot_core._pipeline_lock:
            bot_core.GROUP_PIPELINE[GID] = {
                "phase": "generating",
                "since": time.time() - bot_core.REPLY_PHASE_MAX - 5,
                "pending": False,
                "last_reply_at": 0.0,
            }
        ran = bot_core.process_buffered_message(GID)
        check("卡死相位被接管并跑起来", ran is True, ran)
        check("ran 了一次", seen["n"] == 1, seen["n"])
        with bot_core._pipeline_lock:
            phase = bot_core.GROUP_PIPELINE[GID]["phase"]
        check("接管后正常收尾", phase == "idle", phase)
    finally:
        bot_core._pipeline_step = orig_step
        _reset()


def test_decide_forced_shortcut():
    print("\n[5] _decide：forced=True 不调决策插件，直接 reply")
    _reset()
    orig_pm, orig_ok = bot_core.get_plugin_manager, bot_core.PLUGIN_OK
    fake = _FakePM([("group_chat_decider", {"action": "skip", "reason": "不该接"})])
    bot_core.get_plugin_manager = lambda: fake
    bot_core.PLUGIN_OK = True
    try:
        r = bot_core._decide(GID, {"text": "[10001] 小a 在吗", "forced": True})
        check("act=reply", r.get("act") == "reply", r)
        check("reason=触发", r.get("reason") == "触发", r)
        check("没调插件", fake.calls == 0, fake.calls)
    finally:
        bot_core.get_plugin_manager, bot_core.PLUGIN_OK = orig_pm, orig_ok
        _reset()


def test_decide_observe_is_pure():
    print("\n[6] _decide：observe 只回结论，不写 GROUP_OBSERVE")
    _reset()
    orig_pm, orig_ok = bot_core.get_plugin_manager, bot_core.PLUGIN_OK
    orig_typed = bot_core._dispatch_typed_before_reply
    fake = _FakePM([("group_chat_decider",
                     {"action": "observe", "after": 25, "reason": "这会儿别接"})])
    bot_core.get_plugin_manager = lambda: fake
    bot_core.PLUGIN_OK = True
    bot_core._dispatch_typed_before_reply = lambda *a, **kw: []
    try:
        r = bot_core._decide(GID, {"text": "[10001] 在干嘛", "forced": False})
        check("act=observe", r.get("act") == "observe", r)
        check("after=25", r.get("after") == 25, r)
        check("理由带出来", "别接" in str(r.get("reason") or ""), r.get("reason"))
        check("_decide 自己没写观察窗口", GID not in bot_core.GROUP_OBSERVE)
    finally:
        bot_core.get_plugin_manager, bot_core.PLUGIN_OK = orig_pm, orig_ok
        bot_core._dispatch_typed_before_reply = orig_typed
        _reset()


def test_observe_second_look_phase_guard():
    print("\n[7] 观察回看：相位非 idle 时不覆盖待批数据")
    _reset()
    with bot_core._pipeline_lock:
        bot_core.GROUP_PIPELINE[GID] = {
            "phase": "generating", "since": time.time(),
            "pending": False, "last_reply_at": 0.0,
        }
    bot_core.GROUP_OBSERVE[GID] = {"text": "[10001] 回看这句话", "at": time.time(),
                                   "timer": None, "reason": "测试"}
    try:
        bot_core._observe_second_look(GID)
        check("没写 GROUP_MSG_BUFFER", GID not in bot_core.GROUP_MSG_BUFFER,
              bot_core.GROUP_MSG_BUFFER.get(GID))
        check("观察记录已消费", GID not in bot_core.GROUP_OBSERVE)
    finally:
        _reset()


def test_upsert_branches():
    print("\n[8] _pipeline_upsert：新建 / 合并 / 过期三条分支")
    _reset()
    try:
        r1 = bot_core._pipeline_upsert(
            GID, sender="10001", text="第一句",
            flags={"at_bot": True, "quote_me": True}, forced=True)
        buf = bot_core.GROUP_MSG_BUFFER.get(GID) or {}
        check("新建返回 created", r1 == "created", r1)
        check("文本带说话人标签",
              re.search(r"\[10001·[0-9a-z]{4}\] 第一句", str(buf.get("text"))),
              buf.get("text"))
        check("forced 写进批次", buf.get("forced") is True, buf.get("forced"))
        expect = bot_core._batch_deadline(
            now=buf.get("time"), first_time=buf.get("time"), same_sender=False,
            in_session=False, triggered=True, has_context=False,
            has_bare_image=False)
        check("deadline 来自 _batch_deadline",
              abs(float(buf.get("deadline") or 0) - expect) < 1e-6,
              (buf.get("deadline"), expect))
        check("批次带 Timer", _timer_of() is not None)

        r2 = bot_core._pipeline_upsert(
            GID, sender="10001", text="第二句", flags={}, forced=False)
        buf2 = bot_core.GROUP_MSG_BUFFER.get(GID) or {}
        check("窗口内返回 merged", r2 == "merged", r2)
        # 1.5.9 §B1.1：同人续话现在也带前缀（原来不带，模型会把续句算成上一个人说的）
        _t2 = str(buf2.get("text"))
        check("同人续话也各自带前缀",
              re.search(r"\[10001·[0-9a-z]{4}\] 第一句\n\[10001·[0-9a-z]{4}\] 第二句", _t2)
              is not None, _t2)
        check("forced 合并取或（不回落）", buf2.get("forced") is True, buf2.get("forced"))

        buf2["deadline"] = time.time() - 1
        orig_step = bot_core._pipeline_step
        bot_core._pipeline_step = lambda gid: None   # 结算时不真跑模型
        try:
            r3 = bot_core._pipeline_upsert(
                GID, sender="10002", text="第三句", flags={}, forced=False)
        finally:
            bot_core._pipeline_step = orig_step
        buf3 = bot_core.GROUP_MSG_BUFFER.get(GID) or {}
        check("过期后返回 expired", r3 == "expired", r3)
        check("换人加标签再合并",
              re.search(r"\[10002·[0-9a-z]{4}\] 第三句", str(buf3.get("text"))),
              buf3.get("text"))
        check("过期后 deadline 被推后", float(buf3.get("deadline") or 0) > time.time())
    finally:
        _cancel(bot_core.GROUP_MSG_BUFFER.get(GID))
        _reset()


def test_upsert_expired_in_flight():
    print("\n[9] 过期批次已被在途线程 pop 走 → 新建批次，不写已摘下的字典")
    _reset()
    old = {
        "text": "[10001] 旧批次", "sender": "10001", "time": time.time() - 30,
        "deadline": time.time() - 20, "first_time": time.time() - 30,
        "is_at_bot": False, "has_xiao_a": False, "has_bot_nick": False,
        "has_quote": False, "quote_me": False, "has_text_at": False,
        "in_session": False, "has_context": False, "images": [],
        "timer": None, "merged_n": 0, "forced": False, "poke_uid": None,
    }
    bot_core.GROUP_MSG_BUFFER[GID] = old
    orig_pbm = bot_core.process_buffered_message

    def settle_and_drop(gid):
        # 模拟在途线程：pop 走旧批次后开始生成
        with bot_core._buffer_lock:
            bot_core.GROUP_MSG_BUFFER.pop(gid, None)
        return True

    bot_core.process_buffered_message = settle_and_drop
    try:
        r = bot_core._pipeline_upsert(GID, sender="10002", text="新消息", flags={})
        new = bot_core.GROUP_MSG_BUFFER.get(GID) or {}
        check("返回 created（旧批已不在）", r == "created", r)
        check("派生的新批次只有新消息",
              re.search(r"\[10002·[0-9a-z]{4}\] 新消息", str(new.get("text"))),
              new.get("text"))
        check("没有写进已摘下的旧字典",
              "新消息" not in str(old.get("text")), old.get("text"))
    finally:
        bot_core.process_buffered_message = orig_pbm
        _cancel(bot_core.GROUP_MSG_BUFFER.get(GID))
        _reset()


def main():
    test_concurrent_and_pending()
    test_exception_releases_phase()
    test_phase_timeout_takeover()
    test_decide_forced_shortcut()
    test_decide_observe_is_pure()
    test_observe_second_look_phase_guard()
    test_upsert_branches()
    test_upsert_expired_in_flight()

    print("\n" + "=" * 56)
    if _fail:
        print(f"失败 {len(_fail)}：{_fail}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
