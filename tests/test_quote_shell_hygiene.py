# -*- coding: utf-8 -*-
"""引用消息壳（`[引用:…] / [当前]`）的卫生回归（2026-11-10 真机证据）。

真机 09:40–10:05 群聊复盘指出四类问题，本测试钉住 P0 修复后的判据：

  A  检索词不能来自被引原文 —— 真机拿 Bot 自己上一句「先把出处给我，再吹」
     去搜，搜回「先」字字典页，模型据此编出「谢霆锋 2026 演唱会 MV 带 AI 修复」。
  B  `【结果质量：偏薄】` 的结果正文不能再交给模型（软提示已被证明无效）。
  D  存档 / 记忆 / 口癖学习 / 日志只能吃**本次发言**，不能吃程序注记。

另外钉住「给模型看的上下文仍保留引用关系」—— 剥掉会让模型更分不清谁在说话。

运行方式（项目根目录）：
    python tests/test_quote_shell_hygiene.py
"""
import importlib
import io
import os
import sys
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import message_shell as ms  # noqa: E402

PASS = 0
FAIL = 0
FAILURES = []


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        FAILURES.append(f"{name} {extra}")
        print(f"  [FAIL] {name} {extra}")


def read(rel):
    with io.open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


# ── 真机形态（号码 / 昵称均为占位） ────────────────────────────────────
REAL_QUOTE = ("[占位群友A·g7h8] [引用:我(Bot)｜引用我，必回] 先把出处给我，再吹\n"
              "[当前] 帮我查一下休格")
THIN_RESULT = ("【先把出处给我，再吹】搜索结果：\n"
               "1. [hanyuguoxue.com] 先的意思,先的解释,先的拼音...\n"
               "2. [hgcha.com] 《先》的拼音,先字的意思、组词、部首、笔画...\n"
               "【结果质量：偏薄】最高相关度只有 0.60（优质线 1.5）。"
               "不要凭常识补全没查到的部分。")
GOOD_RESULT = "【休格】搜索结果：\n1. 休格是某作品里的角色，出处见官方设定集。\n"


# ══════════════════════════════════════════════════════════════════════
# 1. 单一拆解口：三种语义
# ══════════════════════════════════════════════════════════════════════
def test_utterance():
    check("单条消息只留 [当前] 之后", ms.utterance(REAL_QUOTE) == "帮我查一下休格",
          repr(ms.utterance(REAL_QUOTE)))
    check("不含被引原文 / 程序注记",
          all(x not in ms.utterance(REAL_QUOTE)
              for x in ("先把出处", "[引用", "[当前]", "别抢话")))
    check("私聊裸 [引用] 形态",
          ms.utterance("[引用] 旧话\n[当前] 这句") == "这句",
          repr(ms.utterance("[引用] 旧话\n[当前] 这句")))
    check("只有引用没有 [当前] → 剥壳留正文",
          ms.utterance("[引用:甲｜别抢话] 只有引用没有当前标记")
          == "只有引用没有当前标记")
    check("普通消息原样", ms.utterance("在吗") == "在吗")
    check("空串安全", ms.utterance("") == "" and ms.utterance(None) == "")


def test_for_log():
    batch = ("[占位群友A·g7h8] [引用:占位群友B｜他在引用占位群友B，"
             "**不是在叫我**，别抢话] 总不可能整个高速只有这一座桥嘛\n"
             "[当前] @占位群友C  互联网不是")
    got = ms.for_log(batch)
    check("日志里没有 [引用:…] 注记", "[引用" not in got, repr(got))
    check("日志里没有 [当前] 标记", "[当前]" not in got, repr(got))
    check("日志里没有「别抢话」指令文字", "别抢话" not in got, repr(got))
    check("日志保留两行真实正文",
          "总不可能整个高速只有这一座桥嘛" in got and "互联网不是" in got,
          repr(got))


def test_searchable():
    got = ms.searchable(REAL_QUOTE)
    check("检索输入只留本次发言", got == "帮我查一下休格", repr(got))
    check("检索输入不含 Bot 自己那句「先把出处给我，再吹」",
          "先把出处给我" not in got, repr(got))
    check("检索输入不含 [引用] / [当前]",
          "[引用" not in got and "[当前]" not in got)

    # 没有 [当前] 的批次：逐行丢掉 Bot 自己的行 / 注入块 / 引用行
    mixed = ("[09:58] 我(Bot): 先把出处给我，再吹\n"
             "【群聊近况（真实流水，最新在后）】\n"
             "[占位群友A] [引用:某人｜…] 被引原文\n"
             "[占位群友B] 帮我查一下休格")
    got2 = ms.searchable(mixed)
    check("Bot 自己的历史行被丢掉", "先把出处给我" not in got2, repr(got2))
    check("近况块被丢掉", "群聊近况" not in got2, repr(got2))
    check("引用行被丢掉", "被引原文" not in got2, repr(got2))
    check("真实提问还在", "帮我查一下休格" in got2, repr(got2))


# ══════════════════════════════════════════════════════════════════════
# 2. web_search：检索词来源 + 低质量硬闸
# ══════════════════════════════════════════════════════════════════════
def _ws():
    return importlib.import_module("plugins.web_search")


def _call(ws, message, result):
    """跑一次 before_reply，全程不触网。"""
    with mock.patch.object(ws, "search_web", return_value=result) as sw, \
            mock.patch("search_prefs.prefer_model_search", return_value=False), \
            mock.patch("search_prefs.shape_query", side_effect=lambda q: q):
        out = ws.before_reply(message=message, group_id="10001", context={})
    return out, sw


def test_before_reply():
    ws = _ws()
    out, sw = _call(ws, REAL_QUOTE, GOOD_RESULT)
    check("真机形态仍能触发搜索", isinstance(out, dict), repr(out))
    if isinstance(out, dict):
        q = (out.get("meta") or {}).get("query") or ""
        check("★ 检索词不含被引原文那句 Bot 自己的话",
              "先把出处给我" not in q, repr(q))
        check("★ 检索词来自本次发言", "休格" in q, repr(q))
        check("正常结果照常注入（不误伤）", "休格是某作品里的角色" in out["content"])
    check("确实调了一次自家搜索", sw.call_count == 1)

    # 双保险：_pick_search_query 自己也不该挑程序壳行
    q2 = ws._pick_search_query(
        "[引用:我(Bot)｜…] 先把出处给我，再吹\n帮我查一下休格")
    check("★ _pick_search_query 跳过引用行",
          "先把出处给我" not in q2 and "休格" in q2, repr(q2))

    # 没有本次发言（整批只有被引原文）→ 不该搜
    out2, sw2 = _call(ws, "[引用:某｜…] 先把出处给我，再吹", GOOD_RESULT)
    check("只有被引原文时不触发搜索", out2 is None, repr(out2))
    check("没调搜索", sw2.call_count == 0)


def test_thin_result_gate():
    ws = _ws()
    out, _ = _call(ws, "帮我查一下休格", THIN_RESULT)
    check("薄结果仍返回 inject（本轮算联网过）", isinstance(out, dict))
    if isinstance(out, dict):
        c = out["content"]
        check("★ 字典页正文没有进 prompt", "hanyuguoxue" not in c and "hgcha" not in c,
              repr(c[:120]))
        check("★ 质量标注原文也没有进 prompt", "结果质量：偏薄" not in c)
        check("★ 明确要求直接说没查到", "我没查到，不确定" in c, repr(c[:120]))
        check("★ 明确禁止补具体细节", "不要给出具体的作品名" in c)

    out2, _ = _call(ws, "帮我查一下休格", GOOD_RESULT)
    if isinstance(out2, dict):
        check("优质结果不受硬闸影响", "休格是某作品里的角色" in out2["content"])


# ══════════════════════════════════════════════════════════════════════
# 3. 静态闸：下游不许再退回「直接传带壳 text」
# ══════════════════════════════════════════════════════════════════════
def test_wiring():
    bc = read("bot_core.py")
    check("存档/学习/记忆共用 utterance（至少 4 处）",
          bc.count("message_shell.utterance(") >= 4,
          f"实际 {bc.count('message_shell.utterance(')}")
    check("决策摘要日志走 for_log（2 处）",
          bc.count("message_shell.for_log(") >= 2,
          f"实际 {bc.count('message_shell.for_log(')}")
    check("_strip_bot_annotation 收敛为薄封装",
          "return message_shell.utterance(text)" in bc)
    check("群/私聊 chat_style 都不再直接吃 text",
          "_cs_mod.auto_learn(str(group_id), sender, _user_utterance)" in bc
          and "message_shell.utterance(text))" in bc)
    check("群/私聊 memory_feed 都用纯本次发言",
          '"text": _user_utterance,' in bc and '"text": _pm_utterance,' in bc)
    check("私聊日志也用纯本次发言",
          'save_chat_log(sender, "对方", _pm_utterance)' in bc)
    # 给模型看的上下文必须保留壳，别为了「干净」把引用关系一起剥掉
    check("给模型看的 GROUP_TIMELINE 仍存带壳 text",
          "GROUP_TIMELINE[group_id].append((current_time, sender, sender_nick, text))" in bc)

    ws_src = read("plugins/web_search/__init__.py")
    check("web_search 用 searchable 切本次发言",
          "from message_shell import searchable" in ws_src
          and "_utterance = searchable(msg)" in ws_src)
    check("低质量结果有硬闸", "_thin = bool(re.search(" in ws_src
          and "不要给出具体的作品名" in ws_src)


def main():
    test_utterance()
    test_for_log()
    test_searchable()
    test_before_reply()
    test_thin_result_gate()
    test_wiring()
    print("\n" + "=" * 58)
    print(f"通过 {PASS} / 失败 {FAIL}")
    if FAILURES:
        print("失败项：")
        for f in FAILURES:
            print("  -", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
