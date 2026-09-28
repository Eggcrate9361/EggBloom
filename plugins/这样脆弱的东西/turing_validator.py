# -*- coding: utf-8 -*-
"""图灵测试验证：蒸馏出的人格，能不能冒充原主？

两种判法：
  - judge_fn 可用 → 让一个模型做盲判：给同一句刺激和两份回复（一份真的、
    一份是人格文件写的），让它判断哪份更像同一个人。这才是真正的盲测。
  - judge_fn 不可用 → 退回长度+关键词粗评（只当作降级参考，不代表通过）。

方向很重要：被蒸馏的是**目标这个人**，所以要拿"对方说的话"当刺激，
比对"这个人当时怎么回" —— 反过来测就成了在测机器人。
"""
import random
import re


def validate(persona_markdown, test_pairs=None, reply_fn=None, judge_fn=None, rounds=5):
    """执行图灵测试。

    参数：
      persona_markdown 人格文件内容
      test_pairs       [{"prompt": "...", "real_reply": "..."}]
                       prompt = 当时对方说的话；real_reply = 目标本人的真实回复
      reply_fn         fn(persona, prompt) -> str，用该人格生成回复
      judge_fn         fn(items) -> {"verdicts": [{"index": n, "same": bool,
                        "reason": str}], "score": float}；缺省则粗评
      rounds           测试轮数
    """
    if not reply_fn:
        return {"status": "pending", "score": None,
                "detail": "未注入 AI 客户端，图灵测试待配置模型后执行"}
    test_pairs = test_pairs or []
    if not test_pairs:
        return {"status": "pending", "score": None,
                "detail": "没有真实对话样本可做盲测对比"}

    samples = random.sample(test_pairs, min(rounds, len(test_pairs)))
    items = []
    for pair in samples:
        prompt = pair.get("prompt", "")
        real = pair.get("real_reply", "")
        if not prompt or not real:
            continue
        try:
            generated = (reply_fn(persona_markdown, prompt) or "").strip()
        except Exception as e:
            items.append({"prompt": prompt, "real": real, "generated": "",
                          "error": str(e)})
            continue
        items.append({"prompt": prompt, "real": real, "generated": generated})

    items = [it for it in items if it.get("generated") or it.get("error") is not None]
    if not items:
        return {"status": "failed", "score": 0, "detail": "无有效测试样本"}

    # ── 判定 ──
    judged = False
    if judge_fn:
        try:
            verdict = judge_fn(items) or {}
            for v in verdict.get("verdicts") or []:
                try:
                    idx = int(v.get("index"))
                except (TypeError, ValueError):
                    continue
                if 0 <= idx < len(items):
                    items[idx]["same"] = bool(v.get("same"))
                    items[idx]["reason"] = str(v.get("reason") or "")[:120]
            judged = any("same" in it for it in items)
        except Exception as e:
            items[0]["judge_error"] = str(e)

    if not judged:
        for it in items:
            it["score"] = round(_rough_similarity(it.get("generated", ""),
                                                  it.get("real", "")), 2)
            it["same"] = it["score"] >= 0.6

    matches = sum(1 for it in items if it.get("same"))
    pass_rate = matches / len(items)
    status = "passed" if pass_rate >= 0.8 else "failed"
    method = "LLM 盲判" if judged else "粗糙相似度（降级）"
    return {
        "status": status,
        "score": round(pass_rate, 2),
        "method": method,
        "detail": f"{matches}/{len(items)} 轮通过（{pass_rate:.0%}），阈值 80%，判定方式：{method}",
        "rounds": items,
    }


def _rough_similarity(a, b):
    """无 LLM 时的降级判法：长度接近 + 词重叠。只作参考，不代表盲测结果。"""
    if not a or not b:
        return 0.0
    len_score = 1.0 - min(1.0, abs(len(a) - len(b)) / max(1, max(len(a), len(b))))
    wa = set(re.findall(r"[\u4e00-\u9fff]{2,4}", a))
    wb = set(re.findall(r"[\u4e00-\u9fff]{2,4}", b))
    if not wa or not wb:
        return round(len_score * 0.5, 2)
    overlap = len(wa & wb) / min(len(wa), len(wb))
    return round(0.6 * len_score + 0.4 * overlap, 2)
