# -*- coding: utf-8 -*-
"""P1 / P2 / F-3 回归（2026-11-12）。

覆盖四条互不相同的边界：

  #85 `web_search.after_reply` 从「模糊词表」扩到
      「确定性引述 + 本轮无有效结果」—— 真机 09:56 的
      「搜了，…只有谢霆锋的《逆行》，2026 年那版演唱会 MV」一个模糊词都没有。
  #86 主链 `search_web` 工具路径的质量硬闸：薄结果在路由、工具出口、
      搜索标记循环三处都不许把正文交给模型。
  #87 P2 口径：群聊非管理语境不注入「主人」措辞；近况块保留引用对象、
      去掉「别抢话」这类程序指令。
  #88 存量脏特征审计脚本：只读、能列出脏条目、号码掩码。

运行方式（项目根目录）：
    python tests/test_p1_guard_and_context.py
"""
import importlib
import io
import os
import re
import sqlite3
import sys
import tempfile
import time
from pathlib import Path
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import bot_core  # noqa: E402
import message_shell as ms  # noqa: E402

sys.path.insert(0, HERE)
from _quiet_exit import quiet_exit  # noqa: E402
quiet_exit()

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


def _ws():
    return importlib.import_module("plugins.web_search")


GID = "10001"
CTX = {"sender": "占位用户"}

# 占位号，仅用于验证掩码，不对应任何真实账号
FAKE_ID = "1234567890"

THIN_RESULT = ("【先】搜索结果：\n"
               "1. [hanyuguoxue.com] 先的意思,先的解释,先的拼音...\n"
               "【结果质量：偏薄】最高相关度只有 0.60（优质线 1.5）。")
GOOD_RESULT = "【休格】搜索结果：\n1. 休格是某作品里的角色。"

DEFINITE = [
    "搜了，「逆行食性」能被搜到的只有谢霆锋的《逆行》，2026 年那版演唱会 MV。没有叫这名字的作品。",
    "搜到了，但全是谢霆锋《逆行》的 MV，2026 年 5 月那版里面还带 AI 修复；跟作品没关系。",
    "弑君者、鸡蛋伯爵，绕了半天全是方舟的。",
]
SAFE_REPLY = [
    "这个我联网查了，没看到确切说法，就不瞎说了。",
    "我搜了，没查到，不确定。",
    "没听过这名字，你自己看过吗",
    "你们全是笨蛋",
    "我猜大概是这样吧",
    "刚看到 2026 年的旧新闻，笑死",
    "搜了一圈还是空的，炼金术士上千个。哪个",
]


# ══════════════════════════════════════════════════════════════════════
# #85 确定性引述幻觉闸
# ══════════════════════════════════════════════════════════════════════
def _arm(ws, valid):
    ws._clear_round(GID, CTX)
    ws._mark_injected(GID, CTX, valid=valid)


def _after(ws, reply):
    return ws.after_reply(message="帮我查一下休格", reply=reply,
                          group_id=GID, context=CTX)


def test_definite_citation_pattern():
    ws = _ws()
    for s in DEFINITE:
        check(f"★ 真机确定性编造命中：{s[:12]}",
              ws._is_definite_citation(s) is True, repr(s[:20]))
    for s in SAFE_REPLY:
        check(f"正常 / 守规矩回复不误伤：{s[:12]}",
              ws._is_definite_citation(s) is False, repr(s[:20]))
    check("超长段不整句判定",
          ws._is_definite_citation("搜了，《X》" + "啊" * 500) is False)
    check("空串不判", ws._is_definite_citation("") is False)
    check("模糊词老判据仍在", ws._is_uncertain_reply("应该是这样吧") is True)


def test_after_reply_state_machine():
    ws = _ws()
    good_cfg = {"block_uncertain_reply": True}
    with mock.patch.object(ws, "get_config", return_value=good_cfg):
        ws._clear_round(GID, CTX)
        check("没联网 → 放行（人格的一部分）", _after(ws, DEFINITE[0]) is None)

        _arm(ws, valid=False)
        r = _after(ws, DEFINITE[0])
        check("★ 薄结果 + 确定性引述 → 拦截", isinstance(r, dict), repr(r))
        if isinstance(r, dict):
            check("拦截 reason 标明确定性引述",
                  r["meta"]["reason"] == "definite_citation_without_valid_result",
                  repr(r["meta"]))
            check("拦截动作是 override", r["action"] == "override")

        _arm(ws, valid=False)
        r2 = _after(ws, "应该是这样吧")
        check("薄结果 + 模糊词仍走老闸",
              isinstance(r2, dict)
              and r2["meta"]["reason"] == "uncertain_after_search", repr(r2))

        _arm(ws, valid=True)
        check("★ 有合格结果 → 同样句式不拦（正常答题零影响）",
              _after(ws, DEFINITE[0]) is None)

        _arm(ws, valid=False)
        check("薄结果 + 守规矩回复不拦", _after(ws, SAFE_REPLY[0]) is None)

    with mock.patch.object(ws, "get_config",
                           return_value={"block_uncertain_reply": False}):
        _arm(ws, valid=False)
        check("插件开关关掉 → 一律不拦", _after(ws, DEFINITE[0]) is None)


def test_before_reply_records_quality_and_clears_round():
    ws = _ws()

    def _call(message, result):
        with mock.patch.object(ws, "search_web", return_value=result), \
                mock.patch("search_prefs.prefer_model_search", return_value=False), \
                mock.patch("search_prefs.shape_query", side_effect=lambda q: q):
            return ws.before_reply(message=message, group_id=GID, context=CTX)

    _call("帮我查一下休格", THIN_RESULT)
    check("★ 薄结果轮次记 valid=False",
          ws._round_has_valid_result(GID, CTX) is False)
    _call("帮我查一下休格", GOOD_RESULT)
    check("合格结果轮次记 valid=True",
          ws._round_has_valid_result(GID, CTX) is True)

    # 上一轮薄结果不能挂到下一轮不联网的普通闲聊上
    _call("帮我查一下休格", THIN_RESULT)
    check("先确认这一轮是薄结果", ws._round_has_valid_result(GID, CTX) is False)
    out = _call("在吗哈哈哈", GOOD_RESULT)
    check("普通闲聊不触发搜索", out is None, repr(out))
    check("★ 本轮不联网 → 旧标记已清", ws._recently_injected(GID, CTX) is False)
    check("旧标记清掉后状态回到放行", ws._round_has_valid_result(GID, CTX) is True)

    ws._clear_round(GID, CTX)


# ══════════════════════════════════════════════════════════════════════
# #86 主链 search_web 质量硬闸
# ══════════════════════════════════════════════════════════════════════
def test_thin_result_predicate():
    for tag in ("偏薄", "年份对不上", "几乎没有可用内容"):
        s = f"【x】搜索结果：\n1. 某条\n【结果质量：{tag}】理由。"
        check(f"质量标注「{tag}」被认出", bot_core._is_thin_search_result(s) is True)
    check("正常结果不误判", bot_core._is_thin_search_result(GOOD_RESULT) is False)
    check("空值不误判", bot_core._is_thin_search_result(None) is False)
    check("★ 两个判据不混：薄结果不算失败提示",
          bot_core._is_search_failure(THIN_RESULT) is False)
    check("失败提示仍被认出",
          bot_core._is_search_failure(bot_core._search_failure_text("x")) is True)


def test_tool_web_gate():
    orig = bot_core.search_web
    try:
        bot_core.search_web = lambda q: THIN_RESULT
        out = bot_core._tool_web("休格")
        check("★ 工具出口判失败", "❌" in out, repr(out[:80]))
        check("★ 字典页正文一行都没给模型", "hanyuguoxue" not in out, repr(out))
        check("★ 质量标注原文也没给", "结果质量" not in out, repr(out))
        check("明确禁止补作品名/年份", "别给作品名、人名、年份" in out, repr(out))

        bot_core.search_web = lambda q: GOOD_RESULT
        out2 = bot_core._tool_web("休格")
        check("正常结果不误伤", "❌" not in out2 and "休格是某作品里的角色" in out2)
    finally:
        bot_core.search_web = orig


def test_search_tag_loop_gate():
    orig_s, orig_c = bot_core.search_web, bot_core.call_deepseek
    prompts, queries = [], []
    try:
        bot_core.search_web = lambda q: (queries.append(q), THIN_RESULT)[1]
        bot_core.call_deepseek = lambda msg, tools, **kw: (
            prompts.append(msg), "好了")[1]
        bot_core._apply_search_tag_loop(
            "[搜索:休格]", "休格是谁", group_id=0, is_group=False,
            temperature=0.8)
        check("确实重答过一次", len(prompts) == 1, repr(len(prompts)))
        p = prompts[0] if prompts else ""
        check("★ 薄结果按「没成功」处理", "没成功" in p, repr(p[:100]))
        check("★ 不给「请基于搜索结果作答」指令",
              "请直接基于以上搜索结果回答用户问题" not in p)
        check("★ 字典页正文没进重答 prompt", "hanyuguoxue" not in p)
        check("薄结果只搜一次就停", len(queries) == 1, repr(queries))
    finally:
        bot_core.search_web, bot_core.call_deepseek = orig_s, orig_c


def test_search_web_route_gate():
    """自家搜索薄结果 → 直接按没查到返回，不再重复烧一次 DeepSeek。"""
    import web_access
    with mock.patch.object(web_access, "search_web", return_value=THIN_RESULT), \
            mock.patch("search_prefs.prefer_model_search", return_value=False), \
            mock.patch.object(bot_core, "_search_via_deepseek") as ds:
        out = bot_core.search_web("帮我查一下休格")
        check("★ 路由层把薄结果换成失败提示",
              bot_core._is_search_failure(out) is True, repr(out[:100]))
        check("★ 正文没从路由层漏出去", "hanyuguoxue" not in str(out))
        check("不再重复调 DeepSeek 兜底", ds.call_count == 0, repr(ds.call_count))


# ══════════════════════════════════════════════════════════════════════
# #87 P2 两处口径
# ══════════════════════════════════════════════════════════════════════
def test_for_context():
    raw = ("[引用:某群友｜他在引用某群友，**不是在叫我**，别抢话] "
           "[图片]\n[当前] @某人 什么意思")
    got = ms.for_context(raw)
    check("★ 保留引用对象", "[引用:某群友]" in got, repr(got))
    check("★ 去掉程序指令文字", "别抢话" not in got and "不是在叫我" not in got,
          repr(got))
    check("★ 被引原文与本次发言都还在",
          "[图片]" in got and "[当前] @某人 什么意思" in got, repr(got))
    check("半角分隔符同样处理",
          "别抢话" not in ms.for_context("[引用:张三|…别抢话] 原文"))
    check("普通文本恒等变换", ms.for_context("今天真热啊") == "今天真热啊")
    check("幂等", ms.for_context(got) == got)
    check("已在 message_shell 的公开出口里", "for_context" in ms.__all__)


def test_group_context_block():
    orig_t = dict(bot_core.GROUP_TIMELINE)
    orig_b = dict(bot_core.GROUP_BOT_REPLIES)
    try:
        bot_core.GROUP_TIMELINE["g"] = [
            (time.time() - 60, "占位群友", "占位群友",
             "[引用:张三｜**不是在叫我**，别抢话] 被引原文\n[当前] 我这句"),
        ]
        bot_core.GROUP_BOT_REPLIES["g"] = []
        out = bot_core._build_group_context_block("g", "999")
        check("近况块仍带引用对象", "[引用:张三]" in out, repr(out))
        check("★ 近况块不出现「别抢话」", "别抢话" not in out, repr(out))
        check("★ 近况块不出现「不是在叫我」", "不是在叫我" not in out, repr(out))
        check("本次发言保留", "我这句" in out, repr(out))
    finally:
        bot_core.GROUP_TIMELINE.clear()
        bot_core.GROUP_TIMELINE.update(orig_t)
        bot_core.GROUP_BOT_REPLIES.clear()
        bot_core.GROUP_BOT_REPLIES.update(orig_b)


def _cap(is_group, user_msg, adm=False):
    cfg = {"capability_view_enable": False, "persona_relation_enable": False,
           "group_tool_allowlist": ["core"]}
    with mock.patch.object(bot_core, "_config", cfg), \
            mock.patch.object(bot_core, "_get_admin_qqs", return_value={"999"}), \
            mock.patch.object(bot_core, "resolve_qq_nick", return_value="占位管理员"), \
            mock.patch.object(bot_core, "_is_authorized", return_value=adm), \
            mock.patch.object(bot_core, "BOT_QQ", "0"):
        return bot_core._capability_block(
            is_group=is_group, group_id="g", sender="999",
            tool_names=(), user_msg=user_msg)


def test_capability_owner_wording():
    plain = _cap(True, "今天真热啊")
    check("★ 群聊普通闲聊不出现「主人」", "主人" not in plain, repr(plain))
    check("★ 也不出现「不是主人」的否定句", "**不是**" not in plain, repr(plain))
    check("★ 也不提「谁都能让我干活」", "谁都能让我干活" not in plain, repr(plain))
    check("还是会说清现在是谁在说话", "现在跟你说话的是" in plain, repr(plain))
    check("仍保留「别答应下来再装失败」的正确口径",
          "别答应下来再装失败" in plain, repr(plain))

    private = _cap(False, "今天真热啊")
    check("★ 私聊逐字节保持原措辞（零行为变化）", "（主人）" in private,
          repr(private[:120]))

    admin_msg = _cap(True, "帮我改配置")
    check("确实在谈管理操作时恢复身份措辞", "（主人）" in admin_msg,
          repr(admin_msg[:120]))

    adm_sender = _cap(True, "今天真热啊", adm=True)
    check("管理员本人在场时恢复「是主人」", "**是主人**" in adm_sender,
          repr(adm_sender[:120]))

    check("白名单说明不再借「主人」措辞",
          "让主人加进" not in read("bot_core.py"))


# ══════════════════════════════════════════════════════════════════════
# #88 存量脏特征审计脚本
# ══════════════════════════════════════════════════════════════════════
def test_taint_audit_script():
    src = read("scripts/audit_style_taint.py")
    write_sql = re.compile(
        r"INSERT\s+INTO|UPDATE\s+\w+\s+SET|DELETE\s+FROM|DROP\s+TABLE|"
        r"CREATE\s+TABLE|REPLACE\s+INTO", re.I)
    check("★ 没有任何写 SQL", write_sql.search(src) is None,
          repr(write_sql.findall(src)[:4]))
    check("★ 只读打开 SQLite", "mode=ro" in src)
    check("★ 不 import 会建库的插件模块",
          'import chat_style' not in src and 'from plugins.ego' not in src)
    check("号码掩码存在", "_NUM_RE" in src and "****" in src)

    sys.path.insert(0, os.path.join(ROOT, "scripts"))
    mod = importlib.import_module("audit_style_taint")

    with tempfile.TemporaryDirectory(prefix="eb_p1_") as td:
        root = Path(td)
        d = root / "plugins" / "ego" / "chat_style" / "data"
        d.mkdir(parents=True)
        db = d / "chat_style.db"
        con = sqlite3.connect(db)
        con.executescript("""
            CREATE TABLE style_samples (id INTEGER PRIMARY KEY, scope_id TEXT,
              user_id TEXT, message TEXT, reply TEXT, timestamp REAL);
            CREATE TABLE style_features (id INTEGER PRIMARY KEY, scope_id TEXT,
              user_id TEXT, feature_type TEXT, feature_value TEXT, weight REAL,
              use_count INTEGER, last_used REAL, created_at REAL);
        """)
        con.execute("INSERT INTO style_samples VALUES (1,?,?,?,'',0)",
                    (FAKE_ID, FAKE_ID, "[引用:某｜别抢话] 原文\n[当前] 本次发言"))
        con.execute("INSERT INTO style_features VALUES (1,?,?,?,?,?,?,0,0)",
                    (FAKE_ID, FAKE_ID, "口头禅", "当前", 0.8, 3))
        con.commit()
        con.close()

        report = mod.build_report(root, limit=20)
        check("报告标出只列不删", "只列不删" in report)
        check("★ 能列出被污染的样本", "style_samples | 1" in report,
              repr(report[:400]))
        check("★ 能列出已蒸出来的脏特征", "style_features | 1" in report,
              repr(report[:400]))
        check("★ 假号被掩码", "12****90" in report, repr(report[:800]))
        check("★ 原文里的假号没泄漏", FAKE_ID not in report)
        check("personality.txt 默认跳过不误报",
              "默认不扫描" in report and "plugins/personality.txt | 0" in report,
              repr(report[:400]))


def main():
    test_definite_citation_pattern()
    test_after_reply_state_machine()
    test_before_reply_records_quality_and_clears_round()
    test_thin_result_predicate()
    test_tool_web_gate()
    test_search_tag_loop_gate()
    test_search_web_route_gate()
    test_for_context()
    test_group_context_block()
    test_capability_owner_wording()
    test_taint_audit_script()
    print("\n" + "=" * 58)
    print(f"通过 {PASS} / 失败 {FAIL}")
    if FAILURES:
        print("失败项：")
        for f in FAILURES:
            print("  -", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
