#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""提交信息闸（commit-msg gate）。

为什么有这个文件
================
2026-11-13 的全量体检（内部设计资料《提交历史体检-20261113》）发现：
`tests/test_commit_format.py` 的 `[3]` 报「2026-11-05 起的 105 个提交正文没有转述句式」，
而实际至少有 6 个提交正文写着「用户说……」。原因不是运气不好，是判据本身错了：

1. 只查**行首**（`l.strip().startswith(p)`）——「按用户指出的…」「真机 00:40：用户说…」
   「…用户原话是…」「11:52 用户「谢谢你」」全在句中，一个都扫不到；
2. 短语表只有 6 个定值，`用户指出` / `用户「`（无冒号）/ `按用户` / `根据用户` 都不在表内；
3. 同文件 `[1]` 的 5 条检查是「文档里写了某句话」，属于自证，不验证任何历史事实。

结论：判据和被测对象必须分开写，且判据要能被**反例**证伪。所以这里做三件事：

* 规则实现集中在本文件，`tests/test_commit_format.py` 只负责调用 + 反例回归；
* 规范的机器可读部分写在 `CONTRIBUTING.md` 的 `commitlint` 区块里，本文件**读它**，
  类型表/生效日期/基线提交只有一个来源，改文档即改闸，不会「文档一套、实际一套」；
* 每条规则都能被自身夹具证伪（见 `tests/test_commit_format.py` 的 `[2] 反例`），
  闸报 0 违规时同时打印「扫了多少条」，避免 0 条输入造成的假绿。

规则编号与 `CONTRIBUTING.md §6.2` 的表格一一对应，`--check-policy` 做双向校验。

用法
====
    python tools/commit_lint.py --message-file .git/COMMIT_EDITMSG   # commit-msg 钩子
    python tools/commit_lint.py --rev-range 3832b6b..HEAD            # 扫一段历史
    python tools/commit_lint.py --all-refs --json                    # 全 refs 审计
    python tools/commit_lint.py --check-policy                       # 文档 ↔ 闸 一致性

退出码：0 无 error；1 有 error（或 --strict 下有 warn）；2 用法/环境错误。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import privacy_terms

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
POLICY_DOC = os.path.join(ROOT, "CONTRIBUTING.md")
BEGIN = "<!-- commitlint:begin -->"
END = "<!-- commitlint:end -->"
DEBT_FILE = os.path.join(ROOT, "tools", "commit_lint_debt.json")

from identity_numbers import blocked_digests, blocked_in, is_synthetic, mask  # noqa: E402

#: 规则清单（实现侧）。`enforced` 表示由本文件机器判定；False 的写进规范由人核。
#: 编号与 CONTRIBUTING.md §6.2 表格必须一致，`--check-policy` 会双向比对。
RULE_META = {
    "CM-01": ("标题格式", "error"),
    "CM-02": ("正文写现象，不写转述", "error"),
    "CM-03": ("提交信息不含真实身份号", "error"),
    "CM-04": ("提交信息不含昵称/称呼", "warn"),
    "CM-05": ("不写跑测结果与不可复核的自证", "error"),
    "CM-06": ("不含密钥/凭据", "error"),
    "CM-07": ("不写第一人称心路与对话口吻", "warn"),
    "CM-08": ("不含其他身份残留（邮箱/本机路径）", "error"),
    "CM-09": ("不写职场拟人化措辞", "error"),
}

# ─────────────────────────── 规则正则 ───────────────────────────
# 全部对**整段正文**匹配，不按行首。这是 11-13 体检里最直接的失效点。

# CM-02 转述：主语是「用户」+ 言说动词，或「按/根据用户」。
# 「用户可见」「用户目录」「用户态」这类技术搭配不能误伤 —— 所以动词是必需的，
# 且「问」后面用 (?!题|卷|号|候) 排掉「用户问题」「用户问卷」。
#
# 2026-11-13 补：把「用户 09:04 截图 + 日志」这种**带时间/载体的归因**也算转述。
# 它不写「说」，但把现象挂到了人身上 —— 正例应该只写「09:04 截图 + 日志」。
_RE_TRANSCRIBE = re.compile(
    r"(?:用户|对方|客户|报障人|反馈人)\s*(?:说|称|表示|反馈|指出|要求|原话|提到|问(?!题|卷|号|候)|吐槽|怀疑|反映)"
    r"|(?:按|根据|依照|依据)\s*用户"
    r"|用户\s*\d{1,2}\s*[:：]\s*\d{2}"
    r"|用户\s*(?=截图|日志|反馈|抱怨|指定|指派|原话|话(?!题))"
    r"|用户\s*[：:「]"
)
# 规范/复盘本身要举反例，但那只能豁免**那一行**，不能豁免整条 message：
# 11-13 第一次试跑时按整条 message 豁免，9a70c50 / 4671a6b / 31320c1 / 1a88ecd
# 因为正文里恰好有「判据」二字就整条免疫，6 个漏判只抓到 2 个 —— 同一个坑不能踩两次。
#
# 2026-11-13 收：原来还允许「提到 CM-xx 就免检」，那是给所有人留的后门 ——
# 行尾补一句 `CM-09` 就能把违禁词带过去，等于没有闸。写规则不需要复述词表，
# 正例如「新增规则覆盖身份号、跑测自证、职场口吻三类」（0 违规，实测）。
# 只有**显式**以「反例/正例/示例」开头的行才免 —— 它自带上下文，复核时一眼能看。
_RE_RULE_TALK_LINE = re.compile(r"^\s*(?:反例|正例|示例|不要用|禁止)")

# CM-03 真实身份号：两层判据，实现见 tools/identity_numbers.py（与源码扫描同一份）。
#
# 11-13 的教训是「判据分家」：CM-03 只读提交信息，`check_secrets.py` 只读文件内容，
# 两边各自的号码规则又不一致 —— 结果 11 处真号进了源码，两条闸一条都没命中。
# 现在两边调同一个 `scan_line` / `is_synthetic`，只保留一处定义。
#
# 提交信息这一侧的窗口比源码宽（8-11 位照报，不要求出现在身份位上）：
# 正文是散文，没有 `user_id=` 这种可以锚定的语法，而「群 xxx 里点不开」这种写法
# 读起来再自然不过。误报的代价只是改一句措辞，漏报的代价是号码永久留在
# `git log` 里 —— 不对等，所以偏严。
_RE_DIGITS = re.compile(r"(?<!\d)(\d{8,11})(?!\d)")
_RE_YMD = re.compile(r"^(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])$")
_RE_HEX = re.compile(r"0[xX][0-9A-Fa-f]+")

# CM-04 昵称/称呼：不是号码，但转公开后同样是身份残留。
# 只在 --strict（发布闸）里阻断，平时 warn —— 这些词在技术叙述里可能合法。
_RE_NICK = re.compile(r"老大")


def _nick_pattern():
    """内置规则只放通用称呼；真实昵称由本地环境变量提供，不进仓库。"""
    extra = list(privacy_terms.load_nicknames())
    if not extra:
        return _RE_NICK
    return re.compile(
        "|".join([_RE_NICK.pattern] + [re.escape(x) for x in extra]),
        re.I,
    )
# `小a` 是内置人格预设名，属于产品功能名。描述人格预设时不报，
# 否则「去掉一个预设，只剩 小a / 娱乐向」这种正常改动会被判成身份残留。
_RE_NICK_PERSONA = re.compile(r"(?i)小a(?![a-z])")
_RE_PERSONA_CTX = re.compile(r"人格|预设|persona|下拉|人设")

# CM-05 跑测结果：把一次性执行结果写进永久提交信息，读者无法复核。
_RE_SELFPROOF = re.compile(
    r"RESULT\s*[:：]"
    r"|(?:全量|全部|测试|用例|回归|套件)\s*\d{1,4}\s*(?:条|项|个)?\s*(?:全绿|绿|通过|全过)"
    r"|(?<![\d/])\d{1,4}\s*/\s*\d{1,4}\s*(?:通过|绿|全过)"
    r"|(?:手写率|覆盖率)\s*\d{1,3}\s*%"
)

# CM-06 密钥/凭据
_RE_SECRET = re.compile(
    r"sk-[A-Za-z0-9_\-]{16,}"
    r"|AKID[A-Za-z0-9]{10,}"
    r"|ghp_[A-Za-z0-9]{20,}"
    r"|xox[bap]-[A-Za-z0-9-]{10,}"
    r"|BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY"
    r"|Bearer\s+[A-Za-z0-9\-_.]{20,}"
)

# CM-07 第一人称心路 / 对话口吻（warn：单人项目里「我顺手」并非错误，但它把
# 提交信息写成了日记，读者读不到事实。规范里要求改成被动的事实陈述。）
# 只看行首：行内的「我先吃着饭」是引用 bot 的回复，不是提交者的心路。
_RE_FIRSTPERSON = re.compile(r"^\s*(?:我(?!们)|让我|我来|我先|我顺手|我再|接着上一刀)")

# CM-08 其他身份残留：邮箱、Windows 本机绝对路径、Unix 家目录
_RE_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_RE_WINPATH = re.compile(r"(?i)\b[A-Z]:\\\\?(?:Users|Documents and Settings)\\\\?(?!Public|Default)[^\s，。、）)]{2,}")
#: 服务账号不是个人身份，部署文档里到处都是
_HOME_ALLOW = ("ubuntu", "root", "runner", "app", "deploy", "ec2-user")
_RE_HOMEPATH = re.compile(r"(?<![\w.])/(?:home|Users)/([A-Za-z0-9_.\-]{2,})")

# CM-09 职场拟人化措辞：把程序写成"被派活的下属"、把需求方写成"上级"，
# 读者拿不到技术事实，只拿到一个角色扮演。典型是 9c07149 的
# 「通读/审查类请求第一轮就派活」——准确说法是「第一轮就交给子 agent」。
# 「指定」只在**归因到人**时判：「指定路径 / 指定目标 / -DistPath 指定」
# 是正常工程用语，全词禁会误伤 6 处（历史实测），所以用上下文限定。
_RE_WORKPLACE = re.compile(
    r"派活|派单|派工|派任务|下发任务"
    r"|(?:用户|对方|按|根据|依照)\s*指定"
    r"|指定要"
)


# ─────────────────────────── 规范读取 ───────────────────────────
def load_policy(path: str = POLICY_DOC) -> dict:
    """从 CONTRIBUTING.md 的 commitlint 区块读机器可读规范。

    文档是唯一来源：类型表、生效日期、基线提交都写在这里，闸不重复定义。
    读不到就报错退出（而不是用内置默认值兜底 —— 兜底会让文档失效而没人发现）。
    """
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError as exc:
        raise SystemExit(f"[commit_lint] 读不到规范文档 {path}: {exc}")
    if BEGIN not in text or END not in text:
        raise SystemExit(f"[commit_lint] {path} 里缺少 {BEGIN} / {END} 标记区块")
    block = text.split(BEGIN, 1)[1].split(END, 1)[0]
    block = block.strip()
    block = re.sub(r"^```[a-zA-Z]*\s*", "", block)
    block = re.sub(r"```\s*$", "", block).strip()
    try:
        cfg = json.loads(block)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"[commit_lint] commitlint 区块不是合法 JSON：{exc}")
    for key in ("types", "baseline", "subject_template"):
        if key not in cfg:
            raise SystemExit(f"[commit_lint] commitlint 区块缺少字段 {key}")
    cfg.setdefault("scope_pattern", "[a-z0-9][a-z0-9_,-]*")
    cfg.setdefault("machine_rules", sorted(RULE_META))
    return cfg


# ─────────────────────────── 单条 message 判定 ───────────────────────────
_BLOCKED_CACHE = None


def _blocked() -> dict:
    """拦名单（哈希清单 + 本机配置）。进程内只读一次。"""
    global _BLOCKED_CACHE
    if _BLOCKED_CACHE is None:
        _BLOCKED_CACHE = blocked_digests()
    return _BLOCKED_CACHE


def cm03_numbers(line: str, blocked: dict | None = None) -> list:
    """CM-03 命中 → [(号, 理由)]，同一行同号只算一次。

    两遍，对应 `tools/identity_numbers.py` 的两层：
    1. 拦名单 —— 在**原始行**上精确匹配，位置不限（真号写在注释里也一样是泄漏）；
    2. 形状 —— 抹掉十六进制常量后，8-11 位、非合成段、非 YYYYMMDD 照报。
    """
    blocked = _blocked() if blocked is None else blocked
    out, seen = [], set()
    for num, why, _col in blocked_in(line, blocked):
        if num in seen:
            continue
        out.append((num, f"拦名单里的真实身份号（{len(num)} 位）"))
        seen.add(num)
    for num in _RE_DIGITS.findall(_RE_HEX.sub("", line)):
        if num in seen or _RE_YMD.match(num) or is_synthetic(num):
            continue
        out.append((num, f"疑似真实身份号 {len(num)} 位数字"))
        seen.add(num)
    return out


def lint_message(msg: str, policy: dict | None = None, blocked: dict | None = None) -> list[dict]:
    """返回违规列表：[{rule, level, line, text, detail}]。

    纯函数，不碰文件系统（除 policy / 拦名单由调用方传入）。测试用它对夹具跑反例。
    """
    policy = policy or load_policy()
    out: list[dict] = []

    def add(rule, line, text, detail=""):
        out.append({
            "rule": rule,
            "level": RULE_META[rule][1],
            "line": line,
            "text": text.strip()[:160],
            "detail": detail or RULE_META[rule][0],
        })

    lines = msg.splitlines()
    subject = lines[0] if lines else ""

    # CM-01 标题格式（`!` 是 Conventional Commits 的破坏性标记，可选）
    types = "|".join(re.escape(t) for t in policy["types"])
    scope = policy["scope_pattern"]
    pat = re.compile(rf"^(?:{types})(?:\({scope}\))?!?: \d{{2}}-\d{{2}} \S.*$")
    if not subject.strip():
        add("CM-01", 1, "<空标题>", "标题不能为空")
    elif not pat.match(subject.strip()):
        add("CM-01", 1, subject, f"应为 {policy['subject_template']}")

    for lineno, line in enumerate(lines[1:], 2):
        if not line.strip():
            continue
        if _RE_RULE_TALK_LINE.search(line):
            continue  # 举反例/写规则的那一行自己不算违规

        if _RE_TRANSCRIBE.search(line):
            add("CM-02", lineno, line, "转述句式：改写成日志原文/报错/代码现状")

        # 先把十六进制常量抹掉，否则 0x00000000FFFFFFFF 会撞上数字规则
        for num, why in cm03_numbers(line, blocked):
            add("CM-03", lineno, line, why + f"（{mask(num)}）")

        _nick = _nick_pattern()
        if _nick.search(line):
            add("CM-04", lineno, line, "昵称/称呼残留")
        elif _RE_NICK_PERSONA.search(line) and not _RE_PERSONA_CTX.search(line):
            add("CM-04", lineno, line, "昵称残留（人格名以外的语境）")

        if _RE_SELFPROOF.search(line):
            add("CM-05", lineno, line, "一次性跑测结果，不可复核")

        if _RE_SECRET.search(line):
            add("CM-06", lineno, line, "疑似密钥/凭据，立即撤销并改写")

        if _RE_FIRSTPERSON.search(line):
            add("CM-07", lineno, line, "第一人称心路，改成事实陈述")

        home = _RE_HOMEPATH.search(line)
        if (_RE_EMAIL.search(line) or _RE_WINPATH.search(line)
                or (home and home.group(1).lower() not in _HOME_ALLOW)):
            add("CM-08", lineno, line, "邮箱/本机路径等身份残留")

        if _RE_WORKPLACE.search(line):
            add("CM-09", lineno, line, "职场拟人化措辞：写程序做了什么，不写给人派活")

    # 标题也在规则管辖内（CM-03/06 常常直接写在标题里）
    for num, why in cm03_numbers(subject, blocked):
        add("CM-03", 1, subject, why + f"（{mask(num)}）")
    if _RE_SECRET.search(subject):
        add("CM-06", 1, subject, "疑似密钥/凭据写在标题")
    if _nick_pattern().search(subject):
        add("CM-04", 1, subject, "昵称/称呼写在标题")
    if _RE_WORKPLACE.search(subject):
        add("CM-09", 1, subject, "职场拟人化措辞写在标题")

    # 去重（同一行同规则多次命中只报一次）
    seen, uniq = set(), []
    for v in out:
        key = (v["rule"], v["line"], v["text"])
        if key in seen:
            continue
        seen.add(key)
        uniq.append(v)
    return uniq


# ─────────────────────────── 历史扫描 ───────────────────────────
def _git(*args, check=True) -> str:
    r = subprocess.run(["git", *args], cwd=ROOT, capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    if check and r.returncode != 0:
        raise SystemExit(f"[commit_lint] git {' '.join(args)} 失败: {r.stderr.strip()}")
    return r.stdout


def _rev_exists(rev: str) -> bool:
    return subprocess.run(["git", "rev-parse", "--verify", "--quiet", rev + "^{commit}"],
                          cwd=ROOT, capture_output=True).returncode == 0


def read_commits(rev_range: str = "HEAD", all_refs: bool = False) -> list[dict]:
    """一次 git log 取回 (sha, 日期, message)，避免 N 次子进程。"""
    args = ["log", "--format=%H%x1f%cI%x1f%B%x1e"]
    args.append("--all" if all_refs else rev_range)
    raw = _git(*args)
    out = []
    for chunk in raw.split("\x1e"):
        if not chunk.strip():
            continue
        parts = chunk.split("\x1f", 2)
        if len(parts) < 3:
            continue
        out.append({"sha": parts[0].strip(), "date": parts[1].strip(), "msg": parts[2]})
    return out


def classify(commits: list[dict], policy: dict) -> tuple[set, set]:
    """把提交分成 (活跃, 冻结)。

    基线 = 规范生效的那一笔（`policy["baseline"]`）。
    * 基线之后（`baseline..HEAD` 可达）= 活跃，必须 0 违规；
    * 基线及之前 = 冻结，只统计不阻断（已 push，按规范 §6.5 不重写）；
    * 基线 sha 不存在（orphan 全新历史 / 公开仓库首克隆）= 全是活跃。
    用 sha 集合而不是 `--since` 日期：日期过滤会被 author/committer 时区、
    approxidate 解析影响，11-13 那次的「只扫到 105 个」就是这类问题。
    """
    if policy["baseline"] and _rev_exists(policy["baseline"]):
        active = {c["sha"] for c in commits} & set(_git("rev-list", f"{policy['baseline']}..HEAD").split())
    else:
        active = {c["sha"] for c in commits}
    return active, {c["sha"] for c in commits} - active


def rule_since(policy: dict, rule: str) -> str:
    """该规则的生效日（YYYY-MM-DD）。没单列的规则跟 body_effective 走。

    为什么允许单列：CM-05（不写跑测结果）是 11-13 体检才提出的，
    而 11-05~11-09 的提交按当时口径把 RESULT 块当成「判据」写在正文里。
    把口径写清楚（自 11-10 起判）比事后追认 29 条例外诚实，也比假装
    它们不存在诚实。规则生效日只有这一处，改它要连带改 §6.2 的日期列。
    """
    return policy.get("rule_since", {}).get(rule) or policy.get("body_effective") or policy["effective"]


def audit(rev_range="HEAD", all_refs=False, policy=None, strict=False, publish=False):
    """扫描并判定。

    strict：活跃窗口内 warn 升 error，登记表不作数（日常发布闸）。
    publish：**公开判据** —— 不看活跃/冻结、不看规则生效日、不看登记表。
        为什么需要它：`--strict` 只判基线之后的提交，把 9 处活跃违规清零后它会报
        「通过」，而 42 处冻结泄漏照样躺在 `git log` 里。转公开时那些一样会被看见，
        所以公开前必须跑 publish 口径，而不是把窗口内的账做平就宣布干净。
    """
    policy = policy or load_policy()
    commits = read_commits(rev_range, all_refs)
    active, frozen = classify(commits, policy)
    debt = load_debt()
    strict = strict or publish

    violations = []
    for c in commits:
        day = c["date"][:10]
        for v in lint_message(c["msg"], policy):
            if strict and v["rule"] == "CM-04":
                v["level"] = "error"
            if publish:
                is_active = True
            else:
                is_active = c["sha"] in active and day >= rule_since(policy, v["rule"])
            violations.append({**v, "sha": c["sha"], "date": c["date"],
                               "active": is_active,
                               "debt": (not publish) and debt_key(c["sha"], v["rule"]) in debt})

    # 例外登记只在日常闸里生效；发布闸（--strict / --publish）不认它 ——
    # 转公开前必须真的清理干净，不能靠登记表把问题留在 git log 里。
    errors = [v for v in violations
              if v["level"] == "error" and v["active"] and (strict or not v["debt"])]
    return {
        "commits": len(commits),
        "active": len(active),
        "frozen": len(frozen),
        "violations": violations,
        "errors": errors,
        "debt": debt,
        "policy": policy,
        "publish": publish,
    }


# ─────────────────────────── 例外登记（debt） ───────────────────────────
def debt_key(sha: str, rule: str) -> str:
    return f"{sha[:7]}:{rule}"


def load_debt() -> dict:
    """读取例外登记。每条必须有 sha/rule/why/decision，缺一不可。

    为什么不用「KNOWN_EXCEPTIONS 白名单」那种写法：旧闸把 5 条不合规提交
    列成一组字符串，谁都不知道为什么留、留到什么时候。这里要求写明
    `decision`（重写 / orphan / 永久保留）和 `review_by`（复核日期），
    且 `tests/test_commit_format.py` 会验证每条仍然真的违规 —— 过期条目必须删，
    不能靠「反正白名单里一直留着」蒙过去。
    """
    if not os.path.exists(DEBT_FILE):
        return {}
    try:
        with open(DEBT_FILE, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"[commit_lint] 例外登记表损坏 {DEBT_FILE}: {exc}")
    out = {}
    for entry in data.get("entries", []):
        for key in ("sha", "rule", "why", "decision", "review_by"):
            if not entry.get(key):
                raise SystemExit(f"[commit_lint] 例外条目缺字段 {key}: {entry}")
        out[debt_key(entry["sha"], entry["rule"])] = entry
    return out


def check_policy(policy: dict) -> list[str]:
    """文档 ↔ 闸 双向一致性。返回问题清单（空 = 一致）。"""
    problems = []
    doc_rules = list(policy.get("machine_rules", []))
    if sorted(doc_rules) != sorted(RULE_META):
        problems.append(
            f"文档的 machine_rules 与实现不一致：文档多 {sorted(set(doc_rules) - set(RULE_META))}，"
            f"实现多 {sorted(set(RULE_META) - set(doc_rules))}")
    if policy["subject_template"] not in _read_text(POLICY_DOC):
        problems.append("文档正文里没出现 subject_template 原文")
    if not _rev_exists(policy["baseline"]) and policy["baseline"] != "":
        problems.append(
            f"基线提交 {policy['baseline']} 不存在（历史已 orphan 重写？此时应把 "
            f"baseline 改成空串或新根提交，否则冻结/活跃划分失效）")
    return problems


def _read_text(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return ""


# ─────────────────────────── CLI ───────────────────────────
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="提交信息闸 / 历史审计")
    ap.add_argument("--message-file", help="commit-msg 钩子：待检查的 message 文件")
    ap.add_argument("--rev-range", default="HEAD", help="审计范围（默认 HEAD 全历史）")
    ap.add_argument("--all-refs", action="store_true", help="扫 --all 全部 refs")
    ap.add_argument("--json", action="store_true", help="机器可读输出")
    ap.add_argument("--strict", action="store_true", help="warn 也算违规（发布闸用）")
    ap.add_argument("--publish", action="store_true",
                    help="公开判据：冻结也算、不看生效日与登记表（转公开前跑这个）")
    ap.add_argument("--check-policy", action="store_true", help="只做文档↔闸一致性校验")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    policy = load_policy()

    if args.check_policy:
        problems = check_policy(policy)
        for p in problems:
            print(f"  [FAIL] {p}")
        print("规范与闸一致" if not problems else f"不一致 {len(problems)} 处")
        return 0 if not problems else 1

    if args.message_file:
        with open(args.message_file, encoding="utf-8") as fh:
            msg = fh.read()
        # 注释行（`#` 开头）不是正文，git 本体会剥掉，这里先剥。
        msg = "\n".join(l for l in msg.splitlines() if not l.startswith("#"))
        vs = lint_message(msg, policy)
        blocking = [v for v in vs if v["level"] == "error"
                    or (args.strict and v["level"] == "warn")]
        for v in vs:
            print(f"  [{v['level'].upper()}] {v['rule']} 行{v['line']}: {v['detail']}\n"
                  f"      {v['text']}")
        if blocking:
            print(f"\n[commit_lint] {len(blocking)} 处阻断。规范见 CONTRIBUTING.md §6，"
                  f"确认无误可用 git commit --no-verify 跳过（会在发布闸里暴露）。")
            return 1
        return 0

    # --publish 不自动扩到 --all-refs：公开判据要判「会被公开的那条链」，
    # 也就是 main。本地 backup/* 与 server/* 按 §6.6 永远不推，把它们算进来
    # 只会让数字变大而不可行动；要全量体检时显式加 --all-refs。
    result = audit(args.rev_range, args.all_refs, policy,
                   strict=args.strict, publish=args.publish)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if not result["errors"] else 1

    if not args.quiet:
        counts: dict[str, int] = {}
        for v in result["violations"]:
            scope = "全部" if args.publish else ("活跃" if v["active"] else "冻结")
            key = f"{v['rule']}/{scope}"
            counts[key] = counts.get(key, 0) + 1
        print(f"扫描 {result['commits']} 个提交（活跃 {result['active']} / 冻结 {result['frozen']}）")
        for key in sorted(counts):
            print(f"  {key}: {counts[key]}")
        if result["errors"]:
            shas = {v["sha"] for v in result["errors"]}
            scope = "公开判据（含冻结）" if args.publish else "活跃窗口"
            print(f"\n{scope}违规 {len(result['errors'])} 处 / 涉及 {len(shas)} 个提交：")
            for v in result["errors"][:40]:
                print(f"  {v['sha'][:7]} {v['rule']} {v['detail']}\n      {v['text']}")
    return 0 if not result["errors"] else 1


if __name__ == "__main__":
    sys.exit(main())
