# -*- coding: utf-8 -*-
"""harness/bot_hook.py —— bot 主回复链的**薄接入点**（把 harness 的高标准落到实处）。

为什么需要它
    bot_core 的 `reply_to()` 是个巨型函数（几百行），在里面散点改造风险高。
    本模块把"harness 要往主链里加什么"**收敛成几个纯函数**，bot_core 只调用：
      - `time_parts(...)`      : 时间分级注入（粗粒度靠前 / 精粒度靠尾）
      - `agent_tools_for(...)` : 按场景挑选工具子集 → **稳定的 schema 列表**
      - `skill_l1_block()`     : Skill 元数据（L1）常驻块
      - `chat_fn()`            : 包一层"轮内工具缓存"的 chat 闭包

原则
    - 本模块**不 import bot_core**（全部依赖注入）。
    - 任何一步失败都**静默降级**为原行为，绝不阻断回复。
    - 所有输出**顺序稳定**，以保前缀缓存。
"""
import re
import threading

from . import time_ctx as _tc
from . import tool_registry as _tr
from . import skill_loader as _sk

# ── 按场景的工具子集策略 ────────────────────────────────────────────
# 关键：**默认只给最小集**。工具不是能力展示，是 token 成本。
# 只有在输入里明确出现"需要某类能力"的信号时，才把对应分组加进来。
# 这样闲聊/普通群聊回复的请求里，工具描述几乎为零 → 省下的全是钱。

#: 桌面操控类关键词
#  ★ 2026-10-23：这一组除 web_operate / search_web 外的**全部内置工具**（含
#    send_user_file）都挂在 GROUP_DESKTOP 下，见 bot_core.py 的注册循环。
#    也就是说「发文件」这个能力和「关机」「截图」共用一个开关 —— 词没命中，
#    整组都不在，模型会回答「我手上没有发文件的工具」。
#    实测原话：私聊「…zip 发出来」→ 答「这轮我手上压根没有发文件的工具」；
#    改口「发给我」立刻能发。同一件事两种措辞，一次成一次不成。
_DESKTOP_KW = ("电脑", "屏幕", "桌面", "截图", "音量", "静音", "关机", "重启",
               "锁屏", "休眠", "进程", "任务管理器", "内存", "cpu", "显卡", "gpu",
               "打开", "启动", "运行", "app", "应用", "程序", "文件", "文件夹",
               "发送文件", "发给我", "本地",
               # ── 文件传输的同义说法（2026-10-23 补）──
               #  只补「把东西给我」这一类，不补「发」单字 —— 单字会命中
               #  「发现」「发布」，把 desktop 组拖进无关对话，白烧 token。
               "发出来", "发过来", "发一下", "发我", "传给我", "传过来", "传一下",
               "给我看", "给我发", "发份", "发个文件", "发这个", "发那个",
               "拖出来", "挪出来", "导出来", "导出", "打包给", "压缩包", "zip",
               ".zip", ".rar", ".7z", ".exe", ".msi", ".pdf", ".docx", ".xlsx")
#: 网页操作类关键词
_WEB_KW = ("网页", "网站", "链接", "http://", "https://", "打开网", "登录",
           "注册", "填表", "表单", "问卷", "答题", "下单", "购买", "提交",
           "爬", "抓取", "收藏", "浏览器")
#: 群能力关键词
_GROUP_KW = ("谁", "成员", "群里", "随机", "@", "在线", "画", "生成图", "画个",
             "画张", "来张图", "配图")

#: 触发 skill 子集的关键词 —— 命中才注入 skill__load / skill__read 两个工具。
#  刻意保守：闲聊、情绪、日常对话**不**给（skill 工具的定义也不便宜）。
_SKILL_KW = ("技能", "skill", "帮我做", "帮我搞", "帮我弄", "帮我处理", "帮我转",
             "帮我生成", "帮我整理", "帮我分析", "怎么做", "怎么办", "如何",
             "教程", "步骤", "流程", "专业", "处理一下", "帮我写", "帮我查文档",
             "pdf", "word", "excel", "ppt", "文档", "表格", "幻灯片", "格式转换")

# ── 工作型工具（harness 自带）的注入闸门 ──────────────────────────
#  ws_read / ws_glob / ws_grep 是**只读零副作用**，命中即给；
#  ws_write / ws_edit / ws_edit_many 有副作用，附加"私聊 or 明确改文件意图"
#  py_run / job_* 成本最高，只在明确要"跑代码"时给。
_WORK_READ_KW = ("文件", "代码", "源码", "脚本", "配置", "日志", "目录", "文件夹",
                 "项目", "仓库", "报错", "错误", "异常", "堆栈", "traceback",
                 "查找", "搜索", "搜一下", "找一下", "定位", "在哪", "哪一行",
                 "打开看", "看一下", "读一下", "内容是", "写了什么",
                 ".py", ".js", ".json", ".md", ".txt", ".yml", ".toml", ".cfg",
                 ".log", ".html", ".css", "harness", "bot_core", "plugin")
_WORK_WRITE_KW = ("修改", "改一下", "改成", "改成", "替换", "替换成", "删掉", "删除",
                  "加上", "增加", "添加", "新建文件", "创建文件", "写个文件",
                  "改文件", "编辑", "重写", "重构", "修复", "patch", "fix",
                  "写入", "保存到", "另存为")
_PROC_KW = ("跑一下", "运行一下", "执行一下", "跑个", "跑段", "计算", "算一下",
            "统计一下", "批量", "处理数据", "数据分析", "跑脚本", "执行代码",
            "python", "写个脚本", "写段代码", "测试一下", "跑测试", "验证")


def _any_in(text, kws):
    t = (text or "").lower()
    return any(k.lower() in t for k in kws)


def has_skills():
    """当前是否发现了任何 skill（无 skill 则永远不注入 skill 工具）。"""
    try:
        return bool(_sk.discover())
    except Exception:
        return False


def _should_offer_skills(user_msg):
    return _any_in(user_msg, _SKILL_KW)


#: 像本机路径的东西：Windows 盘符路径、Unix 绝对路径、带扩展名的文件名。
#  判据是**形态**不是措辞 —— 「E:\xxx.zip」「/home/a.txt」「报告.pdf」都算。
_PATH_LIKE_RE = re.compile(
    r"(?:[A-Za-z]:[\\/])"                       # C:\ 或 D:/
    r"|(?:^|[\s（(])/(?:[^\s/]+/)+"             # /a/b/
    r"|[\w\u4e00-\u9fff-]+\.(?:zip|rar|7z|tar|gz|exe|msi|dmg|pdf|docx?|xlsx?|pptx?|"
    r"txt|md|json|ya?ml|csv|log|py|js|ts|html?|css|png|jpe?g|gif|mp4|mp3|wav)\b",
    re.IGNORECASE,
)


def _mentions_local_path(text):
    """这句话里有没有出现**像本机路径**的片段。

    为什么要单独判：上面 _DESKTOP_KW 是措辞表，永远补不完。
    「E:\\EggBloom水星\\dist\\EggBloom-Full.zip 发出来」这种句子，
    真正说明"要动本机文件"的是那个路径，不是动词。
    用户只要给出路径，要么想让我们读，要么想让我们发，两种都需要 desktop 组。
    """
    try:
        return bool(_PATH_LIKE_RE.search(text or ""))
    except Exception:
        return False


def agent_tools_for(user_msg, *, is_group=True, extra_groups=(), enable_desktop=True,
                    enable_write=True):
    """按场景挑选工具子集，返回**稳定顺序**的 OpenAI schema 列表。

    规则（保守优先，宁可少给不可多给）
      - 永远含 core（搜索）+ **work 只读**（读/找/搜；零副作用，且省下的往返
        远比它的定义文本贵 —— 这是"极低成本"与"高效率"同时成立的少数选择）
      - 命中网页关键词 → + web
      - 命中桌面关键词 → + desktop（仅私聊 / enable_desktop）
      - 命中群能力关键词 → + group
      - **命中技能关键词 → + skill**（这是 skill 体系在主链的入口）
      - **命中写意图 → + write**（有副作用；群聊默认不给，见 write_gate）
      - **命中执行意图 → + proc**（py_run / job_*；最贵，条件最严）
      - **多步任务 → + todo**
      - extra_groups 由调用方显式追加

    返回：schema 列表（可能为 []）
    """
    groups = [_tr.GROUP_CORE, _tr.GROUP_WORK]
    if _any_in(user_msg, _WEB_KW):
        groups.append(_tr.GROUP_WEB)
    if not is_group and enable_desktop and (
            _any_in(user_msg, _DESKTOP_KW) or _mentions_local_path(user_msg)):
        #  2026-10-23：加了 _mentions_local_path —— 措辞表补不完，路径形态是硬信号。
        #  只给私聊（群里不给本机能力，这条边界不变）。
        groups.append(_tr.GROUP_DESKTOP)
    if is_group and _any_in(user_msg, _GROUP_KW):
        groups.append(_tr.GROUP_GROUP)
    # ── 写型：有副作用。群聊里默认**不给**（群里改本机文件风险太高）；
    #    私聊且命中写意图才给。这是一道独立闸门，不靠关键词巧合。
    if enable_write and (not is_group) and _any_in(user_msg, _WORK_WRITE_KW):
        groups.append(_tr.GROUP_WRITE)
    # ── 执行型：最贵。私聊 + 明确"跑代码"意图才给。
    if (not is_group) and _any_in(user_msg, _PROC_KW):
        groups.append(_tr.GROUP_PROC)
    # ── 规划型：多步任务（"帮我…然后…"、明确的多件事）才给。
    if has_skills() and _should_offer_skills(user_msg):
        groups.append(_tr.GROUP_SKILL)
    if _should_plan(user_msg):
        groups.append(_tr.GROUP_TODO)
    for g in (extra_groups or ()):
        if g not in groups:
            groups.append(g)
    return _tr.resolve_groups(groups)


#: 多步信号：出现"然后/接着/再/最后/依次/批量/第一步"这类顺序词，
#  或一句里出现多个动作诉求 → 值得给 todo（规划的成本只有在多步时才划算）。
_MULTISTEP_KW = ("然后", "接着", "再然后", "最后", "依次", "第一步", "第二步",
                 "分步", "批量", "逐个", "先…再", "一件事一件事",
                 "整个流程", "从头到尾", "完成后再")


def _should_plan(user_msg):
    t = user_msg or ""
    if _any_in(t, _MULTISTEP_KW):
        return True
    # 并列动词计数：出现 >=2 个动作词，视为多步
    verbs = ("改", "写", "加", "删", "查", "搜", "跑", "建", "读", "看", "统计", "整理")
    return sum(1 for v in verbs if v in t) >= 3


def tool_names_for(user_msg, *, is_group=True, extra_groups=(), enable_desktop=True,
                   enable_write=True):
    """同上，但只返回**工具名**（供 chat_fn 的 loader 用，避免重复建 schema）。

    ★ MCP（2026-10-10）：agent_tools_for 的分组里从不含 GROUP_MCP —— MCP 工具
      即使注册进了 registry 也永远选不出来。这里自动追加**已加载**的 MCP 工具
      （只在 mcp_hub 缓存里有的 = 用户在 MCP 设置区点过"加载"或开了 prefetch）。
      没加载的零成本、不拉进程握手 —— 与 mcp_hub 的惰性红线一致。
    """
    names = [s["function"]["name"]
             for s in agent_tools_for(user_msg, is_group=is_group,
                                      extra_groups=extra_groups,
                                      enable_desktop=enable_desktop,
                                      enable_write=enable_write)]
    try:
        from . import mcp_hub as _mcp
        for _srv, _c in _mcp.cached_servers().items():
            for t in (_c.get("tools") or []):
                n = t.get("function", {}).get("name")
                if n and n not in names:
                    names.append(n)
    except Exception:
        pass
    return names


# ── 时效性护栏（harness.freshness 的薄封装）─────────────────────────
def freshness_parts(user_msg, *, has_search=True):
    """返回 (anchors, directive)。

    anchors   : 可放**靠前**稳定区的时效锚（知识截止 + 日期基准）——变化慢，保缓存
    directive : 只在本轮**时效敏感**时才非空；放在当前用户消息**之前**（尾部）

    为什么分两段：锚点（"我的知识截止于 X"）是**长期事实**，一天内不变 → 放前面
    也能命中前缀缓存；而"这句话要用工具核实"是**本轮指令** → 只能放尾部。
    这样"时效性在场"与"前缀缓存不失效"同时成立。
    """
    try:
        from . import freshness as _fr
    except Exception:
        return "", ""
    anchors = ""
    try:
        gap = _fr.knowledge_cutoff_gap()
        anchor = _fr.date_anchor()
        parts = [x for x in (anchor, gap) if x]
        anchors = "\n".join(parts)
    except Exception:
        anchors = ""
    directive = ""
    try:
        directive = _fr.freshness_directive(user_msg, has_search=has_search)
    except Exception:
        directive = ""
    return anchors, directive


def note_tool_freshness(session_key, tool_name):
    """记录一次外部信息类工具的**成功时刻**（供过期判定）。"""
    try:
        from . import freshness as _fr
        _fr.mark(session_key, tool_name)
    except Exception:
        pass


def stale_note(session_key, tool_name):
    """该工具结果是否已过期 → 返回可用作事实引用的警告（空串=未过期）。"""
    try:
        from . import freshness as _fr
        return _fr.stale_note(session_key, tool_name)
    except Exception:
        return ""


# ── 时间分级注入 ────────────────────────────────────────────────────
_TIME_CTX = {}
_time_lock = threading.Lock()


def _ctx_for(key):
    with _time_lock:
        c = _TIME_CTX.get(key)
        if c is None:
            c = _tc.TimeContext(key)
            # 简单上限，避免无界增长
            if len(_TIME_CTX) > 500:
                for k in list(_TIME_CTX.keys())[:100]:
                    _TIME_CTX.pop(k, None)
            _TIME_CTX[key] = c
        return c


def time_parts(session_key, *, need_precise=False):
    """返回 (coarse, fine)：粗粒度（可放靠前）+ 精粒度（只能放尾部）。

    两段都可能是 ""（表示本时段无需刷新）——调用方直接跳过空串。
    """
    try:
        return _ctx_for(session_key or "__default__").parts(need_precise=need_precise)
    except Exception:
        return "", ""


def coarse_prefix_block(session_key):
    """粗粒度时间块（用于插到历史摘要**之前**的稳定区）。"""
    c, _ = time_parts(session_key)
    return c


def fine_tail_block(session_key, *, force=False):
    """精粒度时间块（用于插到当前用户消息**之前**）。"""
    try:
        return _ctx_for(session_key or "__default__").fine(force=force)
    except Exception:
        return ""


def mismatch_note(user_msg, session_key="__default__", *, ts=None):
    """对方话里的时间前提跟钟点矛盾 → 返回可执行提示；否则 ""。"""
    try:
        return _tc.mismatch_note(user_msg, ts=ts)
    except Exception:
        return ""


# ── Skill L1 元数据 ────────────────────────────────────────────────
_SKILL_CACHE = {"text": None, "at": 0.0}


def skill_l1_block(*, ttl=120, limit=None):
    """Skill 元数据常驻块（**极省**）。带 TTL 缓存，避免每轮扫盘。"""
    import time as _t
    if _SKILL_CACHE["text"] is not None and (_t.time() - _SKILL_CACHE["at"]) < ttl:
        return _SKILL_CACHE["text"]
    try:
        text = _sk.l1_block(limit=limit)
    except Exception:
        text = ""
    _SKILL_CACHE["text"] = text
    _SKILL_CACHE["at"] = _t.time()
    return text


def skill_tool_functions():
    """skill 暴露的内部工具：skill__load / skill__read。"""
    def _load(args):
        name = (args or {}).get("name") or ""
        r = _sk.l2_body(name)
        if not r.get("ok"):
            return json_dumps(r)
        parts = [f"# 技能：{r['display_name']}", r.get("description") or "", "",
                 r.get("body") or ""]
        res = r.get("resources") or {}
        if res:
            parts.append("\n可用资源（需要时用 skill__read 读取）：")
            for kind, files in res.items():
                parts.append(f"- {kind}: " + "、".join(files))
        if r.get("truncated"):
            parts.append("\n（正文较长已截断；如需完整内容请读取 SKILL.md）")
        return "\n".join(parts)

    def _read(args):
        name = (args or {}).get("name") or ""
        path = (args or {}).get("path") or ""
        r = _sk.l3_read(name, path)
        if not r.get("ok"):
            return json_dumps(r)
        return r.get("content") or ""

    return {"skill__load": _load, "skill__read": _read}


SKILL_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "skill__load",
            "description": "载入某个技能的详细步骤（先看【可用技能】清单，判断相关再载入）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "技能名（清单里的英文名）"}
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "skill__read",
            "description": "读取某个技能目录下的资源文件（references/scripts/assets 里的文件）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "技能名"},
                    "path": {"type": "string", "description": "相对技能目录的路径，如 references/x.md"},
                },
                "required": ["name", "path"],
            },
        },
    },
]


def json_dumps(obj):
    import json
    try:
        return json.dumps(obj, ensure_ascii=False, default=str)
    except Exception:
        return str(obj)


# ── chat_fn 包装（轮内工具缓存 + schema 注入）──────────────────────
def wrap_chat_fn(base_chat_fn, *, tools_provider=None):
    """把 bot_core 的 chat 闭包包一层：**同一次 run 内**只解析一次工具 schema。

    base_chat_fn(messages, tools, max_tokens=...) -> (content, tool_calls, usage)
    tools_provider() -> schema 列表（惰性、缓存由调用方保证）

    为什么要包：agent_loop 每步都会调 chat_fn；若不缓存，则每步都要重新
    解析工具列表（并且如果解析结果顺序抖动 → 前缀缓存每步失效）。
    """
    cache = {"schemas": None}

    def _chat(messages, tools=None, max_tokens=1500):
        if cache["schemas"] is None:
            if tools_provider is not None:
                try:
                    cache["schemas"] = list(tools_provider() or [])
                except Exception:
                    cache["schemas"] = None
            if cache["schemas"] is None:
                # 没给 provider：沿用调用方传入的 tools（名字列表或 schema）
                cache["schemas"] = tools if isinstance(tools, list) else []
        use = cache["schemas"] if cache["schemas"] else tools
        return base_chat_fn(messages, use, max_tokens=max_tokens)

    return _chat


def reset_time_cache():
    with _time_lock:
        _TIME_CTX.clear()
    _SKILL_CACHE["text"] = None
