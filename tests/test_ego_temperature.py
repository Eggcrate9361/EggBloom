# -*- coding: utf-8 -*-
"""P0-1 单测：EGO 情绪温度真正生效。

修复前的事实（回查源码确认）：
  call_deepseek(personality_temperature=_ego_temp)
    → _submit_ai_task 存进 params
    → _ai_worker 取出并传给 _call_deepseek_sync(personality_temperature=...)
    → **_call_deepseek_sync 收了却不用**
    → _call_chat_sync 内部恒取全局 AI_TEMPERATURE
  ⇒ 「情绪影响说话风格」实际不存在。群聊侧 _ST.get("personality_temperature")
    同样是白传。

本测试锁两件事：
  A. 温度决策函数 _effective_temperature 的语义（可用值采用 / 脏值回退 / 夹取）
  B. 接线是通的（结构性证据：签名有参数、payload 用它、调用点真的传了）
  C. ego 聚合是「首个非空即定」（匹配其自身注释写明的「mood 优先」）
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import bot_core  # noqa: E402

PASS = []
FAIL = []


def check(name, cond, detail=""):
    if cond:
        PASS.append(name)
        print(f"  [OK]   {name}")
    else:
        FAIL.append(name)
        print(f"  [FAIL] {name}  {detail}")


print("=== A. _effective_temperature 语义 ===")
check("函数存在", hasattr(bot_core, "_effective_temperature"))
if hasattr(bot_core, "_effective_temperature"):
    f = bot_core._effective_temperature
    GLOBAL = bot_core.AI_TEMPERATURE

    check("None → 回退全局 AI_TEMPERATURE", f(None) == GLOBAL, f"{f(None)} vs {GLOBAL}")
    check("★ 传 0.9 → 采用 0.9", f(0.9) == 0.9, f(0.9))
    check("★ 传 0.4（EGO 下限）→ 采用", f(0.4) == 0.4)
    check("传字符串数字 '0.8' → 采用", f("0.8") == 0.8)
    check("超上限 5.0 → 夹到 2.0", f(5.0) == 2.0, f(5.0))
    check("超下限 0.01 → 夹到 0.2", f(0.01) == 0.2, f(0.01))
    check("★ 非数值 'abc' → 回退全局（不抛异常）", f("abc") == GLOBAL)
    check("★ NaN → 回退全局（不把 NaN 发给 API）", f(float("nan")) == GLOBAL)
    check("None 与脏值都不返回 NaN", str(f(float("nan"))).lower() != "nan")

print("\n=== B. 接线是通的（结构性证据）===")
src = open(os.path.join(ROOT, "bot_core.py"), encoding="utf-8").read()

check("★ _call_chat_sync 签名含 personality_temperature",
      bool(re.search(r"def _call_chat_sync\([^)]*personality_temperature", src, re.S)))
check("★ payload 的 temperature 取自 effective_temperature",
      '"temperature": effective_temperature' in src)
check("★ effective_temperature 由 _effective_temperature(...) 计算",
      "effective_temperature = _effective_temperature(personality_temperature)" in src)
check("★ 真实调用点把 personality_temperature 传下去了",
      re.search(r"_call_chat_sync\(messages[^)]*personality_temperature=personality_temperature", src, re.S) is not None)
check("anthropic/gemini 分支也带上了 temperature",
      re.search(r"make_provider\([^)]*temperature=effective_temperature", src, re.S) is not None)
check("旧行为可回退：默认参数是 None（不传即原样）",
      bool(re.search(r"def _call_chat_sync\([^)]*personality_temperature=None", src, re.S)))
# 其余三个调用点不传该参数 → 走 None → 保持原行为（不受本次改动影响）
_other_calls = [m for m in re.finditer(r"_call_chat_sync\(", src)]
check("其余调用点未被迫改动（默认 None 兼容）", len(_other_calls) >= 4, len(_other_calls))

print("\n=== C. ego 聚合：首个非空即定（mood 优先）===")
try:
    import plugins.ego as _ego

    saved = {}
    for _n in ("_mood_before_reply", "_personality_before_reply", "_memory_before_reply",
               "_style_before_reply", "_rhythm_before_reply"):
        saved[_n] = getattr(_ego, _n, None)
    _saved_flag = getattr(_ego, "_flag", None)

    try:
        _ego._flag = lambda k: True
        # mood 先给 1.4，后续模块给出不同温度 —— 复现「后者覆盖前者」的老问题
        _ego._mood_before_reply = lambda **kw: {"action": "inject", "content": "m", "temperature": 1.4}
        _ego._personality_before_reply = lambda **kw: {"action": "inject", "content": "p", "temperature": 0.6}
        _ego._memory_before_reply = lambda **kw: {"action": "inject", "content": "mem"}
        _ego._style_before_reply = lambda **kw: {"action": "inject", "content": "s", "temperature": 0.5}
        _ego._rhythm_before_reply = lambda **kw: {"action": "inject", "content": "r", "temperature": 0.9}

        out = _ego.before_reply("hi", "123", {})
        check("★ ego 返回温度取首个非空（mood 的 1.4）",
              isinstance(out, dict) and out.get("temperature") == 1.4,
              (out or {}).get("temperature"))
        check("注入内容仍聚合全部子模块",
              isinstance(out, dict) and all(x in (out.get("content") or "") for x in ("m", "p", "mem", "s", "r")))
    finally:
        for _n, _v in saved.items():
            setattr(_ego, _n, _v)
        if _saved_flag is not None:
            _ego._flag = _saved_flag
except Exception as e:  # pragma: no cover
    check(f"ego 可导入并受测（{type(e).__name__}: {e}）", False)

print("\n" + "=" * 62)
print(f"通过 {len(PASS)} / 失败 {len(FAIL)}")
if FAIL:
    print("失败项：", FAIL)
sys.exit(1 if FAIL else 0)
