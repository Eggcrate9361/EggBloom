# -*- coding: utf-8 -*-
"""工具轮收尾：收尾这一步**必须不带工具**

## 真机事故（2026-10-27 17:32，群 123456789）

```
17:32:42 deciding → generating
17:32:46 工具调用: ['search_web', 'search_web']
17:32:48 搜索「明日方舟 2026 周年限定」→ 自家搜索命中
17:32:55 工具调用: ['search_web', 'search_web']
17:33:11 工具调用: ['search_web', 'search_web']
17:33:30 [工具轮次] 达到上限 3 轮，收尾
17:33:41 AI决定不回复: (空)          ← 群里一句话没有
```

三轮搜索**全部命中**，钱也花了，最后一句没有。用户看到的结论是
「所有调用搜索工具的问题都无法回复」。

## 根因

收尾这一下仍然传 `with_tools=tools_for_call` —— 模型在"该说话了"的
时刻还以为自己能继续搜，于是继续吐 tool_call。而 `_stream_chat_lines`
只读 `delta.content`，`delta.tool_calls` 被整个丢掉 → 返回空串。

**"收尾"的语义就是"现在只能说、不能做"。工具留在手里，模型就会继续做。**

## 契约

1. `_finalize_reply` 调用 `_stream_chat_lines` / `_call_chat_sync` 时必须
   `with_tools=False` —— 这是唯一能保证出文本的前提
2. 跑过工具轮（had_tools_rounds>0）时要追加一句收束指令
3. 第一轮仍空 → 用最小指令再要一次（只一次，不自旋）
4. 流式函数看到只有工具增量、没有文本时，日志必须写明
   「模型仍要求调用 …，本轮无文本」而不是让上层误以为模型主动沉默
5. 收尾过程要打 progress —— 否则等待方按"多久没动静"判超时，
   把一条马上要生成的回复砍掉

运行：python tests/test_tool_finalize.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

_FAIL = []


def check(name, ok, detail=""):
    if ok:
        print(f"  [OK]   {name}")
    else:
        print(f"  [FAIL] {name}  {detail}")
        _FAIL.append(name)


def _src():
    import io
    return io.open(os.path.join(ROOT, "bot_core.py"), encoding="utf-8").read()


def _finalize_body():
    """取出 _finalize_reply 的函数体（到下一个顶层 def 为止）。"""
    src = _src()
    i = src.find("def _finalize_reply(")
    if i < 0:
        return ""
    j = src.find("\ndef ", i + 10)
    return src[i:j if j > 0 else len(src)]


# ── ① 收尾必须不带工具（用桩替换，真跑一遍）────────────────────────────

def test_finalize_strips_tools():
    import bot_core

    calls = []

    def fake_stream(messages, runtime_cfg=None, max_tokens=None,
                    with_tools=True, scene="private"):
        calls.append({"with_tools": with_tools, "n_msgs": len(messages),
                      "scene": scene})
        return "查到了，是「将雪投火」那期。"

    old = bot_core._stream_chat_lines
    bot_core._stream_chat_lines = fake_stream
    try:
        out = bot_core._finalize_reply(
            [{"role": "user", "content": "今年明日方舟周年限定是谁"}],
            800, "group", had_tools_rounds=3)
    finally:
        bot_core._stream_chat_lines = old

    check("收尾返回了文本", "将雪投火" in out, repr(out)[:80])
    check("只调用一次（第一轮就出文本）", len(calls) == 1, len(calls))
    check("with_tools 必须是 False", calls and calls[0]["with_tools"] is False,
          calls[0] if calls else None)
    check("scene 透传", calls and calls[0]["scene"] == "group", calls)


def test_finalize_appends_wrapup_hint():
    """跑过工具轮 → 必须多一条收束指令，否则模型会拿空结果硬凑。"""
    import bot_core

    seen = {}

    def fake_stream(messages, runtime_cfg=None, max_tokens=None,
                    with_tools=True, scene="private"):
        seen["messages"] = list(messages)
        return "好"

    old = bot_core._stream_chat_lines
    bot_core._stream_chat_lines = fake_stream
    try:
        bot_core._finalize_reply([{"role": "user", "content": "问"}],
                                 800, "group", had_tools_rounds=2)
        with_hint = list(seen["messages"])
        bot_core._finalize_reply([{"role": "user", "content": "问"}],
                                 800, "group", had_tools_rounds=0)
        no_hint = list(seen["messages"])
    finally:
        bot_core._stream_chat_lines = old

    check("跑过工具轮 → 多出一条消息", len(with_hint) == 2, len(with_hint))
    check("多出的那条是收束指令",
          "收尾" in str(with_hint[-1].get("content", "")),
          with_hint[-1] if with_hint else None)
    check("没跑工具轮 → 不加收束指令", len(no_hint) == 1, len(no_hint))


def test_finalize_retries_with_minimal_instruction():
    """第一轮空 → 用最小指令再要一次；只一次，不要自旋。"""
    import bot_core

    calls = []

    def fake_stream(messages, runtime_cfg=None, max_tokens=None,
                    with_tools=True, scene="private"):
        calls.append(list(messages))
        if len(calls) == 1:
            return ""                 # 第一轮空（思维链或残留指令带偏）
        return "不知道就是不知道"

    old = bot_core._stream_chat_lines
    bot_core._stream_chat_lines = fake_stream
    try:
        out = bot_core._finalize_reply(
            [{"role": "user", "content": "今年明日方舟周年限定是谁"}],
            800, "group", had_tools_rounds=3)
    finally:
        bot_core._stream_chat_lines = old

    check("第二轮救回来了", "不知道" in out, repr(out)[:60])
    check("总共只调两次（不自旋）", len(calls) == 2, len(calls))
    if len(calls) >= 2:
        _second = calls[1]
        check("第二轮消息更短", len(_second) <= 2, len(_second))
        check("第二轮带上了原问题",
              "周年限定" in str(_second[-1].get("content", "")),
              str(_second[-1])[:120])


def test_finalize_never_raises():
    """两轮都炸也不能抛 —— 上层还有兜底，抛了就整条丢。"""
    import bot_core

    def boom(*a, **k):
        raise RuntimeError("上游 502")

    old = bot_core._stream_chat_lines
    old_sync = bot_core._call_chat_sync
    bot_core._stream_chat_lines = boom
    bot_core._call_chat_sync = boom
    try:
        out = bot_core._finalize_reply([{"role": "user", "content": "x"}],
                                       800, "group", had_tools_rounds=1)
    finally:
        bot_core._stream_chat_lines = old
        bot_core._call_chat_sync = old_sync
    check("全炸也返回字符串而不是抛", isinstance(out, str), type(out))


# ── ② 流式函数必须认出工具增量 ────────────────────────────────────────

def test_stream_reports_tool_only():
    """只有工具增量、没有文本 → 日志要说清，不能装作"说完了但是空"。"""
    body = _src()
    i = body.find("def _stream_chat_lines(")
    j = body.find("\ndef ", i + 10)
    seg = body[i:j if j > 0 else i + 6000]
    check("流式解析读到了 tool_calls 增量",
          'delta.get("tool_calls")' in seg, "没读 tool_calls")
    check("空文本 + 有工具时打点", "[流式收尾]" in seg)
    check("日志里写明不是主动沉默",
          "不是模型决定沉默" in seg, "缺这句会把人引向'决策器有问题'")


def test_stream_mark_truncated_contract():
    """返回值仍须带 truncated 元信息（发送端靠它决定要不要删残句）。"""
    import bot_core
    r = bot_core._mark_truncated("abc", True)
    check("truncated 元信息保留", getattr(r, "truncated", None) is True)


# ── ③ 接线 ────────────────────────────────────────────────────────────

def test_wired_no_tools_on_finalize():
    import re
    src = _src()
    i = src.find("def _call_deepseek_sync(")
    j = src.find("\ndef ", i + 10)
    main = src[i:j if j > 0 else len(src)]
    # 只数**真调用**：注释与 docstring 里会提到老写法（讲为什么改），
    # 那些不算。判据是"这一行以 return/call 形式出现，且不是注释"。
    _calls = [ln for ln in main.splitlines()
              if "with_tools=tools_for_call" in ln
              and not ln.strip().startswith("#")
              and not ln.strip().startswith("旧版")]
    # 正常生成那一轮**应该**带工具（它就是用来决定要不要搜的），
    # 允许且只允许 1 处；收尾路径必须全部改成 _finalize_reply。
    check("带工具的只剩正常生成那一轮", len(_calls) == 1, _calls)
    check("该处是正常生成，不是收尾",
          _calls and "stream=False" in _calls[0], _calls)
    check("收尾全部走 _finalize_reply",
          main.count("_finalize_reply(") >= 3,
          main.count("_finalize_reply("))
    # 收尾要给 progress，否则等待方会把马上要来的回复判成超时
    check("收尾带 progress",
          main.count("progress=progress") >= 3, main.count("progress=progress"))


def test_with_tools_false_omits_payload_key():
    """with_tools=False 时 payload 里不能带 tools 字段。

    直接读 `_call_chat_sync` 的源码断言 —— 它非流式分支返回 (msg, usage)、
    流式分支返回 (url, key, payload)，解包形状不同，调用它去拿 payload
    只是在测"我有没有猜对分支"，不是测契约。
    """
    src = _src()
    seg = src[src.find("def _call_chat_sync("):]
    seg = seg[:seg.find("\ndef ", 30) if seg.find("\ndef ", 30) > 0 else 4000]
    check("payload 只在 with_tools 为真时挂 tools",
          'if with_tools:\n        payload["tools"]' in seg, seg[:200])
    check("裸 False 不会被当成工具列表",
          'isinstance(with_tools, list) else TOOLS' in seg)


def main():
    print("=" * 58)
    print("工具轮收尾：不带工具、必出文本")
    print("=" * 58)
    for fn in (test_finalize_strips_tools,
               test_finalize_appends_wrapup_hint,
               test_finalize_retries_with_minimal_instruction,
               test_finalize_never_raises,
               test_stream_reports_tool_only,
               test_stream_mark_truncated_contract,
               test_wired_no_tools_on_finalize,
               test_with_tools_false_omits_payload_key):
        print(f"\n[{fn.__name__}]")
        fn()
    print("\n" + "=" * 58)
    if _FAIL:
        print(f"失败 {len(_FAIL)}：{_FAIL}")
        sys.exit(1)
    print("全部通过")


if __name__ == "__main__":
    main()