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

本文件锁四件事：
  [1] 源码里两处实现都设了 argtypes/restype（跨机器都成立，不依赖本机权限）
  [2] privhelper.is_elevated() 与"独立写的参考实现"在同一进程里结论一致
  [3] TokenElevation 与 whoami 完整性标签的关系符合 UAC 配置
      ★ 11-05 修真红：EnableLUA=0 时两者本来就不等价（见判据注释），
        旧用例拿等号硬判，把正确的生产实现判成了红。
  [4] 组合表验证 [3] 的判据不空转（不依赖本机权限）
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


def _enable_lua():
    """UAC 总开关（本机实测 0 = 关闭）。读不到返回 None，判据会走保守分支。"""
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System")
        return int(winreg.QueryValueEx(key, "EnableLUA")[0])
    except Exception:
        return None


def _elevation_label_verdict(high, ref, enable_lua):
    """TokenElevation 与 whoami 完整性标签「该不该相等」的判据。

    ★ 2026-11-05 真红根因：这两个东西问的不是同一个问题。
      · TokenElevation(20)：这个 token 有没有被**提升**；
      · whoami Mandatory Label：进程的**完整性级别**。
      EnableLUA=0 时 Windows 不拆分管理员 token，TokenElevation 恒为 True，
      但进程完整性仍可能是 Medium（实测：发行 python 子进程 Medium，
      系统 python High）。拿等号硬判必然红，而生产实现并没有错。

    UAC 开启的机器上仍要求两者一致；UAC 关闭时只要求 TokenElevation 为
    True（不拆分 token 的必然结果），并显式记录分叉原因，不让用例空转。
    """
    ref = bool(ref)
    if enable_lua == 0:
        if not ref:
            return False, "UAC 关闭时 TokenElevation 应为 True"
        return True, ("EnableLUA=0：token 不拆分，TokenElevation 恒 True；"
                      "完整性标签仍可为 Medium（已知分叉，不是探测坏了）")
    ok = (bool(high) == ref)
    why = ("UAC 开启：标签与 TokenElevation 一致" if ok
           else f"UAC 开启却分叉：whoami_high={high} ref={ref}")
    return ok, why


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
        lua = _enable_lua()
        ok, why = _elevation_label_verdict(high, ref, lua)
        check("★ TokenElevation 与完整性标签的关系符合 UAC 配置", ok, why)
        print(f"  [note] EnableLUA={lua} whoami_high={high} ref={ref} → {why}")
    else:
        print("  [skip] 取不到 whoami 完整性标签")

    print("\n[4] 判据本身不空转（组合表，不依赖本机权限）")
    combos = [
        (True, True, 1, True, "UAC开·High+True 一致"),
        (True, False, 1, False, "UAC开·High+False 必须判红"),
        (False, False, 1, True, "UAC开·Medium+False 一致"),
        (False, True, 1, False, "UAC开·Medium+True 必须判红"),
        (False, True, 0, True, "UAC关·Medium+True 预期分叉（本机）"),
        (True, True, 0, True, "UAC关·High+True 放行"),
        (False, False, 0, False, "UAC关·TokenElevation=False 必须判红"),
    ]
    for h, r, l, want, label in combos:
        got, _ = _elevation_label_verdict(h, r, l)
        check(label, got == want, f"got={got} want={want}")

    print("\n" + "=" * 56)
    print(f"通过 {PASS} / 失败 {FAIL}")
    for x in FAILURES:
        print("  -", x)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
