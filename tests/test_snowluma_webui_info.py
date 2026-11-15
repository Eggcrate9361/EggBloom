# -*- coding: utf-8 -*-
"""SnowLuma 初始凭据抓取：只读头 + 结果缓存（2026-10-27）

## 线上事故

协议页卡在「读取中…」，切别的页也停在这一页。

```
/api/status          -> 200  0.012s
/api/config          -> 200  0.021s
/api/protocol/status -> 000  8.015s   ← 唯一卡死的
```

根因在 `get_webui_info()`：它每次都把 `logs/` 下**所有** `snowluma-*.log`
整文件读完，找「initial credentials」那一行。本机实测
23 个文件 / 1024 MB / **4.5 秒**，而且一条都没命中（密码早改过）→ 每次请求
都全量扫完再返回。叠上 `is_ready()` 最多 3 秒的探测，就是 8 秒起。

前端 `API()` 没有超时、`go()` 的 busy 也不会在异常时释放 → 一页卡住锁死导航。

## 本测试锁什么

1. 默认落到最近一个日志的**头部**就能命中（凭据是启动时打印的）
2. 命中之后第二次调用走缓存 —— 判据是"把 logs 目录删掉，结果仍在"
3. 超出头部窗口的标记**不会**被找到（这是有意的预算，不是漏读）
4. 大文件下仍很快（4MB 的文件，冷调用应远低于 1 秒）

运行：python tests/test_snowluma_webui_info.py
"""
import os
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import protocols.snowluma_adapter as sa  # noqa: E402

_fail = []


def check(name, ok, detail=""):
    if ok:
        print(f"  [OK]   {name}")
    else:
        print(f"  [FAIL] {name}  {detail}")
        _fail.append(name)


class _Adapter(sa.SnowLumaAdapter):
    """只测 get_webui_info —— 不走真正的构造（那要 node.exe / 完整配置）。"""

    def __init__(self, root_dir):
        self._cfg = {"webui_port": 5099}
        self._root = root_dir

    @property
    def runtime_dir(self):
        return self._root


def _make_logs(root, filename, body, pad_bytes=0):
    d = os.path.join(root, "logs")
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, filename)
    with open(p, "w", encoding="utf-8") as f:
        f.write(body)
        if pad_bytes:
            f.write("\n" + ("x" * 800 + "\n") * (pad_bytes // 800))
    return p


def _fresh(name):
    root = tempfile.mkdtemp(prefix="eggbloom_snowluma_")
    sa._WEBUI_INFO_CACHE.pop(os.path.join(root, "logs"), None)
    return root


def test_head_hit():
    print("\n[1] 凭据在文件头 → 命中")
    root = _fresh("hit")
    try:
        _make_logs(root, "snowluma-2026-10-27.log",
                   "2026-10-27 09:00:00 boot\n"
                   "initial credentials: user=admin password=Zx9Qw2\n"
                   "2026-10-27 09:00:01 ready\n")
        info = _Adapter(root).get_webui_info()
        check("user 命中", info.get("user") == "admin", info)
        check("token 命中", info.get("token") == "Zx9Qw2", info)
        check("url 指向 webui_port", info.get("url", "").endswith(":5099"), info.get("url"))
    finally:
        shutil.rmtree(root, ignore_errors=True)
        sa._WEBUI_INFO_CACHE.pop(os.path.join(root, "logs"), None)


def test_newest_wins():
    print("\n[2] 多个文件时取**最新**的那个")
    root = _fresh("newest")
    try:
        old = _make_logs(root, "snowluma-2026-10-26.log",
                         "initial credentials: user=admin password=OLD111\n")
        new = _make_logs(root, "snowluma-2026-10-27.log",
                         "initial credentials: user=admin password=NEW222\n")
        # 让 new 确实是"更新的"
        os.utime(old, (time.time() - 3600, time.time() - 3600))
        os.utime(new, (time.time(), time.time()))
        info = _Adapter(root).get_webui_info()
        check("取最新的密码", info.get("token") == "NEW222", info.get("token"))
    finally:
        shutil.rmtree(root, ignore_errors=True)
        sa._WEBUI_INFO_CACHE.pop(os.path.join(root, "logs"), None)


def test_cache_reused():
    print("\n[3] 第二次调用走缓存（改掉日志里的密码，返回的仍是缓存值）")
    root = _fresh("cache")
    logs = os.path.join(root, "logs")
    try:
        p = _make_logs(root, "snowluma-2026-10-27.log",
                       "initial credentials: user=admin password=CACHE9\n")
        a = _Adapter(root)
        first = a.get_webui_info()
        # 把文件改成另一个密码（相当于"如果重新读就会变"）
        with open(p, "w", encoding="utf-8") as f:
            f.write("initial credentials: user=admin password=CHANGED\n")
        second = a.get_webui_info()
        check("首次命中", first.get("token") == "CACHE9", first)
        check("★ 第二次仍是缓存值（没重读文件）", second.get("token") == "CACHE9", second)
    finally:
        shutil.rmtree(root, ignore_errors=True)
        sa._WEBUI_INFO_CACHE.pop(logs, None)


def test_beyond_head_not_scanned():
    print("\n[4] 超出头部窗口的标记不读（有意的预算）")
    root = _fresh("beyond")
    try:
        # 先填满 > _HEAD_BYTES，再放标记
        filler = "y" * 800 + "\n"
        body = filler * ((sa._HEAD_BYTES // 800) + 50)
        _make_logs(root, "snowluma-2026-10-27.log",
                   body + "initial credentials: user=admin password=LATE99\n")
        info = _Adapter(root).get_webui_info()
        check("★ 文件深处的不算命中（只读头 %dKB）" % (sa._HEAD_BYTES // 1024),
              info.get("token") == "", info.get("token"))
    finally:
        shutil.rmtree(root, ignore_errors=True)
        sa._WEBUI_INFO_CACHE.pop(os.path.join(root, "logs"), None)


def test_big_file_is_fast():
    print("\n[5] 大日志也很快（原来整文件读完要 4.5s / 1GB）")
    root = _fresh("perf")
    try:
        _make_logs(root, "snowluma-2026-10-27.log",
                   "initial credentials: user=admin password=FAST1\n",
                   pad_bytes=8 * 1024 * 1024)   # 8MB
        t0 = time.time()
        info = _Adapter(root).get_webui_info()
        dt = time.time() - t0
        check("8MB 日志命中", info.get("token") == "FAST1", info.get("token"))
        check("★ 冷调用 < 1s（实测 %0.3fs）" % dt, dt < 1.0, f"{dt:.3f}s")
        t0 = time.time()
        _Adapter(root).get_webui_info()
        dt2 = time.time() - t0
        check("热调用更省（缓存命中 %0.4fs）" % dt2, dt2 < 0.2, f"{dt2:.4f}s")
    finally:
        shutil.rmtree(root, ignore_errors=True)
        sa._WEBUI_INFO_CACHE.pop(os.path.join(root, "logs"), None)


def main():
    test_head_hit()
    test_newest_wins()
    test_cache_reused()
    test_beyond_head_not_scanned()
    test_big_file_is_fast()

    print("\n" + "=" * 56)
    if _fail:
        print(f"失败 {len(_fail)}：{_fail}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
