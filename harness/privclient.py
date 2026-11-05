# -*- coding: utf-8 -*-
"""高权限助手的客户端：EggBloom 这边怎么用它。

设计原则：
  · **不可用就退回本地**。助手没装/没跑时，一切照旧（不让功能因为助手缺席而瘫痪）。
  · 只在"需要额外权限"的地方尝试：文件移动/删除/建目录、启动进程。
  · 失败不抛给模型看——返回 ok=False + 一句人话，由上层决定怎么说。
"""
import json
import os
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TOKEN_FILE = os.path.join(ROOT, "data", "privhelper.token")
HOST = os.environ.get("EGGBLOOM_PRIVHELPER_HOST", "127.0.0.1")
PORT = int(os.environ.get("EGGBLOOM_PRIVHELPER_PORT", "3007"))
TIMEOUT = 15


def _token():
    try:
        with open(TOKEN_FILE, encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return ""


def health(timeout=1.5):
    """助手在不在？返回 dict 或 None。"""
    try:
        with urllib.request.urlopen("http://%s:%d/health" % (HOST, PORT),
                                    timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "ignore"))
    except Exception:
        return None


def available():
    h = health()
    return bool(h and h.get("ok"))


def elevated():
    h = health() or {}
    return bool(h.get("elevated"))


def call(op, args=None, timeout=TIMEOUT):
    """调一个操作。返回 {"ok": bool, ...}；助手不可用时返回 None（让调用方退回本地）。"""
    tok = _token()
    if not tok:
        return None
    body = json.dumps({"op": op, "args": args or {}}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        "http://%s:%d/rpc" % (HOST, PORT), data=body, method="POST",
        headers={"Content-Type": "application/json", "X-Token": tok})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "ignore"))
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode("utf-8", "ignore"))
        except Exception:
            return {"ok": False, "error": "HTTP %s" % e.code}
    except Exception:
        return None


def move(src, dst):
    return call("fs.move", {"src": src, "dst": dst})


def delete(path):
    return call("fs.delete", {"path": path})


def mkdir(path):
    return call("fs.mkdir", {"path": path})


def spawn(exe, args=None, cwd=None):
    """在助手自己的会话（session 0）启动 —— GUI/注入类别用这个。"""
    return call("spawn", {"exe": exe, "args": args or [], "cwd": cwd})


def spawn_session(exe, args=None, cwd=None):
    """★ 在**用户会话**里以用户身份启动（能注入 QQ、能出窗口）。

    助手是 SYSTEM，有 SeTcbPrivilege；它取交互会话的令牌再 CreateProcessAsUser，
    所以进程的会话=登录会话、完整性跟随用户令牌。协议拉起走这条。
    """
    return call("spawn_session", {"exe": exe, "args": args or [], "cwd": cwd}, timeout=30)


__all__ = ["health", "available", "elevated", "call",
           "move", "delete", "mkdir", "spawn", "spawn_session", "TOKEN_FILE"]
