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

★ 2026-10-23 第二批：另有一套 merge_window（三个 group_msg_buffer_window*
  配置键）在算"还能不能合并进来"，与 _batch_delay 是两套独立判据。
  实测三个档位里两个空转、一个有害（详见 _batch_delay docstring）。
  现在"批次何时关闭"由 _batch_deadline 算出的 deadline 独家决定，
  下面第 7 节钉的就是这件事：deadline 与定时器**必须出自同一次计算**。
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

# ★ 测试固定 1.5.9 §B2 的分级默认值：不依赖本机 bot_config.json ——
#   否则同一份测试在不同机器上期望值不同（实测本机配置 5/8 与默认 3/2 不一致）。
bot_core._config["group_msg_buffer_window"] = 3
bot_core._config["group_msg_buffer_window_session"] = 3
bot_core._config["group_msg_buffer_window_trigger"] = 2


def mk(**kw):
    base = dict(same_sender=False, in_session=False,
                triggered=False, has_context=False, has_bare_image=False,
                first_time=None, now=0)
    base.update(kw)
    return d(**base)


# ── 1. 基本档位 ──────────────────────────────────────────────
check("普通消息等 3 秒", mk() == 3, f"实际={mk()}")
check("同人续发等 3 秒", mk(same_sender=True) == 3)
check("会话模式等 3 秒", mk(in_session=True) == 3)
check("同人优先于会话（同档）", mk(same_sender=True, in_session=True) == 3)

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
check("合并 + 同人 + 裸图 → 4 秒（裸图下限）",
      mk(same_sender=True, has_bare_image=True) == 4,
      "同人 3 秒比裸图 4 秒短，取 4")
check("合并 + 换人 + 裸图 → 4 秒",
      mk(has_bare_image=True) == 4)
check("非裸图不受 4 秒影响", mk(has_bare_image=False) == 3)


# ── 4. 整批 8 秒硬顶 ────────────────────────────────────────
check("硬顶：批内第 7 秒只剩 1 秒",
      mk(same_sender=True, has_context=True, first_time=0, now=7) == 1,
      "15 秒硬顶不成立，8 秒到点必须收")
check("硬顶：批内第 3 秒剩 2 秒",
      mk(same_sender=True, has_context=True, first_time=0, now=3) == 2)
check("硬顶保底 1 秒，不会归零或负数",
      mk(same_sender=True, has_context=True, first_time=0, now=100) == 1,
      "归零会让 Timer 立刻触发，等于不合并")
check("无 first_time 时不设硬顶",
      mk(same_sender=True, has_context=True, first_time=None) == 3)


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
# 1.5.9 §B2：分级目标里有 2.0 / 3.0，返回值改 float。锁「正数且不超过硬顶」这条不变量，
# 而不是锁类型 —— 类型不是行为。
check("全部不超过硬顶", all(v <= bot_core.GROUP_BATCH_HARD_CAP for v in vals), f"实际={vals}")
check("全部是数字", all(isinstance(v, (int, float)) for v in vals), f"实际={vals}")


# ── 7. deadline 与定时器同源（本轮修的核心）──────────────────
#   旧代码：定时器按 _batch_delay(4s) 设，"能不能合并"按 merge_window(3s) 判。
#   结果定时器还没响，缓冲区先被判过期 —— 本该等的 1 秒被吃掉。
#   现在两者都由 _batch_deadline 一次算出来，这里钉死它们不可能再分叉。
dl = bot_core._batch_deadline

base7 = dict(same_sender=False, in_session=False, triggered=False,
             has_context=False, has_bare_image=False)

# 同一个 now，deadline - now 必须恰好等于 _batch_delay 的返回值
for name, kw in (("裸图", dict(has_bare_image=True)),
                 ("同人", dict(same_sender=True)),
                 ("触发", dict(triggered=True)),
                 ("普通", dict())):
    _d = d(now=100, first_time=100, **dict(base7, **kw))
    _dl = dl(now=100, first_time=100, **dict(base7, **kw))
    check(f"「{name}」deadline - now == delay", _dl - 100 == _d, f"deadline 差={_dl-100} delay={_d}")

# ★ 关键回归：窗口 3 秒、裸图 4 秒的场景，deadline 必须比旧窗口晚
#   （旧代码这里会在第 3 秒判过期，把批次提前结算）
check("裸图 deadline 晚于旧 3 秒窗口",
      dl(now=0, first_time=0, **dict(base7, has_bare_image=True)) > 3,
      "★ 旧 merge_window=3 < delay=4，窗口先到期 → 本该等 4 秒的批次 3 秒就被结算")

# 硬顶必须在 deadline 上也生效（不能只有定时器受硬顶约束）
check("deadline 受 5 秒硬顶约束",
      dl(now=3, first_time=0, **dict(base7, same_sender=True)) == 5,
      "第 3 秒又来一条同人消息：delay 本要 3 秒（到 6），硬顶压到 5")

# first_time 必传：传错就等于换了批次，界面上不会有任何提示，所以只允许显式传
try:
    dl(now=0, **base7)
    check("deadline 缺 first_time 必须报错", False, "缺参数却静默成功，硬顶起点会变成 now")
except TypeError:
    check("deadline 缺 first_time 必须报错", True)

# ── 8. 配置值真的被读到（三个键不能变成死配置）──────────────
#   这三个键挂在群聊设置页上。如果 _batch_delay 不读它们，用户在界面上
#   改成什么都毫无效果 —— 那和 SESSION_MAX_ROUNDS 那种死配置是同一类问题。
_cfg = bot_core._config
_saved = {k: _cfg.get(k) for k in
          ("group_msg_buffer_window", "group_msg_buffer_window_session",
           "group_msg_buffer_window_trigger")}
try:
    _cfg["group_msg_buffer_window"] = 9
    check("普通档读 group_msg_buffer_window",
          d(now=0, first_time=None, **base7) == 9,
          "设成 9 就应得 9，得 3 说明没读配置")
    _cfg["group_msg_buffer_window_session"] = 7
    check("同人档读 group_msg_buffer_window_session",
          d(now=0, first_time=None, **dict(base7, same_sender=True)) == 7)
    _cfg["group_msg_buffer_window_trigger"] = 6
    check("触发档读 group_msg_buffer_window_trigger",
          d(now=0, first_time=None, **dict(base7, triggered=True)) == 6)
finally:
    for k, v in _saved.items():
        if v is None:
            _cfg.pop(k, None)
        else:
            _cfg[k] = v

# 配置写坏（负数 / 非数字）必须退回默认值，不能算出 0 或负数秒
_check_dl = bot_core._delay_cfg
try:
    _cfg["group_msg_buffer_window"] = 0
    check("配置 0 秒退回默认", _check_dl("group_msg_buffer_window", 3) == 3,
          "0 会让 Timer 立即触发，等于不合并")
    _cfg["group_msg_buffer_window"] = "abc"
    check("配置非数字退回默认", _check_dl("group_msg_buffer_window", 3) == 3)
    _cfg["group_msg_buffer_window"] = -5
    check("配置负数退回默认", _check_dl("group_msg_buffer_window", 3) == 3)
finally:
    for k, v in _saved.items():
        if v is None:
            _cfg.pop(k, None)
        else:
            _cfg[k] = v


print()
if FAIL:
    print("FAILED %d / %d" % (len(FAIL), len(PASS) + len(FAIL)))
    for f in FAIL:
        print("  -", f)
    sys.exit(1)
print("PASS all %d" % len(PASS))