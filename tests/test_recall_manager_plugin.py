# -*- coding: utf-8 -*-
"""tests/test_recall_manager_plugin.py —— 撤回反馈插件接线（2026-11-03，刀 2）

锁六件事：
  A. 默认**关闭**（主链还在自己回，两边同时开会双回）
  B. `on_event` 只接 `notice.recall`，别的事件一律不管
  C. 判定通过 → 走 `ctx.send_text` 发一句，群走 group_id、私聊走 target
  D. 判定不通过 → 一个字都不发
  E. 发送成功才写状态（冷却 + 去重键）；同一条 message_id 第二次不再发
  F. 发送失败**不吃冷却** —— 一次网络抖动不该让这个群沉默一分钟
  G. `get_api_status` 会说清楚"主链是否已让位"
"""
import importlib
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

rm = importlib.import_module("plugins.recall_manager")
from plugins.recall_manager import policy as P  # noqa: E402

PASS = 0
FAIL = 0
FAILURES = []

GID = "1000000003"
UID = "1000000001"
# 插件内部取的是真实时钟（time.time()），所以事件时间必须以"现在"为基准，
# 否则 last_bot_msg 会落在 300 秒窗口外，判定直接变成"与 bot 无关"。
NOW_BASE = time.time()


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
    """最小宿主：配置、私有 KV、发送出口。"""

    def __init__(self, cfg=None):
        self.cfg = dict(cfg or {})
        self.store = {}
        self.sent = []
        self.logs = []

    def get_config(self, key=None, default=None):
        return self.cfg if key is None else self.cfg.get(key, default)

    def get_state(self, key=None, default=None):
        return self.store if key is None else self.store.get(key, default)

    def set_state(self, key, value):
        self.store[key] = value
        return True

    def send_text(self, text, target=None, group_id=None):
        self.sent.append({"text": text, "target": target, "group_id": group_id})
        return True

    def log(self, msg, level="info"):
        self.logs.append((level, msg))


class FailSendCtx(FakeCtx):
    def send_text(self, text, target=None, group_id=None):
        self.sent.append({"text": text, "target": target, "group_id": group_id})
        return False


def fire(ctx, **kw):
    """调用 on_event 并等投递线程落地（发送已改为后台线程，不阻塞主循环）。

    每次都清掉内存占位：每个用例都当"第一次见到这条撤回"，
    否则前面用例占过的 message_id 会让后面误判成重复事件。
    """
    rm._INST_RECENT.clear()
    rm.on_event(ctx, **kw)
    time.sleep(0.2)


def wait_state(ctx, timeout=2.0):
    """等后台线程把冷却/去重写进状态（发送是异步的，写完状态才算落地）。"""
    t0 = time.time()
    while time.time() - t0 < timeout:
        st = ctx.get_state(rm.STATE_KEY, {}) or {}
        if st.get("last_fire"):
            return True
        time.sleep(0.02)
    return False


def wait_sent(ctx, n=1, timeout=3.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if len(ctx.sent) >= n:
            return True
        time.sleep(0.02)
    return False

def recall_ev(**kw):
    ev = {"group_id": GID, "operator": UID, "message_id": "m1",
          "buffer_cancelled": False, "last_bot_msg": NOW_BASE - 30,
          "session_active": True, "idle_seconds": 10, "user_recent": True,
          "ts": NOW_BASE}
    ev.update(kw)
    return ev


def main():
    print("[A] 默认关闭")
    check("★ manifest default_enabled = False",
          rm.PLUGIN_MANIFEST.get("default_enabled") is False, rm.PLUGIN_MANIFEST)
    check("插件名对得上", rm.PLUGIN_MANIFEST.get("name") == "recall_manager")

    print("\n[A2] ABI 钩子必须显式登记（漏了会『加载成功但收不到事件』）")
    hooks = getattr(rm, "PLUGIN_HOOKS", {}) or {}
    check("★ PLUGIN_HOOKS 是字典", isinstance(hooks, dict), type(hooks))
    check("★ on_event 已登记", callable(hooks.get("on_event")), sorted(hooks))
    check("on_event 指向同一个函数", hooks.get("on_event") is rm.on_event)
    for h in ("on_load", "on_enable", "on_disable", "on_unload"):
        check(f"{h} 已登记", callable(hooks.get(h)), sorted(hooks))

    print("\n[B] 只接 notice.recall")
    ctx = FakeCtx()
    fire(ctx, event_name="message.in", payload={"group_id": GID})
    fire(ctx, event_name="notice.poke", payload={"group_id": GID})
    check("★ 别的事件一律不发", ctx.sent == [], ctx.sent)
    check("别的事件也不写状态", ctx.store == {}, ctx.store)

    print("\n[C] 群撤回：判定通过就回")
    ctx = FakeCtx()
    fire(ctx, event_name="notice.recall", payload=recall_ev())
    wait_sent(ctx, 1)
    check("★ 发了一条", len(ctx.sent) == 1, ctx.sent)
    check("★ 走 group_id（不是 target）",
          ctx.sent and ctx.sent[0]["group_id"] == GID and ctx.sent[0]["target"] is None, ctx.sent)
    check("发的是文案池里的一句",
          ctx.sent and ctx.sent[0]["text"] in list(P.PRESET_SEEN), ctx.sent)

    print("\n[D] 判定不通过：一个字都不发")
    ctx = FakeCtx()
    fire(ctx, event_name="notice.recall",
                payload=recall_ev(last_bot_msg=0, buffer_cancelled=False))
    check("与 bot 无关 → 不发", ctx.sent == [], ctx.sent)
    ctx = FakeCtx(cfg={"recall_feedback_enable": False})
    fire(ctx, event_name="notice.recall", payload=recall_ev())
    check("开关关掉 → 不发", ctx.sent == [], ctx.sent)

    print("\n[E] 发送成功才写状态；同一条不重复回")
    ctx = FakeCtx()
    fire(ctx, event_name="notice.recall", payload=recall_ev())
    wait_state(ctx)
    st = ctx.get_state(rm.STATE_KEY, {})
    check("★ 写了冷却", bool(st.get("last_fire")), st)
    check("★ 写了去重键", f"{P.fire_key(P.SCENE_GROUP, GID)}:m1" in (st.get("seen") or {}), st)
    n_before = len(ctx.sent)
    fire(ctx, event_name="notice.recall", payload=recall_ev(message_id="m2"))
    check("冷却期内换一条 id 也不回（冷却拦截）", len(ctx.sent) == n_before, ctx.sent)

    print("\n[E2] 冷却过期后，同一条 message_id 仍不重复回")
    ctx = FakeCtx()
    fire(ctx, event_name="notice.recall", payload=recall_ev())
    st = ctx.get_state(rm.STATE_KEY, {})
    key = P.fire_key(P.SCENE_GROUP, GID)
    st["last_fire"][key] = NOW_BASE - 99999            # 手动把冷却拨到很早以前
    ctx.set_state(rm.STATE_KEY, st)
    n_before = len(ctx.sent)
    fire(ctx, event_name="notice.recall", payload=recall_ev(message_id="m1"))
    check("★ 同一条不重复回（去重生效）", len(ctx.sent) == n_before, ctx.sent)

    print("\n[E3] 同一条撤回被推两遍，只发一条（协议层重复推送）")
    ctx = FakeCtx()
    rm._INST_RECENT.clear()
    rm.on_event(ctx, event_name="notice.recall", payload=recall_ev(message_id="dup1"))
    rm.on_event(ctx, event_name="notice.recall", payload=recall_ev(message_id="dup1"))
    time.sleep(0.45)
    check("★ 重复事件只发一条", len(ctx.sent) == 1, ctx.sent)

    print("\n[F] 发送失败不吃冷却")
    ctx = FailSendCtx()
    fire(ctx, event_name="notice.recall", payload=recall_ev())
    check("尝试发了", len(ctx.sent) == 1, ctx.sent)
    check("★ 失败不写状态（否则一次抖动沉默一分钟）",
          ctx.get_state(rm.STATE_KEY, {}) == {}, ctx.store)

    print("\n[G] 私聊撤回走 target")
    ctx = FakeCtx()
    fire(ctx, event_name="notice.recall",
                payload=recall_ev(group_id="", session_active=True, idle_seconds=5))
    wait_sent(ctx, 1)
    check("★ 私聊发了一条", len(ctx.sent) == 1, ctx.sent)
    check("★ 走 target（不是 group_id）",
          ctx.sent and ctx.sent[0]["target"] == UID and ctx.sent[0]["group_id"] is None, ctx.sent)

    print("\n[H] get_api_status 说清主链有没有让位")
    rm.on_load(FakeCtx(cfg={"recall_feedback_by": "core"}))
    st_core = rm.get_api_status()
    check("core 时状态是 idle", st_core.get("status") == "idle", st_core)
    check("★ 提示要先改配置", "recall_feedback_by" in str(st_core.get("detail")), st_core)
    rm.on_load(FakeCtx(cfg={"recall_feedback_by": "plugin"}))
    check("plugin 时状态是 ready", rm.get_api_status().get("status") == "ready")

    print("\n" + "=" * 60)
    print(f"通过 {PASS} / 失败 {FAIL}")
    if FAILURES:
        print("失败项：")
        for f in FAILURES:
            print("  -", f)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
