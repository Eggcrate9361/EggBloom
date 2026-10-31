# -*- coding: utf-8 -*-
"""scripts/export_config_docs.py —— 从**一份数据**导出配置说明，并做三处一致性审计。

背景（1.5.8 计划 #22）
    配置说明原本要"集中到 config_meta.py，配置页、文档、example 三处派生"。
    实际没有建 config_meta.py —— 因为那条链路已经有了，再建一个就是**第二份源**：

        v3 的字段标记（唯一源）
          → scripts/extract_field_meta.py   → scripts/_fieldmeta.json
          → scripts/patch_field_defaults.py → default / restart
          → scripts/inject_field_meta.py    → v4 页面 FIELD_META

    配置页要的 label / desc / 控件形态 / 默认值 / 要不要重启，全在这条链上。
    所以这里**不新建数据文件**，只做两件链上没有的事：

      ① 导出 `docs/配置项说明.md`（人读的那一份，配置页没法装下全部 desc）
      ② 审计三个键集合是否一致，把漏登记的**点名出来**

为什么审计比导出重要
    "加了配置键忘了同步 example"在这个仓库犯过三次
    （agent_group_enable / ban_fabrication / fix_question_marks）。
    人靠记忆对不齐 115 个键，所以让脚本每次跑都对一遍：

        DEFAULT_CONFIG（代码认的键）
        bot_config.json.example（给用户看的清单）
        _fieldmeta.json（配置页能改的字段）

    差集分三类，处理方式不同：
      A. example 有、DEFAULT 没有  → 分两种：代码用 _config.get 直接读的
         （合法，是"运行时读 + 兜底"的键，文档照写）；以及**零引用**的死键
         （必须删，留在 example 里用户改了没反应）
      B. DEFAULT 有、example 没有  → 必须补。用户根本不知道这些键存在
      C. _fieldmeta 有、DEFAULT 没有 → 页面能改但默认值表里没有，
         多数是嵌套键（de_ai.* / auto_accept.*）或凭据类，属正常

用法
    python scripts/export_config_docs.py            # 导出文档 + 审计
    python scripts/export_config_docs.py --audit    # 只审计，不写文件
    python scripts/export_config_docs.py --strict   # 有差集就退出码 1（CI 用）

幂等：同样的输入产出同样的文档。
"""
import io
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import config  # noqa: E402

META = os.path.join(HERE, "_fieldmeta.json")
EXAMPLE = os.path.join(ROOT, "bot_config.json.example")
OUT_DOC = os.path.join(ROOT, "docs", "配置项说明.md")

# v4 配置页的 6 个分组（与 webui_artistic_v4.html 的 CFG_GROUPS 一致）。
# 这里写死一份而不是去解析前端 —— 分组是**给人看的归类**，不属于"配置事实"，
# 两边不需要联动；真改了分组，改这里比改正则可靠。
GROUP_OF_PANEL = {
    "basic": "接入", "provider": "接入",
    "llm": "说话", "deai": "说话", "length": "说话",
    "group": "群聊", "trigger": "群聊", "exit": "群聊", "perm": "群聊",
    "meme": "互动", "recall": "互动",
    "switches": "功能", "autoaccept": "功能", "session": "功能",
    "ui": "界面",
}
GROUP_ORDER = ["接入", "说话", "群聊", "互动", "功能", "界面"]
GROUP_HINT = {
    # ★ 提示语必须与这一组**实际装了什么**对上。第一版照 CFG_GROUPS 的意图写
    #   「模型、Key、协议地址」，但 api_key / api_url / api_provider 的 panel 是
    #   llm（归「说话」组），接入组里只有 Bot 身份和首批目标 —— 用户照提示语
    #   在这一组里找 Key 会找不到。宁可写得朴素，不要写得漂亮但错。
    "接入": "Bot 身份与首批目标（QQ、群、私聊对象）。模型与 Key 在下一组「说话 → 对话大脑」。",
    "说话": "语气、长度、去 AI 味，以及模型与 Key。改动即时生效。",
    "群聊": "什么时候开口、什么时候退场、哪些群参与。",
    "互动": "表情包与撤回反馈。",
    "功能": "各种能力开关、自动通过、会话参数。",
    "界面": "只影响控制台外观，不影响 Bot 行为。",
}


def load_meta():
    meta = json.load(io.open(META, encoding="utf-8"))
    return meta["fields"], meta.get("panels", [])


#: 参与"这个键还有没有人读"判定的源码目录。不含 tests / scripts / _ref / 第三方 venv。
#: ★ 新增"独立模块消费配置"时**必须加进这张表**：漏一个模块，文档就会把
#:   活键标成「0 处」，下一个接手的人会把它当死键删掉（reply_gate 就是这么漏的）。
SCAN_DIRS = ("bot_core.py", "config.py", "comfyui_client.py", "logger_setup.py",
             "web_access.py", "webui", "protocols", "plugins", "harness",
             "reply_gate.py", "reply_drop.py", "reply_budget.py", "persona_form.py",
             "speak_gate.py", "meme_collect.py", "meme_engine.py")

#: 配置对象的变量名白名单。**必须限定变量名**，不能只匹配 `.get("key")`：
#   `free_llm.py` 里有 `r.get("model", "")` —— 那是读 API 响应字段，不是读配置。
#   宽匹配会把这类命中算成"这个键还活着"，于是 `model` 这个**真死键**被判成活的
#   （实测踩过：第一版 audit 把 19 个死键报成 1 个）。
CFG_VARS = (r"(?:_config|runtime_cfg|_runtime_cfg|cfg|config"
            r"|self\.config|self\._cfg|self\.cfg|self\.config_var)")


#: 遗留键：只有**旧的 Tkinter 面板**（image_gen_panel.py，挂在没人 import 的
#: main_window.py 下）还在读写，主链早就不读了。不删是因为删除是有风险动作，
#: 且那个面板确实会把它写回配置文件；标出来让人知情，别当活键用。
#: `log_level` 曾是这一类的成员，已从 example 删掉（见下）。
LEGACY_KEYS = {
    "llm_model": "仅旧 Tkinter 生图面板读写（image_gen_panel.py），主链不读",
    "model": "webui_server 的兜底读取（cfg.get('model_name') or cfg.get('model')）",
    "provider": "bot_core 的兜底读取（api_provider or provider）",
}


def _scan_files():
    out = []
    for d in SCAN_DIRS:
        p = os.path.join(ROOT, d)
        if os.path.isfile(p):
            out.append(p)
        elif os.path.isdir(p):
            for root, dirs, files in os.walk(p):
                dirs[:] = [x for x in dirs
                           if not x.startswith(".") and x not in ("__pycache__", "data")]
                for f in files:
                    if f.endswith(".py"):
                        out.append(os.path.join(root, f))
    return out


_CACHE = {}


def code_refs(key):
    """这个键被**当成配置读**了几次。

    判据只认两种读取形态，别的都不算：
        配置对象.get("key")   —— 变量名见 CFG_VARS 白名单
        配置对象["key"]
    不认 `"key": value`（那是字典字面量，不是读取）—— 用宽匹配时
    protocols 驱动模板里的 `"ws_port": 3101` 会被当成读取，一个死键就活了。
    """
    if key in _CACHE:
        return _CACHE[key]
    pat = re.compile(r'(?:%s)\.get\(\s*["\']%s["\']|(?:%s)\["%s"\]'
                     % (CFG_VARS, re.escape(key), CFG_VARS, re.escape(key)))
    n = 0
    for f in _scan_files():
        try:
            src = io.open(f, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        n += len(pat.findall(src))
    _CACHE[key] = n
    return n


def fmt_default(v):
    """默认值的人读形式。结构太复杂就不显示具体内容，只给形状。"""
    if isinstance(v, bool):
        return "开启" if v else "关闭"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, str):
        if not v:
            return "空"
        # URL / 路径不截断：截成 `https://api.deepseek.com/chat/co…` 等于没给，
        # 用户还是不知道默认地址是什么。这类值在表格里换行即可。
        if v.startswith(("http://", "https://", "ws://", "wss://")):
            return v
        return v if len(v) <= 32 else v[:32] + "…"
    if isinstance(v, list):
        if not v:
            return "空"
        if all(isinstance(x, str) for x in v) and sum(len(x) for x in v) <= 40:
            return "、".join(v)
        return "%d 项" % len(v)
    if isinstance(v, dict):
        return "对象（%d 个子键）" % len(v)
    return "—"


def default_of(key, defaults):
    """取一个字段的默认值。嵌套键（de_ai.ban_words）要按 sub 往下取一层。

    早期版本直接 `defaults.get(key)`，嵌套键全取到 None → 文档里 19 个
    de_ai / auto_accept 开关的默认值都是「—」，而这批恰恰最该写清楚。
    """
    if "." in key:
        top, sub = key.split(".", 1)
        node = defaults.get(top)
        if isinstance(node, dict) and sub in node:
            return node[sub]
        return None
    return defaults.get(key)


def panel_label(panels, panel):
    """面板的中文名。_fieldmeta 的 panels 表就是 [id, 中文名]，
    拿不到时退回 id（不猜 —— 猜一个错名比显示 id 更糟）。"""
    for pid, name in panels:
        if pid == panel:
            return name
    return panel


def build_doc(fields, panels_meta, defaults, example_keys):
    """生成 docs/配置项说明.md。"""
    L = []
    L.append("# 配置项说明")
    L.append("")
    L.append("本文由 `scripts/export_config_docs.py` 从 `scripts/_fieldmeta.json`、")
    L.append("`config.DEFAULT_CONFIG` 与 `bot_config.json.example` 自动生成，**不要手工编辑**。")
    L.append("")
    L.append("配置页（控制台 → 配置中心）能改的每一项都在这里，含含义、默认值与改了要不要重启。")
    L.append("键名以 `bot_config.json.example` 为准；页面上的中文标签来自 v3 的字段标记，")
    L.append("与本文是同一份数据的两面。")
    L.append("")
    L.append("表里「默认值」一列：")
    L.append("")
    L.append("- `—` = `config.DEFAULT_CONFIG` 里没有这个键。它仍可能生效 —— 代码用")
    L.append("  `_config.get(\"key\", 兜底值)` 直接读，兜底值写在读取处（本地模型路径、")
    L.append("  ComfyUI 参数这类）。要看兜底值，搜 `bot_core.py` 里的键名。")
    L.append("- 「重启」列标「要」的项，改完必须重启后端进程才生效；其余热生效。")
    L.append("")

    # 按分组 → 面板 → 字段三层组织。
    # ★ 必须先按 panel 收拢，再在面板内按 label 排序。反过来（先按 label 排一遍）
    #   会让同一面板的字段按字母序散开，面板小标题被切成七八段 —— 实测踩过：
    #   「对话大脑」出现两次，「去 AI 味」出现五次。
    grouped = {}
    for key, m in fields.items():
        g = GROUP_OF_PANEL.get(m.get("panel", ""), "功能")
        grouped.setdefault(g, {}).setdefault(m.get("panel", ""), []).append((key, m))

    for g in GROUP_ORDER:
        panels = grouped.get(g)
        if not panels:
            continue
        L.append("## " + g)
        L.append("")
        L.append(GROUP_HINT.get(g, ""))
        L.append("")
        for p, rows in panels.items():
            L.append("### " + panel_label(panels_meta, p))
            L.append("")
            L.append("| 配置项 | 键名 | 默认值 | 重启 |")
            L.append("|---|---|---|---|")
            for key, m in sorted(rows, key=lambda kv: (kv[1].get("label") or kv[0])):
                dv = default_of(key, defaults)
                if dv is None and key not in defaults and "." not in key:
                    d = "—"
                else:
                    d = fmt_default(dv)
                restart = "要" if m.get("restart") else ""
                label = m.get("label") or key
                desc = (m.get("desc") or "").replace("|", "／")
                if desc:
                    label = label + "（" + desc + "）"
                L.append("| %s | `%s` | %s | %s |" % (label, key, d, restart))
            L.append("")

    # ── 不在配置页里、但 example 登记了的键 ──
    extra = sorted(k for k in example_keys if k not in fields and k != "providers")
    if extra:
        L.append("## 仅存在于 bot_config.json.example")
        L.append("")
        L.append("这些键配置页上没有入口（协议地址、ComfyUI 参数、本地模型路径这类，")
        L.append("在各自页签里填）。要改就手工编辑 `bot_config.json`。")
        L.append("")
        L.append("| 键名 | 代码引用 |")
        L.append("|---|---|")
        for k in extra:
            L.append("| `%s` | %d 处 |" % (k, code_refs(k)))
        L.append("")

    L.append("## 一致性是怎么保证的")
    L.append("")
    L.append("- 配置页的字段标签、控件形态、说明：v3 的字段标记 → `extract_field_meta.py`")
    L.append("- 默认值与「要不要重启」：`config.DEFAULT_CONFIG` 与 `bot_core` 的模块级常量 → `patch_field_defaults.py`")
    L.append("- 本文与上面的审计：`export_config_docs.py`")
    L.append("")
    L.append("改任何一个配置键，按这个顺序跑：")
    L.append("")
    L.append("```")
    L.append("python scripts/extract_field_meta.py      # v3 标记 → _fieldmeta.json")
    L.append("python scripts/patch_field_defaults.py    # 补 default / restart")
    L.append("python scripts/inject_field_meta.py       # 注入 v4 页面")
    L.append("python scripts/export_config_docs.py      # 导出本文 + 三处一致性审计")
    L.append("```")
    L.append("")
    return "\n".join(L)


def audit(fields, defaults, example_keys):
    """三处键集合比对。返回 (问题列表, 报告行)。"""
    dk, ek, fk = set(defaults), set(example_keys), set(fields)

    only_example = sorted(ek - dk)          # A
    only_default = sorted(dk - ek)          # B
    only_meta = sorted(fk - dk)             # C

    dead = []
    runtime = []
    for k in only_example:
        # 顺序有讲究：**先判遗留**。遗留键里有的仍有代码引用（model / provider 是
        # 兜底读取，llm_model 被旧 Tkinter 面板读写），先按 refs 分类会把它们
        # 混进"运行时读"，看起来跟正常键一样，看不出它是遗留。
        if k in LEGACY_KEYS:
            continue
        if code_refs(k) == 0 and k not in ("providers",):
            dead.append(k)
        else:
            runtime.append(k)

    problems = []
    if only_default:
        problems.append("代码认但 example 漏登记 %d 个：%s" % (len(only_default), "、".join(only_default)))
    if dead:
        problems.append("example 里有死键（代码零引用）%d 个：%s" % (len(dead), "、".join(dead)))

    legacy = sorted(k for k in only_example if k in LEGACY_KEYS)

    lines = []
    lines.append("三处键集合：DEFAULT_CONFIG %d · example %d · 配置页字段 %d"
                 % (len(dk), len(ek), len(fk)))
    lines.append("")
    lines.append("A. example 有、DEFAULT_CONFIG 没有（%d）" % len(only_example))
    lines.append("   运行时读（_config.get 直接取，合法）%d：%s"
                 % (len(runtime), "、".join(runtime) or "无"))
    lines.append("   遗留键（只有旧面板读写，主链不读）%d：%s"
                 % (len(legacy), "、".join(legacy) or "无"))
    lines.append("   死键（代码零引用，必须删）%d：%s"
                 % (len(dead), "、".join(dead) or "无"))
    lines.append("")
    lines.append("B. DEFAULT_CONFIG 有、example 没有（%d）：%s"
                 % (len(only_default), "、".join(only_default) or "无"))
    lines.append("C. 配置页字段有、DEFAULT_CONFIG 没有（%d）：%s"
                 % (len(only_meta), "、".join(only_meta) or "无"))
    return problems, lines, {"only_default": only_default, "dead": dead}


def main():
    audit_only = "--audit" in sys.argv
    strict = "--strict" in sys.argv
    fields, panels = load_meta()
    defaults = config.DEFAULT_CONFIG
    # utf-8-sig：example 历史上带过 BOM，带不带都能读（json.load 遇 BOM 会炸）
    example = json.load(io.open(EXAMPLE, encoding="utf-8-sig"))
    example_keys = set(example.keys())

    problems, lines, detail = audit(fields, defaults, example_keys)

    print("── 三处一致性审计 ──")
    for ln in lines:
        print(ln)

    if problems:
        print("")
        print("问题（要处理）：")
        for p in problems:
            print("  - " + p)

    if audit_only:
        return 1 if (strict and problems) else 0

    doc = build_doc(fields, panels, defaults, example_keys)
    if not os.path.isdir(os.path.dirname(OUT_DOC)):
        os.makedirs(os.path.dirname(OUT_DOC))
    old = io.open(OUT_DOC, encoding="utf-8").read() if os.path.exists(OUT_DOC) else ""
    io.open(OUT_DOC, "w", encoding="utf-8", newline="").write(doc)
    n_fields = len(fields)
    print("")
    print("已写出 %s（%d 个配置页字段，%d 字符）"
          % (os.path.relpath(OUT_DOC, ROOT), n_fields, len(doc)))
    if old == doc:
        print("内容与上一版一致（幂等）")
    return 1 if (strict and problems) else 0


if __name__ == "__main__":
    sys.exit(main())
