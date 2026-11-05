# -*- coding: utf-8 -*-
"""高权限助手（privhelper）—— 给 EggBloom 补上它自己拿不到的权限。

## 为什么需要（真机 2026-11-05）

· 这台机器 UAC 关闭（EnableLUA=0），桌面启动本应是完整令牌
· 但 EggBloom 是 Electron，**自己会降权**：父进程 High，它自己 Medium
· 它的协议拉起、文件操作、系统命令因此全卡在 Medium

## 做法

由「最高权限 / SYSTEM」计划任务拉起的小服务，不经过 Electron，不降权：

    GET  /health                           → {"ok":true,"elevated":true,...}
    POST /rpc   {"op":"fs.move","args":{}} Header: X-Token
                  ops: ping / fs.move / fs.delete / fs.mkdir / spawn

安全边界：
  · 监听 127.0.0.1，令牌 48 位十六进制，存在 data/privhelper.token
  · 每个路径操作都要在允许的根下（默认：用户目录 + 所有固定盘根，可用
    EGGBLOOM_PRIVHELPER_ROOTS 覆盖）
  · 不提供任意 shell；`spawn` 只接受具体 exe（不做命令拼接）
"""
import hmac
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.path.join(ROOT, "data")
TOKEN_FILE = os.path.join(DATA, "privhelper.token")
LOG_FILE = os.path.join(DATA, "privhelper.log")
PORT = int(os.environ.get("EGGBLOOM_PRIVHELPER_PORT", "3007"))

_lock = threading.Lock()


def log(msg):
    line = "[%s] %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    try:
        os.makedirs(DATA, exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def is_elevated():
    try:
        import ctypes
        from ctypes import wintypes
        adv, k32 = ctypes.windll.advapi32, ctypes.windll.kernel32
        h = wintypes.HANDLE()
        if not adv.OpenProcessToken(k32.GetCurrentProcess(), 0x0008, ctypes.byref(h)):
            return False
        buf, need = ctypes.c_ulong(0), ctypes.c_ulong(0)
        ok = adv.GetTokenInformation(h, 20, ctypes.byref(buf),
                                     ctypes.sizeof(buf), ctypes.byref(need))
        k32.CloseHandle(h)
        return bool(ok and buf.value)
    except Exception:
        return False


def ensure_token():
    try:
        with open(TOKEN_FILE, encoding="utf-8") as f:
            tok = f.read().strip()
        if tok:
            return tok
    except Exception:
        pass
    import secrets
    tok = secrets.token_hex(24)
    os.makedirs(DATA, exist_ok=True)
    with open(TOKEN_FILE, "w", encoding="utf-8") as f:
        f.write(tok)
    return tok


def _norm(p):
    return os.path.abspath(os.path.expandvars(os.path.expanduser(str(p or ""))))


def allowed_roots():
    env = os.environ.get("EGGBLOOM_PRIVHELPER_ROOTS", "").strip()
    if env:
        return [_norm(x) for x in env.split(";") if x.strip()]
    roots = [os.path.expanduser("~")]
    try:
        import string
        for d in string.ascii_uppercase:
            p = d + ":\\"
            if os.path.exists(p):
                roots.append(p)
    except Exception:
        pass
    return roots


def within(path):
    p = _norm(path).lower()
    for r in allowed_roots():
        rl = _norm(r).lower().rstrip("\\")
        if p == rl or p.startswith(rl + "\\"):
            return True
    return False


def _win_spawn_in_session(exe, argv, cwd=None):
    """在「活动控制台会话」里以该会话用户身份启动进程。返回 pid。

    只能在 SYSTEM 下用（WTSQueryUserToken 需要 SeTcbPrivilege）。
    """
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    adv = ctypes.WinDLL("advapi32", use_last_error=True)
    userenv = ctypes.WinDLL("userenv", use_last_error=True)
    wts = ctypes.WinDLL("wtsapi32", use_last_error=True)

    k32.WTSGetActiveConsoleSessionId.restype = wintypes.DWORD
    sid = k32.WTSGetActiveConsoleSessionId()
    if sid in (0xFFFFFFFF, 0):
        return {"ok": False, "error": "没有活动的交互会话（是不是没人登录？）"}

    h_token = wintypes.HANDLE()
    if not wts.WTSQueryUserToken(sid, ctypes.byref(h_token)):
        return {"ok": False, "error": "WTSQueryUserToken 失败: %s"
                % ctypes.get_last_error()}

    MAXIMUM_ALLOWED = 0x02000000
    SecurityImpersonation = 2
    TokenPrimary = 1
    h_dup = wintypes.HANDLE()
    if not adv.DuplicateTokenEx(h_token, MAXIMUM_ALLOWED, None,
                                SecurityImpersonation, TokenPrimary,
                                ctypes.byref(h_dup)):
        k32.CloseHandle(h_token)
        return {"ok": False, "error": "DuplicateTokenEx 失败: %s"
                % ctypes.get_last_error()}

    env = ctypes.c_void_p()
    CREATE_UNICODE_ENVIRONMENT = 0x00000400
    if not userenv.CreateEnvironmentBlock(ctypes.byref(env), h_dup, False):
        env = ctypes.c_void_p()

    class STARTUPINFOW(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD), ("lpReserved", wintypes.LPWSTR),
            ("lpDesktop", wintypes.LPWSTR), ("lpTitle", wintypes.LPWSTR),
            ("dwX", wintypes.DWORD), ("dwY", wintypes.DWORD),
            ("dwXSize", wintypes.DWORD), ("dwYSize", wintypes.DWORD),
            ("dwXCountChars", wintypes.DWORD), ("dwYCountChars", wintypes.DWORD),
            ("dwFillAttribute", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
            ("wShowWindow", wintypes.WORD), ("cbReserved2", wintypes.WORD),
            ("lpReserved2", ctypes.c_void_p), ("hStdInput", wintypes.HANDLE),
            ("hStdOutput", wintypes.HANDLE), ("hStdError", wintypes.HANDLE),
        ]

    class PROCESS_INFORMATION(ctypes.Structure):
        _fields_ = [("hProcess", wintypes.HANDLE), ("hThread", wintypes.HANDLE),
                    ("dwProcessId", wintypes.DWORD), ("dwThreadId", wintypes.DWORD)]

    si = STARTUPINFOW()
    si.cb = ctypes.sizeof(si)
    si.lpDesktop = "winsta0\\default"
    pi = PROCESS_INFORMATION()
    cmdline = subprocess.list2cmdline([exe] + [str(a) for a in (argv or [])])
    ok = adv.CreateProcessAsUserW(
        h_dup, None, ctypes.c_wchar_p(cmdline), None, None, False,
        CREATE_UNICODE_ENVIRONMENT, env, cwd, ctypes.byref(si), ctypes.byref(pi))
    err = ctypes.get_last_error() if not ok else 0

    if env:
        try:
            userenv.DestroyEnvironmentBlock(env)
        except Exception:
            pass
    k32.CloseHandle(h_dup)
    k32.CloseHandle(h_token)
    if not ok:
        return {"ok": False, "error": "CreateProcessAsUserW 失败: %s" % err}
    k32.CloseHandle(pi.hThread)
    k32.CloseHandle(pi.hProcess)
    return {"ok": True, "pid": pi.dwProcessId, "session": int(sid)}


def op_ping(args):
    return {"ok": True, "recipe": "privhelper/1"}


def op_fs_move(args):
    src, dst = _norm(args.get("src")), _norm(args.get("dst"))
    if not within(src) or not within(dst):
        return {"ok": False, "error": "路径不在允许范围内"}
    if not os.path.exists(src):
        return {"ok": False, "error": "源不存在: %s" % src}
    parent = os.path.dirname(dst)
    if parent:
        os.makedirs(parent, exist_ok=True)
    shutil.move(src, dst)
    return {"ok": True, "src": src, "dst": dst}


def op_fs_delete(args):
    p = _norm(args.get("path") or args.get("src"))
    if not within(p):
        return {"ok": False, "error": "路径不在允许范围内"}
    if not os.path.exists(p):
        return {"ok": False, "error": "不存在: %s" % p}
    if os.path.isdir(p):
        shutil.rmtree(p)
    else:
        os.remove(p)
    return {"ok": True, "path": p}


def op_fs_mkdir(args):
    p = _norm(args.get("path"))
    if not within(p):
        return {"ok": False, "error": "路径不在允许范围内"}
    os.makedirs(p, exist_ok=True)
    return {"ok": True, "path": p}


def op_spawn(args):
    """在助手自己的会话（session 0）启动。GUI/注入类不要用这个。"""
    exe = str(args.get("exe") or "").strip()
    argv = [str(a) for a in (args.get("args") or [])]
    cwd = args.get("cwd") or (os.path.dirname(exe) if exe else None)
    if not exe or not os.path.isfile(exe):
        return {"ok": False, "error": "exe 不存在: %s" % exe}
    p = subprocess.Popen([exe] + argv, cwd=cwd, close_fds=True,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return {"ok": True, "pid": p.pid}


def op_spawn_session(args):
    """★ 在用户会话里启动（能注入 QQ、能出窗口）。协议拉起走这条。"""
    exe = str(args.get("exe") or "").strip()
    argv = [str(a) for a in (args.get("args") or [])]
    cwd = args.get("cwd") or (os.path.dirname(exe) if exe else None)
    if not exe or not os.path.isfile(exe):
        return {"ok": False, "error": "exe 不存在: %s" % exe}
    if os.name != "nt":
        return {"ok": False, "error": "仅 Windows 支持"}
    return _win_spawn_in_session(exe, argv, cwd)


OPS = {"ping": op_ping, "fs.move": op_fs_move, "fs.delete": op_fs_delete,
       "fs.mkdir": op_fs_mkdir, "spawn": op_spawn,
       "spawn_session": op_spawn_session}


class Handler(BaseHTTPRequestHandler):
    server_version = "EggBloomPrivHelper/1"

    def log_message(self, fmt, *a):
        return

    def _json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def do_GET(self):
        if self.path.split("?")[0] == "/health":
            self._json(200, {"ok": True, "elevated": is_elevated(),
                             "pid": os.getpid(), "port": PORT,
                             "ops": sorted(OPS)})
        else:
            self._json(404, {"ok": False, "error": "not found"})

    def _token_ok(self):
        want = ensure_token()
        got = self.headers.get("X-Token", "")
        return bool(want) and hmac.compare_digest(got, want)

    def do_POST(self):
        if self.path.split("?")[0] != "/rpc":
            self._json(404, {"ok": False, "error": "not found"})
            return
        if not self._token_ok():
            self._json(403, {"ok": False, "error": "bad token"})
            return
        try:
            n = int(self.headers.get("Content-Length", "0") or 0)
            raw = self.rfile.read(min(n, 1 << 20)) if n else b"{}"
            req = json.loads((raw.decode("utf-8", "ignore") or "{}"))
        except Exception as e:
            self._json(400, {"ok": False, "error": "bad json: %s" % e})
            return
        op = str(req.get("op") or "")
        fn = OPS.get(op)
        if not fn:
            self._json(400, {"ok": False, "error": "未知操作: %s" % op})
            return
        try:
            with _lock:
                res = fn(req.get("args") or {}) or {"ok": True}
        except Exception as e:
            res = {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
        log("%s -> %s" % (op, "ok" if res.get("ok") else res.get("error")))
        self._json(200, res)


def serve(port=None):
    ensure_token()
    srv = ThreadingHTTPServer(("127.0.0.1", int(port or PORT)), Handler)
    return srv


def main():
    tok = ensure_token()
    log("privhelper 启动 pid=%s port=%s elevated=%s" % (os.getpid(), PORT, is_elevated()))
    print("privhelper 已启动 pid=%s port=%s elevated=%s" % (os.getpid(), PORT, is_elevated()))
    print("token 文件: %s" % TOKEN_FILE)
    try:
        serve().serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    sys.exit(main())
