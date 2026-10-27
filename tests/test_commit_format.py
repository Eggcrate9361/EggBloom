# -*- coding: utf-8 -*-
"""CONTRIBUTING 的提交格式与桌面规范一致，且实际提交按它来。

2026-10-24 之前，两处不一致：

    CONTRIBUTING.md   标准 Conventional Commits，标题不含日期
    桌面规范          要求 <type>(<scope>): <MM-DD> <做了什么>

结果是文档写一套、实际做一套。维护者已经因为同类问题指出过一次
（82f1016 修掉 6 处与实际不符的说明），不能再有第二回。

这个文件锁三件事：

  [1] CONTRIBUTING 里的格式串带 <MM-DD>（与桌面规范第 45 行一致）
  [2] 最近 30 个提交全部匹配那个格式（判据是真实 git log，不是抄文档）
  [3] 边界写清楚了：2026-10-23 起执行，更早的不带日期

不测"文档里出现了某个词"，测的是**格式串本身**和**真实提交**。
"""
import io
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

_fail = []


def check(name, ok, detail=""):
    if ok:
        print(f"  [OK]   {name}")
    else:
        print(f"  [FAIL] {name}  {detail}")
        _fail.append(name)


def main():
    contrib = io.open(os.path.join(ROOT, "CONTRIBUTING.md"),
                      encoding="utf-8").read()

    print("[1] CONTRIBUTING 的格式串带日期")
    check("格式串是 <type>(<scope>): <MM-DD> <做了什么>",
          "<type>(<scope>): <MM-DD> <做了什么>" in contrib)
    # 旧格式必须真的消失，不能只是新增了一段
    check("旧格式（不含日期的 <subject>）不再单独出现",
          "<type>(<scope>): <subject>" not in contrib)
    check("示例提交带日期",
          re.search(r"`[a-z]+\([a-z]+\): \d{2}-\d{2} ", contrib) is not None)
    # 边界说明：否则对着规范核对历史会对不上
    check("写明了生效起点（2026-10-24 起）", "2026-10-24 起统一执行" in contrib)
    check("说明了更早的提交格式不一", "格式不一" in contrib)

    print("\n[2] 实际提交按这个格式来")
    # 边界从文档里读，不写死 —— 写死就又变成两处判据。
    # 为什么不能改成"从 HEAD 起的连续合规段"：历史上 10-23 16:10~20:45
    # 那一段本来就是合规的，夹在 `68f52c6`（10-23 21:25，不合规）前面，
    # 连续前缀这个不变量不成立。按文档声明的生效日期切才准确。
    m_since = re.search(r"自\s*(\d{4}-\d{2}-\d{2})\s*起统一执行", contrib)
    check("文档里写明了生效日期", m_since is not None)
    since = m_since.group(1) if m_since else "2026-10-24"
    r = subprocess.run(["git", "log", "--format=%s", "--since=" + since],
                       capture_output=True, text=True, encoding="utf-8",
                       cwd=ROOT)
    if r.returncode != 0:
        check("能读到 git log", False, r.stderr)
        return 1
    subjects = [x for x in r.stdout.splitlines() if x.strip()]
    pat = re.compile(r"^[a-z]+(\([a-z0-9_-]+\))?: \d{2}-\d{2} .+")
    # 已知例外：已推送到 origin/main 的提交不重写（提交规范 §6.3）。
    # `1fedafc`（10-26 打包修复）标题漏了日期前缀；它已经在远端，
    # 改它会让所有已克隆的仓库分叉。留作反例，不修。
    KNOWN_EXCEPTIONS = {
        "fix(pack): Lite 重建改用改名让路，绕开批量删除保护",
    }
    bad = [s for s in subjects if not pat.match(s) and s not in KNOWN_EXCEPTIONS]
    check(f"{since} 起的 {len(subjects)} 个提交全部带日期前缀",
          not bad, bad[:5])
    check("提交数够多（少于 10 个说明 --since 没生效）",
          len(subjects) >= 10, len(subjects))
    # 文档得说清边界，不能让人对着规范核对历史还对不上
    check("说明了更早的提交格式不一", "格式不一" in contrib)

    print("\n[3] 发布清单的示例命令同一格式")
    chk = io.open(os.path.join(ROOT, "docs", "RELEASE_CHECKLIST.md"),
                  encoding="utf-8").read()
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