#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""提交前敏感信息扫描（2026-10-29）。

为什么有这个：10-28 那次"全仓脱敏"是人工发现后才补的；此后每次提交都得再
确认一遍有没有把真实 QQ / 群号 / 密钥 / 本机路径带进版本库。人工确认不可靠，
所以把它做成脚本，并挂成 pre-commit —— 命中即拒绝提交。

用法：
    python scripts/check_secrets.py            # 扫全仓受跟踪文件
    python scripts/check_secrets.py --staged   # 只扫暂存区的改动（pre-commit 用）
    python scripts/check_secrets.py --all      # 同默认，显式写法

退出码：0 = 干净；1 = 命中（输出 file:line 与原因）。
"""
import argparse
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: 明确是假值的片段 —— 测试夹具/示例配置里允许出现
WHITELIST = (
    "abcdefghijklmnopqrstuvwxyz",
    "example.com",
    "YOUR_",
    "sk-test",
    "sk-xxxx",
    "000000000",
    "1234567890",
    "YourName",
    "abcdefghijklmnop",
    "10000004",   # config.py 注释里"另一个 bot"的实测 uid，不是本机身份
    "10003",      # scripts/audit_config_parity.py 的占位 bot_qq（测试用假值）
)

PATTERNS = [
    ("疑似真实 API Key", re.compile(r"sk-[A-Za-z0-9]{20,}")),
    ("Bearer token", re.compile(r"Bearer\s+[A-Za-z0-9\-_\.]{20,}")),
    ("私钥块", re.compile(r"BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY")),
    ("密码/密钥赋值", re.compile(r"(?i)(password|passwd|secret_key|access_key)\s*[:=]\s*['\"][^'\"]{6,}")),
    ("本机用户目录", re.compile(r"(?i)[A-Za-z]:\\\\?Users\\\\?(?!Public|Default)[A-Za-z0-9_.\-]{2,}")),
    ("11 位数字（疑似 QQ/手机号）", re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")),
    # 只认"配置键 + 数字"的形态（group_ids/admin_qqs/bot_qq…）：
    # 单独的数字太容易撞日期、预算、版本号（真机误报 12 处全是这类）。
    ("配置里的真实号（键 + 数字）", re.compile(
        r"(?i)\"?(group_ids?|admin_qqs?|bot_qq|target_qqs?|focus_qq|qq_of)\"?\s*[:=]\s*\[?\s*\"?\d{5,12}")),
]

#: 只在这些文件里查"群号样式数字"（普通代码里的端口、版本号、哈希不该报）
CONFIG_HINT = ("config", "bot_config", "_fieldmeta", "配置", ".example", ".json")


def _git(*args):
    return subprocess.run(["git"] + list(args), cwd=ROOT, capture_output=True).stdout.decode("utf-8", "replace")


def _files(staged: bool):
    if staged:
        out = _git("diff", "--cached", "--name-only", "--diff-filter=ACM")
    else:
        out = _git("ls-files")
    return [f for f in out.splitlines() if f.strip()]


def _text_of(path: str, staged: bool):
    if staged:
        r = subprocess.run(["git", "show", f":{path}"], cwd=ROOT, capture_output=True)
        if r.returncode != 0:
            return None
        return r.stdout.decode("utf-8", "replace")
    try:
        with open(os.path.join(ROOT, path), encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return None


def scan(paths, staged: bool):
    hits = []
    for path in paths:
        if path.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".gif", ".zip", ".exe", ".dll",
                                  ".pyc", ".db", ".ttf", ".otf", ".woff", ".woff2", ".pfb", ".bin")):
            continue
        text = _text_of(path, staged)
        if not text:
            continue
        is_config = any(h in path.lower() for h in CONFIG_HINT)
        for lineno, line in enumerate(text.splitlines(), 1):
            if any(w in line for w in WHITELIST):
                continue
            for name, pat in PATTERNS:
                if name.startswith("配置里的真实号") and not is_config:
                    continue
                m = pat.search(line)
                if m:
                    hits.append((path, lineno, name, m.group(0)[:40]))
    return hits


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--staged", action="store_true", help="只扫暂存区（pre-commit 用）")
    ap.add_argument("--all", action="store_true", help="扫全仓（默认）")
    args = ap.parse_args()

    paths = _files(args.staged)
    if not paths:
        print("[check_secrets] 没有要检查的文件")
        return 0
    hits = scan(paths, args.staged)
    if not hits:
        print(f"[check_secrets] 干净（检查 {len(paths)} 个文件）")
        return 0
    print(f"[check_secrets] 命中 {len(hits)} 处，拒绝提交：")
    for path, line, name, val in hits[:50]:
        print(f"  {path}:{line}  [{name}]  {val}")
    print("\n如果确认是假值/示例，请把它写进 scripts/check_secrets.py 的 WHITELIST（附理由）。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
