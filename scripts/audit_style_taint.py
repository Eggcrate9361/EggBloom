# -*- coding: utf-8 -*-
"""存量脏特征「只列不删」审计（证据 §F-3）。

背景（2026-11-10 真机证据 D-3）：
    `[引用:X｜…别抢话] 被引原文\\n[当前] 本次发言` 这个复合壳被
    `chat_style.add_message()` 原样写进样本库，`_extract_features()` 又从
    样本里挖口头禅 / 开头句式 / 表情习惯，于是「QQ」「用户」「[引」「(Bot)」
    成了某个群友的说话习惯，持续注入 prompt。

这个脚本**只读**：不 import 任何会建库建表的插件模块（`chat_style` 在
import 期就会 `os.makedirs` + `_init_db()`），不执行任何写 SQL，不改任何源文件。

用法::

    # 指向运行副本根目录（可以是本机数据目录或只读挂载的副本）
    python scripts/audit_style_taint.py --root "D:\\path\\to\\running-copy"
    python scripts/audit_style_taint.py --root <path> --out <清单.md> --limit 500

扫描的落点（不存在就如实记「缺失」，不报错）:
    1. plugins/ego/chat_style/data/chat_style.db  → style_samples / style_features
    2. data/people_aliases.json                   → 别名值
    3. self_learning_db/self_learning.db          → messages / expressions / jargon
    4. plugins/personality.txt                    → 自主学习写入的人格文本

输出里的 QQ / 群号一律掩码为「前 2 位 + **** + 后 2 位」。
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

# ── 判据 ────────────────────────────────────────────────────────────────
#: 正常本里用于判断「这一条是不是被系统标记污染了」
TEXT_RULES = [
    ("ctx_current", re.compile(r"\[当前\]")),
    ("quote_note", re.compile(r"\[引用[:\]]")),
    ("bot_mark", re.compile(r"我\(Bot\)|\(Bot\)")),
    ("instruction", re.compile(r"别抢话|不是在叫我|他在引用|别自说自话")),
    ("media_placeholder", re.compile(
        r"\[(?:图片|表情|动画表情|语音|视频|文件|卡片|分享)\]")),
    ("memory_label", re.compile(
        r"【(?:成就|核心记忆|日升|群聊近况|联网搜索结果|要求)】")),
    ("shell_fragment", re.compile(r"\[引|\[当|\[系|\[图|\[我\(")),
    ("fullwidth_bar", re.compile(r"｜")),
]

#: 特征值很短，形如「当前」「[引」「(Bot)」，不能套正文正则逐字符去猜。
#: 这些是 `_extract_features()` 实测会挖出来、且明显不是人话习惯的短值。
SHORT_TAINT = frozenset({
    "当前", "引用", "别抢话", "不是在叫我", "他在引用", "QQ", "qq", "用户",
    "[引", "[当", "[图", "[系", "(Bot)", "我(Bot)", "系统", "图片", "表情",
    "【成就", "【核心记忆", "｜", "[引用", "当前]", "引用]",
})
SHORT_TAINT_PATTERNS = (
    re.compile(r"\[(?:当前|引用|图片|表情|系统)"),
    re.compile(r"\(Bot\)"),
    re.compile(r"别抢话|不是在叫我|他在引用"),
)

#: 人格文本是**规则说明文档**，本来就会写「[表情:情绪词]」「我(Bot)」这类
#: 词。套 TEXT_RULES 会把说明当脏数据（本地实测误报 2 行）。这里只认
#: 真正的复合壳与程序指令，且默认不扫描，需 `--scan-personality` 显式开。
STRICT_PERSONALITY_RULES = [
    ("ctx_current", re.compile(r"\[当前\]")),
    ("quote_note", re.compile(r"\[引用:[^\]\n]{0,60}[｜|][^\]\n]{0,200}\]")),
    ("instruction", re.compile(r"别抢话|不是在叫我")),
    ("shell_fragment", re.compile(r"\[引|\[当")),
]

#: 9–11 位连续数字（QQ / 群号）一律掩码
_NUM_RE = re.compile(r"(?<!\d)\d{9,11}(?!\d)")


def mask(value) -> str:
    s = str(value if value is not None else "")
    return _NUM_RE.sub(lambda m: m.group(0)[:2] + "****" + m.group(0)[-2:], s)


def hit_rules(text) -> list:
    return [name for name, rx in TEXT_RULES if rx.search(str(text or ""))]


def hit_feature(value) -> bool:
    s = str(value if value is not None else "").strip()
    if s in SHORT_TAINT:
        return True
    return any(rx.search(s) for rx in SHORT_TAINT_PATTERNS)


def esc(value, width=140) -> str:
    """Markdown 表格单元：掩码 + 转义 + 截断。"""
    s = mask(value).replace("\r", " ").replace("\n", "\\n")
    s = s.replace("|", "\\|")
    if len(s) > width:
        s = s[:width] + "…"
    return s or "(空)"


def stamp(ts) -> str:
    try:
        return _dt.datetime.fromtimestamp(float(ts)).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError, OSError, OverflowError):
        return "-"


def weight(value) -> str:
    """权重收敛到 3 位：原始 float 的小数部分会被误读成 11 位号码。"""
    try:
        return f"{float(value):.3f}"
    except (TypeError, ValueError):
        return "-"


# ── 只读打开 SQLite ─────────────────────────────────────────────────────
def open_ro(db: Path):
    """只读打开。WAL 未 checkpoint 时 mode=ro 会失败 → 复制三件套到临时目录再读。

    复制只读源文件，不碰原件；TemporaryDirectory 退出即清理。
    返回 (conn, cleanup)；调用方必须 close(conn) 再 cleanup()。
    """
    uri = db.resolve().as_uri() + "?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True)
        conn.execute("SELECT name FROM sqlite_master LIMIT 1").fetchone()
        return conn, None
    except sqlite3.Error:
        pass
    tmp = tempfile.TemporaryDirectory(prefix="eb_taint_")
    stage = Path(tmp.name) / db.name
    shutil.copy2(db, stage)
    for suffix in ("-wal", "-shm"):
        side = Path(str(db) + suffix)
        if side.exists():
            shutil.copy2(side, Path(str(stage) + suffix))
    conn = sqlite3.connect(stage.resolve().as_uri() + "?mode=ro", uri=True)
    return conn, tmp


def table_exists(conn, name) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return bool(row)


def read_rows(conn, table, cols):
    if not table_exists(conn, table):
        return None
    want = ",".join(cols)
    return conn.execute(f"SELECT {want} FROM {table}").fetchall()


# ── 各落点 ──────────────────────────────────────────────────────────────
def scan_chat_style(root: Path, limit: int, notes: list):
    path = root / "plugins" / "ego" / "chat_style" / "data" / "chat_style.db"
    if not path.exists():
        notes.append(f"`{mask(path)}`：缺失")
        return [], [], 0, 0
    conn, cleanup = open_ro(path)
    try:
        samples = read_rows(conn, "style_samples",
                            ["id", "scope_id", "user_id", "message", "timestamp"])
        features = read_rows(conn, "style_features",
                             ["id", "scope_id", "user_id", "feature_type",
                              "feature_value", "weight", "use_count"])
        out_s, out_f = [], []
        if samples is not None:
            for row in samples:
                rules = hit_rules(row[3])
                if rules:
                    out_s.append((row[0], row[1], row[2], row[3], row[4],
                                  ",".join(rules)))
        if features is not None:
            for row in features:
                if hit_feature(row[4]):
                    out_f.append(tuple(row))
        notes.append(
            f"`{mask(path)}`：style_samples {len(samples or [])} 行 / "
            f"命中 {len(out_s)}；style_features {len(features or [])} 行 / 命中 {len(out_f)}")
        return out_s[:limit], out_f[:limit], len(out_s), len(out_f)
    finally:
        conn.close()
        if cleanup is not None:
            cleanup.cleanup()


def scan_self_learning(root: Path, limit: int, notes: list):
    path = root / "self_learning_db" / "self_learning.db"
    if not path.exists():
        notes.append(f"`{mask(path)}`：缺失")
        return [], 0
    conn, cleanup = open_ro(path)
    try:
        hits = []
        for table, text_col, extra in (
            ("messages", "text", ["id", "group_id", "user_id", "text", "timestamp"]),
            ("expressions", "expression", ["id", "group_id", "scene", "expression", "count"]),
            ("jargon", "word", ["id", "group_id", "word", "count"]),
        ):
            rows = read_rows(conn, table, extra)
            if rows is None:
                notes.append(f"`{table}`：表不存在")
                continue
            idx = extra.index(text_col)
            for row in rows:
                rules = hit_rules(row[idx]) or (
                    ["short_taint"] if hit_feature(row[idx]) else [])
                if rules:
                    # 数值型 Unix 时间戳长得像 QQ 号，先转日期再交给掩码，
                    # 否则清单里会出现「17****00.0」这种看不懂的东西。
                    shown = tuple(stamp(v) if isinstance(v, (int, float))
                                  and not isinstance(v, bool) and v > 1_000_000_000
                                  else v for v in row)
                    hits.append((table, shown, ",".join(rules)))
            notes.append(f"`{table}`：{len(rows)} 行")
        return hits[:limit], len(hits)
    finally:
        conn.close()
        if cleanup is not None:
            cleanup.cleanup()


def scan_aliases(root: Path, limit: int, notes: list):
    """称呼档案。1.6.0 叫 people_aliases.json，本机 1.5.x 运行副本叫
    people.json —— 两个都试，谁在扫谁，不因为改名就整块漏掉。"""
    candidates = ("data/people_aliases.json", "data/people.json")
    hits, found = [], 0
    for rel in candidates:
        path = root / rel
        if not path.exists():
            continue
        found += 1
        try:
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError) as e:
            notes.append(f"`{rel}`：读取失败 {type(e).__name__}")
            continue
        sub = []
        stack = [("", data)]
        while stack:
            prefix, node = stack.pop()
            if isinstance(node, dict):
                for k, v in node.items():
                    stack.append((f"{prefix}.{k}" if prefix else str(k), v))
            elif isinstance(node, list):
                for i, v in enumerate(node):
                    stack.append((f"{prefix}[{i}]", v))
            elif isinstance(node, str) and (hit_rules(node) or hit_feature(node)):
                sub.append((f"{rel}:{prefix}", node,
                            ",".join(hit_rules(node)) or "short_taint"))
        notes.append(f"`{rel}`：命中 {len(sub)} 个值")
        hits.extend(sub)
    if not found:
        notes.append("people_aliases.json / people.json：均缺失")
    return hits[:limit], len(hits)


def scan_personality(root: Path, limit: int, notes: list, enabled=False):
    path = root / "plugins" / "personality.txt"
    if not enabled:
        notes.append("plugins/personality.txt：默认跳过（规则说明文档，标记词属正常，"
                     "需要时加 --scan-personality）")
        return [], 0
    if not path.exists():
        notes.append(f"`{mask(path)}`：缺失")
        return [], 0
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as e:
        notes.append(f"`{mask(path)}`：读取失败 {type(e).__name__}")
        return [], 0
    hits = []
    for i, ln in enumerate(lines):
        rules = [name for name, rx in STRICT_PERSONALITY_RULES if rx.search(ln)]
        if rules:
            hits.append((i + 1, ln, ",".join(rules)))
    notes.append(f"`{mask(path)}`：{len(lines)} 行 / 严格判据命中 {len(hits)}")
    return hits[:limit], len(hits)


# ── 报告 ────────────────────────────────────────────────────────────────
def build_report(root: Path, limit: int, scan_persona=False) -> str:
    notes, L = [], []
    s, f, n_s, n_f = scan_chat_style(root, limit, notes)
    sl, n_sl = scan_self_learning(root, limit, notes)
    al, n_al = scan_aliases(root, limit, notes)
    pl, n_pl = scan_personality(root, limit, notes, enabled=scan_persona)

    L.append("# 存量脏特征只读清单（证据 §F-3）")
    L.append("")
    L.append(f"> 生成：{_dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  ")
    L.append(f"> 数据源：`{mask(root)}`  ")
    L.append("> **只列不删**：本脚本没有执行任何写 SQL，也没有改动源文件。  ")
    L.append("> 号码一律掩码；清单只供审阅，清理方案需单独放行。")
    L.append("")
    L.append("## 0. 汇总")
    L.append("")
    L.append("| 落点 | 命中条数 |")
    L.append("| --- | ---: |")
    L.append(f"| chat_style / style_samples | {n_s} |")
    L.append(f"| chat_style / style_features | {n_f} |")
    L.append(f"| self_learning（三张表合计） | {n_sl} |")
    L.append(f"| 称呼档案（people_aliases.json / people.json） | {n_al} |")
    L.append(f"| plugins/personality.txt | {n_pl} |")
    L.append("")
    L.append("扫描说明：" + "；".join(notes))
    L.append("")

    L.append("## 1. chat_style / style_samples（被污染的样本原文）")
    L.append("")
    if not s:
        L.append("无命中，或该数据文件不存在。")
    else:
        L.append("| id | scope | user | 时间 | 命中规则 | 原文 |")
        L.append("| ---: | --- | --- | --- | --- | --- |")
        for row in s:
            L.append(f"| {row[0]} | {esc(row[1], 24)} | {esc(row[2], 24)} | "
                     f"{stamp(row[4])} | {esc(row[5], 60)} | {esc(row[3])} |")
    L.append("")

    L.append("## 2. chat_style / style_features（已经蒸出来的脏特征）")
    L.append("")
    L.append("这一组最关键：特征一旦落库会持续注入 prompt，越用越歪。")
    L.append("")
    if not f:
        L.append("无命中，或该数据文件不存在。")
    else:
        L.append("| id | scope | user | 类型 | 特征值 | 权重 | 复用 |")
        L.append("| ---: | --- | --- | --- | --- | ---: | ---: |")
        for row in f:
            L.append(f"| {row[0]} | {esc(row[1], 24)} | {esc(row[2], 24)} | "
                     f"{esc(row[3], 16)} | {esc(row[4], 60)} | "
                     f"{weight(row[5])} | {row[6]} |")
    L.append("")

    L.append("## 3. self_learning（自主学习语料 / 口头禅 / 黑话）")
    L.append("")
    if not sl:
        L.append("无命中，或该数据文件不存在。")
    else:
        L.append("| 表 | 命中规则 | 原文 |")
        L.append("| --- | --- | --- |")
        for table, row, rules in sl:
            L.append(f"| {table} | {esc(rules, 60)} | {esc(list(row))} |")
    L.append("")

    L.append("## 4. 称呼档案（被存成称呼的壳标记）")
    L.append("")
    if not al:
        L.append("无命中，或该数据文件不存在。")
    else:
        L.append("| 路径 | 命中规则 | 值 |")
        L.append("| --- | --- | --- |")
        for key, val, rules in al:
            L.append(f"| {esc(key, 60)} | {esc(rules, 60)} | {esc(val)} |")
    L.append("")

    L.append("## 5. plugins/personality.txt（人格文本里的壳标记）")
    L.append("")
    if not scan_persona:
        L.append("默认不扫描：这份文件是规则说明文档，出现 `[表情:情绪词]` / "
                 "`我(Bot)` 是正常写法，套通用判据会误报。确需检查时加 "
                 "`--scan-personality`，届时只认复合壳与程序指令。")
    elif not pl:
        L.append("无命中，或该文件不存在。")
    else:
        L.append("| 行号 | 命中规则 | 原文 |")
        L.append("| ---: | --- | --- |")
        for no, line, rules in pl:
            L.append(f"| {no} | {esc(rules, 60)} | {esc(line)} |")
    L.append("")
    L.append(f"> 每组最多列 {limit} 条；用 `--limit` 调整。汇总里的数字是完整命中数。")
    L.append("")
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="存量脏特征只读审计（只列不删）")
    ap.add_argument("--root", required=True, help="运行副本根目录（只读）")
    ap.add_argument("--out", default="", help="清单输出路径；不填则只打印到 stdout")
    ap.add_argument("--limit", type=int, default=200, help="每组最多列出多少条（默认 200）")
    ap.add_argument("--scan-personality", action="store_true",
                    help="额外用严格判据检查 plugins/personality.txt（默认跳过，避免误报）")
    args = ap.parse_args(argv)
    root = Path(args.root)
    if not root.is_dir():
        print(f"[audit] --root 不是目录: {mask(root)}", file=sys.stderr)
        return 2
    report = build_report(root, max(1, args.limit), scan_persona=args.scan_personality)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(report, encoding="utf-8")
        print(f"[audit] 清单已写出: {out}")
        print(f"[audit] 只读完成，未改动数据源: {mask(root)}")
    else:
        print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
