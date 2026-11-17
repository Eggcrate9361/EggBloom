# -*- coding: utf-8 -*-
"""从完整图库的 tags.json 恢复运行库缺失的基础标签。

只补目标库不存在且文件真实存在的条目，不覆盖运行库已有标签，不改审核状态。
默认 dry-run；确认后用 ``--write`` 原子写入。
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


def _load(path: Path) -> dict:
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def plan(target_root: Path, donor_tags: Path) -> tuple[dict, dict, list[str]]:
    target_file = target_root / "tags.json"
    target = _load(target_file)
    donor = _load(donor_tags)
    added, missing = {}, []
    for rel, tags in donor.items():
        key = str(rel or "").replace("\\", "/")
        if not key or key in target:
            continue
        if not (target_root / key).is_file():
            missing.append(key)
            continue
        value = tags.get("tags") if isinstance(tags, dict) else tags
        if isinstance(value, str):
            value = [value]
        if isinstance(value, list) and any(str(v).strip() for v in value):
            added[key] = [str(v).strip() for v in value if str(v).strip()]
    return target, added, missing


def write(target_root: Path, target: dict, added: dict) -> None:
    output = dict(target)
    output.update(added)
    path = target_root / "tags.json"
    tmp = target_root / "tags.json.tmp"
    tmp.write_text(json.dumps(output, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    os.replace(tmp, path)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Recover missing base meme tags.")
    parser.add_argument("target", help="runtime memes/memes directory")
    parser.add_argument("--donor", required=True,
                        help="complete tags.json from a Full/Standard install")
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args(argv)

    target_root = Path(args.target).resolve()
    donor_tags = Path(args.donor).resolve()
    if not target_root.is_dir():
        raise SystemExit(f"target not found: {target_root}")
    if not donor_tags.is_file():
        raise SystemExit(f"donor tags not found: {donor_tags}")

    target, added, missing = plan(target_root, donor_tags)
    print(json.dumps({
        "target": str(target_root),
        "existing_entries": len(target),
        "recoverable_entries": len(added),
        "donor_entries_without_target_file": len(missing),
        "dry_run": not args.write,
    }, ensure_ascii=False, indent=2))
    if args.write and added:
        write(target_root, target, added)
        print(f"wrote {len(added)} recovered entries to {target_root / 'tags.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
