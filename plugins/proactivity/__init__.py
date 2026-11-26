# -*- coding: utf-8 -*-
"""plugins/proactivity —— 主动性行为的**插件壳**（订阅事件 + 定时扫描 + 投递）

本文件**不含任何判断逻辑**：状态怎么变在 `autonomy/ledger.py`（老路径 `harness/proactivity/ledger.py` 是转发壳），
该不该说在 `harness/proactivity/arbitrator.py`。这里只负责三件脏活：

    ① 同步收事件 → 更新内存里的会话状态（绝不阻塞主循环）
    ② 后台 tick → 扫描超时候选 → 仲裁 → 只把结果丢进投递线程
    ③ 投递 → ctx.send_text（唯一出口）→ 记账 / 留痕

## 为什么判定不能同步做

`on_event` 跑在 bot_core 主循环线程里。`ctx.ai_chat()` 要几秒，
同步做会把消息接收卡住 —— 撤回反馈插件已经因为「发送阻塞 6 秒」
吃过一次亏（用户撤回的消息照样被回复）。所以这里连模型判断一起丢后台。

## 四条行为

    no_reply       发出后长时间无人回 → 算了 / 轻补一句 / 换话题
    at_dropped     被 @ 却没等到 bot 的回复 → 补一句或说明
    pending_topic  自己说过「等下给你」却没下文 → 补上或交代
    recall         撤回（默认关；要接管必须把 recall_feedback_by 改成 proactivity，防双回）

前三条默认开，但共享**配置页滑条**上的预算档位（默认 1=克制，每场景每小时 1 次）。
"""
from __future__ import annotations

import os
import re
import threading
import time
from typing import Any, Dict, List, Optional

from harness.proactivity import arbitrator as _arb
from harness.proactivity import ledger as _led

PLUGIN_SPEC = 1
PLUGIN_MANIFEST = {
    "name": "proactivity",
    "version": "0.1.0",
    "api_version": 1,
    "author": "EggBloom",
    "description": "主动性行为：观察会话状态 → 规则+模型判断 → 统一预算下主动反馈",
    "dependencies": [],
    "priority": 80,
    "default_enabled": True,
}

STATE_KEY = "proactivity_runtime"
TICK_SEC = 30.0
DEDUP_TTL_S = 3600.0

_ctx = None
_stop = threading.Event()
_thread: Optional[threading.Thread] = None
_runtime_lock = threading.RLock()
_ledger: Optional[_led.Ledger] = None
_pending: List[Dict[str, Any]] = []
_sent_hourly: Dict[str, int] = {}
_last_sent: Dict[str, float] = {}
_inflight: set = set()
#: 面板"最近动作"（只在内存里，重启清空；要长期审计走日志）
_recent: List[Dict[str, Any]] = []


# ── 生命周期 ──────────────────────────────────────────────────────
def _cfg() -> dict:
    try:
        c = _ctx.get_config()
        return c if isinstance(c, dict) else {}
    except Exception:
        return {}


def _data_path() -> str:
    try:
        from data_store import get_data_dir
        base = get_data_dir()
    except Exception:
        base = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "data")
    return os.path.join(str(base), "proactivity", "ledger.json")


def _restore_runtime():
    global _sent_hourly, _last_sent
    try:
        st = _ctx.get_state(STATE_KEY, {}) or {}
        _last_sent = {k: float(v) for k, v in (st.get("last_sent") or {}).items()}
        _sent_hourly = {k: int(v) for k, v in (st.get("sent_hourly") or {}).items()
                        if str(k).endswith("|%d" % int(time.time() // 3600))}
    except Exception:
        pass


def _save_runtime():
    try:
        with _runtime_lock:
            _ctx.set_state(STATE_KEY, {
                "last_sent": _last_sent,
                "sent_hourly": _sent_hourly,
            })
    except Exception:
        pass


def _log(msg, level="info"):
    try:
        _ctx.log(msg, level)
    except Exception:
        pass


def _start_thread():
    """幂等起 tick 线程。

    ★ 2026-11-06 真机：plugin_manager 加载 spec 插件时**只调 on_load**，
      `on_enable` 只在用户在插件页手动点启用时才触发。第一次把线程启动
      写在 on_enable 里 → 插件默认启用、API 显示 loaded=true，但观测层
      根本没跑（日志一行都没有）。必须和 proactive_talk 一样在 on_load 起。
    """
    global _thread
    _stop.clear()
    if _thread and _thread.is_alive():
        return
    _thread = threading.Thread(target=_tick_loop, daemon=True, name="proactivity")
    _thread.start()
    _log("[主动] 观测层已启动（tick %ds）" % int(TICK_SEC))


def on_load(ctx):
    global _ctx, _ledger
    _ctx = ctx
    _ledger = _led.Ledger(_data_path())
    _restore_runtime()
    _start_thread()


def on_enable(ctx):
    global _ctx
    _ctx = ctx
    _start_thread()


def on_disable(ctx):
    _stop.set()


def on_unload(ctx):
    _stop.set()
    try:
        if _ledger:
            _ledger.save()
    except Exception:
        pass


# ── 事件：同步只更新状态，绝不做判断/发送 ─────────────────────────
def on_event(ctx, event_name: str = "", payload: dict = None, **kwargs):
    global _ctx
    _ctx = ctx
    if _ledger is None:
        return None
    name = str(event_name or "")
    p = payload if isinstance(payload, dict) else {}
    now = time.time()
    try:
        if name == "bot.replied":
            gid = str(p.get("group_id") or "")
            scene = str(p.get("scene") or ("group" if gid else "private"))
            target = gid if scene == "group" else str(p.get("user_id") or "")
            if target:
                _ledger.update(scene, target,
                               lambda s, n: _led.observe_bot_reply(s, p, n), now)
            return None
        if name == "message.in":
            gid = str(p.get("group_id") or "")
            scene = "group" if gid else "private"
            target = gid if gid else str(p.get("sender") or "")
            if target:
                _ledger.update(scene, target,
                               lambda s, n: _led.observe_user_message(s, p, n), now)
            return None
        if name == "turn.skipped":
            gid = str(p.get("group_id") or "")
            scene = "group" if gid else "private"
            target = gid if gid else str(p.get("sender") or p.get("user_id") or "")
            if target:
                _ledger.update(scene, target,
                               lambda s, n: _led.observe_skip(s, p, n), now)
            return None
        if name in ("outbound.failed", "outbound.sent"):
            scene = str(p.get("scene") or "group")
            target = str(p.get("target") or "")
            if target:
                ok = name == "outbound.sent"
                _ledger.update(
                    scene, target,
                    lambda s, n: _led.observe_delivery(s, p, n, ok=ok), now)
                _ledger.save()
            return None
        if name == "notice.poke":
            # 被戳也算一次互动：冷场计时从这里重新起算，不该"刚被戳完又去戳"
            gid = str(p.get("group_id") or "")
            scene = "group" if gid else "private"
            target = gid if gid else str(p.get("operator") or p.get("user_id") or "")
            if target:
                _ledger.update(scene, target,
                               lambda s, n: _led.observe_user_message(
                                   s, {"sender": target, "group_id": gid,
                                       "text": "", "ts": n}, n), now)
            return None
        if name == "job.done":
            # ⑨ 后台任务（含视频/AIGC 这类长链路）结束。任务本身不属于某个会话，
            #    汇报对象选**最近 15 分钟还活跃**的那个；都凉了就不打扰。
            _tgt, _scene = _recent_target(now, within=900)
            if _tgt:
                obs = _led.job_observation(p, _tgt, now, _scene)
                if obs:
                    with _runtime_lock:
                        _pending.append(obs)
            return None
        if name in ("notice.group_name", "notice.member_in",
                    "notice.member_out", "notice.friend_add"):
            # ⑫ 状态变更：即时候选（改群名 / 新人 / 退群 / 加好友）
            obs = _led.notice_observation(p, name, now)
            if obs:
                with _runtime_lock:
                    _pending.append(obs)
            return None
        if name == "notice.recall":
            gid = str(p.get("group_id") or "")
            scene = "group" if gid else "private"
            target = gid if gid else str(p.get("operator") or p.get("user_id") or "")
            if target:
                _ledger.update(scene, target,
                               lambda s, n: _led.observe_notice(s, p, n), now)
                obs = _led.recall_observation(p, now)
                if obs:
                    with _runtime_lock:
                        _pending.append(obs)
            return None
    except Exception as e:
        _log("[主动] 事件观测失败（已忽略）: %s" % e, "warn")
    return None


def _recent_target(now: float, within: float = 900.0):
    """最近还活跃的会话 (target, scene)。任务汇报 / 日程提醒没有天然收件人，
    都按这个选；都凉了就不打扰（返回空串）。"""
    best = None
    if _ledger is None:
        return "", ""
    for st in _ledger.states():
        ts = float(st.get("last_activity_ts") or 0)
        if ts and now - ts <= within and (
                best is None or ts > float(best.get("last_activity_ts") or 0)):
            best = st
    if not best:
        return "", ""
    return str(best.get("target") or ""), str(best.get("scene") or "private")


def _schedule_candidate(now: float, cfg: dict):
    """⑬ 日程临近：schedule 插件给出「下一条 + 剩余分钟」，这里只负责转成候选。"""
    if not bool(cfg.get("proactivity_schedule_enable", True)):
        return None
    try:
        near = int(cfg.get("proactivity_schedule_min") or 15)
    except (TypeError, ValueError):
        near = 15
    try:
        from plugins.schedule import now_state
        st = now_state() or {}
    except Exception:
        return None
    try:
        rem = int(st.get("remaining_min") or 0)
    except (TypeError, ValueError):
        return None
    if not (1 <= rem <= near):
        return None
    target, scene = _recent_target(now, within=3600)
    if not target:
        return None
    return _led.schedule_observation(st, target, now, scene)


def _care_candidate(now: float, cfg: dict):
    """⑭ 主动关心：读 vitals 的 get_vitals（它本来就有这个 action）。"""
    if not bool(cfg.get("proactivity_care_enable", True)):
        return None
    try:
        from plugins.vitals import PLUGIN_ACTIONS as _va
        data = _va["get_vitals"]({}) or {}
    except Exception:
        return None
    target, scene = _recent_target(now, within=3600)
    if not target:
        return None
    return _led.care_observation(data, target, now, scene)


# ── 后台：扫描 + 仲裁 + 投递 ─────────────────────────────────────
def _hour_key(scene: str) -> str:
    return "%s|%d" % (scene, int(time.time() // 3600))


def _session_key(scene: str, target: str) -> str:
    return "%s:%s" % (scene, target)


def _is_fired(dk: str) -> bool:
    return bool(_ledger) and _ledger.already_fired(dk, DEDUP_TTL_S)


def _mark_fired(dk: str, ts: float) -> None:
    """模型判 skip 也算处理过，否则同一 observation 每 tick 重进模型。"""
    if _ledger and dk:
        _ledger.mark_fired(dk, ts)


def _last(scene: str, target: str) -> float:
    with _runtime_lock:
        return float(_last_sent.get(_session_key(scene, target)) or 0)


def _hourly(scene: str) -> int:
    with _runtime_lock:
        return int(_sent_hourly.get(_hour_key(scene)) or 0)


def _chat(messages, max_tokens=160):
    try:
        return _ctx.ai_chat(messages, max_tokens=max_tokens)
    except Exception:
        return ""


#: 主动消息发出前的统一收口判据。
#: 这些不是"内容好不好"，而是"一眼看得出不是人说的"：
#:   · 单字应声（在/嗯/哦）—— 没人问就在，答什么
#:   · 时长数字（四小时/3分钟）—— 工程字段泄漏成台词（见 11-20 22:09）
#:   · 内部字段名（gap_hours / last_topic 之类）直接出现在正文里
_SAY_ECHO_RE = re.compile(r"^[\s在嗯哦啊哈额欸诶\-—…、,，。~～!！?？]*$")
_SAY_DURATION_RE = re.compile(
    r"(?:\d+|[一二两三四五六七八九十百]+)\s*(?:个)?\s*(?:小时|分钟|秒钟|天|周|星期)")
_SAY_FIELD_RE = re.compile(
    r"gap_hours|last_topic|at_dropped|no_reply|pending_topic|idle_contact"
    r"|resume_topic|user_burst|image_only|name_dropped")
#: 把"我在判断这该不该接"说出来 —— 真机 11-22 00:46 主动发了
#: 「叫的是某某，没我啥事」，群友随即引用它问「啥」。
#: 这类句子是模型自己的内部结论，不该进聊天。
_SAY_META_RE = re.compile(
    r"没我啥事|跟我没关系|与我无关|不关我事|不是在叫我|叫的是|在叫别人"
    r"|不是冲着我|没叫我|关我啥事")
#: 阴阳式招呼。真机 11-22 15:04 主动发「哟 舍得出来了」——群友只是隔了几小时
#: 出现，没有任何欠交代。这类话在**主动开口**里几乎只有阴阳这一种用法，
#: 所以只在这一处拦，不放进主链。
_SAY_SNARK_RE = re.compile(
    r"舍得出来|舍得露面|终于来了|终于舍得|还知道回来|稀客|难得啊|可算来了")


def _sanitize_say(text: str, kind: str = ""):
    """返回 (clean_text, reason)。reason 非空表示这条不该发出去。"""
    t = str(text or "").strip()
    if not t:
        return "", "empty"
    if len(t) <= 3 and _SAY_ECHO_RE.match(t):
        return "", "echo"
    if _SAY_DURATION_RE.search(t):
        return "", "duration"
    if _SAY_FIELD_RE.search(t):
        return "", "field"
    if _SAY_META_RE.search(t):
        return "", "meta"
    if _SAY_SNARK_RE.search(t):
        return "", "snark"
    return t, ""


def _deliver(action: Dict[str, Any]):
    """真正执行动作。成功才记账；失败不记账。

    say  → ctx.send_text（文本，私聊/群聊都行）
    poke → ctx.api_call("send_poke")（**只有私聊有这个动作**，规则层已拦群聊）
    """
    obs = action.get("obs") or {}
    dk = str(obs.get("dedupe_key") or "")
    if dk in _inflight:
        return
    _inflight.add(dk)
    try:
        act = str(action.get("act") or "say")
        text = str(action.get("text") or "").strip()
        if act == "say" and not text:
            return
        scene, target = str(obs.get("scene")), str(obs.get("target"))
        if act == "poke":
            if scene != "private":
                _log("[主动/跳过] 群聊不支持 poke", "warn")
                return
            try:
                r = _ctx.api_call("send_poke", {"user_id": target}) or {}
            except Exception as e:
                _log("[主动戳] 调用异常（不重试）: %s" % e, "warn")
                return
            ok = bool(r.get("status") == "ok" or r.get("retcode") == 0 or r.get("data"))
            if not ok:
                _log("[主动戳] 失败（不重试、不回滚额度）: %s" % str(r)[:120], "warn")
                return
        elif act in ("image", "video"):
            # ⑨ 的产物直发：视频链路 / 抽帧图做好了就直接发过去，
            # 而不是只嘴上说"做好了"。路径由 job.done 的 artifact 带来。
            path = str(action.get("artifact") or obs.get("artifact") or "")
            if not path or not os.path.isfile(path):
                _log("[主动/跳过] %s 产物不存在: %s" % (act, path), "warn")
                return
            try:
                if act == "video":
                    ok = (bool(_ctx.send_video(path, group_id=target)) if scene == "group"
                          else bool(_ctx.send_video(path, target=target)))
                else:
                    ok = (bool(_ctx.send_image(path, group_id=target)) if scene == "group"
                          else bool(_ctx.send_image(path, target=target)))
            except Exception as e:
                _log("[主动/发送异常] %s: %s" % (act, e), "warn")
                return
            if not ok:
                _log("[主动/发送失败] %s %s:%s" % (act, scene, target), "warn")
                return
        elif act == "say":
            _clean, _why = _sanitize_say(text, str(obs.get("kind") or ""))
            if _why:
                _log("[主动/跳过] %s 正文被判据拦下（%s）: %s"
                     % (obs.get("kind"), _why, text[:40]), "warn")
                return
            text = _clean
            ok = (bool(_ctx.send_text(text, group_id=target)) if scene == "group"
                  else bool(_ctx.send_text(text, target=target)))
            if not ok:
                _log("[主动/发送失败] %s %s:%s" % (obs.get("kind"), scene, target), "warn")
                return
        else:
            return
        # 产物之后补一句配文（say 分支已经发过，不重复）
        if act in ("image", "video") and text:
            try:
                if scene == "group":
                    _ctx.send_text(text, group_id=target)
                else:
                    _ctx.send_text(text, target=target)
            except Exception:
                pass
        now = time.time()
        with _runtime_lock:
            _last_sent[_session_key(scene, target)] = now
            hk = _hour_key(scene)
            _sent_hourly[hk] = int(_sent_hourly.get(hk) or 0) + 1
            _recent.append({"ts": now, "kind": obs.get("kind"), "act": act,
                            # 2026-11-26 S3：统一动作，和群聊/私聊/主动层同一套词
                            "action": str(action.get("action") or act),
                            "target": "%s:%s" % (scene, target),
                            "text": text[:80],
                            "reason": str(action.get("reason") or "")[:60]})
            del _recent[:-RECENT_MAX]
        if _ledger:
            _ledger.mark_fired(dk, now)
            _ledger.save()
        _save_runtime()
        _log("[主动] %s(%s) → %s:%s｜%s" % (
            obs.get("kind"), act, scene, target, text[:40] or "(无文案)"))
    finally:
        _inflight.discard(dk)


def _num_or(cfg, key, default):
    """取数字配置；**0 是有效值**（分离态把所有阈值归零 = 立刻），
    不能像以前那样用 `x or default` 把 0 吃掉退回默认值。"""
    v = cfg.get(key)
    if v is None or v == "":
        return float(default)
    try:
        return float(v)
    except Exception:
        return float(default)


def _tick_once():
    if _ledger is None:
        return
    cfg = _cfg()
    now = time.time()
    timeout = {
        "no_reply_s": _num_or(cfg, "proactivity_no_reply_min", 8) * 60,
        "at_dropped_s": _num_or(cfg, "proactivity_at_dropped_min", 3) * 60,
        "promise_s": _num_or(cfg, "proactivity_pending_topic_min", 20) * 60,
        # 冷场：沿用老戳一戳插件的 4 小时下限，可用配置调
        "idle_s": _num_or(cfg, "proactivity_idle_min", 240) * 60,
        # 2026-11-07 P3：⑤⑥⑦⑧ 的阈值
        "burst_n": max(2, int(_num_or(cfg, "proactivity_burst_count", 3))),
        "burst_s": _num_or(cfg, "proactivity_burst_min", 2) * 60,
        "image_s": _num_or(cfg, "proactivity_image_min", 2) * 60,
        "named_s": _num_or(cfg, "proactivity_name_min", 2) * 60,
        "resume_s": _num_or(cfg, "proactivity_resume_min", 3) * 60,
        "resume_gap_h": _num_or(cfg, "proactivity_resume_gap_h", 6),
        # 明确投递失败期间只允许 outbox 重试原文，不再让模型生成“我没看见”。
        "delivery_hold_s": max(600.0,
                               _num_or(cfg, "proactivity_at_dropped_min", 3) * 60),
    }
    observations: List[Dict[str, Any]] = []
    for st in _ledger.states():
        try:
            observations += _led.scan(st, now, **timeout)
        except Exception:
            continue
    with _runtime_lock:
        observations += list(_pending)
        _pending.clear()
    # ⑬ 日程临近：不依赖会话状态，单独取一次（插件不在就静默跳过）
    for _fn in (_schedule_candidate, _care_candidate):
        try:
            _o = _fn(now, cfg)
            if _o:
                observations.append(_o)
        except Exception:
            pass
    if not observations:
        return
    action = _arb.arbitrate(
        observations, cfg=cfg, now=now,
        is_fired=_is_fired, last_sent=_last, hourly_count=_hourly,
        chat_fn=_chat, log=_log, mark_fired=_mark_fired)
    if action:
        threading.Thread(target=_deliver, args=(action,), daemon=True,
                         name="proactivity-send").start()
    _ledger.save()


def _tick_loop():
    # 启动后先缓一拍：刚重启时不该立刻对"重启前的沉默"做判断
    _stop.wait(min(TICK_SEC, 20))
    # ★ on_load 阶段的 ctx.log 会回退到 stdout（那时 bot_core.log 还没注入），
    #   日志文件里一行都看不到 → "插件到底跑没跑"不可查。等主程序就绪后
    #   在这里补一行落盘日志，只打一次。
    _log("[主动] 观测层就绪（tick %ds，预算档位由配置页决定）" % int(TICK_SEC))
    while not _stop.is_set():
        try:
            _tick_once()
        except Exception as e:
            _log("[主动] tick 异常（不影响主程序）: %s" % e, "warn")
        _stop.wait(TICK_SEC)


PLUGIN_HOOKS = {
    "on_load": on_load,
    "on_enable": on_enable,
    "on_disable": on_disable,
    "on_unload": on_unload,
    "on_event": on_event,
}


# ── 插件面板（/page?plugin_id=proactivity）────────────────────────
#: 面板允许读写的键。**白名单**：面板不能写 api_key 这类敏感键，
#: 也不能顺手改别人的配置 —— 只认这一张表。
CFG_KEYS = (
    "proactivity_enable",
    "proactivity_budget_level",
    "proactivity_quiet_hours",
    "proactivity_no_reply_min",
    "proactivity_at_dropped_min",
    "proactivity_pending_topic_min",
    "proactivity_idle_min",
    "proactivity_no_reply_enable",
    "proactivity_at_dropped_enable",
    "proactivity_pending_topic_enable",
    "proactivity_idle_contact_enable",
    "proactivity_recall_enable",
    "proactivity_takeover_legacy",
    # 2026-11-07 P3：⑤⑥⑦⑧⑨⑫ 六条
    "proactivity_user_burst_enable",
    "proactivity_image_only_enable",
    "proactivity_name_dropped_enable",
    "proactivity_resume_enable",
    "proactivity_job_enable",
    "proactivity_state_enable",
    "proactivity_burst_count",
    "proactivity_burst_min",
    "proactivity_image_min",
    "proactivity_name_min",
    "proactivity_resume_gap_h",
    "proactivity_resume_min",
    "proactivity_schedule_enable",
    "proactivity_schedule_min",
    "proactivity_care_enable",
    "proactivity_persona",
    "proactivity_personality",
    "proactivity_behavior_style",
    "proactivity_reply_style",
    "proactivity_style_pool",
    "proactivity_style_prob",
)

#: 面板上"最近动作"最多留几条
RECENT_MAX = 8


def _recent_list():
    with _runtime_lock:
        return list(_recent)


def _action_cfg() -> Dict[str, Any]:
    """面板用：直接读合并配置，不依赖 `_ctx`。

    插件面板的 action 可能跑在 webui 进程（bot_core 没起时走本地兜底），
    那时 `on_load` 没执行、`_ctx` 是 None —— 用 `_cfg()` 会一路异常拿到空表，
    面板就显示全是"—"。读配置这件事本来就不需要宿主上下文。
    """
    try:
        from config import load_config
        return load_config() or {}
    except Exception:
        return {}


def _status() -> Dict[str, Any]:
    lv = _arb.level_of(_action_cfg())
    with _runtime_lock:
        hourly = dict(_sent_hourly)
        last = dict(_last_sent)
    return {
        "running": bool(_thread and _thread.is_alive()),
        "sessions": len(_ledger.states()) if _ledger else 0,
        "pending": len(_pending),
        "recall_feedback_by": str(_action_cfg().get("recall_feedback_by") or "core"),
        "budget_level": lv.get("level"),
        "budget_label": lv.get("label"),
        "per_hour": lv.get("per_hour"),
        "cooldown_s": lv.get("cooldown_s"),
        "hourly": hourly,
        "last_sent": last,
        "recent": _recent_list(),
    }


def _clean_value(k: str, v):
    """面板传来的值一律规整：数字钳到合法区间，布尔只认真值。"""
    if k.endswith("_enable") or k in ("proactivity_enable", "proactivity_takeover_legacy"):
        return bool(v)
    if k == "proactivity_budget_level":
        try:
            return max(0, min(3, int(v)))
        except (TypeError, ValueError):
            return 1
    if k == "proactivity_quiet_hours":
        s = str(v or "").strip()
        import re as _re
        if not s:
            return ""
        return s if _re.match(r"^\d{1,2}-\d{1,2}$", s) else ""
    if k == "proactivity_persona":
        return str(v) if str(v) in ("entertain", "gentle", "sharp") else "entertain"
    if k == "proactivity_style_pool":
        if isinstance(v, list):
            return [str(x).strip() for x in v if str(x).strip()][:8]
        return [x.strip() for x in str(v or "").splitlines() if x.strip()][:8]
    if k == "proactivity_style_prob":
        try:
            return max(0.0, min(1.0, float(v)))
        except (TypeError, ValueError):
            return 0.15
    if k == "proactivity_burst_count":
        try:
            return max(2, min(10, int(float(v))))
        except (TypeError, ValueError):
            return 3
    if k == "proactivity_resume_gap_h":
        try:
            return max(1, min(720, int(float(v))))
        except (TypeError, ValueError):
            return 6
    if k.endswith("_min"):
        try:
            return max(1, min(1440, int(float(v))))
        except (TypeError, ValueError):
            return 240 if k == "proactivity_idle_min" else 10
    return v


def act_get_config(params=None):
    cfg = _action_cfg()
    return {"ok": True,
            "config": {k: cfg.get(k) for k in CFG_KEYS},
            "status": _status()}


def act_set_config(params=None):
    update = {k: _clean_value(k, v) for k, v in (params or {}).items()
              if k in CFG_KEYS}
    if not update:
        return {"ok": False, "error": "没有可写的键"}
    try:
        # 直接改原始 bot_config.json（不把 load_config 合并进来的默认值一起固化），
        # 落盘后 invalidate_config_cache，主进程下一轮就读到。
        import json as _json
        from config import save_config, APP_CONFIG_FILE
        raw = {}
        try:
            with open(APP_CONFIG_FILE, encoding="utf-8") as f:
                raw = _json.load(f) or {}
        except Exception:
            raw = {}
        raw.update(update)
        ok = bool(save_config(raw))
        return {"ok": ok, "config": {k: _action_cfg().get(k) for k in CFG_KEYS},
                "applied": update,
                "error": "" if ok else "配置写盘失败"}
    except Exception as e:
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}


def act_take_recall(params=None):
    """④ 一键交棒：主链让位 + 本插件接管。

    必须**同时**写两个键，只写一个的后果：
      · 只写 recall_feedback_by → 主链让位了，但本插件开关没开 = 谁都不回
      · 只开插件开关 → 主链照回 + 本插件也回 = 一次撤回两条
    主链那边已认 "proactivity" 这个值（`bot_core.py` 两处判断）。
    """
    try:
        import json as _json
        from config import save_config, APP_CONFIG_FILE
        raw = {}
        try:
            with open(APP_CONFIG_FILE, encoding="utf-8") as f:
                raw = _json.load(f) or {}
        except Exception:
            raw = {}
        raw["recall_feedback_by"] = "proactivity"
        raw["proactivity_recall_enable"] = True
        ok = bool(save_config(raw))
        return {"ok": ok, "recall_feedback_by": raw["recall_feedback_by"],
                "error": "" if ok else "配置写盘失败"}
    except Exception as e:
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}


PLUGIN_ACTIONS = {
    "get_config": act_get_config,
    "set_config": act_set_config,
    "take_recall": act_take_recall,
}
