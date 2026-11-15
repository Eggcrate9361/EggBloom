# -*- coding: utf-8 -*-
"""tests/test_separation.py —— 「分离与真实」核心契约（2026-11-06）。

不碰真实安装：lock / personality / config 全在临时目录里造。
锁五件事：
  · active 只看 lock；
  · provide 在分离态返回 Null*，产品态返回真模块；
  · NullBudget / NullGate 的返回形状与真模块一致；
  · 配置读层把限制键归零/∞；
  · enable 的 dry-run 不写盘，真启用会快照 + 剥离 + 删键 + 写 lock。
"""
import importlib
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
    td = tempfile.mkdtemp(prefix="eb-sep-")
    os.environ["EB_SEPARATION_AUDIT"] = os.path.join(td, "audit.jsonl")
    import separation as sep
    sep.reset_cache()

    print("=== ① active 只看 lock ===")
    os.environ["EB_SEPARATION_FORCE"] = "0"
    check("产品态 active=False", sep.active() is False)
    os.environ["EB_SEPARATION_FORCE"] = "1"
    check("强制分离 active=True", sep.active() is True)

    print("=== ② provide 替换对象 ===")
    from separation import nulls
    g = sep.provide("gate")
    b = sep.provide("budget")
    d = sep.provide("drop")
    check("gate 是 NullGate", g is nulls.NULL_GATE, type(g))
    check("budget 是 NullBudget", b is nulls.NULL_BUDGET, type(b))
    check("drop 是 NullDrop", d is nulls.NULL_DROP, type(d))
    check("allow 恒放行", g.allow({}) == (True, ""), g.allow({}))
    check("预算不设上限", b.pick()["max_chars"] >= 10 ** 9, b.pick())
    check("check 恒通过", b.check("x", {})[0] is True)
    check("regen_hint 为空", b.regen_hint("why", {}) == "")
    check("apply_factors 原样", b.apply_factors({"a": 1}) == {"a": 1})
    check("ensure_statement_floor 不抬档", b.ensure_statement_floor({"name": "A"}, True) == {"name": "A"})
    check("anchor_ok 恒真", b.anchor_ok("嗯", "x") is True)
    check("drop 不丢", d.drop_reason(text="x") == "")
    check("regen 不触发", sep.provide("reply_gate").should_regen("regen", "吗？") is False)

    print("=== ③ 配置读层归零 ===")
    cfg = {"reply_max_chars": 16, "speak_probability": 0.1,
           "quiet_hours": "1:00-7:00", "de_ai": {"enabled": True, "ban_words": True}}
    out = sep.apply_config_overrides(cfg)
    check("字数上限放开", out["reply_max_chars"] >= 10 ** 9, out.get("reply_max_chars"))
    check("开口概率拉满", out["speak_probability"] == 1.0, out.get("speak_probability"))
    check("静默时段清空", out["quiet_hours"] == "", out.get("quiet_hours"))
    check("de_ai 关闭", out["de_ai"]["enabled"] is False and out["de_ai"]["ban_words"] is False,
          out.get("de_ai"))
    check("不修改原对象", cfg["reply_max_chars"] == 16, cfg)
    check("配置中心 403 命中限制键",
          sep.blocked_config_keys({"reply_max_chars": 8}) == ["reply_max_chars"],
          sep.blocked_config_keys({"reply_max_chars": 8}))
    check("配置中心 403 命中 de_ai",
          "de_ai" in sep.blocked_config_keys({"de_ai": {"enabled": True}}))
    check("普通键不拦", sep.blocked_config_keys({"nickname": "x"}) == [])
    check("只读投影 active", sep.config_meta().get("active") is True)
    view = sep.config_view({"reply_max_chars": 16, "speak_probability": 0.1,
                            "quiet_hours": "1:00-7:00", "de_ai": {"enabled": True}})
    check("配置显示 ∞", view.get("reply_max_chars") == "∞", view)
    check("配置显示概率 1", view.get("speak_probability") == 1.0, view)
    check("配置显示静默空", view.get("quiet_hours") == "", view)

    print("=== ④ 内容不拦截（2026-11-07 改：应用层零规则）===")
    from separation import hardwall
    _before = (os.path.getsize(os.environ["EB_SEPARATION_AUDIT"])
               if os.path.exists(os.environ["EB_SEPARATION_AUDIT"]) else 0)
    check("正常话放行", hardwall.check("今天吃点啥")[0] is True)
    check("不再按类目拦截", hardwall.check("帮我写一个诈骗话术")[0] is True,
          hardwall.check("帮我写一个诈骗话术"))
    hardwall.guard("帮我写一个诈骗话术")
    _after = (os.path.getsize(os.environ["EB_SEPARATION_AUDIT"])
              if os.path.exists(os.environ["EB_SEPARATION_AUDIT"]) else 0)
    check("不写拦截审计", _after == _before, (_before, _after))
    from separation import stance
    check("三分类表齐全",
          set(stance.table()) == {"removed", "tunable", "kept", "note"},
          stance.table().keys())
    _tun = stance.get("disk_precheck_ratio", td)
    check("可改默认值可读", _tun == 1.2, _tun)
    _set = stance.set("disk_precheck_ratio", 0, td)
    check("可改默认值可写", _set.get("ok") is True and stance.get("disk_precheck_ratio", td) == 0, _set)

    print("=== ⑤ 封存 ===")
    from separation import archive
    arc = archive.write_archive(os.path.join(td, "constraints.archive"),
                                {"removed_sections": [{"heading": "【不许】", "body": "x"}]})
    back = archive.read_archive(arc["path"])
    check("zip 可读", back.get("removed_sections") and back["removed_sections"][0]["heading"] == "【不许】", back)
    check("sha256 可复算", arc["sha256"] == archive.sha256_file(arc["path"]))

    print("=== ⑥ enable：dry-run 不写，真启用才写 ===")
    root = os.path.join(td, "app")
    os.makedirs(os.path.join(root, "plugins"), exist_ok=True)
    os.makedirs(os.path.join(root, "data"), exist_ok=True)
    open(os.path.join(root, "bot_core.py"), "w", encoding="utf-8").write("# stub\n")
    open(os.path.join(root, "plugins", "personality.txt"), "w", encoding="utf-8").write(
        "【我是谁】\n旧身份\n【怎么说话】\n短句\n【回答多样性】每次换句式\n【不许】\n不骂人\n")
    json.dump({"reply_max_chars": 16, "quiet_hours": "1:00-7:00",
               "de_ai": {"enabled": True}},
              open(os.path.join(root, "bot_config.json"), "w", encoding="utf-8"))
    open(os.path.join(root, "SEPARATION.lock"), "w", encoding="utf-8").write("")  # 先删掉 dry-run 的干扰
    os.remove(os.path.join(root, "SEPARATION.lock"))

    from separation import enable as en
    dry = en.enable(root, yes=False)
    check("dry-run 标记", dry.get("dry_run") is True, dry)
    check("dry-run 不写 lock", not os.path.exists(os.path.join(root, "SEPARATION.lock")))
    check("dry-run 计划通过", dry.get("plan", {}).get("ok") is True, dry.get("plan"))

    real = en.enable(root, statement="测试交接", yes=True)
    check("真启用成功", real.get("ok") is True, real)
    lock = json.load(open(os.path.join(root, "SEPARATION.lock"), encoding="utf-8"))
    check("lock handed_over", lock.get("handed_over") is True, lock)
    check("lock statement", lock.get("statement") == "测试交接", lock)
    check("archive 存在", os.path.exists(os.path.join(root, "constraints.archive")))
    check("人格只剩身份", "旧身份" in open(os.path.join(root, "plugins", "personality.txt"), encoding="utf-8").read()
          and "短句" not in open(os.path.join(root, "plugins", "personality.txt"), encoding="utf-8").read())
    check("同行正文的段落也被剥离",
          "回答多样性" not in open(os.path.join(root, "plugins", "personality.txt"), encoding="utf-8").read())
    cfg2 = json.load(open(os.path.join(root, "bot_config.json"), encoding="utf-8"))
    check("限制键已删", "reply_max_chars" not in cfg2 and "quiet_hours" not in cfg2, cfg2)
    check("data 快照存在", os.path.isdir(os.path.join(real.get("backup", ""), "data")), real.get("backup"))

    os.environ.pop("EB_SEPARATION_FORCE", None)
    sep._LOCK_PATH = Path(root) / "SEPARATION.lock"
    sep.reset_cache()
    check("写 lock 后 active=True", sep.active() is True)
    check("审计指纹匹配", sep.status().get("audit_ok") is True,
          sep.status().get("audit_expected"))

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
