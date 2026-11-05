# -*- coding: utf-8 -*-
"""harness/filesafe.py —— 文件管理场景的安全层（1.6.0 P2 第一刀）

## 为什么单独一层

`tool_fs.py` 只有 read / write / edit / glob / grep —— 全是"不破坏东西"的操作。
一旦要给 agent 删文件、移动文件（P2 验收：把文件整理到 D 盘），红线就到了：
**强制人工确认 + 备份 + 回收站**，一条都不能省。

把这三条写进调用方是守不住的 —— 每个调用点都得记得，总有一个会忘。
所以做成"先申请、再确认、后执行"的两段式：

    prepare()  → 只做检查与计划，返回 token 与将要发生什么
    confirm()  → 凭 token 执行；没有 token 或 token 过期，一律拒绝

## 三条红线的具体做法

① **人工确认**：破坏性操作（move / delete）**必须**先 prepare 拿 token；
   token 300 秒过期、一次性（用完即删）。调用方拿不到"直接删"的入口。
② **备份**：move / delete 之前，先把原件复制到 `data/filesafe/backups/<ts>/`。
   备份失败就不执行 —— 宁可不做，也不留"删了但没备份"的状态。
③ **回收站**：delete **永不** `os.unlink`，一律 move 到 `data/filesafe/recycle/<yyyymmdd>/`，
   并往 `recycle.jsonl` 追加一条（原件路径 / 回收站路径 / 时间），可回溯、可恢复。

## 边界

路径不设白名单时**不限制位置**（用户确实可能整理到 D 盘），但会：
  · 规范化后若指向不允许的根（调用方传了 allowed_roots 时）直接拒绝；
  · 覆盖已存在的目标文件时，先把目标也备份一份 —— 被覆盖的东西同样是"丢失"。
"""
from __future__ import annotations

import io
import json
import os
import re
import shutil
import threading
import time
import uuid

#: token 有效期（秒）。太短人来不及点，太长等于没有确认
TOKEN_TTL = 300.0

#: 破坏性操作：必须走 prepare/confirm
DESTRUCTIVE = ("move", "delete")

_LOCK = threading.Lock()
_PENDING: dict = {}          # token -> {op, src, dst, created, expires, allowed_roots}
#: 最近用过的 token（有界）。只为把「执行过」和「传错」区分开，不参与鉴权。
_USED: dict = {}
_USED_MAX = 256
_RECYCLE_LOCK = threading.Lock()


def _now(now=None):
    return float(now) if now is not None else time.time()


def _day_dir(root, ts):
    import datetime
    return datetime.datetime.fromtimestamp(ts).strftime("%Y%m%d")


def _norm(p):
    return os.path.abspath(os.path.expanduser(str(p or "")))


def _inside(path, roots):
    """path 是否在任一 root 之内（roots 为空 = 不限制）。"""
    if not roots:
        return True
    p = os.path.normcase(os.path.normpath(path))
    for r in roots:
        rn = os.path.normcase(os.path.normpath(_norm(r)))
        if p == rn or p.startswith(rn + os.sep):
            return True
    return False


class FileSafeError(ValueError):
    """不可执行（拒绝时抛，消息里带原因）。"""


def _base_dir(base_dir=None) -> str:
    if base_dir:
        return _norm(base_dir)
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "data", "filesafe")


def _backup(path, base_dir, now):
    """把 path 复制到备份区；返回备份路径（失败抛异常 —— 备份不成就不许动原件）。"""
    d = os.path.join(_base_dir(base_dir), "backups", _day_dir(base_dir, now))
    os.makedirs(d, exist_ok=True)
    name = os.path.basename(path.rstrip("\\/")) or "item"
    dest = os.path.join(d, "%d_%s" % (int(now), name))
    if os.path.isdir(path):
        shutil.copytree(path, dest, dirs_exist_ok=True)
    else:
        shutil.copy2(path, dest)
    return dest


def _recycle(path, base_dir, now, extra=None):
    """删除 = 移进回收站 + 记一条账。永不 unlink。"""
    d = os.path.join(_base_dir(base_dir), "recycle", _day_dir(base_dir, now))
    os.makedirs(d, exist_ok=True)
    name = os.path.basename(path.rstrip("\\/")) or "item"
    dest = os.path.join(d, "%d_%s" % (int(now), name))
    n = 1
    while os.path.exists(dest):
        dest = os.path.join(d, "%d_%d_%s" % (int(now), n, name))
        n += 1
    shutil.move(path, dest)
    rec = {"ts": now, "src": path, "recycle": dest}
    if extra:
        rec.update(extra)
    with _RECYCLE_LOCK:
        log = os.path.join(_base_dir(base_dir), "recycle.jsonl")
        os.makedirs(os.path.dirname(log), exist_ok=True)
        with io.open(log, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return dest


# ── 两段式 ──────────────────────────────────────────────────
def prepare(op, src, dst=None, *, allowed_roots=None, base_dir=None, now=None,
            ttl=TOKEN_TTL) -> dict:
    """检查并生成待确认操作。返回 {token, op, src, dst, summary, expires}。

    只做检查，不动任何文件 —— 这一步失败就不该有 token。
    """
    op = str(op or "").strip().lower()
    if op not in ("move", "copy", "delete"):
        raise FileSafeError(f"不支持的操作: {op}")
    src_p = _norm(src)
    if not os.path.exists(src_p):
        raise FileSafeError(f"源路径不存在: {src_p}")
    roots = list(allowed_roots or [])
    if not _inside(src_p, roots):
        raise FileSafeError(f"源路径不在允许范围内: {src_p}")

    dst_p = None
    if op in ("move", "copy"):
        if not str(dst or "").strip():
            raise FileSafeError("move/copy 必须给目标路径")
        dst_p = _norm(dst)
        if not _inside(dst_p, roots):
            raise FileSafeError(f"目标路径不在允许范围内: {dst_p}")
        if os.path.normcase(src_p) == os.path.normcase(dst_p):
            raise FileSafeError("源和目标是同一个路径")
        if not os.path.isdir(os.path.dirname(dst_p)):
            raise FileSafeError(f"目标目录不存在（不会替你造目录）: {os.path.dirname(dst_p)}")

    ts = _now(now)
    token = uuid.uuid4().hex[:16]
    will_overwrite = bool(dst_p and os.path.exists(dst_p))
    summary = {
        "move": f"移动 {src_p} → {dst_p}" + ("（目标已存在，会被覆盖；覆盖前备份）" if will_overwrite else ""),
        "copy": f"复制 {src_p} → {dst_p}" + ("（目标已存在，会被覆盖）" if will_overwrite else ""),
        "delete": f"删除 {src_p}（进回收站，可恢复）",
    }[op]
    rec = {"token": token, "op": op, "src": src_p, "dst": dst_p,
           "created": ts, "expires": ts + float(ttl or TOKEN_TTL),
           "allowed_roots": roots, "base_dir": base_dir,
           "will_overwrite": will_overwrite}
    with _LOCK:
        _PENDING[token] = rec
    return {"token": token, "op": op, "src": src_p, "dst": dst_p,
            "summary": summary, "expires": rec["expires"],
            "requires_confirm": op in DESTRUCTIVE,
            "will_overwrite": will_overwrite}


def cancel(token) -> bool:
    with _LOCK:
        return _PENDING.pop(str(token or ""), None) is not None


def pending() -> list:
    with _LOCK:
        return [dict(v) for v in _PENDING.values()]


def _priv_move(src, dst):
    """通过高权限助手移动文件。助手不可用 → None（调用方照旧抛原异常）。"""
    try:
        from harness import privclient as _pc
    except Exception:
        try:
            import privclient as _pc
        except Exception:
            return None
    try:
        return _pc.move(src, dst)
    except Exception:
        return None


def confirm(token, *, now=None) -> dict:
    """凭 token 执行。没 token / 过期 / 源已消失 → 拒绝，**不执行任何写操作**。"""
    token = str(token or "").strip()
    with _LOCK:
        rec = _PENDING.get(token)
        if not rec:
            if token in _USED:
                raise FileSafeError("这个操作已经执行过了（token 一次性）")
            raise FileSafeError("没有这个待确认操作（token 无效）")
    ts = _now(now)
    if ts > float(rec.get("expires") or 0):
        with _LOCK:
            _PENDING.pop(token, None)
        raise FileSafeError("确认已过期，请重新发起")
    # 一次性：先摘掉，避免并发重复执行
    with _LOCK:
        rec = _PENDING.pop(token, None)
    if not rec:
        raise FileSafeError("这个操作已经被执行过了")

    op, src_p, dst_p = rec["op"], rec["src"], rec.get("dst")
    base_dir = rec.get("base_dir")
    if not os.path.exists(src_p):
        raise FileSafeError(f"源路径已经不存在了: {src_p}")

    out = {"ok": True, "op": op, "src": src_p, "dst": dst_p, "backup": "", "recycle": ""}
    try:
        if op in DESTRUCTIVE:
            out["backup"] = _backup(src_p, base_dir, ts)      # 备份失败 → 抛异常，不继续
        if op == "move":
            if dst_p and os.path.exists(dst_p):
                _backup(dst_p, base_dir, ts)                  # 目标被覆盖也要能恢复
            try:
                shutil.move(src_p, dst_p)
            except PermissionError:
                # ★ 2026-11-05：本进程被系统限制（EggBloom 是 Medium）时，
                #   交给高权限助手做；助手不在就照旧抛出去（不假装成功）。
                _r = _priv_move(src_p, dst_p)
                if not (_r and _r.get("ok")):
                    raise
        elif op == "copy":
            if os.path.isdir(src_p):
                shutil.copytree(src_p, dst_p, dirs_exist_ok=True)
            else:
                shutil.copy2(src_p, dst_p)
        elif op == "delete":
            out["recycle"] = _recycle(src_p, base_dir, ts, extra={"token": token})
        with _LOCK:
            _USED[token] = ts
            if len(_USED) > _USED_MAX:
                for k in sorted(_USED, key=lambda x: _USED[x])[:len(_USED) - _USED_MAX]:
                    _USED.pop(k, None)
    except Exception as e:
        out.update(ok=False, error=f"{type(e).__name__}: {e}")
        raise FileSafeError(f"执行失败: {e}")
    return out


def restore(recycle_path, dst, *, allowed_roots=None, base_dir=None) -> dict:
    """从回收站恢复。恢复同样要有明确目标，不做"猜它原来在哪"。"""
    src_p, dst_p = _norm(recycle_path), _norm(dst)
    roots = list(allowed_roots or [])
    if not _inside(dst_p, roots):
        raise FileSafeError(f"目标路径不在允许范围内: {dst_p}")
    if not os.path.exists(src_p):
        raise FileSafeError(f"回收站里没有这个文件: {src_p}")
    if os.path.exists(dst_p):
        raise FileSafeError(f"目标已存在，不覆盖: {dst_p}")
    os.makedirs(os.path.dirname(dst_p) or ".", exist_ok=True)
    shutil.move(src_p, dst_p)
    return {"ok": True, "from": src_p, "to": dst_p}


__all__ = ["prepare", "confirm", "cancel", "pending", "restore",
           "FileSafeError", "TOKEN_TTL", "DESTRUCTIVE"]
