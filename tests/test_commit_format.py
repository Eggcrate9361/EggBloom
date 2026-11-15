# -*- coding: utf-8 -*-
"""提交规范闸：判据本身要被反例证伪，不能只证明"文档里写了某句话"。

2026-11-13 体检（`提交历史体检-20261113.md` §3）的结论是：上一版这个文件
**判 0 违规，而实际有 6 个**。四个原因分别对应本文件的四组检查：

| 旧闸的毛病 | 现在的判据 |
|---|---|
| 只查行首 `startswith` | `[2]` 用 6 条**原句**做夹具，句中出现的也必须抓到 |
| 短语表 6 个定值 | `[3]` 每个规则一正一反，反例必须先红 |
| `--since` 按日期过滤，扫少了自己不知道 | `[4]` 用基线 sha 划分，且断言扫描数 == `git rev-list` 的数 |
| `[1]` 5 条"文档里有这句话"的自证 | `[1]` 改成文档 ↔ 闸**双向**一致 + 基线可解析 |

闸的实现搬到了 `tools/commit_lint.py`（钩子与发布闸共用同一份），
本文件只做验收：它红了说明判据失效，它绿了说明判据和历史都对得上。
"""
import io
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, ROOT)

try:
    import commit_lint as cl
except ImportError as exc:  # 判据文件被删/改名时要明说，不要静默跳过
    print(f"  [FAIL] 无法导入 tools/commit_lint.py：{exc}")
    sys.exit(1)

_fail = []


def check(name, ok, detail=""):
    if ok:
        print(f"  [OK]   {name}")
    else:
        print(f"  [FAIL] {name}  {detail}")
        _fail.append(name)


def rules_of(msg, policy, rule):
    return [v for v in cl.lint_message(msg, policy) if v["rule"] == rule]


def main():
    policy = cl.load_policy()

    # ── [1] 文档 ↔ 闸 双向一致 ────────────────────────────────
    #  旧 [1] 检查的是"CONTRIBUTING 里出现了格式串"这类字符串存在性，
    #  文档写错了也照样绿。现在检查的是**两边能不能对上**：区块能解析、
    #  规则清单双向相等、基线提交真的存在。
    print("[1] 规范区块与闸一致（双向，不是「文档里有这句话」）")
    problems = cl.check_policy(policy)
    check("文档 commitlint 区块与实现一致", not problems, problems)
    check("类型表非空且包含 feat/fix",
          "feat" in policy["types"] and "fix" in policy["types"], policy.get("types"))
    check("基线提交可解析（活跃/冻结划分的前提）",
          bool(policy.get("baseline")) and cl._rev_exists(policy["baseline"]),
          policy.get("baseline"))
    check("每条规则都写了生效日",
          set(policy.get("rule_since", {})) == set(policy["machine_rules"]),
          sorted(set(policy["machine_rules"]) - set(policy.get("rule_since", {}))))

    # ── [2] 6 条漏判原句必须被抓到 ─────────────────────────────
    #  夹具是**真实提交里的原句**，不是编的。删掉任何一条，这一节会红。
    print("\n[2] 体检点名的 6 个漏判，逐句回放")
    MISSED = [
        ("b250cbf", "按用户指出的「视频接口/真实 OCR/图像」补：", "CM-02"),
        ("64373b2", "「你把谁禁言了」，它复述不出来，或者干脆否认。"
                    "用户原话是「bot 不知道自己干了什么说了什么」", "CM-02"),
        ("9a70c50", "真机 00:40：用户说「生日结束了吧」，bot 回「未找到进程: 了吧」。", "CM-02"),
        ("4671a6b", "生气，而同一轮 ego 注入里用户「情绪 平静 / 状态 neutral」，没有任何依据；", "CM-02"),
        ("31320c1", "11:52 用户「谢谢你」", "CM-02"),
        ("1a88ecd", "用户「你吃的是token？」", "CM-02"),
    ]
    msg_tpl = "fix(batch): 11-06 占位标题\n\n{body}\n"
    for sha, body, rule in MISSED:
        hits = rules_of(msg_tpl.format(body=body), policy, rule)
        check(f"{sha} 的转述句被抓到（{rule}）", bool(hits), body[:40])

    # 反例的另一面：正常的「用户可见 / 用户目录 / 用户问题」不能误伤，
    # 否则闸会因为噪声被 --no-verify 绕过，等于没有闸。
    print("\n[2b] 不误伤正常技术表述")
    for body in ("用户可见功能不变。", "写入用户目录时按权限回退。",
                 "用户问题的复现路径见 issue 模板。"):
        hits = rules_of(msg_tpl.format(body=body), policy, "CM-02")
        check(f"不误判：{body[:14]}", not hits, [h["text"] for h in hits])

    # ── [3] 每条规则一正一反 ──────────────────────────────────
    print("\n[3] 其余规则的正反例")
    cases = [
        ("CM-01", "fix(config): 10-24 日志级别配了不生效\n", True),
        ("CM-01", "fix(config): 日志级别配了不生效\n", False),      # 缺日期
        ("CM-01", "Fix: 10-24 大写类型\n", False),                  # 类型不在表内
        # 反例用合成号 2000000007（不在 DIGIT_ALLOW 里），不用真号 ——
        # 夹具本身也是进库的源码，写真号等于把泄漏搬进测试。
        ("CM-03", "fix(a): 11-06 私聊 2000000007 串人\n", False),   # 10 位非白名单号
        ("CM-03", "fix(a): 11-06 私聊 1000000001 串人\n", True),    # 合成号放行
        ("CM-03", "fix(a): 11-06 备份 20261113 修复\n", True),      # YYYYMMDD 放行
        ("CM-03", "fix(a): 11-06 掩码 0x00000000FFFFFFFF\n", True),  # 十六进制放行
        ("CM-05", "fix(a): 11-06 收口\n\nRESULT: 全量 287 绿\n", False),
        ("CM-05", "fix(a): 11-06 收口\n\n判据：20 条夹具覆盖三种输入\n", True),
        ("CM-06", "fix(a): 11-06 修配置\n\nkey=sk-abcdefghijklmnopqrstuvwxyz\n", False),
        ("CM-07", "fix(a): 11-06 修配置\n\n我顺手把日志也改了\n", False),
        # 夹具用 %USERNAME% 而不是真实账户名样式的占位：CM-08 照判（[^\s]+ 能吃下它），
        # 但 scripts/check_secrets.py 的「本机用户目录」不认 %，不会挡自己的测试。
        ("CM-08", "fix(a): 11-06 修配置\n\n路径 C:\\Users\\%USERNAME%\\Desktop\\a.txt\n", False),
        # CM-09：职场拟人化措辞。正例里的「指定路径」必须放行，否则正常工程汉语被误伤。
        ("CM-09", "feat(agent): 11-02 通读类请求第一轮就派活\n", False),
        ("CM-09", "fix(a): 11-06 收口\n\n按用户指定只改协议页\n", False),
        ("CM-09", "fix(a): 11-06 收口\n\n也可传 path 指定单张；-DistPath 指定\n", True),
        # CM-02 补：带时间/载体的归因也算转述（「用户 09:04 截图」）
        ("CM-02", "fix(a): 11-06 收口\n\n## 现场（用户 09:04 截图 + 日志）\n", False),
        ("CM-02", "fix(a): 11-06 收口\n\n## 现场（09:04 截图 + 日志）\n", True),
    ]
    for rule, msg, should_pass in cases:
        hits = rules_of(msg, policy, rule)
        name = f"{rule} {'放行' if should_pass else '拦住'}：{msg.splitlines()[-1][:34]}"
        check(name, (not hits) if should_pass else bool(hits),
              [h["detail"] for h in hits])

    # ── [4] 真实历史：活跃期 0 未登记违规 ─────────────────────
    #  旧闸的 `--since` 只扫到 105 个却不知道少扫了多少。
    #  这里直接和 `git rev-list --count` 对数，扫少了会红。
    print("\n[4] 真实历史（基线之后的提交必须干净）")
    try:
        expected = int(subprocess.run(["git", "rev-list", "--count", "HEAD"], cwd=ROOT,
                                      capture_output=True, text=True).stdout.strip())
    except (ValueError, OSError):
        print("  [SKIP] 不在 git 仓库里，跳过历史检查")
        expected = 0
    result = cl.audit("HEAD", False, policy)
    if expected:
        check(f"扫描数与 rev-list 一致（{expected} 个提交）",
              result["commits"] == expected, f"{result['commits']} != {expected}")
    check(f"活跃提交数 >= 1（{result['active']}），否则判据没生效", result["active"] >= 1)
    check(f"活跃期未登记违规为 0（已登记 {len(result['debt'])} 条）",
          not result["errors"],
          [(v["sha"][:7], v["rule"], v["text"][:40]) for v in result["errors"][:5]])

    # ── [5] 例外登记不能变成第二个白名单 ───────────────────────
    #  每条必须**仍然真的违规**，过期条目要删；否则登记表会越攒越多，
    #  变成旧闸那种「KNOWN_EXCEPTIONS 里躺着 5 条谁也不知道为什么」。
    print("\n[5] 例外登记（tools/commit_lint_debt.json）")
    live = {cl.debt_key(v["sha"], v["rule"]) for v in result["violations"]}
    stale = [k for k in result["debt"] if k not in live]
    check("没有过期条目（不再违规的要删掉）", not stale, stale)
    check("条目数不超过冻结+活跃违规总数", len(result["debt"]) <= len(result["violations"]),
          f"{len(result['debt'])} > {len(result['violations'])}")
    if os.path.exists(cl.DEBT_FILE):
        data = json.load(io.open(cl.DEBT_FILE, encoding="utf-8"))
        for e in data.get("entries", []):
            check(f"{e['sha']} 写明了处置方式（{e.get('decision')}）",
                  e.get("decision") in ("rewrite", "orphan", "keep"), e.get("decision"))
            check(f"{e['sha']} 有复核日 {e.get('review_by')}", bool(e.get("review_by")))

    # ── [6] 发布闸现状（只报数，不判红） ───────────────────────
    #  两个口径都要打印：--strict 只判活跃窗口，--publish 判整条历史。
    #  只报 --strict 会给出「9 → 0 就干净了」的错觉，而公开时读者看的是整条历史。
    print("\n[6] 发布闸现状（转公开前 --publish 必须为 0）")
    strict = cl.audit("HEAD", False, policy, strict=True)
    pub = cl.audit("HEAD", False, policy, publish=True)
    print(f"        --strict（活跃窗口）：{len(strict['errors'])} 处")
    print(f"        --publish（整条历史）：{len(pub['errors'])} 处 / "
          f"涉及 {len({v['sha'] for v in pub['errors']})} 个提交")
    check("--publish 覆盖的提交数 >= --strict（前者含冻结）",
          len({v["sha"] for v in pub["errors"]}) >= len({v["sha"] for v in strict["errors"]}),
          f"{len({v['sha'] for v in pub['errors']})} < {len({v['sha'] for v in strict['errors']})}")

    # ── [7] 发布清单里的示例命令同一格式 ───────────────────────
    print("\n[7] 发布清单的示例命令同一格式")
    chk = io.open(os.path.join(ROOT, "docs", "RELEASE_CHECKLIST.md"), encoding="utf-8").read()
    check("chore(release) 示例带日期",
          'chore(release): <MM-DD> <version>' in chk)

    print("\n" + "=" * 56)
    if _fail:
        print(f"失败 {len(_fail)}：{_fail}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
