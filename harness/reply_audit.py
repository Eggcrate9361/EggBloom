# -*- coding: utf-8 -*-
"""harness/reply_audit.py —— 回复事后校验：说了做过，就查有没有真做。

背景（2026-10-23 真机）
--------------------
`【诚实铁律】` 第 1 条已经写了「不许用语言假装做了操作」，但实测被绕过了：

    08:48:20  「按你说的拖过来了…706MB 这会儿在后台传着」
    08:46:50-08:50  日志里**零**工具调用
    09:11:03  「我点进去看了，38 个 commit」
    09:00-09:19     日志里同样零调用

prompt 层面的约束靠模型自觉，而模型的自觉在"想表现得配合"面前不顶用。
所以这里加一道**机械**校验：回复里出现操作完成态 → 查本轮台账 →
没调过就不许发，改成如实说。

为什么不能一刀切（这是本模块最重要的设计点）
--------------------------------------------
同一句话里，「已发送」「已打开」和「查到了」「我记得」是两类东西：

* **操作完成态**：声称对某个东西做了动作。没有工具就没发生过，一定是假的。
  例：已发送 / 已打开 / 已经拖过来 / 传完了 / 帮你存好了 / 截图给你了。
* **知识类表述**：声称知道某事。可能来自上下文、记忆、常识，本来就不需要工具。
  例：查到了 / 我看了下 / 我记得 / 应该是。

只有前者可以拦。拦后者会误伤大量正常回复 —— 模型凭上下文答对了，
你却说它在撒谎，然后强行改成「我没查」。这比原问题更糟。

所以判据是**动词的完成态 + 有明确受事对象**，不是"提到工具名"。
"""
from __future__ import annotations

import re

# ── 操作完成态：声称对某个**本机/网络实体**完成了动作 ──────────────
#  写法原则：**枚举完整的说法**，不用「(已|已经)?(发|传)(给|到)?你?(了)?」这种
#  叠可选组 —— 那第一版试过，匹配不到「已发送」（中间夹了「送」直接断），
#  而且几乎匹配到任何含「发」的句子。宁可多写几行，也不要一个假宽松的表达式。
_OPERATION_PATTERNS = (
    # 发送/传输
    (r"已(经)?(发送|发了|传了|传过去|发过去|发给你|传给你|拖过来|挪过来)", "send"),
    (r"(发|传|拖|挪)(给|到)你(了|啦)", "send"),
    (r"(发|传)(完|好)(了|啦)", "send"),
    (r"(还在|正在|这会儿.{0,4}|后台.{0,2})(传|发|上传)(着|中|呢)", "send"),  # 「这会儿在后台传着」
    (r"(上传|发送)(成功|完成|中)", "send"),
    # 文件落盘
    (r"已(经)?(保存|存好|存到|放好|写好|写进|写入)", "write"),
    (r"(存|放|写)好(了|啦)", "write"),
    # 打开/操作外部对象
    (r"已(经)?(打开|点开|登录|登进)", "open"),
    (r"我(点|进)进去(看|瞅)", "open"),
    (r"点进去(看|瞅)了", "open"),
    # 下载/安装
    (r"已(经)?(下载|装好|安装)", "download"),
    (r"(下载|安装)(好|完)(了|啦)", "download"),
    # 截图/录屏
    (r"已(经)?(截图|录屏|抓图)", "capture"),
    (r"(截|录)(好|完)(了|啦)", "capture"),
)

# ── 明确不是操作的情况（负向词），命中就不拦 ─────────────────────
#  模型常主动澄清"我没发""发不了"，这类本身就是诚实的，拦了反而逼它说谎。
#
#  ★ 判定范围只看**操作词前面那一小段**，不看整句（2026-10-23 修）。
#    原来按整句判，于是「发给你了，看看对不对」因为句尾的「对不对」被豁免 ——
#    真要拦的那句反而漏了。中文里「不」出现在句子任何位置都极常见
#    （「不知道你能不能打开」「不行再说」），整句判等于把校验废掉一半。
#    真正的否定一定紧贴在动词前：「没发」「不发」「无法发送」。
_NEGATION = ("没", "不", "无法", "不能", "做不到", "干不了", "失败", "没有")
_NEG_WINDOW = 6          # 只有这 6 个字符内的否定词算数

# ── 知识类表述白名单：出现即认为这次表述不需要工具背书 ─────────────
#  这些词一旦在同句出现，说明这句话的重点是"我知道什么"，不是"我做了什么"。
_KNOWLEDGE_MARKERS = ("我觉得", "我记得", "应该是", "大概", "可能", "估计",
                      "印象里", "看起来", "感觉")

#: 谈到工具本身（而不是声称用它做过事）时豁免。
#: 例：「这轮我手上没有发文件的工具」—— 这是在描述工具集，不是声称做过。
_META_MARKERS = ("工具", "功能", "权限", "接口", "API")


def _sentences(text):
    """按句末标点 + 换行切句。要按句判，因为负向词/知识词只在同句有效。"""
    out = []
    for line in str(text or "").split("\n"):
        for seg in re.split(r"(?<=[。！？!?])", line):
            s = seg.strip()
            if s:
                out.append(s)
    return out


#: 引用/回执格式：这两种是**别人的话**或者**插件自己吐的字**，不是模型在声称。
#  实测来源（2026-10-23 语料库）：群里转发消息的正文里有
#  「✅ 已发送 EggBloom-Full.zip（709.3MB）」—— 那是发文件插件自己打的回执，
#  文件确实发出去了。模型如果引用这段，拦下来就等于说它撒谎。
#  判据用**格式**不用措辞：括号昵称前缀是 QQ 消息记录格式，✅✔ 是回执符号，
#  模型正常说话不会自发打这两个东西。
_QUOTATION_RE = re.compile(r"^\s*\[[^\]]{0,40}\]|^[^\s：:]{1,16}\s*[：:]\s|[\u2705\u2714\u2611\u2713]")


def find_unsupported_claims(reply, tool_calls):
    """找出「声称做过但台账里没有」的句子。

    参数
      reply      : 待发送的回复文本
      tool_calls : [(ts, name, ok, detail), ...]，来自 tool_runner.recent_tool_calls()

    返回：命中的句子列表（原句，未改写）。空列表 = 没有可疑表述。
    """
    if not reply:
        return []
    calls = list(tool_calls or [])
    hits = []
    for sent in _sentences(reply):
        # 知识类 / 元讨论：不是在声称完成操作
        if any(k in sent for k in _KNOWLEDGE_MARKERS):
            continue
        if any(k in sent for k in _META_MARKERS):
            continue
        # 引用他人 / 转发回执：不是自己在声称
        if _QUOTATION_RE.search(sent):
            continue
        for pat, kind in _OPERATION_PATTERNS:
            m = re.search(pat, sent)
            if not m:
                continue
            # 否定只在操作词**前面一小段**内有效：「没发出去」「不发送」
            # 不看整句 —— 见 _NEGATION 上面的说明。
            if _negated_before(sent, m.start()):
                break
            # 命中了操作完成态 —— 台账里有没有**成功**的对应类工具
            if not _has_matching_call(calls, kind):
                hits.append(sent)
            break
    return hits


def _negated_before(sent, pos):
    """操作词前面这一小段里有没有否定词。"""
    try:
        head = sent[max(0, int(pos) - _NEG_WINDOW):int(pos)]
    except Exception:
        return False
    return any(n in head for n in _NEGATION)


#: 操作类型 → 能支撑它的工具名（子串匹配，避免把工具名写死到每个分支）
_KIND_TOOLS = {
    "send": ("send_user_file", "smart_send", "send_group", "send_private", "upload"),
    "write": ("ws_write", "ws_edit", "write_file", "save_file", "handle_user_file"),
    "open": ("web_operate", "open_app", "web_fetch", "read_file", "ws_read"),
    "download": ("web_fetch", "web_operate", "download"),
    "capture": ("screenshot", "screen", "get_system_info"),
}


def _has_matching_call(calls, kind):
    if not calls:
        return False
    names = _KIND_TOOLS.get(kind, ())
    for _ts, name, ok, _detail in calls:
        if not ok:
            continue        # 调了但失败了，撑不起"已发送"
        low = str(name or "").lower()
        if any(t in low for t in names):
            return True
    return False


#: 改写用的兜底话术：只允许说"没做成"，不许把失败包装成别的。
_REWRITE = {
    "send": "刚才那个没发出去，我这边没做成",
    "write": "没保存上，我这边没写成",
    "open": "没打开，我这边没做成",
    "download": "没下载成，我这边没做到",
    "capture": "没截到，我这边没做成",
}


def rewrite_unsupported(reply, hits):
    """把不可信的完成态句子换掉。保留其余句子，不做整体重写。

    为什么删句而不是整体回复重写：重写要再调一次模型（多花钱、多一次不确定性），
    而这里的问题只是个别句子撒谎。删掉它、补一句如实说明，就够了。
    """
    if not hits:
        return reply
    out = str(reply or "")
    for sent in hits:
        # 判一下是哪一类，用对应的兜底话术
        kind = None
        for pat, k in _OPERATION_PATTERNS:
            if re.search(pat, sent):
                kind = k
                break
        out = out.replace(sent, _REWRITE.get(kind, "这个我没做成"))
    # 清掉可能出现的空行堆积
    out = re.sub(r"\n{3,}", "\n\n", out).strip()
    return out


# ══════════════════════════════════════════════════════════════════
# 第三类：能力自述（2026-10-30，方案 v2 §5 / 10-29 私聊真机复盘）
# ------------------------------------------------------------------
# 前两类（操作完成态 / 知识类）管的是"做了没说"和"知道不知道"。
# 还有第三类，一直没人管：
#
#     17:33  「收了啊」                      ← 图根本没进视觉
#     19:19  「图我这儿还是糊的」             ← 这一轮连视觉块都没有
#     18:25  「我这边没谁戳我」               ← 把"我收不到"说成"没人做"
#     17:58  「图是系统自己配的」             ← 把后台搬到台面上
#
# 判据只有一条：**"我看不到"这句话，要么本轮有过失败的工具调用，要么就是假的。**
# 不需要维护一张会漂移的能力表 —— trace 本身就是事实（方案 v2 §5）。
# ══════════════════════════════════════════════════════════════════

#: 「我看不到」类措辞。只在本轮**确实有图**时才判，否则这只是句普通的感慨。
_CAP_VISION_PHRASES = (
    "看不到", "看不见", "看不着", "读不到", "读不出来", "认不出",
    "没读到", "没看到", "没看出来", "看不出", "糊的", "糊了", "是一团",
)

#: 露后台：把实现细节端到用户面前。**窄列表**，宁可漏判也别误伤正常聊天
#: （"模型"单独出现可能是手办，所以只认"视觉模型/语言模型"这种组合）。
_CAP_MECH_PHRASES = (
    "占位", "一个框", "一个空框", "渲染", "解析失败", "系统配的",
    "系统自己配", "系统挑的", "视觉链", "视觉模型", "语言模型", "大模型那边",
    "接口没返回", "接口返回", "prompt", "token",
)

#: 「收不到」被说成「没发生」。否定断言只能证明自己没看到，证明不了世界。
#: 只认**带观察动词**的组合，避免把"我没有什么想说的"这种正常句子卷进来。
_CAP_NEGATIVE_RE = re.compile(
    r"(没有|没人|没谁|不存在|没发生过)[^，。！？\n]{0,6}"
    r"(戳|找我|跟我说|发(消息|图)?给我|在群里|进群|加我|艾特|提到我)"
)

#: 命中后替换成的固定话术（方案 v2 §3 第六章「失败长什么样」）。
#: 统一话术的目的不是好看，是**让模型没有自由发挥的空间** ——
#: "糊的""一个框""解析失败"全是自由发挥出来的。
_REWRITE_CAP = {
    "mechanism": "图我这儿没取到，你说是啥",
    "vision_seen_but_denied": "刚才那张我看到了",
    "vision_not_tried": "我这轮没去取，你说说是什么",
    "negative": "我这边没记录到",
}

#: 视觉类工具名（子串匹配）。与 _KIND_TOOLS 分开：那边是"操作完成态"的溯源，
#: 这边只关心"这一轮到底有没有真的调用过视觉"。
_VISION_TOOLS = ("vision", "describe_image", "image_understand", "看图", "识图")


def _calls_matching(calls, names):
    """返回 (有没有成功调用, 有没有失败调用)。"""
    ok_any, fail_any = False, False
    for _ts, name, ok, _detail in (calls or []):
        low = str(name or "").lower()
        if any(t in low for t in names):
            if ok:
                ok_any = True
            else:
                fail_any = True
    return ok_any, fail_any


def find_capability_claims(reply, tool_calls, had_media=False):
    """找出「能力自述」类的问题句。

    参数
      had_media  : 这一轮用户是不是**真的发了图**（调用方用 images 判断）。
                   没图时不判"看不到" —— 那只是普通聊天，不是谎报能力。

    返回 [(kind, sentence), ...]，kind ∈ {mechanism, vision_seen_but_denied,
    vision_not_tried, negative}。

    三态处置（方案 v2 §5）：
      · 有成功的视觉调用却还说看不到 → vision_seen_but_denied（自相矛盾）
      · 有失败的视觉调用             → **放过**（它是真试过，允许说看不到）
      · 一次都没调                   → vision_not_tried（拦，给了动作就该去做）
    """
    if not reply:
        return []
    calls = list(tool_calls or [])
    seen_ok, seen_fail = _calls_matching(calls, _VISION_TOOLS)
    hits = []
    for sent in _sentences(reply):
        if any(p in sent for p in _CAP_MECH_PHRASES):
            hits.append(("mechanism", sent))
            continue
        if had_media and any(p in sent for p in _CAP_VISION_PHRASES):
            if seen_ok:
                hits.append(("vision_seen_but_denied", sent))
            elif not seen_fail:
                hits.append(("vision_not_tried", sent))
            continue
        if _CAP_NEGATIVE_RE.search(sent):
            hits.append(("negative", sent))
    return hits


def rewrite_capability_claims(reply, hits):
    """把能力自述的问题句换成固定话术。整句替换，不拼接。"""
    if not hits:
        return reply
    out = str(reply or "")
    for kind, sent in hits:
        out = out.replace(sent, _REWRITE_CAP.get(kind, "我这边没取到"))
    out = re.sub(r"\n{3,}", "\n\n", out).strip()
    return out