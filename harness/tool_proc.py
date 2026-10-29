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
import threading
import subprocess
import uuid

from . import workspace as _ws

# ── 预算 ───────────────────────────────────────────────────────────
DEFAULT_TIMEOUT = 20.0          # 单次执行默认超时（秒）
MAX_TIMEOUT = 120.0
MAX_OUTPUT_CHARS = 60_000       # 单次内联上限（超出交给 spill）
MAX_JOBS = 8                    # 常驻后台任务上限
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
    try:
        timeout = float(args.get("timeout") or DEFAULT_TIMEOUT)
    except Exception:
        timeout = DEFAULT_TIMEOUT
    timeout = max(1.0, min(timeout, MAX_TIMEOUT))

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
    return head + "\n" + "\n\n".join(parts)


# ══════════════════════════════════════════════════════════════════
# 后台任务（job_*）—— 长任务不阻塞循环
# ══════════════════════════════════════════════════════════════════
class _Job:
    __slots__ = ("id", "label", "cmd", "proc", "ring", "lock", "status",
                 "started", "ended", "read_pos", "kind")

    def __init__(self, jid, label, cmd, proc, kind="shell"):
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

    def _pump(self):
        try:
            for raw in self.proc.stdout:
                with self.lock:
                    self.ring.append(raw.rstrip("\n"))
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

    def tail(self, wait=False, timeout=5.0):
        """返回上次读取之后的新增行（增量），以及状态行。"""
        deadline = time.time() + max(0.0, timeout)
        while True:
            with self.lock:
                new = self.ring[self.read_pos:]
                self.read_pos = len(self.ring)
                st = self.status
            if new or not wait or st != "running" or time.time() >= deadline:
                return new, st
            time.sleep(0.15)


_JOBS = {}
_JOBS_LOCK = threading.Lock()


def _new_job(label, cmd, kind="shell"):
    with _JOBS_LOCK:
        if len(_JOBS) >= MAX_JOBS:
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
        j = _Job(jid, label, cmd, proc, kind=kind)
        _JOBS[jid] = j
    threading.Thread(target=j._pump, daemon=True).start()
    return j, ""


def job_start(args):
    """在后台启动一个 Python 脚本文件（不阻塞；用 job_output 增量读取）。"""
    args = args or {}
    res = _ws.resolve(args.get("path"), must_exist=True)
    if not res.ok:
        return _ws.describe_error(res)
    path = res.path
    if os.path.isdir(path):
        return f"[错误] {_ws.rel(path)} 是目录。job_start 需要一个脚本文件。"

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


#: job_output 的等待上限（1.5.10 H6）。
#: 默认 5s 与改前一致；环境变量可放到 1800s —— 1.6.0 的 job_wait(max_sec=1800) 就在这层之上。
JOB_WAIT_DEFAULT_MS = 5000
JOB_WAIT_MAX_MS = int(os.environ.get("EGGBLOOM_JOB_WAIT_MAX_MS", "5000") or 5000)


def _job_wait_ms(raw) -> int:
    """把调用方给的 timeout_ms 夹到 [0, JOB_WAIT_MAX_MS]，缺省用默认值。"""
    try:
        ms = int(raw) if raw not in (None, "") else JOB_WAIT_DEFAULT_MS
    except Exception:
        ms = JOB_WAIT_DEFAULT_MS
    return max(0, min(ms, JOB_WAIT_MAX_MS))


def job_output(args):
    """读取后台任务的**新增**输出（增量，不重复返回已读过的内容）。"""
    args = args or {}
    jid = str(args.get("job_id") or "")
    j = _JOBS.get(jid)
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


def job_list(args=None):
    """列出所有后台任务及其状态。"""
    if not _JOBS:
        return "（当前没有后台任务）"
    lines = []
    for jid, j in _JOBS.items():
        lines.append(f"  {jid} [{j.status}] {j.label} — 已运行 {time.time() - j.started:.0f}s")
    return "后台任务：\n" + "\n".join(lines)


def job_kill(args):
    """结束一个后台任务（及其子进程）。"""
    args = args or {}
    jid = str(args.get("job_id") or "")
    j = _JOBS.get(jid)
    if not j:
        return f"[错误] 没有后台任务 {jid}。"
    _kill_tree(j.proc)
    with j.lock:
        j.status = "killed"
        j.ended = time.time()
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
         "timeout": dict(_NUM, description=f"超时秒数，默认 {DEFAULT_TIMEOUT:.0f}，上限 {MAX_TIMEOUT:.0f}"),
         "session": dict(_STR, description="会话名，默认 default（不同名即不同环境）"),
         "reset": dict(_BOOL, description="为 true 时先重启会话（清空全部变量）")},
        ["code"]),
    _fn("job_start",
        "在后台启动一个 Python 脚本（不阻塞；适合长任务）。用 job_output 增量读取输出。",
        {"path": dict(_STR, description="脚本文件路径"),
         "args": {"type": "array", "items": _STR, "description": "传给脚本的命令行参数"},
         "label": dict(_STR, description="任务显示名")},
        ["path"]),
    _fn("job_output",
        "读取后台任务的**新增**输出（增量，不重复）。wait=true 时等它结束（有上限）。",
        {"job_id": dict(_STR, description="job_start 返回的任务号"),
         "wait": dict(_BOOL, description="等待任务结束或超时"),
         "timeout_ms": dict(_NUM, description="等待上限（毫秒），仅 wait=true 时有效")},
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
