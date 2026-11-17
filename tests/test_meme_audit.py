# -*- coding: utf-8 -*-
"""全量表情库审计必须区分基础图、待审核候选和可发送图片。"""
import importlib.util
import json
import os
import pathlib
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SPEC = importlib.util.spec_from_file_location(
    "audit_memes", os.path.join(ROOT, "scripts", "audit_memes.py"))
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)
REPAIR_SPEC = importlib.util.spec_from_file_location(
    "repair_meme_index", os.path.join(ROOT, "scripts", "repair_meme_index.py"))
REPAIR = importlib.util.module_from_spec(REPAIR_SPEC)
REPAIR_SPEC.loader.exec_module(REPAIR)

from PIL import Image  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name + ("" if not extra else f"  {extra}"))


td = tempfile.mkdtemp(prefix="meme_audit_")
try:
    root = os.path.join(td, "memes")
    collected = os.path.join(root, "collected")
    os.makedirs(collected)
    for rel in ("base.png", "collected/approved.png", "collected/pending.png"):
        path = os.path.join(root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        Image.new("RGB", (16, 16), (30, 60, 90)).save(path, "PNG")
    with open(os.path.join(root, "tags.json"), "w", encoding="utf-8") as f:
        json.dump({
            "base.png": ["开心"],
            "collected/approved.png": {"tags": ["疑惑"]},
            "collected/pending.png": ["呆滞"],
        }, f, ensure_ascii=False)
    with open(os.path.join(collected, "index.json"), "w", encoding="utf-8") as f:
        json.dump({
            "a": {"path": "collected/approved.png", "status": "approved", "tags": ["疑惑"]},
            "b": {"path": "collected/pending.png", "status": "pending", "tags": ["呆滞"]},
        }, f, ensure_ascii=False)

    rows, summary = MOD.collect(pathlib.Path(root))
    by_path = {row["path"]: row for row in rows}
    check("清点全部图片", len(rows) == 3, len(rows))
    check("基础图可匹配", by_path["base.png"]["reachable"] == "可匹配发送",
          by_path["base.png"])
    check("已审核候选可匹配",
          by_path["collected/approved.png"]["reachable"] == "可匹配发送",
          by_path["collected/approved.png"])
    check("★ pending 候选明确不可发送",
          by_path["collected/pending.png"]["reachable"] == "否：待审核",
          by_path["collected/pending.png"])
    check("汇总统计直接可匹配数量", summary["reachable"] == 2, summary)
    check("Markdown 包含每张图", MOD.render_markdown(rows, summary).count("\n| ") >= 3)

    recover = os.path.join(root, "recover.png")
    Image.new("RGB", (16, 16), (90, 30, 60)).save(recover, "PNG")
    donor = os.path.join(td, "donor-tags.json")
    with open(donor, "w", encoding="utf-8") as f:
        json.dump({
            "base.png": ["开心"],
            "recover.png": ["温柔"],
            "missing.png": ["疑惑"],
            "collected/pending.png": ["不应覆盖"],
        }, f, ensure_ascii=False)
    target, added, missing = REPAIR.plan(pathlib.Path(root), pathlib.Path(donor))
    check("修复计划只补目标缺失的已有文件",
          added == {"recover.png": ["温柔"]}, added)
    check("修复计划报告找不到的捐赠条目", missing == ["missing.png"], missing)
    check("修复计划不覆盖运行库原有标签",
          target["collected/pending.png"] == ["呆滞"], target)
    REPAIR.write(pathlib.Path(root), target, added)
    repaired = json.loads(pathlib.Path(root, "tags.json").read_text(encoding="utf-8"))
    check("修复写入只追加恢复条目",
          repaired.get("recover.png") == ["温柔"]
          and repaired.get("collected/pending.png") == ["呆滞"], repaired)
finally:
    shutil.rmtree(td, ignore_errors=True)

print(f"通过 {len(PASS)} / 失败 {len(FAIL)}")
for item in FAIL:
    print("  FAIL", item)
sys.exit(1 if FAIL else 0)
