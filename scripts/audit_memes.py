# -*- coding: utf-8 -*-
"""清点并解释一套运行中的表情库。

输出每个图片文件的格式、尺寸、标签、审核状态和能否进入发送池。解释只依据
``tags.json``、``collected/index.json`` 与已缓存的 ``descriptions.json``，
不会为了“看起来完整”而编造画面。
"""
from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from pathlib import Path

IMAGE_EXTS = {".gif", ".jpg", ".jpeg", ".png", ".webp"}


def _load(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _tags(value) -> list[str]:
    if isinstance(value, dict):
        value = value.get("tags")
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    out = []
    for item in value:
        text = str(item or "").strip()
        if text and text not in out:
            out.append(text)
    return out


def _desc(value) -> str:
    if isinstance(value, dict):
        return str(value.get("desc") or value.get("description") or "").strip()
    return str(value or "").strip()


def _image_info(path: Path) -> tuple[str, str]:
    try:
        from PIL import Image
        with Image.open(path) as image:
            width, height = image.size
            frames = int(getattr(image, "n_frames", 1) or 1)
            return "%d×%d" % (width, height), str(frames)
    except Exception:
        return "-", "-"


def _index_by_path(index: dict) -> dict:
    out = {}
    for sha, rec in index.items():
        if not isinstance(rec, dict):
            continue
        rel = str(rec.get("path") or "").replace("\\", "/")
        if rel:
            out[rel] = dict(rec, sha=sha)
    return out


def _blocked_rels(root: Path) -> set[str]:
    data = _load(root / "collected" / "blocked.json")
    out = set()
    for value in data.values():
        if isinstance(value, dict) and value.get("rel"):
            out.add(str(value["rel"]).replace("\\", "/"))
    return out


def collect(root: Path, *, include_descriptions: bool = False,
            fallback_tags: Path | None = None) -> tuple[list[dict], dict]:
    tags_db = _load(root / "tags.json")
    fallback_db = _load(fallback_tags) if fallback_tags else {}
    descs = _load(root / "descriptions.json")
    indexed = _index_by_path(_load(root / "collected" / "index.json"))
    blocked = _blocked_rels(root)
    rows = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in IMAGE_EXTS:
            continue
        rel = path.relative_to(root).as_posix()
        rec = indexed.get(rel, {})
        runtime_tags = _tags(rec.get("tags"))
        base_tags = _tags(tags_db.get(rel))
        tags = runtime_tags or base_tags or _tags(fallback_db.get(rel))
        if rec:
            status = str(rec.get("status") or "approved")
        elif base_tags:
            # 基础图库没有 collected index，但进入 tags.json 就会被 match_meme 使用。
            status = "approved"
        else:
            status = "unindexed"
        if rel in blocked:
            status = "blocked"
        desc = _desc(descs.get(rel))
        if not desc:
            desc = _desc(descs.get(path.name))
        size, frames = _image_info(path)
        if status == "blocked":
            reachable = "否：黑名单"
            meaning = "已被标记为不喜欢，不再发送"
        elif status == "pending":
            reachable = "否：待审核"
            meaning = "候选表情包，人工通过前不会自动发送"
        elif status == "rejected":
            reachable = "否：已拒绝"
            meaning = "审核拒绝，不进入发送池"
        elif status == "unindexed":
            reachable = "否：当前索引缺失"
            meaning = (("图库索引缺失；语义：" + "、".join(tags[:4]))
                       if tags else "图库索引缺失，且没有可用标签")
        elif not tags:
            reachable = "仅随机兜底" if status == "approved" else "否：未建立索引"
            meaning = "没有被记录的情绪语义，不能可靠按情境使用"
        else:
            reachable = "可匹配发送"
            meaning = "表达情绪：" + "、".join(tags[:4])
        row = {
            "path": rel,
            "format": path.suffix.lower().lstrip("."),
            "size": size,
            "frames": frames,
            "tags": tags,
            "status": status,
            "reachable": reachable,
            "meaning": meaning,
        }
        if include_descriptions and desc:
            row["description"] = desc
        rows.append(row)
    summary = {
        "root": str(root),
        "files": len(rows),
        "tags_entries": len(tags_db),
        "fallback_tag_entries": len(fallback_db),
        "indexed_entries": len(indexed),
        "statuses": dict(sorted(Counter(r["status"] for r in rows).items())),
        "formats": dict(sorted(Counter(r["format"] for r in rows).items())),
        "tag_counts": dict(sorted(
            Counter(tag for row in rows for tag in row["tags"]).most_common())),
        "reachable": sum(1 for row in rows if row["reachable"] == "可匹配发送"),
    }
    return rows, summary


def render_markdown(rows: list[dict], summary: dict) -> str:
    lines = [
        "# Meme Library Audit",
        "",
        f"- Root: `{summary['root']}`",
        f"- Image files: {summary['files']}",
        f"- Tag entries: {summary['tags_entries']}",
        f"- Read-only fallback tag entries: {summary['fallback_tag_entries']}",
        f"- Collected index entries: {summary['indexed_entries']}",
        f"- Directly matchable: {summary['reachable']}",
        f"- Status counts: `{json.dumps(summary['statuses'], ensure_ascii=False)}`",
        f"- Format counts: `{json.dumps(summary['formats'], ensure_ascii=False)}`",
        "",
        "## Findings",
        "",
    ]
    unindexed = int(summary["statuses"].get("unindexed", 0) or 0)
    pending = int(summary["statuses"].get("pending", 0) or 0)
    fallback_n = int(summary.get("fallback_tag_entries", 0) or 0)
    if unindexed:
        lines.append(
            f"- {unindexed} 张图片没有当前运行索引，无法被 `match_meme` 选择。"
            + (f" 捐赠索引中有 {fallback_n} 条可用语义，可用 "
               "`python scripts/repair_meme_index.py <target> --donor <tags.json> --write` 恢复。"
               if fallback_n else ""))
    if pending:
        lines.append(f"- {pending} 张候选表情仍在审核区，审核通过前不会发送。")
    if not unindexed and not pending:
        lines.append("- 所有图片都有可用索引或已审核状态。")
    lines.extend([
        "",
        "| File | Type | Size | Frames | Status | Tags | Interpretation |",
        "|---|---|---:|---:|---|---|---|",
    ])
    for row in rows:
        tags = "、".join(row["tags"][:6]) or "-"
        interpretation = row["meaning"]
        if row.get("description"):
            interpretation += "；画面：" + row["description"].replace("|", "/")
        values = (
            row["path"], row["format"], row["size"], row["frames"],
            row["status"], tags, interpretation,
        )
        lines.append("| " + " | ".join(str(x).replace("|", "/") for x in values) + " |")
    lines.extend(("", "## Tag distribution", "", "| Tag | Count |", "|---|---:|"))
    for tag, count in sorted(summary["tag_counts"].items(), key=lambda x: (-x[1], x[0])):
        lines.append(f"| {tag} | {count} |")
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Audit every image in a meme library.")
    parser.add_argument("root", nargs="?", default="memes/memes")
    parser.add_argument("--out", default="", help="Markdown output path")
    parser.add_argument("--include-descriptions", action="store_true")
    parser.add_argument("--fallback-tags", default="",
                        help="Read-only donor tags.json for interpretation when the runtime index was truncated")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    if not root.is_dir():
        raise SystemExit(f"meme root not found: {root}")
    fallback = Path(args.fallback_tags).resolve() if args.fallback_tags else None
    rows, summary = collect(
        root, include_descriptions=args.include_descriptions,
        fallback_tags=fallback)
    text = render_markdown(rows, summary)
    if args.out:
        out = Path(args.out).resolve()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        print(f"wrote {out} ({len(rows)} images)")
    else:
        print(text)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
