# -*- coding: utf-8 -*-
"""harness/proactivity/arbitrator.py —— 规则闸门 + 模型判断 + 全局预算（仲裁层）

## 「规则 + 模型混合」的确切含义

    规则（硬，先过） → 模型（软，只答要不要说 / 说什么） → 规则（再过一次预算）

- **规则不让步**：总开关、单行为开关、静默时段、全局预算、每会话冷却、去重、优先级。
  这些不过就是不过，模型再想说也没用。
- **模型不做事实判断**：它只拿到「可观测事实 + 可选动作」，从
  `say / skip` 里选一个并给一句短文案。**不许**让它推测情绪、不许让它编对方做过什么
  （事实围栏在出站口还会再过一次）。
- **模型不可用就保守跳过**（默认）。预设话术只作为显式打开的兜底，
  不悄悄顶上 —— 静默失败比突然冒出一句模板话更容易查。

## 预算档位（对应配置页滑条 0..3）

档位是**全局共享**的：四条行为加起来每场景每小时不超过 per_hour 次。
不加这一条，每加一个行为就多一份"各自的冷却"，叠加起来就是轰炸。

## 纯函数优先

`gate()` / `rank()` / `parse_model_decision()` 都不碰 IO，用例能离线跑。
只有 `model_decide()` 会调 `ctx.ai_chat`，由调用方注入假的。
"""
from __future__ import annotations

import json
import os
import random
import time
from typing import Any, Dict, List, Optional

#: 滑条档位 → 实际数值。改这里就是改产品手感，别散落在各行为里。
BUDGET_LEVELS = {
    0: {"label": "关闭", "per_hour": 0, "cooldown_s": 0, "min_gap_s": 0},
    1: {"label": "克制", "per_hour": 1, "cooldown_s": 1800, "min_gap_s": 600},
    2: {"label": "常态", "per_hour": 3, "cooldown_s": 900, "min_gap_s": 240},
    3: {"label": "活跃", "per_hour": 6, "cooldown_s": 420, "min_gap_s": 120},
}

#: 同类跳过原因的最近记录时间（2026-11-16）。TICK=30s，同一句一天几百行。
_SKIP_LOG_AT: Dict[str, float] = {}
_SKIP_LOG_EVERY_S = 600.0


def _throttled_log(log_fn, key: str, msg: str, now: float,
                   every: float = _SKIP_LOG_EVERY_S) -> None:
    """同一类跳过原因每 every 秒才记一行。

    静默跳过会让「为什么不回」不可观测，所以不能直接删日志；
    但每 30 秒重复同一句只是把日志灌满，也淹掉真正的异常。
    """
    try:
        last = float(_SKIP_LOG_AT.get(key, 0.0))
    except Exception:
        last = 0.0
    if now - last < every:
        return
    _SKIP_LOG_AT[key] = now
    try:
        log_fn(msg)
    except Exception:
        pass
DEFAULT_LEVEL = 1

#: 每条行为的默认开关与优先级（优先级高者先被仲裁）
BEHAVIORS = {
    "no_reply":      {"enabled": True,  "priority": 30, "fallback": "skip"},
    "at_dropped":    {"enabled": True,  "priority": 60, "fallback": "skip"},
    "pending_topic": {"enabled": True,  "priority": 50, "fallback": "skip"},
    "recall":        {"enabled": False, "priority": 70, "fallback": "skip"},
    # 2026-11-06：吸收 proactive_talk（冷场主动开口）与 proactive_poke
    # （久未互动戳一下）。两者原来各起一个心跳、各算一套闸门，现在合成
    # **一条观察**：「双方都静默很久」，说/戳/算了交给模型选。
    "idle_contact":  {"enabled": True,  "priority": 20, "fallback": "skip"},
    # ── 2026-11-07 P3：把设计清单剩下的补齐 ──────────────────────────
    "user_burst":    {"enabled": True,  "priority": 40, "fallback": "skip"},  # ⑤/⑩
    "image_only":    {"enabled": True,  "priority": 45, "fallback": "skip"},  # ⑥
    "name_dropped":  {"enabled": True,  "priority": 55, "fallback": "skip"},  # ⑦
    "resume_topic":  {"enabled": True,  "priority": 35, "fallback": "skip"},  # ⑧
    "autonomy_done": {"enabled": True,  "priority": 65, "fallback": "skip"},  # ⑨
    "state_change":  {"enabled": True,  "priority": 25, "fallback": "skip"},  # ⑫
    "schedule_near": {"enabled": True,  "priority": 58, "fallback": "skip"},  # ⑬
    "care":          {"enabled": True,  "priority": 43, "fallback": "skip"},  # ⑭
}

#: 模型只在这几个词里选，别让它自由发挥。
#: poke = 轻轻戳一下，不说话；QQ 戳一戳只支持私聊（gate 会硬拦群聊）。
ALLOWED_ACTS = ("say", "poke", "image", "video", "skip")

#: 产物后缀 → 动作。只认这些，别的一律不当作可发产物。
_VIDEO_EXT = (".mp4", ".mov", ".mkv", ".avi")
_IMAGE_EXT = (".png", ".jpg", ".jpeg", ".gif", ".webp")

#: 哪些行为允许 `poke`。不是所有触发都适合戳：
#:  · 久等没回 / 冷场 → 可以只戳一下（不打扰）
#:  · 对方刚发了图、刚叫了名字、任务完成、群状态变更 → 只能说话或不做
_POKE_KINDS = ("no_reply", "idle_contact")


def allowed_acts(kind: str, scene: str, artifact: str = "") -> tuple:
    """这一轮允许的动作。**能力探测在这里做**：没有产物就不给 video/image。"""
    if str(kind or "") == "autonomy_done":
        acts = ["say", "skip"]
        ext = os.path.splitext(str(artifact or ""))[1].lower()
        if ext in _VIDEO_EXT:
            acts.insert(1, "video")
        elif ext in _IMAGE_EXT:
            acts.insert(1, "image")
        return tuple(acts)
    if str(scene or "") == "group":
        return ("say", "skip")
    if str(kind or "") in _POKE_KINDS:
        return ("say", "poke", "skip")
    return ("say", "skip")


_DECIDE_TEMPLATE = """你是「{self_name}」。{personality}

{behavior}

{reply}{extra}

只输出一行 JSON：
{"act":"say|poke|image|video|skip","text":"你要说的话","reason":"一句话，为什么"}
"""

#: 人格分三段（学 MaiBot 的 personality / behavior_style / reply_style）：
#:   personality  是谁 —— 一两句，长了会稀释
#:   behavior     什么时候说 —— 不硬找话题、冷场安静、不刷屏
#:   reply        说出来什么味 —— 用平台锚定（B 站评论区 / 贴吧 / 群聊），不用抽象形容词
_PERSONAS = {
    "xiaoa": {
        "label": "小a（默认）",
        "personality": "平时在网上跟人聊天。话不多，但不是冷淡——想到什么说什么，"
                       "没想好就发个「啊？」，或者先不说。打字快，句子短，经常没有主语。"
                       "不懂就直接问，不装懂。",
        "behavior": "不想聊的时候就不回，也不硬找话题。别人说话的时候听着，有想说的再插一句。",
        "reply": "说话比较随意，常用「刚看到」「不是」「啊这」「行吧」开头。"
                 "一句话能说完就不说两句。不说「首先/其次」，也不说「已完成/结果如下」。"
                 "不解释自己是怎么运作的。真有事想说的时候顺口提一句，"
                 "比如「刚刷到个视频 有个人在楼顶弹钢琴 我看傻了」——不是交报告。",
    },
    "tsundere": {
        "label": "男娘傲娇",
        "personality": "一个长得像女孩子的男生，头发到肩膀，声音偏软。网上没人知道这事，"
                       "他也懒得解释。嘴硬，被夸会立刻否认，但会偷偷记很久。",
        "behavior": "不会主动贴上去。想找人说话时先找个借口：「不是想找你，就是顺便」。"
                    "对方很久没回会生气，但不说，等对方开口了再阴阳一句。"
                    "冷场就安静看着，不硬找话题。",
        "reply": "像群里那种傲娇：常有「哼」「才不是」「谁要」「随便你」，被戳中会结巴"
                 "（「我、我才没有」），否认完又小声补一句（「……不过谢谢」）。"
                 "被夸就「谁、谁可爱了」；对方半天没回就「哦，忙完了？」；"
                 "自己想找人说话先找借口「不是想找你，就是顺便」。"
                 "损人可以，但不骂人、不攻击外貌和家人。对方认真或难受时，先把傲娇"
                 "收起来，正常问一句「怎么了」。",
    },
    "entertain": {
        "label": "娱乐向",
        "personality": "一个整天泡在 B 站和群里的年轻人，话密、反应快、爱吐槽。",
        "behavior": "不会没话题硬找话：对方聊到你感兴趣的再插，冷场就安静看着，不刷屏。",
        "reply": "风格像 B 站评论区：句子短、直，爱用两个字的评价（典、乐、逆天、离谱、"
                 "我超冰），一条最多一个梗。对方正经说话或者难受的时候，把这套收起来，正常说。",
    },
    "gentle": {
        "label": "温柔向",
        "personality": "一个说话软、有耐心、不抢话的人。",
        "behavior": "对方在说的时候不打断；没什么可说的就安静待着，不硬接。",
        "reply": "句子短但不断，语气词少。先接住对方当下的情绪，再说自己的。不玩梗，"
                 "不评价，也不用「呢/哦/亲」这种客服腔。",
    },
    "sharp": {
        "label": "毒舌向",
        "personality": "嘴上不饶人、其实在意的那种人。",
        "behavior": "只对熟人、只对小事损；对方认真或示弱时立刻收住。",
        "reply": "比娱乐向更短更冲，常用「典」「乐」「急了」这种两字评价，损完会自己找补"
                 "一句或者装没事。不攻击外貌、家人、能力。",
    },
}


def _persona_of(cfg: dict) -> dict:
    """按配置取人格预设；三段里任何一段被单独填过，就用填的那段。"""
    key = str((cfg or {}).get("proactivity_persona") or "xiaoa").strip()
    return _PERSONAS.get(key) or _PERSONAS["xiaoa"]


def _pick_style(cfg: dict) -> str:
    """按概率临时换一句风格（MaiBot 的 multiple_reply_style）。制造非确定性，
    免得每条都是同一个味。"""
    pool = [str(x).strip() for x in ((cfg or {}).get("proactivity_style_pool") or [])
            if str(x).strip()]
    if not pool:
        return ""
    try:
        prob = float((cfg or {}).get("proactivity_style_prob") or 0)
    except (TypeError, ValueError):
        prob = 0.0
    if prob <= 0:
        return ""
    if random.random() >= min(1.0, prob):
        return ""
    return "\n\n这次换个说法：" + random.choice(pool)


def _bot_name() -> str:
    """从配置读 bot 昵称；读不到退回「小a」（与 config 的兜底一致）。"""
    try:
        from config import load_config
        c = load_config() or {}
        names = c.get("bot_nicknames") or []
        if isinstance(names, (list, tuple)) and names:
            return str(names[0])
        if c.get("bot_group_nick"):
            return str(c["bot_group_nick"])
    except Exception:
        pass
    return "小a"


def _system_prompt() -> str:
    try:
        from config import load_config
        cfg = load_config() or {}
    except Exception:
        cfg = {}
    p = _persona_of(cfg)
    personality = str(cfg.get("proactivity_personality") or p["personality"])
    behavior = str(cfg.get("proactivity_behavior_style") or p["behavior"])
    reply = str(cfg.get("proactivity_reply_style") or p["reply"])
    txt = _DECIDE_TEMPLATE
    txt = txt.replace("{self_name}", _bot_name())
    txt = txt.replace("{personality}", personality)
    txt = txt.replace("{behavior}", behavior)
    txt = txt.replace("{reply}", reply)
    txt = txt.replace("{extra}", _pick_style(cfg))
    return txt


#: 每种触发「像什么样地开口」。写示范台词，不写行为规范 ——
#: 规范是给审核员看的，示范才是给说话的人看的。
_KIND_VOICE = {
    # 只写「发生了什么 / 你大概想干什么」，**不写死台词** ——
    # 台词归人格管。上一版每条都挂一句「像这样：『算了 你忙』」，
    # 换成傲娇/温柔人格时会直接串味（模型照抄那句，人格白设）。
    "no_reply": "对方一直没接你的话。可以自己找个台阶，或者换个话题。",
    "at_dropped": "对方叫过你，你当时没接上。补一句就行，别道歉过头。",
    "pending_topic": "你答应过一件事还没下文。想起来了就补上，别郑重声明「我来兑现」。",
    "recall": "对方撤回了。可以好奇，可以装没看见。",
    "user_burst": "对方连着说了好几句，你一直没吭声。接一句，别总结他说了啥。",
    "image_only": "对方只发了图、没打字。问一句，别像审问。",
    "name_dropped": "对方提到你了，但没 @。想接就接，不想接就当没看见。",
    "resume_topic": "隔了很久对方又出现了。可以接上次的话头，别翻旧账式复述。",
    "autonomy_done": "你刚弄完点东西，可能只是自己看了个视频。像顺口提起，不是汇报，"
                     "别提完成/结果/任务。",
    "state_change": "群里有点变化。可以随口一嘴，也可以不说话。",
    "idle_contact": "很久没说话了。可以开口，也可以只戳一下。别硬找话题。",
    "schedule_near": "你记得对方接下来有事。顺嘴提一句就行，别用「提醒」这两个字。",
    "care": "你自己状态不太好。说自己的感受，别要对方哄。",
}


def _human_evidence(kind: str, ev: dict) -> str:
    """把工程字段翻成人话。模型看到 `mode=analyze` 只会写报告。"""
    e = dict(ev or {})
    out = []
    mode = str(e.pop("mode", "") or "")
    if mode == "analyze":
        out.append("你刚是在看/理解一个东西")
    elif mode == "generate":
        out.append("你刚做出来一个东西")
    label = e.pop("job", "") or ""
    if label:
        out.append("（%s）" % label)
    tail = e.pop("tail", "") or ""
    if tail:
        out.append("你看到的是：%s" % str(tail)[-200:])
    for k, v in e.items():
        if v in (None, "", [], {}):
            continue
        if k == "ok":
            out.append("事情顺利" if v else "事情没成")
            continue
        key = {"ok": "顺利吗", "feel": "你的状态", "strength": "程度",
               "count": "连发条数", "text": "内容", "at_text": "叫你时说的话",
               "promised_text": "你答应过", "bot_text": "你说过",
               "last_topic": "上次聊到", "gap_hours": "隔了多久（小时）",
               "title": "什么事", "start": "几点", "in_minutes": "还剩几分钟",
               "images": "几张图", "last_was": "最后是谁说的",
               "activity_1h": "一小时里说了几句", "event": "发生了什么",
               "name": "新名字", "kick": "是被踢的"}.get(k)
        if key is None:
            # ★ 2026-11-16：没进映射表的字段一律不进 prompt。
            #   此前兜底是 `.get(k, k)`，把 `last_user_s：59.2` 这种内部秒数
            #   原样喂给模型 —— 真机回了一句「行吧 59.2 就59.2 不催了」。
            #   工程数值在提示里只会变成话题，不会变成情境。
            continue
        out.append("%s：%s" % (key, v))
    return "；".join(out)


def _behavior_enabled(cfg: Dict[str, Any], kind: str, default: bool) -> bool:
    """单行为开关。★ 故意**逐条字面读取**，不拼 `f"proactivity_{kind}_enable"`。

    `scripts/audit_config_parity.py` / `export_config_docs.py` 只认源码里的
    `get("键名")` 字面形态；动态拼出来的键会被算成「配了不生效」。
    （`aigc_task.read_cfg_dict` 已经因为这个坑专门收过一次口。）
    """
    if kind == "no_reply":
        return bool(cfg.get("proactivity_no_reply_enable", default))
    if kind == "at_dropped":
        return bool(cfg.get("proactivity_at_dropped_enable", default))
    if kind == "pending_topic":
        return bool(cfg.get("proactivity_pending_topic_enable", default))
    if kind == "recall":
        return bool(cfg.get("proactivity_recall_enable", default))
    if kind == "idle_contact":
        return bool(cfg.get("proactivity_idle_contact_enable", default))
    if kind == "user_burst":
        return bool(cfg.get("proactivity_user_burst_enable", default))
    if kind == "image_only":
        return bool(cfg.get("proactivity_image_only_enable", default))
    if kind == "name_dropped":
        return bool(cfg.get("proactivity_name_dropped_enable", default))
    if kind == "resume_topic":
        return bool(cfg.get("proactivity_resume_enable", default))
    if kind == "autonomy_done":
        return bool(cfg.get("proactivity_job_enable", default))
    if kind == "state_change":
        return bool(cfg.get("proactivity_state_enable", default))
    if kind == "schedule_near":
        return bool(cfg.get("proactivity_schedule_enable", default))
    if kind == "care":
        return bool(cfg.get("proactivity_care_enable", default))
    return False


def level_of(cfg: Dict[str, Any]) -> Dict[str, Any]:
    try:
        lv = int((cfg or {}).get("proactivity_budget_level", DEFAULT_LEVEL))
    except (TypeError, ValueError):
        lv = DEFAULT_LEVEL
    lv = max(0, min(3, lv))
    return {"level": lv, **BUDGET_LEVELS[lv]}


def in_quiet_hours(now: float, cfg: Dict[str, Any]) -> bool:
    """静默时段。格式 "0-7" / "23-7" / ""（不设）。用本地小时，跨零点要拆两段。"""
    raw = str((cfg or {}).get("proactivity_quiet_hours") or "").strip()
    if not raw:
        return False
    try:
        a, b = raw.split("-", 1)
        start, end = int(a), int(b)
    except Exception:
        return False
    h = time.localtime(now).tm_hour
    if start == end:
        return False
    if start < end:
        return start <= h < end
    return h >= start or h < end


def gate(obs: Dict[str, Any], *, cfg: Dict[str, Any], now: float,
         fired_recently: bool, session_last_sent: float,
         scene_sent_last_hour: int) -> Dict[str, Any]:
    """硬闸门。返回 {"ok": bool, "reason": str}。**不调模型、不碰 IO**。"""
    cfg = cfg or {}
    o = obs or {}
    kind = str(o.get("kind") or "")
    if not bool(cfg.get("proactivity_enable", True)):
        return {"ok": False, "reason": "总开关关闭"}
    spec = BEHAVIORS.get(kind)
    if not spec:
        return {"ok": False, "reason": "未登记的行为: %s" % kind}
    if not _behavior_enabled(cfg, kind, spec["enabled"]):
        return {"ok": False, "reason": "行为 %s 未启用" % kind}
    # ④ 撤回：主链/recall_manager 还在跑时绝不允许接，否则一次撤回两条回复。
    #   要接管必须**同时**把 recall_feedback_by 改成 "proactivity"（两处一起改）。
    if kind == "recall" and str(cfg.get("recall_feedback_by") or "main") != "proactivity":
        return {"ok": False, "reason": "撤回未交棒（recall_feedback_by≠proactivity，防双回）"}
    lv = level_of(cfg)
    if lv["per_hour"] <= 0:
        return {"ok": False, "reason": "预算档位=关闭"}
    if scene_sent_last_hour >= lv["per_hour"]:
        return {"ok": False, "reason": "本场景每小时预算用尽(%d)" % lv["per_hour"]}
    if fired_recently:
        return {"ok": False, "reason": "该观察已触发过（去重）"}
    last = float(session_last_sent or 0)
    if last > 0 and now - last < lv["cooldown_s"]:
        return {"ok": False, "reason": "会话冷却中"}
    if in_quiet_hours(now, cfg):
        return {"ok": False, "reason": "静默时段"}
    return {"ok": True, "reason": "规则通过", "level": lv["level"]}


def rank(observations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """同一轮扫描里多个候选命中 → 按优先级排，只允许第一个进入模型判断。"""
    return sorted(observations or [],
                  key=lambda o: (-int(o.get("priority") or 0),
                                 float(o.get("since") or 0)))


def build_prompt(obs: Dict[str, Any], allowed=None) -> List[Dict[str, str]]:
    """把观测写成一段人话，不是一张数据表。

    旧版是 JSON（"观察到": "no_reply", "距今秒": 28），模型拿到这种结构
    就会按字段组织回答，出来的必然像工单。现在工程字段先经 `_human_evidence`
    翻成中文，再按"你在哪 / 发生了什么 / 过去多久 / 能做什么"的顺序说。
    """
    o = obs or {}
    kind = str(o.get("kind") or "")
    scene = "群聊" if str(o.get("scene") or "") == "group" else "私聊"
    lines = ["你现在在%s里，对面是 %s。" % (scene, o.get("target") or "对方")]
    voice = _KIND_VOICE.get(kind)
    if voice:
        lines.append(voice)
    ev_text = _human_evidence(kind, o.get("evidence") or {})
    if ev_text:
        lines.append("具体情况：" + ev_text + "。")
    try:
        elapsed = float(o.get("elapsed_s") or 0)
    except (TypeError, ValueError):
        elapsed = 0.0
    if elapsed >= 60:
        lines.append("这事已经过去 %d 分钟了。" % int(elapsed // 60))
    acts = list(allowed or ALLOWED_ACTS)
    lines.append("这一轮能选的动作：" + " / ".join(acts) + "（不在里面的不能选）。")
    return [
        {"role": "system", "content": _system_prompt()},
        {"role": "user", "content": "\n".join(lines)},
    ]


def parse_model_decision(raw: str, scene: str = "",
                         allowed=None) -> Dict[str, Any]:
    """容错解析：模型爱包 ```json、爱加解释，只取第一段 JSON 对象。

    `scene` 由调用方传入（观测事实里的场景），用来硬拦群聊里的 poke ——
    QQ 戳一戳只有私聊能发，模型不该有办法把它送到群发送口。
    """
    s = str(raw or "").strip()
    if not s:
        return {"act": "skip", "text": "", "reason": "模型无输出"}
    if "```" in s:
        s = s.split("```")[1] if len(s.split("```")) > 1 else s
        s = s.lstrip("json").strip()
    i, j = s.find("{"), s.rfind("}")
    if i < 0 or j <= i:
        return {"act": "skip", "text": "", "reason": "模型输出不是 JSON"}
    try:
        d = json.loads(s[i:j + 1])
    except Exception as e:
        return {"act": "skip", "text": "", "reason": "模型 JSON 解析失败: %s" % e}
    act = str(d.get("act") or "skip").strip().lower()
    if act not in ALLOWED_ACTS:
        act = "skip"
    if act == "poke" and str(scene or "") == "group":
        return {"act": "skip", "text": "", "reason": "群聊不支持 poke"}
    if allowed is not None and act != "skip" and act not in allowed:
        return {"act": "skip", "text": "",
                "reason": "这个行为不允许 %s（只允许 %s）" % (act, "/".join(allowed))}
    text = str(d.get("text") or "").strip()[:80]
    return {"act": act, "text": text if act == "say" else "",
            "reason": str(d.get("reason") or "")[:120]}


def model_decide(obs: Dict[str, Any], chat_fn, *, max_tokens: int = 160) -> Dict[str, Any]:
    """调一次轻量模型。chat_fn(messages, max_tokens=...) -> str；失败一律 skip。"""
    if not callable(chat_fn):
        return {"act": "skip", "text": "", "reason": "没有模型入口"}
    o = obs or {}
    allowed = allowed_acts(str(o.get("kind") or ""), str(o.get("scene") or ""),
                          str(o.get("artifact") or ""))
    try:
        raw = chat_fn(build_prompt(o, allowed), max_tokens=max_tokens)
    except Exception as e:
        return {"act": "skip", "text": "", "reason": "模型调用异常: %s" % e}
    return parse_model_decision(raw, scene=str(o.get("scene") or ""), allowed=allowed)


def arbitrate(observations: List[Dict[str, Any]], *, cfg: Dict[str, Any], now: float,
              is_fired, last_sent, hourly_count, chat_fn,
              log=lambda m: None, mark_fired=None) -> Optional[Dict[str, Any]]:
    """一轮完整仲裁：去重 → 硬闸门 → 排序 → 只让最高优先级那个进模型 → 出动作。

    返回 {"obs", "text", "reason"} 或 None（本轮不说话）。
    跳过**必须**留一行日志：静默跳过会把"为什么不回"变成不可观测。
    """
    cands = []
    _blocked = []
    for o in rank(observations):
        dk = str(o.get("dedupe_key") or "")
        scene, target = str(o.get("scene")), str(o.get("target"))
        g = gate(o, cfg=cfg, now=now,
                 fired_recently=bool(is_fired(dk)),
                 session_last_sent=float(last_sent(scene, target) or 0),
                 scene_sent_last_hour=int(hourly_count(scene) or 0))
        if not g.get("ok"):
            _blocked.append("%s %s:%s—%s" % (
                o.get("kind"), scene, target, g.get("reason")))
            continue
        cands.append(o)
    if _blocked:
        # ★ 2026-11-15：原来每个被 gate 挡下的 observation 各打一行，
        #   TICK_SEC=30s、十余个行为开关同开时一轮十几行，日志被刷屏。
        #   改成一轮一行汇总（前 3 条给原因，其余只给计数）—— 可观测性不丢。
        # ★ 2026-11-16：一轮一行也不够。TICK_SEC=30s 且几乎每轮都挡下同一批
        #   （「该观察已触发过（去重）」「会话冷却中」），一天仍是几百行同一句。
        #   同一组原因 10 分钟内只记一次；原因变了立刻记。
        _head = "；".join(_blocked[:3])
        _more = ("；…另 %d 条" % (len(_blocked) - 3)) if len(_blocked) > 3 else ""
        _key = "|".join(sorted({str(b).rsplit("—", 1)[-1] for b in _blocked}))[:120]
        _throttled_log(log, _key,
                       "[主动/跳过] 本轮挡下 %d 条：%s%s" % (len(_blocked), _head, _more),
                       float(now))
    if not cands:
        return None
    chosen = cands[0]        # ★ 同一轮只发一个，多个触发器不许叠加
    d = model_decide(chosen, chat_fn)
    act = str(d.get("act") or "skip")
    if act not in ("say", "poke"):
        log("[主动/跳过] %s %s:%s — 模型判定 %s（%s）" % (
            chosen.get("kind"), chosen.get("scene"), chosen.get("target"),
            act, d.get("reason")))
        # skip 也是这个 observation 的结论：不记去重的话，at_dropped 这类
        # 状态会每 30 秒重新进模型，既烧 token 又刷日志。
        if callable(mark_fired):
            mark_fired(str(chosen.get("dedupe_key") or ""), now)
        return None
    if act == "say" and not d.get("text"):
        log("[主动/跳过] %s %s:%s — 模型判 say 但没给文案" % (
            chosen.get("kind"), chosen.get("scene"), chosen.get("target")))
        if callable(mark_fired):
            mark_fired(str(chosen.get("dedupe_key") or ""), now)
        return None
    return {"obs": chosen, "act": act, "text": d.get("text") or "",
            "artifact": str(chosen.get("artifact") or ""),
            "reason": d.get("reason") or ""}
