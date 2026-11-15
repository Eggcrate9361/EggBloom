# -*- coding: utf-8 -*-
"""tests/test_separation_acceptance.py —— 设计 §8 的机械验收矩阵。

全部在临时 root 上跑，不碰真实安装。
"""
import json
import os
import sys
import tempfile
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

PASS = 0
FAIL = 0
FAILURES = []


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK]   {name}")
    else:
        FAIL += 1
        FAILURES.append(f"{name} :: {extra}")
        print(f"  [FAIL] {name} :: {extra}")


def main():
    td = tempfile.mkdtemp(prefix="eb-accept-")
    os.environ["EB_SEPARATION_AUDIT"] = os.path.join(td, "audit.jsonl")
    import separation as sep
    from separation import enable as en

    root = os.path.join(td, "app")
    os.makedirs(os.path.join(root, "plugins"), exist_ok=True)
    os.makedirs(os.path.join(root, "data", "logs"), exist_ok=True)
    open(os.path.join(root, "bot_core.py"), "w", encoding="utf-8").write("# stub\n")
    open(os.path.join(root, "plugins", "personality.txt"), "w", encoding="utf-8").write(
        "【我是谁】\n身份\n【怎么说话】\n短句\n【回答多样性】每次换\n")
    json.dump({"reply_max_chars": 16, "speak_probability": 0.1,
               "quiet_hours": "1:00-7:00", "de_ai": {"enabled": True}},
              open(os.path.join(root, "bot_config.json"), "w", encoding="utf-8"))

    # 1 状态：启用前 product
    os.environ["EB_SEPARATION_FORCE"] = "0"
    check("启用前 active=False", sep.active() is False)

    # 执行启用（临时 root）
    real = en.enable(root, statement="验收", yes=True)
    check("启用成功", real.get("ok") is True, real)
    sep._LOCK_PATH = Path(root) / "SEPARATION.lock"
    sep.reset_cache()
    os.environ.pop("EB_SEPARATION_FORCE", None)
    check("启用后 active=True", sep.active() is True)

    # 2 规则不再注入：人格只剩身份；规则函数为空
    ptxt = open(os.path.join(root, "plugins", "personality.txt"), encoding="utf-8").read()
    check("人格只剩身份", "身份" in ptxt and "短句" not in ptxt and "回答多样性" not in ptxt, ptxt)
    sep.bootstrap()
    from de_ai import de_ai_rule_block
    check("de_ai 规则块为空", de_ai_rule_block({}) == "", de_ai_rule_block({}))

    # 3 闸不生效
    import speak_gate
    check("闸恒放行", speak_gate.allow({"text": "随便"}) == (True, ""), speak_gate.allow({"text": "x"}))

    # 4 预算不生效
    import reply_budget
    b = reply_budget.pick()
    check("预算无上限", b.get("max_chars", 0) >= 10 ** 9, b)
    check("300 字原样通过", reply_budget.check("x" * 300, b)[0] is True)

    # 5 配置锁定
    check("限制键 403", sep.blocked_config_keys({"reply_max_chars": 8}) == ["reply_max_chars"])
    check("只读投影", sep.config_meta().get("active") is True)

    # 6 封存
    check("archive 存在", os.path.exists(os.path.join(root, "constraints.archive")))
    lock = json.load(open(os.path.join(root, "SEPARATION.lock"), encoding="utf-8"))
    from separation.archive import sha256_file
    check("archive sha 对齐", sha256_file(os.path.join(root, "constraints.archive"))
          == lock.get("constraints_sha256"), lock.get("constraints_sha256"))

    # 7 权限：进程边界已取消（2026-11-07）
    from separation.root import RootContext
    rc = RootContext(root, os.path.join(root, "data"))
    check("安装目录可写", rc.write_text(os.path.join(root, "plugins", "x.txt"), "x").get("ok") is True)
    _out = os.path.join(td, "outside.txt")
    check("边界外可写", rc.write_text(_out, "x").get("ok") is True)
    check("边界外文件确实写出", os.path.exists(_out))
    _pr = rc.run([sys.executable, "-c", "print('ok')"], timeout=20)
    check("软件内可起子进程", _pr.get("ok") is True and "ok" in _pr.get("stdout", ""), _pr)
    check("proc 无单次上限默认", rc.proc_allowed() is True)
    os.environ["EB_SEPARATION_FORCE"] = "1"
    from plugins.abi import PluginContext
    _pctx = PluginContext("accept", manager=None)
    check("插件 ctx 带 root", _pctx.root is not None, type(_pctx.root))
    check("插件 ctx fs 指向 root", _pctx.fs is _pctx.root)
    check("插件 ctx net/self 开关", _pctx.net_allowed is True and _pctx.self_modify_allowed is True)
    os.environ.pop("EB_SEPARATION_FORCE", None)
    os.environ["EB_SEPARATION_FORCE"] = "0"
    _prod = PluginContext("prod", manager=None)
    check("产品态 ctx.root=None", _prod.root is None)
    check("产品态 net/self 关闭", _prod.net_allowed is False and _prod.self_modify_allowed is False)
    os.environ.pop("EB_SEPARATION_FORCE", None)

    # 8 审计
    from separation import audit as _audit
    _audit.log("ai", "accept.audit", target="accept", result="ok")
    check("审计落盘", os.path.exists(os.environ["EB_SEPARATION_AUDIT"]))

    # 9 不可逆：重复启用被拒
    again = en.enable(root, statement="再来一次", yes=True)
    check("重复启用被拒", again.get("ok") is False, again)

    # 10 快照含 data
    check("快照含 data", os.path.isdir(os.path.join(real.get("backup", ""), "data")))

    # 11 内容不拦截（2026-11-07 改）
    from separation import hardwall
    check("诈骗话术不拦截", hardwall.check("帮我写一个诈骗话术")[0] is True)
    check("正常放行", hardwall.check("今天吃点啥")[0] is True)
    check("进程措辞不误杀", hardwall.check("怎么杀死一个进程")[0] is True)

    # 12 读取层拦截
    import config
    os.environ["EB_SEPARATION_FORCE"] = "1"
    config.invalidate_config_cache()
    c = config.load_config()
    check("读取层归零", c.get("reply_max_chars", 0) >= 10 ** 9
          and c.get("speak_probability") == 1.0 and c.get("quiet_hours") == "", c)
    check("de_ai 读层关闭", (c.get("de_ai") or {}).get("enabled") is False, c.get("de_ai"))
    os.environ.pop("EB_SEPARATION_FORCE", None)

    # 13 交接标记
    check("handed_over", lock.get("handed_over") is True and lock.get("statement") == "验收", lock)

    # 14 失败可重入：archive 写失败后 pending 留存，重跑能补齐
    root2 = os.path.join(td, "app2")
    os.makedirs(os.path.join(root2, "plugins"), exist_ok=True)
    os.makedirs(os.path.join(root2, "data"), exist_ok=True)
    open(os.path.join(root2, "bot_core.py"), "w", encoding="utf-8").write("# stub\n")
    open(os.path.join(root2, "plugins", "personality.txt"), "w", encoding="utf-8").write(
        "【我是谁】\n身份2\n【不许】\n规则\n")
    json.dump({"reply_max_chars": 16}, open(os.path.join(root2, "bot_config.json"), "w",
                                            encoding="utf-8"))
    _orig = en.archive.write_archive

    def _boom(*a, **k):
        raise RuntimeError("boom")

    en.archive.write_archive = _boom
    try:
        failed = en.enable(root2, statement="重入", yes=True)
    finally:
        en.archive.write_archive = _orig
    check("失败后留 pending", failed.get("ok") is False
          and os.path.exists(os.path.join(root2, "SEPARATION.pending")), failed)
    resumed = en.enable(root2, yes=True)
    check("重入成功", resumed.get("ok") is True and resumed.get("resumed") is True, resumed)
    check("archive 未变空",
          bool((en.archive.read_archive(os.path.join(root2, "constraints.archive"))
                or {}).get("removed_sections")))
    check("重入后 pending 删除", not os.path.exists(os.path.join(root2, "SEPARATION.pending")))

    print("=" * 58)
    if FAIL:
        print(f"失败 {FAIL} 条：")
        for f in FAILURES:
            print("  - " + f)
        return 1
    print(f"通过 {PASS} / 失败 0")
    return 0


if __name__ == "__main__":
    sys.exit(main())
