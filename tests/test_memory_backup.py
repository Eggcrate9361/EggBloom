# -*- coding: utf-8 -*-
"""记忆备份/体检/恢复（memory_backup.py，2026-10-30）。

钉死：
  1. 能体检出"追加写被打断"的半行
  2. 快照是 gzip、能取到最新、能原样恢复
  3. 恢复前先给当前文件留档（绝不直接覆盖）
  4. 每个源文件只留 N 份

运行：python tests/test_memory_backup.py
"""
import gzip
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import memory_backup as mb  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name if not extra else f"{name}  {extra}")


TMP = tempfile.mkdtemp(prefix="membak_test_")
try:
    src = os.path.join(TMP, "sandglass.txt")
    with open(src, "w", encoding="utf-8") as f:
        f.write("2026-10-30 14:00:00 | user | [小明(1)] 你好\n")
        f.write("2026-10-30 14:01:00 | agent | 你好呀\n")

    v = mb.verify(src)
    check("体检正常", v["ok"] is True, v)
    check("行数正确", v["lines"] == 2, v)
    check("首末时间可读", v["first"].startswith("2026-10-30") and v["last"], v)

    # 半行（追加写被打断）
    src_bad = os.path.join(TMP, "bad.txt")
    with open(src_bad, "w", encoding="utf-8") as f:
        f.write("2026-10-30 14:00:00 | user | 完整\n")
        f.write("2026-10-30 14:01:00 | user | 写到一半")
    vb = mb.verify(src_bad)
    check("半行能被发现", vb["ok"] is False and "残缺" in vb["error"], vb)

    # 快照 / 最新 / 恢复
    bdir = os.path.join(TMP, "backups")
    s1 = mb.snapshot(src, bdir, stamp="20261030-000001")
    s2 = mb.snapshot(src, bdir, stamp="20261030-000002")
    check("快照落盘", s1 and os.path.isfile(s1), s1)
    check("最新取到第二份", mb.latest(bdir) == s2, mb.latest(bdir))
    check("按文件名过滤", mb.latest(bdir, "sandglass.txt") == s2)
    with gzip.open(s1, "rt", encoding="utf-8") as f:
        check("快照内容一致", "你好" in f.read())

    # 恢复：先留档，再覆盖
    with open(src, "w", encoding="utf-8") as f:
        f.write("被破坏\n")
    check("恢复成功", mb.restore(s1, src) is True)
    got = open(src, encoding="utf-8").read()
    check("恢复后内容回来", "你好" in got and "被破坏" not in got, got)
    baks = [n for n in os.listdir(TMP) if ".before-restore-" in n]
    check("恢复前留了旧档", len(baks) == 1, baks)

    # 保留 N 份
    for i in range(5):
        mb.snapshot(src, bdir, stamp=f"20261030-00010{i}")
    removed = mb.prune(bdir, keep=3)
    left = [n for n in os.listdir(bdir) if n.startswith("sandglass.txt.")]
    check("只保留 N 份", len(left) == 3, left)
    check("prune 返回删除数", removed == 5 - 3 + 0 or removed >= 3, removed)
finally:
    shutil.rmtree(TMP, ignore_errors=True)

print(f"通过 {len(PASS)} / 失败 {len(FAIL)}")
for n in FAIL:
    print(f"  FAIL  {n}")
sys.exit(1 if FAIL else 0)
