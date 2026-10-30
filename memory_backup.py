# -*- coding: utf-8 -*-
"""memory_backup.py —— 记忆的"世界毁灭"级保险（2026-10-30）。

## 现在的问题

- 沙漏 `sandglass.txt` 是**追加写**，从不重写 —— 好；
- 但只有一份。磁盘坏、误删、写到一半断电（最后一行残缺），就全没了；
- 没有"这份文件还是不是完整的"的判断，也没有"从备份拉回来"的入口。

## 这一层做什么

1. **快照**：把记忆文件 gzip 到 `data/backups/`，按天保留 N 份；
2. **校验**：文件能读完、最后一行能解析（追加写特有的"半行"损坏）；
3. **恢复**：从最新快照拉回，恢复前先把当前文件改名留档（绝不直接覆盖）；
4. **体检**：`verify` 返回行数/首末时间，用来回答"记忆到底有多少"。

## 不做什么

- 不做增量/去重（沙漏本身就是追加日志）；
- 不做云上传（没有可信目标，先不做）。

用法：
    python memory_backup.py                 # 备份 + 体检
    python memory_backup.py --restore       # 从最新快照恢复
"""
from __future__ import annotations

import gzip
import os
import shutil
import sys
import time

APP_ROOT = os.path.dirname(os.path.abspath(__file__))
DEFAULT_BACKUP_DIR = os.path.join(APP_ROOT, "data", "backups")
DEFAULT_KEEP = 14


def _memory_files() -> list:
    """需要上保险的文件（存在才备）。"""
    out = []
    for rel in (os.path.join(".neurobase", "sandglass.txt"),
                os.path.join("data", "people.json"),
                os.path.join("data", "people_extra.json"),
                os.path.join("data", "people_aliases.json"),
                os.path.join("data", "mood_state.json")):
        p = os.path.join(APP_ROOT, rel)
        if os.path.isfile(p):
            out.append(p)
    return out


def verify(path: str) -> dict:
    """体检：{ok, lines, first, last, bytes, error}。

    "追加写"最典型的损坏是**最后一行只写了一半**。判据：最后一行必须能
    按 `时间 | 角色 | 正文` 切成 3 段；切不开就是残缺（仍然可读，但要告警）。
    """
    res = {"ok": False, "lines": 0, "first": "", "last": "", "bytes": 0,
           "error": ""}
    try:
        res["bytes"] = os.path.getsize(path)
        # 追加写的半行：最后一行写到一半，**没有换行结尾**。
        # 只看"能否切成 3 段"不够 —— 截断发生在正文里，照样有三段。
        with open(path, "rb") as fb:
            fb.seek(0, os.SEEK_END)
            size = fb.tell()
            if size > 0:
                fb.seek(max(0, size - 1))
                tail_byte = fb.read(1)
            else:
                tail_byte = b"\n"
        first = last = ""
        n = 0
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            for ln in f:
                s = ln.rstrip("\r\n")
                if not s.strip():
                    continue
                n += 1
                if not first:
                    first = s
                last = s
        res["lines"] = n
        res["first"] = first[:19]
        res["last"] = last[:19]
        ok_tail = (n == 0) or (tail_byte == b"\n" and len(last.split(" | ")) == 3)
        res["ok"] = bool(ok_tail)
        if not ok_tail:
            res["error"] = "最后一行残缺（追加写被打断/没有换行结尾）"
    except Exception as e:
        res["error"] = f"{type(e).__name__}: {e}"
    return res


def snapshot(src: str, dest_dir: str = DEFAULT_BACKUP_DIR, *,
             stamp: str = "") -> str:
    """把一个文件 gzip 到 dest_dir，返回快照路径（失败返回空串）。"""
    if not os.path.isfile(src):
        return ""
    os.makedirs(dest_dir, exist_ok=True)
    stamp = stamp or time.strftime("%Y%m%d-%H%M%S")
    base = os.path.basename(src)
    dst = os.path.join(dest_dir, f"{base}.{stamp}.gz")
    try:
        with open(src, "rb") as fi, gzip.open(dst, "wb") as fo:
            shutil.copyfileobj(fi, fo)
        return dst
    except Exception:
        return ""


def prune(dest_dir: str = DEFAULT_BACKUP_DIR, keep: int = DEFAULT_KEEP) -> int:
    """每个源文件只留最新 keep 份，返回删掉的数量。"""
    try:
        names = [n for n in os.listdir(dest_dir) if n.endswith(".gz")]
    except Exception:
        return 0
    groups = {}
    for n in names:
        base = n.rsplit(".", 2)[0]          # 去掉 .gz 与时间戳
        groups.setdefault(base, []).append(n)
    removed = 0
    for _base, items in groups.items():
        items.sort()
        for old in items[:-max(1, int(keep))]:
            try:
                os.remove(os.path.join(dest_dir, old))
                removed += 1
            except Exception:
                pass
    return removed


def latest(dest_dir: str = DEFAULT_BACKUP_DIR, base: str = "") -> str:
    """最新一份快照路径（可指定源文件名）。"""
    try:
        names = sorted(n for n in os.listdir(dest_dir) if n.endswith(".gz"))
    except Exception:
        return ""
    if base:
        names = [n for n in names if n.startswith(base + ".")]
    return os.path.join(dest_dir, names[-1]) if names else ""


def restore(snapshot_path: str, target: str) -> bool:
    """从快照恢复。先给现有文件改名留档（`.before-restore-<ts>`），绝不直接覆盖。"""
    if not os.path.isfile(snapshot_path) or not target:
        return False
    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        if os.path.isfile(target):
            bak = f"{target}.before-restore-{time.strftime('%Y%m%d-%H%M%S')}"
            shutil.copy2(target, bak)
        tmp = target + ".restore.tmp"
        with gzip.open(snapshot_path, "rb") as fi, open(tmp, "wb") as fo:
            shutil.copyfileobj(fi, fo)
        os.replace(tmp, target)
        return True
    except Exception:
        return False


def backup_all(dest_dir: str = DEFAULT_BACKUP_DIR, keep: int = DEFAULT_KEEP) -> dict:
    """给所有记忆文件各备一份，返回 {文件: 快照路径}。"""
    out = {}
    for p in _memory_files():
        out[os.path.basename(p)] = snapshot(p, dest_dir)
    prune(dest_dir, keep)
    return out


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--restore" in argv:
        base = ""
        for a in argv:
            if a.startswith("--file="):
                base = a.split("=", 1)[1]
        snap = latest(DEFAULT_BACKUP_DIR, base)
        if not snap:
            print("没有可用快照")
            return 1
        target = os.path.join(APP_ROOT, ".neurobase", "sandglass.txt") if not base \
            else os.path.join(APP_ROOT, "data", base)
        ok = restore(snap, target)
        print(("已恢复: " if ok else "恢复失败: ") + snap + " → " + target)
        return 0 if ok else 1
    made = backup_all()
    for name, path in made.items():
        v = verify(os.path.join(APP_ROOT, ".neurobase", "sandglass.txt")) \
            if name == "sandglass.txt" else {"lines": "-", "ok": True}
        print(f"备份 {name}: {'OK' if path else '跳过'}  行数={v.get('lines')} "
              f"{'完整' if v.get('ok') else '残缺'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
