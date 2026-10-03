# -*- coding: utf-8 -*-
"""
scripts/merge_slang_dicts.py — 一次性数据迁移：把两份分裂的 slang 词典合并为一份真源。

背景（2026-10-03，见 docs/plan/EggBloom 1.5 交接执行手册.md 任务 1）：
    注入侧 slang_injector.py 读<app>/slang_dict.json                （104 条）
    学习侧 slang_learner      写<app>/plugins/ego/slang_dict.json   （ 61 条）
    两个不同文件，永不交汇 → "让它学的黑话，学完进了死胡同"。
    代码侧已统一指向应用根（见 slang_injector.SLANG_DICT_PATH / slang_learner.SLANG_DICT_PATH），
    本脚本负责把两侧历史数据并成一个文件，**旧条目一条不丢**。

合并规则（保守优先，理由见 README / commit message）：
    1. 只在应用根存在的词条  → 原样保留。
    2. 只在学习侧存在的词条  → 补进真源。
    3. 两侧都有但释义不同    → **保留应用根的值**（那是注入侧正在实际使用的版本），
                              把学习侧的值写进 _conflicts 备份，供人工复核，不静默丢弃。
    4. slang_replies.json 同理（学习侧为空，真源已有 1 条）。

用法：
    python scripts/merge_slang_dicts.py --app "<resources\\app 绝对路径>" --dry-run
    python scripts/merge_slang_dicts.py --app "<resources\\app 绝对路径>"

只做加法：真源里已有的词条不会被覆盖或删除。学习侧旧文件默认保留（--purge-legacy 才删），
因为 sync-dist.ps1 只增不删，源码库侧不存在这两个运行态 JSON。
"""
import argparse
import json
import os
import shutil
import sys
import time


def _load(path, default=None):
    if not os.path.exists(path):
        return {} if default is None else default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"  ! 读取失败 {path}: {e}")
        return {} if default is None else default


def _save(path, entries):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"entries": entries}, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _entries_of(path):
    data = _load(path, {"entries": {}})
    if not isinstance(data, dict):
        return {}
    e = data.get("entries")
    return e if isinstance(e, dict) else {}


def _backup(path, tag):
    if not os.path.exists(path):
        return None
    bak = f"{path}.bak-{tag}"
    shutil.copy2(path, bak)
    return bak


def merge_dict(app_root, dry_run=False, purge_legacy=False):
    root_dict = os.path.join(app_root, "slang_dict.json")
    legacy_dict = os.path.join(app_root, "plugins", "ego", "slang_dict.json")
    root_repl = os.path.join(app_root, "slang_replies.json")
    legacy_repl = os.path.join(app_root, "plugins", "ego", "slang_replies.json")

    a = _entries_of(root_dict)
    b = _entries_of(legacy_dict)
    merged = dict(a)
    added = []
    conflicts = {}
    for k, v in b.items():
        if k not in a:
            merged[k] = v
            added.append(k)
        elif a[k] != v:
            conflicts[k] = {"真源(保留)": a[k], "学习侧(备份)": v}

    ra = _entries_of(root_repl)
    rb = _entries_of(legacy_repl)
    rmerged = dict(ra)
    r_added = [k for k in rb if k not in ra]
    for k in r_added:
        rmerged[k] = rb[k]

    print(f"真源  {os.path.basename(root_dict)}: {len(a)} 条")
    print(f"学习侧{os.path.basename(legacy_dict)}: {len(b)} 条")
    print(f"→ 合并后: {len(merged)} 条（新增 {len(added)}，释义冲突 {len(conflicts)}）")
    print(f"replies: 真源 {len(ra)} + 学习侧 {len(rb)} → {len(rmerged)}（新增 {len(r_added)}）")
    if conflicts:
        print("\n释义冲突（保留真源值，学习侧值写入备份文件待人工复核）:")
        for k, v in conflicts.items():
            print(f"  · {k}")
    if added:
        print(f"\n仅存在于学习侧、已补入真源的 {len(added)} 条:")
        for k in added:
            print(f"  + {k}")

    if dry_run:
        print("\n(DRY RUN) 未写入任何文件")
        return 0

    tag = time.strftime("%Y%m%d-%H%M%S")
    for p in (root_dict, root_repl):
        _backup(p, tag)
    print(f"\n已备份原真源文件（后缀 .bak-{tag}）")

    _save(root_dict, merged)
    print(f"✔ 写入 {root_dict}（{len(merged)} 条）")
    _save(root_repl, rmerged)
    print(f"✔ 写入 {root_repl}（{len(rmerged)} 条）")

    if conflicts:
        cpath = os.path.join(app_root, "plugins", "ego", "slang_learner", "data", "slang_conflicts.json")
        os.makedirs(os.path.dirname(cpath), exist_ok=True)
        with open(cpath, "w", encoding="utf-8") as f:
            json.dump({"merged_at": tag, "conflicts": conflicts}, f, ensure_ascii=False, indent=2)
        print(f"✔ 冲突详情写入 {cpath}")

    if purge_legacy:
        for p in (legacy_dict, legacy_repl):
            if os.path.exists(p):
                shutil.copy2(p, f"{p}.bak-{tag}")
                os.remove(p)
                print(f"✔ 已移除分裂的学习侧文件 {p}（副本留作 .bak-{tag}）")
    else:
        print("\n学习侧旧文件保留（它们已无人读取；确认无误后可手工删除，或加 --purge-legacy）")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--app", required=True, help="resources\\app 绝对路径")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--purge-legacy", action="store_true",
                    help="同时删除学习侧旧文件（会先备份）")
    a = ap.parse_args()
    app = os.path.abspath(a.app)
    if not os.path.isdir(app):
        print(f"错误：{app} 不是目录")
        return 2
    return merge_dict(app, a.dry_run, a.purge_legacy)


if __name__ == "__main__":
    sys.exit(main())
