# -*- coding: utf-8 -*-
"""tests/test_persona_format.py —— 人格规则层只认一种写法（FIXPLAN §3.4）

规则层在 `plugins/personality.txt` 的【不许】段里，写法只有一种：

    ### 块名 | scope=group|private|both
    【块名】正文…

别的写法（`## 睡觉`、`【睡觉】`、少个 #）过去会被**静默丢弃** —— 规则看着在
文件里，实际从没进过 prompt。这个文件锁四件事：

  A. 规范人格文件里没有"孤儿行"（不属于任何 ### 块的内容）
  B. 每个注册的 prompt 段都能在文件里找到对应块（改名后不会静默消失）
  C. ### 块名不重复（重名时后一个会顶掉前一个）
  D. 孤儿检测**真的会报**（临时造一份写错标题的文件）

运行：python tests/test_persona_format.py
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import bot_core  # noqa: E402

PASS = 0
FAIL = 0
FAILURES = []

#: 由 de_ai 模块生成、不在人格文件里的段
_EXTERNAL_SEGMENTS = {"去AI味铁律"}


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
    blocks = bot_core._persona_rule_blocks()

    print("[A] 规范文件没有孤儿行")
    orphans = bot_core.persona_rule_orphans()
    check("孤儿行 0 条", not orphans, orphans[:3])

    print("\n[B] 每个注册段都有对应块")
    for name in bot_core.prompt_segment_names():
        if name in _EXTERNAL_SEGMENTS:
            continue
        check(f"段「{name}」在文件里有 ### 块", name in blocks, sorted(blocks))

    print("\n[C] 块名不重复")
    import io
    raw = io.open(bot_core.PERSONA_CANONICAL, encoding="utf-8").read()
    heads = [ln.strip() for ln in raw.splitlines() if ln.strip().startswith("###")]
    names = [h[3:].split("|")[0].strip() for h in heads]
    dup = sorted({n for n in names if names.count(n) > 1})
    check("没有重名 ### 块", not dup, dup)

    print("\n[D] 孤儿检测真的会报")
    tmp = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8")
    tmp.write("【我是谁】\n测试人格。\n\n### 不许用\n【不许】\n")
    tmp.write("## 睡觉\n这一行标题少个 #，属于孤儿行。\n")
    tmp.write("### 正常块 | scope=both\n【正常块】正文。\n")
    tmp.close()
    old_canonical = bot_core.PERSONA_CANONICAL
    old_cache = dict(bot_core._RULE_BLOCK_CACHE)
    try:
        bot_core.PERSONA_CANONICAL = tmp.name
        bot_core._RULE_BLOCK_CACHE.clear()
        got = bot_core.persona_rule_orphans()
        got_blocks = bot_core._persona_rule_blocks()
    finally:
        bot_core.PERSONA_CANONICAL = old_canonical
        bot_core._RULE_BLOCK_CACHE.clear()
        bot_core._RULE_BLOCK_CACHE.update(old_cache)
        os.unlink(tmp.name)
    check("写错标题的行被识别为孤儿", any("少个 #" in x for x in got), got)
    check("正常块照常解析", "正常块" in got_blocks, list(got_blocks))
    check("孤儿行不会混进块正文",
          "少个 #" not in (got_blocks.get("正常块") or ""), got_blocks.get("正常块"))

    print("\n" + "=" * 56)
    print(f"通过 {PASS} / 失败 {FAIL}")
    if FAILURES:
        print("失败项：")
        for f in FAILURES:
            print("  -", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
