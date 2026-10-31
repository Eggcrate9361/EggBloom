# -*- coding: utf-8 -*-
"""harness/updater.py —— 终端用户的热补丁更新（2026-11-01）

## 背景

EggBloom Desktop 是便携目录：`EggBloom Desktop.exe` + `resources\\app`（Python 侧）。
在此之前**没有任何更新通道** —— 用户拿到新版本只能整包重下、手工覆盖。

整包有 200MB+，而绝大多数版本只是改了几十个 `.py` / `.html`。
所以这里做**增量热补丁**：只替换代码文件，数据目录一个字节都不碰。

## 设计（照着"三层更新"里能热替换的那两层来）

```
发布侧  scripts/make_patch.py  →  dist/patch-<ver>.zip + latest.json（sha256）
用户侧  updater.check()        →  比对本地 VERSION 与 manifest
        updater.apply()        →  校验 sha256 → 白名单校验 → 备份 → 覆盖
        重启                   →  webui_server 由 Electron 守护，退出即重启
```

## 铁律（写死在代码里，不靠调用方自觉）

1. 只覆盖**白名单路径**（代码/页面/评测），拒绝 `..`、绝对路径、盘符、符号链接；
2. 目标文件必须先备份到 `data/update-backup/<时间戳>/`，出问题能原样回滚；
3. **绝不删除**目标端任何文件（只覆盖 + 新增），数据目录天然安全；
4. sha256 对不上直接拒绝，不写盘；
5. 单文件/总大小有上限，防止 manifest 被换成"塞满磁盘"的东西。

CLI：
    python -m harness.updater --check                 # 只看有没有新版
    python -m harness.updater --check --url <manifest>
    python -m harness.updater --apply patch.zip --manifest latest.json
    python -m harness.updater --rollback <备份目录>
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import time
import urllib.request
import zipfile

#: 补丁允许覆盖的路径前缀（相对 `resources\\app`）
ALLOW_PREFIXES = ("harness/", "plugins/", "webui/", "evals/", "skills/", "protocols/")
#: 根目录允许的独立文件（顶层模块）
ALLOW_ROOT_PY = True
#: 无论如何都不许碰的（数据 / 配置 / 缓存），纵深防御
DENY_PARTS = ("data/", "logs/", "chat_logs/", "memes/", "image_cache/",
              "private_memory/", "self_learning_db/", "personality_versions/",
              ".neurobase/", "node_modules/", "__pycache__/", "tests/")
DENY_NAMES = ("bot_config.json", ".env", "bot_config.json.example",
              "providers.json", "plugin_state.json", "last_port.txt", "VERSION")
#: webui 下这些是用户数据/构建产物，不进补丁
DENY_WEBUI = ("webui/providers.json",)

MAX_ENTRY_BYTES = 8 * 1024 * 1024      # 单文件 8MB
MAX_TOTAL_BYTES = 60 * 1024 * 1024     # 整包 60MB
MAX_ENTRIES = 4000


def app_root() -> str:
    """`resources\\app` 根目录（本文件在 app/harness/ 下）。"""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read_version(root: str) -> str:
    """本地版本号：优先 VERSION 文件，退回 config 里的常量。

    VERSION 的位置随发行方式不同（源码树在项目根，发行包在 `resources\\` 的上一级），
    所以两个候选都看。
    """
    cands = [
        os.path.join(os.path.dirname(root), "VERSION"),                 # resources/VERSION
        os.path.join(os.path.dirname(os.path.dirname(root)), "VERSION"),  # 安装根/VERSION
    ]
    for p in cands:
        try:
            with open(p, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip().lower().startswith("release"):
                        return line.split("=", 1)[1].strip()
        except OSError:
            continue
    try:
        sys.path.insert(0, root)
        import config as _c  # noqa: WPS433
        return str(getattr(_c, "APP_VERSION", "") or "")
    except Exception:
        return ""


def _norm(name: str) -> str:
    # 不在这里 lstrip("./") —— 那会把 "../evil.py" 洗成 "evil.py"（单测抓到的真 bug）。
    # 只做分隔符统一与去空白，剩下的交给 is_allowed 逐段判。
    return str(name or "").replace("\\", "/").strip()


def is_allowed(name: str) -> bool:
    """这个 zip 条目允许落到 `resources\\app` 吗（纯函数，便于单测）。"""
    p = _norm(name)
    if not p or p.endswith("/"):
        return False
    if p.startswith("/") or ":" in p.split("/")[0]:
        return False
    parts = p.split("/")
    if any(seg in ("", ".", "..") for seg in parts):
        return False
    low = p.lower()
    if any(low.startswith(d) or ("/" + d) in low for d in DENY_PARTS):
        return False
    if low.startswith(".git/"):
        return False
    if p in DENY_NAMES or low in [x.lower() for x in DENY_NAMES]:
        return False
    if low in [x.lower() for x in DENY_WEBUI]:
        return False
    # 根目录的 .py
    if "/" not in p:
        return bool(ALLOW_ROOT_PY and p.endswith(".py"))
    return any(p.startswith(pre) for pre in ALLOW_PREFIXES)


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def check(manifest_url: str, timeout: float = 8.0, root: str = "") -> dict:
    """拿远端 manifest 和本地版本比一比。不写盘、不下载补丁。"""
    out = {"ok": False, "local": "", "remote": "", "newer": False,
           "error": "", "manifest": {}}
    root = root or app_root()
    out["local"] = _read_version(root)
    if not manifest_url:
        out["error"] = "没有配置更新地址"
        return out
    try:
        req = urllib.request.Request(manifest_url, headers={"User-Agent": "EggBloom-Updater"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("manifest 不是对象")
    except Exception as e:
        out["error"] = f"拉取失败: {type(e).__name__}: {e}"
        return out
    out["manifest"] = data
    out["remote"] = str(data.get("version") or "")
    out["ok"] = True
    out["newer"] = bool(out["remote"]) and out["remote"] != out["local"]
    return out


def _backup_dir(root: str) -> str:
    ts = time.strftime("%Y%m%d-%H%M%S")
    d = os.path.join(root, "data", "update-backup", ts)
    os.makedirs(d, exist_ok=True)
    return d


def apply(zip_path: str, manifest: dict, root: str = "",
          backup: bool = True) -> dict:
    """把补丁覆盖进 app 目录。返回报告；任何校验失败都不写盘。"""
    root = root or app_root()
    rep = {"ok": False, "applied": [], "backed_up": [], "skipped": [],
           "backup_dir": "", "error": ""}
    want = str((manifest or {}).get("sha256") or "").lower()
    if not want:
        rep["error"] = "manifest 没有 sha256，拒绝安装"
        return rep
    try:
        got = sha256_file(zip_path)
    except OSError as e:
        rep["error"] = f"读不到补丁: {e}"
        return rep
    if got != want:
        rep["error"] = "sha256 不匹配（补丁被改动或下载不完整）"
        return rep

    try:
        zf = zipfile.ZipFile(zip_path)
    except zipfile.BadZipFile as e:
        rep["error"] = f"不是合法 zip: {e}"
        return rep

    with zf:
        infos = [i for i in zf.infolist() if not i.is_dir()]
        if len(infos) > MAX_ENTRIES:
            rep["error"] = f"条目太多（{len(infos)} > {MAX_ENTRIES}）"
            return rep
        total = 0
        for i in infos:
            if i.file_size > MAX_ENTRY_BYTES:
                rep["error"] = f"单文件过大: {i.filename}"
                return rep
            total += i.file_size
        if total > MAX_TOTAL_BYTES:
            rep["error"] = f"补丁总大小超限（{total} 字节）"
            return rep
        allowed = [i for i in infos if is_allowed(i.filename)]
        rep["skipped"] = [i.filename for i in infos if not is_allowed(i.filename)]
        if not allowed:
            rep["error"] = "补丁里没有可覆盖的文件"
            return rep

        bdir = _backup_dir(root) if backup else ""
        rep["backup_dir"] = bdir
        for i in allowed:
            rel = _norm(i.filename)
            dst = os.path.join(root, rel.replace("/", os.sep))
            if backup and os.path.isfile(dst):
                bdst = os.path.join(bdir, rel.replace("/", os.sep))
                os.makedirs(os.path.dirname(bdst), exist_ok=True)
                shutil.copy2(dst, bdst)
                rep["backed_up"].append(rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            with zf.open(i) as src, open(dst, "wb") as out:
                shutil.copyfileobj(src, out)
            rep["applied"].append(rel)
    rep["ok"] = bool(rep["applied"])
    return rep


def rollback(backup_dir: str, root: str = "") -> dict:
    """把 <备份目录> 里的文件原样拷回去。"""
    root = root or app_root()
    rep = {"ok": False, "restored": [], "error": ""}
    if not backup_dir or not os.path.isdir(backup_dir):
        rep["error"] = "备份目录不存在"
        return rep
    for dirpath, _dirs, files in os.walk(backup_dir):
        for fn in files:
            src = os.path.join(dirpath, fn)
            rel = os.path.relpath(src, backup_dir)
            if not is_allowed(rel.replace(os.sep, "/")):
                continue
            dst = os.path.join(root, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
            rep["restored"].append(rel.replace(os.sep, "/"))
    rep["ok"] = bool(rep["restored"])
    return rep


def _main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="EggBloom 热补丁更新")
    ap.add_argument("--check", action="store_true", help="查有没有新版本")
    ap.add_argument("--apply", metavar="ZIP", help="安装补丁 zip")
    ap.add_argument("--manifest", metavar="JSON", help="manifest 文件（--apply 用）")
    ap.add_argument("--rollback", metavar="DIR", help="回滚到某个备份目录")
    ap.add_argument("--url", default="", help="manifest 地址（覆盖配置）")
    ap.add_argument("--root", default="", help="app 根目录（默认自动探测）")
    a = ap.parse_args(argv)

    root = a.root or app_root()
    if a.check:
        url = a.url
        if not url:
            try:
                sys.path.insert(0, root)
                import config as _c
                url = str(_c.load_config().get("update_manifest_url") or "")
            except Exception:
                url = ""
        r = check(url, root=root)
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return 0 if r.get("ok") else 1
    if a.apply:
        if not a.manifest:
            print("--apply 需要 --manifest")
            return 2
        with open(a.manifest, "r", encoding="utf-8") as f:
            man = json.load(f)
        r = apply(a.apply, man, root=root)
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return 0 if r.get("ok") else 1
    if a.rollback:
        r = rollback(a.rollback, root=root)
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return 0 if r.get("ok") else 1
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(_main())
