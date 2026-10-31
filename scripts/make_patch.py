# -*- coding: utf-8 -*-
"""scripts/make_patch.py —— 生成热补丁（增量 zip + latest.json）

给 `harness/updater.py` 用。用户端只下几 MB，不用重下 200MB 整包。

用法：
    python scripts/make_patch.py --version 1.5.11             # 自上个 tag 起的改动
    python scripts/make_patch.py --since v1.5.10 --version 1.5.11
    python scripts/make_patch.py --all --version 1.5.11       # 全量白名单文件
    python scripts/make_patch.py --dry-run

产物（默认 dist/）：
    patch-<ver>.zip   只含白名单路径（代码/页面/评测），相对 resources\\app
    latest.json       {version, sha256, size, url, notes, files}

注意：
- `url` 是**发布后**的公网地址，默认留空，发布时用 `--url` 指定；
- 白名单与拒绝名单**复用 updater.py**，两边不会分叉。
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from harness.updater import is_allowed  # noqa: E402


def _git(*args) -> str:
    return subprocess.run(["git"] + list(args), cwd=ROOT, capture_output=True,
                          text=True, encoding="utf-8", errors="replace").stdout.strip()


def _last_tag() -> str:
    tags = _git("tag", "--sort=-creatordate").splitlines()
    return tags[0] if tags else ""


def changed_files(since: str) -> list:
    if not since:
        return []
    out = _git("diff", "--name-only", f"{since}..HEAD")
    return [ln.strip() for ln in out.splitlines() if ln.strip()]


def all_allowed_files() -> list:
    files = []
    for dirpath, dirs, names in os.walk(ROOT):
        rel_dir = os.path.relpath(dirpath, ROOT).replace("\\", "/")
        if rel_dir == ".":
            rel_dir = ""
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in
                   ("data", "logs", "chat_logs", "memes", "image_cache",
                    "private_memory", "self_learning_db", "personality_versions",
                    "node_modules", "__pycache__", "tests", "dist", "docs")]
        for n in names:
            rel = f"{rel_dir}/{n}" if rel_dir else n
            if is_allowed(rel):
                files.append(rel)
    return files


def build(files, out_dir, version, url, notes, dry_run=False) -> dict:
    files = sorted(set(f for f in files if is_allowed(f) and os.path.isfile(os.path.join(ROOT, f))))
    os.makedirs(out_dir, exist_ok=True)
    zip_path = os.path.join(out_dir, f"patch-{version}.zip")
    if dry_run:
        print(f"[dry-run] {len(files)} 个文件 → {zip_path}")
        for f in files[:20]:
            print("   ", f)
        if len(files) > 20:
            print(f"    … 还有 {len(files) - 20} 个")
        return {"files": len(files), "zip": zip_path, "dry_run": True}
    tmp = zip_path + ".new"
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for rel in files:
            zf.write(os.path.join(ROOT, rel), rel.replace("\\", "/"))
    os.replace(tmp, zip_path)
    h = hashlib.sha256()
    with open(zip_path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    man = {
        "version": version,
        "sha256": h.hexdigest(),
        "size": os.path.getsize(zip_path),
        "url": url,
        "notes": notes,
        "files": files,
        "built_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    with open(os.path.join(out_dir, "latest.json"), "w", encoding="utf-8") as f:
        json.dump(man, f, ensure_ascii=False, indent=2)
    return {"files": len(files), "zip": zip_path, "sha256": h.hexdigest(),
            "size": man["size"], "manifest": os.path.join(out_dir, "latest.json")}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="生成 EggBloom 热补丁")
    ap.add_argument("--since", default="", help="git 起点（默认上一个 tag）")
    ap.add_argument("--all", action="store_true", help="全量白名单文件")
    ap.add_argument("--version", default="", help="版本号（默认取 VERSION）")
    ap.add_argument("--url", default="", help="补丁的公网下载地址（写进 manifest）")
    ap.add_argument("--notes", default="", help="更新说明")
    ap.add_argument("--out", default=os.path.join(ROOT, "dist"), help="输出目录")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)

    version = a.version
    if not version:
        try:
            with open(os.path.join(os.path.dirname(ROOT), "VERSION"), encoding="utf-8") as f:
                for line in f:
                    if line.strip().lower().startswith("release"):
                        version = line.split("=", 1)[1].strip()
        except OSError:
            version = ""
    version = version or time.strftime("%Y%m%d")
    if a.all:
        files = all_allowed_files()
    else:
        since = a.since or _last_tag()
        files = changed_files(since)
        if not files:
            print(f"自 {since or '(无 tag)'} 起没有改动；用 --all 生成全量白名单补丁")
            return 1
    r = build(files, a.out, version, a.url, a.notes, dry_run=a.dry_run)
    print(json.dumps(r, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
