# -*- coding: utf-8 -*-
"""scripts/review_fact_queue.py —— 待确认事实队列（1.5.11 尾巴）

抽取器现在**不再把猜的当事实**：置信度 < 0.6 的进 `fact_candidates`，
等人点头才升进 `facts`（facts 才是每轮注入 prompt 的那张表）。

    python scripts/review_fact_queue.py                 # 列出来
    python scripts/review_fact_queue.py --confirm 3 5   # 确认第 3、5 条
    python scripts/review_fact_queue.py --discard 7     # 丢掉第 7 条
    python scripts/review_fact_queue.py --db <path>     # 指定库

判据与运行时同一份代码（直接加载插件模块）。
"""
import argparse
import importlib.util
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DB = os.path.join(ROOT, "plugins", "ego", "super_memory", "data", "super_memory.db")


def load_memory(db_path):
    os.environ["EGGBLOOM_SUPER_MEMORY_DB"] = db_path
    spec = importlib.util.spec_from_file_location(
        "eb_super_memory_review", os.path.join(ROOT, "plugins", "ego", "super_memory", "__init__.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod._memory


def main(argv=None):
    ap = argparse.ArgumentParser(description="待确认事实队列")
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--confirm", nargs="*", type=int, default=None, help="确认这些 id")
    ap.add_argument("--discard", nargs="*", type=int, default=None, help="丢弃这些 id")
    args = ap.parse_args(argv)

    if not os.path.exists(args.db):
        print(f"库不存在：{args.db}")
        return 2

    mem = load_memory(args.db)

    if args.confirm:
        for cid in args.confirm:
            print(("确认 #%s 成功" if mem.confirm_candidate(cid) else "确认 #%s 失败（id 不存在？）") % cid)
    if args.discard:
        for cid in args.discard:
            print(("丢弃 #%s 成功" if mem.discard_candidate(cid) else "丢弃 #%s 失败") % cid)

    rows = mem.pending_candidates(limit=100)
    if not rows:
        print("待确认队列是空的。")
        return 0
    print(f"待确认 {len(rows)} 条（id / 置信度 / 类别 / 内容）：")
    for r in rows:
        print(f"  #{r['id']:<4} {r['confidence']:.2f}  [{r['category']}] {r['content']}")
    print("\n确认：--confirm <id>…   丢弃：--discard <id>…")
    return 0


if __name__ == "__main__":
    sys.exit(main())
