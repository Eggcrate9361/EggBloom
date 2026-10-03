# -*- coding: utf-8 -*-
"""scripts/activate.py —— 让源码改动真正生效，并**证明**它生成了效。

为什么需要这个脚本
    EggBloom 的"生效"不是一步，而是两步，任何一步漏了都会**静默失败**：

      1. 源码库 (D:\\git\\EggBloom) → 发行目录 (resources\\app)
         由 scripts/sync-dist.ps1 负责。它是 **只增不删** 的 robocopy /E。
         漏了这一步 → 程序跑的还是旧文件。

      2. 正在运行的进程 → 重启
         后端是 `python resources\\app\\webui\\webui_server.py`，是**独立 python 进程**；
         Electron (`EggBloom Desktop.exe`) 只是浏览器壳，不持有业务逻辑。
         **后端进程不重启，就永远不会加载新代码。**

所以本脚本做：同步 → 校验 → 明确告诉你第 2 步怎么重启。

关于 __pycache__（实测澄清，别被传言误导）
    本脚本写出来后专门做了实测：Python 的 .pyc 校验基于源文件 **mtime + size**。
    源码被 sync 更新后 mtime 必然变 → 旧 .pyc 自动失效 → Python 会重编译。
    因此 **不需要清 .pyc**，也不该在别人的发行目录里批量删文件。
    本脚本只做体检（列出与源码 mtime/size 不匹配的 .pyc），**不删除**。

用法
    python scripts/activate.py                      # 同步 + 体检 + 校验
    python scripts/activate.py --check-only         # 只校验（不改动任何东西）
    python scripts/activate.py --dist "E:\\..."     # 指定发行包根目录
    python scripts/activate.py --no-sync            # 跳过同步（只体检 + 校验）
"""
import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys

SRC_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DIST = r"E:\EggBloom水星\EggBloom Desktop-new"

# 这些顶层项由 sync-dist.ps1 负责（与脚本的 $dirItems 保持一致 + 根目录文件）
ROOT_FILES = ["bot_core.py", "webui_server.py"]
DIR_ITEMS = ["plugins", "protocols", "webui", "nexsandglass", "components",
             "memes", "docs", "harness"]

# 运行时会写入的数据文件/目录：两边本来就该不一样，**不算同步失败**。
# 判定依据是"这些是程序自己产生的状态，不是源码"。
RUNTIME_SKIP_DIRS = {"data", "state", "logs", "chat_logs", "image_cache",
                     "__pycache__", "node_modules", "distills", "personality_versions"}
RUNTIME_SKIP_EXTS = {".db", ".db-wal", ".db-shm", ".sqlite", ".sqlite3", ".log",
                     ".pyc", ".pyo", ".tmp", ".bak", ".pid", ".lock"}
# 仅在 data/state 等目录外也可能被运行时改写、且属于配置状态的文件名
RUNTIME_SKIP_NAMES = {"config.json", "history.json", "last_port.txt",
                      "slang_dict.json", "slang_replies.json",
                      "slang_conflicts.json", "bot_config.json"}
# 说明：**源码库里没有**这些文件（`git ls-files` 为空），它们是程序首次运行时
# 自己生成的默认配置/状态。所以两边不同属正常，绝不能当同步失败。
# （曾误把 plugins/web_operator/data/config.json 当源码项，实测源码库是 `{}` 空壳、
#   运行副本是完整配置 → 判定为运行时状态，已改正。）

OK = "  [OK]  "
BAD = "  [!!]  "
INFO = "  [--]  "
SKIP = "  [..]  "


def _git_tracked(paths=None):
    """返回源码库中被 git 跟踪的文件集合（相对路径，正斜杠）。

    这是判定"是源码还是运行时状态"的**权威依据**：
      源码库里没被 git 跟踪 → 它是程序运行时自己生成的，不参与同步校验。
    """
    try:
        r = subprocess.run(["git", "ls-files"], cwd=SRC_ROOT,
                           capture_output=True, timeout=60)
        out = r.stdout.decode("utf-8", errors="replace")
        return {line.strip().replace("/", os.sep) for line in out.splitlines() if line.strip()}
    except Exception:
        return None


def _is_runtime_artifact(rel_path, tracked=None):
    """判断某文件是否是"运行时产生的状态"（两边不同属正常）。

    优先级：git 跟踪状态（权威）> 路径/扩展名启发式。
    """
    rel = os.path.normpath(rel_path)
    if tracked is not None:
        # git 说它是被跟踪的源码 → 必须一致；否则是运行时产物
        return rel.replace("/", os.sep) not in tracked
    norm = rel.replace("/", os.sep)
    parts = norm.split(os.sep)
    # 路径中出现运行时刻录的目录名（如 plugins/ego/xxx/data/...）
    if any(p in RUNTIME_SKIP_DIRS for p in parts[:-1]):
        return True
    ext = os.path.splitext(norm)[1].lower()
    if ext in RUNTIME_SKIP_EXTS:
        return True
    if parts[-1] in RUNTIME_SKIP_NAMES:
        return True
    return False


def _md5(path):
    try:
        with open(path, "rb") as f:
            return hashlib.md5(f.read()).hexdigest()
    except Exception:
        return None


def _say(msg):
    print(msg, flush=True)


def find_dist(explicit=None):
    """定位发行包根目录（含 resources\\app\\webui\\webui_server.py）。"""
    cands = []
    if explicit:
        cands.append(explicit)
    cands += [DEFAULT_DIST,
              r"E:\EggBloomDist",
              os.path.join(os.path.expanduser("~"), "EggBloom Desktop-new")]
    for c in cands:
        if c and os.path.isfile(os.path.join(c, "resources", "app", "webui", "webui_server.py")):
            return os.path.abspath(c)
    return None


def step_sync(dist, dry=False):
    """第 1 段：源码库 → 发行目录（调用项目自带 sync-dist.ps1）。"""
    script = os.path.join(SRC_ROOT, "scripts", "sync-dist.ps1")
    if not os.path.isfile(script):
        _say(BAD + f"未找到同步脚本: {script}")
        return False
    cmd = ["powershell", "-ExecutionPolicy", "Bypass", "-File", script,
           "-DistPath", dist]
    if dry:
        cmd.append("-DryRun")
    _say(INFO + "执行同步: " + " ".join(cmd[:4]) + " ...")
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=600)
        out = (r.stdout or b"")
        for enc in ("utf-16", "utf-8", "gbk"):
            try:
                text = out.decode(enc)
                break
            except Exception:
                continue
        else:
            text = out.decode("utf-8", errors="replace")
        # 只摘关键行，避免刷屏
        for line in text.splitlines():
            s = line.strip()
            if not s:
                continue
            if any(k in s for k in ("VERSION", "失败", "错误", "汇总", "commit",
                                    "已复制文件", "需处理")):
                _say("      " + s)
        return r.returncode == 0
    except Exception as e:
        _say(BAD + f"同步失败: {e}")
        return False


def scan_pyc(dist):
    """第 2 段（**只检测、不删除**）：找出可能让新代码不生效的陈旧 .pyc。

    为什么只报告不删除
        - 发行目录里有 100+ 个 .pyc，批量删除是危险的破坏性操作；
        - 更重要的是：**Python 的 .pyc 校验基于源文件的 mtime+size**，
          只要 .py 被 sync 更新过，mtime 变了 → 旧 .pyc 自动失效 → 本来就会重编译。
        - 真正会出问题的只有极少数情况：.py 被**回退**成 mtime/size 完全相同的内容
          （例如用 git checkout 恢复到较早版本，且同一秒内）。所以这里做"体检"，
          把可疑项列出来，由**你**决定要不要删。

    返回 (stale_list, total)
    """
    import struct
    app = os.path.join(dist, "resources", "app")
    total = 0
    stale = []
    for root, dirs, files in os.walk(app):
        if "__pycache__" not in dirs:
            continue
        pyc_dir = os.path.join(root, "__pycache__")
        parent = root
        for fn in files:
            if not fn.endswith(".pyc"):
                continue
            total += 1
            # 从 .pyc 头读源 mtime/size（PEP 552 时间戳模式，前 16 字节）
            mod = fn.split(".")[0]
            src = os.path.join(parent, mod + ".py")
            if not os.path.isfile(src):
                continue
            try:
                with open(os.path.join(pyc_dir, fn), "rb") as f:
                    head = f.read(16)
                magic, flags, mtime, size = struct.unpack("<4sIII", head)
            except Exception:
                stale.append(os.path.relpath(os.path.join(pyc_dir, fn), app))
                continue
            st = os.stat(src)
            if int(st.st_mtime) != mtime or int(st.st_size) != size:
                stale.append(os.path.relpath(os.path.join(pyc_dir, fn), app))
    return stale, total


def step_report_pyc(dist):
    stale, total = scan_pyc(dist)
    _say(INFO + f"发现 .pyc 共 {total} 个")
    if not stale:
        _say(OK + "没有陈旧 .pyc（源码 mtime/size 都变过 → Python 会自动重编译）")
        return True
    _say(BAD + f"有 {len(stale)} 个 .pyc 与源码 mtime/size 不匹配"
              f"（正常来说 Python 会自动重编译，**不影响生效**）：")
    for n in stale[:10]:
        _say("        · " + n)
    if len(stale) > 10:
        _say(f"        ... 其余 {len(stale) - 10} 个")
    _say(INFO + "如需彻底强制重编译，可手动删除上面这些 .pyc（本脚本不会替你删）")
    return True


def step_verify(dist):
    """第 3 段：校验「发行目录 == 源码库」，并给出**具体证据**。"""
    src_app = SRC_ROOT
    dst_app = os.path.join(dist, "resources", "app")
    all_ok = True
    checked = 0

    # git 跟踪清单 = 判定"源码 vs 运行时状态"的权威依据
    tracked = _git_tracked()
    if tracked is None:
        _say(INFO + "读不到 git 跟踪清单 → 用路径/扩展名启发式判定运行时文件")
    else:
        _say(INFO + f"git 跟踪 {len(tracked)} 个文件（作为「是否源码」的判据）")

    # 根目录文件
    for f in ROOT_FILES:
        a = _md5(os.path.join(src_app, f))
        b = _md5(os.path.join(dst_app, f))
        if a is None:
            continue
        checked += 1
        if a == b:
            _say(OK + f"{f}  MD5 一致")
        else:
            all_ok = False
            _say(BAD + f"{f}  MD5 **不一致** → 未同步（src={a[:8]} dst={b[:8] if b else '缺失'})")

    # 目录项：逐文件比对（只看源码库里存在、且不是运行时状态的文件）
    for d in DIR_ITEMS:
        src_d = os.path.join(src_app, d)
        if not os.path.isdir(src_d):
            continue
        diff = missing = skipped = 0
        names = []
        for root, _dirs, files in os.walk(src_d):
            if "__pycache__" in root:
                continue
            for fn in files:
                if fn.endswith(".pyc"):
                    continue
                sp = os.path.join(root, fn)
                rel = os.path.relpath(sp, src_app)
                if _is_runtime_artifact(rel, tracked):
                    skipped += 1
                    continue
                dp = os.path.join(dst_app, rel)
                if not os.path.isfile(dp):
                    missing += 1
                    names.append(rel)
                    continue
                if _md5(sp) != _md5(dp):
                    diff += 1
                    names.append(rel)
        checked += 1
        if diff == 0 and missing == 0:
            extra = f"（跳过 {skipped} 个运行时数据文件）" if skipped else ""
            _say(OK + f"{d}/  全部一致{extra}")
        else:
            all_ok = False
            _say(BAD + f"{d}/  不一致 {diff} 个 / 缺失 {missing} 个"
                      + (f"（另跳过 {skipped} 个运行时数据文件）" if skipped else ""))
            for n in names[:8]:
                _say("        · " + n)

    # VERSION
    vf = os.path.join(dist, "VERSION")
    commit = ""
    try:
        txt = open(vf, encoding="utf-8", errors="replace").read()
        m = re.search(r"source_commit=(\S+)", txt)
        commit = m.group(1) if m else "?"
    except Exception:
        commit = "(读不到)"
    _say(INFO + f"发行包 VERSION: source_commit={commit}")

    # 源码库当前 HEAD（对不上就说明同步落后了）
    try:
        r = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                           cwd=SRC_ROOT, capture_output=True, timeout=30)
        head = r.stdout.decode("utf-8", errors="replace").strip()
        if head and commit and head != commit:
            all_ok = False
            _say(BAD + f"源码库 HEAD={head} ≠ 发行包 commit={commit} → **同步落后，请重跑同步**")
        elif head:
            _say(OK + f"源码库 HEAD={head} 与发行包一致")
    except Exception:
        pass

    _say(INFO + f"共校验 {checked} 项")
    return all_ok


def step_restart_hint(dist):
    """必须在最后提醒：进程不重启，一切白做。"""
    exe = os.path.join(dist, "EggBloom Desktop.exe")
    bat = os.path.join(dist, "start.bat")
    _say("")
    _say("  ── 最后一步（必须人工做）：让新代码被加载 ──")
    _say("     后端是独立 python 进程，**不重启就不会用新代码**。")
    _say("     1) 关掉正在运行的后端（那个 'EggBloom-WebUI' 命令行窗口，或任务管理器里的 python）")
    if os.path.isfile(bat):
        _say(f"     2) 重新运行: {bat}")
    if os.path.isfile(exe):
        _say(f"        （它会拉起后端并打开 {exe}）")
    _say("     3) 确认 http://localhost:8080 能打开 → 新代码已生效")


def main():
    ap = argparse.ArgumentParser(description="让源码改动生效并验证")
    ap.add_argument("--dist", default=None, help="发行包根目录")
    ap.add_argument("--check-only", action="store_true", help="只校验")
    ap.add_argument("--no-sync", action="store_true", help="跳过同步")
    ap.add_argument("--dry-run", action="store_true", help="同步用 DryRun")
    args = ap.parse_args()

    dist = find_dist(args.dist)
    if not dist:
        _say(BAD + "找不到发行包目录。请用 --dist 指定（需含 resources\\app\\webui\\webui_server.py）")
        return 2

    _say("=" * 64)
    _say("  EggBloom 生效流程")
    _say(f"  源码库  : {SRC_ROOT}")
    _say(f"  发行包  : {dist}")
    _say("=" * 64)

    ok = True
    if not args.check_only and not args.no_sync:
        _say("\n[1/3] 同步 源码库 → 发行目录")
        if not step_sync(dist, dry=args.dry_run):
            ok = False

    if not args.check_only:
        _say("\n[2/3] 体检 __pycache__（只报告，不删除——实测不需要清）")
        step_report_pyc(dist)

    _say("\n[3/3] 校验 发行目录 == 源码库")
    if not step_verify(dist):
        ok = False

    if not args.check_only:
        step_restart_hint(dist)

    _say("")
    _say("=" * 64)
    _say("  结果: " + ("[OK] 一致，可以重启生效" if ok else "[!!] 有不一致项，见上面 [!!] 行"))
    _say("=" * 64)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
