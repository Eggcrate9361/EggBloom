# -*- coding: utf-8 -*-
"""tests/test_length_retry.py —— 思维链吃光输出预算 → 加倍重试一次

线上实证（2026-10-13 23:41 私聊）：usage total=5547 = prompt 4747 +
completion **恰好 800** —— deepseek-flash 的 reasoning_content 把
AI_MAX_TOKENS=800 的输出预算全吃光，content 为空 → 用户收到
"收到消息了，但暂时无法处理"。代码里的推理模型豁免名单
（r1/v4/qwq/think/reason）不含 flash —— 靠名字猜不全，改成认现象：
content 空 + finish_reason=length → 预算翻倍重试（封顶 2400，只一次）。
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


def _run_with_stub(script):
    """script: 每次 _post_chat 调用返回一个响应 dict；记录 max_tokens 序列。"""
    calls = []
    seq = iter(script)

    def fake_post(api_url, payload, api_key="", timeout=60):
        calls.append(payload["max_tokens"])
        return next(seq)

    orig_post, orig_cfg = bot_core._post_chat, bot_core._llm_config
    orig_ts = None
    try:
        import token_stats
        orig_ts = token_stats.record
        token_stats.record = lambda *a, **k: None
    except Exception:
        pass
    bot_core._post_chat = fake_post
    bot_core._llm_config = lambda *a, **k: {
        "model_name": "deepseek-flash", "api_url": "x", "api_key": "y",
        "provider_type": "openai"}
    try:
        r = bot_core._call_deepseek_sync("你好", target_qq="10003",
                                         is_group=False)
    finally:
        bot_core._post_chat = orig_post
        bot_core._llm_config = orig_cfg
        try:
            import token_stats
            if orig_ts:
                token_stats.record = orig_ts
        except Exception:
            pass
    return r, calls


def _resp(content, finish="stop", completion=50):
    return {"choices": [{"message": {"content": content},
                         "finish_reason": finish}],
            "usage": {"total_tokens": 100, "prompt_tokens": 60,
                      "completion_tokens": completion}}


def main():
    print("\n=== ① 事故复现：空 content + length → 加倍重试后拿到回复 ===")
    r, calls = _run_with_stub([
        _resp("", finish="length", completion=800),
        _resp("这次正常回复了"),
    ])
    check("★ 最终拿到非空回复", r == "这次正常回复了", repr(r))
    check("★ 预算 800 → 1600 翻倍重试", calls == [800, 1600], str(calls))

    print("\n=== ② 重试只有一次：第二次仍 length 截断 → 不再无限加倍 ===")
    r2, calls2 = _run_with_stub([
        _resp("", finish="length"),
        _resp("", finish="length"),
    ])
    check("★ 只重试一次（共 2 次调用）", len(calls2) == 2, str(calls2))
    check("最终仍空 → 返回空串（上层走兜底文案）", not r2, repr(r2))

    print("\n=== ③ 不误伤：正常回复 / stop 结尾不触发重试 ===")
    r3, calls3 = _run_with_stub([_resp("正常一句话")])
    check("正常回复单次调用", calls3 == [800] and r3 == "正常一句话", str(calls3))
    # 空 content 但 finish=stop（模型真想说空话）→ 不重试
    r4, calls4 = _run_with_stub([_resp("", finish="stop")])
    check("空 content + stop → 不重试（尊重模型）", calls4 == [800], str(calls4))

    print("\n=== ④ 封顶：预算基数大时翻倍也封顶 2400 ===")
    orig_ai, orig_pm = bot_core.AI_MAX_TOKENS, bot_core._config.get("ai_private_max_tokens")
    bot_core.AI_MAX_TOKENS = 2000
    bot_core._config["ai_private_max_tokens"] = 2000
    try:
        r5, calls5 = _run_with_stub([
            _resp("", finish="length"),
            _resp("回来了"),
        ])
        check("★ 重试预算封顶 2400", calls5 == [2000, 2400], str(calls5))
    finally:
        bot_core.AI_MAX_TOKENS = orig_ai
        if orig_pm is not None:
            bot_core._config["ai_private_max_tokens"] = orig_pm

    print("\n" + "=" * 56)
    print(f"通过 {PASS} / 失败 {FAIL}")
    if FAILURES:
        print("失败项：")
        for f in FAILURES:
            print("  -", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
