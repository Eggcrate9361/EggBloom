# -*- coding: utf-8 -*-
"""tests/test_prompt_single_path.py —— 人格只有一份（2026-11-04）

之前有两条 prompt 路径：
  · 主链：`bot_core._build_static_prompt()` 自己拼（段落过滤 + 预算 + 能力边界）
  · 主动说话：从 personality_engine 插件拿"人设原文"

两边各一份 → 人格改一处，另一处不一定跟上。真机表现就是同一条约束在主回复里
生效、在主动说话里不生效（「刚说别找你」正是主动那条路说出来的）。

现在主动说话优先用主链的静态提示词；拿不到才退回插件原文（老行为）。

判据：
  ① 主动说话的 system prompt 里含主链才有的段落（【没有耳朵】这类）
  ② 它和主链静态提示词的前缀一致（同一份东西，不是各拼一份）
  ③ 拿不到主链提示词时能降级（不抛异常、仍有提示词）
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
import plugins.proactive_talk as PT  # noqa: E402

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
    static = bc._build_static_prompt(False) or ""
    msgs = PT._build_messages({}, {"recent_msgs": {}, "history_map": {}}, "3150007406")
    sys_p = str(msgs[0].get("content") or "")

    print("[A] 主动说话用的是主链那份人格")
    check("★ 含【没有耳朵】（主链才维护的约束）", "【没有耳朵】" in sys_p, sys_p[:60])
    check("★ 含【别露后台】", "【别露后台】" in sys_p or "能力边界" in sys_p)
    check("★ 人格块是主链原文的前缀",
          static[:200] and static[:200] in sys_p, (len(static), len(sys_p)))

    print("\n[B] 人格内容一致（不是各拼一份）")
    # 取主链静态提示词里的第一段人格，确认它也出现在主动说话的 prompt 里
    head = static.strip().split("\n\n")[0].strip()
    check("第一段人格在两处一致", head and head in sys_p, head[:40])

    print("\n[C] 降级：主链拿不到时不炸")
    old = bc._build_static_prompt
    try:
        bc._build_static_prompt = lambda is_group=False: (_ for _ in ()).throw(RuntimeError("boom"))
        msgs2 = PT._build_messages({}, {"recent_msgs": {}, "history_map": {}}, "3150007406")
        sys2 = str(msgs2[0].get("content") or "")
        check("★ 降级后仍有提示词", len(sys2) > 50, len(sys2))
        check("降级不抛异常", isinstance(msgs2, list) and len(msgs2) == 2)
    finally:
        bc._build_static_prompt = old

    print("\n[D] 源码层面：确实是调主链，不是复制文本")
    src = open(os.path.join(ROOT, "plugins", "proactive_talk", "__init__.py"),
               encoding="utf-8", errors="replace").read()
    check("★ 调了 _build_static_prompt", "_build_static_prompt" in src)
    check("★ 保留了旧的人设兜底", "_fetch_personality(cfg)" in src)

    print("\n" + "=" * 60)
    print(f"通过 {PASS} / 失败 {FAIL}")
    if FAILURES:
        print("失败项：")
        for x in FAILURES:
            print("  -", x)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
