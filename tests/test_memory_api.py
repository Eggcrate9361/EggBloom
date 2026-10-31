# -*- coding: utf-8 -*-
"""tests/test_memory_api.py —— 记忆唯一读写口（FIXPLAN 1.5.11 P0-3）

`harness/memory_api.py` 把散在 bot_core 的五处记忆入口收成 `recall()` / `remember()`。
这个文件锁七件事：

  A. recall 装了几段：身份 → 称呼 → 跨场景 → 检索 → 群画像
  B. 私聊/群聊取的东西不同（私聊走沙漏 light，群聊走 builder + 群画像）
  C. 跨场景开关 `memory_scope_share=False` → 不注入【另一边】
  D. 预算：超了丢后面的段，身份永远先装；丢弃要打日志
  E. 异常隔离：某一段挂了，其它段照常
  F. remember 分发：user_msg / bot_msg / fact 写对地方，event 明确返回 False
  G. 有注入才打 [记忆] 日志（"这轮为什么只注入这些"可解释）

全部打桩，不碰真实沙漏/people 文件。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from harness import memory_api as M  # noqa: E402
import people                       # noqa: E402
import people_alias                 # noqa: E402
import sandglass_bridge as SB       # noqa: E402

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
        FAILURES.append(name + (f"  → {extra}" if extra else ""))
        print(f"  [FAIL] {name}  {extra}")


class Patch:
    """临时替换模块属性，退出时还原。"""

    def __init__(self):
        self.old = []

    def set(self, mod, name, value):
        self.old.append((mod, name, getattr(mod, name, None)))
        setattr(mod, name, value)

    def undo(self):
        for mod, name, val in reversed(self.old):
            setattr(mod, name, val)
        self.old = []


def main():
    logs = []
    p = Patch()
    p.set(M, "_log", logs.append)
    p.set(people, "card", lambda uid, gid="", private=False: f"【人物档案】{uid}")
    p.set(people_alias, "prompt_line", lambda uid: f"【称呼】叫他{uid}")
    p.set(SB, "cross_scene_recent",
          lambda uid, **kw: ["昨晚在群里说想换工作"] if kw.get("exclude_group", "") == "" else [])
    p.set(SB, "build_people_block", lambda gid: f"【群里的人】群{gid}有3个人")
    p.set(SB, "get_memory_prompt_block", lambda q, light=True: f"【私聊记忆】关于「{q}」")
    written = {}
    p.set(SB, "log_user_message", lambda text, **kw: written.setdefault("user", (text, kw)))
    p.set(SB, "log_bot_reply", lambda text, **kw: written.setdefault("bot", (text, kw)))
    p.set(people, "add_fact",
          lambda uid, key, value, scope="global": written.setdefault("fact", (uid, key, value, scope)))

    try:
        print("[A] 私聊：身份 / 称呼 / 跨场景 / 检索")
        out = M.recall("10001", scene="private", query="换工作", private=True,
                       cfg={"memory_scope_share": True})
        check("带身份档案", "【人物档案】10001" in out, out)
        check("带称呼", "【称呼】" in out, out)
        check("带跨场景（私聊取群里说的）", "【另一边】" in out and "换工作" in out, out)
        check("带私聊检索", "【私聊记忆】" in out, out)
        check("私聊不带群画像", "【群里的人】" not in out, out)

        print("\n[B] 群聊：身份 / 跨场景 / 检索 / 群画像")
        logs.clear()
        out = M.recall("10002", scene="group", group_id="777", query="周末",
                       cfg={"memory_scope_share": True})
        check("带身份档案", "【人物档案】10002" in out, out)
        check("带群画像", "【群里的人】群777" in out, out)
        check("群聊不取私聊检索块", "【私聊记忆】" not in out, out)

        print("\n[C] 跨场景开关")
        out = M.recall("10001", scene="private", query="换工作", private=True,
                       cfg={"memory_scope_share": False})
        check("关掉后没有【另一边】", "【另一边】" not in out, out)

        print("\n[D] 预算")
        logs.clear()
        out = M.recall("10003", scene="private", query="换工作", private=True,
                       budget=15, cfg={"memory_scope_share": True})
        check("只保住第一段（身份）", "【人物档案】" in out and "【称呼】" not in out, out)
        check("丢弃有日志", any("超预算丢弃" in x for x in logs), logs)
        check("有注入有日志", any(x.startswith("[记忆]") and "注入" in x for x in logs), logs)

        print("\n[E] 异常隔离")
        p.set(people, "card", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
        out = M.recall("10004", scene="private", query="x", private=True,
                       cfg={"memory_scope_share": True})
        check("档案挂了不影响称呼", "【称呼】" in out, out)
        p.undo()
        # 重新打桩（p.undo 把上面的桩都撤了）
        p.set(M, "_log", logs.append)
        p.set(people, "card", lambda uid, gid="", private=False: f"【人物档案】{uid}")
        p.set(people_alias, "prompt_line", lambda uid: f"【称呼】叫他{uid}")
        p.set(SB, "log_user_message", lambda text, **kw: written.setdefault("user", (text, kw)))
        p.set(SB, "log_bot_reply", lambda text, **kw: written.setdefault("bot", (text, kw)))
        p.set(people, "add_fact",
              lambda uid, key, value, scope="global": written.setdefault("fact", (uid, key, value, scope)))

        print("\n[F] remember 分发")
        check("user_msg → 沙漏",
              M.remember("user_msg", uid="10001", text="在吗", group_id="777",
                         meta={"nick": "阿明"}) is True and written.get("user", ("",))[0] == "在吗",
              written.get("user"))
        check("bot_msg → 沙漏", M.remember("bot_msg", text="在", group_id="777") is True,
              written.get("bot"))
        check("fact → people.add_fact",
              M.remember("fact", uid="10001", text="喜欢猫",
                         meta={"key": "偏好", "scope": "private"}) is True
              and written.get("fact") == ("10001", "偏好", "喜欢猫", "private"),
              written.get("fact"))
        check("event 明确返回 False（没有存储层，不装作成功）",
              M.remember("event", uid="10001", text="今天吃饭") is False)
        check("空文本返回 False", M.remember("user_msg", text="") is False)
        check("未知 kind 返回 False", M.remember("wat", text="x") is False)

        print("\n[G] 没有记忆时不打日志")
        logs.clear()
        p.set(SB, "cross_scene_recent", lambda uid, **kw: [])
        p.set(SB, "build_people_block", lambda gid: "")
        p.set(people, "card", lambda *a, **k: "")
        p.set(people_alias, "prompt_line", lambda uid: "")
        out = M.recall("9", scene="group", group_id="", query="",
                       cfg={"memory_scope_share": True})
        check("返回空串", out == "", out)
        check("空注入不打 [记忆] 日志", not any(x.startswith("[记忆]") for x in logs), logs)
    finally:
        p.undo()

    print("\n" + "=" * 56)
    print(f"通过 {PASS} / 失败 {FAIL}")
    if FAILURES:
        print("失败项：")
        for f in FAILURES:
            print("  -", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
