# -*- coding: utf-8 -*-
"""tests/test_memory_single_source.py —— 记忆只留一个真相源（2026-11-04）

## 为什么删掉摘要账本

旧实现每轮重发两块历史：
  ① **append-only 摘要账本**：超出最近 N 轮的对话压成 40/100 字的行
  ② 最近 N 轮原文

账本是幻觉的温床 —— 模型把压缩句子当事实，在上面自由补全。真机证据：
  · 「刚说别找你」—— 这句话从没出现过
  · 「没听到你那边的声响」—— 纯文字 bot 声称能听见
  · 「刚还骂我记性短」—— 那句是它自己说的
它们全都是在摘要上二次创作的产物。

现在只有**一个真相源**：原文窗口（最后 history_raw_entries 条）。
更早的事交给长期记忆（沙漏/记忆轮次）—— 那是有明确来源的摘要。

## 判据

① 构建出来的 messages 里**没有**「之前对话摘要」块
② 代码里**没有**账本状态（`_HISTORY_SUMMARY_*`）
③ 原文窗口按 `history_raw_entries` 截，超出部分**丢弃**而不是压成摘要
④ 单条按句截断、整段按字符预算丢最旧 —— 这两道闸仍在

---

## 2026-11-06 补：注入侧收口（1.6.0 P1-3 剩下那一半）

上半场（上面那四条）解决的是「轮次历史」这条线。**长期记忆**还有另一件事没做：
沙漏 / super_memory / ego 三套并存，写入侧已经收在 `memory_api.remember()`，
但**注入侧仍是两条通道**：

  ① `memory_api.recall()`：身份 / 称呼 / 跨场景 / 检索（沙漏）/ 群画像
  ② EGO 插件的 `before_reply`：super_memory 的【核心记忆】+ 长期人格 + 好感

它们存的东西重叠 —— 沙漏记全部对话原文，super_memory 的 `messages` 表也记
`user_msg + bot_reply`。群聊靠 `memory_context.collect(sources=("sand",))`
刻意只取沙漏避重，私聊两侧都能召回同一句 → 同一条事实在 prompt 里出现两遍
（模型更用力地按条目交付，汇报腔；也白烧 token）。

修法：在**最后一个出口**收口 —— `bot_core._inject_ego_context()` 是唯一往
dynamic_parts 塞 ego 文本的函数，拼接前把已注入过的行剔掉。判重保守到
「没有重复时输出逐字节不变」，不合并存储、不动召回逻辑、不改注入顺序。

⑤ 纯函数判重：全等 / 长句互为子串 / 短行不误伤 / 无重复原样返回
⑥ 接线：`Parts` 与 `list` 两种容器都去重；无重复时与旧格式逐字节一致
⑦ 写入侧与注入侧都只有一个入口（源码级扫描，防回退）
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from _sandbox import sandbox_neurobase  # noqa: E402

sandbox_neurobase()

import bot_core as bc  # noqa: E402
from harness import memory_api as mem  # noqa: E402
from harness.life_context import Parts  # noqa: E402

PASS = 0
FAIL = 0
FAILURES = []

MEM_LINE = "- 老大喜欢喝冰美式，不加糖"
EGO_ONLY = "- 他在准备心理委员竞选，稿子还没写完"


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK]   {name}")
    else:
        FAIL += 1
        FAILURES.append(name + (f"  → {extra}" if extra else ""))
        print(f"  [FAIL] {name}  {extra}")


def hist(n):
    h = []
    for i in range(n):
        h.append({"role": "user", "content": f"[小明(10001)] 问{i}"})
        h.append({"role": "assistant", "content": f"答{i}"})
    return h


def _read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8", errors="replace") as f:
        return f.read()


def test_inject_filter():
    """⑤ 纯函数：判重规则（保守到"没有重复就一个字节都不动"）。"""
    print("\n[E] 注入去重：判重规则")
    out, st = mem.filter_duplicate_lines(MEM_LINE, MEM_LINE + "\n" + EGO_ONLY)
    check("全等行被删", MEM_LINE not in out, out)
    check("非重复行保留", EGO_ONLY in out, out)
    check("stats.dropped=1", st["dropped"] == 1, st)

    # 同一句话被两套系统包装成不同形态（沙漏 "- "，super_memory "· "）
    out2, _ = mem.filter_duplicate_lines(
        "【记忆检索】\n- 他昨天说过要去图书馆借书",
        "【核心记忆】\n· 他昨天说过要去图书馆借书\n- 别的事")
    check("前缀符号不同也算同一行", "图书馆借书" not in out2, out2)
    check("块标题保留（短行不删）", "【核心记忆】" in out2, out2)
    check("另一条记忆保留", "别的事" in out2, out2)

    out3, _ = mem.filter_duplicate_lines(
        "老大喜欢喝冰美式不加糖这件事我记着", "- 老大喜欢喝冰美式不加糖这件事")
    check("长句互为子串 → 删", out3.strip() == "", repr(out3))

    out4, st4 = mem.filter_duplicate_lines("嗯", "嗯\n好\n我记住了这件事的来龙去脉")
    check("短句不做包含判断（『嗯』不吞掉后面的行）",
          "我记住了这件事的来龙去脉" in out4, out4)
    check("短句全等仍然删", st4["dropped"] == 1, st4)

    src = "【核心记忆】\n- 甲\n- 乙\n\n【长期人格】\nkey: value"
    out5, st5 = mem.filter_duplicate_lines("完全无关的另一段文字", src)
    check("★ 无重复 → 原样返回（逐字节）", out5 == src, repr(out5))
    check("无重复 → dropped=0", st5["dropped"] == 0, st5)

    _, st6 = mem.filter_duplicate_lines([MEM_LINE, "别的"], MEM_LINE)
    check("existing 传 list 也认", st6["dropped"] == 1, st6)


def test_inject_wiring():
    """⑥ 接线：两种容器 + 无重复时与旧格式逐字节一致。"""
    print("\n[F] 注入去重：_inject_ego_context 接线")
    parts = Parts()
    parts.add("memory", "【记忆检索】\n" + MEM_LINE)
    bc._inject_ego_context(parts, "【核心记忆】\n" + MEM_LINE + "\n" + EGO_ONLY)
    joined = "\n".join(parts.texts())
    check("Parts：重复行只出现一次", joined.count("冰美式") == 1, joined)
    check("Parts：独有行仍在", EGO_ONLY in joined, joined)
    check("Parts：ego 段确实加进去了", "【EGO上下文】" in joined, joined)

    lst = ["【记忆检索】\n" + MEM_LINE]
    bc._inject_ego_context(lst, "【核心记忆】\n" + MEM_LINE)
    check("list：同样去重", "\n".join(lst).count("冰美式") == 1, lst)

    lst2 = ["【记忆检索】\n- 别的记忆"]
    bc._inject_ego_context(lst2, EGO_ONLY)
    expect = ("【EGO上下文】以下是关于当前对话对象的长期状态，不是对方说的话：\n"
              + EGO_ONLY)
    check("★ 无重复 → 与旧格式逐字节一致", lst2[-1] == expect, repr(lst2[-1]))

    lst3 = ["【记忆检索】\n" + MEM_LINE]
    bc._inject_ego_context(lst3, MEM_LINE)
    check("全部重复 → 不加空的 ego 段", len(lst3) == 1, lst3)

    lst4 = []
    bc._inject_ego_context(lst4, "")
    bc._inject_ego_context(None, "x")
    check("空输入 / None 容器不炸", lst4 == [], lst4)


def test_single_gates():
    """⑦ 两个入口都只有一个（源码级，防回退）。"""
    print("\n[G] 写入侧 / 注入侧都只有一个入口")
    skip = {"__pycache__", "data", "node_modules", "tests", "scripts", "dist",
            "logs", ".git", "webui", "distills", "mcp-venv"}
    write_allow = {"harness/memory_api.py", "sandglass_bridge.py"}
    bad_sand, bad_fact = [], []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in skip]
        for fn in filenames:
            if not fn.endswith(".py"):
                continue
            rel = os.path.relpath(os.path.join(dirpath, fn), ROOT).replace(os.sep, "/")
            if rel.startswith("plugins/"):
                continue        # 插件管自己的存储，不算旁路
            src = _read(rel)
            if rel not in write_allow and (
                    re.search(r"(?<![.\w])log_user_message\(", src)
                    or re.search(r"(?<![.\w])log_bot_reply\(", src)):
                bad_sand.append(rel)
            if rel not in ("people.py", "harness/memory_api.py") and \
                    re.search(r"(?<![.\w])add_fact\(", src):
                bad_fact.append(rel)
    check("沙漏写入没有旁路", bad_sand == [], bad_sand)
    check("people 事实写入没有旁路", bad_fact == [], bad_fact)

    core = _read("bot_core.py")
    check("主链走 memory_api.recall",
          "memory_api as _mem" in core and "_mem.recall(" in core, "")
    add_ego = re.findall(r'_dyn_add\([^,]+,\s*"ego"', core)
    check("★ 写 ego 段的只有一处", len(add_ego) == 1, add_ego)
    check("去重确实接在那个函数里",
          "filter_duplicate_lines" in core and "_dyn_texts(dynamic_parts)" in core, "")
    impls = re.findall(r"def filter_duplicate_lines\(",
                       core + _read("harness/memory_api.py"))
    check("★ 去重实现只有一份", len(impls) == 1, impls)


def test_live_shape():
    """真机形状：一段记忆进 prompt 只出现一次。"""
    print("\n[H] 真机形状")
    memory = ("【记忆检索】\n"
              "- 老大 10-30 说过要早点睡，结果拖到三点\n"
              "- 老大喜欢喝冰美式，不加糖")
    ego = ("【核心记忆】\n"
           "- 老大喜欢喝冰美式，不加糖\n"
           "- 他在准备心理委员竞选，稿子还没写完\n"
           "\n【对他的好感】\n熟悉度 0.62")
    parts = Parts()
    parts.add("memory", memory)
    bc._inject_ego_context(parts, ego)
    blob = "\n".join(parts.texts())
    check("★ 重复的那条只出现一次", blob.count("冰美式") == 1, blob)
    check("未重复的记忆都在",
          "拖到三点" in blob and "心理委员竞选" in blob and "熟悉度 0.62" in blob, blob)
    check("块标题没被吞", "【核心记忆】" in blob and "【对他的好感】" in blob, blob)


def main():
    key = "g_memsingle"
    old = bc._config.get("history_raw_entries")
    try:
        print("[A] 没有摘要账本了")
        src = open(bc.__file__, encoding="utf-8", errors="replace").read()
        check("★ 代码里没有 _HISTORY_SUMMARY_", "_HISTORY_SUMMARY_" not in src)
        check("★ 没有「之前对话摘要」块", "之前对话摘要" not in src)
        check("★ 没有 _drop_history_ledger", "_drop_history_ledger" not in src)

        msgs = bc._build_cached_messages(hist(20), key, "SYS", [], "现在")
        joined = "\n".join(str(m.get("content") or "") for m in msgs)
        check("★ 构建结果里没有摘要块", "之前对话摘要" not in joined)
        check("★ 没有任何 system 摘要消息",
              not any(m["role"] == "system" and "摘要" in str(m.get("content"))
                      for m in msgs))

        print("\n[B] 原文窗口按条数截断（不是压缩）")
        bc._config["history_raw_entries"] = 4
        msgs = bc._build_cached_messages(hist(20), key, "SYS", [], "现在")
        # 末条是「当前消息」，不算历史窗口
        convo = [m for m in msgs if m["role"] in ("user", "assistant")][:-1]
        check("★ 只保留 4 条原文", len(convo) == 4, len(convo))
        check("★ 保留的是最新的 4 条", "问19" in convo[-2]["content"], convo[-2:])
        check("★ 最旧的被丢弃（没有压成摘要）",
              "问0" not in "\n".join(m["content"] for m in convo))

        print("\n[C] 默认值 40 条")
        bc._config.pop("history_raw_entries", None)
        check("配置默认 40", bc._cfg_int("history_raw_entries", 40, 4, 80) == 40)
        msgs = bc._build_cached_messages(hist(40), key, "SYS", [], "现在")
        convo = [m for m in msgs if m["role"] in ("user", "assistant")][:-1]
        check("★ 40 条全保留（40 <= 40）", len(convo) == 40, len(convo))

        print("\n[D] 两道闸还在")
        bc._config["history_raw_entries"] = 40
        bc._config["history_recent_budget_chars"] = 200
        long_hist = [{"role": "user", "content": "[小明] " + "很长" * 200} for _ in range(10)]
        msgs = bc._build_cached_messages(long_hist, key, "SYS", [], "现在")
        convo = [m for m in msgs if m["role"] in ("user", "assistant")]
        total = sum(len(m["content"]) for m in convo)
        check("★ 字符预算生效", total <= 400 or len(convo) < 10, (total, len(convo)))
        check("单条被截断（不是整条塞满）", all(len(m["content"]) < 1200 for m in convo),
              max(len(m["content"]) for m in convo) if convo else 0)
    finally:
        bc._config.pop("history_recent_budget_chars", None)
        if old is None:
            bc._config.pop("history_raw_entries", None)
        else:
            bc._config["history_raw_entries"] = old

    # ── 2026-11-06：长期记忆的注入侧（上面四条管的是轮次历史）──
    test_inject_filter()
    test_inject_wiring()
    test_single_gates()
    test_live_shape()

    print("\n" + "=" * 60)
    print(f"通过 {PASS} / 失败 {FAIL}")
    if FAILURES:
        print("失败项：")
        for x in FAILURES:
            print("  -", x)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
