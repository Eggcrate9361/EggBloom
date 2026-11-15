# -*- coding: utf-8 -*-
"""图片下载的 URL 清洗；工具调用去重（2026-10-27 真机）

## 两个现象

**图片下载 400**

```
[图片理解] 下载失败 https://multimedia.nt.qq.com.cn/download?appid=1407&amp;fileid=...
HTTP Error 400: Bad Request
```

CQ 码里的 URL 带 XML 实体 `&amp;`，没还原就请求，`fileid` 变成 `amp;fileid`，
QQ 服务器判参数非法。这个 URL 来自引用消息的 raw 段，普通消息不经过这条路径，
所以只有"引用一张图"才会踩到。

**搜索重复执行**

```
工具调用: ['search_web', 'search_web']
```

模型一次返回两个一模一样的调用，连着三轮。等于同一个查询搜六遍：一倍时间、
一倍请求、两份重复结果塞进上下文。

## 契约

1. `_clean_url` 还原 `&amp;` / `&#38;` / `&quot;` 等实体
2. `download_image_url` 入口先清洗，缓存 key 也按清洗后的 URL 算
3. `_dedup_tool_calls` 只按 (name, arguments) 字节相同去重
4. 参数不同的并行调用必须保留 —— 那是合法的并行

运行：python tests/test_image_url_dedup.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

_FAIL = []


def check(name, ok, detail=""):
    if ok:
        print(f"  [OK]   {name}")
    else:
        print(f"  [FAIL] {name}  {detail}")
        _FAIL.append(name)


REAL = ("https://multimedia.nt.qq.com.cn/download?appid=1407&amp;fileid=EhTm3Fgz"
        "&amp;spec=0&amp;rkey=abc")


def test_clean_url():
    import bot_core
    got = bot_core._clean_url(REAL)
    check("去掉 &amp;", "&amp;" not in got, got[:80])
    check("参数分隔正确", "appid=1407&fileid=" in got, got[:80])
    check("普通 URL 不变",
          bot_core._clean_url("https://a.com/x?y=1&z=2") == "https://a.com/x?y=1&z=2")
    check("空值安全", bot_core._clean_url("") == "")
    check("去空白", bot_core._clean_url("  http://a  ") == "http://a")


def test_download_uses_clean_url():
    """下载函数必须用清洗后的 URL，否则 400 照旧。

    不 mock 网络：本机 requests 可能没装，走的是 urllib 分支，
    mock 任一侧都容易只测到一半。改为断言"清洗发生在入口第一行"。
    """
    import io
    import re
    src = io.open(os.path.join(ROOT, "bot_core.py"), encoding="utf-8").read()
    m = re.search(r"def download_image_url\(url: str\):\n(.*?)\n\n\ndef ", src, re.S)
    check("能找到 download_image_url", bool(m))
    body = m.group(1) if m else ""
    # 函数体第一行是 docstring —— 断言"第一个**语句**就清洗"，
    # 不要求字面第一行（原先那样写会把注释/docstring 也算进去，误判）。
    _head = re.sub(r'\A\s*""".*?"""', "", body, flags=re.S).strip()
    check("第一句就清洗 URL", _head.startswith("url = _clean_url(url)"), _head[:80])
    # 清洗必须发生在**取缓存 key 之前**，否则 md5 仍按脏 URL 算，同一张图会
    # 反复下载（400 只是第一层症状）。
    _i_clean = _head.find("url = _clean_url(url)")
    _i_md5 = _head.find("hashlib.md5(url")
    check("清洗早于缓存 key 计算", 0 <= _i_clean < _i_md5, (_i_clean, _i_md5))
    check("清洗后再判 http 前缀",
          _head.find('str(url).startswith("http")') > _i_clean, _head[:120])


def test_dedup():
    import bot_core
    same = [
        {"function": {"name": "search_web", "arguments": '{"query":"明日方舟"}'}},
        {"function": {"name": "search_web", "arguments": '{"query":"明日方舟"}'}},
    ]
    got = bot_core._dedup_tool_calls(same)
    check("完全相同的调用只留一个", len(got) == 1, len(got))

    diff = [
        {"function": {"name": "search_web", "arguments": '{"query":"A"}'}},
        {"function": {"name": "search_web", "arguments": '{"query":"B"}'}},
    ]
    check("参数不同的并行调用都保留", len(bot_core._dedup_tool_calls(diff)) == 2)

    mixed = [
        {"function": {"name": "search_web", "arguments": '{"query":"A"}'}},
        {"function": {"name": "take_screenshot", "arguments": "{}"}},
        {"function": {"name": "search_web", "arguments": '{"query":"A"}'}},
    ]
    got2 = bot_core._dedup_tool_calls(mixed)
    check("混合调用只砍重复的那个", len(got2) == 2, [t["function"]["name"] for t in got2])
    check("顺序保持", got2[0]["function"]["name"] == "search_web", got2)
    check("空入参安全", bot_core._dedup_tool_calls([]) == [])
    check("None 安全", bot_core._dedup_tool_calls(None) == [])


def test_wired():
    import io
    src = io.open(os.path.join(ROOT, "bot_core.py"), encoding="utf-8").read()
    check("下载入口清洗 URL", "url = _clean_url(url)" in src)
    check("工具调用走去重", "_uniq_tc = _dedup_tool_calls(tool_calls)" in src)
    check("日志可检索", "[工具去重]" in src)


def main():
    print("=" * 58)
    print("图片 URL 清洗 / 工具调用去重")
    print("=" * 58)
    for fn in (test_clean_url, test_download_uses_clean_url, test_dedup, test_wired):
        print(f"\n[{fn.__name__}]")
        fn()
    print("\n" + "=" * 58)
    if _FAIL:
        print(f"失败 {len(_FAIL)}：{_FAIL}")
        sys.exit(1)
    print("全部通过")


if __name__ == "__main__":
    main()
