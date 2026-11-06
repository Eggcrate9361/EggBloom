# -*- coding: utf-8 -*-
"""tests/test_tool_capability_groups.py —— 工具许可 = 身份，不是措辞（1.6.0）

## 为什么有这一条

工具该不该给模型，以前在 `_tools_subset_for_scene()` 里按**写死的组**判断，
而组之外还有一层 `bot_hook` 的关键词裁剪（"文件/截图/发我"命中才给 desktop）。
关键词那层 2026-11-06 已被 `tools_full_inject_enable=true` 绕开，但写死的组
带来两个问题：

  1. 想"私聊只让聊天"必须改代码 —— 现在三份名单进配置中心；
  2. **权限不够**和**这轮没挂上**在能力清单里混成一句「要的话直接说」。
     模型照做：非管理员问「能发文件吗」→「能，把路径给我」→ 然后没有工具。
     这不是模型撒谎，是我们教错了。

判据：
  [A] 默认组语义与旧行为完全等价（群不给本机能力 / 私聊白名单全量 / 非白名单只读）
  [B] 三份名单可从配置覆盖；空数组回默认（不能手滑把自己锁死）
  [C] 不认识的组名忽略并保留其余（配置写错不至于"工具全没"）
  [D] 能力清单：被权限挡掉的进 need_admin，文案是「别先答应」，不是「要的话直接说」
  [E] 真机形状：非白名单私聊里 desktop 工具被标权限；白名单里正常可用
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from _sandbox import sandbox_neurobase  # noqa: E402

sandbox_neurobase()

import bot_core as bc  # noqa: E402
import capabilities_view as cv  # noqa: E402
from harness import tool_registry as tr  # noqa: E402

PASS = 0
FAIL = 0
FAILURES = []


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK]   {name}")
    else:
        FAIL += 1
        FAILURES.append(name + (f"  → {extra}" if extra else ""))
        print(f"  [FAIL] {name}  {extra}")


def sch(n):
    return {"type": "function",
            "function": {"name": n, "parameters": {"type": "object", "properties": {}}}}


# 四组各注册一个假工具（真实的注册表在测试进程里是空的）
for _n, _g in (("_g_core", tr.GROUP_CORE), ("_g_work", tr.GROUP_WORK),
               ("_g_desktop", tr.GROUP_DESKTOP), ("_g_write", tr.GROUP_WRITE),
               ("_g_proc", tr.GROUP_PROC), ("_g_group", tr.GROUP_GROUP)):
    tr.register(_n, sch(_n), groups=(_g,), source="test")


def groups_set(names):
    out = set()
    for n in names:
        out |= set(tr.groups_of(n))
    return out


def main():
    cfg = bc._config
    saved = {k: cfg.get(k) for k in
             ("group_tool_groups", "private_tool_groups", "private_public_groups",
              "agent_as_tool", "private_tool_whitelist")}
    try:
        print("=== A) 默认语义与旧行为等价 ===")
        for k in ("group_tool_groups", "private_tool_groups", "private_public_groups"):
            cfg[k] = []
        g = set(bc._tools_subset_for_scene(True, privileged=True))
        check("群聊有 core/work/group", {"_g_core", "_g_work", "_g_group"} <= g, sorted(g))
        check("群聊没有 desktop", "_g_desktop" not in g, sorted(g))
        check("群聊没有 write", "_g_write" not in g, sorted(g))
        check("群聊没有 proc", "_g_proc" not in g, sorted(g))

        p = set(bc._tools_subset_for_scene(False, privileged=True))
        check("私聊白名单有 desktop", "_g_desktop" in p, sorted(p))
        check("私聊白名单有 write/proc", {"_g_write", "_g_proc"} <= p, sorted(p))

        n = set(bc._tools_subset_for_scene(False, privileged=False))
        check("私聊非白名单只能聊天（只剩 core）", "_g_core" in n, sorted(n))
        check("私聊非白名单拿不到 work/desktop/write/proc",
              not ({"_g_work", "_g_desktop", "_g_write", "_g_proc"} & n), sorted(n))

        print("\n=== B) 配置覆盖 ===")
        cfg["private_public_groups"] = ["core"]
        n2 = set(bc._tools_subset_for_scene(False, privileged=False))
        check("只给 core 就真的只剩 core", "_g_work" not in n2 and "_g_core" in n2, sorted(n2))

        cfg["private_tool_groups"] = ["core", "work"]
        p2 = set(bc._tools_subset_for_scene(False, privileged=True))
        check("白名单也能收窄（去掉 desktop）", "_g_desktop" not in p2, sorted(p2))

        cfg["private_tool_groups"] = []
        p3 = set(bc._tools_subset_for_scene(False, privileged=True))
        check("空数组回默认（不会把自己锁死）", "_g_desktop" in p3, sorted(p3))

        print("\n=== C) 写错的组名 ===")
        cfg["private_public_groups"] = ["core", "desktp"]     # 少一个 o
        n3 = set(bc._tools_subset_for_scene(False, privileged=False))
        check("不认识的忽略，其余照常", "_g_core" in n3 and "_g_work" not in n3, sorted(n3))
        cfg["private_public_groups"] = ["desktp"]
        n4 = set(bc._tools_subset_for_scene(False, privileged=False))
        check("全都不认识 → 回默认（core）", "_g_core" in n4 and "_g_work" not in n4, sorted(n4))

        print("\n=== D) 能力清单的三态文案 ===")
        snap = cv.build_snapshot(
            offered_tools=["_g_work"], all_tools=["_g_work", "_g_desktop", "_g_proc"],
            denied_tools=["_g_desktop", "_g_proc"], is_group=False)
        kinds = {i["name"]: k for k, v in snap.items() for i in v}
        check("能用的进 ok", kinds.get("_g_work") == cv.STATE_OK, kinds)
        check("权限不够的进 need_admin", kinds.get("_g_desktop") == cv.STATE_NEED_ADMIN, kinds)
        how = [i.get("how") for i in snap[cv.STATE_NEED_ADMIN] if i["name"] == "_g_desktop"][0]
        check("文案是「别先答应」", "别先答应" in how, how)
        check("文案不再说「要的话直接说」", "直接说" not in how, how)

        lines = "\n".join(cv.render(snap, is_group=False))
        check("渲染出「权限不够」这一行", "权限不够" in lines, lines)
        check("权限不够的工具不在「要的话直接说」那段",
              "_g_desktop" not in lines.split("权限不够")[0].split("这轮没挂上")[-1], lines)

        snap2 = cv.build_snapshot(offered_tools=[], all_tools=["_g_proc"],
                                  denied_tools=[], is_group=False)
        how2 = snap2[cv.STATE_NEED_ENABLE][0].get("how") or ""
        check("没挂上（非权限）仍旧说「要的话直接说」", "直接说" in how2, how2)

        print("\n=== E) 真机形状：非白名单私聊 ===")
        cfg["private_tool_whitelist"] = ["3150007406"]
        cfg["private_public_groups"] = []
        offered_n = set(bc._tools_subset_for_scene(False, privileged=False))
        denied = [n for n in ("_g_desktop", "_g_write", "_g_proc") if n not in offered_n]
        check("非白名单：三个特权工具都在 denied", len(denied) == 3, denied)
        offered_y = set(bc._tools_subset_for_scene(False, privileged=True))
        check("白名单：三个特权工具都在 offered",
              {"_g_desktop", "_g_write", "_g_proc"} <= offered_y, sorted(offered_y))
    finally:
        for k, v in saved.items():
            if v is None:
                cfg.pop(k, None)
            else:
                cfg[k] = v

    print("\n" + "=" * 56)
    print(f"通过 {PASS} / 失败 {FAIL}")
    for n in FAILURES:
        print(f"  FAIL  {n}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
