# -*- coding: utf-8 -*-
"""tests/test_aigc_task.py —— AIGC 统一任务层（1.6.0 P1.5 第①刀）

钉住的是**任务层**，不是模型：
  状态机、产物落盘布局、配额、开关零请求、删除与重启恢复。

用假后端跑，不碰网络也不碰模型 —— 这样这一刀可以在没有 ComfyUI / piper / whisper
的机器上验证，等真后端接进来时只剩"适配"这一件事。
"""
import json
import os
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from harness import aigc_task as A  # noqa: E402

PASS = 0
FAIL = 0
FAILURES = []

#: 固定时钟：2026-11-03 18:30:00 本地时间，让"目录日期"可确定性断言
FIXED_TS = time.mktime((2026, 11, 3, 18, 30, 0, 0, 0, -1))


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK]   {name}")
    else:
        FAIL += 1
        FAILURES.append(name + (f"  → {extra}" if extra else ""))
        print(f"  [FAIL] {name}  {extra}")


def make_service(cfg=None, root=None):
    cfg = {"aigc_enable": True} if cfg is None else cfg
    root = root or tempfile.mkdtemp(prefix="aigc_test_")
    return A.AigcService(root, cfg_getter=lambda: cfg, clock=lambda: FIXED_TS), root


def main():
    print("[A] 关掉时一个请求都不发（验收第 6 条）")
    called = {"n": 0}

    def fake_image(task, params, ctx):
        called["n"] += 1
        return {"artifact": b"\x89PNG", "ext": ".png"}

    svc, root = make_service({"aigc_enable": False})
    svc.register_backend(A.KIND_IMAGE, fake_image)
    r = svc.submit(A.KIND_IMAGE, {"prompt": "猫"})
    check("★ 未启用时提交失败", r.get("ok") is False, r)
    check("★ 理由说明是开关", "未启用" in str(r.get("reason")), r.get("reason"))
    check("★ 后端一次都没被调用", called["n"] == 0, called)
    shutil.rmtree(root, ignore_errors=True)

    print("\n[B] 正常任务：pending → done，产物与元数据落盘")
    got = {}

    def ok_image(task, params, ctx):
        ctx.report(0.5, "半路")
        got["mid"] = svc.get(task["id"]) or {}
        return {"artifact": b"\x89PNGDATA", "ext": ".png",
                "meta": {"width": 512, "height": 512}}

    svc, root = make_service()
    svc.register_backend(A.KIND_IMAGE, ok_image)
    r = svc.submit(A.KIND_IMAGE, {"prompt": "猫"}, sync=True)
    check("提交成功", r.get("ok") is True, r)
    t = svc.get((r.get("task") or {}).get("id"))
    check("★ 最终状态 done", t.get("status") == A.ST_DONE, t.get("status"))
    check("进度归一 1.0", t.get("progress") == 1.0, t.get("progress"))
    check("★ 中途汇报过进度",
          (got.get("mid") or {}).get("progress") == 0.5, (got.get("mid") or {}).get("progress"))
    art = t.get("artifact") or ""
    check("★ 产物路径符合 data/media/<kind>/<yyyymmdd>/<id>.<ext>",
          os.path.basename(os.path.dirname(art)) == "20261103"
          and os.path.basename(os.path.dirname(os.path.dirname(art))) == "image"
          and art.endswith(".png"), art)
    check("产物真的写出来了", os.path.exists(art), art)
    check("产物内容正确", open(art, "rb").read() == b"\x89PNGDATA")
    meta_path = art[:-4] + ".json"
    check("★ 同名 .json 元数据存在", os.path.exists(meta_path), meta_path)
    meta = json.load(open(meta_path, encoding="utf-8"))
    check("元数据带业务字段", (meta.get("meta") or {}).get("width") == 512, meta.get("meta"))
    check("元数据带状态", meta.get("status") == A.ST_DONE, meta.get("status"))

    print("\n[C] 后端抛异常 → failed，不吞")
    def boom(task, params, ctx):
        raise RuntimeError("模型没装")
    svc.register_backend(A.KIND_TTS, boom)
    r2 = svc.submit(A.KIND_TTS, {"text": "你好"}, sync=True)
    t2 = svc.get((r2.get("task") or {}).get("id"))
    check("★ 状态 failed", t2.get("status") == A.ST_FAILED, t2.get("status"))
    check("★ 错误信息保留", "模型没装" in str(t2.get("error")), t2.get("error"))

    print("\n[D] 后端缺失 → failed 且理由明确")
    r3 = svc.submit(A.KIND_ASR, {"audio": "x.wav"}, sync=True)
    check("后端缺失时失败", r3.get("ok") is False, r3)
    check("★ 说明哪个后端缺失", "未安装" in str(r3.get("reason")), r3.get("reason"))

    print("\n[E] 配额：超了就不调用后端")
    called2 = {"n": 0}
    def count_image(task, params, ctx):
        called2["n"] += 1
        return {"artifact": b"x", "ext": ".png"}
    svc2, root2 = make_service({"aigc_enable": True, "aigc_daily_image_quota": 2})
    svc2.register_backend(A.KIND_IMAGE, count_image)
    a = svc2.submit(A.KIND_IMAGE, {}, sync=True)
    b = svc2.submit(A.KIND_IMAGE, {}, sync=True)
    c = svc2.submit(A.KIND_IMAGE, {}, sync=True)
    check("前两个成功", a.get("ok") and b.get("ok"), (a.get("ok"), b.get("ok")))
    check("★ 第三个被配额挡下", c.get("ok") is False and "额度" in str(c.get("reason")), c)
    check("★ 被挡时后端没再被调用", called2["n"] == 2, called2)
    shutil.rmtree(root2, ignore_errors=True)

    print("\n[F] 未知种类 / 列表 / 删除")
    bad = svc.submit("hologram", {})
    check("未知种类被拒", bad.get("ok") is False and "未知" in str(bad.get("reason")), bad)
    lst = svc.list()
    check("列表里有刚提交的任务", len(lst) >= 2, len(lst))
    check("列表按时间倒序", lst[0].get("created_at") >= lst[-1].get("created_at"))
    victim = lst[0]["id"]
    art_v = lst[0].get("artifact") or ""
    check("删除返回 True", svc.delete(victim) is True)
    check("★ 删除后查不到", svc.get(victim) is None)
    if art_v:
        check("★ 产物文件也删了", not os.path.exists(art_v), art_v)
    check("删不存在的返回 False", svc.delete("nope") is False)

    print("\n[G] 重启恢复：上次没跑完的标失败，别永远转圈")
    root3 = tempfile.mkdtemp(prefix="aigc_restart_")
    d = os.path.join(root3, "image", "20261103")
    os.makedirs(d)
    stale = {"id": "aigc_image_1_abcd", "kind": "image", "status": A.ST_RUNNING,
             "progress": 0.3, "created_at": FIXED_TS, "params": {}}
    json.dump(stale, open(os.path.join(d, "aigc_image_1_abcd.json"), "w", encoding="utf-8"),
              ensure_ascii=False)
    svc3 = A.AigcService(root3, cfg_getter=lambda: {"aigc_enable": True},
                         clock=lambda: FIXED_TS)
    svc3.load_from_disk()
    t3 = svc3.get("aigc_image_1_abcd")
    check("★ 读回旧任务", bool(t3), t3)
    check("★ 未完成的被标 failed", t3.get("status") == A.ST_FAILED, t3.get("status"))
    check("给了原因", bool(t3.get("error")), t3.get("error"))
    shutil.rmtree(root3, ignore_errors=True)

    print("\n[H] 时钟注入决定目录日期")
    root4 = tempfile.mkdtemp(prefix="aigc_clock_")
    other = time.mktime((2027, 1, 2, 3, 4, 5, 0, 0, -1))
    svc4 = A.AigcService(root4, cfg_getter=lambda: {"aigc_enable": True},
                         clock=lambda: other)
    svc4.register_backend(A.KIND_IMAGE, lambda t, p, c: {"artifact": b"x", "ext": ".png"})
    r4 = svc4.submit(A.KIND_IMAGE, {}, sync=True)
    art4 = (svc4.get((r4.get("task") or {}).get("id")) or {}).get("artifact") or ""
    check("★ 按注入的时钟分目录", os.path.basename(os.path.dirname(art4)) == "20270102", art4)
    shutil.rmtree(root4, ignore_errors=True)

    for r in (root, root2):
        shutil.rmtree(r, ignore_errors=True)

    print("\n" + "=" * 60)
    print(f"通过 {PASS} / 失败 {FAIL}")
    if FAILURES:
        print("失败项：")
        for f in FAILURES:
            print("  -", f)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
