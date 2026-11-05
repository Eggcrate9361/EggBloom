# -*- coding: utf-8 -*-
"""tests/test_elevation_probe.py —— 「我是不是高权限」这个探测必须真的会亮（2026-11-05）

为什么单独建一个文件：这个探测一旦坏了，**所有依赖它的判断都静默走错分支** ——
privhelper 的 /health 永远报 elevated:false，协议页永远给「请右键以管理员身份运行」。
它坏起来没有任何报错，只有行为不对，所以必须有一条用例钉住。

真机由来（2026-11-05 12:5x）：
  以 SYSTEM 起的 privhelper，/health 返回 {"elevated": false}；
  同一进程里补上 argtypes/restype 的实现返回 True。
  根因：`k32.GetCurrentProcess()` 默认 restype 是 32 位 c_int，
  伪句柄 -1 被截断成 0x00000000FFFFFFFF，OpenProcessToken 拿到无效句柄直接失败。

本文件锁三件事：
  [1] 源码里两处实现都设了 argtypes/restype（跨机器都成立，不依赖本机权限）
  [2] privhelper.is_elevated() 与"独立写的参考实现"在同一进程里结论一致
  [3] 不一致时能说清楚差在哪（不是只报一句 False）
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

PASS, FAIL, FAILURES = 0, 0, []


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK]   {name}")
    else:
        FAIL += 1
        FAILURES.append(name + (f"  → {extra}" if extra else ""))
        print(f"  [FAIL] {name}  {extra}")


def _reference_is_elevated():
    """独立写的参考实现：同样的问题，显式声明所有类型。"""
    try:
        import ctypes
        from ctypes import wintypes
        adv, k32 = ctypes.windll.advapi32, ctypes.windll.kernel32
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        adv.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                         ctypes.POINTER(wintypes.HANDLE)]
        adv.OpenProcessToken.restype = wintypes.BOOL
        adv.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                            ctypes.c_void_p, wintypes.DWORD,
                                            ctypes.POINTER(wintypes.DWORD)]
        adv.GetTokenInformation.restype = wintypes.BOOL
        h = wintypes.HANDLE()
        if not adv.OpenProcessToken(k32.GetCurrentProcess(), 0x0008, ctypes.byref(h)):
            return None
        buf, need = ctypes.c_ulong(0), ctypes.c_ulong(0)
        ok = adv.GetTokenInformation(h, 20, ctypes.byref(buf),
                                     ctypes.sizeof(buf), ctypes.byref(need))
        k32.CloseHandle(h)
        if not ok:
            return None
        return bool(buf.value)
    except Exception:
        return None


def main():
    print("[1] 两处实现都显式声明了 ctypes 类型（跨机器判据）")
    targets = [
        ("harness/privhelper.py", "def is_elevated("),
        ("webui/webui_server.py", "def _self_is_elevated("),
    ]
    for rel, head in targets:
        path = os.path.join(ROOT, rel)
        with open(path, encoding="utf-8", errors="replace") as f:
            src = f.read()
        i = src.find(head)
        body = src[i:i + 1600] if i >= 0 else ""
        check(f"{rel} 有 {head.strip()}", i >= 0)
        check(f"{rel} 设了 GetCurrentProcess.restype",
              "GetCurrentProcess.restype" in body, body[:60])
        check(f"{rel} 设了 OpenProcessToken.argtypes",
              "OpenProcessToken.argtypes" in body, body[:60])
        check(f"{rel} 设了 GetTokenInformation.argtypes",
              "GetTokenInformation.argtypes" in body, body[:60])

    print("\n[2] 与参考实现在同一进程里结论一致")
    from harness import privhelper
    got = privhelper.is_elevated()
    ref = _reference_is_elevated()
    check("参考实现能给出结论（不是探测失败）", ref is not None, ref)
    check("★ privhelper.is_elevated() == 参考实现", got == ref, f"got={got} ref={ref}")

    print("\n[3] 参考实现本身可信：与 whoami 的完整性标签对得上")
    # 只在本机能取到 whoami 时判；取不到就跳过，不让测试变成"环境依赖"
    try:
        import subprocess
        out = subprocess.run(["whoami", "/groups"], capture_output=True,
                             text=True, encoding="utf-8", errors="replace",
                             timeout=15).stdout or ""
    except Exception:
        out = ""
    if "Mandatory Label" in out:
        high = "High Mandatory Level" in out or "System Mandatory Level" in out
        check("whoami 的标签与参考实现一致", high == bool(ref), f"whoami_high={high} ref={ref}")
    else:
        print("  [skip] 取不到 whoami 完整性标签")

    print("\n" + "=" * 56)
    print(f"通过 {PASS} / 失败 {FAIL}")
    for x in FAILURES:
        print("  -", x)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
