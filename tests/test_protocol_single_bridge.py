# -*- coding: utf-8 -*-
"""tests/test_protocol_single_bridge.py —— 协议层只允许一条事件桥（2026-11-03）

## 为什么单独立一个用例

真机现象：戳一戳反戳两次、撤回反馈发两条。表面是两个功能各坏了，实际是
**同一份事件被投递了两遍**。

根因不在反戳也不在撤回，在协议层：`/api/protocol/start` 每调一次就
`new ProtocolManager()`，旧的既不 stop 也不再被引用 —— 可它的 WS 事件桥
线程还在跑、还在转发。调用几次就有几条桥，每条都往 bot 投一遍。

日志里 `[事件] 收到` 成对出现这件事存在了很久（统计到 2796 组，最早 10-28），
只是因为 message 类有 `message_id` 去重挡着，notice 类没有，才没被发现。

本用例钉住两件事：
  ① 适配器实例上重复调用 `_start_event_bridge` 只起一条桥；
  ② `webui` 的 start 接口有"已在运行就别再 new"的判断。

第 ② 条用源码断言（那是个请求处理器，替身成本高于收益），
写成和 `test_opencode_provider` 一样的形态。
"""
import os
import sys
import threading
import time

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
        FAILURES.append(name + (f"  → {extra}" if extra else ""))
        print(f"  [FAIL] {name}  {extra}")


def main():
    from protocols.onebot_std_adapter import OneBotStdAdapter

    print("[A] 同一实例重复启动，只起一条桥")

    class Fake(OneBotStdAdapter):
        """只保留桥相关字段，不碰网络/子进程。"""

        def __init__(self):
            self.name = "fake-adapter"
            self.display_name = "Fake"
            self._bridge_stop = threading.Event()
            self._bridge_state = "idle"
            self._bridge_thread = None
            self.loops = 0

        def _bridge_loop(self):
            self.loops += 1
            while not self._bridge_stop.is_set():
                time.sleep(0.01)

    f = Fake()
    f._start_event_bridge()
    f._start_event_bridge()
    f._start_event_bridge()
    time.sleep(0.15)
    check("★ 连调三次也只起一条桥", f.loops == 1, f.loops)
    check("★ 记下了线程引用", isinstance(f._bridge_thread, threading.Thread))
    check("线程真的活着", f._bridge_thread.is_alive() is True)

    # 收尾：让桥线程退出，别拖住进程
    f._bridge_stop.set()
    time.sleep(0.1)
    check("置 stop 后线程退出", f._bridge_thread.is_alive() is False)

    print("\n[B] 桥停了之后允许再起（重连场景）")
    f._start_event_bridge()
    time.sleep(0.15)
    check("★ 停掉后能重新起一条", f.loops == 2, f.loops)
    f._bridge_stop.set()
    time.sleep(0.1)

    print("\n[C] webui 的 start 接口要幂等")
    web = open(os.path.join(ROOT, "webui", "webui_server.py"), encoding="utf-8").read()
    seg = web[web.find('/api/protocol/start'):]
    seg = seg[:seg.find("/api/protocol/stop")] if "/api/protocol/stop" in seg else seg[:2000]
    check("★ start 前先查已有 manager", "_existing = getattr(S, \"_protocol_manager\", None)" in seg)
    check("★ 已在运行就直接返回", "_existing.is_running()" in seg)
    check("★ 有明确的『已在运行』回复", "已在运行" in seg)
    check("先判断再 new（顺序不能反）",
          seg.find("is_running()") < seg.find("ProtocolManager()"), seg[:80])

    print("\n" + "=" * 60)
    print(f"通过 {PASS} / 失败 {FAIL}")
    if FAILURES:
        print("失败项：")
        for x in FAILURES:
            print("  -", x)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
