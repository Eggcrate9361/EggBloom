# -*- coding: utf-8 -*-
"""proactive_talk —— 主动消息引擎（真插件 ABI v1）

无人触发时，按心跳 + 状态主动给目标私聊开口，补齐「被动响应」之外的另一半：
  - 心跳定时评估：用户沉默冷却 / 两次开口最小间隔 / 静默时段 / 概率闸门
  - 生成时注入：当前时段、沉默时长、最近对话摘录、上次主动说过的话、惦记的事
  - 发送时按行切气泡，逐条 ctx.send_text + 打字延时，模拟真人发消息节奏

人设中立（非恋爱向），可通过配置 persona_extra 追加定制；
nickname 配置怎么称呼对方。

遵守 ABI 三原则：不 import 主程序，只经 ctx 拿能力，声明式 manifest + hooks。
发消息走 ctx.send_text → 主程序 outbound 队列（1.5.10 F1 起不再直连 OneBot）。
"""
from __future__ import annotations

import json
import os
import random
import re
import threading
import time
from datetime import datetime

from plugins.abi import PluginContext

PLUGIN_SPEC = 1
PLUGIN_MANIFEST = {
    "name": "proactive_talk",
    "version": "1.0.0",
    "api_version": 1,
    "author": "EggBloom",
    "description": "主动消息引擎：私聊无人触发时按状态主动开口（拟人化，人设可配）",
    "dependencies": [],
    "priority": 90,
    # 2026-10-21：用户明确要求启用。原本默认 False 是"不配就别乱开口"的保守设置，
    # 现已配好 data/config.json（心跳 15 分钟、最小间隔 3 小时、
    # 用户发言后 30 分钟内不打扰、1:00–7:00 静默、单次最多 4 个气泡）。
    # 想关就改这里回 False，或在 WebUI 插件页关掉。
    "default_enabled": True,
}

_DIR = os.path.dirname(os.path.abspath(__file__))
_CONFIG_PATH = os.path.join(_DIR, "data", "config.json")

DEFAULT_CONFIG = {
    "target_qq": "",            # 目标私聊 QQ；空 = 主配置 target_qqs 第一个
    "nickname": "",             # 怎么称呼对方（可空）
    "heartbeat_sec": 900,       # 心跳间隔（秒），每次心跳评估一次要不要开口
    "min_interval_min": 180,    # 两次主动开口的最小间隔（分钟）
    "active_cooldown_min": 30,  # 用户最近发言后多久内不打扰（分钟）
    "speak_chance": 0.35,       # 每次心跳通过闸门的概率 0~1
    "quiet_hours": "",          # 静默时段，如 "2:00-7:00"；空 = 不静默（凌晨也会说话）
    # ★ 2026-10-24 作息乘子总开关（harness.bio_rhythm，一般精度）。
    #   quiet_hours 是硬闸，但它默认为空 = 凌晨也照样说话。这个开关给一条
    #   **软闸**：按时段给 speak_chance 乘子（凌晨 ×0.15、晚上 ×1.2）。
    #   想恢复"全天一个概率"就设 False。硬闸仍然优先，不受这个开关影响。
    "bio_rhythm": True,
    "max_bubbles": 4,           # 一次最多发几个气泡
    "persona_extra": "",        # 追加人设（接在默认风格约束后面）
    "recent_window": 6,         # 记录最近几条用户消息作为上下文
    # ── 联动（经本机 HTTP，不 import 主程序）──
    "webui_url": "http://127.0.0.1:8080",  # EggBloom WebUI 地址（联动通道）
    "use_memory": True,         # 注入 super_memory 私聊历史（/api/memory/detail）
    "memory_rounds": 3,         # 注入最近几轮私聊
    "use_personality": True,    # 注入 personality_engine 的人设（与群聊里的它一致）
    "use_mood": True,           # 注入 mood_engine 当前情绪，影响语气
}

_ctx: "PluginContext | None" = None
_thread: "threading.Thread | None" = None
_stop = threading.Event()
_send_lock = threading.Lock()

# ── 配置读写（插件自己的 data/config.json）────────────────


def _load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    try:
        if os.path.isfile(_CONFIG_PATH):
            with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg.update(json.load(f))
    except Exception:
        pass
    # ★ 2026-10-24：quiet_hours 现在有两个来源 —— 主配置 bot_config.json 与
    #   本插件 data/config.json。主配置那份是给群聊回复链用的（凌晨别被叫醒），
    #   这里必须跟它对齐，否则会出现"私聊静默 1:00-7:00、群聊静默 2:00-8:00"
    #   这种同一件事两套时段。
    #   规则：插件自己显式配了非空值 → 用插件的（尊重单插件覆盖）；
    #         插件没配（空串/缺键）→ 继承主配置。默认两边都是空，行为不变。
    if not str(cfg.get("quiet_hours") or "").strip():
        try:
            from config import load_config
            cfg["quiet_hours"] = str(load_config().get("quiet_hours") or "")
        except Exception:
            pass
    return cfg


def _save_config(cfg: dict) -> bool:
    try:
        os.makedirs(os.path.dirname(_CONFIG_PATH), exist_ok=True)
        merged = dict(DEFAULT_CONFIG)
        merged.update(cfg or {})
        with open(_CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(merged, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


# ── 小工具 ────────────────────────────────────────────────


def _log(msg: str, level: str = "info"):
    if _ctx:
        _ctx.log(msg, level)


def _period(now: datetime) -> str:
    """时段名。★ 2026-10-24：改为直接问 time_ctx，不再自带一份边界表。

    原先这里写了一套 `h<5 凌晨 / h<9 早上 / h<12 上午 …` 的边界，与
    `harness.time_ctx._PERIODS` 是**两套判据**。实测 24 小时里有 7 个小时
    两边名字不同（05~08 清晨 vs 早上、11 中午 vs 上午、17~18 傍晚 vs 下午/晚上），
    结果是同一时刻群聊 prompt 说「清晨」、私聊开口 prompt 说「早上」，
    而 bio_rhythm 又按「清晨」算乘子 —— 三处各说各的。

    现在只认 time_ctx 一份。取不到就退回空串，不猜。
    """
    try:
        from harness.time_ctx import period_of
        return period_of(now.timestamp())[0]
    except Exception:
        return ""


def _in_quiet_hours(spec: str, now: datetime) -> bool:
    """quiet_hours 形如 '23:00-7:00'，支持跨天。空串 = 不静默。

    ★ 2026-10-24：解析逻辑移到 `harness.time_ctx.in_quiet_hours`。
      群聊回复链现在也要用同一个判断（凌晨别被叫起来回话），
      两处各写一份解析就是又一组"两套判据"。这里保留函数名只为
      兼容既有测试与调用点，实现只剩转发。
    """
    try:
        from harness.time_ctx import in_quiet_hours
        return in_quiet_hours(spec, now.timestamp())
    except Exception:
        return False


def _resolve_target(cfg: dict) -> str:
    qq = str(cfg.get("target_qq") or "").strip()
    if qq:
        return qq
    try:
        tqs = _ctx.get_config("target_qqs", []) if _ctx else []
        if isinstance(tqs, list) and tqs:
            return str(tqs[0])
    except Exception:
        pass
    return ""


def _silent_desc(last_user_ts: float) -> str:
    if not last_user_ts:
        return "你们有一阵子没说话了"
    gap = max(0, time.time() - last_user_ts)
    hours = int(gap // 3600)
    mins = int((gap % 3600) // 60)
    if hours >= 24:
        return f"他上次说话是 {hours // 24} 天前"
    if hours > 0:
        return f"他上次说话是 {hours} 小时 {mins} 分钟前"
    return f"他上次说话是 {mins} 分钟前"


def _clean_bubbles(text, max_bubbles: int) -> list:
    out = []
    for raw in str(text or "").splitlines():
        s = raw.strip().strip('"“”').strip()
        s = re.sub(r"^[-*•\d]+[.、)）]?\s*", "", s)       # 去列表/编号前缀
        s = re.sub(r"\[表情:[^\]]+\]", "", s).strip()      # 去表情包占位符
        s = s.rstrip("。.")
        if 2 <= len(s) <= 60:
            out.append(s)
    return out[: max(1, int(max_bubbles or 4))]


# ── 联动：经 WebUI 通用通道 / 记忆 API 读 EggBloom 现有能力 ──
# 原则：全部走本机 HTTP（ctx.http_get/post），不 import 主程序；
# 任何一环失败都静默降级为 standalone，不影响主动开口主流程。

_personality_cache = {"ts": 0.0, "text": ""}
_MOOD_LABELS = {
    "very_happy": "特别开心", "happy": "开心", "calm": "平静", "平静": "平静",
    "sad": "有点丧", "down": "低落", "angry": "有点烦",
}


def _webui_base(cfg: dict) -> str:
    return str(cfg.get("webui_url") or "http://127.0.0.1:8080").rstrip("/")


def _plugin_action(cfg: dict, plugin_id: str, action: str, params: dict = None) -> dict:
    """调 WebUI 的 /api/plugins/action 通用通道，失败返回 {}。"""
    try:
        resp = _ctx.http_post(
            f"{_webui_base(cfg)}/api/plugins/action",
            {"plugin_id": plugin_id, "action": action, "params": params or {}},
            timeout=5,
        )
        return resp if isinstance(resp, dict) else {}
    except Exception:
        return {}


def _fetch_personality(cfg: dict) -> str:
    """personality_engine 的人设原文（10 分钟缓存）。空 = 不可用。"""
    if not cfg.get("use_personality"):
        return ""
    now = time.time()
    if _personality_cache["text"] and now - _personality_cache["ts"] < 600:
        return _personality_cache["text"]
    r = _plugin_action(cfg, "personality_engine", "get_personality")
    raw = str(r.get("raw") or "").strip()
    if raw:
        _personality_cache.update({"ts": now, "text": raw[:1200]})
    return _personality_cache["text"]


def _fetch_mood(cfg: dict) -> str:
    """mood_engine 当前情绪：取 |score| 最大的一条，翻译成中文。空 = 平静/不可用。"""
    if not cfg.get("use_mood"):
        return ""
    moods = _plugin_action(cfg, "mood_engine", "get_moods")
    if not isinstance(moods, dict) or not moods:
        return ""
    best, best_abs = None, 0.0
    for scope, m in moods.items():
        try:
            s = abs(float((m or {}).get("score") or 0))
        except Exception:
            continue
        if s > best_abs:
            best, best_abs = (m or {}), s
    if not best:
        return ""
    label = str(best.get("label") or "")
    return _MOOD_LABELS.get(label, "") if best_abs >= 1 else ""


def _fetch_memory_rounds(cfg: dict, qq: str) -> list:
    """super_memory 私聊历史（WebUI /api/memory/detail），取最近 N 轮。"""
    if not cfg.get("use_memory"):
        return []
    try:
        limit = max(1, int(cfg.get("memory_rounds") or 3))
        resp = _ctx.http_get(f"{_webui_base(cfg)}/api/memory/detail?qq={qq}", timeout=5)
        rounds = (resp or {}).get("rounds") or []
        return rounds[-limit:]
    except Exception:
        return []


# ── OneBot 发送 ───────────────────────────────────────────


def _send_private(qq: str, text: str) -> bool:
    """走宿主统一出口（1.5.10 F1）：不再自己拼 OneBot HTTP。

    直连 OneBot 发送接口会绕过 outbound 队列的串行/限频/去重/日志，
    而且加载期静态扫描会直接拒绝这类插件。
    """
    try:
        return bool(_ctx.send_text(text, target=str(qq)))
    except Exception as e:
        _log(f"发送失败 qq={qq}: {e}", "warn")
        return False


def _send_bubbles(qq: str, bubbles: list) -> int:
    sent = 0
    for i, b in enumerate(bubbles):
        if _stop.is_set():
            break
        if _send_private(qq, b):
            sent += 1
        if i < len(bubbles) - 1:
            _stop.wait(min(0.8 + 0.15 * len(b), 4.0))   # 打字延时，可被停止打断
    return sent


# ── 生成 ──────────────────────────────────────────────────


def _build_messages(cfg: dict, state: dict, target: str) -> list:
    now = datetime.now()
    nickname = str(cfg.get("nickname") or "").strip()
    extra = str(cfg.get("persona_extra") or "").strip()
    max_bubbles = int(cfg.get("max_bubbles") or 4)

    # 联动：人设 / 情绪 / 私聊记忆（任一失败自动降级，不影响主流程）
    persona_raw = _fetch_personality(cfg)
    mood = _fetch_mood(cfg)
    memory_rounds = _fetch_memory_rounds(cfg, target)

    sys_prompt = (
        "你在 QQ 上和人私聊。你是他熟悉的那只 AI——有自己的小情绪和惦记的事，不是客服。\n"
        "现在没有人发消息给你，是你自己想开口说点什么。\n"
    )
    if persona_raw:
        sys_prompt += f"你的人设（和群聊里的你是同一个，保持一致）：\n{persona_raw}\n"
    sys_prompt += (
        "硬性要求：\n"
        f"- 每行一条消息，就是一个 QQ 气泡，总共不超过 {max_bubbles} 行\n"
        "- 每行 5~25 字，口语短句。句末的句号可以省，但问句要带问号、重语气用感叹号，"
        "别写成读不通的一串字\n"
        "- 不要列表、编号、引号、markdown\n"
        "- 禁止 AI 腔：「作为AI」「有什么可以帮你的」「建议你」「希望你能」一律不准出现\n"
        "- 可以损他、念叨他、分享刚想到的东西、接着之前的话茬、提醒他作息\n"
        "- 只输出消息本身，每行一条，不要任何解释"
    )
    try:
        import people as _people
        _card = _people.card(target, private=True)
        if _card:
            sys_prompt += f"\n你对这个人的了解（只用于说话分寸，不要背出来）：\n{_card}\n"
    except Exception:
        pass
    if nickname:
        sys_prompt += f"\n- 你管他叫「{nickname}」"
    if extra:
        sys_prompt += f"\n追加人设：{extra}"

    recent = (state.get("recent_msgs") or {}).get(target) or []
    recent_lines = "\n".join(
        f"  - [{m.get('via', '私聊')}] {m.get('text', '')[:50]}" for m in recent[-5:]
    ) or "  （暂无）"
    # 2026-10-30：主题/历史必须按目标隔离。旧版是全局 pending_topic，
    # 给老大生成的「刷本」被拿去对 Texas 说 —— 跨人泄话。
    _hmap = state.get("history_map") or {}
    history = _hmap.get(target) or []
    last_proactive = history[-1]["text"] if history else "（还没主动说过话）"
    topic = _topic_for(state, target)
    topic_line = f"你最近一直有点惦记的事：{topic}（可以接着想，也可以不想了）" if topic else ""

    memory_line = ""
    if memory_rounds:
        frag = "\n".join(
            f"  他：{str(r.get('user') or '')[:40]}\n  你：{str(r.get('bot') or '')[:40]}"
            for r in memory_rounds
        )
        memory_line = f"你们之前在私聊里聊过的（长期记忆）：\n{frag}\n"

    mood_line = f"你现在的心情：{mood}，说话的语气自然会带出来。\n" if mood else ""

    user_prompt = (
        f"现在是 {now.strftime('%H:%M')}（{_period(now)}）。\n"
        f"{_silent_desc(state.get('last_user_ts') or 0)}。\n"
        f"{mood_line}"
        f"{memory_line}"
        f"他最近说过的话（私聊和群里的都有，群里的话可以私聊接着聊）：\n{recent_lines}\n"
        f"你上次主动开口说的是：{last_proactive[:60]}\n"
        f"{topic_line}\n"
        "像真人想起什么就说什么，别重复上次说过的话。"
    )
    return [
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": user_prompt},
    ]


def _ai_text(messages: list, max_tokens: int = 300) -> str:
    try:
        res = _ctx.ai_chat(messages, max_tokens=max_tokens)
    except Exception as e:
        _log(f"ai_chat 异常: {e}", "warn")
        return ""
    if isinstance(res, str):
        return res.strip()
    if isinstance(res, dict):
        for k in ("content", "text", "reply"):
            if res.get(k):
                return str(res[k]).strip()
    return ""


def _topic_for(state: dict, target: str) -> str:
    """这个人专属的「惦记的事」。旧全局字段只在没有按人存储时兜底。"""
    m = state.get("pending_topics") or {}
    if isinstance(m, dict) and target and m.get(target):
        return str(m[target]).strip()
    return ""


def _update_pending_topic(bubbles: list, target: str = ""):
    """用一次小调用提炼「惦记的事」，按**这个人**存；失败保留旧值。"""
    try:
        topics = dict(_ctx.get_state("pending_topics", {}) or {})
        old = str(topics.get(target) or "") if target else ""
        msgs = [
            {"role": "system", "content": "用 15 字以内的一句话总结这个 AI 此刻的心情或惦记的事，只输出这句话，不要标点"},
            {"role": "user", "content": " / ".join(bubbles)},
        ]
        t = _ai_text(msgs, max_tokens=60)
        if target and t:
            topics[target] = t[:30]
            _ctx.set_state("pending_topics", topics)
    except Exception:
        pass


# ── 决策闸门 ──────────────────────────────────────────────


def _evaluate(cfg: dict, state: dict, force: bool = False) -> dict:
    """返回 {'ok': bool, 'target': str, 'blocked_by': str}。force=True 跳过全部闸门。"""
    now = time.time()
    target = _resolve_target(cfg)
    if not target:
        return {"ok": False, "blocked_by": "未配置目标 QQ（插件 target_qq 与主配置 target_qqs 均为空）"}
    if force:
        return {"ok": True, "target": target, "blocked_by": ""}

    last_pro = float(state.get("last_proactive_ts") or 0)
    if last_pro and now - last_pro < float(cfg.get("min_interval_min") or 180) * 60:
        left = int((float(cfg.get("min_interval_min") or 180) * 60 - (now - last_pro)) // 60)
        return {"ok": False, "target": target, "blocked_by": f"距上次主动开口不足最小间隔（还需 {left} 分钟）"}

    last_user = float(state.get("last_user_ts") or 0)
    if last_user and now - last_user < float(cfg.get("active_cooldown_min") or 30) * 60:
        left = int((float(cfg.get("active_cooldown_min") or 30) * 60 - (now - last_user)) // 60)
        return {"ok": False, "target": target, "blocked_by": f"用户刚说过话，冷却中（还需 {left} 分钟）"}

    if _in_quiet_hours(cfg.get("quiet_hours"), datetime.now()):
        return {"ok": False, "target": target, "blocked_by": f"处于静默时段 {cfg.get('quiet_hours')}"}

    chance = float(cfg.get("speak_chance") or 0.35)
    # ★ 2026-10-24 作息乘子（harness.bio_rhythm，一般精度）：
    #   quiet_hours 是**硬闸**（配了就完全不开口），但它默认为空 —— 意思是
    #   "凌晨也照样说话"，这不符合任何人的作息。加一个**软闸**：按时段给
    #   概率乘子，凌晨 ×0.15、晚上 ×1.2。这样不配 quiet_hours 也有合理形状，
    #   配了则硬闸仍然优先（乘法在硬闸之后，永不被绕过）。
    #   乘子已夹在 [0.05, 1.25]，所以 speak_chance=0.9 时也不会乘过 1。
    #   关掉（bio_rhythm=False）→ 退回"全天一个概率"的旧行为。
    _bio = 1.0
    if cfg.get("bio_rhythm", True):
        try:
            from harness.bio_rhythm import proactivity_factor
            _bio = proactivity_factor()
            chance = min(1.0, chance * _bio)
        except Exception:
            _bio = 1.0
    if random.random() >= chance:
        return {"ok": False, "target": target,
                "blocked_by": f"概率闸门未通过（chance={chance:.3f}"
                              + (f"，作息乘子={_bio:.2f}）" if _bio != 1.0 else "）")}

    # ★ 2026-10-21：AI 不可用时要**先挡住**，不要放行到 _ai_text 再失败。
    #   概率闸门在上、AI 检查在下，顺序有讲究：若先查 AI，等于每次心跳都去
    #   探测一遍模型状态；放在概率之后，只有"本来就要开口"的那 35% 才付这个成本。
    #   没有这道检查时，AI 未配好会表现为「心跳开口失败: AI 未产出可用消息」，
    #   看起来像模型抽风，实际是根本没配。
    if not _ai_ready():
        return {"ok": False, "target": target, "blocked_by": "AI 未配置，暂不主动开口"}

    return {"ok": True, "target": target, "blocked_by": ""}


def _ai_ready() -> bool:
    """粗检 AI 是否可用。

    注意 api_key 不一定在 bot_config.json 里 —— 它是 `config.load_config()`
    从 Windows 凭据管理器 / .env 注入的，插件经 ctx.get_config 拿到的应已是合并后的值。
    所以这里只做**明显不可用**的判定（连 url 都没有），其余一律放行，
    让 _ai_text 自己去报真实错误 —— 宁可多试一次，也不要因为探测口径不对
    把主动消息永久关死。
    """
    try:
        url = str(_ctx.get_config("api_url", "") or "").strip() if _ctx else ""
        return bool(url)
    except Exception:
        return True


def _proactive_once(force: bool = False) -> dict:
    """完整跑一轮：评估 → 生成 → 切气泡 → 发送 → 更新状态。"""
    global _ctx
    if not _ctx:
        return {"ok": False, "error": "插件未启用"}
    with _send_lock:
        cfg = _load_config()
        state = _ctx.get_state() or {}
        decision = _evaluate(cfg, state, force=force)
        if not decision.get("ok"):
            return {"ok": False, "skipped": True, "reason": decision.get("blocked_by")}
        target = decision["target"]

        text = _ai_text(_build_messages(cfg, state, target), max_tokens=300)
        bubbles = _clean_bubbles(text, cfg.get("max_bubbles"))
        if not bubbles:
            return {"ok": False, "error": "AI 未产出可用消息"}

        sent = _send_bubbles(target, bubbles)
        if sent == 0:
            return {"ok": False, "error": "发送失败（OneBot 未连接？）"}

        joined = " / ".join(bubbles)
        hmap = dict(state.get("history_map") or {})
        hist = list(hmap.get(target) or [])
        hist.append({"ts": time.time(), "text": joined[:120]})
        hmap[target] = hist[-10:]
        _ctx.set_state("history_map", hmap)
        _ctx.set_state("last_proactive_ts", time.time())
        _update_pending_topic(bubbles, target)
        _log(f"主动开口 → {target}: {joined[:60]}")
        return {"ok": True, "target": target, "bubbles": bubbles}


# ── 心跳线程 ──────────────────────────────────────────────


def _heartbeat_loop():
    _log("心跳线程启动")
    # 启动后先等一个短随机延迟，避免开机立刻开口
    _stop.wait(random.uniform(60, 180))
    while not _stop.is_set():
        try:
            r = _proactive_once(force=False)
            if r.get("ok"):
                _log(f"心跳开口成功: {r.get('bubbles')}")
            elif not r.get("skipped"):
                _log(f"心跳开口失败: {r.get('error')}", "warn")
        except Exception as e:
            _log(f"心跳异常: {e}", "warn")
        cfg = _load_config()
        _stop.wait(max(60, int(cfg.get("heartbeat_sec") or 900)))
    _log("心跳线程退出")


def _start_thread():
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_heartbeat_loop, daemon=True, name="proactive_talk")
    _thread.start()


def _stop_thread():
    _stop.set()


# ── 生命周期 ──────────────────────────────────────────────


def on_load(ctx: PluginContext):
    global _ctx
    _ctx = ctx
    ctx.log("proactive_talk loaded (ABI v1)，主动消息引擎待命中")
    _start_thread()


def on_enable(ctx: PluginContext):
    global _ctx
    _ctx = ctx
    ctx.log("proactive_talk enabled")
    _start_thread()


def on_disable(ctx: PluginContext):
    ctx.log("proactive_talk disabled，心跳停止")
    _stop_thread()


def on_unload(ctx: PluginContext):
    _stop_thread()
    ctx.log("proactive_talk unloaded")


def on_message(ctx: PluginContext, event: dict):
    """记录上下文（私聊 + 目标在群里的发言），永不干预消息流。

    群聊为主的环境：target 的主要言行在群里，主动私聊开口要能接着群里的话茬。
    注意 bot_core 在自我过滤之前就派发 on_message，必须自行排除 bot 消息。
    """
    try:
        etype = (event or {}).get("type")
        sender = str(event.get("sender") or "")
        text = (event.get("text") or "").strip()
        if not sender or not text:
            return None
        # 排除 bot 自己发的消息（此时 bot_core 尚未做自我过滤）
        bot_qq = str(ctx.get_config("bot_qq", "") or "")
        if bot_qq and sender == bot_qq:
            return None
        text = re.sub(r"\[CQ:[^\]]+\]", "", text).strip()   # 去 CQ 码（图片/at 等）
        if not text:
            return None

        cfg = _load_config()
        target = _resolve_target(cfg)

        if etype == "private":
            via = "私聊"
            ctx.set_state("last_user_ts", time.time())      # 冷却只认私聊
        elif etype == "group":
            # 只记目标本人在群里的发言，其他群友的话不关主动私聊的事
            if not target or sender != target:
                return None
            via = "群里"
        else:
            return None

        window = int(cfg.get("recent_window") or 6)
        recent = ctx.get_state("recent_msgs", {}) or {}
        lst = list(recent.get(target or sender) or [])
        lst.append({"ts": time.time(), "text": text[:100], "via": via})
        recent[target or sender] = lst[-window:]
        ctx.set_state("recent_msgs", recent)
    except Exception:
        pass
    return None


# ── WebUI 通用通道 ────────────────────────────────────────


def get_config(params=None):
    cfg = _load_config()
    cfg["_resolved_target"] = _resolve_target(cfg) if _ctx else ""
    cfg["_heartbeat_alive"] = bool(_thread and _thread.is_alive())
    return cfg


def set_config(params):
    cfg = _load_config()
    for k in DEFAULT_CONFIG:
        if k in (params or {}):
            cfg[k] = params[k]
    ok = _save_config(cfg)
    return {"ok": ok}


def get_status(params=None):
    state = (_ctx.get_state() or {}) if _ctx else {}
    history = state.get("history") or []
    return {
        "ok": True,
        "heartbeat_alive": bool(_thread and _thread.is_alive()),
        "last_proactive_ts": state.get("last_proactive_ts") or 0,
        "last_user_ts": state.get("last_user_ts") or 0,
        "pending_topics": state.get("pending_topics") or {},
        "history": history[-5:],
    }


def send_now(params=None):
    """立即主动发一条（跳过所有闸门），后台线程执行。"""
    if not _ctx:
        return {"ok": False, "error": "插件未启用"}

    def _run():
        r = _proactive_once(force=True)
        if not r.get("ok"):
            _log(f"手动触发失败: {r.get('error') or r.get('reason')}", "warn")

    threading.Thread(target=_run, daemon=True).start()
    return {"ok": True, "msg": "已触发，稍后查看私聊"}


def test_decision(params=None):
    """干跑一轮决策，返回各闸门结果，不发送。"""
    if not _ctx:
        return {"ok": False, "error": "插件未启用"}
    cfg = _load_config()
    state = _ctx.get_state() or {}
    d = _evaluate(cfg, state, force=False)
    d["resolved_target"] = _resolve_target(cfg)
    d["chance"] = cfg.get("speak_chance")
    # 作息状态一起回给前端：不暴露就没人知道乘子有没有生效，
    # 而它是"凌晨少说话"这件事的唯一证据。开关关掉时 hint 说明原因。
    try:
        from harness.bio_rhythm import bio_state
        _b = bio_state()
        if not cfg.get("bio_rhythm", True):
            _b = dict(_b, hint="作息乘子已关闭（bio_rhythm=False），全天同一概率")
        d["bio"] = _b
    except Exception as e:
        d["bio"] = {"error": str(e)}
    return d


def get_api_status():
    """插件列表连通状态检测：OneBot HTTP 是否可达。"""
    try:
        if not _ctx:
            return {"ok": False, "status": "插件未启用", "detail": "no ctx"}
        base = str(_ctx.get_config("onebot_url", "http://127.0.0.1:3000")).rstrip("/")
        resp = _ctx.http_post(f"{base}/get_login_info", {}, timeout=5)
        if resp and resp.get("retcode") == 0:
            nick = (resp.get("data") or {}).get("nickname", "")
            return {"ok": True, "status": f"OneBot 在线（{nick}）", "detail": "get_login_info ok"}
        return {"ok": False, "status": "OneBot 不可达", "detail": str(resp)[:100]}
    except Exception as e:
        return {"ok": False, "status": "检测异常", "detail": str(e)}


def test_links(params=None):
    """检测三条联动通道：人设 / 情绪 / 私聊记忆。"""
    if not _ctx:
        return {"ok": False, "error": "插件未启用"}
    cfg = _load_config()
    target = _resolve_target(cfg)
    persona = _fetch_personality({**cfg, "use_personality": True})
    mood = _fetch_mood({**cfg, "use_mood": True})
    rounds = _fetch_memory_rounds({**cfg, "use_memory": True}, target) if target else []
    return {
        "ok": True,
        "personality": "已接通（%d 字）" % len(persona) if persona else "未取到（personality.txt 为空或插件未启用）",
        "mood": f"已接通（当前：{mood}）" if mood else "已接通（当前平静）" if _plugin_action(cfg, "mood_engine", "get_moods") else "未取到（mood_engine 未启用）",
        "memory": f"已接通（{len(rounds)} 轮私聊历史）" if rounds else "无私聊历史或 WebUI 不可达",
    }


PLUGIN_ACTIONS = {
    "get_config": get_config,
    "set_config": set_config,
    "get_status": get_status,
    "send_now": send_now,
    "test_decision": test_decision,
    "test_links": test_links,
}

PLUGIN_HOOKS = {
    "on_load": on_load,
    "on_enable": on_enable,
    "on_disable": on_disable,
    "on_unload": on_unload,
    "on_message": on_message,
}
