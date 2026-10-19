# -*- coding: utf-8 -*-
"""scripts/dev_watch.py —— 改代码 → 自动同步 → 自动重启后端 → 等到真的能打开。

为什么需要它
    生效是两步（见 scripts/activate.py 顶部说明）：同步 + **重启后端**。
    第二步全靠人记，忘了就表现为"改了没反应"，而且**不报错** —— 这是本项目
    最容易误判的一类问题。本脚本把第二步也自动化。

它做什么
    1. 盯着运行时源码（顶层 *.py + plugins/protocols/harness/webui/components/scripts 下的 *.py）
    2. 静默 debounce（默认 1.2s）后跑一遍 activate 的同步 + 校验
    3. 结束旧后端进程：
         · 若 EggBloom Desktop.exe 在跑 → 只杀后端，壳会在 3s 内自动拉起（不要自己 spawn，否则抢端口）
         · 若壳没跑 → 用发行包自带的 python 直接拉起 webui_server.py
    4. 轮询 http://127.0.0.1:<port>/ 直到有响应，打印耗时

    只盯 **.py**：前端 HTML 是 mtime 热重载（刷新浏览器即可），重启后端反而更慢。

用法
    python scripts/dev_watch.py                       # 常驻监听
    python scripts/dev_watch.py --once --force        # 立刻走一轮（不监听）
    python scripts/dev_watch.py --dry-run --once --force   # 只打印会做什么，不杀进程/不写盘
    python scripts/dev_watch.py --no-restart          # 只同步，不碰进程
    python scripts/dev_watch.py --dist "E:\\..."      # 显式指定发行根

安全边界
    · 只杀 cmdline 里含 webui_server.py 且**路径位于发行包 app 目录内**的进程；
    · 同步/校验失败 → **不重启**（避免把半同步的代码跑起来）；
    · 默认 dry-run 关闭，但第一次建议先跑 --dry-run --once --force 看一眼。
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

SRC_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: 需要重启才生效的源码目录（前端 html 不在内：它热重载）
#  2026-10-19：nexsandglass 已移除（沙漏改走根目录 sandglass_lite.py，被顶层 *.py 覆盖）
WATCH_DIRS = ("plugins", "protocols", "harness", "webui",
              "components", "scripts")
WATCH_EXTS = {".py"}

#: 运行时目录 / 缓存：变了也不该触发重启
SKIP_DIRS = {"__pycache__", ".git", ".ruff_cache", "node_modules",
             "data", "logs", "chat_logs", "image_cache", "personality_versions",
             "memes"}

DEFAULT_DEBOUNCE = 1.2
DEFAULT_INTERVAL = 1.0
READY_TIMEOUT = 60.0
SHELL_NAME = "eggbbloom desktop.exe"


# ── 纯函数（可单测，不碰进程/网络）────────────────────────────────

def is_watched(rel_path: str) -> bool:
    """该相对路径是否属于"改了就需重启后端"的源码。"""
    p = Path(str(rel_path).replace("\\", "/"))
    if p.suffix.lower() not in WATCH_EXTS:
        return False
    parts = p.parts
    if not parts:
        return False
    if any(part in SKIP_DIRS for part in parts[:-1]):
        return False
    if len(parts) == 1:          # 顶层 *.py
        return True
    return parts[0] in WATCH_DIRS


def snapshot(root: str) -> dict:
    """{相对路径: (mtime_ns, size)} —— 只看被监视的 .py。"""
    out: dict[str, tuple] = {}
    root = os.path.abspath(root)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if d not in SKIP_DIRS and not d.startswith(".")]
        for fn in filenames:
            if not fn.lower().endswith(".py"):
                continue
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, root).replace("\\", "/")
            if not is_watched(rel):
                continue
            try:
                st = os.stat(full)
            except OSError:
                continue
            out[rel] = (st.st_mtime_ns, st.st_size)
    return out


def changed_files(old: dict, new: dict) -> list:
    """两次快照之间新增/修改/删除的文件（排序，便于稳定输出）。"""
    keys = set(old) | set(new)
    return sorted(k for k in keys if old.get(k) != new.get(k))


def backend_processes(procs: list, app_dir: str) -> list:
    """从进程快照里挑出「跑着本发行包 webui_server.py」的后端进程。

    procs: [{"pid": int, "cmdline": [str], "cwd": str|None}]
    判据：cmdline 里有 webui_server.py，且解析后的绝对路径位于 app_dir 下。
    **路径不匹配的一律不动** —— 避免杀掉另一个发行包/另一个项目的后端。
    """
    target = os.path.normcase(os.path.abspath(app_dir))
    hits = []
    for p in procs or []:
        cmd = p.get("cmdline") or []
        cand = [c for c in cmd if "webui_server.py" in str(c).replace("\\", "/")]
        if not cand:
            continue
        raw = str(cand[0]).replace("\\", "/")
        if not os.path.isabs(raw):
            base = p.get("cwd") or ""
            raw = os.path.join(base, raw)
        full = os.path.normcase(os.path.abspath(raw))
        if full == target or full.startswith(target + os.sep):
            hits.append(p)
    return hits


def read_port(app_dir: str, default: int = 8080) -> int:
    """读 last_port.txt；缺失/非法时回退默认端口。"""
    try:
        txt = Path(app_dir, "last_port.txt").read_text(encoding="utf-8").strip()
        port = int(txt)
        return port if 1024 <= port <= 65535 else default
    except Exception:
        return default


# ── 进程侧（Linux 也能 import，只是 Windows 分支才有意义）──────────

def _collect_procs() -> list:
    try:
        import psutil  # type: ignore
    except Exception:
        return []
    out = []
    for pr in psutil.process_iter(["pid", "cmdline", "cwd"]):
        try:
            info = pr.info
            if not info.get("cmdline"):
                continue
            out.append({"pid": info["pid"],
                        "cmdline": list(info["cmdline"]),
                        "cwd": info.get("cwd")})
        except Exception:
            continue
    return out


def shell_running() -> bool:
    """Electron 外壳是否在跑（在跑就别自己 spawn，否则两个后端抢端口）。"""
    try:
        import psutil  # type: ignore
    except Exception:
        return False
    for pr in psutil.process_iter(["name"]):
        try:
            if str(pr.info.get("name") or "").lower() == SHELL_NAME:
                return True
        except Exception:
            continue
    return False


def _spawn_backend(dist: str, app_dir: str) -> dict:
    """用发行包自带 python 拉起后端（仅在 Electron 壳没跑时）。"""
    py = os.path.join(dist, "python", "python.exe")
    if not os.path.isfile(py):
        py = sys.executable
    script = os.path.join(app_dir, "webui", "webui_server.py")
    if not os.path.isfile(script):
        return {"ok": False, "error": f"找不到 {script}"}
    env = dict(os.environ)
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    flags = 0
    if os.name == "nt":
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    log_dir = os.path.join(app_dir, "logs")
    try:
        os.makedirs(log_dir, exist_ok=True)
        log = open(os.path.join(log_dir, "dev_watch_spawn.log"), "ab")
    except Exception:
        log = subprocess.DEVNULL
    try:
        proc = subprocess.Popen([py, script], cwd=app_dir, env=env,
                                stdout=log, stderr=log, stdin=subprocess.DEVNULL,
                                creationflags=flags)
        return {"ok": True, "pid": proc.pid, "python": py}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def restart_backend(dist: str, app_dir: str, *, dry: bool = False) -> dict:
    """结束旧后端并按情况拉起新后端。返回可供打印的结果字典。"""
    procs = _collect_procs()
    targets = backend_processes(procs, app_dir)
    has_shell = shell_running()
    result = {"killed": [], "spawned": None, "shell": has_shell,
              "targets": [p["pid"] for p in targets]}

    if dry:
        return result

    if not targets:
        # 后端没在跑（例如刚崩/刚关）→ 直接拉起
        if has_shell:
            result["note"] = "后端未运行，壳会自动拉起（等它就绪）"
        else:
            result["spawned"] = _spawn_backend(dist, app_dir)
        return result

    import psutil  # type: ignore
    for t in targets:
        pid = t["pid"]
        try:
            p = psutil.Process(pid)
            p.terminate()
            try:
                p.wait(timeout=8)
            except psutil.TimeoutExpired:
                p.kill()
            result["killed"].append(pid)
        except Exception as e:
            result.setdefault("errors", []).append(f"pid {pid}: {e}")

    if has_shell:
        result["note"] = "壳在跑 → 只杀后端，由壳自动拉起（避免抢端口）"
    else:
        time.sleep(0.6)                      # 让端口释放
        result["spawned"] = _spawn_backend(dist, app_dir)
    return result


def wait_ready(app_dir: str, *, timeout: float = READY_TIMEOUT) -> tuple:
    """轮询本机 WebUI，直到有 HTTP 响应。返回 (port|None, 耗时秒)。"""
    import urllib.error
    import urllib.request

    t0 = time.time()
    tried = []
    port = read_port(app_dir)
    tried.append(port)
    # 顺延后的端口也试一下：先读文件，再兜 8080/8081
    for extra in (8080, 8081):
        if extra not in tried:
            tried.append(extra)
    while time.time() - t0 < timeout:
        for p in tried:
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{p}/", timeout=1.0)
                return p, time.time() - t0
            except urllib.error.HTTPError:
                # 有 HTTP 响应（哪怕是 403/404）= 服务已经起来了
                return p, time.time() - t0
            except Exception:
                continue
        time.sleep(0.5)
    return None, time.time() - t0


# ── 主流程 ───────────────────────────────────────────────────────

def _load_activate():
    """复用 activate.py（同一目录），避免同步/校验逻辑写两份。"""
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)
    import activate  # type: ignore
    return activate


def run_cycle(dist: str, app_dir: str, files: list, args) -> bool:
    act = _load_activate()
    shown = ", ".join(files[:6]) + (f" …共 {len(files)} 个" if len(files) > 6 else "")
    print(f"\n[watch] 检测到改动: {shown}", flush=True)

    if args.dry_run:
        procs = backend_processes(_collect_procs(), app_dir)
        print(f"[watch][dry] 将同步 {dist}", flush=True)
        print(f"[watch][dry] 将结束后端 pid={[p['pid'] for p in procs] or '无'}；"
              f"壳在跑={shell_running()}", flush=True)
        return True

    if not args.no_sync:
        print("[watch] 同步 …", flush=True)
        if not act.step_sync(dist):
            print("[watch] !! 同步失败 → 本轮不重启", flush=True)
            return False
        act.step_report_pyc(dist)
        if not act.step_verify(dist):
            print("[watch] !! 校验不一致 → 本轮不重启（避免跑半同步代码）", flush=True)
            return False

    if args.no_restart:
        print("[watch] --no-restart：已同步，未重启", flush=True)
        return True

    print("[watch] 重启后端 …", flush=True)
    res = restart_backend(dist, app_dir, dry=False)
    if res.get("killed"):
        print(f"[watch] 已结束: {res['killed']}", flush=True)
    if res.get("spawned"):
        sp = res["spawned"]
        print(f"[watch] 已拉起: pid={sp.get('pid')}" if sp.get("ok")
              else f"[watch] !! 拉起失败: {sp.get('error')}", flush=True)
    if res.get("note"):
        print(f"[watch] {res['note']}", flush=True)
    for e in res.get("errors") or []:
        print(f"[watch] !! {e}", flush=True)

    port, secs = wait_ready(app_dir, timeout=args.ready_timeout)
    if port:
        print(f"[watch] ✓ 就绪 http://localhost:{port}（{secs:.1f}s）", flush=True)
        return True
    print(f"[watch] !! {secs:.0f}s 内没等到 WebUI，请手动看 logs/", flush=True)
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description="改代码自动同步 + 重启后端")
    ap.add_argument("--dist", default=None, help="发行包根目录")
    ap.add_argument("--interval", type=float, default=DEFAULT_INTERVAL,
                    help=f"扫描间隔秒（默认 {DEFAULT_INTERVAL}）")
    ap.add_argument("--debounce", type=float, default=DEFAULT_DEBOUNCE,
                    help=f"静默多久才动作（默认 {DEFAULT_DEBOUNCE}s）")
    ap.add_argument("--once", action="store_true", help="只走一轮就退出")
    ap.add_argument("--force", action="store_true", help="配合 --once：无改动也执行")
    ap.add_argument("--dry-run", action="store_true", help="只打印，不写盘/不杀进程")
    ap.add_argument("--no-sync", action="store_true", help="跳过同步")
    ap.add_argument("--no-restart", action="store_true", help="只同步不重启")
    ap.add_argument("--ready-timeout", type=float, default=READY_TIMEOUT)
    args = ap.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    act = _load_activate()
    dist = act.find_dist(args.dist)
    if not dist:
        print("[watch] 找不到发行包。用 --dist 指定（需含 resources\\app\\webui\\webui_server.py）")
        return 2
    app_dir = os.path.join(dist, "resources", "app")

    print("=" * 64)
    print("  EggBloom 开发监听")
    print(f"  源码库 : {SRC_ROOT}")
    print(f"  发行包 : {dist}")
    print(f"  模式   : {'dry-run' if args.dry_run else 'apply'}"
          f"{' / once' if args.once else ''}")
    print("  监视   : 顶层 *.py + " + " / ".join(WATCH_DIRS) + " 下的 *.py")
    print("=" * 64, flush=True)

    prev = snapshot(SRC_ROOT)

    if args.once:
        files = ["(forced)"] if args.force else changed_files(prev, snapshot(SRC_ROOT))
        if not files:
            print("[watch] 没有改动（--force 可强制执行）")
            return 0
        return 0 if run_cycle(dist, app_dir, files, args) else 1

    # 常驻：记录"最后一次变动时刻"，静默 debounce 后才动作
    pending: list = []
    last_change = 0.0
    try:
        while True:
            time.sleep(max(0.2, args.interval))
            now = snapshot(SRC_ROOT)
            diff = changed_files(prev, now)
            if diff:
                prev = now
                for f in diff:
                    if f not in pending:
                        pending.append(f)
                last_change = time.time()
                continue
            if pending and (time.time() - last_change) >= args.debounce:
                files = sorted(pending)
                pending = []
                run_cycle(dist, app_dir, files, args)
                prev = snapshot(SRC_ROOT)     # 动作期间可能又变了，重新取基线
    except KeyboardInterrupt:
        print("\n[watch] 已退出")
        return 0


if __name__ == "__main__":
    sys.exit(main())
