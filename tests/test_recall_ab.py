# -*- coding: utf-8 -*-
"""tests/test_recall_ab.py —— 撤回反馈的 A/B：不双回，也不哑掉（2026-11-03）

真机 A/B 需要 QQ 在线、真发真撤，不适合放进用例。但这个对照的**判定**可以离线做：
同一条撤回事件，在两种 `recall_feedback_by` 设置下各跑一遍，数一共回了几句。

要证明的三件事：

  A. `core`（默认，行为不变）：主链回 1 句，插件 0 句     → 合计 **1**
  B. `plugin` + 插件启用：主链 0 句，插件 1 句           → 合计 **1**
  C. ★ `plugin` + 插件**没**启用：0 句                   → 合计 **0**

C 不是 bug，是**已知的操作陷阱**：只改配置不开插件，撤回就没人回了。
把它写成用例，是因为这种"配置漂移"在真机上不会报错，只会静默少一句话 ——
只有用例能把它钉住。

（`_handle_recall_event` 的副作用 —— 取消未发出的缓冲区 —— 不在本用例范围，
它跟"要不要回"是两条独立的路：开了 plugin 也照样截断。）
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from _sandbox import sandbox_neurobase  # noqa: E402

sandbox_neurobase()

import bot_core  # noqa: E402
import plugins.recall_manager as rm  # noqa: E402
from plugins.recall_manager import policy as P  # noqa: E402

PASS = 0
FAIL = 0
FAILURES = []

GID = "1000000003"
UID = "1000000001"


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK]   {name}")
    else:
        FAIL += 1
        FAILURES.append(name + (f"  → {extra}" if extra else ""))
        print(f"  [FAIL] {name}  {extra}")


class FakeCtx:
    """插件侧的最小宿主：配置、状态、发送出口。"""

    def __init__(self, by):
        self.by = by
        self.store = {}
        self.sent = []

    def get_config(self, key=None, default=None):
        cfg = {"recall_feedback_by": self.by}
        return cfg if key is None else cfg.get(key, default)

    def get_state(self, key=None, default=None):
        return self.store if key is None else self.store.get(key, default)

    def set_state(self, k, v):
        self.store[k] = v
        return True

    def send_text(self, text, target=None, group_id=None):
        self.sent.append({"text": text, "target": target, "group_id": group_id})
        return True

    def log(self, msg, level="info"):
        pass


def main():
    # ── 打桩：捕获发送与事件，绝不打到真群 ──
    core_sent = []
    events = []
    orig = (bot_core.smart_send, bot_core.smart_send_group, bot_core.get_plugin_manager)

    class _PM:
        def emit_event(self, name, payload=None):
            events.append((name, dict(payload or {})))
            return []

    bot_core.smart_send = lambda text, target=None: core_sent.append(("private", target, text)) or True
    bot_core.smart_send_group = lambda gid, text: core_sent.append(("group", gid, text)) or True
    bot_core.get_plugin_manager = lambda: _PM()

    def run(by, plugin_enabled):
        """跑一条群撤回，返回 (主链发了几条, 插件发了几条)。"""
        del core_sent[:]
        del events[:]
        bot_core._config["recall_feedback_by"] = by
        # 让"值不值得回"通过：bot 最近说过话
        bot_core.GROUP_LAST_BOT_MSG[GID] = time.time()
        bot_core.GROUP_RECENT_MSGS[GID] = [(time.time(), UID, "我刚才说的话")]
        bot_core.RECALL_FEEDBACK_TIME.clear()

        bot_core._handle_recall_event({
            "notice_type": "group_recall", "user_id": UID,
            "group_id": GID, "message_id": "m-%s-%s" % (by, plugin_enabled),
        })
        n_core = len(core_sent)

        n_plugin = 0
        if plugin_enabled:
            ctx = FakeCtx(by)
            for name, payload in events:
                if name == "notice.recall":
                    rm.on_event(ctx, event_name=name, payload=payload)
            n_plugin = len(ctx.sent)
        return n_core, n_plugin

    try:
        print("[A] recall_feedback_by=core（默认，行为不变）")
        core, plug = run("core", plugin_enabled=False)
        check("★ 主链回 1 句", core == 1, core)
        check("插件 0 句", plug == 0, plug)
        check("★ 合计正好 1（不双回）", core + plug == 1, (core, plug))

        print("\n[B] recall_feedback_by=plugin + 插件启用")
        core, plug = run("plugin", plugin_enabled=True)
        check("★ 主链让位（0 句）", core == 0, core)
        check("★ 插件回 1 句", plug == 1, plug)
        check("★ 合计正好 1（不双回、也没哑掉）", core + plug == 1, (core, plug))

        print("\n[C] recall_feedback_by=plugin + 插件没开（已知操作陷阱）")
        core, plug = run("plugin", plugin_enabled=False)
        check("主链让位（0 句）", core == 0, core)
        check("插件没开（0 句）", plug == 0, plug)
        check("★ 合计 0 —— 只改一半配置就会静默少一句话", core + plug == 0, (core, plug))

        print("\n[D] 配置写错/缺省时回落到主链")
        bot_core._config.pop("recall_feedback_by", None)
        del core_sent[:]
        del events[:]
        bot_core.GROUP_LAST_BOT_MSG[GID] = time.time()
        bot_core.GROUP_RECENT_MSGS[GID] = [(time.time(), UID, "我刚才说的话")]
        bot_core.RECALL_FEEDBACK_TIME.clear()
        bot_core._handle_recall_event({"notice_type": "group_recall", "user_id": UID,
                                       "group_id": GID, "message_id": "m-default"})
        check("★ 缺省 = core（老配置不改也照旧）", len(core_sent) == 1, core_sent)
    finally:
        bot_core.smart_send, bot_core.smart_send_group, bot_core.get_plugin_manager = orig

    print("\n" + "=" * 60)
    print(f"通过 {PASS} / 失败 {FAIL}")
    if FAILURES:
        print("失败项：")
        for f in FAILURES:
            print("  -", f)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
