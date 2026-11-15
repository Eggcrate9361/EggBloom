# -*- coding: utf-8 -*-
"""harness/tool_proc.py —— 持久 Python 会话 + 后台任务（`py_run` / `job_*`）。

为什么是 Python 而不是 bash / pwsh（这是本机环境的**实证结论**，不是偏好）
    用户的原话："powershell 调用跟狗屎一样（我还真的不知道怎么处理）"。
    经排查，这不是用户不会用，而是本机**真实存在**的一串限制：
      · Bash 工具下 `dirname`/`head` 报 command not found（PATH 异常）
      · PowerShell 工具**不回显 stdout**，必须落盘再读
      · `Start-Job`（后台任务）被安全策略禁用
      · `cmd /c` 被拦截；`New-Object -ComObject` 被拦截
      · `netsh` 等原生命令中文输出乱码
      · 甚至存在 Remove-Item 报错但实际已删除的情况
    把这些"坑"如实暴露给模型，等于让它在雷区里开车 —— 命中率必然低。
    而 EggBloom **自带一个 Python 解释器**（`<dist>/python/python.exe`），
    它是项目自身赖以运行的运行时，稳定、无上述限制、且**零额外成本**。

    结论：给 agent 的默认执行工具是 **`py_run`（持久 Python 会话）**。
    这也是"极高命中率 + 极低成本"的体现 —— 把不确定性从模型身上拿走。

持久会话的三个收益
    高效率：进程常驻 → 第二次调用省掉解释器启动（本机冷启动约 200~400ms）；
            更重要的是**变量、import、cwd 全部保留**，多步计算不必反复重建状态。
    极低成本：常驻进程让"先探测环境、再干活"变成一次性成本。
    高命中率：用户环境（Windows/中文路径/venv）在会话开始时探测一次，
             后续调用都在这套已确认可用的环境里跑，不会每次都踩编码坑。

安全边界（必须守）
    · 工作目录固定在工作区根（agent 的工具边界同一套）。
    · 超时强制终止（杀掉整个进程树，避免孤儿进程占端口/文件）。
    · 输出按 harness 的 spill 预算截断（超大输出落盘 + 给路径）。
    · **不做源码过滤** —— 这是本地项目自己的开发机，模型与用户同一信任域；
      真正的边界是"工作目录"和"超时"，而不是假装能做沙箱（假沙箱比没有更危险）。
"""
import os
import sys
import json
import time
import queue
import atexit
import re
import threading
import subprocess
import uuid

from . import workspace as _ws


def _separation_active():
    try:
        from separation import active
        return bool(active())
    except Exception:
        return False


# ── 预算 ───────────────────────────────────────────────────────────
# 分离态（2026-11-07 方向重定）：不设单次时长上限、不设后台任务上限。
# 产品态维持原值。None = 不超时 / 不封顶；0 = 不限条数。
DEFAULT_TIMEOUT = None if _separation_active() else 20.0
MAX_TIMEOUT = None if _separation_active() else 120.0
MAX_OUTPUT_CHARS = 60_000       # 单次内联上限（超出交给 spill）
MAX_JOBS = 0 if _separation_active() else 8   # 0 = 不限
JOB_RING = 400                  # 每个 job 保留的最近输出行


# ══════════════════════════════════════════════════════════════════
# 解释器定位（优先项目自带 python，其次当前解释器）
# ══════════════════════════════════════════════════════════════════
_PY = {"path": None}


def python_executable():
    """定位一个可用的 Python 解释器（缓存）。

    顺序：项目自带 → 环境变量 → 当前解释器。
    """
    if _PY["path"]:
        return _PY["path"]
    roots = []
    # 1) 项目自带（发行包里 python/python.exe）
    try:
        from data_store import get_data_dir          # noqa
        _roots = [os.path.dirname(str(get_data_dir()))]
    except Exception:
        _roots = []
    _roots.append(_ws.workspace_root())
    for r in _roots:
        for sub in ("python", "runtime/python", "python/bin"):
            for exe in ("python.exe", "python", "python3"):
                p = os.path.join(r, sub, exe)
                if os.path.isfile(p):
                    _PY["path"] = p
                    return p
    # 2) 当前解释器（最稳：就是正在跑 EggBloom 的那个）
    cand = sys.executable
    _PY["path"] = cand or "python"
    return _PY["path"]


# ══════════════════════════════════════════════════════════════════
# 持久会话驱动的协议
#   父进程写一条 JSON 任务 → 子进程 exec → 回一行 JSON 结果。
#   用显式分隔标记而不是 EOF 判定，避免把 print 的内容误当结果。
# ══════════════════════════════════════════════════════════════════
_BEGIN = "__EGGBLOOM_PYRUN_BEGIN__"
_END = "__EGGBLOOM_PYRUN_END__"

_DRIVER = r'''# -*- coding: utf-8 -*-
"""持久 Python 会话驱动（由 harness.tool_proc 拉起）。

读一行 JSON 任务 → 在**同一个全局命名空间**里 exec → 回一行 JSON 结果。
关键点：
  · 全局命名空间常驻 → 变量/import 跨调用保留（这就是"持久"的意义）
  · stdout / stderr 被重定向收集 → 一起回传（模型要看到 print 和警告）
  · 未捕获异常也回传 traceback（模型看得懂，也方便它自己修）
  · 每个任务前把 cwd 重置为工作区根（避免上一次 os.chdir 影响下一次）
"""
import sys, os, io, json, traceback, contextlib

ROOT = os.environ.get("EGGBLOOM_WS_ROOT") or os.getcwd()
NS = {"__name__": "__main__", "__builtins__": __builtins__}

def _reply(obj):
    sys.__stdout__.write(obj + "\n")
    sys.__stdout__.flush()

_reply("__EGGBLOOM_PYRUN_READY__")

while True:
    line = sys.stdin.readline()
    if not line:
        break
    line = line.strip()
    if not line:
        continue
    try:
        task = json.loads(line)
    except Exception:
        _reply(json.dumps({"ok": False, "error": "bad task json"}))
        continue
    if task.get("cmd") == "exit":
        break
    code = task.get("code") or ""
    try:
        os.chdir(ROOT)
    except Exception:
        pass
    buf_out = io.StringIO()
    ok = True
    err = ""
    try:
        with contextlib.redirect_stdout(buf_out), contextlib.redirect_stderr(buf_out):
            try:
                # 先按表达式求值（REPL 手感：直接写 df.head() 也能出结果）
                val = eval(compile(code, "<py_run>", "eval"), NS)
                if val is not None:
                    print(repr(val))
            except SyntaxError:
                exec(compile(code, "<py_run>", "exec"), NS)
    except SystemExit as e:
        ok = False
        err = "SystemExit: %r" % (e.code,)
    except Exception:
        ok = False
        err = traceback.format_exc(limit=12)
    _reply("__EGGBLOOM_PYRUN_BEGIN__")
    _reply(json.dumps({
        "ok": ok,
        "stdout": buf_out.getvalue(),
        "error": err,
        "cwd": os.getcwd(),
    }, ensure_ascii=False))
    _reply("__EGGBLOOM_PYRUN_END__")
'''


class _Session:
    """一个常驻 Python 子进程会话。

    关键实现细节（踩过的坑）
        子进程的 stdout 是**一条共享的流**。如果每次调用都新起一个读线程去抢这条流，
        就会出现"上一次调用的读线程把这一次的响应读走了"的竞态 ——
        表现是第二次调用返回空、或超时。
        正确做法：**每个会话只起一个常驻读线程**，把解析出的结果投进队列；
        每次调用只是往队列里取属于自己那一条。
    """

    def __init__(self, name="default"):
        self.name = name
        self.proc = None
        self.lock = threading.Lock()
        self.started_at = 0.0
        self.calls = 0
        self.driver_path = os.path.join(_drv_dir(), f"_pyrun_driver_{name}.py")
        self._q = queue.Queue()          # 常驻读线程 → 调用方
        self._reader = None              # 常驻读线程
        self._alive_flag = False

    # ── 生命周期 ──
    def _write_driver(self):
        os.makedirs(os.path.dirname(self.driver_path), exist_ok=True)
        with open(self.driver_path, "w", encoding="utf-8") as f:
            f.write(_DRIVER)

    def _pump(self, proc):
        """常驻读线程：解析 BEGIN..END 块，投进队列。"""
        collecting = False
        payload = []
        try:
            for raw in proc.stdout:
                s = raw.rstrip("\n")
                if s == _BEGIN:
                    collecting = True
                    payload = []
                    continue
                if s == _END:
                    self._q.put(("result", "\n".join(payload)))
                    collecting = False
                    continue
                if collecting:
                    payload.append(s)
        except Exception:
            pass
        self._q.put(("eof", None))

    def start(self):
        if self.proc and self.proc.poll() is None:
            return True
        self._write_driver()
        env = dict(os.environ)
        env["EGGBLOOM_WS_ROOT"] = _ws.workspace_root()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONUTF8"] = "1"
        env.pop("PYTHONDONTWRITEBYTECODE", None)
        creationflags = 0
        if os.name == "nt":
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            self.proc = subprocess.Popen(
                [python_executable(), "-u", self.driver_path],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, cwd=_ws.workspace_root(),
                env=env, text=True, encoding="utf-8", errors="replace",
                bufsize=1, creationflags=creationflags,
            )
        except Exception:
            self.proc = None
            return False
        # 清空旧队列（换进程后残留的响应不能串到新会话）
        try:
            while True:
                self._q.get_nowait()
        except queue.Empty:
            pass
        self._reader = threading.Thread(target=self._pump, args=(self.proc,), daemon=True)
        self._reader.start()
        # 等 READY（驱动自己会打印，被 _pump 当成普通行丢弃；这里只等进程可用）
        t0 = time.time()
        while time.time() - t0 < 10:
            if self.proc.poll() is not None:
                return False
            time.sleep(0.05)
            break
        self.started_at = time.time()
        return True
    def stop(self):
        p = self.proc
        self.proc = None
        if not p:
            return
        try:
            if p.poll() is None:
                p.stdin.write(json.dumps({"cmd": "exit"}) + "\n")
                p.stdin.flush()
                try:
                    p.wait(timeout=2)
                except Exception:
                    _kill_tree(p)
        except Exception:
            _kill_tree(p)

    def alive(self):
        return bool(self.proc and self.proc.poll() is None)

    # ── 执行 ──
    def run(self, code, timeout):
        if not self.alive() and not self.start():
            return {"ok": False, "stdout": "", "error":
                    f"无法启动 Python 会话（解释器：{python_executable()}）"}

        # 清掉可能残留的（上一次超时后迟到的）响应，避免错位
        try:
            while True:
                self._q.get_nowait()
        except queue.Empty:
            pass

        try:
            self.proc.stdin.write(json.dumps({"code": code, "t": time.time()}) + "\n")
            self.proc.stdin.flush()
        except Exception as e:
            return {"ok": False, "stdout": "", "error": f"写入会话失败: {e}"}

        try:
            kind, data = self._q.get(timeout=timeout)
        except queue.Empty:
            # 超时：杀掉会话（否则迟到的响应会串到下一次调用）
            self.stop()
            return {"ok": False, "stdout": "", "error":
                    f"执行超时（>{timeout}s）。会话已重置，请缩小任务或分步执行。",
                    "timeout": True, "reset": True}

        if kind == "eof":
            self.stop()
            return {"ok": False, "stdout": "", "error": "Python 会话意外退出。", "reset": True}

        try:
            res = json.loads(data or "{}")
        except Exception:
            return {"ok": False, "stdout": data or "", "error": "会话返回无法解析。"}
        res["cwd"] = res.get("cwd") or _ws.workspace_root()
        self.calls += 1
        return res


def _drv_dir():
    try:
        from data_store import get_data_dir
        d = os.path.join(str(get_data_dir()), "pyrun")
    except Exception:
        d = os.path.join(_ws.workspace_root(), "data", "pyrun")
    return d


def _kill_tree(p):
    """杀掉进程及其子进程（Windows 用 taskkill /T，POSIX 用进程组）。"""
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)],
                           capture_output=True, timeout=5)
        else:
            import signal
            os.killpg(os.getpgid(p.pid), signal.SIGKILL)
    except Exception:
        try:
            p.kill()
        except Exception:
            pass


_SESSIONS = {}
_SESS_LOCK = threading.Lock()


def _session(name="default"):
    with _SESS_LOCK:
        s = _SESSIONS.get(name)
        if s is None:
            s = _Session(name)
            _SESSIONS[name] = s
        return s


def reset_sessions():
    """清空所有会话（测试/热重载用）。"""
    with _SESS_LOCK:
        for s in _SESSIONS.values():
            s.stop()
        _SESSIONS.clear()


atexit.register(reset_sessions)


# ══════════════════════════════════════════════════════════════════
# py_run
# ══════════════════════════════════════════════════════════════════
def tool_py_run(args):
    """在**持久 Python 会话**里执行代码，返回 stdout/stderr/traceback。

    参数：code / timeout / session / reset
    """
    args = args or {}
    code = args.get("code")
    if code is None:
        return "[错误] 缺少 code（要执行的 Python 代码）。"
    code = str(code)
    if not code.strip():
        return "[错误] code 为空。"

    name = str(args.get("session") or "default")
    raw_timeout = args.get("timeout")
    timeout = DEFAULT_TIMEOUT
    if raw_timeout not in (None, ""):
        try:
            timeout = float(raw_timeout)
        except Exception:
            timeout = DEFAULT_TIMEOUT
    if timeout is not None:
        timeout = max(1.0, timeout)
        if MAX_TIMEOUT:
            timeout = min(timeout, MAX_TIMEOUT)

    s = _session(name)
    if args.get("reset"):
        s.stop()

    started = time.time()
    res = s.run(code, timeout)
    elapsed = time.time() - started

    # ── 渲染结果（自解释，模型不用猜格式）──
    parts = []
    out = (res.get("stdout") or "")
    err = (res.get("error") or "")
    if len(out) > MAX_OUTPUT_CHARS:
        out = out[:MAX_OUTPUT_CHARS] + f"\n…（输出过长已截断，共 {len(out)} 字符）"
    if out.strip():
        parts.append("【输出】\n" + out.rstrip())
    if err.strip():
        parts.append("【异常】\n" + err.rstrip())

    if not parts:
        parts.append("（无输出）")

    if res.get("ok"):
        status = "OK"
    elif res.get("timeout"):
        status = "TIMEOUT"
    else:
        status = "ERROR"

    if res.get("reset"):
        s.stop()

    head = (f"py_run[{name}] {status} 用时 {elapsed:.2f}s"
            f"（会话已运行 {'%.0fs' % (time.time() - s.started_at) if s.started_at else '0s'}，"
            f"cwd={_ws.rel(res.get('cwd') or _ws.workspace_root())}）")
    # 2026-11-06：执行型动作进行为账本。以前"我跑过那段代码"只在当轮的工具
    # 消息里，下一轮就没了；用户问"你刚才跑的是什么"只能复述不出来。
    try:
        from self_actions import record_tool as _rt
        _code_hint = " ".join(str(code).strip().split("\n")[0].split())[:40]
        _rt("run", f"py_run {status} {elapsed:.1f}s：{_code_hint}")
    except Exception:
        pass
    return head + "\n" + "\n\n".join(parts)


# ══════════════════════════════════════════════════════════════════
# 后台任务（job_*）—— 长任务不阻塞循环
# ══════════════════════════════════════════════════════════════════
class _Job:
    __slots__ = ("id", "label", "cmd", "proc", "ring", "lock", "status",
                 "started", "ended", "read_pos", "kind",
                 "log_path", "json_path", "pid", "log_pos")

    def __init__(self, jid, label, cmd, proc, kind="shell", log_path=""):
        self.id = jid
        self.label = label
        self.cmd = cmd
        self.proc = proc
        self.kind = kind
        self.ring = []          # 保留最近 JOB_RING 行
        self.lock = threading.Lock()
        self.status = "running"
        self.started = time.time()
        self.ended = None
        self.read_pos = 0       # 上次读到的位置（增量读取的关键）
        self.log_path = log_path or ""
        self.json_path = ""
        self.pid = int(getattr(proc, "pid", 0) or 0)
        self.log_pos = 0        # 进程重启后从日志末尾续读

    def _write_log(self, line):
        if not self.log_path:
            return
        try:
            with open(self.log_path, "a", encoding="utf-8", errors="replace") as f:
                f.write(line + "\n")
        except Exception:
            pass

    def _poll_log(self):
        """进程重启后：从日志文件续读新增行（设计 §11.1）。"""
        if not self.log_path:
            return
        try:
            if not os.path.exists(self.log_path):
                return
            with open(self.log_path, "r", encoding="utf-8", errors="replace") as f:
                f.seek(self.log_pos)
                lines = f.readlines()
                self.log_pos = f.tell()
        except Exception:
            return
        with self.lock:
            for raw in lines:
                self.ring.append(raw.rstrip("\n"))
            if len(self.ring) > JOB_RING:
                del self.ring[: len(self.ring) - JOB_RING]

    def _pump(self):
        try:
            for raw in self.proc.stdout:
                line = raw.rstrip("\n")
                self._write_log(line)
                with self.lock:
                    self.ring.append(line)
                    if len(self.ring) > JOB_RING:
                        del self.ring[: len(self.ring) - JOB_RING]
        except Exception:
            pass
        try:
            self.proc.wait(timeout=5)
        except Exception:
            pass
        with self.lock:
            if self.status == "running":
                self.status = "finished" if (self.proc.returncode in (0, None)) else "failed"
            self.ended = time.time()
        _persist_job(self)
        # ★ 2026-11-07 主动性 P2：后台任务结束要发事件。
        #   主动性的 ⑨「任务交付后主动汇报」就靠它；放在 _pump 收尾（进程真的退出）
        #   而不是 job_wait —— 没人来 wait 的那些任务同样该被汇报。
        try:
            from bot_core import _emit_plugin_event
            _tail = "".join(self.ring[-6:])[-600:]
            _emit_plugin_event("job.done", {
                "job_id": self.id,
                "label": self.label,
                "kind": self.kind,
                "ok": self.status == "finished",
                "returncode": self.proc.returncode,
                "tail": _tail,
                # 产物路径：视频/图片/音频链路会把输出路径打在最后几行，
                # 主动性据此直接发产物，而不是只说一句"做好了"。
                "artifact": _guess_artifact(_tail),
                "ts": time.time(),
            })
        except Exception:
            pass

    def tail(self, wait=False, timeout=5.0):
        """返回上次读取之后的新增行（增量），以及状态行。"""
        deadline = time.time() + max(0.0, timeout)
        while True:
            if self.proc is None:
                self._poll_log()
                if self.status == "running" and not _pid_alive(self.pid):
                    with self.lock:
                        self.status = "finished"
                        self.ended = self.ended or time.time()
                    _persist_job(self)
            with self.lock:
                new = self.ring[self.read_pos:]
                self.read_pos = len(self.ring)
                st = self.status
            if new or not wait or st != "running" or time.time() >= deadline:
                return new, st
            time.sleep(0.15)


def _jobs_dir() -> str:
    """后台任务落盘目录：<data>/autonomy/jobs（设计 §11.1）。"""
    try:
        from data_store import get_data_dir
        base = str(get_data_dir())
    except Exception:
        base = os.path.join(_ws.workspace_root(), "data")
    p = os.path.join(base, "autonomy", "jobs")
    try:
        os.makedirs(p, exist_ok=True)
    except Exception:
        pass
    return p


def _persist_job(j) -> None:
    """把任务元数据原子写盘；job_output 重启后靠它接上。

    1.6.0：同时把状态镜像进**统一任务中心**（收口到全局唯一 job_id）。
    这一份是跨进程的 —— 另一个进程只要打开同一个库，就能凭 job_id 续等，
    而不必像 `_JOBS` 那样依赖本进程 import 期的一次性 `_restore_jobs()`。
    """
    try:
        path = j.json_path or os.path.join(_jobs_dir(), f"{j.id}.json")
        j.json_path = path
        data = {
            "id": j.id, "label": j.label, "cmd": j.cmd,
            "pid": int(j.pid or 0), "status": j.status,
            "started": j.started, "ended": j.ended, "kind": j.kind,
            "log_path": j.log_path,
        }
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, default=str)
        os.replace(tmp, path)
    except Exception:
        pass
    try:
        from . import task_center as _tc
        _tc.register(
            j.id, kind=j.kind, source="tool_proc", external_id=j.id,
            label=j.label, status=j.status, pid=int(j.pid or 0),
            log_path=j.log_path, payload={"cmd": j.cmd},
            started=j.started, ended=j.ended or 0.0)
    except Exception:
        pass


_JOBS = {}
_JOBS_LOCK = threading.Lock()


def _pid_alive(pid: int) -> bool:
    try:
        pid = int(pid)
        if pid <= 0:
            return False
        if os.name == "nt":
            import ctypes
            h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
            if not h:
                return False
            code = ctypes.c_ulong(0)
            ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code))
            ctypes.windll.kernel32.CloseHandle(h)
            return code.value == 259  # STILL_ACTIVE
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def _restore_jobs() -> None:
    """启动时接上还活着的后台任务；已死的清掉元数据（日志一并清）。"""
    d = _jobs_dir()
    try:
        names = os.listdir(d)
    except Exception:
        return
    for name in names:
        if not name.endswith(".json"):
            continue
        path = os.path.join(d, name)
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue
        jid = str(data.get("id") or name[:-5])
        pid = int(data.get("pid") or 0)
        log_path = str(data.get("log_path") or os.path.join(d, f"{jid}.log"))
        if not _pid_alive(pid):
            try:
                os.remove(path)
                if os.path.exists(log_path):
                    os.remove(log_path)
            except Exception:
                pass
            continue
        j = _Job(jid, str(data.get("label") or jid), data.get("cmd") or [],
                 None, kind=str(data.get("kind") or "shell"), log_path=log_path)
        j.json_path = path
        j.pid = pid
        j.status = "running"
        j.started = float(data.get("started") or time.time())
        j._poll_log()
        _JOBS[jid] = j
    try:
        _JOBS_LOCK  # no-op：保持锁对象在模块加载期存在
    except Exception:
        pass


try:
    _restore_jobs()
except Exception:
    pass


#: 从任务输出里捞产物路径（视频/图片/音频）。只认**真实存在**的文件，
#: 免得把日志里的示例路径当成产物发出去。
_ARTIFACT_RE = re.compile(
    r"([A-Za-z]:[\\/][^\s\"'<>|，。；：]+\.(?:mp4|mov|mkv|avi|png|jpe?g|gif|webp|wav|mp3|ogg))",
    re.I)


def _guess_artifact(text: str) -> str:
    for raw in reversed(_ARTIFACT_RE.findall(str(text or ""))):
        p = raw.strip().rstrip(".,;:，。；：）)")
        try:
            if os.path.isfile(p):
                return p
        except Exception:
            continue
    return ""


def _new_job(label, cmd, kind="shell"):
    with _JOBS_LOCK:
        if MAX_JOBS and len(_JOBS) >= MAX_JOBS:
            return None, f"后台任务已达上限（{MAX_JOBS}）。请先用 job_kill 结束一些任务。"
        jid = "job-" + uuid.uuid4().hex[:6]
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        creationflags = 0
        if os.name == "nt":
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                cwd=_ws.workspace_root(), env=env, text=True, encoding="utf-8",
                errors="replace", bufsize=1, shell=isinstance(cmd, str),
                creationflags=creationflags,
            )
        except Exception as e:
            return None, f"启动失败: {type(e).__name__}: {e}"
        j = _Job(jid, label, cmd, proc, kind=kind,
                 log_path=os.path.join(_jobs_dir(), f"{jid}.log"))
        j.json_path = os.path.join(_jobs_dir(), f"{jid}.json")
        _JOBS[jid] = j
    _persist_job(j)
    threading.Thread(target=j._pump, daemon=True).start()
    return j, ""


def job_start(args):
    """在后台启动一个 Python 脚本或**内联命令**（不阻塞；用 job_output/job_wait 读）。"""
    args = args or {}
    # ★ 1.6.0 P3.2（2026-11-01）：允许内联命令，不必先 ws_write 一个脚本。
    #   自主开发层的下载/装依赖都是"一次长命令"，多两次往返就是白烧预算。
    inline = args.get("cmd")
    if isinstance(inline, list) and inline:
        cmd = [str(x) for x in inline]
        j, err = _new_job(args.get("label") or " ".join(cmd[:2]), cmd, kind="shell")
        if not j:
            return f"[错误] {err}"
        return (f"已在后台启动 {j.id}（shell）：{' '.join(cmd[:3])}…\n"
                f"用 job_wait(job_id='{j.id}') 一次等到结束，或 job_output 增量读。")
    if isinstance(inline, str) and inline.strip():
        cmd = inline.strip()
        j, err = _new_job(args.get("label") or cmd[:40], cmd, kind="shell")
        if not j:
            return f"[错误] {err}"
        return (f"已在后台启动 {j.id}（shell）：{cmd[:60]}\n"
                f"用 job_wait(job_id='{j.id}') 一次等到结束。")

    res = _ws.resolve(args.get("path"), must_exist=True)
    if not res.ok:
        return _ws.describe_error(res)
    path = res.path
    if os.path.isdir(path):
        return f"[错误] {_ws.rel(path)} 是目录。job_start 需要一个脚本文件（或传 cmd 内联命令）。"

    extra = args.get("args")
    cmd = [python_executable(), "-u", path]
    if isinstance(extra, list):
        cmd += [str(x) for x in extra]
    elif isinstance(extra, str) and extra.strip():
        cmd += extra.split()

    j, err = _new_job(args.get("label") or os.path.basename(path), cmd, kind="python")
    if not j:
        return f"[错误] {err}"
    return (f"已在后台启动 {j.id}（{j.kind}）：{_ws.rel(path)}\n"
            f"用 job_output(job_id='{j.id}') 增量读取输出；job_kill 结束它。")


#: job_output 的等待上限。分离态不设上限（0 = 不限）；产品态维持 5s。
JOB_WAIT_DEFAULT_MS = 5000


def _env_int(name, default):
    try:
        raw = os.environ.get(name)
        return int(raw) if raw not in (None, "") else int(default)
    except Exception:
        return int(default)


JOB_WAIT_MAX_MS = _env_int("EGGBLOOM_JOB_WAIT_MAX_MS",
                           0 if _separation_active() else 5000)


def _job_wait_ms(raw) -> int:
    """把调用方给的 timeout_ms 夹到 [0, JOB_WAIT_MAX_MS]；0 表示不封顶。"""
    try:
        ms = int(raw) if raw not in (None, "") else JOB_WAIT_DEFAULT_MS
    except Exception:
        ms = JOB_WAIT_DEFAULT_MS
    ms = max(0, ms)
    return min(ms, JOB_WAIT_MAX_MS) if JOB_WAIT_MAX_MS > 0 else ms


def _reconnect(jid: str):
    """1.6.0：凭 job_id 从**统一任务中心**接回一个本进程 `_JOBS` 里没有的任务。

    解决的正是「`_JOBS` 是纯内存表，重启后 job_wait 接不上」：任务的 pid / 日志
    路径 / 状态都落进总表了，这里重建一个没有 Popen 句柄的 `_Job`（靠 pid 判活、
    靠日志文件续读），之后 `tail()` 的既有逻辑照常工作。找不到返回 None。
    """
    try:
        from . import task_center as _tc
        rec = _tc.get(jid)
    except Exception:
        rec = None
    if not rec or rec.get("source") != "tool_proc":
        return None
    payload = rec.get("payload")
    cmd = payload.get("cmd") if isinstance(payload, dict) else []
    log_path = str(rec.get("log_path") or "")
    j = _Job(str(rec.get("job_id") or jid), str(rec.get("label") or jid),
             cmd, None, kind=str(rec.get("kind") or "shell"), log_path=log_path)
    j.json_path = os.path.join(os.path.dirname(log_path) or _jobs_dir(), f"{jid}.json")
    j.pid = int(rec.get("pid") or 0)
    j.status = str(rec.get("status") or "running")
    j.started = float(rec.get("started") or time.time())
    j.ended = float(rec.get("ended") or 0) or None
    j._poll_log()
    with _JOBS_LOCK:
        _JOBS[jid] = j
    return j


def _tc_extra_lines():
    """job_list 用：总表里那些本进程没有句柄、但**可凭 job_id 续等**的任务。"""
    try:
        from . import task_center as _tc
        recs = _tc.list_jobs(source="tool_proc", limit=100)
    except Exception:
        return []
    out = []
    for r in recs:
        if r.get("job_id") in _JOBS:
            continue
        out.append(f"  {r['job_id']} [{r.get('status')}] {r.get('label')} "
                   f"— 总表登记（可凭 job_wait 续等）")
    return out


def job_output(args):
    """读取后台任务的**新增**输出（增量，不重复返回已读过的内容）。"""
    args = args or {}
    jid = str(args.get("job_id") or "")
    j = _JOBS.get(jid) or _reconnect(jid)
    if not j:
        alive = ", ".join(f"{k}({v.status})" for k, v in _JOBS.items()) or "无"
        return f"[错误] 没有后台任务 {jid}。当前任务：{alive}"

    new, st = j.tail(wait=bool(args.get("wait")), timeout=_job_wait_ms(args.get("timeout_ms")) / 1000.0)
    head = f"{jid} [{st}] — {j.label}（增量 {len(new)} 行）"
    body = "\n".join(new) if new else "（本次无新增输出）"
    out = head + "\n" + body
    if st != "running":
        out += f"\n[status: {st}]（任务已结束）"
    else:
        out += "\n[status: running]"
    return out


#: `job_wait` 的单次等待天花板。分离态 0 = 不封顶；产品态 120s。
JOB_WAIT_CEILING = float(os.environ.get(
    "EGGBLOOM_JOB_WAIT_CEILING", "0" if _separation_active() else "120") or 0)
#: 返回时最多带多少行输出（尾部），防止 20 分钟的日志把上下文灌爆
JOB_WAIT_TAIL_LINES = 80


def job_wait(args):
    """**一次调用等到结束**（不烧轮询预算）。

    设计依据：`分离与真实-设计.md` §11.1 —— 20 分钟的下载应该只消耗 1 次工具预算，
    而不是 200 次 `job_output`。内部每 2 秒看一次，超时才返回。
    """
    args = args or {}
    jid = str(args.get("job_id") or "")
    j = _JOBS.get(jid) or _reconnect(jid)
    if not j:
        alive = ", ".join(f"{k}({v.status})" for k, v in _JOBS.items()) or "无"
        return f"[错误] 没有后台任务 {jid}。当前任务：{alive}"
    raw_sec = args.get("max_sec")
    try:
        max_sec = float(raw_sec) if raw_sec not in (None, "") else (0.0 if JOB_WAIT_CEILING <= 0 else 120.0)
    except (TypeError, ValueError):
        max_sec = 0.0 if JOB_WAIT_CEILING <= 0 else 120.0
    if max_sec > 0:
        max_sec = max(1.0, max_sec)
        if JOB_WAIT_CEILING > 0:
            max_sec = min(max_sec, JOB_WAIT_CEILING)
    until = str(args.get("until") or "exit").strip()
    pattern = None
    if until and until != "exit":
        try:
            pattern = re.compile(until)
        except re.error as e:
            return f"[错误] until 不是合法正则：{e}"

    t0 = time.time()
    collected, hit = [], False
    while True:
        new, st = j.tail(wait=True, timeout=2.0)
        if new:
            if pattern is not None and any(pattern.search(x) for x in new):
                hit = True
            collected.extend(new)
        if hit:
            break
        if st != "running":
            break
        if max_sec > 0 and time.time() - t0 >= max_sec:
            break
        time.sleep(0.2)

    elapsed = time.time() - t0
    st = j.status
    tail = collected[-JOB_WAIT_TAIL_LINES:]
    lines = [f"{jid} [{st}] — {j.label}（等待 {elapsed:.1f}s，读回 {len(collected)} 行"
             + (f"，命中 /{until}/" if hit else "") + "）"]
    if st == "running":
        lines.append(f"[status: running] 已等 {max_sec:.0f}s 还没结束，可以再等一次"
                     "（job_wait）或先干别的（job_output 随时可读）。")
    else:
        lines.append(f"[status: {st}]（任务已结束）")
    if tail:
        lines.append("【输出·最后%d行】" % len(tail))
        lines.extend(tail)
    else:
        lines.append("（本次无新增输出）")
    return "\n".join(lines)


def job_list(args=None):
    """列出所有后台任务及其状态（含总表里可续等的、本进程没句柄的任务）。"""
    lines = []
    for jid, j in _JOBS.items():
        lines.append(f"  {jid} [{j.status}] {j.label} — 已运行 {time.time() - j.started:.0f}s")
    lines.extend(_tc_extra_lines())
    if not lines:
        return "（当前没有后台任务）"
    return "后台任务：\n" + "\n".join(lines)


def job_kill(args):
    """结束一个后台任务（及其子进程）。"""
    args = args or {}
    jid = str(args.get("job_id") or "")
    j = _JOBS.get(jid)
    if not j:
        return f"[错误] 没有后台任务 {jid}。"
    if j.proc is not None:
        _kill_tree(j.proc)
    elif j.pid:
        # 重启后接上的任务没有 Popen 句柄，只能按 pid 杀进程树
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(j.pid), "/T", "/F"],
                               capture_output=True, text=True, timeout=10)
            else:
                os.kill(int(j.pid), 15)
        except Exception:
            pass
    with j.lock:
        j.status = "killed"
        j.ended = time.time()
    _persist_job(j)
    return f"已结束 {jid}（{j.label}）。原因：{args.get('reason') or '未说明'}"


# ══════════════════════════════════════════════════════════════════
# 会话信息（让模型知道自己在什么环境里跑 —— 一次探测，长期受益）
# ══════════════════════════════════════════════════════════════════
def env_report():
    """一句话环境说明（可注入 system prompt 或首个工具结果里）。"""
    py = python_executable()
    return (f"工作目录={_ws.workspace_root()}；Python={py}；"
            f"可用工具：ws_read/ws_edit/ws_write/ws_glob/ws_grep/ws_edit_many/py_run/"
            f"job_start/job_output/job_list/job_kill")


# ══════════════════════════════════════════════════════════════════
# todo（整表替换）
# ══════════════════════════════════════════════════════════════════
_STATUSES = ("pending", "in_progress", "completed")
_TODOS = {"list": []}
_todo_lock = threading.Lock()


def todo_write(args):
    """记录/更新任务清单。**每次都要发完整清单**（整表替换，没有部分更新）。

    约束（与 dsh 一致，且是有用的约束）
      · content 必须非空、不重复
      · 默认最多一个 in_progress（多步顺序任务才清晰）
      · 无 in_progress 仅在所有任务都完成时允许
    """
    args = args or {}
    raw = args.get("todos")
    if not isinstance(raw, list):
        return "[错误] todos 必须是数组，每项 {content, status}。"
    if len(raw) > 30:
        return "[错误] 任务项过多（上限 30）。请合并同类项。"

    seen = set()
    clean = []
    active = 0
    for i, it in enumerate(raw, 1):
        if not isinstance(it, dict):
            return f"[错误] 第 {i} 项不是对象。"
        c = str(it.get("content") or "").strip()
        s = str(it.get("status") or "").strip()
        if not c:
            return f"[错误] 第 {i} 项 content 为空。"
        if c in seen:
            return f"[错误] 第 {i} 项与前面重复：{c}"
        seen.add(c)
        if s not in _STATUSES:
            return (f"[错误] 第 {i} 项 status 非法：{s!r}。"
                    f"只能是 {' / '.join(_STATUSES)}。")
        if s == "in_progress":
            active += 1
        clean.append({"content": c, "status": s})

    if active > 1:
        return (f"[错误] 有 {active} 项处于 in_progress。请只保留**一个**当前正在做的任务"
                f"（顺序任务一次做一件更清晰）。")

    with _todo_lock:
        _TODOS["list"] = clean

    marks = {"pending": "·", "in_progress": ">", "completed": "x"}
    lines = [f"{marks[t['status']]} {t['content']}" for t in clean]
    done = sum(1 for t in clean if t["status"] == "completed")
    return f"任务清单已更新（{done}/{len(clean)} 完成）：\n" + "\n".join(lines)


def todos():
    """供前端页读取当前清单。"""
    with _todo_lock:
        return list(_TODOS["list"])


# ══════════════════════════════════════════════════════════════════
# schema
# ══════════════════════════════════════════════════════════════════
_STR = {"type": "string"}
_BOOL = {"type": "boolean"}
_NUM = {"type": "number"}


def _fn(name, desc, props, required):
    return {"type": "function", "function": {
        "name": name, "description": desc,
        "parameters": {"type": "object", "properties": props, "required": required}}}


PROC_TOOLS = [
    _fn("py_run",
        "在**持久 Python 会话**中执行代码（变量/import/cwd 跨调用保留）。"
        "做数据处理、计算、批量文件操作、调库时用它。最后一行可以是表达式，会打印其值。",
        {"code": dict(_STR, description="要执行的 Python 代码"),
         "timeout": dict(_NUM, description=(
            "超时秒数；分离态不设上限（不传即不超时）" if MAX_TIMEOUT is None
            else f"超时秒数，默认 {DEFAULT_TIMEOUT:.0f}，上限 {MAX_TIMEOUT:.0f}")),
         "session": dict(_STR, description="会话名，默认 default（不同名即不同环境）"),
         "reset": dict(_BOOL, description="为 true 时先重启会话（清空全部变量）")},
        ["code"]),
    _fn("job_start",
        "在后台启动任务（不阻塞；适合长任务）：给 path 跑 Python 脚本，或给 cmd 直接跑命令。"
        "长任务用 job_wait 一次等到结束，别用 job_output 反复轮询。",
        {"path": dict(_STR, description="脚本文件路径（与 cmd 二选一）"),
         "cmd": {"type": ["string", "array"], "items": _STR,
                 "description": "内联命令：字符串走 shell，或直接给 argv 列表（如 [\"python\",\"-u\",\"-c\",\"...\"]）"},
         "args": {"type": "array", "items": _STR, "description": "传给脚本的命令行参数"},
         "label": dict(_STR, description="任务显示名")},
        []),
    _fn("job_output",
        "读取后台任务的**新增**输出（增量，不重复）。wait=true 时等它结束（有上限）。",
        {"job_id": dict(_STR, description="job_start 返回的任务号"),
         "wait": dict(_BOOL, description="等待任务结束或超时"),
         "timeout_ms": dict(_NUM, description="等待上限（毫秒），仅 wait=true 时有效")},
        ["job_id"]),
    _fn("job_wait",
        "**一次调用阻塞等到后台任务结束**（内部每 2 秒看一眼）。"
        "20 分钟的下载只消耗 1 次工具调用，不要用 job_output 反复轮询。"
        "until 可传正则（等某行出现就返回），默认 exit=等进程退出。",
        {"job_id": dict(_STR, description="任务号"),
         "max_sec": dict(_NUM, description=f"最多等多久（秒），默认 120，上限 {JOB_WAIT_CEILING:.0f}"),
         "until": dict(_STR, description="'exit'（默认）或正则，命中即返回")},
        ["job_id"]),
    _fn("job_list", "列出所有后台任务及其状态。", {}, []),
    _fn("job_kill", "结束一个后台任务（含其子进程）。",
        {"job_id": dict(_STR, description="任务号"),
         "reason": dict(_STR, description="结束原因（可选）")},
        ["job_id"]),
]

TODO_TOOLS = [
    _fn("todo_write",
        "记录/更新当前工作的任务清单。每次都要发**完整清单**（整表替换，无部分更新）。"
        "多步任务开始前先列清单，完成一项立刻标记，不要攒着一起标。",
        {"todos": {"type": "array", "description": "完整任务清单", "items": {
            "type": "object", "properties": {
                "content": dict(_STR, description="任务内容（简短祈使句）"),
                "status": {"type": "string", "enum": list(_STATUSES),
                           "description": "pending 未开始 / in_progress 进行中 / completed 已完成"},
            }, "required": ["content", "status"]}}},
        ["todos"]),
]

PROC_FUNCTIONS = {
    "py_run": tool_py_run,
    "job_start": job_start,
    "job_output": job_output,
    "job_wait": job_wait,
    "job_list": job_list,
    "job_kill": job_kill,
    "todo_write": todo_write,
}


def register_all(*, root=None):
    """把执行型 + 规划型工具注册进 tool_registry，并返回实现字典。

      proc : py_run / job_* —— 最贵，注入条件最严
      todo : todo_write      —— 多步任务才有价值
    """
    from . import tool_registry as _tr
    if root:
        _ws.set_root(root)
    fns = dict(PROC_FUNCTIONS)
    for sch in PROC_TOOLS:
        name = sch["function"]["name"]
        _tr.register(name, sch, fn=fns.get(name),
                     groups=(_tr.GROUP_PROC,), source="harness")
    for sch in TODO_TOOLS:
        name = sch["function"]["name"]
        _tr.register(name, sch, fn=fns.get(name),
                     groups=(_tr.GROUP_TODO,), source="harness")
    return fns
