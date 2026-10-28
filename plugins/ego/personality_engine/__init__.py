# -*- coding: utf-8 -*-
"""
personality_engine 人格引擎插件 v0.2.0

核心能力（来自 personality-engine-final.md）：
- ConversationState：话题/模式/连续追问/情绪
- ExampleRetriever：按场景检索 few-shot 示例
- build_personality_prompt：动态组装 prompt
- post_process：禁用词/颜文字/长度/口头禅
- ActiveReplyEngine：空闲时主动搭话
- LearningEngine：夜间批量学习
- build_memory_context：叙事化记忆注入

注意：主逻辑作为插件提供，由 bot_core / 其他插件按需调用。
不强制侵入 bot_core.py。
"""

import json
import os
import random
import re
import time
from typing import Any, Dict, List, Optional

PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(PLUGIN_DIR, "data")
PERSONALITY_PATH = os.path.join(os.path.dirname(os.path.dirname(PLUGIN_DIR)), "personality.txt")
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")
EXAMPLES_DIR = os.path.join(os.path.dirname(os.path.dirname(PLUGIN_DIR)), "personality", "examples")
STATE_DIR = os.path.join(os.path.dirname(os.path.dirname(PLUGIN_DIR)), "conversation_state")
PROHIBITED_PATH = os.path.join(os.path.dirname(os.path.dirname(PLUGIN_DIR)), "personality", "prohibited_patterns.txt")

PLUGIN_META = {
    "name": "personality_engine",
    "version": "0.2.0",
    "description": "人格引擎：会话状态 + 记忆注入 + few-shot + 动态 prompt + 学习闭环",
    "author": "EggBloom",
    "group_only": False,
    "enabled": True,
    "dependencies": [],
    "hooks": ["before_reply", "after_reply"],
    "priority": 20,
    "estimated_tokens": 0,
    "tags": ["人格"],
    "page": "plugins/personality_engine/ui.html",
}

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(EXAMPLES_DIR, exist_ok=True)
os.makedirs(STATE_DIR, exist_ok=True)


def _load_json(path: str, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _save_json(path: str, data):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[PersonalityEngine] 保存失败 {path}: {e}")


def parse_personality(content: str) -> Dict[str, Any]:
    sections: Dict[str, str] = {}
    current_section = None
    current_content: List[str] = []

    for line in content.split("\n"):
        if line.startswith("【") and "】" in line:
            if current_section:
                sections[current_section] = "\n".join(current_content).strip()
            current_section = line.strip()
            current_content = []
        else:
            current_content.append(line)

    if current_section:
        sections[current_section] = "\n".join(current_content).strip()

    return {"raw": content, "sections": sections}


def _distill_persona() -> str:
    """蒸馏人格卡（由「这样脆弱的东西」插件发布），存在则优先于手写人设。

    读取的是插件的 data/published/active.persona.txt —— 由该插件的
    publish / disable / rollback 动作维护，这里只读不写。
    读取失败时静默回退到手写人设，绝不能因为蒸馏插件出问题而拖垮 bot。
    """
    try:
        # PLUGIN_DIR = app/plugins/ego/personality_engine → 三级 dirname 是 app 根
        _app = os.path.dirname(os.path.dirname(os.path.dirname(PLUGIN_DIR)))
        _p = os.path.join(_app, "plugins", "这样脆弱的东西",
                          "data", "published", "active.persona.txt")
        if _p and os.path.exists(_p):
            with open(_p, "r", encoding="utf-8") as f:
                content = f.read().strip()
            if content:
                return content
    except Exception:
        pass
    return ""


def get_personality() -> Dict[str, Any]:
    """读取人格，返回 raw/sections/config。

    优先级：蒸馏人格卡（若已发布）> personality.txt。
    """
    _distilled = _distill_persona()
    if _distilled:
        return {
            "raw": _distilled,
            "sections": parse_personality(_distilled).get("sections", {}),
            "config": get_config(),
        }

    if not os.path.exists(PERSONALITY_PATH):
        return {"raw": "", "sections": {}, "config": get_config()}

    try:
        with open(PERSONALITY_PATH, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception:
        return {"raw": "", "sections": {}, "config": get_config()}

    return {
        "raw": content,
        "sections": parse_personality(content).get("sections", {}),
        "config": get_config(),
    }


def save_personality(sections: Dict[str, str], config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """保存人格配置到 personality.txt，并可选更新 config"""
    parts = []

    section_order = [
        "【你是谁】",
        "【日常】",
        "【性格】",
        "【细节补充】",
        "【口头禅】",
        "【情感表达】",
        "【特殊情绪】",
        "【情绪关键词映射】",
        "【说话方式】",
        "【群聊行为】",
        "【遇到各种事】",
        "【绝对不能说的话】",
        "【图片生成】",
        "【自主学习成果】",
    ]

    for section in section_order:
        if section in sections:
            parts.append(section)
            content = sections[section].strip()
            if content:
                parts.append(content)

    for section, content in sections.items():
        if section not in section_order:
            parts.append(section)
            if content:
                parts.append(content)

    new_content = "\n".join(parts)

    try:
        with open(PERSONALITY_PATH, "w", encoding="utf-8") as f:
            f.write(new_content)
        if isinstance(config, dict):
            set_config(config)
        return {"success": True, "path": PERSONALITY_PATH}
    except Exception as e:
        return {"success": False, "error": str(e)}


def get_config() -> Dict[str, Any]:
    return _load_json(CONFIG_PATH, {
        "auto_backup": True,
        "backup_count": 5,
        "example_enabled": True,
        "active_reply": {
            "enable": False,
            "possibility": 0.1,
            "idle_minutes": 30,
            "whitelist": [],
        },
    })


def set_config(data: Dict[str, Any]) -> Dict[str, Any]:
    config = get_config()
    for key in ["auto_backup", "backup_count", "example_enabled"]:
        if key in data:
            config[key] = data[key]
    if "active_reply" in data and isinstance(data["active_reply"], dict):
        config["active_reply"] = data["active_reply"]
    _save_json(CONFIG_PATH, config)
    return config


# ------------------------------------------------------------------
# 会话状态
# ------------------------------------------------------------------
#: 群聊消息里的发送者前缀，两种写法都要能剥掉：
#    `[碱式碳酸大脑] 正文`   —— 抓取/转发格式
#    `碱式碳酸大脑：正文`     —— 记录格式
#  ★ 2026-10-23：加这个是因为 `_extract_topic` 用 `msg[:8]` 切，
#    切出来的**永远**是昵称（214 条真实语料，无一例外）。见该方法说明。
_SENDER_PREFIX_RE = re.compile(
    r"^\s*(?:\[[^\]]{0,40}\]\s*|[^\s：:\[\]]{1,16}\s*[：:]\s*)"
)

#: 媒体占位符。它们是"这里有张图"，不是内容，拿来当话题等于没话题。
_PLACEHOLDER_RE = re.compile(
    r"^\s*(?:\[图片\]|\[卡片\]|\[分享\].*|\[语音\]|\[视频\]|\[表情\]|\[动画表情\]|\[文件\])\s*$"
)

#: 行内占位符（2026-10-28）。整行是占位符时上面那条管用，但
#: `[A] 你和他们去吃饭了吗 [图片]` 这种"正文 + 图片"的形态它拦不住，
#: 于是取 12 字会切在 `[图片]` 中间，话题变成 `你和他们去吃饭了吗 [图`
#: —— 真机 1499 条里 141 条（9.4%）长这样。所以正文里的占位符要**先删掉**。
_INLINE_PLACEHOLDER_RE = re.compile(
    r"\[(?:图片|卡片|分享|语音|视频|表情|动画表情|文件|引用)[^\]]{0,40}\]"
)

#: 系统生成的文本，不是用户说的话（2026-10-28）。
#: 戳一戳会被 bot_core 合成为 `（戳了戳你，这是第 N 次）` 塞进 merged_text，
#: 真机 1499 条里 305 条（20.3%）的话题就是这个 —— 同一句、可预测、零信息。
_SYS_TEXT_RE = re.compile(r"^[（(]?\s*戳了戳你")

#: 指令型短句（`#sl` / `/help`）不是话题。真机 207 条（13.8%）是 `#sl`：
#: 一个词、没有语义，当话题只会让模型每轮都被告知"你们在聊 #sl"。
_CMD_RE = re.compile(r"^[#/／][A-Za-z0-9_\-]{1,24}$")

#: Bot 自己说的话在 merged_text 里带 `[我(Bot)]` 前缀。话题应该是**对方的**
#: 关注点，拿自己的话当话题会让下一轮自己接自己（真机 9 条，量小但错得明确）。
#:
#: ★ 注意 merged_text 的完整形态是 `[HH:MM] [昵称] 正文`
#:   （真机实证：`合并消息: [09:24] [我(Bot)] 然后呢`），
#:   时间标签在昵称前面，所以必须先剥掉它才认得出"是我自己说的"。
_SELF_LINE_RE = re.compile(r"^\s*\[我\s*\(?Bot\)?\]")
_TIME_TAG_RE = re.compile(r"^(?:\[\d{1,2}:\d{2}(?::\d{2})?\]\s*)+")

#: 开头的 @ 提及（可能叠多个）。`@AAA无理由退货   ` 剥完是空串，
#: 应当跳过而不是当成话题。
#:
#: ★ 必须用 `(?=\s|$)` 收尾：中文没有空格，裸写 `@\S{1,30}` 会把
#:   `@小a帮我看看这个` 整句吃掉，话题直接变空（真机没出现是因为 QQ
#:   总在 @ 后面补空格 —— 实测 40 个 @ 全部带空格，但不能靠这个巧合）。
_AT_PREFIX_RE = re.compile(r"^(?:@[^\s@]{1,40}(?=\s|$)\s*)+")
#: 没有任何分隔的 @（`@小a帮我看看`）：只剥掉 @ 本身，不猜名字在哪结束。
_BARE_AT_RE = re.compile(r"^@+")


def _clip_topic(s: str, n: int = 12) -> str:
    """截到 n 字，但不把话题切在一个没闭合的 `[` 里。

    中文没有空格可切，硬截是常态；唯一必须处理的是"截断后留下半个占位符"，
    那看起来像乱码，还会让模型以为话题里有个方括号。
    """
    s = (s or "").strip()
    if len(s) <= n:
        return s
    cut = s[:n]
    i = cut.rfind("[")
    if i >= 0 and "]" not in cut[i:]:
        # 截断点落在一个未闭合的 `[` 里 → 退回到它之前
        cut = cut[:i].rstrip()
    return cut


def _strip_sender_prefix(text: str) -> str:
    """剥掉消息开头的昵称前缀。没有前缀就原样返回。"""
    t = str(text or "").strip()
    if not t:
        return ""
    try:
        # 可能叠了两层（转发里带原始记录），最多剥两次
        for _ in range(2):
            new = _SENDER_PREFIX_RE.sub("", t, count=1).strip()
            if new == t:
                break
            t = new
            if not t:
                break
    except Exception:
        return str(text or "").strip()
    # ★ 2026-10-23：剥完只剩空串时，原实现会因为 `new == t` 提前 break
    #   而把带括号的昵称原样留下（「[某人] 」→ 仍带 `[某人] `）。
    #   这类消息在群里就是纯昵称、没正文，话题应当为空而不是昵称。
    return "" if not t else t


#: 多久没说话就当作新会话（秒）。超过它，连续追问计数归零、话题重取。
#: 30 分钟是群聊里"这一轮还算同一段对话"的经验值：比它长的间隔，
#: 上一句的追问链已经断了，硬接着数只会让注入的话题停在几天前。
_IDLE_RESET_SEC = 30 * 60

#: 连续追问计数的封顶。它只用于给提示词一点"聊到第几轮"的氛围，
#: 不是审计数据 —— 无限涨没有信息量，还会白占 token（真机到过 93）。
_TURN_CAP = 9


class ConversationState:
    """轻量会话状态，不搞复杂权重。"""

    def __init__(self, user_id: str = "default"):
        self.user_id = user_id
        self.current_topic: Optional[str] = None
        self.mode: str = "normal"
        self.turn_count: int = 0
        self.last_user_msg: str = ""
        self.last_bot_reply: str = ""
        self.emotion: str = "neutral"
        self.affection: float = 0.5
        self.last_update: float = time.time()
        self._load()

    def _path(self) -> str:
        return os.path.join(STATE_DIR, f"{self.user_id}.json")

    def _load(self):
        data = _load_json(self._path(), {})
        for key in [
            "current_topic",
            "mode",
            "turn_count",
            "last_user_msg",
            "last_bot_reply",
            "emotion",
            "affection",
            "last_update",
        ]:
            if key in data:
                setattr(self, key, data[key])
        # ★ 2026-10-28：老状态文件里存着棘轮涨上去的值（真机到过 93）。
        #   不夹一下的话，进程重启后第一轮 to_prompt() 仍会把 93 写进提示词，
        #   要等下一次 after_reply 才自愈 —— 而那一轮已经说错话了。
        try:
            self.turn_count = max(0, min(int(self.turn_count or 0), _TURN_CAP))
        except (TypeError, ValueError):
            self.turn_count = 0

    def _save(self):
        _save_json(self._path(), {
            "current_topic": self.current_topic,
            "mode": self.mode,
            "turn_count": self.turn_count,
            "last_user_msg": self.last_user_msg,
            "last_bot_reply": self.last_bot_reply,
            "emotion": self.emotion,
            "affection": self.affection,
            "last_update": self.last_update,
        })

    def update(self, user_msg: str, bot_reply: str):
        # ★ 2026-10-28：先判"隔了多久"，再改 last_update。
        #   顺序反了的话 gap 恒为 0，超时重置永远不触发。
        _now = time.time()
        try:
            _gap = _now - float(self.last_update or 0)
        except (TypeError, ValueError):
            _gap = _IDLE_RESET_SEC + 1          # 时间戳坏了 → 当新会话处理
        if _gap > _IDLE_RESET_SEC:
            # 半小时以上没说话，上一句接不上了：计数归零、话题重取。
            # 不重置的话，跨天的两段对话会被当成"同一次追问链"。
            self.turn_count = 0
            self.current_topic = None

        self.last_user_msg = user_msg
        self.last_bot_reply = bot_reply
        self.last_update = _now

        if self._is_follow_up(user_msg):
            # 封顶：这个数字只用来给提示词一点"聊到第几轮"的氛围，
            # 不是审计数据。真机曾涨到 93 —— 每轮都在告诉模型
            # 「你已经连续追问 93 次」，既假又白占 token。
            self.turn_count = min(self.turn_count + 1, _TURN_CAP)
        else:
            self.turn_count = 0
            # ★ 2026-10-28：抽不出话题时**保留**上一个，不要清空。
            #   真机 1499 条合并消息里 584 条（39%）是纯图片/表情，
            #   `_extract_topic` 对它们返回空串 —— 直接赋值等于每发一张图
            #   就把"在聊什么"抹掉，下一轮模型完全失去上下文。
            #   话题的失效交给 `_IDLE_RESET_SEC`（半小时没说话就重开），
            #   而不是交给"这条消息没有文字"。
            _new_topic = self._extract_topic(user_msg)
            if _new_topic:
                self.current_topic = _new_topic

        if self._is_serious(user_msg):
            self.mode = "serious"
        elif self._is_playful(user_msg):
            self.mode = "playful"
        else:
            self.mode = "normal"

        self.emotion = self._detect_emotion(user_msg)
        self._save()

    def _is_follow_up(self, msg: str) -> bool:
        """这一句**本身**是不是追问。只看消息，不看历史。

        ★ 2026-10-28 修：原实现末尾带了 `or self.turn_count > 0`，
          于是**只要追问过一次，之后每一句都算追问** —— turn_count
          变成只升不降的棘轮（真机涨到 93），更糟的是走不到 else 分支，
          `current_topic` 从此**永不更新**（日志里话题长期停在
          「[(/ω＼)]」这种旧昵称上，模型一直被告知"你们在聊这个"）。

          判据必须只依赖入参：一句是不是追问，跟上一句有没有追问无关。
          连续段落的延续性交给 `update()` 里的空闲重置 + 计数封顶处理。
        """
        follow_up_hints = ["呢", "那", "然后", "还有", "再说", "再说说", "往下", "继续"]
        return any(msg.endswith(h) or f" {h}" in msg for h in follow_up_hints)

    def _extract_topic(self, msg: str) -> str:
        """取一句能代表"在聊什么"的短句。

        ★ 2026-10-23：原先是 `msg[:8]`，实测**每一句都截到了昵称前缀**。
          群消息的形态是 `[昵称] 正文`，`[:8]` 切出来是 `[碱式碳酸大脑]`、
          `[(/ω＼)] `；214 条真实语料里，提取出的「话题」214 条全是昵称，
          没有一条是话题。也就是说这个字段一直在给模型喂假情报：
          它被告知「话题=[碱式碳酸大脑]」，而真相是那个群友叫这个名字。

        ★ 2026-10-23 第二轮：剥完前缀仍不对 —— merged_text 是**多条消息拼的**，
          形如 `[A] 你在哪\n[B] 你认识群主吗`。取 12 字会跨到换行后面，
          又截到 `[B]`。所以只能取**第一行**，且跳过 `[图片]`/`[分享]`
          这类占位符（它们不是内容，拿来当话题等于没话题）。

        ★ 2026-10-28 第三轮（真机 1499 条合并消息逐条跑出来的）：
          前两轮只解决了"别把昵称当话题"，剩下四类垃圾占了近一半 ——
            · `（戳了戳你，这是第 1 次）`  305 条（20.3%），系统生成，零信息
            · `#sl`                        207 条（13.8%），指令词，没有语义
            · `你和他们去吃饭了吗 [图`      141 条（9.4%），切在占位符中间
            · `@AAA无理由退货   `            39 条（2.6%），只剩一个 @
          另外 584 条（39.0%）是纯图片/表情，返回空串 —— 这是对的，
          但 `update()` 会因此把话题**清空**，下一轮模型就完全不知道在聊什么。
          现在：跳过前两类、先删行内占位符再截断、剥掉 @、不清空只覆盖。
        """
        # ★ 不要在这里先对整个 text 剥一次前缀（2026-10-28）：
        #   那样 `[我(Bot)] xxx` 会被剥成 `xxx`，下面 `_SELF_LINE_RE` 就认
        #   不出"这是我自己说的话"，反而把它当成了话题。
        #   剥前缀只需要逐行做（`_strip_sender_prefix` 内部已经循环两次）。
        text = str(msg or "").strip()
        if not text:
            return ""
        # 只取第一条有实际内容的消息行
        for line in text.split("\n"):
            line = _TIME_TAG_RE.sub("", line)
            if _SELF_LINE_RE.match(line):
                continue
            s = _strip_sender_prefix(line).strip()
            if not s:
                continue
            # @ 提及可能叠多个，剥完只剩空就跳过这一行
            _s2 = _AT_PREFIX_RE.sub("", s).strip()
            if _s2 == s and s.startswith("@"):
                # 没匹配上"@ + 名字 + 空格"的形态（例如 `@小a帮我看看`）：
                # 只剥 @ 本身，别把正文一起吃掉
                _s2 = _BARE_AT_RE.sub("", s).strip()
            s = _s2
            if not s:
                continue
            if _PLACEHOLDER_RE.fullmatch(s):
                continue
            # 正文里的占位符先删（`... [图片]` → `...`），再判断是不是空
            s = _INLINE_PLACEHOLDER_RE.sub("", s).strip()
            if not s:
                continue
            # 系统生成的戳一戳文本、纯指令词，都不是话题
            if _SYS_TEXT_RE.match(s) or _CMD_RE.match(s):
                continue
            return _clip_topic(s, 12)
        return ""

    def _is_serious(self, msg: str) -> bool:
        serious_keywords = ["难受", "抱歉", "对不起", "麻烦", "帮帮我", "怎么办", "焦虑", "抑郁", "累", "压力"]
        return any(k in msg for k in serious_keywords)

    def _is_playful(self, msg: str) -> bool:
        playful_keywords = ["哈哈", "笑死", "好耶", "太强了", "牛", "666", "🐮", "草", "绷不住了"]
        return any(k in msg for k in playful_keywords)

    def _detect_emotion(self, msg: str) -> str:
        positive = ["开心", "哈哈", "好耶", "谢谢", "喜欢", "爱", "棒", "厉害", "笑", "舒服"]
        negative = ["难受", "烦", "讨厌", "难过", "哭", "累", "压力", "焦虑", "抑郁", "吐"]
        if any(k in msg for k in positive):
            return "happy"
        if any(k in msg for k in negative):
            return "sad"
        return "neutral"

    def to_prompt(self) -> str:
        """生成会话状态行。

        ★ 2026-10-21 修复：原先这里固定输出 `好感度={self.affection:.1f}`，
        但 `self.affection` 自 0.5 起**从来没有被任何代码改过**（update() 里
        没有 affection 分支），所以它对每个用户永远是「好感度=0.5」——
        一条恒定不变、还长得像动态数据的假情报。
        真正的好感度在 super_memory 的 affection 表（0–100，含分档），
        由 super_memory 自己以【用户关系】注入。两者同时进提示词时，
        模型会看到「好感度=0.5」和「好感度 65，好友」两个互相打脸的结论。
        所以这里**不再输出** affection —— 只报真正会被更新的那几项。

        ★ 2026-10-23：加"不变化就不写"的过滤。真实语料统计（214 条群消息）：
          情绪 neutral 203 条 / happy 11 条，模式 normal 210 条 / playful 4 条。
          也就是说这两项**绝大多数时候都是默认值**，写出来等于每轮都告诉模型
          「当前气氛正常、情绪中性」—— 一句恒定的、占 token 的废话。
          而且它还会覆盖模型自己的判断：群聊里模型本来能读出"这句是在骂人"，
          但旁边写着「情绪=neutral」，它就没有理由凶了。
          规则：默认值不输出，只有真的偏离默认时才说。
        """
        bits = []
        if self.current_topic:
            bits.append(f"话题={self.current_topic}")
        # 默认值不写（normal / neutral 就是没说）
        if self.mode and self.mode != "normal":
            bits.append(f"模式={self.mode}")
        if self.emotion and self.emotion != "neutral":
            bits.append(f"情绪={self.emotion}")
        # 单独一轮不算"连续追问"，说了也是噪声
        if self.turn_count >= 2:
            bits.append(f"连续追问={self.turn_count}")
        if not bits:
            return ""
        return "当前对话状态：" + "，".join(bits)


def get_state(user_id: str = "default") -> ConversationState:
    return ConversationState(user_id)


# ------------------------------------------------------------------
# 记忆上下文
# ------------------------------------------------------------------
def build_memory_context(
    user_id: str,
    message: str,
    state: ConversationState,
    private_memory=None,
    self_learning=None,
    super_memory=None,
) -> str:
    parts = [state.to_prompt()]

    if super_memory and hasattr(super_memory, "recall"):
        try:
            results = super_memory.recall(user_id, message, top_k=3)
            if results:
                parts.append("相关记忆：" + "；".join(str(r) for r in results[:3]))
        except Exception:
            pass

    if private_memory and hasattr(private_memory, "get_relevant"):
        try:
            mem = private_memory.get_relevant(user_id, message)
            if mem:
                parts.append("私聊记忆：" + str(mem))
        except Exception:
            pass

    if self_learning and hasattr(self_learning, "get_style_hints"):
        try:
            hints = self_learning.get_style_hints(user_id)
            if hints:
                parts.append("学习特征：" + str(hints))
        except Exception:
            pass

    return "\n".join(parts) if parts else ""


# ------------------------------------------------------------------
# prompt 组装
# ------------------------------------------------------------------
def build_personality_prompt(
    state: ConversationState,
    memory_ctx: str,
    examples: List[Dict[str, str]],
    user_msg: str,
    user_id: str = "preview",
) -> str:
    parts = []

    if memory_ctx:
        parts.append(memory_ctx)

    if examples:
        parts.append("参考示例：")
        for idx, ex in enumerate(examples[:2], 1):
            user_example = ex.get("user", ex.get("input", ""))
            bot_example = ex.get("bot", ex.get("reply", ""))
            if user_example and bot_example:
                # ★ 2026-10-23：多行示例要按行加缩进。
                #   原实现直接 f-string 拼接，人格里的示例又都是多行（
                #   「是啊\n怎么突然问这个」），拼出来是：
                #       1. 用户：你是不是AI啊
                #          小a：是啊
                #       怎么突然问这个
                #   第二行掉到顶格，编号结构被破坏，模型看到的是"示例之外
                #   还有一条无主的行"。而群里真人回复几乎都是多行短句，
                #   这恰恰是最该示范的形态 —— 结构错了示范就白给。
                _bot_lines = str(bot_example).split("\n")
                _bot_block = "\n".join("      " + ln.strip() for ln in _bot_lines if ln.strip())
                parts.append(f"{idx}. 用户：{str(user_example).strip()}\n   小a：{_bot_block.strip()}")

    if state.mode == "serious":
        parts.append("注意：当前用户情绪偏严肃，回复更稳、更简洁，少玩梗。")
    elif state.mode == "playful":
        parts.append("注意：当前气氛轻松，可以适当玩梗，保持活泼。")

    if state.emotion == "happy":
        parts.append("用户看起来比较开心，可以更放松。")
    elif state.emotion == "sad":
        parts.append("用户看起来有点低落，先接住情绪，别急着开玩笑。")

    return "\n".join(parts) if parts else ""


# ------------------------------------------------------------------
# 示例检索
# ------------------------------------------------------------------
class ExampleRetriever:
    def __init__(self, base_path: str = EXAMPLES_DIR):
        self.base_path = base_path
        self.examples = self._load_all(base_path)

    def _load_all(self, base_path: str) -> Dict[str, List[Dict[str, str]]]:
        examples: Dict[str, List[Dict[str, str]]] = {}
        if not os.path.isdir(base_path):
            return examples
        for name in os.listdir(base_path):
            if not (name.endswith(".yaml") or name.endswith(".json")):
                continue
            scene = name.rsplit(".", 1)[0]
            path = os.path.join(base_path, name)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    examples[scene] = data
                elif isinstance(data, dict):
                    examples[scene] = [data]
            except Exception:
                pass
        return examples

    def retrieve(self, message: str, state: ConversationState, top_k: int = 2) -> List[Dict[str, str]]:
        """挑 few-shot 示例。打分看两个来源：场景名，以及**示例自己的问句**。

        ★ 2026-10-21 修复：原实现把所有场景**无条件**塞进 candidates，
        零分场景也会参与排序。top_k=2 时，只要示例库超过 2 个场景，
        就必然会把「一个都没命中的场景」当成参考示例注入 —— 等于随机配货，
        跟当前这句话毫无关系，反而污染人格。现在零分场景直接丢弃，
        全都不命中就返回空（宁可不给示例，也不给错的示例）。

        ★ 2026-10-23：只看场景名会漏得很厉害。场景名是文件名
        （`AI_你是AI吗_机器人_GPT_人工智能`），拆成下划线当关键词用，
        所以「你是真人吗」这种说法一条都命中不了 —— 而它恰恰是最需要
        示例兜住的一类（问身份），命中不了就只能靠模型自由发挥。
        现在把**示例里的问句**也当关键词来源，按字符 n-gram 重合度打分。
        打分刻意做得很粗：只要有两三个字对得上就够，不需要语义理解。
        """
        if not self.examples:
            return []
        text = str(message or "").lower()
        text_bg = _bigrams(text)
        candidates = []
        for scene, items in self.examples.items():
            score = 0
            lowered = scene.lower()
            if lowered in text:
                score += 3
            for kw in lowered.split("_"):
                if kw and kw in text:
                    score += 2
            # 示例问句与这句话的字面重合（二元组），封顶免得长句占尽便宜
            #  ★ 2026-10-23：阈值不能是 1。实测「你好」和「你好厉害啊」
            #    会共享「你好」这一个二元组 → 把夸赞示例配给一句打招呼；
            #    「今天天气不错」和「今天好累啊」共享「今天」→ 配给心情不好示例。
            #    这种错误示例比不给示例更糟（模型会照着错误语境说话）。
            #    要求「短句至少一半的字重合」，长句按 3 个封顶。
            for ex in (items or []):
                q = str(ex.get("user") or ex.get("input") or "").lower()
                if not q:
                    continue
                q_bg = _bigrams(q)
                if not q_bg:
                    continue
                overlap = len(text_bg & q_bg)
                if not overlap:
                    continue
                need = max(2, len(q_bg) // 2) if len(q_bg) <= 6 else 3
                if overlap >= need:
                    score += min(3, overlap)
            if score <= 0:          # ← 没命中就别当示例
                continue
            candidates.append((score, scene, items))

        # ★ 2026-10-23：身份问句单独兜一层，且只兜**身份场景**。
        #   字面重合在这里必然失效：「你是真人吗」和示例「你是不是AI啊」
        #   只共享「你是」一个二元组，达不到阈值 → 0 条。而问身份恰恰是
        #   最不能自由发挥的一类（答错就等于不承认自己是 AI）。
        #   所以不靠字面，靠**问法枚举**；但必须限定只在身份场景里加分，
        #   否则会给所有场景一起抬分，等于没判。
        if _is_identity_question(text):
            for scene, items in self.examples.items():
                if not _is_identity_scene(scene):
                    continue
                if not any(c[1] == scene for c in candidates):
                    candidates.append((9, scene, items))
        if not candidates:
            return []
        candidates.sort(key=lambda x: (x[0], x[1]), reverse=True)
        results: List[Dict[str, str]] = []
        for _, _scene, items in candidates[:top_k]:
            results.extend(items[:3])
        return results[:top_k]


def _bigrams(text: str) -> set:
    """中文没有词边界，用二元组做粗重合度。扣掉标点与空白。"""
    t = re.sub(r"[\s，。！？、；：,.!?;:~…\-—_（）()\"'“”‘’]+", "", str(text or ""))
    return {t[i:i + 2] for i in range(len(t) - 1)}


#: 「在问你是不是人/AI」的问法。枚举而不是靠字面重合，理由见 retrieve。
_IDENTITY_ASK_KW = (
    "你是ai", "是不是ai", "是ai吗", "你是机器人", "是机器人", "是不是人",
    "真人", "是人吗", "你是人", "你是gpt", "是gpt", "你是不是程序",
    "你是程序", "是模型吗", "你是模型", "人机", "你是假的", "你不是人",
    "你到底是什么", "你是啥", "你是什么东西",
)


def _is_identity_question(text: str) -> bool:
    t = str(text or "").lower()
    return any(k in t for k in _IDENTITY_ASK_KW)


def _is_identity_scene(scene: str) -> bool:
    """场景名是不是"身份问答"这一类。文件名形如 `AI_你是AI吗_机器人_GPT_人工智能`。"""
    s = str(scene or "").lower()
    return ("ai" in s) or ("机器人" in s) or ("gpt" in s) or ("人工智能" in s)


def get_example_retriever() -> ExampleRetriever:
    return ExampleRetriever()


# ------------------------------------------------------------------
# 动态温度
# ------------------------------------------------------------------
def get_dynamic_temperature(state: ConversationState) -> float:
    base = 0.75
    if state.mode == "serious":
        base = 0.6
    elif state.mode == "playful":
        base = 0.9
    if state.emotion == "happy":
        base += 0.1
    elif state.emotion == "sad":
        base -= 0.1
    return max(0.4, min(1.2, base))


# ------------------------------------------------------------------
# 后处理
# ------------------------------------------------------------------
def post_process(reply: str, state: ConversationState) -> str:
    prohibited = load_prohibited_patterns()
    for pattern in prohibited:
        if not pattern or pattern not in reply:
            continue
        # ★ 2026-10-21：子串删除必须防误伤。
        #   实测「不建议你这么做」被删成「不这么做」——语义直接反过来，
        #   比原句更糟。所以只在 pattern **前面不是否定词**时才删。
        #   （中文没有词边界，这里用否定前缀做最小可行的护栏。）
        if pattern in _NEGATION_GUARDED:
            reply = re.sub(r"(?<![不没别勿])" + re.escape(pattern), "", reply)
        else:
            reply = reply.replace(pattern, "")
    reply = _tidy_after_strip(reply)
    reply = reply.strip()
    if len(reply) > 240:
        reply = reply[:240].rstrip() + "…"
    return reply


# 这些词前面可能挂否定词，删之前要看一眼（"不建议你" ≠ "建议你"）
_NEGATION_GUARDED = frozenset({
    "建议你", "希望对你有所帮助", "希望对你有帮助", "需要我帮你",
})


def _tidy_after_strip(text: str) -> str:
    """删词之后收拾残局：去空行、清句首标点、并连标点。

    删词留下的坑是「。 ，」这种**标点撞标点**：原句被切成
    「作为AI」+「，我无法回答这个问题」+「。」，删掉词之后前一段空了，
    后一段又自带逗号，拼起来就是「，这个问题。，多喝水。」。
    所以清理要按**句**做，先切句、再逐句剥掉开头标点、最后重拼。
    """
    if not text:
        return text
    out_lines = []
    for line in text.split("\n"):
        s = line.strip()
        if not s:
            continue
        # 先按句末标点切段，逐段剥掉行首标点
        segs = re.split(r"(?<=[。！？!?])", s)
        cleaned = []
        for seg in segs:
            seg = seg.strip()
            seg = re.sub(r"^[，。、；：,.;:\s]+", "", seg)
            if seg:
                cleaned.append(seg)
        s = "".join(cleaned)
        # 撞在一起的标点合并：。， / ，。 / 。。 ……
        s = re.sub(r"[，、；：,;:]+(?=[。！？!?])", "", s)
        s = re.sub(r"(?<=[。！？!?])[，、；：,;:]+", "", s)
        s = re.sub(r"([。！？])\1+", r"\1", s)
        s = re.sub(r"[，]{2,}", "，", s)
        s = s.strip()
        if s:
            out_lines.append(s)
    return "\n".join(out_lines)


def load_prohibited_patterns() -> List[str]:
    """读禁用词表。文件里一行一个词，不支持注释。

    ★ 2026-10-23：身份词只禁**套话**，不禁**直陈**。
      表里留着「作为AI」「作为一个语言模型」「我是助手」「我是AI助手」——
      这些是客服开场腔 + 回避话术（"作为AI，我无法回答"），删掉无害。
      但「我是AI」「我是一个AI」「我是人工智能」**不能进表**：
      人格已改成被问就承认，这里再删只会逼模型绕弯子撒谎，
      改了 prompt 也看不出效果。判定标准是「像客服」而非「提到身份」。

    ★ 2026-10-23：返回前按长度**降序**排序。子串删除对顺序敏感，
      表里短词在前就会吃掉长词的前缀：
        「作为AI助手」先命中「作为AI」→ 删完剩「助手」
        「需要我帮你吗」先命中「需要我帮你」→ 删完剩「吗」
      长词先删，短词才轮得到它自己该删的部分。
    """
    try:
        if not os.path.exists(PROHIBITED_PATH):
            return []
        with open(PROHIBITED_PATH, "r", encoding="utf-8") as f:
            pats = [line.strip() for line in f if line.strip()]
        return sorted(pats, key=len, reverse=True)
    except Exception:
        return []


# ------------------------------------------------------------------
# 主动回复
# ------------------------------------------------------------------
class ActiveReplyEngine:
    def __init__(self, cfg: Optional[Dict[str, Any]] = None):
        self.cfg = cfg or get_config().get("active_reply", {})
        self.enable = bool(self.cfg.get("enable", False))
        self.possibility = float(self.cfg.get("possibility", 0.1))
        self.idle_minutes = int(self.cfg.get("idle_minutes", 30))
        self.whitelist = list(self.cfg.get("whitelist", []))

    def should_reply(self, user_id: str, group_id: str) -> bool:
        if not self.enable:
            return False
        if self.whitelist and user_id not in self.whitelist:
            return False
        if random.random() > self.possibility:
            return False
        return True

    def pick_topic(self, state: ConversationState) -> str:
        candidates = [
            "在干嘛",
            "最近有没有遇到好玩的事",
            "今天过得怎么样",
            "有没有在摸鱼",
            "要不要聊点轻松的",
        ]
        if state.mode == "serious":
            candidates = ["要不多聊聊？", "需要我陪着说会儿吗？", "想说说看吗？"]
        return random.choice(candidates)


def get_active_reply_engine() -> ActiveReplyEngine:
    return ActiveReplyEngine(get_config().get("active_reply"))


# ------------------------------------------------------------------
# 学习引擎
# ------------------------------------------------------------------
class LearningEngine:
    def learn(self, recent_dialogues: List[Dict[str, str]], example_retriever: Optional[ExampleRetriever] = None):
        if not recent_dialogues:
            return
        scores: List[float] = []
        for item in recent_dialogues:
            reply = item.get("bot", "")
            score = 0.0
            if any(c in reply for c in ["…", "！", "?"]):
                score += 0.5
            if any(c in reply for c in ["哈哈", "真的假的", "笑死", "好家伙"]):
                score += 0.5
            if 10 <= len(reply) <= 120:
                score += 0.5
            scores.append(score)

        avg = sum(scores) / len(scores)
        high_quality = [
            recent_dialogues[i] for i, s in enumerate(scores) if s >= max(avg, 2.5)
        ][:5]

        self._save_learning_candidates(high_quality)
        low_quality = [
            recent_dialogues[i] for i, s in enumerate(scores) if s < 1.0
        ][:5]
        self._save_prohibited([item.get("bot", "") for item in low_quality if item.get("bot")])

    def _save_learning_candidates(self, candidates: List[Dict[str, str]]):
        path = os.path.join(DATA_DIR, "learning_candidates.json")
        try:
            existing = _load_json(path, [])
            if not isinstance(existing, list):
                existing = []
            existing.extend(candidates)
            _save_json(path, existing[:50])
        except Exception as e:
            print(f"[PersonalityEngine] 保存 learning_candidates 失败: {e}")

    def _save_prohibited(self, replies: List[str]):
        try:
            existing = set(load_prohibited_patterns())
            for reply in replies:
                reply = reply.strip()
                if reply and reply not in existing:
                    existing.add(reply)
            with open(PROHIBITED_PATH, "w", encoding="utf-8") as f:
                for item in sorted(existing):
                    f.write(item + "\n")
        except Exception as e:
            print(f"[PersonalityEngine] 保存 prohibited 失败: {e}")


def get_learning_engine() -> LearningEngine:
    return LearningEngine()


# ------------------------------------------------------------------
# 示例库
# ------------------------------------------------------------------
def get_examples(scene: str = "") -> Dict[str, Any]:
    try:
        files = []
        if not os.path.isdir(EXAMPLES_DIR):
            return {"examples": [], "scenes": []}
        for name in os.listdir(EXAMPLES_DIR):
            if not (name.endswith(".yaml") or name.endswith(".json")):
                continue
            files.append(name.rsplit(".", 1)[0])
        scenes = sorted(files)
        examples = []
        for s in scenes:
            path = os.path.join(EXAMPLES_DIR, s + ".json")
            if not os.path.exists(path):
                continue
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if scene and s != scene:
                    continue
                if isinstance(data, list):
                    examples.extend(data)
                elif isinstance(data, dict):
                    examples.append(data)
            except Exception:
                pass
        return {"examples": examples, "scenes": scenes}
    except Exception as e:
        return {"examples": [], "scenes": [], "error": str(e)}


def save_examples(data: Dict[str, Any]) -> Dict[str, Any]:
    examples = data.get("examples") if isinstance(data, dict) else None
    if examples is None and isinstance(data, list):
        examples = data
    if not isinstance(examples, list):
        return {"success": False, "error": "examples must be a list"}
    path = os.path.join(EXAMPLES_DIR, "examples.json")
    try:
        os.makedirs(EXAMPLES_DIR, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(examples, f, ensure_ascii=False, indent=2)
        return {"success": True, "path": path}
    except Exception as e:
        return {"success": False, "error": str(e)}


# ------------------------------------------------------------------
# 插件钩子
# ------------------------------------------------------------------
def _get_user_id(context: Optional[dict] = None) -> str:
    ctx = context or {}
    return str(ctx.get("sender") or ctx.get("user_id") or "default")


def before_reply(message: str = "", group_id: str = "", context: Optional[dict] = None, **kwargs) -> Optional[dict]:
    """before_reply 钩子：注入人格引擎的 prompt/温度，不改动主流程。"""
    try:
        user_id = _get_user_id(context)
        state = get_state(user_id)
        config = get_config()
        use_examples = config.get("example_enabled", True)
        retriever = get_example_retriever()
        examples = retriever.retrieve(message, state, top_k=2) if use_examples else []
        memory_ctx = build_memory_context(
            user_id,
            message,
            state,
            private_memory=kwargs.get("private_memory"),
            self_learning=kwargs.get("self_learning"),
            super_memory=kwargs.get("super_memory"),
        )
        prompt = build_personality_prompt(state, memory_ctx, examples, message)
        temperature = get_dynamic_temperature(state)
        return {
            "action": "inject",
            "content": prompt,
            "temperature": temperature,
            "personality_state": {
                "topic": state.current_topic,
                "mode": state.mode,
                "emotion": state.emotion,
                "turn_count": state.turn_count,
            },
        }
    except Exception as e:
        print(f"[PersonalityEngine] before_reply error: {e}")
        return None


def after_reply(message: str = "", group_id: str = "", reply: str = "", context: Optional[dict] = None, **kwargs) -> Optional[dict]:
    """after_reply 钩子：更新会话状态，并做轻度后处理。"""
    try:
        user_id = _get_user_id(context)
        state = get_state(user_id)
        state.update(message, reply)
        processed = post_process(reply, state)
        if processed != reply:
            return {"action": "override", "reply": processed}
        return None
    except Exception as e:
        print(f"[PersonalityEngine] after_reply error: {e}")
        return None


# ------------------------------------------------------------------
# 对外接口
# ------------------------------------------------------------------
__all__ = [
    "PLUGIN_META",
    "get_personality",
    "save_personality",
    "get_config",
    "set_config",
    "get_examples",
    "save_examples",
    "ConversationState",
    "ExampleRetriever",
    "build_memory_context",
    "build_personality_prompt",
    "post_process",
    "get_active_reply_engine",
    "get_learning_engine",
    "before_reply",
    "after_reply",
    "load_prohibited_patterns",
]


def on_load():
    print("[PersonalityEngine] 人格引擎 v0.2.0 已启用")

# ── 通用插件通道动作（WebUI plugin_id='ego.personality_engine'） ──
def _action_save_personality(params=None):
    params = params or {}
    sections = params.get("sections") or params.get("personality") or {}
    return save_personality(sections)


def _action_save_examples(params=None):
    params = params or {}
    return save_examples(params.get("examples") or [])


PLUGIN_ACTIONS = {
    "get_config": lambda params: get_config(),
    "set_config": lambda params: set_config(params or {}),
    "get_personality": lambda params: get_personality(),
    "save_personality": _action_save_personality,
    "get_examples": lambda params: get_examples(str((params or {}).get("scene") or "")),
    "save_examples": _action_save_examples,
    "get_state": lambda params: get_state(),
}
