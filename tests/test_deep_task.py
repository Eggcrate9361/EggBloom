# -*- coding: utf-8 -*-
"""deep_task：agent 从"模式"降成"工具"（1.6.0 刀 3）的可执行判据。

## 钉死什么

1. **注入闸**：`agent_as_tool` 没开 → 模型根本看不到这个工具；开了才进工具集。
   私聊还要求过白名单（`privileged`），群聊还要求 `agent_group_enable` ——
   因为它会开一个能调工具的子 agent，普通私聊用户不该有这双手。
2. **递归闸**：多步任务里再调多步任务 → 直接拒绝（不挡就是无限套娃）。
3. **就绪闸**：`agent_service` 没就绪时返回人话错误，不抛异常、不炸主链。
4. **结果形状**：成功 → `{ok, answer, steps}`；失败/空结论 → `{ok:false, error}`。
5. **退出后能再调**：一次失败不能把递归闸卡死（`depth()` 归零）。

运行：python tests/test_deep_task.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import bot_core  # noqa: E402
from harness import tool_deep as td  # noqa: E402
from harness import tool_registry as tr  # noqa: E402

# 注册本身是被测对象（bot_core 的启动块里也是调这一个函数）
td.register_all()

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name if not extra else f"{name}  → {extra}")


def _names(is_group=False, privileged=True, **cfg):
    backup = dict(bot_core._config)
    try:
        bot_core._config.update(cfg)
        tr.register("deep_task", td.DEEP_TOOLS[0], fn=td.deep_task,
                    groups=(tr.GROUP_DEEP,), source="test")
        return bot_core._tools_subset_for_scene(is_group, privileged=privileged)
    finally:
        bot_core._config.clear()
        bot_core._config.update(backup)


# ── 1. 注册与分组 ────────────────────────────────────────────────────
check("注册后注册表里有 deep_task", tr.has("deep_task"))
check("归在 GROUP_DEEP 组", tr.GROUP_DEEP in tr.groups_of("deep_task"),
      tr.groups_of("deep_task"))
check("GROUP_DEEP 在展示顺序里", tr.GROUP_DEEP in tr.GROUP_SEQ, tr.GROUP_SEQ)
check("默认不在常驻组（不会白占 prompt）",
      tr.GROUP_DEEP not in tr.DEFAULT_RESIDENT_GROUPS, tr.DEFAULT_RESIDENT_GROUPS)

# ── 2. 注入闸 ────────────────────────────────────────────────────────
off = _names(agent_as_tool=False)
check("★ 开关关：私聊不给 deep_task", "deep_task" not in off)
check("★ 开关关：群聊不给 deep_task",
      "deep_task" not in _names(is_group=True, agent_as_tool=False))

on_priv = _names(agent_as_tool=True)
check("★ 开关开：私聊（白名单内）给 deep_task", "deep_task" in on_priv)
check("开关开：私聊但没过白名单不给",
      "deep_task" not in _names(agent_as_tool=True, privileged=False))
check("开关开：群聊还要 agent_group_enable",
      "deep_task" not in _names(is_group=True, agent_as_tool=True))
check("开关开：群聊 + agent_group_enable 才给",
      "deep_task" in _names(is_group=True, agent_as_tool=True,
                            agent_group_enable=True))
check("开关开时其它工具不受影响（只多这一组）",
      set(on_priv) - set(off) == {"deep_task"}, set(on_priv) - set(off))

# ── 3. 递归闸 ────────────────────────────────────────────────────────
r1 = td.deep_task("第一层", _runner=lambda **k: {"ok": True, "answer": "干完了", "steps": 3})
check("正常调用成功", r1.get("ok") and r1.get("answer") == "干完了", r1)
check("正常调用带步数", r1.get("steps") == 3, r1)
check("一次调用后递归闸归零", td.depth() == 0, td.depth())

seen = {}


def _nested(**k):
    """在"多步任务"里再调一次 —— 模拟子 agent 也拿到 deep_task。"""
    seen["inner"] = td.deep_task("第二层", _runner=lambda **k2: {"ok": True, "answer": "不该到"})
    return {"ok": True, "answer": "外层完成", "steps": 1}


r2 = td.deep_task("外层", _runner=_nested)
check("★ 递归闸：内层被拒", seen.get("inner", {}).get("ok") is False, seen.get("inner"))
check("递归闸给的是人话（别套娃）", "套娃" in str(seen.get("inner", {}).get("error", "")),
      seen.get("inner"))
check("外层仍然成功", r2.get("ok") is True, r2)
check("嵌套结束后递归闸归零", td.depth() == 0, td.depth())

# ── 4. 就绪闸 / 失败兜底 ─────────────────────────────────────────────
check("空 task 直接拒", td.deep_task("  ").get("ok") is False, td.deep_task("  "))


def _boom(**k):
    raise RuntimeError("子 agent 炸了")


r3 = td.deep_task("会炸的", _runner=_boom)
check("★ 子 agent 抛异常 → 人话错误，不冒泡", r3.get("ok") is False, r3)
check("异常信息带类型", "RuntimeError" in str(r3.get("error")), r3)
check("★ 炸过之后递归闸没卡死", td.depth() == 0, td.depth())
r4 = td.deep_task("再试一次", _runner=lambda **k: {"ok": True, "answer": "好了", "steps": 1})
check("炸过还能再调", r4.get("ok") is True, r4)

check("子 agent 报错 → 原样传达",
      td.deep_task("x", _runner=lambda **k: {"ok": False, "error": "没钱了"}).get("error") == "没钱了")
check("子 agent 空结论 → 不当成功",
      td.deep_task("x", _runner=lambda **k: {"ok": True, "answer": "  "}).get("ok") is False)
check("子 agent 返回非 dict → 不当成功",
      td.deep_task("x", _runner=lambda **k: "一段字符串").get("ok") is False)

# ── 5. 场景上下文透传 ────────────────────────────────────────────────
got = {}


def _spy(**k):
    got.update(k)
    return {"ok": True, "answer": "ok", "steps": 1}


td.set_context(is_group=True, group_id="829854136", sender="3150007406")
td.deep_task("查一下", context="用户说：看看新闻", _runner=_spy)
check("群场景透传 is_group", got.get("is_group") is True, got.get("is_group"))
check("context_hint 带场景", "群里" in str(got.get("context_hint")), got.get("context_hint"))
check("context_hint 带调用方给的已知信息",
      "看看新闻" in str(got.get("context_hint")), got.get("context_hint"))
check("不对外 publish（别把子过程推给前端）", got.get("publish") is False, got.get("publish"))

td.set_context(is_group=False, group_id="", sender="2011257385")
got.clear()
td.deep_task("查一下", _runner=_spy)
check("私聊场景透传 sender", got.get("is_group") is False and "2011257385" in str(got.get("context_hint")),
      got.get("context_hint"))

# ── 6. 开关默认值 ────────────────────────────────────────────────────
import config as _cfgmod  # noqa: E402

check("★ 默认关：DEFAULT_CONFIG.agent_as_tool=False",
      _cfgmod.DEFAULT_CONFIG.get("agent_as_tool") is False,
      _cfgmod.DEFAULT_CONFIG.get("agent_as_tool"))

print(f"\n通过 {len(PASS)} / 失败 {len(FAIL)}")
for n in FAIL:
    print(f"  FAIL  {n}")
sys.exit(1 if FAIL else 0)
