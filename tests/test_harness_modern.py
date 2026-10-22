# -*- coding: utf-8 -*-
"""tests/test_harness_modern.py —— harness 现代化基建回归（时间分级 / 工具注册表 /
Skill 三级披露 / MCP hub / chat_fn 包装）。

运行：python tests/test_harness_modern.py
"""
import io
import json
import os
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from harness import time_ctx, tool_registry as tr, skill_loader as sk, mcp_hub, bot_hook

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


def _mk_schema(name, desc="d"):
    return {"type": "function", "function": {
        "name": name, "description": desc,
        "parameters": {"type": "object", "properties": {}, "required": []}}}


# ══════════════════════════════════════════════════════════════════
# 1. time_ctx 分级
# ══════════════════════════════════════════════════════════════════
def test_time_ctx():
    tr_ = time_ctx.TimeContext("t1")
    t = time.mktime((2026, 3, 5, 9, 30, 0, 0, 0, -1))
    c1, f1 = tr_.parts(t)
    check("time: 首次给粗粒度", c1 and "2026年3月5日" in c1, c1)
    check("time: 首次给精粒度", f1 and "09:30" in f1, f1)
    check("time: 粗粒度含时段", "上午" in c1, c1)

    # ★ 2026-10-22 语义修复：parts() 必须**总是**返回文本。
    #   旧断言是"同分钟返回空串"—— 那条约束建立在"prompt 会累积"的错误前提上，
    #   实际每轮从头重建，返回空串 = 这一轮 prompt 里没有时间（"没有时间观念"的根因）。
    c2, f2 = tr_.parts(t + 5)
    check("time: 同分钟也照样给粗", "3月5日" in (c2 or ""), c2)
    check("time: 同分钟也照样给精", "09:30" in (f2 or ""), f2)

    # 同一时段跨分钟：精粒度跟上，粗粒度保持（时段没变）
    c3, f3 = tr_.parts(t + 70)
    check("time: 跨分钟精粒度更新", "09:31" in f3, f3)
    check("time: 同时段粗粒度稳定", c3 == c1, repr(c3))

    # ★ 时段变了必须刷新粗粒度（否则"上午"会挂到晚上，跟精确时间打架）
    t_noon = time.mktime((2026, 3, 5, 12, 30, 0, 0, 0, -1))
    c4, f4 = tr_.parts(t_noon)
    check("time: 跨时段刷粗", "中午" in (c4 or ""), c4)
    check("time: 跨时段不残留上午", "上午" not in (c4 or ""), c4)
    check("time: 跨时段精粒度同步", "12:30" in f4, f4)

    # 跨天 → 日期变
    t2 = t + 86400
    c5, f5 = tr_.parts(t2)
    check("time: 跨天刷粗", c5 and "3月6日" in c5, c5)

    # need_precise 向后兼容（现在行为与默认一致）
    _c6, f6 = tr_.parts(t2, need_precise=True)
    check("time: need_precise 兼容", "09:30" in f6, f6)

    # 时段边界
    n0, _ = time_ctx.period_of(time.mktime((2026, 1, 1, 0, 0, 0, 0, 0, -1)))
    n23, _ = time_ctx.period_of(time.mktime((2026, 1, 1, 23, 59, 0, 0, 0, -1)))
    n12, _ = time_ctx.period_of(time.mktime((2026, 1, 1, 12, 0, 0, 0, 0, -1)))
    check("time: 0点=凌晨", n0 == "凌晨", n0)
    check("time: 23点=深夜", n23 == "深夜", n23)
    check("time: 12点=中午", n12 == "中午", n12)


def test_time_mismatch():
    """时间前提核对：问候语与钟点矛盾时必须给出可执行提示。"""
    def at(h, m=0):
        return time.mktime((2026, 10, 22, h, m, 0, 0, 0, -1))

    # ── 该报的矛盾 ──
    for msg, ts, want in (
        ("晚上好", at(10, 44), "上午"),
        ("晚上好啊", at(9, 15), "上午"),
        ("晚安", at(14, 0), "下午"),
        ("早上好", at(21, 30), "晚上"),
        ("早安！", at(23, 10), "深夜"),     # 23:10 时 period_of 判"深夜"，不是"晚上"
        ("中午好", at(8, 0), "上午"),
        ("下午好", at(9, 0), "上午"),
    ):
        note = time_ctx.mismatch_note(msg, ts=ts)
        check(f"mismatch: 「{msg}」@{int((ts % 86400)//3600)}时 要指出",
              bool(note) and want in note, note)
        # 提示必须是**可执行**的，不能只说"对不上"
        if note:
            check(f"mismatch: 「{msg}」提示含时间", "：" in note and "别顺着答" in note, note[:60])

    # ── 口语钟点：人不会说"这才23点吧" ──
    for h, want in ((0, "十二点"), (9, "九点"), (10, "十点"), (11, "十一点"),
                    (12, "十二点"), (13, "下午一点"), (17, "下午五点"),
                    (20, "晚上八点"), (23, "晚上十一点")):
        got = time_ctx._spoken_hour(h)
        check(f"spoken: {h}点→{want}", got == want, got)

    # ── 不该误报的（时段对得上 / 不是单纯问候）──
    for msg, ts in (
        ("晚上好", at(21, 0)),
        ("晚上好", at(0, 30)),      # 凌晨说晚上好勉强算对（还没睡）
        ("早上好", at(7, 0)),
        ("早安", at(6, 30)),
        ("中午好", at(11, 30)),
        ("下午好", at(15, 0)),
        ("晚安", at(23, 50)),
        ("昨天他跟我说晚上好", at(10, 0)),   # 叙述，不是问候
        ("晚上好各位，今天开会", at(10, 0)),  # 带内容，不判
        ("", at(10, 0)),
    ):
        note = time_ctx.mismatch_note(msg, ts=ts)
        check(f"mismatch: 「{msg}」不该报", note == "", repr(note))


def test_time_injection_reaches_private_chat():
    """源码级守卫：时间注入不得只在群聊分支里。

    这是"没有时间观念"的直接成因 —— 10:44 私聊说「晚上好」，prompt 里
    压根没有"现在几点"，模型只能顺着答。
    """
    import io as _io
    import os as _os
    _root = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
    with _io.open(_os.path.join(_root, "bot_core.py"), encoding="utf-8") as f:
        src = f.read()
    seg = src[src.find("def _call_deepseek_sync"):]
    seg = seg[:seg.find("messages = _build_cached_messages")]
    check("time: 私聊/群聊都走 time_parts", "time_parts(_time_key)" in seg, seg[:0])
    check("time: 时间 key 区分私聊群聊",
          'f"g_{group_id}" if (is_group and group_id) else f"p_{target_qq}"' in seg, "")
    # 时间注入块必须在 `if is_group and group_id:` 之外（缩进 4 空格）
    _line = [ln for ln in seg.splitlines() if "time_parts(_time_key)" in ln]
    check("time: 注入块不在群聊内层",
          bool(_line) and _line[0].startswith("        _coarse"), repr(_line))


# ══════════════════════════════════════════════════════════════════
# 2. tool_registry
# ══════════════════════════════════════════════════════════════════
def test_tool_registry():
    tr.reset()
    # 注册顺序故意打乱，验证 resolve 的**确定性排序**
    tr.register("take_screenshot", _mk_schema("take_screenshot"), groups=(tr.GROUP_DESKTOP,))
    tr.register("search_web", _mk_schema("search_web"), groups=(tr.GROUP_CORE,))
    tr.register("web_operate", _mk_schema("web_operate"), groups=(tr.GROUP_WEB,))
    tr.register("get_group_members", _mk_schema("get_group_members"), groups=(tr.GROUP_GROUP,))

    names = ["web_operate", "search_web", "take_screenshot", "get_group_members"]
    r1 = tr.resolve(names)
    r2 = tr.resolve(list(reversed(names)))
    n1 = [s["function"]["name"] for s in r1]
    n2 = [s["function"]["name"] for s in r2]
    check("toolreg: resolve 顺序稳定", n1 == n2, f"{n1} vs {n2}")
    check("toolreg: core 在最前", n1[0] == "search_web", n1)
    check("toolreg: group 在 web 前", n1.index("get_group_members") < n1.index("web_operate"), n1)

    # 去重
    r3 = tr.resolve(["search_web", "search_web", "search_web"])
    check("toolreg: 去重", len(r3) == 1, len(r3))

    # 未知工具静默跳过 / strict 抛错
    r4 = tr.resolve(["search_web", "nope_tool"])
    check("toolreg: 未知静默跳过", len(r4) == 1, len(r4))
    try:
        tr.resolve(["nope_tool"], strict=True)
        check("toolreg: strict 抛错", False, "没抛")
    except tr.ToolNotFound:
        check("toolreg: strict 抛错", True)

    # 分组解析
    g = tr.resolve_groups([tr.GROUP_CORE, tr.GROUP_GROUP])
    gn = [s["function"]["name"] for s in g]
    check("toolreg: 按分组解析", set(gn) == {"search_web", "get_group_members"}, gn)

    # 惰性 loader
    tr.register_loader("lazy_tool", lambda: _mk_schema("lazy_tool"),
                       groups=(tr.GROUP_MCP,))
    check("toolreg: 惰性未加载时无 schema", tr.stats()["schema_not_loaded"] == ["lazy_tool"],
          tr.stats()["schema_not_loaded"])
    rl = tr.resolve(["lazy_tool"], lazy=True)
    check("toolreg: 惰性可加载", len(rl) == 1 and rl[0]["function"]["name"] == "lazy_tool", rl)
    check("toolreg: 加载后缓存", tr.resolve(["lazy_tool"]) != [])

    # 名字前缀推断分组
    tr.register("mcp__fs__read", _mk_schema("mcp__fs__read"))
    check("toolreg: mcp 前缀推断", tr.GROUP_MCP in tr.groups_of("mcp__fs__read"),
          tr.groups_of("mcp__fs__read"))
    tr.register("skill__load", _mk_schema("skill__load"))
    check("toolreg: skill 前缀推断", tr.GROUP_SKILL in tr.groups_of("skill__load"),
          tr.groups_of("skill__load"))

    # register 校验
    try:
        tr.register("wrong", _mk_schema("other"))
        check("toolreg: 名字不一致报错", False, "没抛")
    except ValueError:
        check("toolreg: 名字不一致报错", True)
    try:
        tr.register("x", {"type": "function", "function": {}})
        check("toolreg: 缺 name 报错", False, "没抛")
    except ValueError:
        check("toolreg: 缺 name 报错", True)

    # schema 冻结（外部改动不污染）
    s = tr.resolve(["search_web"])[0]
    s["function"]["description"] = "HACKED"
    s2 = tr.resolve(["search_web"])[0]
    check("toolreg: schema 隔离", s2["function"]["description"] != "HACKED",
          s2["function"]["description"])

    # 指纹
    sig, cnt = tr.serialize_signature(names)
    check("toolreg: 指纹含全部", cnt == 4 and "search_web" in sig, (sig, cnt))

    # make_loader
    ld = tr.make_loader(["web_operate"])
    out = ld()
    check("toolreg: make_loader", len(out) == 1, out)


# ══════════════════════════════════════════════════════════════════
# 3. skill_loader 三级披露
# ══════════════════════════════════════════════════════════════════
def test_skill_loader():
    tmp = tempfile.mkdtemp(prefix="eb_skill_")
    try:
        # skill A：带 frontmatter
        a = os.path.join(tmp, "pdf-tools")
        os.makedirs(os.path.join(a, "references"), exist_ok=True)
        os.makedirs(os.path.join(a, "scripts"), exist_ok=True)
        with open(os.path.join(a, "SKILL.md"), "w", encoding="utf-8") as f:
            f.write("---\nname: PDF 工具箱\ndescription: 处理 PDF（合并/拆分/表单）\n"
                    "triggers: [pdf, 合并, 拆分]\n---\n\n# 用法\n第一步：xxx\n第二步：yyy\n")
        with open(os.path.join(a, "references", "api.md"), "w", encoding="utf-8") as f:
            f.write("API 详情内容")
        with open(os.path.join(a, "scripts", "run.py"), "w", encoding="utf-8") as f:
            f.write("print(1)")

        # skill B：无 frontmatter（兜底）
        b = os.path.join(tmp, "no-fm")
        os.makedirs(b, exist_ok=True)
        with open(os.path.join(b, "SKILL.md"), "w", encoding="utf-8") as f:
            f.write("# 无元数据技能\n这是第一句话说明。第二句。\n")

        # 非法目录（无 SKILL.md）应被忽略
        os.makedirs(os.path.join(tmp, "empty-dir"), exist_ok=True)

        sk.reset_cache()
        found = sk.discover([tmp], force=True)
        check("skill: 发现两个技能", set(found.keys()) == {"pdf-tools", "no-fm"}, list(found.keys()))

        e = found["pdf-tools"]
        check("skill: 解析 frontmatter name", e["display_name"] == "PDF 工具箱", e["display_name"])
        check("skill: 解析 description", "合并" in e["description"], e["description"])
        check("skill: 解析 triggers", e["triggers"] == ["pdf", "合并", "拆分"], e["triggers"])
        check("skill: 探测 references", e["has_references"] is True)
        check("skill: 探测 scripts", e["has_scripts"] is True)

        e2 = found["no-fm"]
        check("skill: 无 frontmatter 用首句兜底", "第一句话说明" in e2["description"], e2["description"])

        # L1
        l1 = sk.l1_block(found)
        check("skill: L1 含全部技能", "PDF 工具箱" in l1 and "无元数据技能" in l1, l1[:200])
        check("skill: L1 含触发词", "触发词" in l1 and "pdf" in l1, l1[:300])
        check("skill: L1 体积小", len(l1) < 800, len(l1))

        # L2
        r = sk.l2_body("pdf-tools")
        check("skill: L2 ok", r.get("ok") is True, r)
        check("skill: L2 含正文", "第一步" in r.get("body", ""), r.get("body"))
        check("skill: L2 不含 frontmatter", "triggers:" not in r.get("body", ""), r.get("body"))
        check("skill: L2 带资源索引", "references" in (r.get("resources") or {}),
              r.get("resources"))

        # L2 截断
        r2 = sk.l2_body("pdf-tools", max_chars=5)
        check("skill: L2 可截断", r2.get("truncated") is True and len(r2["body"]) == 5,
              (r2.get("truncated"), len(r2.get("body", ""))))

        # L2 不存在
        r3 = sk.l2_body("nope")
        check("skill: L2 不存在报错", r3.get("ok") is False, r3)

        # L3 读
        check("skill: L3 索引含文件", "references/api.md" in sk.l3_index("pdf-tools").get("references", []),
              sk.l3_index("pdf-tools"))
        r4 = sk.l3_read("pdf-tools", "references/api.md")
        check("skill: L3 读取", r4.get("ok") is True and "API 详情" in r4.get("content", ""), r4)
        # L3 越界
        r5 = sk.l3_read("pdf-tools", "../../../windows/win.ini")
        check("skill: L3 越界拒绝", r5.get("ok") is False and "越界" in r5.get("error", ""), r5)
        r6 = sk.l3_read("pdf-tools", "does/not/exist.md")
        check("skill: L3 不存在", r6.get("ok") is False, r6)

        # 缓存：第二次 discover 走缓存
        sk.reset_cache()
        f2 = sk.discover([tmp])
        check("skill: discover 缓存生效", f2 is not None)

        # L1 空根
        check("skill: 空根 L1 为空串", sk.l1_block({}) == "", repr(sk.l1_block({})))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ══════════════════════════════════════════════════════════════════
# 4. mcp_hub（无 mcp 包时的降级行为 + 命名空间）
# ══════════════════════════════════════════════════════════════════
def test_mcp_hub():
    mcp_hub.reset()

    # 命名空间
    check("mcp: namespace", mcp_hub.namespace("fs", "read") == "mcp__fs__read",
          mcp_hub.namespace("fs", "read"))
    s, t = mcp_hub.parse_namespaced("mcp__fs__read")
    check("mcp: parse", (s, t) == ("fs", "read"), (s, t))
    s2, t2 = mcp_hub.parse_namespaced("search_web")
    check("mcp: parse 非 mcp 返回空", (s2, t2) == (None, None), (s2, t2))
    s3, t3 = mcp_hub.parse_namespaced("mcp__broken")
    check("mcp: parse 畸形返回空", (s3, t3) == (None, None), (s3, t3))
    # 工具名里含 __ 的情况
    s4, t4 = mcp_hub.parse_namespaced("mcp__srv__a__b")
    check("mcp: parse 保留工具内下划线", (s4, t4) == ("srv", "a__b"), (s4, t4))

    # 未配置 server（无 mcp 包时，"包未安装"优先——两者都是正确拒绝，不做过度断言）
    mcp_hub.set_servers({})
    r = mcp_hub.list_tools("nope")
    if mcp_hub.available():
        check("mcp: 未配置报错", r.get("ok") is False and "未配置" in r.get("error", ""), r)
    else:
        check("mcp: 无包时优先报未安装", r.get("ok") is False and "未安装" in r.get("error", ""), r)

    # 配置但无 command
    mcp_hub.set_servers({"bad": {"url": "http://x"}})
    r2 = mcp_hub.list_tools("bad")
    check("mcp: 远程/无包 均拒绝", r2.get("ok") is False, r2)

    # call 未配置
    mcp_hub.set_servers({})
    r3 = mcp_hub.call("mcp__nope__x", {})
    check("mcp: call 未配置报错", r3.get("ok") is False, r3)
    # call 非法名（无论有无 mcp 包都必须先拒非法名）
    r4 = mcp_hub.call("search_web", {})
    check("mcp: call 非法名报错", r4.get("ok") is False, r4)

    # available / stats / tool_functions 不抛
    check("mcp: available 返回 bool", isinstance(mcp_hub.available(), bool))
    check("mcp: stats 不抛", isinstance(mcp_hub.stats(), dict), mcp_hub.stats())
    check("mcp: tool_functions 不抛", isinstance(mcp_hub.tool_functions(), dict))

    # 无效配置的 call 路径不炸
    r5 = mcp_hub.call("mcp__bad__go", {})
    check("mcp: 配置缺 command 的 call 不抛", r5.get("ok") is False, r5)


# ══════════════════════════════════════════════════════════════════
# 5. bot_hook（工具子集策略 + 时间 + chat_fn 包装）
# ══════════════════════════════════════════════════════════════════
def test_bot_hook():
    tr.reset()
    tr.register("search_web", _mk_schema("search_web"), groups=(tr.GROUP_CORE,))
    tr.register("take_screenshot", _mk_schema("take_screenshot"), groups=(tr.GROUP_DESKTOP,))
    tr.register("open_app", _mk_schema("open_app"), groups=(tr.GROUP_DESKTOP,))
    tr.register("web_operate", _mk_schema("web_operate"), groups=(tr.GROUP_WEB,))
    tr.register("get_group_members", _mk_schema("get_group_members"), groups=(tr.GROUP_GROUP,))

    # 闲聊 → 只有 core（这是省 token 的关键）
    n = bot_hook.tool_names_for("你好呀，今天开心吗", is_group=False)
    check("hook: 闲聊仅 core", n == ["search_web"], n)

    # 需要看屏幕 → core + desktop
    n2 = bot_hook.tool_names_for("帮我截个图看看屏幕", is_group=False)
    check("hook: 桌面关键词 → desktop", "take_screenshot" in n2 and "search_web" in n2, n2)

    # 群里问成员 → core + group
    n3 = bot_hook.tool_names_for("群里都有谁在", is_group=True)
    check("hook: 群关键词 → group", "get_group_members" in n3, n3)

    # 私聊里问成员 → **不该**给 group（私聊没意义）
    n4 = bot_hook.tool_names_for("群里都有谁在", is_group=False)
    check("hook: 私聊不给 group", "get_group_members" not in n4, n4)

    # 群聊里提到截图 → **不该**给 desktop（群聊不许操控电脑）
    n5 = bot_hook.tool_names_for("帮我截图看看桌面", is_group=True)
    check("hook: 群聊不给 desktop", "take_screenshot" not in n5, n5)

    # 网页链接 → web
    n6 = bot_hook.tool_names_for("帮我打开 https://example.com 看看", is_group=False)
    check("hook: 链接 → web", "web_operate" in n6, n6)

    # 顺序稳定
    a = bot_hook.tool_names_for("截图 打开网页 https://a.com", is_group=False)
    b = bot_hook.tool_names_for("截图 打开网页 https://a.com", is_group=False)
    check("hook: 子集顺序稳定", a == b, f"{a} vs {b}")

    # 返回 schema 形态
    sch = bot_hook.agent_tools_for("截图", is_group=False)
    check("hook: 返回 schema 列表", sch and sch[0].get("type") == "function", sch[:1])

    # extra_groups 显式追加
    n7 = bot_hook.tool_names_for("普通消息", is_group=False, extra_groups=[tr.GROUP_WEB])
    check("hook: extra_groups 生效", "web_operate" in n7, n7)

    # 时间分级（★ 2026-10-22：parts() 现在**总是**返回文本，见 time_ctx 的语义修复）
    bot_hook.reset_time_cache()
    c1, f1 = bot_hook.time_parts("s1")
    c2, f2 = bot_hook.time_parts("s1")
    check("hook: 时间首次给粗", c1 != "", repr(c1))
    check("hook: 时间二次照样给粗", c2 != "" and "【现在】" in c2, repr(c2))
    check("hook: 时间二次照样给精", f2 != "" and "【精确时间】" in f2, repr(f2))
    check("hook: 同一时段粗粒度稳定", c1 == c2, repr((c1, c2)))
    check("hook: 精粒度块不含空", bot_hook.fine_tail_block("s1", force=True) != "")

    # skill block 不抛
    check("hook: skill_l1_block 返回 str", isinstance(bot_hook.skill_l1_block(), str))

    # skill 工具函数
    fns = bot_hook.skill_tool_functions()
    check("hook: skill 工具齐备", set(fns.keys()) == {"skill__load", "skill__read"}, list(fns.keys()))
    out = fns["skill__load"]({"name": "不存在的技能"})
    check("hook: skill__load 不存在不抛", isinstance(out, str) and "不存在" in out, out[:100])

    # chat_fn 包装：轮内只解析一次
    calls = {"n": 0, "seen_tools": None}

    def _provider():
        calls["n"] += 1
        return [_mk_schema("search_web")]

    def _base(messages, tools=None, max_tokens=1500):
        calls["seen_tools"] = tools
        return ("ok", [], {})

    wrapped = bot_hook.wrap_chat_fn(_base, tools_provider=_provider)
    wrapped([{"role": "user", "content": "a"}], None)
    wrapped([{"role": "user", "content": "a"}], None)
    wrapped([{"role": "user", "content": "a"}], None)
    check("hook: chat_fn 轮内缓存 provider 只调 1 次", calls["n"] == 1, calls["n"])
    check("hook: chat_fn 传的是 schema", isinstance(calls["seen_tools"], list) and
          calls["seen_tools"] and calls["seen_tools"][0].get("type") == "function",
          calls["seen_tools"])

    # provider 抛错时降级为传入 tools
    def _bad_provider():
        raise RuntimeError("boom")

    def _base2(messages, tools=None, max_tokens=1500):
        return ("ok2", [], {})

    w2 = bot_hook.wrap_chat_fn(_base2, tools_provider=_bad_provider)
    r = w2([{"role": "user", "content": "x"}], ["search_web"])
    check("hook: provider 异常降级", r[0] == "ok2", r)


# ══════════════════════════════════════════════════════════════════
# 6. agent_loop 的按需工具 / 预算闸
# ══════════════════════════════════════════════════════════════════
def test_agent_loop_tools():
    from harness import agent_loop as al

    # tool_loader：schema 在轮内只解析一次，且顺序稳定
    load_calls = {"n": 0}

    def _loader(names):
        load_calls["n"] += 1
        return [_mk_schema(n) for n in sorted(names)]

    seen = []

    def _chat(messages, tools=None, max_tokens=None):
        seen.append(tools)
        if len(seen) == 1:
            return ("", [{"id": "c1", "function": {"name": "t1", "arguments": "{}"}}], {})
        return ("done", [], {})

    res = al.run_agent(
        session_id="sx", user_input="go",
        chat_fn=_chat, tool_functions={"t1": lambda a: "r1", "t2": lambda a: "r2"},
        tool_loader=_loader, persist=False, logger=lambda m: None,
    )
    check("loop: 工具 loader 只调 1 次", load_calls["n"] == 1, load_calls["n"])
    check("loop: 两次调用工具集一致", seen[0] == seen[1], f"{seen[0]} vs {seen[1]}")
    check("loop: 收敛成功", res.ok and res.answer == "done", res.to_dict())

    # 预算闸：模型不断要工具 → 总调用预算耗尽后**立即如实收敛**（不空转到步数上限）
    def _greedy(messages, tools=None, max_tokens=None):
        return ("", [{"id": f"c{len(seen)}", "function": {"name": "t1", "arguments": "{}"}}], {})

    res2 = al.run_agent(
        session_id="sy", user_input="go",
        chat_fn=_greedy, tool_functions={"t1": lambda a: "r"},
        tool_schemas=[_mk_schema("t1")], max_steps=100, persist=False,
        logger=lambda m: None,
    )
    # 关键：**绝不允许**跑满 101 步 —— 预算耗尽即停
    check("loop: 总预算耗尽即停", res2.steps <= al.MAX_TOOL_CALLS + 2, res2.steps)
    check("loop: 未收敛时如实标记", res2.ok is False and "预算" in (res2.error or ""),
          res2.to_dict())

    # 单步调用数上限：一步塞 20 个调用，只有前 MAX_CALLS_PER_STEP 个真的执行
    ran = {"n": 0}

    def _count_tool(args):
        ran["n"] += 1
        return "r"

    def _many(messages, tools=None, max_tokens=None):
        calls = [{"id": f"m{i}", "function": {"name": "t1", "arguments": "{}"}}
                 for i in range(20)]
        if not any(m.get("role") == "tool" for m in messages):
            return ("", calls, {})
        return ("fin", [], {})

    res3 = al.run_agent(
        session_id="sz", user_input="go",
        chat_fn=_many, tool_functions={"t1": _count_tool},
        tool_schemas=[_mk_schema("t1")], persist=False, logger=lambda m: None,
    )
    check("loop: 单步超限只执行上限个数", ran["n"] == al.MAX_CALLS_PER_STEP, ran["n"])
    check("loop: 单步超限后能收敛", res3.ok and res3.answer == "fin", res3.to_dict())

    # 工具异常被隔离
    def _boom(args):
        raise ValueError("kaboom")

    def _chat2(messages, tools=None, max_tokens=None):
        if not any(m.get("role") == "tool" for m in messages):
            return ("", [{"id": "b1", "function": {"name": "bad", "arguments": "{}"}}], {})
        return ("handled", [], {})

    res4 = al.run_agent(
        session_id="s4", user_input="go", chat_fn=_chat2,
        tool_functions={"bad": _boom}, tool_schemas=[_mk_schema("bad")],
        persist=False, logger=lambda m: None,
    )
    check("loop: 工具异常隔离并收敛", res4.ok and res4.answer == "handled", res4.to_dict())

    # on_event 异常被隔离
    def _bad_event(t, d):
        raise RuntimeError("event boom")

    res5 = al.run_agent(
        session_id="s5", user_input="go",
        chat_fn=lambda m, t=None, max_tokens=None: ("hi", [], {}),
        tool_functions={}, on_event=_bad_event, persist=False, logger=lambda m: None,
    )
    check("loop: on_event 异常隔离", res5.ok and res5.answer == "hi", res5.to_dict())

    # abort
    def _chat3(messages, tools=None, max_tokens=None):
        return ("", [{"id": "a1", "function": {"name": "t1", "arguments": "{}"}}], {})

    st = {"n": 0}

    def _abort():
        st["n"] += 1
        return st["n"] > 2

    res6 = al.run_agent(
        session_id="s6", user_input="go", chat_fn=_chat3,
        tool_functions={"t1": lambda a: "r"}, tool_schemas=[_mk_schema("t1")],
        should_abort=_abort, persist=False, logger=lambda m: None,
    )
    check("loop: 可中止", res6.aborted is True, res6.to_dict())

    # 畸形 tool_calls（无 function 字段）不崩
    def _weird(messages, tools=None, max_tokens=None):
        if not any(m.get("role") == "tool" for m in messages):
            return ("", [{"name": "t1", "arguments": "{}"}], {})  # Anthropic 风格
        return ("ok-weird", [], {})

    res7 = al.run_agent(
        session_id="s7", user_input="go", chat_fn=_weird,
        tool_functions={"t1": lambda a: "r"}, tool_schemas=[_mk_schema("t1")],
        persist=False, logger=lambda m: None,
    )
    check("loop: 兼容平铺 tool_calls", res7.ok and res7.answer == "ok-weird", res7.to_dict())

    # tool_calls 全不可用 → 视作无工具，直接收敛
    def _junk(messages, tools=None, max_tokens=None):
        return ("partial", [{"garbage": 1}], {})

    res8 = al.run_agent(
        session_id="s8", user_input="go", chat_fn=_junk,
        tool_functions={}, persist=False, logger=lambda m: None,
    )
    check("loop: 垃圾 tool_calls 收敛", res8.ok and res8.answer == "partial", res8.to_dict())


def main():
    for fn in (test_time_ctx, test_time_mismatch, test_time_injection_reaches_private_chat,
               test_tool_registry, test_skill_loader,
               test_mcp_hub, test_bot_hook, test_agent_loop_tools):
        try:
            fn()
        except Exception as e:
            import traceback
            global FAIL
            FAIL += 1
            FAILURES.append(f"{fn.__name__} 抛异常: {e}\n{traceback.format_exc()}")
    print(f"\n{'='*60}")
    print(f"PASS: {PASS}   FAIL: {FAIL}")
    if FAILURES:
        print("-" * 60)
        for f in FAILURES:
            print(" ✗ " + f)
    print("=" * 60)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
