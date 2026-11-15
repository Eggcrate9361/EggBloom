# -*- coding: utf-8 -*-
"""tests/test_tool_runner.py —— 工具执行单点实现（主链与 agent 共用）回归。

覆盖：
  · normalize_tool_calls：OpenAI / Anthropic 结构兼容、补 id、剔除无名
  · execute_tool_call：越界防护、参数补齐、异常隔离、retention+spill、
    truncated/error/path 分列、落盘恢复提示
  · 主链与 agent 共用同一语义（两处行为一致）

运行：python tests/test_tool_runner.py
"""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from harness import tool_runner as tr  # noqa: E402
from harness import spill as _spill  # noqa: E402

PASS = 0
FAIL = 0
FAILURES = []


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        FAILURES.append(f"{name} :: {extra}")


# ── 隔离 spill 目录 ────────────────────────────────────────────────
_tmp = tempfile.mkdtemp(prefix="toolrunner_")
_orig = _spill.SPILL_DIR
_spill.SPILL_DIR = os.path.join(_tmp, "spill")


def _tc(name, args="{}", id_="c1"):
    return {"id": id_, "type": "function",
            "function": {"name": name, "arguments": args}}


# ══ normalize_tool_calls ═══════════════════════════════════════════
def test_normalize():
    # OpenAI 标准结构
    r = tr.normalize_tool_calls([_tc("search_web")], session_id="s", step=0)
    check("norm: OpenAI 结构", len(r) == 1 and r[0]["function"]["name"] == "search_web", r)

    # Anthropic 平铺结构
    r2 = tr.normalize_tool_calls([{"name": "open_app", "arguments": "{}"}], session_id="s", step=1)
    check("norm: 平铺结构兼容", r2 and r2[0]["function"]["name"] == "open_app", r2)
    check("norm: 平铺也补 id", bool(r2 and r2[0]["id"]), r2)

    # 缺 id → 补稳定 id
    r3 = tr.normalize_tool_calls([{"function": {"name": "x", "arguments": "{}"}}],
                                 session_id="sess", step=3)
    check("norm: 缺 id 补上", r3[0]["id"].startswith("call_sess_3_"), r3[0]["id"])

    # 无名 → 剔除
    r4 = tr.normalize_tool_calls([{"function": {"arguments": "{}"}},
                                  {"garbage": 1},
                                  "not-a-dict",
                                  _tc("ok")], session_id="s", step=0)
    check("norm: 剔除无名/畸形", len(r4) == 1 and r4[0]["function"]["name"] == "ok", r4)

    # 空/None
    check("norm: 空列表", tr.normalize_tool_calls([], session_id="s") == [])
    check("norm: None", tr.normalize_tool_calls(None, session_id="s") == [])

    # function 存在但 name 为 None，顶层给了 name
    r5 = tr.normalize_tool_calls([{"function": {"name": None, "arguments": "{}"},
                                   "name": "recovered"}], session_id="s", step=0)
    check("norm: function.name 为 None 时用顶层 name",
          r5 and r5[0]["function"]["name"] == "recovered", r5)


# ══ execute_tool_call ══════════════════════════════════════════════
def test_execute_basic():
    fns = {"echo": lambda a: "hello " + str(a.get("x", ""))}
    r = tr.execute_tool_call(_tc("echo", '{"x":"world"}'), tool_functions=fns)
    check("exec: 正常执行 ok", r["ok"] is True, r)
    check("exec: inline 是结果", r["inline"] == "hello world", r["inline"])
    check("exec: full_text 一致", r["full_text"] == "hello world", r["full_text"])
    check("exec: 未截断", r["truncated"] is False, r)
    check("exec: 未落盘", r["spilled"] is False and r["path"] is None, r)
    check("exec: 无 error", r["error"] is None, r)
    check("exec: blocked 为 None", r["blocked"] is None, r)

    # 非 str 返回值 → json 序列化
    fns2 = {"obj": lambda a: {"k": "值", "n": 1}}
    r2 = tr.execute_tool_call(_tc("obj"), tool_functions=fns2)
    check("exec: 非 str 结果 JSON 化", "值" in r2["inline"] and r2["ok"], r2["inline"])

    # 参数非法 JSON → 空 dict（不崩）
    r3 = tr.execute_tool_call(_tc("echo", "not-json"), tool_functions=fns)
    check("exec: 非法 JSON 参数不崩", r3["ok"] is True, r3)


def test_execute_guards():
    called = {"n": 0}

    def _spy(args):
        called["n"] += 1
        return "ran"

    fns = {"available": _spy, "hidden": _spy}

    # 越界防护：本轮未提供的工具 → 不执行
    r = tr.execute_tool_call(_tc("hidden"), tool_functions=fns,
                             offered_names={"available"})
    check("exec: 越界被拒", r["ok"] is False and r["blocked"] == "not_offered", r)
    check("exec: 越界**没有**真的执行", called["n"] == 0, called["n"])
    check("exec: 越界提示含可用清单", "available" in r["inline"], r["inline"])

    # 未提供 且 实现不存在 → 提示未知工具而不是列出清单
    r2 = tr.execute_tool_call(_tc("ghost"), tool_functions=fns, offered_names={"available"})
    check("exec: 不存在且未提供 → 未知工具", "未知工具" in r2["inline"], r2["inline"])

    # offered_names=None → 不拦截
    r3 = tr.execute_tool_call(_tc("available"), tool_functions=fns, offered_names=None)
    check("exec: offered=None 不拦截", r3["ok"] and called["n"] == 1, (r3["ok"], called["n"]))

    # 实现本身不存在（但在提供名单里）→ unknown
    r4 = tr.execute_tool_call(_tc("available"), tool_functions={},
                              offered_names={"available"})
    check("exec: 无实现 → unknown", r4["ok"] is False and r4["blocked"] == "unknown", r4)


def test_execute_exception_isolation():
    def _boom(args):
        raise ValueError("kaboom")

    r = tr.execute_tool_call(_tc("bad"), tool_functions={"bad": _boom})
    check("exec: 异常被隔离(ok=False)", r["ok"] is False, r)
    check("exec: 异常信息进 error", r["error"] and "kaboom" in r["error"], r["error"])
    check("exec: 异常也回灌文本（模型能看到）", "kaboom" in r["inline"], r["inline"])
    check("exec: 异常不算 blocked", r["blocked"] is None, r)


def test_execute_param_fill():
    seen = {}

    def _cap(args):
        seen.update(args)
        return "ok"

    # 群聊补 group_id
    tr.execute_tool_call(_tc("t"), tool_functions={"t": _cap},
                         group_id="12345", is_group=True)
    check("exec: 群聊补 group_id", seen.get("group_id") == "12345", seen)

    # 私聊补 target_qq
    seen.clear()
    tr.execute_tool_call(_tc("t"), tool_functions={"t": _cap},
                         target_qq="999", is_group=False)
    check("exec: 私聊补 target_qq", seen.get("target_qq") == "999", seen)

    # 模型显式给了 → 不覆盖
    seen.clear()
    tr.execute_tool_call(_tc("t", '{"group_id":"model_gave"}'),
                         tool_functions={"t": _cap},
                         group_id="12345", is_group=True)
    check("exec: 不覆盖模型显式值", seen.get("group_id") == "model_gave", seen)


def test_execute_retention_spill():
    big = "中" * 20000  # 远超预算
    r = tr.execute_tool_call(_tc("big"), tool_functions={"big": lambda a: big},
                             max_tokens=100)
    check("exec: 超长标记 spilled", r["spilled"] is True, r["spilled"])
    check("exec: 超长标记 truncated", r["truncated"] is True, r["truncated"])
    check("exec: 落盘给出路径", bool(r["path"]), r["path"])
    check("exec: 落盘文件真实存在", os.path.isfile(r["path"] or ""), r["path"])
    check("exec: 落盘保存完整内容",
          open(r["path"], encoding="utf-8").read() == big, "内容不一致")
    check("exec: inline 含恢复提示", "已保存到" in r["inline"] or "路径" in r["inline"],
          r["inline"][-120:])
    check("exec: inline 体积远小于原文", len(r["inline"]) < len(big) / 5,
          (len(r["inline"]), len(big)))
    check("exec: omitted_bytes > 0", r["omitted_bytes"] > 0, r["omitted_bytes"])

    # 空结果：truncated/spilled/error 各自独立，不互相污染
    r2 = tr.execute_tool_call(_tc("empty"), tool_functions={"empty": lambda a: ""})
    check("exec: 空结果 truncated=False", r2["truncated"] is False, r2["truncated"])
    check("exec: 空结果 spilled=False", r2["spilled"] is False, r2["spilled"])
    check("exec: 空结果 error=None", r2["error"] is None, r2["error"])
    check("exec: 空结果 ok=True", r2["ok"] is True, r2["ok"])


def test_shared_semantics_with_agent():
    """主链与 agent 共用同一实现 → 同一输入得到同一结构。"""
    from harness import agent_loop as al
    check("共用: agent_loop 用同一 normalize",
          al._tr.normalize_tool_calls is tr.normalize_tool_calls, "不是同一个函数")
    check("共用: agent_loop 用同一 execute",
          al._tr.execute_tool_call is tr.execute_tool_call, "不是同一个函数")
    check("共用: 预算常量一致",
          al.DEFAULT_MAX_TOOL_RESULT_TOKENS == tr.DEFAULT_MAX_TOOL_RESULT_TOKENS,
          (al.DEFAULT_MAX_TOOL_RESULT_TOKENS, tr.DEFAULT_MAX_TOOL_RESULT_TOKENS))


def main():
    for fn in (test_normalize, test_execute_basic, test_execute_guards,
               test_execute_exception_isolation, test_execute_param_fill,
               test_execute_retention_spill, test_shared_semantics_with_agent):
        try:
            fn()
        except Exception as e:
            import traceback
            global FAIL
            FAIL += 1
            FAILURES.append(f"{fn.__name__} 抛异常: {e}\n{traceback.format_exc()}")
    print(f"\n{'='*60}")
    print(f"通过 {PASS} / 失败 {FAIL}")
    if FAILURES:
        print("-" * 60)
        for f in FAILURES:
            print("  ✗ " + f)
    print("=" * 60)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
