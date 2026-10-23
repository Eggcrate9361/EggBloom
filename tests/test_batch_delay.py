# -*- coding: utf-8 -*-
"""群消息合并的等待时长（2026-10-23）。

★ 为什么要有这个文件：
  这段延迟计算原来写在**两个地方**（合并到已有批次 / 新建批次），各算各的，
  公式不一致，实测差出三类行为差异：
    · 裸图 4 秒只有合并分支有 —— "一张图作为本批第一条"完全没等，
      而图片下载 + 视觉理解慢的正是这个场景
    · 触发等待 15 vs 10，同一种情况两个数
    · 8 秒硬顶只有合并分支有

  抽成 _batch_delay 之后，这里钉死的是「两边行为必须一致」+「每条规则真的生效」。
  这类差异不会报错、不会崩，只会让某些场景的回慢半拍，所以必须有测试盯着。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import bot_core  # noqa: E402

PASS = []
FAIL = []


def check(name, cond, why=""):
    (PASS if cond else FAIL).append(name if not why or cond else f"{name}　← {why}")


d = bot_core._batch_delay


def mk(**kw):
    base = dict(same_sender=False, in_session=False,
                triggered=False, has_context=False, has_bare_image=False,
                first_time=None, now=0)
    base.update(kw)
    return d(**base)


# ── 1. 基本档位 ──────────────────────────────────────────────
check("普通消息等 3 秒", mk() == 3, f"实际={mk()}")
check("同人续发等 5 秒", mk(same_sender=True) == 5)
check("会话模式等 5 秒", mk(in_session=True) == 5)
check("同人优先于会话", mk(same_sender=True, in_session=True) == 5)

# 触发但已有上下文：不该再等（他在说事，不是在等 @）
check("触发但已有上下文 → 3 秒",
      mk(triggered=True, has_context=True) == 3,
      "已有上下文说明他已经在说了，不需要为 @ 额外等")


# ── 2. 触发等待（2026-10-23 用户定为 8，与硬顶对齐）──────────
check("触发无上下文 → GROUP_TRIGGER_DELAY",
      mk(triggered=True) == bot_core.GROUP_TRIGGER_DELAY)
# ★ 关键：设了 first_time 之后，触发等待**不会**被硬顶压成别的值
check("触发 + 硬顶不冲突",
      mk(triggered=True, first_time=0, now=0) == bot_core.GROUP_TRIGGER_DELAY,
      "15 曾被 8 秒硬顶恒压成 8，那个 15 是死值；现在两者对齐")


# ── 3. 裸图至少 4 秒（这是本次修的核心）────────────────────
check("新建批次 + 裸图 → 至少 4 秒",
      mk(has_bare_image=True, first_time=0, now=0) >= 4,
      "★ 原代码只在合并分支应用这条，新建批次（本批第一条就是图）没等")
check("合并 + 同人 + 裸图 → 5 秒（不降级）",
      mk(same_sender=True, has_bare_image=True) == 5,
      "同人本来等 5 秒，比 4 秒长，不该被压下来")
check("合并 + 换人 + 裸图 → 4 秒",
      mk(has_bare_image=True) == 4)
check("非裸图不受 4 秒影响", mk(has_bare_image=False) == 3)


# ── 4. 整批 8 秒硬顶 ────────────────────────────────────────
check("硬顶：批内第 7 秒只剩 1 秒",
      mk(same_sender=True, has_context=True, first_time=0, now=7) == 1,
      "15 秒硬顶不成立，8 秒到点必须收")
check("硬顶：批内第 3 秒剩 5 秒",
      mk(same_sender=True, has_context=True, first_time=0, now=3) == 5)
check("硬顶保底 1 秒，不会归零或负数",
      mk(same_sender=True, has_context=True, first_time=0, now=100) == 1,
      "归零会让 Timer 立刻触发，等于不合并")
check("无 first_time 时不设硬顶",
      mk(same_sender=True, has_context=True, first_time=None) == 5)


# ── 5. 新建批次这一侧的两条不变量 ───────────────────────────
#   抽函数之后两处调用方算的是同一件事，入参相同结果必然相同 —— 那不用测。
#   会写错的是"新建批次该传什么"，所以钉这两条：
#
#   ① 新建批次时 first_time == now，硬顶左余恰为 8，对结果没有影响。
#      也就是说"新建分支没写硬顶"不是漏，是等价 —— 这一点必须测，
#      否则下次有人看到两边不对称，会以为补上硬顶才是修 bug。
for name, kw in (("裸图", dict(has_bare_image=True)), ("触发", dict(triggered=True)), ("普通", dict())):
    with_ft = mk(first_time=0, now=0, triggered=kw.get("triggered", False),
                 has_bare_image=kw.get("has_bare_image", False))
    no_ft = mk(first_time=None, now=0, triggered=kw.get("triggered", False),
               has_bare_image=kw.get("has_bare_image", False))
    check(f"「{name}」新建时硬顶是空操作", with_ft == no_ft, f"带={with_ft} 不带={no_ft}")

#   ② 新建批次不存在"同人续发"（这一批的第一条，之前没有人）。
#      传了 same_sender=True 就等于把三连发规则用在了错误的位置，
#      会让每张图都等 5 秒而不是 4 秒。
check("新建批次不适用同人规则",
      mk(same_sender=False, has_bare_image=True) == 4,
      "本批第一条没有'上一条'，same_sender 必须是 False")


# ── 6. 返回值必须可直接喂 Timer ─────────────────────────────
vals = [mk(same_sender=True), mk(has_bare_image=True), mk(triggered=True),
        mk(first_time=0, now=99)]
check("全部为正数", all(v > 0 for v in vals), f"实际={vals}")
check("全部是整数秒", all(isinstance(v, int) for v in vals), f"实际={vals}")


print()
if FAIL:
    print("FAILED %d / %d" % (len(FAIL), len(PASS) + len(FAIL)))
    for f in FAIL:
        print("  -", f)
    sys.exit(1)
print("PASS all %d" % len(PASS))