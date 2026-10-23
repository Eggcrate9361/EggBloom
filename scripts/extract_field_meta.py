# -*- coding: utf-8 -*-
"""从 v3 抽出「字段元数据表」，供 v4 配置中心 1:1 复现设置项。

为什么要自动化
--------------
v3 的字段是两张皮：标签在 render*Panel 里，键名在 collectSettings 里，
靠 sXxx 这个 id 缝合。手工抄 120 个字段必然漏（漏一个就是"设置项没有"），
所以这里按 <div class="field"> 切块，把两张皮**按 id 缝合**成一张表。

v3 实际有 4 种字段写法，都要认：
  ① 竖排：field-label → input/textarea → field-desc
  ② 横排开关：input.toggle-input[type=checkbox] → field-label → field-desc
  ③ 列表挂载点：<div id="xxxList"></div>（退场语，交给 mountEditableList）
  ④ 动作按钮：<button id="xxx">
产出：scripts/_fieldmeta.json
"""
import io
import json
import os
import re
from collections import OrderedDict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
V3 = os.path.join(ROOT, "webui", "webui_artistic_v3.html")
s = io.open(V3, encoding="utf-8").read()

PANELS = [
    ("provider", "提供商管理"), ("basic", "基础"), ("llm", "对话大脑"),
    ("session", "会话"), ("group", "群聊"), ("meme", "表情包"),
    ("recall", "撤回反馈"), ("switches", "功能开关"), ("deai", "去 AI 味"),
    ("length", "回复长度"), ("exit", "退场语"), ("trigger", "触发"),
    ("perm", "权限"), ("autoaccept", "自动通过"), ("protoRisk", "协议风险"),
]
FUNC2PANEL = {
    "renderProviderPanel": "provider", "renderBasicPanel": "basic", "renderLLMPanel": "llm",
    "renderSessionPanel": "session", "renderGroupPanel": "group", "renderMemePanel": "meme",
    "renderRecallPanel": "recall", "renderSwitchesPanel": "switches",
    "renderDeAiPanel": "deai", "renderLengthPanel": "length", "renderExitPanel": "exit",
    "renderTriggerPanel": "trigger", "renderPermPanel": "perm",
    "renderAutoAcceptPanel": "autoaccept", "renderProtoRiskPanel": "protoRisk",
}


def body_of(name):
    i = s.find("function " + name)
    if i < 0:
        return ""
    j = s.index("{", s.index(")", i))
    depth, k = 0, j
    while k < len(s):
        if s[k] == "{":
            depth += 1
        elif s[k] == "}":
            depth -= 1
            if depth == 0:
                break
        k += 1
    return s[j:k]


def plain(x):
    x = re.sub(r"<[^>]+>", "", x or "")
    return re.sub(r"\s+", " ", x).strip()


def kind_of(ctl):
    if ctl.startswith("<textarea"):
        return "lines"          # 每行一条（触发词/目标 QQ/群 ID）
    if ctl.startswith("<select"):
        return "select"
    if 'type="checkbox"' in ctl or "toggle-input" in ctl:
        return "bool"
    if 'type="number"' in ctl:
        return "number"
    return "text"


by_id = OrderedDict()
lists = OrderedDict()
actions = OrderedDict()

for fn, panel in FUNC2PANEL.items():
    b = body_of(fn)
    if not b:
        continue
    # 以控件为中心：标签/说明可能在其**前**（竖排写法）也可能在**后**（横排开关写法），
    # 所以两侧都扫，取距离最近的那个。不按 <div class="field"> 切块 ——
    # 那层 div 里有嵌套闭合，块边界正则不可靠（实测只抽出 5 个）。
    for cm in re.finditer(r"<(input|textarea|select)\b[^>]*\bid=\"(s[A-Za-z0-9_]+)\"[^>]*>", b, re.S):
        fid = cm.group(2)
        before, after = b[:cm.start()], b[cm.end():cm.end() + 500]

        def pick(pat):
            """取这个控件**自己那个 .field 块**里的标签/说明。

            ★ 2026-10-24 重写。原实现是"两侧都扫、取字符距离最近的"，
              在竖排写法（label → desc → input）下**系统性串位**：
              自己的 label 与控件之间隔着整段 desc，字符距离 60~100；
              而下一个字段的 label 只隔着几个闭合 div，距离约 45。
              于是"最近"总是选到**别人的**标签 —— 实测标签整体前移一位：
                poke_timeout 显示成「会话缓冲窗口（秒）」
                group_msg_buffer_window_session 显示成「触发缓冲窗口（秒）」
              群聊面板因此出现两个同名标签，用户分不清哪个是哪个。

              字符距离在这里不是有效判据，**块归属**才是：
                ① 控件之前、本块之内的第一个 label/desc → 竖排写法
                   （横排开关写法里控件前面没有 label，会落到 ②）
                ② 否则取控件之后、且**不跨过下一个 .field** 的第一个 → 横排开关写法
              这样既不依赖嵌套 div 的配对，也不会串到邻居字段。
            """
            bs = b.rfind('class="field"', 0, cm.start())
            if bs != -1:
                seg = b[bs:cm.start()]
                m = re.search(pat, seg, re.S)
                if m:
                    return m.group(1)
            tail = b[cm.end(): cm.end() + 500]
            nxt = tail.find('class="field"')
            seg2 = tail[:nxt] if nxt != -1 else tail
            m = re.search(pat, seg2, re.S)
            return m.group(1) if m else None

        lab = pick(r'class="field-label"[^>]*>(.*?)</div>')
        dsc = pick(r'class="field-desc"[^>]*>(.*?)</div>')
        label = plain(lab) if lab else ""
        unit = ""
        mu = re.search(r"[（(]([^）)]*?)[)）]\s*$", label)
        if mu and any(u in mu.group(1) for u in ("秒", "条", "字", "次", "%", "天", "小时", "轮")):
            unit = mu.group(1).strip()
        by_id[fid] = OrderedDict([
            ("label", label), ("desc", plain(dsc) if dsc else ""),
            ("ctl", kind_of(cm.group(0))), ("panel", panel), ("unit", unit)])
    # ③ 列表挂载点（退场语）：<div id="exitShortList"></div>
    for lm in re.finditer(r'<div id="([A-Za-z0-9_]*List)"[^>]*>\s*</div>', b):
        lid = lm.group(1)
        before = b[:lm.start()]
        sec = None
        for sm in re.finditer(r'class="settings-section-label">([^<]*)', before):
            sec = sm.group(1)
        dsc = None
        for dm2 in re.finditer(r'class="field-desc">([^<]*)', before[-500:]):
            dsc = dm2.group(1)
        lists[lid] = OrderedDict([
            ("label", plain(sec) if sec else lid),
            ("desc", plain(dsc) if dsc else ""),
            ("ctl", "editable"), ("panel", panel), ("mount", lid)])
    # ④ 动作按钮
    for bm in re.finditer(r'<button[^>]*id="([A-Za-z0-9_]+)"[^>]*>(.*?)</button>', b, re.S):
        actions[bm.group(1)] = OrderedDict([("label", plain(bm.group(2))), ("panel", panel)])

# ── collectSettings：id → config key ───────────────────────────
# 用「key 位置扫描」而不是括号配对切分：配对切分会被源码里的正则字面量、
# 模板串、嵌套对象里的同名 key 干扰（实测把整张表都映射到了 'disabled'）。
# 只采纳**含有 val/num/chk/splitLines/collectListItems 调用**的 key ——
# 嵌套对象（{text:…, disabled:…}）本来就没有这种调用，天然被过滤掉。
ret = body_of("collectSettings")
ret = ret[ret.index("return {"):]
keypos = [(m.start(1), m.group(1)) for m in re.finditer(r"([a-z_][a-z0-9_]*)\s*:", ret)]
id2key = OrderedDict()
for idx, (pos, key) in enumerate(keypos):
    end = keypos[idx + 1][0] if idx + 1 < len(keypos) else len(ret)
    expr = ret[pos:end]
    # 任何"以字符串字面量为参数"的调用都算（val/num/chk/splitLines/collectListItems
    # 以及 collectDeAi({sX: chk('sX')}) 这类聚合收集器）
    ids = re.findall(r"\(\s*'([^']+)'\s*\)", expr)
    if not ids:
        ids = re.findall(r"\$\(\s*'([^']+)'\s*\)", expr)
    for f in ids:
        id2key.setdefault(f.lstrip("#"), key)   # '#recallPreList' → 'recallPreList'
for m in re.finditer(r"([a-z_][a-z0-9_]*)\s*:\s*collectListItems\(\s*'([^']+)'", ret):
    id2key.setdefault(m.group(2), m.group(1))

# ② v3 的退场语**只读不存**（mountEditableList 读了，collectSettings 没收集）——
#    那是 v3 的缺陷；v4 按读取用的键名实现保存，所以补上映射。
#    这段必须在"缝合"之前：它要改的是 by_id / id2key，改晚了 out 已经建好。
MANUAL_KEY = {
    "exitShortList": "custom_exit_phrases_short",
    "exitIdleList": "custom_exit_phrases_idle",
    "sProvider": "api_provider",
    "sModelManual": "model_name",
    "sLlamaScanRoot": "llama_search_root",
    # 群聊工具白名单的勾选容器：既不出现在 collectSettings 的 id→key 映射里
    #（那里的 sGroupToolAllowEnable 是界面开关），也不是 mountEditableList 的挂载点，
    # 所以只能显式登记，否则这个字段会整个丢掉。
    "sGroupToolList": "group_tool_allowlist",
}
for fid, key in MANUAL_KEY.items():
    id2key.setdefault(fid, key)

# ── 手工补丁：正则吃不下的四类 ───────────────────────────────
# ① de_ai 是**嵌套对象**（de_ai: { enabled: chk('sDeAiEnable'), … }），
#    保存时不是顶层 key，而是 de_ai.<子键>。这里把子开关显式登记。
nested_out = OrderedDict()   # 嵌套键（de_ai.*）先攒着，缝合时并入 out
NESTED = {
    "de_ai": [
        ("sDeAiEnable", "enabled", "启用去AI味总开关", "bool"),
        ("sDeAiPromptInject", "prompt_inject", "prompt 注入说话铁律", "bool"),
        ("sDeAiPostProcess", "post_process", "规则级后处理", "bool"),
        ("sDeAiBanWords", "ban_words", "清理 AI 高频词", "bool"),
        ("sDeAiBanDashes", "ban_dashes", "清理磕破折号", "bool"),
        ("sDeAiBanTriple", "ban_rule_of_three", "压缩三点式排比", "bool"),
        ("sDeAiBanFiller", "ban_filler", "清理套话/路标词", "bool"),
        ("sDeAiBanSyco", "ban_sycophancy", "清理谄媚口吻", "bool"),
        ("sDeAiMaxLen", "max_sentence_len", "超长句拆分阈值（字，0=不拆）", "number"),
    ],
}
for fid, sub, label, ctl in NESTED["de_ai"]:
    src = by_id.get(fid, {})
    # ★ 关键：这些 id 要以 "de_ai.<子键>" 登记，且**从 by_id 里剔除**。
    #   否则 keypos 扫描会把嵌套对象字面量里的 `enabled:` 当成**顶层配置键**，
    #   于是 de_ai 面板把 9 个开关各渲染两遍（顶层 enabled + de_ai.enabled），
    #   而且保存时会把 enabled 写到**顶层**、污染真实配置（实测踩过）。
    by_id.pop(fid, None)
    nested_out["de_ai." + sub] = OrderedDict([
        ("label", src.get("label") or label), ("desc", src.get("desc", "")),
        ("ctl", ctl), ("panel", "deai"), ("unit", src.get("unit", "")),
        ("id", fid), ("nested", "de_ai"), ("sub", sub)])

# ── 嵌套对象键自动识别 ─────────────────────────────────────────
# v3 里有两类配置键是**对象**：`de_ai: {enabled: …}`、`auto_accept: {enabled: …}`。
# 如果只按 keypos 扫 `name:`，对象内部的子键会被误当成**顶层配置键** ——
# 面板里同一个开关渲染两遍，保存时还会把 enabled 写到顶层污染配置（实测踩过）。
# 这里按缩进找出所有 `    key: {` 的顶层对象，再把对象内的 `sub: …('sXxx')`
# 映射成 `key.sub`，并把这些 id 从 by_id 里剔除。
nested_map = {}          # 控件 id → (顶层键, 子键)


def obj_span(text, key):
    """取 `key: { … }` 的对象体。**用括号配对**，不用 `.*?` 非贪婪 ——
    非贪婪会停在对象内部某个"恰好缩进 4 空格的 }"上（实测把 auto_accept
    整个吞进 de_ai，于是 auto_accept 的子键全被错标成 de_ai.*）。"""
    m = re.search(r"\n\s{4}" + re.escape(key) + r"\s*:\s*\{", text)
    if not m:
        return None
    i = m.end() - 1                      # 指向 '{'
    d = 0
    for j in range(i, len(text)):
        if text[j] == "{":
            d += 1
        elif text[j] == "}":
            d -= 1
            if d == 0:
                return text[i + 1:j]
    return None


for nk in re.findall(r"\n\s{4}([a-z_][a-z0-9_]*)\s*:\s*\{", ret):
    body = obj_span(ret, nk)
    if body is None:
        continue
    for sm in re.finditer(r"([a-z_][a-z0-9_]*)\s*:\s*([^,\n]*)", body):
        sub, expr = sm.group(1), sm.group(2)
        for fid in re.findall(r"\(\s*'([^']+)'\s*\)", expr):
            nested_map.setdefault(fid, (nk, sub))

# ── 缝合 ─────────────────────────────────────────────────────
out = OrderedDict(nested_out)
by_id.pop('sGroupToolAllowEnable', None)   # 见上：界面开关，非配置键
for fid, meta in by_id.items():
    if fid in nested_map:            # 嵌套子键：键名 = 顶层键.子键
        nk, sub = nested_map[fid]
        meta = OrderedDict(meta)
        meta["id"] = fid
        meta["nested"] = nk
        meta["sub"] = sub
        out["%s.%s" % (nk, sub)] = meta
        continue
    key = id2key.get(fid)
    if not key:
        continue
    if key in out:
        prev = out[key]
        if not prev["desc"]:
            prev["desc"] = meta["desc"]
        if meta["panel"] not in prev.get("_panels", []):
            prev.setdefault("_panels", [prev["panel"]]).append(meta["panel"])
        continue
    meta["id"] = fid
    out[key] = meta

# 列表挂载点（退场语）：mountEditableList('exitShortList', key) 之类
for lid, meta in lists.items():
    key = id2key.get(lid)
    if not key:
        for m in re.finditer(r"mountEditableList\(\s*['\"]" + lid + r"['\"]\s*,\s*['\"]([a-z_][a-z0-9_]*)", s):
            key = m.group(1)
            break
    # ★ 群聊工具白名单不是"每行一条的文本列表"，而是一组**勾选框**（数据源 GET /tools）。
    #   渲染成划线列表会让人只能手填工具名 —— 与 v3 的交互完全不同。
    #   还要把 sGroupToolAllowEnable 从 by_id 里剔掉：它是**界面开关**，
    #   v3/后端都用"group_tool_allowlist 是不是数组"来表达启用与否
    #   （None = 全放行，数组 = 白名单），并没有独立的 enable 配置键；
    #   不剔掉的话它会把列表项整个覆盖成一个 bool 字段（实测踩过）。
    if lid == "sGroupToolList":
        meta["ctl"] = "toolcheck"
    if key:
        meta["key"] = key
    out[key or lid] = meta

# （NESTED 补丁已应用到缝合之前的 nested_out）

# ② 见上（MANUAL_KEY 已在缝合前应用，这里只补面板归属）

path = os.path.join(HERE, "_fieldmeta.json")

# ── 保留 v3 里没有的字段（2026-10-24 补）────────────────────────────
# 本脚本只能抽出 v3 页面上存在的字段。但 _fieldmeta.json 里还有几个
# **只在 v4 加过、v3 从没有**的字段（bot_nicknames、group_quote_mode）。
# 原来这里是整文件覆盖 → 每跑一次就把它们删掉，且没有任何提示。
# 2026-10-24 实测：在未改动的代码上跑，fields 从 92 掉到 90。
#
# 现在的做法：先读旧表，把本次没抽出来的字段补回去，并在输出里点名。
# 这样「有字段被保留」是可见的，而不是静默丢。
_carried = OrderedDict()
try:
    _old = json.load(io.open(path, encoding="utf-8"))
    for _k, _v in (_old.get("fields") or {}).items():
        if _k not in out:
            _carried[_k] = _v
except Exception:
    pass
for _k, _v in _carried.items():
    out[_k] = _v

io.open(path, "w", encoding="utf-8").write(
    json.dumps({"panels": PANELS, "fields": out, "actions": actions},
               ensure_ascii=False, indent=1))

if _carried:
    print("保留 v3 中不存在的字段 %d 个: %s" % (len(_carried), list(_carried)))

print('DEBUG by_id=%d lists=%d id2key=%d' % (len(by_id), len(lists), len(id2key)))
print('DEBUG id2key sample:', list(id2key.items())[:8])
byp = OrderedDict()
for k, v in out.items():
    byp.setdefault(v.get("panel", "?"), []).append(k)
print("抽出字段 %d 个 / 动作按钮 %d 个" % (len(out), len(actions)))
for p, _ in PANELS:
    if p in byp:
        print("  %-11s %2d 项" % (p, len(byp[p])))
print("\n控件分布:", {k: sum(1 for v in out.values() if v.get("ctl") == k)
                  for k in ("text", "number", "bool", "select", "lines", "editable")})
missing_label = [k for k, v in out.items() if not v.get("label")]
print("缺中文标签:", missing_label)
print("未匹配到 key 的字段 id:", [f for f in by_id if f not in id2key][:10])
print("\n写出:", path)