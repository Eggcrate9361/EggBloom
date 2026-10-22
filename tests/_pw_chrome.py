# -*- coding: utf-8 -*-
"""tests/_pw_chrome.py —— 找到本机 Playwright 可用的 chromium 可执行文件。

## 为什么需要

`pip install playwright` 只装库，**不含浏览器二进制**。浏览器要单独
`playwright install chromium` 下载，且版本号跟库版本绑定 —— 库升级后旧浏览器
目录还在，但路径变了，于是 `pw.chromium.launch()` 直接抛：

    BrowserType.launch: Executable doesn't exist at
    ...\\chromium-1148\\chrome-win64\\chrome.exe
    ║ Please run the following command to download new browsers ║
    ║     playwright install                                    ║

4 个 `test_webui_v4_*` 因此长期在回归里显示"红"。但它**不是代码问题，也不是
真的没装** —— 本机 `%LOCALAPPDATA%\\ms-playwright\\` 下通常已经有 chromium-XXXX
（可能还不止一个版本）。真正要做的是**找到那个已经存在的**，而不是让人重下几百 MB。

## 用法

测试文件里（在 `launch` 之前）：

    from _pw_chrome import resolve_chrome
    exe = resolve_chrome()          # 找不到返回 None
    b = pw.chromium.launch(executable_path=exe)

优先级：
  1. 环境变量 `PW_CHROME`（显式指定，永远最高）；
  2. `%LOCALAPPDATA%\\ms-playwright\\chromium-*\\chrome-win64\\chrome.exe`
     —— **版本号降序**（宁可新，别选旧的）；
  3. Playwright 自带的默认解析（`executable_path=None`）。
"""
import glob
import os
import sys


def _candidates():
    """列出本机 ms-playwright 下的 chromium 可执行文件（新版本在前）。"""
    roots = []
    local = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~/.cache")
    roots.append(os.path.join(local, "ms-playwright"))
    roots.append(os.path.expanduser("~/.cache/ms-playwright"))
    roots.append(os.path.expanduser("~/Library/Caches/ms-playwright"))

    out = []
    for root in roots:
        if not os.path.isdir(root):
            continue
        pats = [
            os.path.join(root, "chromium-*", "chrome-win64", "chrome.exe"),
            os.path.join(root, "chromium-*", "chrome-linux", "chrome"),
            os.path.join(root, "chromium-*", "chrome-mac", "Chromium.app",
                         "Contents", "MacOS", "Chromium"),
        ]
        for p in pats:
            out.extend(glob.glob(p))

    def ver(path):
        # chromium-1228 / chromium_headless_shell-1228 → 1228
        for seg in path.replace("\\", "/").split("/"):
            if seg.startswith("chromium-"):
                try:
                    return int(seg.split("-", 1)[1])
                except (ValueError, IndexError):
                    pass
        return -1

    # 降序：新版本优先
    out.sort(key=ver, reverse=True)
    return out


def resolve_chrome() -> "str | None":
    """返回可用的 chromium 路径；显式 PW_CHROME 优先，找不到返回 None。"""
    env = os.environ.get("PW_CHROME")
    if env and os.path.exists(env):
        return env
    for c in _candidates():
        if os.path.exists(c):
            # 缓存进环境变量，后续别的测试文件不用再扫一遍
            os.environ["PW_CHROME"] = c
            return c
    return None


def require_playwright():
    """导入 sync_playwright；不可用时返回 (None, 原因)。"""
    try:
        from playwright.sync_api import sync_playwright
        return sync_playwright, None
    except Exception as e:
        return None, "未安装 Playwright（%s）" % type(e).__name__


if __name__ == "__main__":
    exe = resolve_chrome()
    if exe:
        print("找到 chromium:", exe)
        print("版本:", os.path.basename(os.path.dirname(os.path.dirname(exe))))
    else:
        print("未找到 chromium（需要 playwright install chromium，或设 PW_CHROME）")
        sys.exit(1)