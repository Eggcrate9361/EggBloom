# -*- coding: utf-8 -*-
"""scripts/clean_memory_facts.py —— 清洗 super_memory 里的垃圾事实/人格行

2026-10-31 真机：facts 表 19 条里能用的不到 3 条（`身份=老大` / `身份=鸡蛋` /
`偏好=概率：多少`），它们每轮都进 prompt，模型于是把**当前对话对象**当成「老大」。
根因是 10-31 之前的抽取器没有质量闸；现在写侧/读侧都加了闸，存量还得清一次。

用法：

    python scripts/clean_memory_facts.py              # 只看（默认 dry-run）
    python scripts/clean_memory_facts.py --apply      # 清掉（先自动备份 .db）
    python scripts/clean_memory_facts.py --db <path>  # 指定库

判据与运行时同一份：`plugins/ego/super_memory/__init__.py::_fact_is_usable`。
"""
import argparse
import importlib.util
import os
import shutil
import sqlite3
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DB = os.path.join(ROOT, "plugins", "ego", "super_memory", "data", "super_memory.db")


def load_gate():
    spec = importlib.util.spec_from_file_location(
        "eb_super_memory", os.path.join(ROOT, "plugins", "ego", "super_memory", "__init__.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod._fact_is_usable


#: persona_kernel 的 key → 事实类别（复用同一套闸）
_KERNEL_CATEGORY = {
    "user_identity": "身份",
    "user_preferences": "偏好",
    "user_location": "位置",
    "key_events": "事件",
}


def main(argv=None):
    ap = argparse.ArgumentParser(description="清洗 super_memory 垃圾事实/人格行")
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--apply", action="store_true", help="真的删（默认只看）")
    args = ap.parse_args(argv)

    if not os.path.exists(args.db):
        print(f"库不存在：{args.db}")
        return 2

    usable = load_gate()
    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row

    bad_facts = []
    for r in conn.execute("SELECT id, category, content FROM facts"):
        if not usable(r["category"], r["content"]):
            bad_facts.append((r["id"], r["category"], r["content"]))

    bad_kernel = []
    for r in conn.execute("SELECT id, key, value FROM persona_kernel"):
        cat = _KERNEL_CATEGORY.get(str(r["key"]), "偏好")
        if not usable(cat, r["value"]):
            bad_kernel.append((r["id"], r["key"], r["value"]))

    print(f"facts: {len(bad_facts)} 条要清 / persona_kernel: {len(bad_kernel)} 条要清")
    for _id, cat, content in bad_facts:
        print(f"  facts#{_id} [{cat}] {content!r}")
    for _id, key, val in bad_kernel:
        print(f"  kernel#{_id} [{key}] {val!r}")

    if not args.apply:
        print("\n（dry-run，加 --apply 才真删）")
        return 0

    backup = args.db + time.strftime(".bak-%Y%m%d-%H%M%S")
    shutil.copyfile(args.db, backup)
    print(f"\n已备份 → {backup}")
    with conn:
        conn.executemany("DELETE FROM facts WHERE id=?", [(i,) for i, _, _ in bad_facts])
        conn.executemany("DELETE FROM persona_kernel WHERE id=?", [(i,) for i, _, _ in bad_kernel])
    print(f"已删除：facts {len(bad_facts)} 条 / persona_kernel {len(bad_kernel)} 条")
    return 0


if __name__ == "__main__":
    sys.exit(main())
