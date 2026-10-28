# -*- coding: utf-8 -*-
"""群聊「模型发起工具调用」这条路径不能崩（2026-10-28，1.5.9 发布前 P0）

## 为什么单独一个文件

`bot_core._call_deepseek_sync` 有 3000+ 行、十几个分支，但**只有"群聊 + 模型决定调工具"
这一条**踩过线上事故（`1eb2bfa` 修的那个 NameError）。

事故的可怕之处不是崩，而是**崩得很像正常**：

```
[16:46:27] 工具调用: ['search_web', 'search_web']
[16:46:27] AI 失败: name 'merged_text' is not defined
[16:46:27] 群[829854136] AI决定不回复: (空)
```

前两行是真相，第三行是假象。线上看到的是"它不说话"，日志给的结论是"模型决定沉默"，
于是排查方向跑到决策器、发言闸、频控上 —— 没有一个是对的。

单测为什么没拦住：`_do_group_admin` 有测试，但它直接调函数，
**从不经过"群聊 + 工具调用"这条真实路径**，所以闭包里引用一个不存在的变量
永远不会被发现。这个文件补的就是那个缺口。

## 本测试做什么

把 `_call_chat_sync` 换成假的（第一轮要求调 `search_web`，第二轮给文本），
把工具执行也换成假的（不联网），然后**真的调用** `_call_deepseek_sync(is_group=True)`。

判据：
1. 返回的是文本，不是 None
2. 工具确实被执行了（不是"跳过工具直接编答案"）
3. 至少两轮模型调用（先工具后收尾）

注意：**修复前不会向外抛异常**。`_call_deepseek_sync` 内部有 try/except，
它把 NameError 记成一行「AI 失败」然后返回 None —— 这恰恰是事故难以定位的原因：
调用方看到的只是"没结果"，而不是"崩了"。所以判据必须落在**返回值**上，
不能靠 `assertRaises`。

## 这条测试有效吗（已实测）

把 `_m=user_msg` 改回 `_m=merged_text` 造一份"回归前"的 `bot_core.py`，
用 `sys.path` 前置加载它跑本文件：

```
[FAIL] 返回了文本  None
[FAIL] 文本内容正确  None
[FAIL] 工具真的被执行了  []
[FAIL] 至少两轮模型调用  1
FAILED (4)
```

同时第 2 组（私聊）在回归前代码上**仍然通过** —— 与真机日志一致
（16:43:40 私聊正常回复，16:46:27 群聊一搜就死）。这是"崩的只有群聊工具路径"
的直接证据。

运行：python tests/test_group_toolcall_no_crash.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import bot_core as bc  # noqa: E402

_fail = []


def check(name, ok, detail=""):
    if ok:
        print(f"  [OK]   {name}")
    else:
        print(f"  [FAIL] {name}  {detail}")
        _fail.append(name)


def _stub(token_stats):
    """把网络与记账都摘掉，只留逻辑。"""
    seq = {"n": 0}

    def fake_chat(messages, runtime_cfg=None, max_tokens=None, stream=False,
                  with_tools=None, personality_temperature=None, **kw):
        seq["n"] += 1
        if seq["n"] == 1:
            return ({"content": "", "tool_calls": [{
                "id": "call_1", "type": "function",
                "function": {"name": "search_web",
                             "arguments": '{"query":"明日方舟 半周年 限定"}'}}]},
                    {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15})
        return ({"content": "限定是维什戴尔。", "tool_calls": []},
                {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15})

    import harness.tool_runner as tr
    seen = {"tools": []}

    def fake_exec(tc, **kw):
        name = (tc.get("function") or {}).get("name") or ""
        seen["tools"].append(name)
        return {"ok": True, "tool": name,
                "full_text": "结果：2026 半周年限定 维什戴尔",
                "inline": "结果：2026 半周年限定 维什戴尔",
                "blocked": False, "truncated": False, "spilled": False,
                "path": None, "omitted_bytes": 0, "error": None, "elapsed": 0.01}

    orig = (bc._call_chat_sync, tr.execute_tool_call)
    bc._call_chat_sync = fake_chat
    tr.execute_tool_call = fake_exec
    return seq, seen, orig


def _restore(orig, token_stats, orig_record):
    import harness.tool_runner as tr
    bc._call_chat_sync, tr.execute_tool_call = orig
    token_stats.record = orig_record


def test_group_toolcall_does_not_crash():
    print("\n[1] 群聊 + 模型调工具 → 不崩，返回文本")
    import token_stats
    orig_record = token_stats.record
    token_stats.record = lambda *a, **k: None
    seq, seen, orig = _stub(token_stats)
    try:
        out = bc._call_deepseek_sync("明日方舟今年半周年限定是谁",
                                     group_id="829854136", is_group=True,
                                     sender="3150007406")
        check("★ 返回了文本（修复前是 None：异常被内部吞成一行'AI 失败'）",
              bool(out and out.strip()), repr(out))
        check("文本内容正确", "维什戴尔" in str(out), repr(out))
        check("★ 工具真的被执行了", seen["tools"] == ["search_web"], seen["tools"])
        check("至少两轮模型调用（先工具后收尾）", seq["n"] >= 2, seq["n"])
    except Exception as e:
        check("★ 返回了文本（修复前是 None：异常被内部吞成一行'AI 失败'）", False,
              f"抛了异常 {type(e).__name__}: {e}")
    finally:
        _restore(orig, token_stats, orig_record)


def test_private_still_works():
    """私聊没有 is_group，不该被群聊那段的改动影响。"""
    print("\n[2] 私聊同样能走工具路径（回归）")
    import token_stats
    orig_record = token_stats.record
    token_stats.record = lambda *a, **k: None
    seq, seen, orig = _stub(token_stats)
    try:
        out = bc._call_deepseek_sync("明日方舟今年半周年限定是谁",
                                     target_qq="3150007406", is_group=False,
                                     sender="3150007406")
        check("私聊返回文本", bool(out and out.strip()), repr(out))
        check("私聊工具被执行", seen["tools"] == ["search_web"], seen["tools"])
    except Exception as e:
        check("私聊不抛异常", False, f"{type(e).__name__}: {e}")
    finally:
        _restore(orig, token_stats, orig_record)


def main():
    test_group_toolcall_does_not_crash()
    test_private_still_works()
    print("\n" + "=" * 56)
    if _fail:
        print(f"FAILED ({len(_fail)}): " + ", ".join(_fail))
        sys.exit(1)
    print("ALL PASS")


if __name__ == "__main__":
    main()
