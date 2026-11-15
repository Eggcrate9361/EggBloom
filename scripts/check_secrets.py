#!/usr/bin/env python
# -*- coding: utf-8 -*-
r"""提交前敏感信息扫描（2026-10-29）。

为什么有这个：10-28 那次"全仓脱敏"是人工发现后才补的；此后每次提交都得再
确认一遍有没有把真实 QQ / 群号 / 密钥 / 本机路径带进版本库。人工确认不可靠，
所以把它做成脚本，并挂成 pre-commit —— 命中即拒绝提交。

用法：
    python scripts/check_secrets.py            # 扫全仓：已跟踪 + 未跟踪（排除 ignored）
    python scripts/check_secrets.py --staged   # 只扫暂存区的改动（pre-commit 用）
    python scripts/check_secrets.py --all      # 同默认，显式写法
    python scripts/check_secrets.py --walk     # 不靠 git，按目录遍历（无 .git 环境）

退出码：0 = 干净；1 = 命中（输出 file:line 与原因）。

2026-11-13：默认口径原来是 `git ls-files`，只列 tracked。新增文件在第一次
`git add` 之前完全不在扫描范围内，于是「干净」只证明旧文件干净 —— 一枚带
日期后缀的配置备份就是这样漏过去的。改成 `--cached --others --exclude-standard`：
涵盖所有会被 `git add -A` 收走的文件，同时跳过 ignored 的本地配置。

2026-11-13 补（**同一天第二刀，因为这刀没挡住东西**）：11 处真实号被推进公开链，
本文件的 PATTERNS 一条都没命中 —— `1[3-9]\d{9}` 只认手机号段，真号是 2/3 开头
的 9-10 位数；`配置键 + 数字` 还只在 config 类文件里查，而泄漏发生在 `tests/`。
号码判据现在统一到 `tools/identity_numbers.py`（与提交信息闸共用一份），
本文件只负责「读文件 → 逐行交出去」。别再往 PATTERNS 里加号码规则。

2026-11-15（第三刀，起因是同一个洞换了副面孔）：
`docs/RELEASE_CHECKLIST.md` 里写着 `ssh://ubuntu@<公网IP>/home/ubuntu/eggbloom-origin.git`，
从 11-13 建根那次起，**36/36 个提交**里都带着它。上面 PATTERNS 仍然一条不命中：
「本机用户目录」那条要求盘符（`C:\Users\…`），POSIX 家目录不在范围；
IP 既不是密钥也不是号码，压根没有判据。

这类东西的共性不是「号码」，是**地址形态** —— 它指向一台真机器，公开等于发请柬。
所以单独一组 `INFRA_PATTERNS`，判据是「能不能顺着它找到一台机器」：
`ssh://`、`user@IP`、公网 IPv4、`/home/<name>/`、`/root/`。

两个刻意的取舍：
- 私网 / 回环 / 文档示例段（10. 172.16-31. 192.168. 127. 192.0.2. 198.51.100. 203.0.113.）
  不算泄漏 —— 它们是配置和示例的正常写法；
- 带 UA 标记的行（`Mozilla/` `Chrome/` `AppleWebKit/`…）跳过 IPv4 判定 ——
  `Chrome/131.0.0.0` 是版本号，不是地址。这条不加，每次跑都报一堆假的。
"""
import argparse
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
# 判据被删/改名时要当场炸，不能静默退化成「没有号码闸」—— 那正是 11-13 的形态。
import identity_numbers as idn  # noqa: E402

#: 明确是假值的片段 —— 测试夹具/示例配置里允许出现。
#: 注意这只是给**下面 PATTERNS** 用的捷径（整行含它就跳过）；号码判据不吃这条，
#: 因为一行里同时出现 example.com 和一个真号时，整行跳过等于把真号放过去。
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
)

PATTERNS = [
    ("疑似真实 API Key", re.compile(r"sk-[A-Za-z0-9]{20,}")),
    ("Bearer token", re.compile(r"Bearer\s+[A-Za-z0-9\-_\.]{20,}")),
    ("私钥块", re.compile(r"BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY")),
    ("密码/密钥赋值", re.compile(r"(?i)(password|passwd|secret_key|access_key)\s*[:=]\s*['\"][^'\"]{6,}")),
    ("本机用户目录", re.compile(r"(?i)[A-Za-z]:\\\\?Users\\\\?(?!Public|Default)[A-Za-z0-9_.\-]{2,}")),
    ("11 位数字（疑似手机号）", re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")),
    # 原来还有一条「配置键 + 数字」，只在 config 类文件里查群号/QQ。
    # 11-13 拆掉了：它的两个毛病恰是漏因 —— 只在 config 里查（泄漏发生在 tests/），
    # 只认 `键: 数字`（泄漏发生在 `user_id=` 和 prompt 字符串里）。
    # 号码判据整体搬到 tools/identity_numbers.py，全文件口径 + 身份位白名单。
]


# ── 基础设施标识（2026-11-15）────────────────────────────────────────────
# 判据不是「号码」而是「地址形态」：能不能顺着它找到一台机器。
INFRA_PATTERNS = [
    ("SSH 地址", re.compile(r"ssh://[^\s`'\"<>]+")),
    ("user@IP", re.compile(r"\b[A-Za-z_][A-Za-z0-9._-]{0,31}@(?:\d{1,3}\.){3}\d{1,3}\b")),
]
_RE_IPV4 = re.compile(
    r"(?<![\d.])(?:(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\.){3}"
    r"(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)(?![\d.])")
_RE_HOME = re.compile(r"(?<![\w.-])/(?:home|Users)/([A-Za-z0-9._-]{2,})/")
_RE_ROOTDIR = re.compile(r"(?<![\w.-])/root/")

#: 带这些标记的行跳过 IPv4 判定 —— `Chrome/131.0.0.0` 是版本号不是地址。
UA_MARKERS = ("Mozilla/", "AppleWebKit/", "Chrome/", "Safari/", "Gecko/", "Version/")

#: 通用占位名：夹具里的 `/home/user/` 不代表一台真机器。
HOME_PLACEHOLDERS = {"user", "username", "yourname", "someone", "example", "xxx"}

#: 确认是夹具/示例的命中，(文件, 命中片段) → 理由。加之前先确认它真的不是真机。
INFRA_ALLOW = {
    ("tests/test_outgoing_audit.py", "/home/administrator/"):
        "脱敏用例的夹具：测的就是路径识别本身，不指向任何真实机器",
}


#: 判据文件自己的夹具就是违规样例（同 `identity_numbers.is_judge_file` 的做法）。
#: 扫自己时只关掉基础设施这一层，号码层照查。
JUDGE_FILE = "scripts/check_secrets.py"


def _ip_is_public(s: str) -> bool:
    """私网 / 回环 / 文档示例段不算泄漏 —— 它们是配置和示例的正常写法。"""
    p = [int(x) for x in s.split(".")]
    if p[0] in (0, 10, 127, 255):
        return False
    if p[0] == 169 and p[1] == 254:
        return False
    if p[0] == 172 and 16 <= p[1] <= 31:
        return False
    if p[0] == 192 and p[1] == 168:
        return False
    if (p[0], p[1], p[2]) in ((192, 0, 2), (198, 51, 100), (203, 0, 113)):
        return False
    return True


def _infra_hits(line: str):
    """一行里的基础设施标识。产出 (名称, 命中片段)。"""
    for name, pat in INFRA_PATTERNS:
        m = pat.search(line)
        if m:
            yield name, m.group(0)
    if not any(u in line for u in UA_MARKERS):
        for m in _RE_IPV4.finditer(line):
            if _ip_is_public(m.group(0)):
                yield "公网 IP", m.group(0)
    for m in _RE_HOME.finditer(line):
        if m.group(1).lower() not in HOME_PLACEHOLDERS:
            yield "POSIX 家目录", m.group(0)
    if _RE_ROOTDIR.search(line):
        yield "root 家目录", "/root/"


def _git(*args):
    return subprocess.run(["git"] + list(args), cwd=ROOT, capture_output=True).stdout.decode("utf-8", "replace")


def _files(staged: bool):
    if staged:
        out = _git("diff", "--cached", "--name-only", "--diff-filter=ACM")
    else:
        # --cached: 已跟踪；--others --exclude-standard: 未跟踪但未被忽略。
        # 二者合起来正好是 `git add -A` 会收走的集合 —— 新增文件在首次 add
        # 之前也必须能扫到，否则闸门只守着旧文件。
        out = _git("ls-files", "--cached", "--others", "--exclude-standard")
    return [f for f in out.splitlines() if f.strip()]


#: --walk 模式跳过的大目录（二进制 / 运行时数据 / 缓存）
WALK_SKIP_DIRS = {"__pycache__", ".venv", "venv", "memes", "image_cache", "backup",
                  "_ref", ".neurobase", "data", "logs", "chat_logs", "dist",
                  "node_modules", ".pytest_cache", "self_learning_db",
                  "personality_versions"}
WALK_EXTS = (".py", ".md", ".json", ".html", ".js", ".bat", ".ps1", ".txt", ".yml", ".yaml")
#: 本地真实配置：本来就装着真实 QQ/群号/模型地址，且已被 .gitignore 排除。
#: 遍历模式跳过它们，否则每次都报一条已知的「命中」。
WALK_SKIP_FILES = {"bot_config.json", "bot_config.json.bak", "bot_config.json.old", ".env"}


def _walk_files():
    """不靠 git 的兜底：直接按目录遍历源码树。

    为什么需要：只有源码、没有 .git 的环境里 `git ls-files` 返回空，
    脚本打一行「没有要检查的文件」就 0 退出 —— 把「扫不到」当成「没问题」，
    是最隐的假绿。而 .gitignore 里明写着 bot_config.json 含真实 QQ 号/群号，
    没 git 时根本无从得知它是否被带进包。
    """
    out = []
    for root, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in WALK_SKIP_DIRS]
        for f in files:
            # `bot_config.json*` 一律跳过：主文件、.old、.bak、以及带日期后缀的
            # 备份（`bot_config.json.bak-20261111-000333`）里本来就装着真实 QQ / 群号，
            # 而它们都被 .gitignore 排除。少跳过一种，每次跑都报一条已知的「命中」。
            if f in WALK_SKIP_FILES or f.startswith("bot_config.json") or ".example" in f:
                continue
            if f.lower().endswith(WALK_EXTS):
                out.append(os.path.relpath(os.path.join(root, f), ROOT).replace(os.sep, "/"))
    return sorted(out)


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


#: 二进制/大件：读了也没意义
BIN_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".zip", ".exe", ".dll",
            ".pyc", ".db", ".ttf", ".otf", ".woff", ".woff2", ".pfb", ".bin")

#: 号码命中在报告里的名字（`kind` 由判据给）
_IDENT_LABEL = {
    "blocked": "真实身份号（在拦名单里）",
    "identity": "身份位出现非合成段的号",
}


def scan(paths, staged: bool):
    hits = []
    blocked = idn.blocked_digests()
    for path in paths:
        if path.lower().endswith(BIN_EXTS):
            continue
        text = _text_of(path, staged)
        if not text:
            continue
        # 判据文件自己的夹具就是违规样例，第二层对它关掉（第一层照查）
        check_identity = not idn.is_judge_file(path)
        check_infra = os.path.normpath(path) != os.path.normpath(JUDGE_FILE)
        for lineno, line in enumerate(text.splitlines(), 1):
            # 号码判据先跑，且**不吃**下面那条「整行含白名单片段就跳过」的捷径：
            # 一行里同时有 example.com 和一个真号时，整行跳过等于把真号放过去。
            for h in idn.scan_line(line, blocked, check_identity):
                hits.append((path, lineno, _IDENT_LABEL[h.kind],
                             f"{idn.mask(h.value)}  {h.why}"))
            # 基础设施标识：也不吃 WHITELIST 捷径（一行里同时有 example.com 和一台真机时，
            # 整行跳过等于把地址放过去），只吃带理由的 INFRA_ALLOW。
            for name, val in _infra_hits(line):
                if not check_infra or (path, val) in INFRA_ALLOW:
                    continue
                hits.append((path, lineno, name, val[:60]))
            if any(w in line for w in WHITELIST):
                continue
            for name, pat in PATTERNS:
                m = pat.search(line)
                if m:
                    hits.append((path, lineno, name, m.group(0)[:40]))
    return hits


#: 判据自检（`--selftest`）。地址一律用文档段/回环/公共 DNS，不写任何真机。
SELFTEST = [
    # 真实泄漏的那一形态：ssh 地址 + user@IP（地址用文档段 192.0.2.0/24）
    # 注意 `/home/ubuntu/` 这里不报：它前面紧贴 `0`（`192.0.2.10`），被 `(?<![\w.-])` 挡掉了；
    # 那一形态本来就靠前两条抓，家目录规则管的是**独立出现**的路径。
    ("ssh://ubuntu@192.0.2.10/home/ubuntu/eggbloom-origin.git", {"SSH 地址", "user@IP"}),
    ("裸仓库在 server 上，地址不入库", set()),
    ("从 8.8.8.8 拉了一段", {"公网 IP"}),
    ("监听 127.0.0.1:8080 / 绑定 0.0.0.0", set()),
    ("内网 10.0.0.5、192.168.1.2、172.16.3.4", set()),
    ("示例 192.0.2.10 / 198.51.100.7 / 203.0.113.9", set()),
    ('"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131.0.0.0 Safari/537.36"', set()),
    ("看下 /home/user/report.pdf", set()),
    ("配置在 /home/administrator/.dsh/settings.json", {"POSIX 家目录"}),
    ("拷到 /root/.ssh/authorized_keys", {"root 家目录"}),
    ("ssh://git@github.com/x/y.git", {"SSH 地址"}),
]


def _selftest() -> int:
    bad = 0
    for line, want in SELFTEST:
        got = {name for name, _ in _infra_hits(line)}
        ok = got == want
        print(f"  [{'OK' if ok else 'FAIL'}] {line[:52]:<52} 期望 {sorted(want)} 实际 {sorted(got)}")
        bad += 0 if ok else 1
    print(f"[check_secrets --selftest] {len(SELFTEST) - bad}/{len(SELFTEST)} 通过")
    return 0 if not bad else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--staged", action="store_true", help="只扫暂存区（pre-commit 用）")
    ap.add_argument("--all", action="store_true", help="扫全仓（默认）")
    ap.add_argument("--walk", action="store_true",
                    help="不靠 git，直接按目录遍历（无 .git 的环境用这个）")
    ap.add_argument("--selftest", action="store_true", help="只跑基础设施判据自检")
    args = ap.parse_args()

    if args.selftest:
        return _selftest()

    if args.staged:
        paths = _files(True)
    else:
        paths = [] if args.walk else _files(False)
        if not paths:
            # 没 git（或显式 --walk）→ 目录遍历。以前这里直接 return 0，
            # 把「扫不到东西」当成「没有敏感信息」，是最隐的假绿。
            paths = _walk_files()
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
    if any("身份号" in h[2] for h in hits):
        print("\n号码类命中怎么改：把夹具改成合成段（1000000xxx / 9000000xx / 1008xxxx，\n"
              "  判据见 tools/identity_numbers.py 的 SYNTH_PATTERNS），别改成另一个真号。\n"
              "  确实是新出现的真实号 → 先清理掉，再用\n"
              "  python tools/identity_numbers.py --add <号> \"说明\" 加进拦名单防回归。")
    if any(h[2] in ("SSH 地址", "user@IP", "公网 IP", "POSIX 家目录", "root 家目录")
           for h in hits):
        print("\n基础设施类命中怎么改：节点地址不进库 —— 写成「另一台机器上的裸仓库」这类描述；\n"
              "  真要给运维看，放被 gitignore 的 bot_config.json / .env。\n"
              "  确认是夹具 → 加进本文件的 INFRA_ALLOW（逐条写理由）。\n"
              "  清掉当前文件只是第一步，历史得单独查：git log --all -S \"<片段>\" --oneline")
    if any("身份号" not in h[2] and h[2] not in (
            "SSH 地址", "user@IP", "公网 IP", "POSIX 家目录", "root 家目录") for h in hits):
        print("\n其余命中若确认是假值/示例，写进 scripts/check_secrets.py 的 WHITELIST（附理由）。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
