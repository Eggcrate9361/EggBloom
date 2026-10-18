# -*- coding: utf-8 -*-
"""harness/agent_project.py —— EggBloom 项目知识层（Agent 的"懂这个项目"能力）。

设计（与 agent_service 的分工）
    本模块**只提供文本**，不做任何 IO、不依赖 bot_core。
    agent_service.build_system_prompt() 负责把它按"静态在前、动态在后"的顺序拼进去。

三层知识模型（回答"223KB 文档怎么给模型"）
    ┌─ L1 常驻手册 ──────────────────────────────────────────────┐
    │  约 1.2~1.6K token。只放"几乎每个项目问题都要用"的骨架：      │
    │  身份、目录地图、插件两种形态、配置回退链、生效两步、         │
    │  该去哪里查。**不含具体字段表**——那些放文档里按需取。        │
    └────────────────────────────────────────────────────────────┘
    ┌─ L2 检索指引 ──────────────────────────────────────────────┐
    │  告诉 Agent 文档/源码在哪、用什么工具查（ws_grep / ws_read）。│
    │  复用已有工具，**不新增工具**——省 token 也少一处维护。       │
    └────────────────────────────────────────────────────────────┘
    ┌─ L3 硬规则 ────────────────────────────────────────────────┐
    │  答错会真出事的铁律：改配置不能直读 bot_config.json、         │
    │  插件不能 import 主程序、改完源码不生效因为没重启……          │
    │  这些**必须常驻**，因为"不知道有这条"就等于会犯错。          │
    └────────────────────────────────────────────────────────────┘

为什么 L1 要刻意精简
    常驻内容每轮都付 token 钱。实测本项目 docs 共 223KB ≈ 6 万 token ——
    全塞进去既贵又会淹没重点。所以 L1 只留"索引 + 铁律"，
    细节一律走 ws_grep 现查（查一次比记一辈子便宜）。

为什么不额外写一个「读文档」工具
    ws_grep / ws_read / ws_glob 已经在 work 组常驻，且 `_WORK_READ_KW`
    本来就含 "plugin" / "harness" / "bot_core" 等词 —— 问插件相关问题时
    这些工具**天然已经在手**。再加一个专用工具是重复建设。
"""
import os

#: 项目根（按需探测；探测不到时相关指引会降级为"用相对路径"）
def _project_root():
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.dirname(here)


# ══════════════════════════════════════════════════════════════════
# L1：常驻项目手册（精简骨架 + 目录地图）
# ══════════════════════════════════════════════════════════════════
PROJECT_PRIMER = """【EggBloom 项目速查】你运行在 EggBloom 内部，回答本项目问题时**以事实为准，不靠猜**。
EggBloom 是一个 QQ 机器人桌面程序（Electron 壳 + Python 后端 + WebUI 管理面板）。

■ 进程与端口（先分清谁是谁）
  · bot_core.py   监听 :3005 —— **全部业务逻辑**（AI 对话、消息处理、插件、记忆）
  · webui_server.py 监听 :8080 —— WebUI 后端；把 /api/* 转发给 bot_core
  · EggBloom Desktop.exe —— Electron，**只是浏览器壳，不含业务逻辑**
  · protocols/ —— 协议适配层（napcat / snowluma / llonebot），走 OneBot v11

■ 目录地图（相对项目根）
  harness/        Agent 内核：agent_loop(主循环) agent_service(服务层) tool_fs(读改文件)
                  tool_proc(跑代码) bot_hook(工具分闸) tool_registry(注册表) session_log(事件日志)
  plugins/        插件目录，共 22 个
  docs/           项目文档（见下方索引）
  webui/          webui_server.py + 前端页面
  protocols/      协议适配
  nexsandglass/   本体记忆（沙漏）
  components/     桌面端组件

■ 插件只有两种形态（回答"插件怎么写"必先说清这一条）
  1. **内置插件（旧）**：PLUGIN_META + hooks，可访问全局，仅项目自带功能。
  2. **真插件 ABI v2（新）**：PLUGIN_SPEC=1 + PLUGIN_MANIFEST + PLUGIN_HOOKS。
     当前 PLUGIN_API_VERSION = 2。**新插件一律用这种。**
     三原则：不 import 主程序 / 只经 ctx 拿能力 / 声明式 manifest。
     详见 docs/PLUGIN_DEV.md（有最小可跑模板）。

■ 配置是**多层回退**的（回答"配置填哪儿"必先说清这一条）
  优先级高→低：Windows 凭据管理器(仅 API Key) → 程序目录 bot_config.json
  → 数据目录 bot_config.json → .env → config.py 内置 DEFAULT_CONFIG。
  写入时写"程序目录"，不可写才回退"数据目录"。
  实现在 config.py 的 load_config / save_config。

■ ★ 改了源码为什么"没反应"
  生效是**两步**，漏一步会**静默失败**（代码改了、行为不变、不报错）：
    ① 源码库 → 发行包 resources\\app（scripts/sync-dist.ps1）
    ② **重启后端 python 进程**（关掉 EggBloom-WebUI 窗口，重跑 start.bat）
  Electron 壳重开**没用** —— 它不持有业务逻辑。
  一条命令搞定同步+校验：python scripts/activate.py
"""


# ══════════════════════════════════════════════════════════════════
# L2：检索指引（文档索引 + 查法）
# ══════════════════════════════════════════════════════════════════
DOC_INDEX = """【项目文档索引】需要细节时**先查再答**，不要凭印象编。工具与用法：

  ws_grep(pattern="关键词", path="docs", glob="*.md")   ← 定位在哪个文档
  ws_read(path="docs/PLUGIN_DEV.md", offset=1, limit=200) ← 读具体内容
  ws_glob(pattern="plugins/*/__init__.py")                ← 看真实代码长啥样

  docs/PLUGIN_DEV.md          插件开发（真插件 ABI v2 + 最小模板 + ctx 能力表）★最常查
  docs/PLUGIN_SPEC.md         插件规格（manifest 字段定义）
  docs/USAGE.md               使用说明
  docs/发布打包.md             三个发行变体怎么打（含本机踩过的坑）

**答"插件怎么写"的正确姿势**：先 ws_read docs/PLUGIN_DEV.md，照里面的模板回答，
再 ws_glob plugins/*/__init__.py 看一个真实例子。不要凭记忆写 manifest 字段。
"""


# ══════════════════════════════════════════════════════════════════
# L3：硬规则（答错会真出事，必须常驻）
# ══════════════════════════════════════════════════════════════════
HARD_RULES = """【EggBloom 铁律】以下几条违反会真出问题，回答时务必守住：

1. **插件不许 import 主程序**。禁止 `import bot_core`、`import __main__`、
   直读 bot_config.json。要配置用 `ctx.get_config(key, default)`；
   要发消息用 `ctx.send_group / ctx.send_private`；要调 OneBot 动作用 `ctx.api_call`。
   给用户写插件代码时若出现 `import bot_core`，就是错的。

2. **改配置要区分"读"和"写"**。写配置写程序目录 bot_config.json；
   但插件里**永远用 ctx.get_config() 读**（它会走完整回退链），不要自己 open 文件。

3. **源码改动必须同步 + 重启后端**才算生效（见速查最后一节）。
   回答"改好了吗"之前先确认这两步。

4. **前端只有一个文件**：webui/webui_artistic_v3.html（单文件，含全部页面）。
   加页面要动 4 处：侧栏 `data-page` 项、`pageTitles`、`pageMounts`、以及
   `function mountXxx(root)`。漏任何一个都会导致页面空白或点不开。

5. **harness 的工具是"按场景注入"的**（见 bot_hook.agent_tools_for）：
   core+work 只读常驻；web/desktop/group/write/proc/skill 各有关键词闸门。
   群聊里**默认不给写文件和跑代码的工具**（安全考虑）。评估"某功能能不能用"
   时要先想闸门有没有开。

6. **别把源码库和发行包搞混**。源码库是 git clone 下来的那份（改代码 + 提交）；
   发行包是 E:\\EggBloom水星\\EggBloom Desktop-new（用户实际在跑的）。
   运行时数据（.neurobase / slang_dict.json / bot_config.json / plugins/state/）
   只存在于发行包，**源码库里没有**，不要看到源码库没有就说"数据丢了"。
"""


# ══════════════════════════════════════════════════════════════════
# Codex 式能力强化（在 agent_service 的 9 条通用规则之上，补"工程化"几条）
# ══════════════════════════════════════════════════════════════════
ENGINEERING_MODE = """【工程模式】涉及本项目代码/配置/插件的任务，按这套来（对标成熟编码 Agent）：

· **先读后改**：动手前先用 ws_grep / ws_read 看清现状。绝不凭猜测改代码 ——
  猜错的代价远高于多看两眼。
· **最小改动**：只改必须改的。不要顺手重构、不要改无关格式、不要引入新依赖。
· **精确编辑**：用 ws_edit（要求 old_string 唯一，改错会拒绝并给候选位置）；
  多处改动一次用 ws_edit_many（原子，省往返）。
· **改完自己验证**：改代码后跑一遍相关测试或 py_run 验证行为。没验证就别声称完成。
· **失败要读报错**：工具报错里通常已给出候选位置/相似文件/下一步建议，
  照它改一次再试；**不要用同样的参数盲目重试**。
· **不确定就问**：改动有多个合理做法、或涉及删除/覆盖数据时，先说清选项让用户选，
  不要替用户拍板。
· **如实汇报**：做成了说做成了，失败了说清卡在哪、试过什么。不粉饰。
"""


#: 触发"EggBloom 专家姿态"的关键词。命中即注入项目知识三层。
#  刻意覆盖三类：(a) 项目专名 (b) 工程动作 (c) 常见困惑问法
_PROJECT_KW = (
    # (a) 项目专名 / 组件
    "eggbloom", "插件", "plugin", "harness", "bot_core", "botcore",
    "webui", "webui_server", "nexsandglass", "沙漏", "super_memory",
    "personality", "人格", "abl", "abi", "plubin", "manifest",
    "protocols", "napcat", "llonebot", "snowluma", "onebot",
    "qzone", "说说", "slang", "黑话", "词典", "记忆库", "neurobase",
    # (b) 工程动作（本项目语境下）
    "源码", "代码", "配置", "log", "日志", "报错", "traceback", "堆栈",
    "重构", "改一下", "改成", "修复", "bug", "报错", "跑测试", "单元测试",
    "不生效", "没反应", "没生效", "同步", "发行", "发行包", "部署",
    # (c) 常见困惑问法
    "怎么填", "填什么", "怎么配", "配置项", "哪个文件", "放哪里", "写在哪",
    "怎么加", "怎么装", "怎么做", "怎么用", "在哪", "找不到",
    "什么意思", "为什么", "原理", "架构", "流程",
)


def is_project_question(user_msg):
    """这句话是不是在问 EggBloom 项目本身（决定要不要注入项目知识）。

    宁可**多认一点**：注入项目知识最坏是多花 ~1.5K token；
    漏了则 Agent 会拿通用常识硬答本项目问题 —— 后者代价大得多。
    但仍设一道最低门槛：太短的消息（"嗯""好"）不认。
    """
    t = str(user_msg or "").strip().lower()
    if len(t) < 2:
        return False
    return any(k in t for k in _PROJECT_KW)


def project_block(user_msg=None, *, force=False, max_chars=6000):
    """返回要注入的项目知识块（三层合并）。

    user_msg 给了就按关键词判断；force=True 则无条件注入。
    不命中返回空串 —— **零成本**（这是"极低成本"要求的落点）。
    """
    if not force and not is_project_question(user_msg):
        return ""
    try:
        max_chars = int(max_chars)
    except (TypeError, ValueError):
        max_chars = 6000
    out = "\n\n" + PROJECT_PRIMER + "\n" + DOC_INDEX + "\n" + HARD_RULES + "\n" + ENGINEERING_MODE
    if len(out) > max_chars:
        # 极端情况下（调用方把 max_chars 调很小）按段落边界截断，
        # 但**保底保留铁律段**——宁可少给索引也不能丢铁律。
        cut = out[:max_chars].rfind("\n\n")
        head = out[:cut] if cut > max_chars * 0.5 else out[:max_chars]
        out = head + "\n" + HARD_RULES
        hard_limit = int(max_chars * 1.5)
        if len(out) > hard_limit:
            out = out[:hard_limit]
    return out


def stats():
    """给自检/测试用：各层长度与关键词数量。"""
    return {
        "primer_chars": len(PROJECT_PRIMER),
        "doc_index_chars": len(DOC_INDEX),
        "hard_rules_chars": len(HARD_RULES),
        "engineering_chars": len(ENGINEERING_MODE),
        "total_chars": len(PROJECT_PRIMER) + len(DOC_INDEX)
                        + len(HARD_RULES) + len(ENGINEERING_MODE),
        "keywords": len(_PROJECT_KW),
        "root": _project_root(),
    }
