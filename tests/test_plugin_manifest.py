# -*- coding: utf-8 -*-
"""1.5.10 H1/H2 回归：plugin.json 读取/校验 + 冲突检查（只警告）。"""
import io
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from plugins import plugin_manifest as pmf  # noqa: E402

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(("  [OK]   " if ok else "  [FAIL] ") + name + ("" if ok else f"  {detail}"))


def _mk(root, pid, obj):
    d = os.path.join(root, pid)
    os.makedirs(d, exist_ok=True)
    with io.open(os.path.join(d, "plugin.json"), "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)


print("\n[1] 清单读取与校验")
tmp = tempfile.mkdtemp(prefix="h1_")
try:
    _mk(tmp, "alpha", {"id": "alpha", "provides": ["reply.policy"], "scope": "group"})
    m, errs = pmf.load(os.path.join(tmp, "alpha"))
    check("合法清单读取成功", m and m["id"] == "alpha" and not errs, (m, errs))

    _mk(tmp, "bad", {"id": "", "scope": "everywhere"})
    m2, errs2 = pmf.load(os.path.join(tmp, "bad"))
    check("★ 缺 id / 非法 scope 被校验拦下", m2 is None and len(errs2) >= 2, errs2)

    _mk(tmp, "noid", {"provides": []})
    m3, errs3 = pmf.load(os.path.join(tmp, "noid"), fallback_id="noid")
    check("缺 id 时用目录名兜底", m3 and m3["id"] == "noid" and not errs3, (m3, errs3))

    allm = pmf.read_all(tmp)
    check("read_all 汇总清单", set(allm) == {"alpha", "noid"}, allm)
finally:
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)

print("\n[2] 冲突检查（H2）")
manifests = {
    "a": {"id": "a", "provides": ["reply.policy"], "requires": []},
    "b": {"id": "b", "provides": ["reply.policy"], "requires": []},
    "c": {"id": "c", "provides": [], "requires": ["does.not.exist"]},
}
rep = pmf.check(manifests)
check("★ 同一能力被两位声明 → 冲突", rep["duplicate_provides"].get("reply.policy") == ["a", "b"], rep)
check("★ requires 指向不存在的提供者 → 缺失依赖", ["c", "does.not.exist"] in rep["missing_requires"], rep)

cyc = {
    "x": {"id": "x", "provides": ["cap.x"], "requires": ["cap.y"]},
    "y": {"id": "y", "provides": ["cap.y"], "requires": ["cap.x"]},
}
rep2 = pmf.check(cyc)
check("★ 互相 requires → 检出依赖环", bool(rep2["cycles"]), rep2)

clean = {"solo": {"id": "solo", "provides": ["p"], "requires": ["p"]}}
rep3 = pmf.check(clean)
check("自给自足不算冲突", not rep3["duplicate_provides"] and not rep3["missing_requires"], rep3)

print("\n[3] 兼容层：仓库里的真实清单")
real = pmf.read_all(os.path.join(ROOT, "plugins"))
check("★ web_search 有清单且能力声明正确",
      real.get("web_search", {}).get("provides") == ["web.search"], real.get("web_search"))
check("无清单的旧插件不在 read_all 里（照常加载）", "proactive_talk" not in real, sorted(real))

print("\n" + "=" * 56)
print(f"通过 {len(PASS)} / 失败 {len(FAIL)}")
if FAIL:
    print("失败项：", FAIL)
sys.exit(1 if FAIL else 0)
