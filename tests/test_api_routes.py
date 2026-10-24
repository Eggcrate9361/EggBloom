# -*- coding: utf-8 -*-
"""前端每个 POST 调用，服务端必须在 do_POST 里有路由。

2026-10-24 实测抓到的 bug：日志页一片空白，报
「读取 /api/events 失败: UNEXPECTED END OF JSON INPUT
  （后端未启动时属正常）」

后端其实是活着的（顶栏 BOT 运行 11 秒）。真因是
`/api/events` 的路由写在了 `do_GET` 里，而前端是
`API('/events', {method:'POST', ...})`。POST 请求匹配不到路由，
落到默认分支返回空响应体，前端 `res.json()` 解析空串抛异常。

那个错误提示还把责任推给"后端未启动"，把人往错的方向引。

为什么值得一个测试：这类错误**在浏览器里看不出根因** ——
页面只是空白加一句提示，服务端日志里连 404 都不会记。
而路由放错方法名是纯手工失误，没有任何机制会拦。

这个文件锁三件事：

  [1] 前端每个 POST 调用在 do_POST 里有对应路由（精确匹配或前缀匹配）
  [2] /api/events 明确在 do_POST 里，且不在 do_GET 里
  [3] 前端错误提示不能把责任推给不存在的原因

判据是**真实代码**：从 v4 HTML 里抓 method:'POST' 的调用，
从 webui_server.py 里抓 do_POST 方法体里的路由表，两边对。
"""
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

_fail = []


def check(name, ok, detail=""):
    if ok:
        print(f"  [OK]   {name}")
    else:
        print(f"  [FAIL] {name}  {detail}")
        _fail.append(name)


def main():
    html = io.open(os.path.join(ROOT, "webui", "webui_artistic_v4.html"),
                   encoding="utf-8").read()
    srv = io.open(os.path.join(ROOT, "webui", "webui_server.py"),
                  encoding="utf-8").read()

    # 服务端 do_POST 方法体（用缩进界定，不用行号 —— 行号会漂）
    m = re.search(r"\n    def do_POST\(self\):\n(.*?)(?=\n    def |\Z)",
                  srv, re.S)
    check("webui_server.py 有 do_POST", m is not None)
    post_block = m.group(1) if m else ""
    post_routes = set(re.findall(r'if path == "([^"]+)"', post_block))
    for mm in re.finditer(r'if path\.startswith\("([^"]+)"', post_block):
        post_routes.add(mm.group(1))
    for mm in re.finditer(r'if path in \(([^)]+)\)', post_block):
        post_routes |= set(re.findall(r'"([^"]+)"', mm.group(1)))

    print(f"[1] 前端 POST 调用 vs do_POST 路由（{len(post_routes)} 条）")
    posts = set()
    for mm in re.finditer(r"API\('([^']+)',\s*\{\s*method:'POST'", html):
        p = mm.group(1)
        posts.add(p if p.startswith("/api") else "/api" + p)
    check("抓到了前端 POST 调用（少于 10 个说明正则失效）",
          len(posts) >= 10, len(posts))
    missing = []
    for p in sorted(posts):
        if not any(p == r or p.startswith(r) for r in post_routes):
            missing.append(p)
    check(f"{len(posts)} 个 POST 调用全部有 do_POST 路由", not missing, missing)

    print("\n[2] /api/events 的位置（这次修的 bug）")
    check("do_POST 里有 /api/events", '"/api/events"' in post_block)
    # do_GET 里不该再有它（有就是两份路由，早晚分叉）
    g = re.search(r"\n    def do_GET\(self\):\n(.*?)(?=\n    def |\Z)", srv, re.S)
    get_block = g.group(1) if g else ""
    check("do_GET 里没有 /api/events（不重复注册）",
          '"/api/events"' not in get_block)

    print("\n[3] 前端错误提示不把责任推给别处")
    seg = html[html.find("读取事件失败"):][:500] if "读取事件失败" in html else ""
    check("事件页的失败提示存在", bool(seg))
    check("提示里没有「后端未启动时属正常」这种归因",
          "后端未启动" not in seg, seg[:120])

    print("\n" + "=" * 56)
    if _fail:
        print(f"失败 {len(_fail)}：{_fail}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())