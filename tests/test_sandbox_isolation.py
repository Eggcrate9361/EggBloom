# -*- coding: utf-8 -*-
"""tests/test_sandbox_isolation.py —— 测试不许写生产数据目录（2026-11-05）

## 事故

`tests/_sandbox.py` 从 2026-11-04 起就在设 `EGGBLOOM_USER_DATA`，想把日志、
状态、指令台账一起指到临时目录。但 `bot_core` 那边三个数据根全是**写死**的：

    _APPDATA_DIR             = %LOCALAPPDATA%\\EggBloom_v3
    _USER_DATA_DIR_FOR_PORTS = 同上
    _USER_DATA_DIR           = 同上

变量没人读 → 沙箱等于没设。实测后果：

  · `qq_instructions.json` 里堆了 529 条 `[10086001]` / `[10086002]` 的假对话，
    其中 **今天一天 110 条** —— 全量回归跑一次就多一批，WebUI 里能直接看到；
  · `qq_bot.log` 混进测试桩的报错（"磁盘已满（模拟）"、"注册表炸了"），
    用户翻日志会以为真出故障。

`_repatch()` 只改**已经 import 过**的模块属性，而绝大多数测试是
「先 sandbox() 再 import bot_core」—— 首次 import 时路径已定死，之后再没人改。
所以唯一的修法是在 **bot_core 定义路径常量时**就认这个环境变量。

## 判据

  [A] 数据根落在临时沙箱里，不是用户的 AppData
  [B] 三个派生文件（日志 / 状态 / 指令）都跟着走
  [C] 真的写一条指令 → 落到沙箱，生产文件字节数一个不变
  [D] 源码级：根目录常量确实读 `EGGBLOOM_USER_DATA`（防有人改回去）
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from _sandbox import sandbox_neurobase  # noqa: E402

SANDBOX = sandbox_neurobase()

import bot_core as bc  # noqa: E402

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


def _real_appdata() -> str:
    return os.path.join(os.path.expanduser("~"), "AppData", "Local", "EggBloom_v3")


def main():
    real = os.path.normcase(os.path.abspath(_real_appdata()))
    tmp_root = os.path.normcase(os.path.abspath(tempfile.gettempdir()))

    def inside_sandbox(path) -> bool:
        """只要落在系统临时目录、且**不是**生产 AppData 就算隔离住了。

        不要求恰好等于本文件的 SANDBOX：`run_all_tests.py` 会先铺一层自己的
        沙箱（`eggsandbox_*`），子测试再调 `sandbox_neurobase()` 才覆盖环境变量，
        于是 bot_core 首次 import 用的是外层那层路径 —— 同样是临时目录，
        同样没碰生产，判据卡太死只会变成假红。
        """
        p = os.path.normcase(os.path.abspath(str(path)))
        return p.startswith(tmp_root) and not p.startswith(real)

    # ── [A] 数据根 ────────────────────────────────────────────
    check("[A] 环境变量已由 sandbox 设好",
          inside_sandbox(os.environ.get("EGGBLOOM_USER_DATA", "")),
          os.environ.get("EGGBLOOM_USER_DATA"))
    check("[A] _USER_DATA_DIR 在沙箱内", inside_sandbox(bc._USER_DATA_DIR),
          bc._USER_DATA_DIR)
    check("[A] _APPDATA_DIR 在沙箱内", inside_sandbox(bc._APPDATA_DIR),
          bc._APPDATA_DIR)
    check("[A] _USER_DATA_DIR 不等于生产路径",
          os.path.normcase(os.path.abspath(bc._USER_DATA_DIR)) != real,
          bc._USER_DATA_DIR)

    # ── [B] 派生文件 ──────────────────────────────────────────
    for attr in ("LOG_FILE", "STATE_FILE", "INSTRUCTION_FILE"):
        p = getattr(bc, attr, None)
        if p is None:
            check(f"[B] {attr} 存在", False, "属性不存在")
            continue
        check(f"[B] {attr} 在沙箱内", inside_sandbox(p), p)

    # ── [C] 真写一条，生产文件不许变 ──────────────────────────
    real_inst = os.path.join(_real_appdata(), "qq_instructions.json")
    real_log = os.path.join(_real_appdata(), "qq_bot.log")
    size_before = {}
    for p in (real_inst, real_log):
        try:
            size_before[p] = os.path.getsize(p)
        except OSError:
            size_before[p] = None

    probe = "【沙箱隔离探针】这条不该出现在生产台账里"
    bc.save_instruction(probe, "（测试回执）")

    written = getattr(bc, "INSTRUCTION_FILE", "")
    check("[C] 指令写到了沙箱文件", inside_sandbox(written), written)
    hit = False
    try:
        with open(written, "r", encoding="utf-8") as f:
            hit = probe in f.read()
    except OSError as e:
        check("[C] 能读回沙箱文件", False, e)
    check("[C] 探针真的落盘了（不是静默丢弃）", hit)

    for p, before in size_before.items():
        if before is None:
            continue          # 生产上本来就没这个文件，无从比较
        try:
            after = os.path.getsize(p)
        except OSError:
            after = None
        check(f"[C] 生产文件没被写：{os.path.basename(p)}", after == before,
              f"{before} → {after}")

    # ── [D] 源码级，防回退 ────────────────────────────────────
    with open(os.path.join(ROOT, "bot_core.py"), "r", encoding="utf-8") as f:
        src = f.read()
    check("[D] 根目录常量读环境变量",
          'os.environ.get("EGGBLOOM_USER_DATA")' in src)
    check("[D] 三处都指向同一个根（不再各写各的）",
          src.count("_USER_DATA_ROOT") >= 4,
          src.count("_USER_DATA_ROOT"))
    # 只查「直接把写死路径赋给数据根」这一种写法；fallback 里出现 expanduser 是正当的。
    import re as _re
    bad = _re.findall(
        r"^\s*(?:_USER_DATA_DIR|_APPDATA_DIR|_USER_DATA_DIR_FOR_PORTS)\s*=\s*"
        r"os\.path\.join\(os\.path\.expanduser", src, _re.M)
    check("[D] 数据根不再裸写死路径", not bad, bad)

    print(f"\n{'=' * 56}\n  PASS={PASS}  FAIL={FAIL}")
    for f in FAILURES:
        print(f"  ✗ {f}")
    print("=" * 56)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
