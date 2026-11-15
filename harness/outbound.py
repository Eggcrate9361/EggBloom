# -*- coding: utf-8 -*-
"""harness/outbound.py —— 所有出站的唯一队列（1.6.0 刀 5）。

## 为什么要有它

1.6.0 之前，出站有五条路各走各的：

| 入口 | 出口 |
|---|---|
| `send_message` / `send_group_message` | `api_call("send_private_msg"/"send_group_msg")` |
| `smart_send` / `smart_send_group` | 同上，只是带打字节奏 |
| `send_image` / `send_group_image` | 同上，消息段是图片 |
| 插件 `ctx.send_text` → 宿主 `_plugin_send_text` | 拐进 smart_send |
| 插件 `ctx.send_group` / `ctx.send_private` | **直连** `api_call` |

后果不是"代码丑"，是**行为不可控**：并发线程同时发会互相插队、失败没有统一重试、
同一条消息可能在日志里出现两次（一次在发送函数、一次在 api_call）、
插件的出口绕过了主链的审计与节奏。

## 这一层做什么（v1：队列本体）

- **串行**：一个 worker 线程按入队顺序发 —— 多线程同时说话也不会交错；
- **可选项**：`outbound_dedupe_sec`（默认 0 = 关）同 target 同内容窗口内去重；
  `outbound_min_gap_ms`（默认 0 = 关）同 target 最小间隔；
- **重试**：失败重试一次（网络抖动最常见），两次都失败如实返回 False；
- **日志**：只在**真的发出去**时打一行 `[发送] …`（一条消息只出现一次）；
- 失败/成功都有结构化统计（`stats()`）。

## 谁调它

只有 bot_core 的发送函数（`send_message` / `send_group_message` /
`send_image` / `send_group_image` / poke）和插件 ABI 走这里；
**插件与协议层不再直连 OneBot**（验收：`grep send_group_msg|send_private_msg`
只剩 outbound 的适配器那一个函数）。

低层发送函数由宿主注入（`set_sender`），这样本模块不依赖 bot_core 的全局状态，
单测能直接塞一个假的发送器。另有可选的 `set_channel()`：把一个 Channel 当作
sender 来源（**默认关**，不设时逐字节保持现有行为），见其 docstring。
"""
from __future__ import annotations

import queue
import threading

try:
    from . import outbox as _outbox
except Exception:            # 单文件跑测试时没有包上下文
    import outbox as _outbox
import time
from typing import Any, Callable, Dict, Optional

#: 底层发送器：fn(kind, target, payload, is_group) -> dict（OneBot 响应）
_SENDER: Optional[Callable[..., dict]] = None
#: 送达校验器（可选）：fn(kind, target, payload, is_group) -> Optional[bool]
#:   True=确认已送达 / False=确认没送达 / None=查不了。
#:   ★ 2026-11-09：只在「不确定发没发出去」（超时 / 网络异常 / retcode=-1）
#:   时才问它 —— 这时候盲目重发就会让用户收到两条一模一样的消息。
_VERIFIER: Optional[Callable[..., Optional[bool]]] = None
#: 可选的 Channel 发送器来源（默认 None = 关闭）。设了之后 _do_job 优先用它的
#: .send()，签名与 _SENDER 一致；不设时逐字节保持现有行为（只用 _SENDER）。
_CHANNEL = None
_LOGGER: Optional[Callable[[str], None]] = None
_CFG: Optional[Callable[[], dict]] = None

_LOCK = threading.Lock()
_QUEUE: "queue.Queue[dict]" = queue.Queue()
#: 已经在内存队列里的 jid（恢复时跳过，防止同一条发两次）
_INMEM_IDS: set = set()
_WORKER: Optional[threading.Thread] = None
_STARTED = False
#: 停机信号（见 shutdown()）。哨兵对象要比任何 job 都唯一。
_STOP = threading.Event()
_SENTINEL = object()

#: 每个 target 上次真正发出的时间（限频用）
_LAST_SENT: Dict[str, float] = {}
#: 去重窗口：key = (is_group, target, kind, text) -> ts
_RECENT: Dict[tuple, float] = {}
_STATS = {"queued": 0, "sent": 0, "failed": 0, "deduped": 0, "retried": 0}

DEFAULT_DEDUPE_SEC = 0.0
DEFAULT_MIN_GAP_MS = 0.0
SEND_TIMEOUT_SEC = 30.0


def set_sender(fn: Optional[Callable[..., dict]]) -> None:
    """注入底层发送器（宿主提供）。测试里塞假函数即可。"""
    global _SENDER
    _SENDER = fn if callable(fn) else None


def set_channel(ch) -> None:
    """可选：把一个 Channel 作为 sender 来源（**默认关闭**）。

    `ch=None`（默认）→ 清除，回到现有 `_SENDER`。Channel.send 的签名与
    `_SENDER` 一致：`fn(kind, target, payload, is_group) -> dict`，返回值按
    `retcode` 判定（见 `_do_job`）。设置后 `_do_job` 优先用 `ch.send`；不设 /
    传 None 时**不改现有行为**，仍只用 `_SENDER`。对没有可调用 .send 的对象
    一律按「关闭」处理，不会把坏对象塞进队列。
    """
    global _CHANNEL
    _CHANNEL = ch if (ch is not None and callable(getattr(ch, "send", None))) else None


def _resolve_sender():
    """当前该用的发送器：默认 `_SENDER`；设了 Channel 时优先 `Channel.send`。"""
    if _CHANNEL is not None:
        fn = getattr(_CHANNEL, "send", None)
        if callable(fn):
            return fn
    return _SENDER


def set_logger(fn: Optional[Callable[[str], None]]) -> None:
    global _LOGGER
    _LOGGER = fn if callable(fn) else None


def set_config(fn: Optional[Callable[[], dict]]) -> None:
    """注入配置读取器（每次发送时现读，热重载立即生效）。"""
    global _CFG
    _CFG = fn if callable(fn) else None


def set_verifier(fn: Optional[Callable[..., Optional[bool]]]) -> None:
    """注入「送达校验器」（宿主提供，可选）。

    签名 `fn(kind, target, payload, is_group) -> Optional[bool]`：
    True=已送达 / False=确认没送达 / None=查不了。

    只在发送结果**不确定**（sender 抛异常、或 api_call 超时统一返回
    retcode=-1）时调用。没注入就按 None 处理，行为退回老逻辑（重试一次）。
    """
    global _VERIFIER
    _VERIFIER = fn if callable(fn) else None


def _is_uncertain(resp: Dict[str, Any]) -> bool:
    """这次失败到底「确不确定没发出去」？

    协议层明确回了业务错误码（retcode 非 0、非 -1）→ 确认没投递，重发安全；
    retcode=-1（超时 / 网络异常的统一返回值）或缺 retcode → 不确定。
    """
    try:
        rc = int(resp.get("retcode", -1))
    except (TypeError, ValueError):
        return True
    return rc == -1


def _verify_delivered(kind: str, target: str, payload: dict,
                      is_group: bool, label: str) -> bool:
    """重发之前问宿主：这条到底发出去没有。True=已送达（别再发）。"""
    fn = _VERIFIER
    if fn is None:
        return False
    try:
        got = fn(kind, target, payload, is_group)
    except Exception as e:
        _log(f"[发送] {label} → {target}: 送达校验异常（按未送达处理）: {e}")
        return False
    if got is True:
        _log(f"[发送] {label} → {target}: 超时后校验已送达，未重发")
        return True
    return False


def _log(msg: str) -> None:
    if _LOGGER:
        try:
            _LOGGER(msg)
        except Exception:
            pass


def _cfg() -> dict:
    if _CFG:
        try:
            return _CFG() or {}
        except Exception:
            return {}
    return {}


def _num(key: str, default: float) -> float:
    try:
        v = _cfg().get(key, default)
        return float(default if v is None else v)
    except Exception:
        return float(default)


def stats() -> dict:
    with _LOCK:
        out = dict(_STATS)
        out["queue"] = _QUEUE.qsize()
    # 落盘队列的分状态计数（sent/pending/dead…）—— 事后查"那条到底发没发"
    try:
        out["db"] = _outbox.default().stats()
    except Exception:
        out["db"] = {}
    return out


def reset_stats() -> None:
    with _LOCK:
        for k in _STATS:
            _STATS[k] = 0


def _recover_from_disk() -> int:
    """把上次没发完的任务排回内存队列（重启恢复）。"""
    n = 0
    try:
        ob = _outbox.default()
        n += ob.recover()
        with _LOCK:
            _skip = set(_INMEM_IDS)
        for chat in ob.chats_with_pending():
            for row in ob.due(chat):
                if row["id"] in _skip:
                    continue
                job = {"kind": row["kind"], "target": row["target"],
                       "payload": row["payload"], "is_group": row["is_group"],
                       "suffix": "", "jid": row["id"],
                       "done": threading.Event(), "ok": None}
                job["done"].set()          # 恢复的任务没有等待者
                _QUEUE.put(job)
                with _LOCK:
                    _INMEM_IDS.add(row["id"])
                n += 1
        if n:
            _log(f"[发送] 重启恢复 {n} 条未发出的消息")
    except Exception as e:
        _log(f"[发送] 恢复失败（本次不恢复）: {e}")
    return n


def _ensure_worker() -> None:
    global _WORKER, _STARTED
    with _LOCK:
        if _STARTED:
            return
        _STARTED = True
    # ★ 恢复必须在**锁外**做：`_recover_from_disk` 自己也要拿 `_LOCK`，
    #   threading.Lock 不可重入 —— 放在锁里会死锁（测试直接挂住）。
    try:
        _recover_from_disk()
    except Exception:
        pass
    _WORKER = threading.Thread(target=_run, daemon=True, name="outbound-worker")
    _WORKER.start()
    # 退出前把 worker 收干净（见 shutdown 的注释：不收会在 finalize 阶段崩进程）
    try:
        import atexit as _atexit
        _atexit.register(shutdown)
    except Exception:
        pass


def shutdown(timeout: float = 1.0) -> None:
    """停掉 worker 并等它退出（atexit 自动调用，测试里也可手动调）。

    为什么必须有这个：worker 是 daemon 线程，解释器 finalize 时如果它正好在写
    stdout，CPython 会抛

        Fatal Python error: _enter_buffered_busy:
        could not acquire lock for <_io.BufferedWriter name='<stdout>'>

    然后**进程直接崩**（Windows exit=0xC0000409）。测试里表现为「断言
    10/10 全过，但退出码非零」，还会被误判成业务代码坏了。
    在 atexit 里先 join 掉，finalize 阶段就没有线程还攥着 stdout。
    """
    _STOP.set()
    try:
        _QUEUE.put(_SENTINEL)          # 唤醒阻塞在 get() 上的 worker
    except Exception:
        pass
    w = _WORKER
    if w is not None and w.is_alive():
        try:
            w.join(timeout)
        except Exception:
            pass


def is_stopped() -> bool:
    """worker 是否已经停机（停机后再入队没有意义）。"""
    return _STOP.is_set()


def _label(kind: str, is_group: bool) -> str:
    where = "群" if is_group else "私聊"
    return {"text": where, "image": where + "图", "poke": where + "戳"}.get(kind, where)


def _run() -> None:
    while True:
        job = _QUEUE.get()
        if job is _SENTINEL:                 # shutdown() 放进来的一颗毒丸
            try:
                _QUEUE.task_done()
            except Exception:
                pass
            break
        try:
            _do_job(job)
        except Exception as e:                       # worker 绝不能死
            _log(f"[发送] 队列异常（继续跑）: {e}")
            try:
                job["done"].set()
            except Exception:
                pass
        finally:
            # ★ 出站动作账本：不论成功/失败/异常，一个 job 只记一条。
            #   挂在 finally 而不是 _do_job 的三个 return 上，避免漏记；
            #   纯旁路，账本自身出错绝不影响 task_done 与队列推进。
            try:
                try:
                    from . import outbound_ledger as _led
                except Exception:
                    import outbound_ledger as _led
                _led.record_job(job, log_fn=_log)
            except Exception:
                pass
            try:
                _QUEUE.task_done()
            except Exception:
                pass


def _do_job(job: dict) -> None:
    kind = str(job.get("kind") or "text")
    target = str(job.get("target") or "")
    is_group = bool(job.get("is_group"))
    payload = job.get("payload") or {}
    label = _label(kind, is_group)

    # 限频（同 target）：只影响"多快能发下一条"，不影响顺序
    gap = _num("outbound_min_gap_ms", DEFAULT_MIN_GAP_MS) / 1000.0
    if gap > 0:
        with _LOCK:
            last = _LAST_SENT.get(target + ("|g" if is_group else "|p"), 0.0)
        wait = gap - (time.time() - last)
        if wait > 0:
            time.sleep(min(wait, 5.0))

    sender = _resolve_sender()
    if sender is None:
        _log(f"[发送] {label} → {target}: 没有底层发送器（未注入），丢弃")
        _mark(failed=True)
        try:
            if job.get("jid"):
                _outbox.default().mark_failed(job["jid"], "没有底层发送器", backoff=False)
        except Exception:
            pass
        job["ok"] = False
        job["done"].set()
        return

    resp: Dict[str, Any] = {}
    for attempt in (1, 2):
        try:
            resp = sender(kind, target, payload, is_group) or {}
        except Exception as e:
            resp = {"retcode": -1, "msg": f"{type(e).__name__}: {e}"}
        # ★ 畸形 retcode（非 int 的字符串等）原先会让 int() 抛给 worker，
        #   job 的 ok 保持 None → 调用方只看到 False，消息静默没了。
        #   统一按 -1（不确定）处理，交给下面的送达校验 / 重试。
        try:
            _rc = int(resp.get("retcode", -1))
        except (TypeError, ValueError):
            _rc = -1
        if _rc == 0:
            with _LOCK:
                _LAST_SENT[target + ("|g" if is_group else "|p")] = time.time()
            _log(f"[发送] {label} → {target}: retcode=0{job.get('suffix') or ''}")
            _mark(sent=True)
            try:
                if job.get("jid"):
                    _outbox.default().mark_sent(job["jid"], resp)
            except Exception:
                pass
            job["ok"] = True
            job["resp"] = resp
            _forget(job)
            job["done"].set()
            return
        # ★ 2026-11-09（§2 #32）：失败分两类。协议层明确回了业务错误码
        #   → 确认没投递，重发安全；超时 / 连接异常（retcode=-1）→ 消息
        #   **可能已经到了**，闷头重发就是用户收到两条一模一样的。
        #   重发前先问宿主校验送达；没注入校验器时行为与原来完全一致。
        if attempt == 1 and _is_uncertain(resp) and _verify_delivered(
                kind, target, payload, is_group, label):
            with _LOCK:
                _LAST_SENT[target + ("|g" if is_group else "|p")] = time.time()
            _mark(sent=True)
            try:
                if job.get("jid"):
                    _outbox.default().mark_sent(
                        job["jid"], {"retcode": 0, "verified": True})
            except Exception:
                pass
            job["ok"] = True
            job["resp"] = {"retcode": 0, "verified": True}
            _forget(job)
            job["done"].set()
            return
        if attempt == 1:
            with _LOCK:
                _STATS["retried"] += 1
            time.sleep(0.3)

    _log(f"[发送] {label} → {target}: retcode={resp.get('retcode')} "
         f"msg={str(resp.get('msg') or '')[:80]}{job.get('suffix') or ''}")
    _mark(failed=True)
    try:
        if job.get("jid"):
            _outbox.default().mark_failed(job["jid"], str(resp.get("msg") or ""), resp)
    except Exception:
        pass
    job["ok"] = False
    job["resp"] = resp
    job["done"].set()


def _forget(job: dict) -> None:
    try:
        with _LOCK:
            _INMEM_IDS.discard(str(job.get("jid") or ""))
    except Exception:
        pass


def _mark(sent: bool = False, failed: bool = False, deduped: bool = False) -> None:
    with _LOCK:
        if sent:
            _STATS["sent"] += 1
        if failed:
            _STATS["failed"] += 1
        if deduped:
            _STATS["deduped"] += 1


def _dedupe_key(job: dict):
    kind = str(job.get("kind") or "text")
    payload = job.get("payload") or {}
    text = payload.get("text")
    if text is None:
        # 图片等没有文本，用长度+首字节做个粗指纹，避免把同样的图去重误杀
        m = payload.get("message")
        text = str(m)[:80]
    return (bool(job.get("is_group")), str(job.get("target") or ""), kind, str(text or ""))


def submit(kind: str, target: str, payload: dict, *, is_group: bool = False,
           suffix: str = "") -> dict:
    """入队一个出站任务；返回 job 字典（可等 `done` 事件）。"""
    job = {"kind": kind, "target": str(target or ""), "payload": payload or {},
           "is_group": bool(is_group), "suffix": suffix,
           "done": threading.Event(), "ok": None, "ts": time.time()}
    # ★ 出站动作账本：调用链来源在**同线程**里读出来存进 job，
    #   worker 是另一线程，contextvar 传不过去。读不到就留空（按 model 处理）。
    #   纯旁路，读失败不影响发送。
    try:
        from . import outbound_ledger as _led
        job["origin"] = _led.current_origin()
    except Exception:
        try:
            import outbound_ledger as _led
            job["origin"] = _led.current_origin()
        except Exception:
            job["origin"] = ""
    # ★ 2026-11-02（A 方案 1/4）：先落盘再入内存队列 —— 进程挂了也不丢。
    #   落盘失败只记一行，不挡发送（发送可用性优先于可审计性）。
    try:
        _ob = _outbox.default()
        _chat = ("g:" if is_group else "p:") + str(target or "")
        _dk = ""
        try:
            _dk = "|".join(str(x) for x in _dedupe_key(job))
        except Exception:
            _dk = ""
        job["jid"] = _ob.add(_chat, kind, str(target or ""), payload or {},
                             is_group=bool(is_group), dedupe=_dk)
        if job["jid"]:
            with _LOCK:
                _INMEM_IDS.add(job["jid"])
    except Exception as e:
        job["jid"] = ""
        _log(f"[发送] 落盘失败（继续走内存队列）: {e}")
    dedupe = _num("outbound_dedupe_sec", DEFAULT_DEDUPE_SEC)
    if dedupe > 0:
        key = _dedupe_key(job)
        now = time.time()
        with _LOCK:
            last = _RECENT.get(key)
            if last is not None and now - last < dedupe:
                _STATS["deduped"] += 1
                job["ok"] = True
                job["done"].set()
                return job
            _RECENT[key] = now
            if len(_RECENT) > 500:
                for k in list(_RECENT)[:250]:
                    _RECENT.pop(k, None)
    # 已停机（进程正在退出）：不再入队。否则调用方会傻等满 timeout 才拿到 False，
    # 而 worker 永远不会来处理这条。检查放在 _ensure_worker() 之前 —— 停机之后
    # 也不该再把线程拉起来。
    if _STOP.is_set():
        job["ok"] = False
        job["resp"] = {"retcode": -1, "msg": "outbound 已停机"}
        job["done"].set()
        return job
    _ensure_worker()
    with _LOCK:
        _STATS["queued"] += 1
    _QUEUE.put(job)
    return job


def send(kind: str, target: str, payload: dict, *, is_group: bool = False,
         suffix: str = "", timeout: float = SEND_TIMEOUT_SEC) -> bool:
    """入队并等待结果（调用方要的是 bool 的场合用这个）。"""
    return send_job(kind, target, payload, is_group=is_group,
                    suffix=suffix, timeout=timeout)[0]


def send_job(kind: str, target: str, payload: dict, *, is_group: bool = False,
             suffix: str = "", timeout: float = SEND_TIMEOUT_SEC):
    """入队并等待结果，返回 `(ok, resp)`。

    需要 OneBot 响应里 `message_id` 的调用方（群消息要记"自己说过什么"）用这个，
    不要为了拿 id 绕过队列直连 api_call。
    """
    job = submit(kind, target, payload, is_group=is_group, suffix=suffix)
    try:
        job["done"].wait(timeout=max(1.0, float(timeout)))
    except Exception:
        pass
    return bool(job.get("ok")), (job.get("resp") or {})


def send_text(text: str, target: str, *, is_group: bool = False,
              suffix: str = "", timeout: float = SEND_TIMEOUT_SEC) -> bool:
    return send("text", target, {"text": text, "message": text},
                is_group=is_group, suffix=suffix, timeout=timeout)


def send_segment(segments, target: str, *, is_group: bool = False,
                 kind: str = "image", suffix: str = "",
                 timeout: float = SEND_TIMEOUT_SEC) -> bool:
    return send(kind, target, {"message": segments, "text": ""},
                is_group=is_group, suffix=suffix, timeout=timeout)


def wait_idle(timeout: float = 5.0) -> bool:
    """等到队列清空（测试/退出用）。"""
    deadline = time.time() + max(0.1, float(timeout))
    while time.time() < deadline:
        if _QUEUE.qsize() == 0:
            time.sleep(0.02)
            if _QUEUE.qsize() == 0:
                return True
        time.sleep(0.02)
    return _QUEUE.qsize() == 0


__all__ = ["set_sender", "set_verifier", "set_channel", "set_logger", "set_config", "submit",
           "send", "send_job", "send_text", "send_segment", "stats",
           "reset_stats", "wait_idle", "shutdown", "is_stopped"]
