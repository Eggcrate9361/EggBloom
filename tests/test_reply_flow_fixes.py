# -*- coding: utf-8 -*-
"""2026-11-05 真机三连话病的回归用例。

同一次群聊里撞出来的三件事：
  · 同人两句隔 5 秒 → 连着回两次（批次 3s 定稿，生成期无回填通道）
  · 表态回合被 A 档「1 条 ≤8 字」挤成「啧，必，强度高还好看」
  · 摘要支线漏了 User-Agent / x-opencode-session → 每轮 403，且测试文案写真日志

判据分行为与静态两层：行为测真实函数，静态钉住"别再改回两处拼 headers"。
"""
import io
import os
import sys
import tempfile
import time
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from _sandbox import sandbox_neurobase  # noqa: E402

sandbox_neurobase()

import reply_budget as rb  # noqa: E402
import bot_core  # noqa: E402

# 本用例会让 _absorb_late_supplements 写日志 → 隔离到临时文件，不碰真日志
_tmp = tempfile.NamedTemporaryFile(prefix="eggbloom_flow_", suffix=".log", delete=False)
_tmp.close()
bot_core.LOG_FILE = _tmp.name

PASS = 0
FAIL = 0
FAILURES = []
GID = "testgroup_flow"


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK]   {name}")
    else:
        FAIL += 1
        FAILURES.append(name + (f"  → {extra}" if extra else ""))
        print(f"  [FAIL] {name}  {extra}")


def _reset_buf(**kw):
    now = time.time()
    timer = mock.Mock()
    base = {
        "text": "[老大·8zff] 新约能天使",
        "sender": "3150007406",
        "first_time": now,
        "timer": timer,
        "is_at_bot": False, "has_xiao_a": False, "has_bot_nick": False,
        "has_quote": False, "forced": False,
    }
    base.update(kw)
    with bot_core._buffer_lock:
        bot_core.GROUP_MSG_BUFFER[GID] = base
    bot_core.GROUP_PIPELINE[GID] = {
        "phase": "generating", "since": now - 3,
        "pending": True, "last_reply_at": 0.0}
    return base, timer, now


def _cleanup():
    with bot_core._buffer_lock:
        bot_core.GROUP_MSG_BUFFER.pop(GID, None)
    bot_core.GROUP_PIPELINE.pop(GID, None)


def main():
    print("[A] 表态抬档：只抬该表态的，短附和原样保留")
    a = rb.pick(0.0)
    check("★ A 档仍是 1 条 ≤8 字（活人附和没被全局拉长）",
          a["name"] == "A" and a["max_chars"] == 8, a)
    up = rb.ensure_statement_floor(a, True)
    check("★ 表态回合抬到 B（≤16 字 / 1–2 条）",
          up["name"] == "B" and up["max_chars"] == 16 and up["parts_max"] == 2, up)
    check("★ 入参不被就地改写", a["max_chars"] == 8, a)
    check("★ 抬档后仍然禁问（防「你觉得呢」式反问）",
          "不要问" in rb.prompt_line(up), rb.prompt_line(up))
    check("★ 抬档后仍禁解释/总结（反 AI 味约束没丢）",
          "不要解释" in rb.prompt_line(up) and "不要总结" in rb.prompt_line(up))
    check("★ 闲聊不抬档", rb.ensure_statement_floor(a, False)["max_chars"] == 8)
    check("★ B/C 档不动", rb.ensure_statement_floor(rb.pick(0.7), True)["max_chars"] == 16)
    check("★ A 档话术仍是 8 字（没全局改）", "8 个字" in rb.prompt_line(rb.pick(0.0)))

    print("\n[B] 生成前吸收同人补充")
    base, timer, now = _reset_buf()
    extra, n = bot_core._absorb_late_supplements(
        GID, "[老大·8zff] 你觉得他要不要整一个新能", now - 3, "3150007406")
    check("★ 吸收到 1 条补充", n == 1 and "新约能天使" in extra, (n, extra))
    check("★ 缓冲区被消费（不会另起一轮）", GID not in bot_core.GROUP_MSG_BUFFER)
    check("★ Timer 被取消（否则 release 还会续跑）", timer.cancel.called)
    check("★ pending 被清（否则 sending 后必发第二条）",
          bot_core.GROUP_PIPELINE[GID]["pending"] is False)
    _cleanup()

    cases = [
        ("换人是插话，不吞", {"sender": "999999"}),
        ("新 @ 是新话题，不吞", {"is_at_bot": True}),
        ("旧批次不动", {"first_time": now - 999}),
        ("长文本不算补充", {"text": "x" * 200}),
    ]
    for name, kw in cases:
        _reset_buf(**kw)
        extra2, n2 = bot_core._absorb_late_supplements(
            GID, "原批", now - 3, "3150007406")
        check("★ " + name, n2 == 0 and GID in bot_core.GROUP_MSG_BUFFER, (n2, extra2[:40]))
        _cleanup()

    print("\n[C] 接线与隔离（静态）")
    src = io.open(os.path.join(ROOT, "bot_core.py"), encoding="utf-8").read()
    i = src.find("def _absorb_late_supplements")
    check("★ 生成前真的调了吸收函数", "_absorb_late_supplements(" in src
          and src.count("_absorb_late_supplements(") >= 2)
    check("★ 抬档真的接在 pick 之后", "ensure_statement_floor(_budget, _need_statement)" in src)
    j = src.find("def _summarize_group_history_inner")
    inner = src[j:src.find("\ndef ", j + 10)] if j >= 0 else ""
    check("★ 摘要支线用统一请求头", "_llm_request_headers(" in inner)
    tc = io.open(os.path.join(HERE, "test_compress_txn.py"), encoding="utf-8").read()
    check("★ 压缩用例隔离了 LOG_FILE", "bot_core.LOG_FILE = self._tmp_log.name" in tc)
    runner = io.open(os.path.join(ROOT, "run_all_tests.py"), encoding="utf-8").read()
    check("★ 运行器对所有测试进程注入日志文件",
          'EGGBLOOM_LOG_FILE=os.path.join(tmp, "qq_bot.log")' in runner
          and 'EGGBLOOM_TEST_MODE="1"' in runner)
    check("★ 重定向有测试模式闸门（生产设了同名变量也不受影响）",
          'os.environ.get("EGGBLOOM_TEST_MODE") == "1"' in src)

    _cleanup()
    try:
        os.remove(_tmp.name)
    except OSError:
        pass
    print("\n" + "=" * 60)
    print(f"通过 {PASS} / 失败 {FAIL}")
    if FAILURES:
        print("失败项：")
        for x in FAILURES:
            print("  -", x)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
