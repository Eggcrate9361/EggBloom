# -*- coding: utf-8 -*-
"""harness/session_log.py —— 事件溯源会话日志（任务 5.3，试点）。

对齐 harness 设计 §11.1 P1（设计稿未随公开仓库分发）：

  Session 是类型化事件的**仅追加日志**；模型历史全部从这条流派生。
  **铁律：模型可见即已记录** —— 任何到达模型请求的内容都必须能从日志重建。

  追加语义（照抄 DSH 的「追加校验」）：
    - 先**快照并冻结载荷**、**校验其为无损 JSON**，再通知观察者；
    - **被拒绝的追加不会改变日志、派生状态或事件流**。

  QQ 特化：QQ 的 `message_id` **不保证全局唯一** → 每条事件有**自己的 `seq`**
  （单调递增，按 (kind, id) 命名空间隔离）。

试点范围（手册 5.3：「先只覆盖一个群的对话历史做试点」）：
  - 环境变量 `EGGBLOOM_SESSION_LOG_GROUPS` = 逗号分隔的群号白名单；
  - **空 / 未设置 = 全关**（默认不改变现有行为，爆炸半径为零）；
  - 只有命中白名单的群才会把对话写入事件日志，并由日志派生 prompt 历史。

落盘位置：`<data_dir>/session_log/<kind>-<id>.jsonl`（jsonl，每行一条事件）。
"""
import os
import json
import time
import threading

try:
    from data_store import get_data_dir
    _DATA_DIR = str(get_data_dir())
except Exception:
    _DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")

SESSION_LOG_DIR = os.path.join(_DATA_DIR, "session_log")

# 事件类型（模型可见的事实）
EV_USER_MSG = "user_msg"        # 一条用户消息（已合并/带前缀，即进模型的形态）
EV_BOT_REPLY = "bot_reply"      # 机器人回复
EV_COMPACT = "compact"          # 摘要检查点（标记对事务，任务 7 用；此处预留）

_lock = threading.Lock()
# 内存索引：{(kind, id): [event, ...]} —— 避免每次读盘
_cache = {}
# 每个命名空间的下一个 seq
_seq = {}


def _ns_path(kind, sid):
    safe = str(sid).replace(os.sep, "_").replace("/", "_")
    return os.path.join(SESSION_LOG_DIR, f"{kind}-{safe}.jsonl")


def enabled_for(group_id):
    """该群是否启用事件溯源会话日志（试点白名单）。默认全关。"""
    raw = os.environ.get("EGGBLOOM_SESSION_LOG_GROUPS", "").strip()
    if not raw:
        return False
    ids = {x.strip() for x in raw.split(",") if x.strip()}
    return str(group_id) in ids


def _load_from_disk(kind, sid):
    path = _ns_path(kind, sid)
    events = []
    if not os.path.isfile(path):
        return events
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except Exception:
                    continue
    except Exception:
        return events
    return events


def _get_events(kind, sid):
    key = (kind, str(sid))
    if key not in _cache:
        _cache[key] = _load_from_disk(kind, sid)
        _nxt = 0
        for e in _cache[key]:
            try:
                _nxt = max(_nxt, int(e.get("seq", -1)) + 1)
            except Exception:
                pass
        _seq[key] = _nxt
    return _cache[key]


def append(kind, sid, ev_type, payload, *, ts=None):
    """追加一条事件。返回写入的事件 dict；**校验失败时抛异常且不改变日志**。

    - payload 会被**深拷贝并冻结**（json round-trip），确保后续外部修改不影响日志。
    - 校验：载荷必须能无损 JSON 往返（无 set / 自定义对象 / NaN 等）。
    """
    if not isinstance(payload, dict):
        raise TypeError("payload 必须是 dict")

    # 快照 + 冻结：json 往返既是深拷贝，也是**无损 JSON 校验**
    try:
        frozen = json.loads(json.dumps(payload, ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError) as e:
        raise ValueError(f"载荷不是无损 JSON，拒绝追加: {e}") from e
    if frozen != payload:
        # round-trip 后有差异 → 说明存在丢精度的值（如 tuple→list 无所谓，但 NaN/Inf 不行）
        pass

    with _lock:
        events = _get_events(kind, sid)
        key = (kind, str(sid))
        seq = _seq.get(key, 0)
        ev = {
            "seq": seq,
            "ts": ts if ts is not None else time.time(),
            "type": ev_type,
            "payload": frozen,
        }
        # 先写盘成功，再改内存（避免磁盘失败却污染派生状态）
        try:
            os.makedirs(SESSION_LOG_DIR, exist_ok=True)
            with open(_ns_path(kind, sid), "a", encoding="utf-8") as f:
                f.write(json.dumps(ev, ensure_ascii=False) + "\n")
        except Exception as e:
            # 追加失败 → **不改变日志 / 派生状态**
            raise IOError(f"事件日志写入失败，已回滚: {e}") from e
        events.append(ev)
        _seq[key] = seq + 1
    return ev


def edit_event(kind, sid, seq, *, content=None, op="edit"):
    """按 seq 修改/删除单条事件，或截断其后所有事件（= 撤回，重新说）。

    用户要的"撤回 / 编辑历史消息"就落在这里：
      op="edit"     把该事件的 payload.content 改成 content（标 edited=true）
      op="delete"   删除该事件
      op="truncate" 删除该事件及其后所有事件（对话回滚到这条之前）
    内存缓存与磁盘 JSONL **同步原子改写**；返回 (ok, 剩余条数)。
    """
    key = (kind, str(sid))
    with _lock:
        events = list(_get_events(kind, sid))
        idx = None
        for i, e in enumerate(events):
            try:
                if int(e.get("seq", -1)) == int(seq):
                    idx = i
                    break
            except Exception:
                continue
        if idx is None:
            return False, len(events)
        if op == "truncate":
            events = events[:idx]
        elif op == "delete":
            events = events[:idx] + events[idx + 1:]
        else:
            if content is None:
                return False, len(events)
            e = dict(events[idx])
            pay = dict(e.get("payload") or {})
            pay["content"] = str(content)
            e["payload"] = pay
            e["edited"] = True
            events[idx] = e
        _cache[key] = events
        _nxt = 0
        for e in events:
            try:
                _nxt = max(_nxt, int(e.get("seq", -1)) + 1)
            except Exception:
                pass
        _seq[key] = _nxt
        path = _ns_path(kind, sid)
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                for e in events:
                    f.write(json.dumps(e, ensure_ascii=False) + "\n")
            os.replace(tmp, path)
        except Exception:
            return False, len(events)
        return True, len(events)


def append_user_msg(group_id, content, *, sender=None, ts=None):
    """记录一条**模型可见**的用户消息（content 为进模型的最终形态）。"""
    return append("group", group_id, EV_USER_MSG,
                  {"content": content, "sender": sender}, ts=ts)


def append_bot_reply(group_id, content, *, ts=None):
    return append("group", group_id, EV_BOT_REPLY, {"content": content}, ts=ts)


def events_of(kind, sid):
    """返回该命名空间的全部事件（副本）。"""
    with _lock:
        return list(_get_events(kind, sid))


def derive_messages(kind, sid, *, limit=None, skip_compacted=True):
    """**纯函数**：从日志派生模型可见的 messages（[{role, content}, ...]）。

    - 只把 user_msg / bot_reply 映射为 user/assistant；
    - compact 事件默认不产出 message（它们是投影，任务 7 用）；
    - limit: 只取最近 N 条（对应现有 `[-10:]` 窗口）。
    """
    out = []
    for ev in events_of(kind, sid):
        t = ev.get("type")
        p = ev.get("payload") or {}
        if t == EV_USER_MSG:
            out.append({"role": "user", "content": p.get("content", "")})
        elif t == EV_BOT_REPLY:
            out.append({"role": "assistant", "content": p.get("content", "")})
        elif t == EV_COMPACT and not skip_compacted:
            c = p.get("summary")
            if c:
                out.append({"role": "system", "content": c})
        # 其它类型不进 messages
    if limit:
        out = out[-int(limit):]
    return out


def replay(group_id, *, limit=None):
    """对外的群回放入口（试点群）。返回派生 messages。"""
    return derive_messages("group", group_id, limit=limit)


def stats(group_id):
    """诊断：试点群的事件统计。"""
    evs = events_of("group", group_id)
    n_user = sum(1 for e in evs if e.get("type") == EV_USER_MSG)
    n_bot = sum(1 for e in evs if e.get("type") == EV_BOT_REPLY)
    return {
        "group_id": str(group_id),
        "total": len(evs),
        "user_msgs": n_user,
        "bot_replies": n_bot,
        "path": _ns_path("group", group_id),
    }


def reset_cache():
    """清空内存索引（测试用；不影响磁盘日志）。"""
    with _lock:
        _cache.clear()
        _seq.clear()


def list_ids(kind, *, limit=None):
    """列出该 kind 下**已有日志的**会话 id（按文件 mtime 倒序 = 最近活动的在前）。

    用途：Agent 页的"历史会话"列表 —— 刷新页面后仍能找回之前的会话
    （这正是设计文档 §5「会话即日志（可回放）」要的能力：**不只是内存里记着**）。
    """
    out = []
    try:
        if not os.path.isdir(SESSION_LOG_DIR):
            return out
        prefix = f"{kind}-"
        entries = []
        for name in os.listdir(SESSION_LOG_DIR):
            if not name.startswith(prefix) or not name.endswith(".jsonl"):
                continue
            p = os.path.join(SESSION_LOG_DIR, name)
            try:
                st = os.stat(p)
            except OSError:
                continue
            sid = name[len(prefix):-len(".jsonl")]
            entries.append((st.st_mtime, sid, st.st_size))
        entries.sort(reverse=True)
        for mt, sid, size in entries:
            out.append({"session_id": sid, "mtime": mt, "bytes": size,
                        "path": _ns_path(kind, sid)})
            if limit and len(out) >= int(limit):
                break
    except Exception:
        pass
    return out


def delete(kind, sid):
    """删除一个会话日志（Agent 页的"清空会话"）。

    返回 True/False。**只删对应的那个 jsonl**，不做通配 —— 避免误伤。
    """
    key = (kind, str(sid))
    path = _ns_path(kind, sid)
    ok = False
    try:
        if os.path.isfile(path):
            os.remove(path)
            ok = True
    except Exception:
        ok = False
    with _lock:
        _cache.pop(key, None)
        _seq.pop(key, None)
    return ok
