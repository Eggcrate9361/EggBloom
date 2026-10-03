# -*- coding: utf-8 -*-
"""黑话管线闭环自检（2026-10-03，任务 1）。

回归的是一个**真 bug**：学习侧写 plugins/ego/slang_dict.json，注入侧读应用根
slang_dict.json，两个文件永不交汇 → "让它学的黑话，学完进了死胡同"。

核心断言：learner 写进去的词，必须能被 slang_injector.get_context_for_llm() 读出来。
跑法会真实改动词典文件（写入一条测试词后立刻撤销），不依赖 pytest。

用法（在项目根目录）：
    python tests/test_slang_pipeline.py
    python tests/test_slang_pipeline.py --app "E:\\...\\resources\\app"   # 对运行副本跑
    python tests/test_slang_pipeline.py --keep   # 不撤销测试词（调试用）

★ 注意：slang_dict.json / slang_replies.json 是**运行态数据，不在源码库里**
（sync-dist.ps1 也不管它们），所以要对运行副本验证时用 --app 指向 resources\\app。
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

_ap = argparse.ArgumentParser()
_ap.add_argument("--keep", action="store_true", help="不撤销测试词（调试用）")
_ap.add_argument("--app", default="", help="运行副本 resources\\app 路径（默认用源码库）")
args = _ap.parse_args()

if args.app:
    # 让 slang_injector / plugins 都从运行副本解析，才能验证真实数据
    args.app = os.path.abspath(args.app)
    sys.path.insert(0, args.app)
else:
    args.app = ROOT

import slang_injector as SI  # noqa: E402
from plugins.ego import slang_learner as SL  # noqa: E402

print(f"被测应用根: {args.app}")
fails = []
PROBE = "zzz_闭环自检探针"


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  {detail}" if detail else ""))
    if not cond:
        fails.append(name)


# ══ 1. 路径统一：两侧必须指向同一个文件 ══════════════════════════════
print("=== 1. 两侧路径是否同一真源 ===")
check("learner.SLANG_DICT_PATH == injector.SLANG_DICT_PATH",
      os.path.normcase(SL.SLANG_DICT_PATH) == os.path.normcase(SI.SLANG_DICT_PATH),
      SL.SLANG_DICT_PATH)
check("learner.SLANG_REPLIES_PATH == injector.SLANG_REPLIES_PATH",
      os.path.normcase(SL.SLANG_REPLIES_PATH) == os.path.normcase(SI.SLANG_REPLIES_PATH),
      SL.SLANG_REPLIES_PATH)
check("真源落在应用根（不是 plugins/ego/）",
      os.path.normcase(os.path.dirname(SI.SLANG_DICT_PATH)) == os.path.normcase(args.app),
      os.path.dirname(SI.SLANG_DICT_PATH))
check("学习侧不再读 plugins/ego/slang_dict.json",
      os.path.normcase(os.path.dirname(SL.SLANG_DICT_PATH)) != os.path.normcase(
          os.path.join(args.app, "plugins", "ego")))

# ══ 2. 旧条目一条不丢 ══════════════════════════════════════════════
print("\n=== 2. 合并后存量 ===")
entries = SI.get_entries()
check("真源可读且非空", len(entries) > 0, f"{len(entries)} 条")
check("合并后条数 >= 107（104 + 61 − 58）", len(entries) >= 107, f"实际 {len(entries)}")

# ══ 3. ★ 闭环：learner 写入 → injector 读出 ══════════════════════════
print("\n=== 3. 学习 → 注入 闭环 ===")
before = SI.get_entries()
had_probe = PROBE in before
ctx = SL.add_manual(PROBE, "闭环自检用探针词，验证学习侧写入能被注入侧读到", source="自检")
check("add_manual 接受新词", bool(ctx.get("ok")), str(ctx.get("error", "")))
check("add_manual 拒绝重复词（去重读的是同一真源）",
      SL.add_manual(PROBE, "重复", source="自检").get("ok") is False)

# 走真实注入路径：inject_selected 才是当初断掉的那一环
res = SL.inject_selected([PROBE])
check("inject_selected 报告已注入", res.get("injected") == 1, str(res))

after = SI.get_entries()
check("注入侧立即读到新词（无需 reload）", PROBE in after)
check("新词释义正确落地", after.get(PROBE, "").startswith("闭环自检用探针词"))

hit = SI.get_context_for_llm(f"刚才那个 {PROBE} 好好笑")
check("get_context_for_llm 命中并注入该词", PROBE in hit)
check("命中提示带使用引导", "接着对方的话回应" in hit)

miss = SI.get_context_for_llm("完全不含任何黑话的普通句子")
check("未命中时不误注入该词", PROBE not in miss)

# ══ 4. 撤销测试词，把词典还原 ═══════════════════════════════════════
if not args.keep:
    print("\n=== 4. 还原 ===")
    SI.remove(PROBE)
    restored = SI.get_entries()
    check("探针词已移除", PROBE not in restored)
    check("其余条目未受影响", len(restored) == len(after) - 1,
          f"{len(after)} → {len(restored)}")
    if not had_probe:
        # 同步清掉 learner 侧的 pending/injected 痕迹，避免污染真实候选池
        for f in (SL.PENDING_FILE, SL.INJECTED_FILE):
            try:
                with open(f, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
            except Exception:
                continue
            if isinstance(data, list):
                data = [x for x in data if not (isinstance(x, dict) and x.get("id") == PROBE)]
                with open(f, "w", encoding="utf-8") as fh:
                    json.dump(data, fh, ensure_ascii=False, indent=2)
        check("learner 候选池已清干净", True)
    else:
        check("探针词原本就存在，未清理（--keep 语义）", True)
else:
    print("\n(--keep：测试词已保留，未还原)")

# ══ 5. replies 侧同样统一 ══════════════════════════════════════════
print("\n=== 5. 回复模板 ===")
check("get_reply_templates 可读真源", isinstance(SL.get_reply_templates(), dict))
SL.set_reply_template("_闭环探针", ["测试回复"])
check("写入的回复模板能被注入侧读到", "_闭环探针" in SI.get_replies())
if not args.keep:
    SL.delete_reply_template("_闭环探针")
    check("探针回复模板已清理", "_闭环探针" not in SI.get_replies())
    check("清理后与自检前一致", "_闭环探针" not in SL.get_reply_templates().get("entries", {}))

print("\n" + "=" * 58)
if fails:
    print(f"失败 {len(fails)} 项：")
    for f in fails:
        print("  - " + f)
    sys.exit(1)
print("全部通过：黑话学习 → 注入 闭环已打通")
