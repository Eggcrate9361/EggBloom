# -*- coding: utf-8 -*-
"""tests/test_attach_guard.py —— 别重复拉起框架（2026-11-05）

## 现场

09:23 手动提权起了 SnowLuma；09:27 EggBloom 又拉起第二个（普通权限）。
两个实例抢 5099/3000/3001，用户打开 WebUI 看到的是那个注入不了 QQ 的。

## 根因

`start()` → `_attach_existing()` 只认"OneBot API 是否就绪"。SnowLuma 从启动到
OneBot 监听要 10~15 秒，这期间 API 不通 → 被判定"没在跑" → 又拉起一个。

## 判据

① API 不通但框架端口开着 → 等它就绪并**接管**（不拉起第二个）
② API 不通、端口也不开 → 立刻返回 False（该拉起就拉起，不白等）
③ 端口判定读 `webui_port` / `default_webui_port`
"""
import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from _sandbox import sandbox_neurobase  # noqa: E402

sandbox_neurobase()

from protocols.onebot_std_adapter import OneBotStdAdapter  # noqa: E402

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


class Fake(OneBotStdAdapter):
    """可控的适配器：probe 结果与端口状态都由测试指定。"""

    name = "fake"
    display_name = "Fake"
    default_webui_port = 65001

    def __init__(self, ready_seq, port_open):
        # 绕过父类 __init__（它会读配置/建 guard），只带上必要字段
        self._ready_seq = list(ready_seq)
        self._port_open = port_open
        self._running = False
        self._bridge_started = False
        self._attached = False
        # webui_port 是基类只读 property，Fake 直接用 default_webui_port 即可

    def probe_ready(self, timeout=2):
        return self._ready_seq.pop(0) if self._ready_seq else False

    def _framework_port_open(self):
        return self._port_open

    def _start_event_bridge(self):
        self._bridge_started = True

    def _wait_ready(self):
        return None

    def _notify(self, level, msg):
        self._attached = True


def main():
    print("[A] API 不通但端口开着 → 等它就绪并接管（不重复拉起）")
    # probe: 第一次 False（API 未就绪），之后 True（就绪）
    f = Fake([False, True], port_open=True)
    t0 = time.time()
    ok = f._attach_existing()
    cost = time.time() - t0
    check("★ 接管成功", ok is True, ok)
    check("★ 起了事件桥", f._bridge_started is True)
    check("★ 标记为运行中", f._running is True)
    check("确实等了（没有立刻拉第二个）", cost >= 0.5, round(cost, 2))

    print("\n[B] API 不通、端口也不开 → 立刻返回 False（该拉起就拉起）")
    f2 = Fake([False], port_open=False)
    t0 = time.time()
    ok2 = f2._attach_existing()
    cost2 = time.time() - t0
    check("★ 返回 False", ok2 is False, ok2)
    check("没白等（<0.4s）", cost2 < 0.4, round(cost2, 2))
    check("没起桥", f2._bridge_started is False)

    print("\n[C] API 直接就绪 → 直接接管")
    f3 = Fake([True], port_open=False)
    ok3 = f3._attach_existing()
    check("★ 直接接管", ok3 is True and f3._bridge_started is True, ok3)

    print("\n[D] 端口判定读的是框架自己的端口")
    src = open(os.path.join(ROOT, "protocols", "onebot_std_adapter.py"),
               encoding="utf-8", errors="replace").read()
    check("★ 有 _framework_port_open", "def _framework_port_open(" in src)
    check("读 webui_port / default_webui_port",
          "webui_port" in src and "default_webui_port" in src)
    check("★ 端口开时先等再判", "框架端口已开但 API 未就绪" in src)
    check("等了但有限度（20 次 ≈ 20 秒）", "for _ in range(20)" in src)

    print("\n" + "=" * 60)
    print(f"通过 {PASS} / 失败 {FAIL}")
    if FAILURES:
        print("失败项：")
        for x in FAILURES:
            print("  -", x)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
