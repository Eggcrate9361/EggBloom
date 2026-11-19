# -*- coding: utf-8 -*-
"""源码闸和提交信息闸必须读取同一份本地昵称名单。"""
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import privacy_terms
import commit_lint
import check_secrets

PASS = []
FAIL = []
TD = tempfile.mkdtemp(prefix="privacy-terms-")
PATH = os.path.join(TD, "nicks.txt")


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name if cond else f"{name}  {extra}")
    print(f"  [{'OK' if cond else 'FAIL'}] {name}" + (f"  {extra}" if extra else ""))


def main():
    with open(PATH, "w", encoding="utf-8-sig") as f:
        f.write("# local only\n昵称甲\n昵称乙\n")
    got = privacy_terms.load_nicknames(PATH, "昵称乙,昵称丙")
    check("文件与环境变量合并并去重",
          got == ("昵称甲", "昵称乙", "昵称丙"), got)

    old_file = os.environ.get("EGGBLOOM_NICKLIST")
    old_env = os.environ.get(privacy_terms.ENV_NAME)
    old_nicks = check_secrets._NICKS
    old_hist = check_secrets.HISTORICAL_NICK_ALLOW
    try:
        os.environ["EGGBLOOM_NICKLIST"] = PATH
        os.environ[privacy_terms.ENV_NAME] = "昵称丙"
        check_secrets._NICKS = None
        check("源码闸读取共享名单",
              "昵称丙" in check_secrets._load_nicks(),
              check_secrets._load_nicks())
        pattern = commit_lint._nick_pattern()
        check("提交信息闸读取同一名单",
              bool(pattern.search("昵称丙")),
              pattern.pattern)
        check("通用老大仍由内置规则保留",
              bool(commit_lint._RE_NICK.search("老大")),
              commit_lint._RE_NICK.pattern)
        check_secrets._NICKS = ["历史昵称"]
        check_secrets.HISTORICAL_NICK_ALLOW = {
            ("legacy.md", privacy_terms.nick_digest("历史昵称"))
        }
        check("当前树仍拦昵称",
              any(h[2] == "真实昵称/称呼"
                  for h in check_secrets.scan_text("legacy.md", "历史昵称")))
        check("已公开历史按路径+摘要豁免",
              not check_secrets.scan_text(
                  "legacy.md", "历史昵称", historical=True))
        check("历史豁免不扩到其他路径",
              any(h[2] == "真实昵称/称呼"
                  for h in check_secrets.scan_text(
                      "other.md", "历史昵称", historical=True)))
    finally:
        check_secrets._NICKS = old_nicks
        check_secrets.HISTORICAL_NICK_ALLOW = old_hist
        if old_file is None:
            os.environ.pop("EGGBLOOM_NICKLIST", None)
        else:
            os.environ["EGGBLOOM_NICKLIST"] = old_file
        if old_env is None:
            os.environ.pop(privacy_terms.ENV_NAME, None)
        else:
            os.environ[privacy_terms.ENV_NAME] = old_env

    proc = subprocess.run(
        [sys.executable, "-c", "import tools.commit_lint"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    check("commit_lint 可作为包导入", proc.returncode == 0,
          proc.stderr[-160:])

    print(f"\n通过 {len(PASS)} / 失败 {len(FAIL)}")
    for x in FAIL:
        print("  FAIL", x)
    return 1 if FAIL else 0


if __name__ == "__main__":
    try:
        code = main()
    finally:
        shutil.rmtree(TD, ignore_errors=True)
    sys.exit(code)
