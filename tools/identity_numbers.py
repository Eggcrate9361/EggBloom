#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""身份号判据 —— 提交信息闸与源码扫描共用同一份（2026-11-13）。

## 为什么有这个文件

11-13 把 11 处真实号推进了公开链：从 server 线搬 `test_hotrestart.py` 时
只改了报错点出来的那一处，同一文件里另有 10 处。事后复盘，当时**没有一道闸
能命中它**：

| 形态 | `check_secrets.py` 当时的规则 | 结果 |
|---|---|---|
| 10 位、2/3 开头（uid、bot 号） | `1[3-9]\\d{9}` —— 只认手机号段 | 漏 |
| 9 位（群号） | `配置键 + 数字` —— 还只在 config 类文件里查 | 漏 |
| 写在测试夹具的 `user_id=` 里 | 同上，`tests/` 不在 config 名单 | 漏 |

而 `tools/commit_lint.py` 的 CM-03 只读**提交信息**，压根不看文件内容。
两条闸各管一半，「号码在源码里」这一半谁都没管。

## 判据分两层，回答的是两个不同问题

**第一层：已知真号（精确匹配，任何位置）**

这些号出现过、清理过，再出现就是回归。「一个真实 QQ 号出现在公开仓库里」
本身即问题，没有哪种上下文能把它变得合理 —— 所以不设位置豁免。
按构造零误报。

清单不存明文，存 `sha256(盐 + 号)` 的前 16 位（`identity_blocklist.txt`）。
哈希是**遮蔽不是加密**：位数空间有限，算力够就能反查。它的作用是让这份清单
不能被 `git grep` 当成号码读出来 —— 别把它当安全边界。

**第二层：身份位 + 合成段白名单（形状）**

身份位上的号（`user_id=…`、`group_id: …`、`target_qqs: […]`）必须落在仓库
既定的合成段里，否则报。这一层挡的是**没见过的**新真号：真号写进源码时
一定在身份位上，因为那是它唯一有用的位置。

第二层是白名单不是黑名单 —— 判据不知道真号有哪些，但它知道假号长什么样。
写成黑名单就只能挡已经犯过的错。

## 已知边界

* 第二层的窗口是 **8-11 位**，和 `commit_lint` 的 CM-03 同一个窗口。依据是
  存量：仓库历史里出现过的六个真号全在 9-10 位（5-8 位号段 2010 年前后就用完了，
  现在注册不到）。代价是「6 位老群号」漏判 —— 那一档只有第一层拦得住。
* 只判赋值右侧的**第一个**号。`GID, UID = "a", "b"` 这种元组解包只看得到 `a`；
  新增夹具时用单值赋值，别用解包。
* 非身份位的自由文本（注释、prompt、日志样例里裸写的号）只有第一层管得着。
  这是刻意的：对整行扫会多出 20 多处误报（时间戳、URL 路径、数字串字面量），
  噪声一多闸就会被 `--no-verify` 绕过，等于没有闸。
* 13 位以上的数字串会滑窗切出 5-12 位的片段逐个对摘要 —— 真号被拼在长串里
  （`100000000110000` 这种）一样能抓出来。这一步是写完这份文件之后补的：
  初版的举例自己就把一个真实群号拼在 15 位数字串中间，而当时「不切」的边界
  让它大摇大摆过了全树扫描。
* 判据文件自己的自检夹具必然写着「违规输入」的样例，所以**只对这个文件**关掉
  第二层；第一层照查 —— 真号藏在这里一样会被抓出来。

## 用法

    python tools/identity_numbers.py --scan            # 扫全仓，列出命中
    python tools/identity_numbers.py --selftest        # 判据自身的正反例
    python tools/identity_numbers.py --check-local     # 本机配置里的号都在拦名单里吗
    python tools/identity_numbers.py --add <号> "说明"  # 往拦名单追加一条

退出码：0 干净 / 1 命中 / 2 用法或环境错误。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BLOCKLIST_FILE = os.path.join(HERE, "identity_blocklist.txt")
LOCAL_CONFIG = os.path.join(ROOT, "bot_config.json")

#: 盐固定写在代码里。换了它，拦名单全部失效 —— 所以别换；
#: 它存在的意义只是让哈希不能和公开的号码彩虹表直接对上。
_SALT = "eggbloom/identity/v1"
_DIGEST_LEN = 16


def digest(num: str) -> str:
    """号 → 拦名单里存的那 16 位。"""
    return hashlib.sha256((_SALT + str(num)).encode("utf-8")).hexdigest()[:_DIGEST_LEN]


def mask(num: str) -> str:
    """报告里显示的形态。位数保留，中间打掉 —— 运维要能对上是哪一条，
    但这两行输出可能被贴进 issue，不该顺便把号再发一遍。"""
    s = str(num)
    if len(s) <= 6:
        return s[0] + "*" * (len(s) - 1)
    return s[:4] + "*" * (len(s) - 6) + s[-2:]


# ─────────────────────────── 第一层：拦名单 ───────────────────────────
def load_blocklist(path: str = BLOCKLIST_FILE) -> dict:
    """读拦名单 → {digest: 说明}。

    行格式：`<16 位十六进制>  <说明>`，`#` 开头与空行忽略。
    格式不对直接报错退出：静默跳过一条等于那一条从来没被拦过。
    """
    out = {}
    if not os.path.exists(path):
        return out
    with open(path, encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, 1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split(None, 1)
            d = parts[0]
            if not re.fullmatch(r"[0-9a-f]{%d}" % _DIGEST_LEN, d):
                raise SystemExit(f"[identity] {path}:{lineno} 摘要格式不对：{d!r}")
            out[d] = parts[1].strip() if len(parts) > 1 else "（未写说明）"
    return out


#: 配置里这些键装的是身份号。读本机 `bot_config.json` 时按它取。
IDENTITY_KEYS = (
    "bot_qq", "admin_qqs", "admin_qq", "group_ids", "group_id", "target_qqs",
    "target_qq", "focus_qq", "qq_of", "owner_qq", "master_qq",
)


def local_identities(config_path: str | None = None) -> list:
    """从本机真实配置里取出所有身份号 → [(号, 字段路径)]。

    为什么要有这一步：拦名单是哈希，改号之后没人会记得去追加一条。
    本机的 `bot_config.json` 是这些号的权威来源（且已被 gitignore），
    运行时拿它补齐，等于拦名单永远跟着真实身份走。
    没有配置文件（CI / 新克隆）时返回空 —— 那一层就只剩哈希清单。
    """
    path = config_path or LOCAL_CONFIG
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return []

    found = []

    def walk(node, trail):
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, f"{trail}/{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{trail}[{i}]")
        elif trail.rsplit("/", 1)[-1].split("[")[0].lower() in IDENTITY_KEYS:
            s = str(node).strip()
            if re.fullmatch(r"\d{5,12}", s):
                found.append((s, trail))

    walk(data, "")
    return found


def blocked_digests(extra: list | None = None) -> dict:
    """拦名单 + 本机配置 → {digest: 说明}。"""
    out = dict(load_blocklist())
    for num, trail in local_identities():
        out.setdefault(digest(num), f"本机配置 {trail}")
    for num, note in (extra or ()):
        out.setdefault(digest(num), note)
    return out


# ─────────────────────────── 第二层：合成段 ───────────────────────────
#: 仓库既定的合成段。加新段要连**为什么这个段不可能撞真号**一起写，
#: 否则半年后没人敢删、也没人敢用。
#: 注意第二层的窗口是 8-11 位，所以这里只列 8-11 位能落进去的段；5-7 位的
#: 占位号（`10003` / `654321`）第二层根本不看，不需要在这里开豁免。
SYNTH_PATTERNS = (
    (r"^1000000\d{1,4}$", "1000000xxx 段：合成 uid"),
    (r"^9000000\d{2}$", "9000000xx 段：合成群号"),
    (r"^1008\d{4}$", "10086xxx 段：qq_instructions 假对话"),
    (r"^1234567\d{0,3}$", "键盘序（123456789 / 1234567890 / +2 +3 +4）"),
    (r"^9{6,10}$", "全 9"),
    (r"^20000000\d$", "20000000x 段：preview 假群号"),
    (r"^3100000\d{2}$", "3100000xx 段：loadtest 压测号"),
)
_SYNTH_RES = tuple((re.compile(p), why) for p, why in SYNTH_PATTERNS)

#: 拦名单之外还要放行的：早期夹具里手写的占位号，形状不在上面任何一段里。
#: 每加一条都要能说出「为什么它不可能是真号」，加不动就说明该改用合成段。
#: 新增夹具请优先用 SYNTH_PATTERNS 里的段，别往这里堆。
SYNTH_EXPLICIT = {
    "741852963": "九宫格 1/4/7-2/5/8-3/6/9 竖列",
    "963258741": "九宫格 9/6/3-2/5/8-7/4/1 竖列",
}


def synth_reason(num: str) -> str | None:
    """是合成号就返回它属于哪一段，否则 None。"""
    if num in SYNTH_EXPLICIT:
        return SYNTH_EXPLICIT[num]
    for pat, why in _SYNTH_RES:
        if pat.match(num):
            return why
    return None


def is_synthetic(num: str) -> bool:
    return synth_reason(num) is not None


# ─────────────────────────── 身份位 ───────────────────────────
#: 协议层装号的键。`sender`/`target` 这类短键必须靠「右侧是 8-11 位数字」兜住，
#: 否则 `target="collected/x.jpg"` 也会被当成一次命中。
_IDENT_KEYS = (
    "qq|uin|uid|gid|sender|sender_id|user_id|group_id|target|target_id|"
    "target_qq|target_qqs|group_ids|bot_qq|admin_qqs?|peer_id|self_id|"
    "operator_id|at_qq|to_qq|focus_qq|qq_of|owner_qq|master_qq"
)
_RE_IDENT = re.compile(
    r"(?i)(?<![\w])(?:%s)[\"']?\s*[:=]\s*\[?\s*[\"']?(\d{8,11})(?!\d)" % _IDENT_KEYS)
#: 第一层要扫全窗口：拦名单里可能躺着 5-8 位的老群号，不能跟着第二层一起收窄
_RE_NUM = re.compile(r"(?<!\d)(\d{5,12})(?!\d)")
#: 13 位以上：整串不可能是号，但真号可能被拼在里头。见 `blocked_in` 的滑窗。
_RE_LONGRUN = re.compile(r"(?<!\d)(\d{13,})(?!\d)")
_NUM_MIN, _NUM_MAX = 5, 12
#: 长数字串的长度上限。超过它就不切了 —— 那不是「号被拼进去」，
#: 是一段哈希/序列化数据，切它在时间和收益上都不划算。
_LONGRUN_CAP = 4096

#: 判据文件自己。自检夹具就是「违规输入」的样例，第二层对它关掉（第一层照查）。
_SELF_FILE = os.path.basename(os.path.abspath(__file__))


def is_judge_file(path: str) -> bool:
    return bool(path) and os.path.basename(path.replace("\\", "/")) == _SELF_FILE


# ─────────────────────────── 判定 ───────────────────────────
class Hit:
    """一处命中。`kind` 决定调用方怎么措辞：`blocked` 是回归，`identity` 是新号。"""

    __slots__ = ("line", "kind", "value", "why", "col")

    def __init__(self, line, kind, value, why, col=0):
        self.line, self.kind, self.value, self.why, self.col = line, kind, value, why, col

    def __repr__(self):
        return f"Hit({self.line}, {self.kind}, {mask(self.value)}, {self.why!r})"

    def as_dict(self):
        return {"line": self.line, "kind": self.kind, "value": self.value,
                "why": self.why, "col": self.col}


def blocked_in(text: str, blocked: dict) -> list:
    """第一层单独可用：文本里出现的拦名单号 → [(号, 说明, 位置)]。

    提交信息闸要它 —— 那边没有「身份位」这种语法可锚定，只能靠精确匹配。

    两遍：先按「独立数字串」匹配，再对 13 位以上的长串滑窗切 5-12 位的片段。
    第二遍不是保险起见 —— 初版的举例把真号拼在 15 位数字串中间，独立匹配看不见，
    全树扫描报了「干净」。号码的位数是已知的，所以对每个片段只需算一次摘要。
    """
    out = []
    if not blocked:
        return out
    for m in _RE_NUM.finditer(text):
        d = digest(m.group(1))
        if d in blocked:
            out.append((m.group(1), blocked[d], m.start()))
    for m in _RE_LONGRUN.finditer(text):
        run = m.group(1)
        if len(run) > _LONGRUN_CAP:
            continue
        seen = set()
        for size in range(_NUM_MIN, _NUM_MAX + 1):
            for i in range(len(run) - size + 1):
                piece = run[i:i + size]
                if piece in seen:
                    continue
                d = digest(piece)
                if d in blocked:
                    seen.add(piece)
                    out.append((piece, blocked[d], m.start() + i))
    return out


def scan_line(line: str, blocked: dict, check_identity: bool = True) -> list:
    """判一行。返回 [Hit]。`blocked` 来自 `blocked_digests()`。

    `check_identity=False` 只跑第一层（拦名单）。唯一用处是判据文件自己：
    它的自检夹具就是违规样例，第二层会把夹具当成真命中。
    """
    out = []
    if not line:
        return out

    # 第一层：任何位置、任何形态。先跑，不设上下文豁免。
    for num, why, col in blocked_in(line, blocked):
        out.append(Hit(0, "blocked", num, why, col))

    if not check_identity:
        return out

    # 第二层：身份位 + 合成段白名单
    for m in _RE_IDENT.finditer(line):
        num = m.group(1)
        if is_synthetic(num) or digest(num) in blocked:
            continue
        out.append(Hit(0, "identity", num, "身份位出现非合成段的号", m.start(1)))
    return out


def scan_text(text: str, blocked: dict, path: str = "") -> list:
    """判一段文本，行号从 1 起。`path` 只用来认出判据文件自己。"""
    check_identity = not is_judge_file(path)
    out = []
    for lineno, line in enumerate(text.splitlines(), 1):
        for h in scan_line(line, blocked, check_identity):
            h.line = lineno
            out.append(h)
    return out


# ─────────────────────────── 自检 ───────────────────────────
#: 判据自身要用反例证伪。夹具全部是**编出来的**号 —— 这个文件本身进库，
#: 写真号等于把要拦的东西写进拦它的文件里。
def selftest() -> int:
    fake_real = ("9999990123", "8887776665")   # 放进拦名单的「假真号」
    bl = {digest(n): "自检用假真号" for n in fake_real}
    new_num = "2468135791"                     # 不在拦名单、也不是任何合成段
    ok = True

    def chk(name, cond):
        nonlocal ok
        print(("  [OK]   " if cond else "  [FAIL] ") + name)
        ok = ok and bool(cond)

    # 第一层：拦名单里的号，任何位置都要抓
    chk("拦名单命中：赋值", bool(scan_line('uid = "9999990123"', bl)))
    chk("拦名单命中：注释里的裸号", bool(scan_line("# 真机（群 8887776665）", bl)))
    chk("拦名单命中：没被合成段豁免掉", bool(scan_line('gid = "9999990123"', bl)))
    chk("拦名单命中：身份位以外的自由文本", bool(scan_line("日志里写着 8887776665 这个号", bl)))
    # 拼在长数字串中间也要抓：初版漏的就是这一种
    chk("拦名单命中：被拼进 14 位数字串",
        bool(scan_line("id = 77%s77" % fake_real[0], bl)))

    # 第二层：身份位 + 非合成段 → 报
    chk("身份位新号被判", bool(scan_line('"user_id": "%s"' % new_num, {})))
    chk("身份位新号被判（列表）", bool(scan_line('{"target_qqs": ["%s"]}' % new_num, {})))
    chk("身份位新号被判（无引号）", bool(scan_line("group_id=%s" % new_num, {})))

    # 合成段与合法常量 → 放
    for num in ("1000000001", "900000005", "10086001", "123456789", "1234567890"):
        chk(f"合成段放行 {num}", not scan_line(f'"user_id": "{num}"', {}))
    chk("非身份位的裸号不报（地址里的数字）",
        not scan_line('url = "https://example.com/news/12002327.html"', {}))
    chk("非身份位的裸号不报（时间戳）",
        not scan_line('{"ts": 1793426150, "kind": "config"}', {}))
    chk("键名只是含 target 的普通字段不报",
        not scan_line('"target_name": "2035 年计划"', {}))
    chk("六位占位号不在第二层窗口内（只有第一层管得着）",
        not scan_line('"group_id": "654321"', {}))
    chk("判据文件的自检夹具不被第二层误伤",
        not scan_line('x = "user_id": "%s"' % new_num, {}, check_identity=False))

    # 屏蔽：报告里不出现完整号，但位数要留着才能对上
    chk("mask 保留位数、打掉中间",
        mask("2468135791") == "2468****91" and len(mask("2468135791")) == 10)
    chk("mask 对 9 位号也不会露全", mask("735192864") == "7351***64")

    # 拦名单文件本身要可解析
    try:
        load_blocklist()
        chk("identity_blocklist.txt 可解析", True)
    except SystemExit as exc:
        chk(f"identity_blocklist.txt 可解析（{exc}）", False)

    print("\n自检通过" if ok else "\n自检失败")
    return 0 if ok else 1


# ─────────────────────────── CLI ───────────────────────────
_BIN_EXT = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".zip", ".exe", ".dll", ".pyc",
            ".db", ".ttf", ".otf", ".woff", ".woff2", ".pfb", ".bin", ".lock",
            ".ico", ".mp3", ".mp4", ".pdf")


def _repo_files():
    out = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"],
                         cwd=ROOT, capture_output=True).stdout.decode("utf-8", "replace")
    return [f for f in out.splitlines() if f.strip()]


def _scan_repo() -> int:
    bl = blocked_digests()
    files = _repo_files()
    if not files:
        print("[identity] 没有 git 文件清单（不是仓库？）")
        return 2
    total = 0
    for rel in files:
        if rel.lower().endswith(_BIN_EXT):
            continue
        try:
            with open(os.path.join(ROOT, rel), encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError:
            continue
        for h in scan_text(text, bl, rel):
            total += 1
            if total <= 60:
                print(f"  {rel}:{h.line}  [{h.kind}] {mask(h.value)}  {h.why}")
    if total:
        print(f"\n[identity] 命中 {total} 处（拦名单 {len(load_blocklist())} 条）")
        return 1
    print(f"[identity] 干净（{len(files)} 个文件 / 拦名单 {len(load_blocklist())} 条）")
    return 0


def _check_local() -> int:
    ids = local_identities()
    if not ids:
        print("[identity] 没有本机配置（或里面没有身份号），跳过")
        return 0
    bl = load_blocklist()
    miss = [(n, t) for n, t in ids if digest(n) not in bl]
    for num, trail in ids:
        state = "在" if digest(num) in bl else "缺"
        print(f"  [{state}] {trail} = {mask(num)}")
    if miss:
        print(f"\n[identity] 拦名单缺 {len(miss)} 条，用 --add 补：")
        for num, trail in miss:
            print(f'  python tools/identity_numbers.py --add {num} "{trail}"')
        return 1
    return 0


def _add(num: str, note: str, path: str = BLOCKLIST_FILE) -> int:
    if not re.fullmatch(r"\d{5,12}", num):
        print(f"[identity] {num!r} 不像身份号（要 5-12 位纯数字）")
        return 2
    d = digest(num)
    if d in load_blocklist(path):
        print(f"[identity] {mask(num)} 已在拦名单里")
        return 0
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(f"{d}  {note}\n")
    print(f"[identity] 已追加 {mask(num)} → {d}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="身份号判据 / 拦名单维护")
    ap.add_argument("--scan", action="store_true", help="扫全仓（git 文件清单）")
    ap.add_argument("--selftest", action="store_true", help="判据自身的正反例")
    ap.add_argument("--check-local", action="store_true", help="本机配置里的号都在拦名单里吗")
    ap.add_argument("--add", nargs=2, metavar=("号", "说明"), help="往拦名单追加一条")
    args = ap.parse_args(argv)
    if args.add:
        return _add(*args.add)
    if args.selftest:
        return selftest()
    if args.check_local:
        return _check_local()
    return _scan_repo()


if __name__ == "__main__":
    sys.exit(main())
