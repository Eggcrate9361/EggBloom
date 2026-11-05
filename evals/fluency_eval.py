# -*- coding: utf-8 -*-
"""evals/fluency_eval.py —— 话病评测台（1.6.0 P1-2，2026-11-05）

    python evals/fluency_eval.py            # 好样本全绿 + 坏样本全被抓
    python evals/fluency_eval.py --json     # 给 CI

## 和 reply 评测台（40 场）的分工

`run_eval.py` 评的是「sample 过一遍生产后处理链再判分」，锁的是后处理 + 通用词表。
这里评的是**模型原文**：因为后处理本来就会把一部分坏句修好，若把 broken 也过
那条链，"故意改坏提示词"就抓不到了。两套互补，不混算（README 的规矩）。

判据尽量复用生产同一份实现：编造/数字直接调 `harness/grounding.ungrounded()`，
不另造一套"什么算编"的规则；其余四类是纯机械规则（词表 + 2-gram 重合）。

## 验收判据（交接文档 P1-2）

  · 21 场基线全过
  · 故意改坏的提示词能被这些场次抓到 → 每条 case 自带 broken，逐条必须判红
  · 判据不能空转 → 把专项判据关掉后坏样本必须全绿（tests/test_fluency_eval.py 锁）
"""
import argparse
import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from harness import grounding  # noqa: E402
from reply_budget import strip_ctx_marks, visible  # noqa: E402

CASES = os.path.join(_HERE, "fluency_cases.json")

#: 反问：句末问号 + 反问/追问词。好样本不带问号，不会误伤。
_RHET = re.compile(r"(你觉得|你说|不是吗|难道|不然|对吧|该谢|不会看|吗|呢)")
#: 冷场：纯语气词 / 敷衍开头。短附和是活人感，但「嗯，我听着」这种在有内容
#: 的追问下就是冷场 —— 判据限定"敷衍词开头且整句 ≤6 字"。
_FLAT = ("嗯", "哦", "啊", "行", "好吧", "随便", "收到", "在呢", "嗯嗯", "哦哦", "好的")
#: 复读重合阈值：与上一条自己的回复 ≥0.6 的双字重合即判复读
_REPEAT_THR = 0.6


def _bigrams(s):
    t = re.sub(r"[\s，。！？、,.!?~～…「」『』\"'（）()]", "", str(s or ""))
    return {t[i:i + 2] for i in range(len(t) - 1)} if len(t) > 1 else set()


def _judge_grounding(case, text):
    """编造 / 数字：生产同一份事实围栏。数字类只挑时长主张。"""
    bad = grounding.ungrounded(
        text,
        evidence_texts=case.get("evidence") or "",
        user_texts=case.get("user_texts"),
        own_texts=case.get("own_texts"),
        now=case.get("now"), since=case.get("since"))
    if case.get("category") == "数字":
        bad = [c for c in bad if c.get("kind") == "time"]
    return ["事实围栏：%s「%s」" % (c.get("kind"), c.get("quote")) for c in bad]


def _judge_rhetorical(case, text):
    t = re.sub(r"\s+", "", str(text or ""))
    if not t.endswith(("?", "？")) or not _RHET.search(t):
        return []
    return ["反问收尾（把问题扔回去）：" + t[:20]]


def _judge_repeat(case, text):
    prev = case.get("prev") or ""
    a, b = _bigrams(text), _bigrams(prev)
    if not a or not b:
        return []
    rate = len(a & b) / float(len(a))
    return ["复读上一条（重合 %.2f）" % rate] if rate >= _REPEAT_THR else []


def _judge_cold(case, text):
    vis = visible(text)
    if not vis:
        return ["空回复（冷场）"]
    if len(vis) <= 2:
        return ["纯语气词/过短：" + vis]
    for w in _FLAT:
        if vis.startswith(w) and len(vis) <= 6:
            return ["敷衍开头（%s）：%s" % (w, vis)]
    return []


JUDGES = {
    "编造": _judge_grounding,
    "数字": _judge_grounding,
    "反问": _judge_rhetorical,
    "复读": _judge_repeat,
    "冷场": _judge_cold,
}


def load_cases(path=CASES):
    with open(path, encoding="utf-8") as f:
        return (json.load(f).get("cases") or [])


def prepare(raw):
    """只去格式标记，不做 de_ai / reply_guard 改写（见文件头说明）。"""
    return strip_ctx_marks(str(raw or "")).strip()


def judge_case(case, raw, enabled=True):
    """返回 failures 列表；空 = 通过。enabled=False 用于证明判据不是空转。"""
    text = prepare(raw)
    if not text:
        return ["空回复"]
    if not enabled:
        return []
    fn = JUDGES.get(str(case.get("category") or ""))
    return fn(case, text) if fn else ["未知分类：" + str(case.get("category"))]


def run(cases, enabled=True):
    """好样本基线。"""
    return [(c, judge_case(c, c.get("sample"), enabled), c.get("sample") or "")
            for c in cases]


def run_broken(cases, enabled=True):
    """改坏后的样本：每条都必须被抓到。"""
    return [(c, judge_case(c, c.get("broken"), enabled), c.get("broken") or "")
            for c in cases]


def summary(results):
    out = {}
    for item in results:
        c, fails = item[0], item[1]
        cat = c.get("category", "?")
        slot = out.setdefault(cat, {"n": 0, "ok": 0})
        slot["n"] += 1
        slot["ok"] += 0 if fails else 1
    return out


def report(results, broken_results):
    print("┌────────────────┬──────┬──────┐")
    print("│ 话病类别       │ 过/总 │ 状态 │")
    print("├────────────────┼──────┼──────┤")
    for cat, s in summary(results).items():
        pad = " " * max(0, 14 - len(cat) * 2)
        print(f"│ {cat}{pad}│ {s['ok']}/{s['n']}  │ {'OK' if s['ok'] == s['n'] else 'RED':<4} │")
    print("└────────────────┴──────┴──────┘")

    bad = [(c, f, o) for c, f, o in results if f]
    missed = [(c, f, o) for c, f, o in broken_results if not f]
    if bad:
        print("\n好样本误判（不该红）：")
        for c, f, o in bad:
            print(f"  [{c.get('id')}] {o!r} → {f}")
    if missed:
        print("\n坏样本漏抓（提示词改坏了却没红）：")
        for c, f, o in missed:
            print(f"  [{c.get('id')}] {c.get('category')} {o!r}")

    total = len(results)
    passed = total - len(bad)
    caught = len(broken_results) - len(missed)
    print(f"\nRESULT: fluency {total} 场 / {passed} 过 / {len(bad)} 红 "
          f"· 坏样本 {caught}/{len(broken_results)} 抓到")
    return 0 if (not bad and not missed) else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description="EggBloom 话病评测台")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--file", default=CASES)
    ap.add_argument("--no-broken", action="store_true", help="只跑好样本基线（调试用）")
    args = ap.parse_args(argv)

    cases = load_cases(args.file)
    if not cases:
        print("场景为空：" + args.file, file=sys.stderr)
        return 2
    results = run(cases)
    broken = run_broken(cases) if not args.no_broken else []

    if args.json:
        payload = {
            "total": len(results),
            "passed": sum(1 for _, f, _ in results if not f),
            "caught": sum(1 for _, f, _ in broken if f),
            "broken_total": len(broken),
            "cases": [{"id": c.get("id"), "category": c.get("category"),
                       "ok": not f, "failures": f} for c, f, _ in results],
            "broken": [{"id": c.get("id"), "caught": bool(f), "failures": f}
                       for c, f, _ in broken],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if payload["passed"] == payload["total"] \
            and payload["caught"] == payload["broken_total"] else 1
    return report(results, broken)


if __name__ == "__main__":
    sys.exit(main())
