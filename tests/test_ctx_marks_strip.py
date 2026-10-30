# -*- coding: utf-8 -*-
"""上下文标记：模型模仿来的时间戳要剥掉（2026-10-27 真机）

## 真机现象

    18:48:38 群回复: [18:46] 别戳
    18:55:20 [表情] 解析结果: cleaned=[10-27 18:55] 记不住，我又不是录音机

输入行首是 `[HH:MM] [名字·锚] 原文`，**当前那条不带时间戳** ——
两种格式不一致，模型就学会了"回复该带时间戳"。

## 契约

1. 行首的上下文标记要能剥掉（时间戳两种格式 + 说话人标签）
2. 行中间的不动（"我在[18:46]说的"是正文）
3. 字数计算不能把标记算进去
4. 权威实现只有一处（reply_budget.strip_ctx_marks），bot_core 只转发

运行：python tests/test_ctx_marks_strip.py
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


def test_strip_marks():
    import reply_budget as rb
    check("时间戳 [HH:MM]", rb.strip_ctx_marks("[18:46] 别戳") == "别戳",
          rb.strip_ctx_marks("[18:46] 别戳"))
    check("时间戳 [HH:MM:SS]",
          rb.strip_ctx_marks("[18:46:16] 塔露拉") == "塔露拉",
          rb.strip_ctx_marks("[18:46:16] 塔露拉"))
    check("日期+时间戳 [MM-DD HH:MM]",
          rb.strip_ctx_marks("[10-27 18:55] 记不住，我又不是录音机")
          == "记不住，我又不是录音机",
          rb.strip_ctx_marks("[10-27 18:55] 记不住，我又不是录音机"))
    check("说话人标签",
          rb.strip_ctx_marks("[小a·8zff] 喂") == "喂",
          rb.strip_ctx_marks("[小a·8zff] 喂"))
    check("行中间的不动",
          rb.strip_ctx_marks("我在[18:46]说的") == "我在[18:46]说的")
    check("普通文本不变",
          rb.strip_ctx_marks("普通回复") == "普通回复")
    check("多行各自处理",
          rb.strip_ctx_marks("[18:46] a\n[18:47] b") == "a\nb",
          rb.strip_ctx_marks("[18:46] a\n[18:47] b"))
    # 2026-10-30（P2-2）：时间锚换成 `10-30 11:40｜`，仍要能剥（兜照抄）
    check("新时间锚 10-30 11:40｜",
          rb.strip_ctx_marks("10-30 11:40｜ 别戳") == "别戳",
          rb.strip_ctx_marks("10-30 11:40｜ 别戳"))
    check("新时间锚带秒",
          rb.strip_ctx_marks("10-30 11:40:05｜ 别戳") == "别戳",
          rb.strip_ctx_marks("10-30 11:40:05｜ 别戳"))
    check("新锚不误伤行首正文",
          rb.strip_ctx_marks("现在10-30 11:40｜开会") == "现在10-30 11:40｜开会",
          rb.strip_ctx_marks("现在10-30 11:40｜开会"))


def test_hist_prefix_format():
    """历史时间锚本身不再用方括号（P2-2 的根因修复）。"""
    import time as _t
    import bot_core
    s = bot_core._hist_time_prefix({"ts": _t.time()})
    check("时间锚不以 [ 开头", not s.startswith("["), s)
    check("时间锚带 ｜ 分隔符", "｜" in s, s)
    check("没有 ts 时给空串", bot_core._hist_time_prefix({}) == "",
          bot_core._hist_time_prefix({}))


def test_visible_excludes_marks():
    """字数计算不能把标记算进去（否则长度判据全歪）。"""
    import reply_budget as rb
    a = rb.visible("[18:46] 别戳")
    b = rb.visible("别戳")
    check("visible 剥掉标记", a == b, (a, b))


def test_bot_core_forwards():
    """bot_core 只做转发，不重复实现（一条规则一个权威源）。"""
    import io
    src = io.open(os.path.join(ROOT, "bot_core.py"), encoding="utf-8").read()
    i = src.find("def _strip_context_marks(")
    seg = src[i:i + 900]
    check("转发到 reply_budget", "reply_budget.strip_ctx_marks" in seg, seg[:300])
    check("没有重复实现正则",
          "re.sub(r'^\\[" not in seg, "还有内联正则")


def main():
    print("=" * 58)
    print("上下文标记剥离")
    print("=" * 58)
    for fn in (test_strip_marks,
               test_visible_excludes_marks,
               test_hist_prefix_format,
               test_bot_core_forwards):
        print(f"\n[{fn.__name__}]")
        fn()
    print("\n" + "=" * 58)
    if _FAIL:
        print(f"失败 {len(_FAIL)}：{_FAIL}")
        sys.exit(1)
    print("全部通过")


if __name__ == "__main__":
    main()
