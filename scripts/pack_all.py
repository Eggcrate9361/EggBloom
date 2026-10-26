# -*- coding: utf-8 -*-
"""scripts/pack_all.py —— 一键重建三个发行包（Lite / Standard / Full）

对应文档：docs/发布打包.md。把那份"手工步骤 + 三个坑"变成一条命令：

  python scripts/pack_all.py                    # 同步 + 修剪 + 重建 Lite + 压缩 + 校验
  python scripts/pack_all.py --skip-sync        # 暂存目录已同步过，只重建 Lite 与重压缩
  python scripts/pack_all.py --only lite        # 只做一个变体（调试用）
  python scripts/pack_all.py --dry-run          # 只打印计划，不动文件

它在文档基础上补掉的三件事（都是踩过的）：
  1) **不用管道喂 tar**：Windows PowerShell 会把原生命令的输出按文本重编码 →
     必须先 `git archive -o <tmp.zip>` 再 tar 解包，否则二进制被损坏；
  2) **7z 先写 .zip.new 再替换**：7z 对已存在的 zip 是"增量添加"，不重建 → 旧条目
     会永远留在包里；
  3) **Lite 的 VERSION 要手写**：VERSION 不是 tracked 文件，git archive 带不出来，
     而包的"版本真相"就写在它里面（release / source_commit / sync_date）。

★ 顺序铁律：改版本 → commit → 打包。打包后再提交，包立刻落后一个 commit，
  `scripts/activate.py --check-only` 会报「同步落后」。
"""
import argparse
import hashlib
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import time
import zipfile

VARIANTS = [("lite", "EggBloom-Lite"), ("standard", "EggBloom-Standard"), ("full", "EggBloom-Full")]
# Standard 里 memes 只留代码（图片库是 Full 的卖点，Standard 用户就是为了不带它）。
# 2026-10-18 改为**压缩期排除**：暂存树不再删文件 —— 递归删几百个文件会被本机安全
# 策略拦下（SAFE_DELETE_BULK_CONFIRM_REQUIRED），而且暂存树本来就会重新同步回来。
STANDARD_MEMES_EXCLUDE = ("kagura_nana", "vrchat", "memes/kagura", "memes/vrchat",
                          "memes/zhenxun")
#: 7z 排除：.pyc 是运行时缓存，打进去既涨体积又把绝对路径带进包里
SEVENZ_EXCLUDES = ["-xr!__pycache__", "-xr!*.pyc",
                   # 打包时被人为改名让路的旧树（如 app.old-1）绝不能进包：
                   # 2026-10-18 它们躺在包根下，把 Standard 从 213MB 顶到 259MB。
                   "-xr!*.old", "-xr!*.old-*"]



def log(*a):
    print(*a, flush=True)


#: 暂存树里允许存在的"运行时"内容（它们本就不在源码库里）
RUNTIME_DIRS = ("data", "logs", "chat_logs", "personality", "personality_versions",
                "private_memory", "self_learning_db", "image_cache",
                "__pycache__", ".neurobase", "models")
RUNTIME_FILES = ("bot_config.json", "plugin_state.json", "last_port.txt", "secret.json",
                 "local_config.json", "machine_config.json",
                 "slang_dict.json", "slang_replies.json", "slang_conflicts.json")

#: 包根必须存在的许可文件（相对 app 根）。缺一个就拒绝出包 —— MIT 要求
#: "版权声明与许可声明随所有副本"，但 sync-dist 只按扩展名搬根文件，LICENSE 没有扩展名。
LICENSE_FILES = ("LICENSE", "THIRD_PARTY.md",
                 "licenses/LiGGUF.txt", "licenses/bge-small-zh-v1.5.txt",
                 "licenses/README.md")
LICENSE_ROOT_FILES = ("LICENSE", "THIRD_PARTY.md")


def stage_licenses(app_dir: pathlib.Path, repo: pathlib.Path) -> list:
    """把许可文件复制进暂存树，返回缺失项（空列表 = 通过）。"""
    app_dir.mkdir(parents=True, exist_ok=True)
    for name in LICENSE_ROOT_FILES:
        src = repo / name
        if src.is_file():
            shutil.copy2(src, app_dir / name)
    src_dir = repo / "licenses"
    if src_dir.is_dir():
        shutil.copytree(src_dir, app_dir / "licenses", dirs_exist_ok=True)
    return [n for n in LICENSE_FILES if not (app_dir / n).is_file()]


def _is_runtime(rel):
    parts = rel.replace("\\", "/").split("/")
    if any(p in RUNTIME_DIRS for p in parts[:-1]):
        return True
    if parts[-1] in RUNTIME_FILES or parts[-1].endswith((".pyc", ".log")):
        return True
    # 表情包图片与运行时 tags.json（Standard 还会在压缩期再排除一次）
    if parts[0] == "memes" and len(parts) > 1 and not parts[-1].endswith(".py"):
        return True
    return False


def audit_stale(app_dir, repo):
    """列出「暂存树有、源码库没有」的文件。

    sync-dist 只增不删（防止误删用户运行态），于是布局变更前的旧文件会一直
    留在暂存树里被打进包 —— 2026-10-18 就因此把 8 月那份带真实 QQ 号的
    webui_artistic_v3.html 带进了 zip。这里做硬闸：发现陈旧文件就不出包。
    """
    import subprocess
    tracked = set(subprocess.run(
        ["git", "-c", "core.quotepath=false", "ls-files"],
        capture_output=True, cwd=str(repo)).stdout.decode("utf-8").splitlines())
    stale = []
    for root, _, files in os.walk(app_dir):
        for f in files:
            full = os.path.join(root, f)
            rel = os.path.relpath(full, app_dir)
            if _is_runtime(rel):
                continue
            if rel.replace("\\", "/") not in tracked:
                stale.append(rel)
    return stale


def md5_bytes(b):
    return hashlib.md5(b).hexdigest()


def md5_file(p, chunk=1 << 20):
    h = hashlib.md5()
    with open(p, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def run(cmd, cwd=None, timeout=3600, quiet_tail=6):
    log("  $", " ".join(str(x) for x in cmd))
    r = subprocess.run([str(x) for x in cmd], cwd=str(cwd) if cwd else None,
                       capture_output=True, timeout=timeout)
    out = (r.stdout or b"").decode("utf-8", "replace") + (r.stderr or b"").decode("utf-8", "replace")
    tail = "\n".join(out.strip().splitlines()[-quiet_tail:])
    if tail:
        log("    " + tail.replace("\n", "\n    "))
    return r.returncode, out


def dir_size_mb(p):
    if not p.exists():
        return 0.0
    tot = 0
    for r, _d, f in os.walk(p):
        for x in f:
            try:
                tot += os.path.getsize(os.path.join(r, x))
            except OSError:
                pass
    return round(tot / 1048576, 1)


def main():
    # Windows 控制台默认 GBK：日志里的 ⚠ 与中文会抛 UnicodeEncodeError（打包直接中断）
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=r"D:\git\EggBloom", help="源码库根")
    ap.add_argument("--base", default=r"E:\EggBloom水星", help="发行工作区（含 build/ 与 dist/）")
    ap.add_argument("--7z", dest="sevenz", default=r"C:\Program Files\7-Zip\7z.exe")
    ap.add_argument("--only", default="", help="只打包某个变体：lite / standard / full")
    ap.add_argument("--skip-sync", action="store_true", help="跳过 Standard/Full 的源码同步")
    ap.add_argument("--meme-src", default=r"E://EggBloom水星//EggBloom Desktop-new//resources//app//memes",
                    help="本机完整表情包库（Standard 取样用）")
    ap.add_argument("--meme-count", type=int, default=20, help="Standard 附带表情包张数")
    ap.add_argument("--allow-stale", action="store_true",
                    help="暂存树里有陈旧文件时仍然出包（默认硬闸拦下）")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    repo = pathlib.Path(args.repo)
    build, dist = pathlib.Path(args.base) / "build", pathlib.Path(args.base) / "dist"
    sevenz = pathlib.Path(args.sevenz)
    only = {args.only} if args.only else None

    if not (repo / "package.json").exists():
        log("!! --repo 不是 EggBloom 源码库:", repo); return 2
    if not sevenz.exists():
        log("!! 找不到 7-Zip:", sevenz, "（用 --7z 指定）"); return 2

    ver = json.loads((repo / "package.json").read_text(encoding="utf-8"))["version"]
    commit = subprocess.run(["git", "-C", str(repo), "rev-parse", "--short", "HEAD"],
                            capture_output=True).stdout.decode().strip()
    # 只看"已跟踪文件被改动"：未跟踪的运行态（表情包图片库 / slang_dict.json / data/ 等）
    # 是**应该**进包的（Full 的就是它们），不算工作区脏。
    dirty = subprocess.run(["git", "-C", str(repo), "status", "--porcelain", "--untracked-files=no"],
                           capture_output=True).stdout.decode().strip()
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    log(f"=== EggBloom 打包 ===  release={ver}  commit={commit}  {stamp}")
    if dirty:
        log("⚠ 有未提交的跟踪文件改动 → 包内 source_commit 会对不上（文档 §4(2)）：")
        for line in dirty.splitlines()[:6]:
            log("   " + line)
        log("   建议：先 commit（改版本 → commit → 打包，顺序不能反）")
    if args.dry_run:
        log("[dry-run] 计划：", [f"{n}→{dist/(f+'.zip')}" for n, f in VARIANTS if not only or n in only])
        return 0

    results = {}

    # ── 1) Standard / Full：源码 → 暂存（robocopy 合并式，只增不删）───────────
    if not args.skip_sync:
        for name in ("standard", "full"):
            if only and name not in only:
                continue
            root = build / name / f"EggBloom-{name.capitalize()}"
            log(f"\n[1] 同步 {name} → {root}")
            if not (root / "resources" / "app" / "webui" / "webui_server.py").exists():
                log("  !! 不是有效发行根（缺 resources/app/webui/webui_server.py），跳过")
                continue
            run(["powershell", "-ExecutionPolicy", "Bypass", "-File",
                 repo / "scripts" / "sync-dist.ps1", "-DistPath", root])

    # ── 1b) 许可文件：补复制 + 硬校验（缺项直接退出，不压缩）────────────
    for name in ("standard", "full"):
        if only and name not in only:
            continue
        app = build / name / f"EggBloom-{name.capitalize()}" / "resources" / "app"
        if not app.exists():
            continue
        missing = stage_licenses(app, repo)
        if missing:
            log(f"  !! {name}: 缺许可文件 {missing} → 拒绝出包")
            return 4
        log(f"  [lic] {name}: LICENSE / THIRD_PARTY.md / licenses 齐")

    # ── 2) Standard 修剪 memes ──────────────────────────────────────────────
    if not only or "standard" in only:
        log("\n[2] Standard：挑一小把表情包（默认 20 张 / ≤3MB，文件名不带 QQ 号）")
        app = build / "standard" / "EggBloom-Standard" / "resources" / "app"
        if os.path.isdir(args.meme_src):
            rc, _ = run([sys.executable, str(repo / "scripts" / "make_meme_sample.py"),
                         "--src", args.meme_src,
                         "--dst", str(app / "memes" / "memes"),
                         "--count", str(args.meme_count)])
            if rc != 0:
                log("  !! 表情包取样失败 → Standard 仍会出包，但只有表情包代码")
        else:
            log(f"  !! 本机表情包库不存在：{args.meme_src}（Standard 将只有表情包代码）")

    # ── 3) Lite：git archive 重建（只含 tracked 文件）+ 手写 VERSION ─────────
    if not only or "lite" in only:
        lite_root = build / "lite" / "EggBloom-Lite"
        log(f"\n[3] Lite 重建 → {lite_root}")
        if lite_root.exists():
            # ★ 改名让路，不用 shutil.rmtree（2026-10-26）。
            #   本机安全策略对"单次批量删除 >50 个文件"直接拦死：
            #   `[safe-delete][SAFE_DELETE_BULK_CONFIRM_REQUIRED] {"count":441,...}`
            #   —— 每重建一次 Lite 都会被拦，打包停在第三步。
            #   rename 是原子的、非破坏性的，同样能达到"给新树腾位置"的效果；
            #   `SEVENZ_EXCLUDES` 里的 `-xr!*.old-*` 本来就把这类目录挡在包外。
            #   旧目录留在 build/lite/ 下不自动清理（要占地就自己删）。
            _aside = build / "lite" / f"EggBloom-Lite.old-{time.strftime('%Y%m%d%H%M%S')}"
            try:
                lite_root.rename(_aside)
                log(f"  旧树改名让路 → {_aside.name}")
            except Exception as e:
                log(f"  !! 旧树改名失败（{e}）→ 继续在原目录上覆盖")
        lite_root.mkdir(parents=True, exist_ok=True)
        tmp_zip = build / "lite" / "_head.zip"
        tmp_zip.unlink(missing_ok=True)
        rc, _ = run(["git", "-C", repo, "archive", "-o", tmp_zip, "HEAD"])
        if rc != 0:
            log("  !! git archive 失败"); return 3
        run(["C:\\Windows\\System32\\tar.exe", "-xf", tmp_zip, "-C", lite_root])
        tmp_zip.unlink(missing_ok=True)
        (lite_root / "VERSION").write_text(
            "# EggBloom Desktop 发行版\n"
            "# 此文件标记当前发行版对应的源码版本\n"
            "# 由发布打包流程写入（docs/发布打包.md §2）\n\n"
            f"release={ver}\n"
            f"source_repo={repo}\n"
            f"source_commit={commit}\n"
            f"sync_date={stamp}\n",
            encoding="utf-8", newline="\n")
        missing = stage_licenses(lite_root, repo)
        if missing:
            log(f"  !! lite: 缺许可文件 {missing} → 拒绝出包")
            return 4
        log("  文件数 =", sum(len(f) for _r, _d, f in os.walk(lite_root)))

    # ── 4) 压缩 → .zip.new → 校验 → 替换 ───────────────────────────────────
    log("\n[4] 7-Zip 压缩")
    for name, folder in VARIANTS:
        if only and name not in only:
            continue
        parent, src = build / name, build / name / folder
        out_new, out_final = dist / f"{folder}.zip.new", dist / f"{folder}.zip"
        if not src.exists():
            log(f"  !! {src} 不存在，跳过")
            continue
        out_new.unlink(missing_ok=True)
        before, t0 = dir_size_mb(src), time.time()
        # 硬闸：Standard/Full 的唯一启动入口是桌面壳 exe，缺了就不该出包
        if name in ("standard", "full") and not any(
                (src / exe).is_file() for exe in ("EggBloom Desktop.exe", "EggBloom.exe")):
            log(f"  !! {folder} 缺桌面壳 exe → 跳过（用户将没有可双击的入口）")
            results[folder] = {"zip_mb": 0, "src_mb": before, "entries": 0, "ok": False}
            continue
        if name == "standard":
            tj = src / "resources" / "app" / "memes" / "memes" / "tags.json"
            try:
                db = json.load(open(tj, encoding="utf-8"))
            except Exception:
                db = {}
            bad = [k for k in db if re.search(r"\d{6,}", str(k))]
            if not db:
                log(f"  !! {folder} 的 memes/tags.json 缺失或为空 → 跳过")
                results[folder] = {"zip_mb": 0, "src_mb": before, "entries": 0, "ok": False}
                continue
            if bad or len(db) > 60:
                log(f"  !! {folder} 的 tags.json 有 {len(bad)} 条带疑似 QQ 号 / 共 {len(db)} 条（应为 ≤60）→ 跳过")
                results[folder] = {"zip_mb": 0, "src_mb": before, "entries": 0, "ok": False}
                continue

        stale = audit_stale(src / "resources" / "app", repo)
        if stale:
            log(f"  !! 陈旧文件 {len(stale)} 个（暂存树有、源码库没有）:")
            for r_ in stale[:10]:
                log(f"       · {r_}")
            if not args.allow_stale:
                log("     → 拒绝出包。先清掉它们，或加 --allow-stale 强行打。")
                results[folder] = {"zip_mb": 0, "src_mb": before, "entries": 0, "ok": False}
                continue

        excludes = list(SEVENZ_EXCLUDES)
        if name == "standard":
            for sub in STANDARD_MEMES_EXCLUDE:
                excludes.append(f"-xr!{folder}/resources/app/memes/{sub}")
            log("  排除图库:", len(STANDARD_MEMES_EXCLUDE), "项")
        rc, _ = run([sevenz, "a", "-tzip", "-mx=5", out_new, folder, "-y"] + excludes,
                    cwd=parent)
        ok, why = True, ""
        try:
            with zipfile.ZipFile(out_new) as z:
                names = z.namelist()
                tops = sorted({n.split("/")[0] for n in names})
                if z.testzip() is not None:
                    ok, why = False, "zip 完整性校验失败"
                if tops != [folder]:
                    ok, why = False, f"顶层目录异常 {tops}"
        except Exception as e:
            ok, why = False, f"无法读取 zip：{e}"
        size = round(out_new.stat().st_size / 1048576, 1)
        if rc != 0 or size < 1:
            ok, why = False, f"7z 退出码 {rc} / 体积异常 {size}MB"
        log(f"  {folder}: 源 {before}MB → zip {size}MB · {len(names)} 条目 · 顶层 {tops} · "
            f"{'OK' if ok else 'BAD:' + why} · {time.time()-t0:.0f}s")
        results[folder] = {"zip_mb": size, "src_mb": before, "entries": len(names), "ok": ok}
        if ok:
            shutil.move(str(out_new), str(out_final))
            log(f"  → 已替换 {out_final}")
        else:
            log("  !! 不替换旧包（保留 .zip.new 供排查）")

    # ── 5) 抽检：包内关键文件 == 源码（文本按忽略行尾比较）─────────────────
    log("\n[5] 抽检包内关键文件")
    norm = lambda b: b.replace(b"\r\n", b"\n")
    checks = [
        ("EggBloom-Lite", "bot_core.py", repo / "bot_core.py"),
        ("EggBloom-Lite", "README.md", repo / "README.md"),
        ("EggBloom-Standard", "EggBloom Desktop.exe", None),
        ("EggBloom-Standard", "resources/app/webui/webui_server.py", repo / "webui" / "webui_server.py"),
        ("EggBloom-Standard", "resources/app/webui/webui_artistic_v4.html", repo / "webui" / "webui_artistic_v4.html"),
        ("EggBloom-Full", "resources/app/bot_core.py", repo / "bot_core.py"),
    ]
    for folder, inner, src in checks:
        zp = dist / f"{folder}.zip"
        if not zp.exists():
            log(f"  {folder}: 包不存在"); continue
        try:
            with zipfile.ZipFile(zp) as z:
                data = z.read(f"{folder}/{inner}")
        except KeyError:
            log(f"  [MISS] {folder} / {inner}"); continue
        same = (norm(data) == norm(src.read_bytes())) if (src and src.exists()) else None
        log(f"  [{'OK' if same else '不一致!' if same is False else '(仅验存在)' if not src else '(源码缺失)'}] {folder} / {inner}")

    # ── 6) 汇总 ─────────────────────────────────────────────────────────────
    log("\n=== 汇总 ===")
    log(json.dumps({"release": ver, "commit": commit, "packages": results}, ensure_ascii=False, indent=1))
    for _n, folder in VARIANTS:
        zp = dist / f"{folder}.zip"
        if zp.exists():
            log(f"  {folder}.zip = {round(zp.stat().st_size/1048576,1)} MB")
    log("\n提醒：包内 VERSION 记的是 %s；此后不要再提交，否则包会「落后一个 commit」。" % commit)
    return 0


if __name__ == "__main__":
    sys.exit(main())
