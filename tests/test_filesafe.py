# -*- coding: utf-8 -*-
"""tests/test_filesafe.py —— 文件管理三条红线（1.6.0 P2 第一刀）

验收原文：「文件管理强制人工确认 + 备份 + 回收站（红线不放宽）」。
本用例只钉这三条，不测"能不能整理文件"：

  ① 没有 token 就不能动文件；token 一次性、会过期
  ② move / delete 之前必须留下备份；备份不成就不执行
  ③ delete 永不真删，只进回收站，而且能按账本恢复
"""
import io
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from harness import filesafe as FS  # noqa: E402

PASS = 0
FAIL = 0
FAILURES = []
NOW = 1793700000.0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK]   {name}")
    else:
        FAIL += 1
        FAILURES.append(name + (f"  → {extra}" if extra else ""))
        print(f"  [FAIL] {name}  {extra}")


def main():
    tmp = tempfile.mkdtemp(prefix="filesafe_")
    base = os.path.join(tmp, "store")
    src_dir = os.path.join(tmp, "src")
    dst_dir = os.path.join(tmp, "dst")
    for d in (src_dir, dst_dir):
        os.makedirs(d)

    def mk(name, body="hello"):
        p = os.path.join(src_dir, name)
        io.open(p, "w", encoding="utf-8").write(body)
        return p

    try:
        print("[A] 人工确认：没有 token 动不了文件")
        p = mk("a.txt")
        plan = FS.prepare("move", p, os.path.join(dst_dir, "a.txt"),
                          allowed_roots=[tmp], base_dir=base, now=NOW)
        check("★ move 需要确认", plan.get("requires_confirm") is True, plan)
        check("token 非空", bool(plan.get("token")))
        check("文件还没动", os.path.exists(p) and not os.path.exists(os.path.join(dst_dir, "a.txt")))
        try:
            FS.confirm("deadbeef", now=NOW)
            check("★ 假 token 被拒", False, "没有抛异常")
        except FS.FileSafeError as e:
            check("★ 假 token 被拒", "没有这个待确认" in str(e), str(e))

        print("\n[B] token 一次性 + 会过期")
        r = FS.confirm(plan["token"], now=NOW)
        check("★ 正确 token 执行成功", r.get("ok") is True, r)
        check("文件已移动到目标", os.path.exists(os.path.join(dst_dir, "a.txt")))
        try:
            FS.confirm(plan["token"], now=NOW)
            check("★ 同一个 token 不能再用", False, "第二次也成功了")
        except FS.FileSafeError as e:
            check("★ 同一个 token 不能再用", "已经执行过" in str(e), str(e))

        p2 = mk("b.txt")
        plan2 = FS.prepare("delete", p2, allowed_roots=[tmp], base_dir=base, now=NOW)
        try:
            FS.confirm(plan2["token"], now=NOW + FS.TOKEN_TTL + 1)
            check("★ 过期 token 被拒", False, "过期还执行")
        except FS.FileSafeError as e:
            check("★ 过期 token 被拒", "过期" in str(e), str(e))
        check("过期后文件还在", os.path.exists(p2))
        FS.cancel(plan2["token"])

        print("\n[C] 备份：动之前先留一份")
        p3 = mk("c.txt", "要备份的内容")
        plan3 = FS.prepare("delete", p3, allowed_roots=[tmp], base_dir=base, now=NOW)
        r3 = FS.confirm(plan3["token"], now=NOW)
        check("★ 备份路径存在", bool(r3.get("backup")) and os.path.exists(r3["backup"]), r3)
        with io.open(r3["backup"], encoding="utf-8") as f:
            check("★ 备份内容一致", f.read() == "要备份的内容")

        print("\n[D] 回收站：delete 不真删")
        check("★ 原件已不在原位", not os.path.exists(p3))
        check("★ 进了回收站", bool(r3.get("recycle")) and os.path.exists(r3["recycle"]), r3)
        log = os.path.join(base, "recycle.jsonl")
        check("★ 账本有记录", os.path.exists(log))
        with io.open(log, encoding="utf-8") as f:
            rec = [l for l in f.read().splitlines() if l.strip()]
        check("★ 账本里能查到这条", rec and "c.txt" in rec[-1], rec[-1:])

        print("\n[E] 能恢复")
        back = os.path.join(dst_dir, "c_restored.txt")
        rr = FS.restore(r3["recycle"], back, allowed_roots=[tmp], base_dir=base)
        check("★ 恢复成功", rr.get("ok") is True and os.path.exists(back), rr)
        with io.open(back, encoding="utf-8") as f:
            check("恢复内容一致", f.read() == "要备份的内容")
        try:
            FS.restore(back, back, allowed_roots=[tmp], base_dir=base)
            check("★ 目标已存在时拒绝覆盖", False, "覆盖了")
        except FS.FileSafeError as e:
            check("★ 目标已存在时拒绝覆盖", "不覆盖" in str(e), str(e))

        print("\n[F] 路径边界")
        outside = mk("outside.txt")      # 该文件在 tmp/src 下，确实存在
        try:
            FS.prepare("delete", outside, allowed_roots=[dst_dir], base_dir=base, now=NOW)
            check("★ 越界路径被拒", False, "放行了")
        except FS.FileSafeError as e:
            check("★ 越界路径被拒", "不在允许范围" in str(e), str(e))
        try:
            FS.prepare("move", os.path.join(tmp, "nope.txt"), os.path.join(dst_dir, "x"),
                       allowed_roots=[tmp], base_dir=base, now=NOW)
            check("★ 源不存在被拒", False, "放行了")
        except FS.FileSafeError as e:
            check("★ 源不存在被拒", "不存在" in str(e), str(e))
        try:
            FS.prepare("move", mk("d.txt"), os.path.join(tmp, "no_such_dir", "d.txt"),
                       allowed_roots=[tmp], base_dir=base, now=NOW)
            check("★ 目标目录不存在被拒（不替你造）", False, "放行了")
        except FS.FileSafeError as e:
            check("★ 目标目录不存在被拒（不替你造）", "目标目录不存在" in str(e), str(e))
        try:
            FS.prepare("rm", mk("e.txt"), allowed_roots=[tmp], base_dir=base, now=NOW)
            check("★ 未知操作被拒", False, "放行了")
        except FS.FileSafeError as e:
            check("★ 未知操作被拒", "不支持" in str(e), str(e))

        print("\n[G] 覆盖目标时，被覆盖的也要留备份")
        victim = os.path.join(dst_dir, "f.txt")
        io.open(victim, "w", encoding="utf-8").write("旧的")
        src_f = mk("f.txt", "新的")
        plan_f = FS.prepare("move", src_f, victim, allowed_roots=[tmp], base_dir=base, now=NOW)
        check("★ 提示会覆盖", plan_f.get("will_overwrite") is True, plan_f)
        FS.confirm(plan_f["token"], now=NOW)
        with io.open(victim, encoding="utf-8") as f:
            check("已覆盖成新内容", f.read() == "新的")
        bdir = os.path.join(base, "backups")
        found = []
        for root_d, _dirs, files in os.walk(bdir):
            for fn in files:
                if fn.endswith("f.txt"):
                    found.append(os.path.join(root_d, fn))
        ok = False
        for b in found:
            with io.open(b, encoding="utf-8") as f:
                if f.read() == "旧的":
                    ok = True
        check("★ 旧内容留了备份", ok, found)

        print("\n[H] copy 不需要确认（不破坏），但也会被 path 边界管")
        cp_src = mk("g.txt", "复制我")
        cp_plan = FS.prepare("copy", cp_src, os.path.join(dst_dir, "g.txt"),
                             allowed_roots=[tmp], base_dir=base, now=NOW)
        check("copy 不要求确认", cp_plan.get("requires_confirm") is False, cp_plan)
        FS.confirm(cp_plan["token"], now=NOW)
        check("源还在（是复制不是移动）", os.path.exists(cp_src))
        check("目标也有一份", os.path.exists(os.path.join(dst_dir, "g.txt")))

    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 60)
    print(f"通过 {PASS} / 失败 {FAIL}")
    if FAILURES:
        print("失败项：")
        for x in FAILURES:
            print("  -", x)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
