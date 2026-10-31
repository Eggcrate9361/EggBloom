# -*- coding: utf-8 -*-
"""harness/qq_primitives 单测（方案 v2 §4）。

重点不是"能不能调通"，而是**越界会被挡住**：路径沙箱、文件黑名单、
onebot 写类动作、域名白名单。这几条是这个模块唯一的防线，破了就等于
把 shell 塞回给模型。

运行：python tests/test_qq_primitives.py
"""
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from harness.qq_primitives import (PrimitiveSet, READ_ACTIONS, WRITE_ACTIONS,  # noqa: E402
                                   envelope)

PASS = []
FAIL = []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name if not extra else f"{name}  {extra}")


_TMP = tempfile.mkdtemp(prefix="qqprim_")
try:
    sandbox = os.path.join(_TMP, "app")
    os.makedirs(os.path.join(sandbox, "data"))
    os.makedirs(os.path.join(sandbox, "memes", "vrchat"))
    with open(os.path.join(sandbox, "data", "people.json"), "w", encoding="utf-8") as f:
        f.write('{"texas": {"name": "Texas"}}')
    with open(os.path.join(sandbox, ".env"), "w", encoding="utf-8") as f:
        f.write("API_KEY=sk-secret")
    with open(os.path.join(sandbox, "token_store.txt"), "w", encoding="utf-8") as f:
        f.write("t")
    os.makedirs(os.path.join(sandbox, "__pycache__"))
    with open(os.path.join(sandbox, "data", "big.txt"), "w", encoding="utf-8") as f:
        f.write("字" * 5000)
    with open(os.path.join(sandbox, "memes", "vrchat", "a.gif"), "wb") as f:
        f.write(b"GIF89a")

    # ── Envelope 形状统一 ───────────────────────────────────────
    e = envelope(True, data=1)
    check("Envelope 四键齐全", set(e) == {"ok", "data", "error", "evidence"}, set(e))
    check("ok 转 bool", envelope(1)["ok"] is True)

    ps = PrimitiveSet(sandbox_root=sandbox)

    # ── 1. 路径沙箱 ─────────────────────────────────────────────
    check("沙箱内相对路径放行",
          ps.resolve("data/people.json").endswith("people.json"))
    for bad, why in (("../escape.txt", "上跳"),
                     ("data/../../escape.txt", "深层上跳"),
                     (os.path.join(_TMP, "outside.txt"), "绝对路径越界"),
                     (".env", "环境变量文件"),
                     ("token_store.txt", "凭据文件"),
                     ("__pycache__/x.pyc", "缓存目录")):
        try:
            ps.resolve(bad)
            check(f"挡住{why}", False, bad)
        except Exception:
            check(f"挡住{why}", True)

    # ── 2. fs_list / fs_read ────────────────────────────────────
    r = ps.fs_list(".")
    check("fs_list ok", r["ok"] and any(x["name"] == "data" for x in r["data"]), r)
    r = ps.fs_list("nope")
    check("fs_list 不存在 → 失败不抛", r["ok"] is False and r["error"], r)
    r = ps.fs_read("data/people.json")
    check("fs_read 读到内容", r["ok"] and "Texas" in r["data"], r)
    r = ps.fs_read("data/big.txt", max_chars=10)
    check("fs_read 截断并标记", r["ok"] and r["evidence"]["truncated"] and len(r["data"]) == 10, r)
    r = ps.fs_read(".env")
    check("fs_read 挡住 .env", r["ok"] is False, r)

    # ── 3. onebot 白名单 ────────────────────────────────────────
    calls = []

    def fake_onebot(action, params):
        calls.append((action, params))
        return {"status": "ok", "retcode": 0, "data": {"user_id": params.get("user_id")}}

    ps2 = PrimitiveSet(sandbox_root=sandbox, onebot=fake_onebot)
    r = ps2.onebot("get_stranger_info", {"user_id": 2011257385})
    check("只读 action 直接可用", r["ok"] and r["data"]["user_id"] == 2011257385, r)
    check("确实调到了注入的实现", calls and calls[0][0] == "get_stranger_info")
    r = ps2.onebot("set_group_kick", {"group_id": 1, "user_id": 2})
    check("写类 action 默认拒绝", r["ok"] is False and "授权" in r["error"], r)
    r = ps2.onebot("something_else", {})
    check("白名单外 action 拒绝", r["ok"] is False and "白名单" in r["error"], r)
    ps3 = PrimitiveSet(sandbox_root=sandbox, onebot=fake_onebot, allow_write=True)
    check("显式授权后写类可放",
          ps3.onebot("set_group_kick", {"group_id": 1})["ok"] is True)

    def boom(action, params):
        raise RuntimeError("连不上")

    ps4 = PrimitiveSet(sandbox_root=sandbox, onebot=boom)
    r = ps4.onebot("get_group_list", {})
    check("注入实现抛异常 → Envelope 不炸", r["ok"] is False and "连不上" in r["error"], r)
    r = PrimitiveSet(sandbox_root=sandbox).onebot("get_group_list", {})
    check("未注入 onebot → 明确报错", r["ok"] is False and "未注入" in r["error"], r)

    # ── 4. http_fetch ───────────────────────────────────────────
    fetched = {}

    def fake_fetch(url, dest=None, max_bytes=0):
        fetched.update({"url": url, "dest": dest, "max": max_bytes})
        if dest:
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "wb") as f:
                f.write(b"\xff\xd8\xff" + b"x" * 100)
        return {"ok": True, "status": 200, "bytes": 103}

    psh = PrimitiveSet(sandbox_root=sandbox, fetch=fake_fetch)
    r = psh.http_fetch("https://q1.qlogo.cn/g?b=qq&nk=1&s=640",
                       save_as="data/avatars/1.jpg")
    check("白名单域名可抓", r["ok"] is True, r)
    check("抓到后给 sha 证据", bool(r["evidence"].get("sha256")), r["evidence"])
    r = psh.http_fetch("https://evil.example.com/a.png")
    check("非白名单域名拒绝", r["ok"] is False and "白名单" in r["error"], r)
    r = psh.http_fetch("file:///c:/windows/win.ini")
    check("非 http(s) 拒绝", r["ok"] is False, r)
    r = psh.http_fetch("https://q1.qlogo.cn/x.jpg", save_as="../escape.jpg")
    check("落盘路径越界拒绝", r["ok"] is False, r)
    r = PrimitiveSet(sandbox_root=sandbox).http_fetch("https://q1.qlogo.cn/x.jpg")
    check("未注入下载器 → 明确报错", r["ok"] is False and "未注入" in r["error"], r)

    # ── 5. vision / asr ────────────────────────────────────────
    psv = PrimitiveSet(sandbox_root=sandbox,
                       vision=lambda p, q: {"ok": True, "text": "银发蓝眼"})
    r = psv.vision("memes/vrchat/a.gif", "画风")
    check("vision 成功返回文本", r["ok"] and r["data"] == "银发蓝眼", r)
    r = psv.vision("memes/vrchat/missing.gif")
    check("vision 文件不存在 → 失败", r["ok"] is False and "不存在" in r["error"], r)
    r = psv.vision("../x.gif")
    check("vision 越界拒绝", r["ok"] is False, r)
    psv2 = PrimitiveSet(sandbox_root=sandbox, vision=lambda p, q: {"ok": True, "text": "  "})
    r = psv2.vision("memes/vrchat/a.gif")
    check("vision 返回空文本 → 算失败（不许拿空当答案）", r["ok"] is False, r)
    r = PrimitiveSet(sandbox_root=sandbox).vision("memes/vrchat/a.gif")
    check("vision 未注入 → 明确报错", r["ok"] is False and "未注入" in r["error"], r)

    psa = PrimitiveSet(sandbox_root=sandbox, asr=lambda p: {"ok": True, "text": "你好"})
    r = psa.asr("memes/vrchat/a.gif")
    check("asr 成功", r["ok"] and r["data"] == "你好", r)

    # ── 6. recall / remember ──────────────────────────────────
    psr = PrimitiveSet(sandbox_root=sandbox,
                       recall=lambda q: [{"t": i} for i in range(10)],
                       remember=lambda t: {"saved": True})
    r = psr.recall("上次聊啥", limit=3)
    check("recall 按 limit 截断", r["ok"] and len(r["data"]) == 3, r)
    check("remember 写入 ok", psr.remember("记住这句")["ok"] is True)
    r = PrimitiveSet(sandbox_root=sandbox).recall("x")
    check("recall 未注入 → 明确报错", r["ok"] is False and "未注入" in r["error"], r)

    # ── 7. call() 与注册表 ────────────────────────────────────
    specs = ps.specs()
    names = sorted(s["name"] for s in specs)
    # 2026-10-31：10-30 加了 self_history（行为账本），8 → 9。这里不再只数字，
    # 顺手把**必须存在**的原语钉住 —— 少一个就是能力被悄悄摘掉了。
    check("注册表 9 个原语", len(specs) == 9, names)
    check("原语清单没少（8 个老的 + self_history）",
          set(names) == {"onebot", "http_fetch", "vision", "asr", "fs_list",
                         "fs_read", "recall", "remember", "self_history"}, names)
    check("注册表条目字段齐全",
          all(set(s) >= {"name", "signature", "side_effects", "permission", "desc"} for s in specs))
    check("call 能按名字调到", ps.call("fs_list", path=".")["ok"] is True)
    check("call 未知原语 → 失败", ps.call("run_python", code="1")["ok"] is False)
    check("call 参数不对 → 失败而不抛", ps.call("fs_read")["ok"] is False)
    check("只读/写类集合不重叠", not (READ_ACTIONS & WRITE_ACTIONS))

    # ── 8. 手册与注册表同源 ────────────────────────────────────
    manual = os.path.join(ROOT, "docs", "qq_agent_manual.md")
    if os.path.isfile(manual):
        txt = open(manual, encoding="utf-8").read()
        missing = [s["name"] for s in specs if f"`{s['name']}`" not in txt]
        check("每个原语都写在手册里", not missing, missing)
finally:
    shutil.rmtree(_TMP, ignore_errors=True)

print(f"通过 {len(PASS)} / 失败 {len(FAIL)}")
for n in FAIL:
    print(f"  FAIL  {n}")
sys.exit(1 if FAIL else 0)
