# -*- coding: utf-8 -*-
"""tools/golden_private.py —— 用历史私聊「空跑」当前提示词

用途：没有真实流量时，验证「现在这套人格 + 上下文」还会不会说出像她的话。
做法：把历史上真实的私聊问答拆出来，用**今天的**人格重建 prompt，重新生成一次，
和当时她实际回的那句放在一起看。

判据不是"和原文一样"（模型必然不同），而是：

  1. 长度分布是否还在原来的区间（突然变长 = 提示词把它变成助手了）；
  2. 有没有新的 AI 腔（"作为…""希望能帮到你"这类）；
  3. 人工抽读：还像不像她。

不写生产目录，不碰账本。API key 从 API_KEY / DEEPSEEK_API_KEY 读。
"""
import argparse
import json
import os
import re
import statistics as st
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ab_brief import _post, build_transcript, load_events  # noqa: E402

#: 一眼能看出"变成客服"的说法
_AI_SMELL = ("作为", "希望能帮到", "希望对你有帮助", "总之", "首先", "其次",
             "如果你需要", "我可以帮你", "请问有什么", "很高兴为你")

DEFAULT_GLOBS = [r"D:\EggBloom水星\server-logs-*\bot-journal.log",
                 r"D:\EggBloom水星\triage-*\bot-journal.log"]


def collect(paths, limit=40, gap_s=240.0):
    """挑「一问一答」的私聊轮次：入站后 3 分钟内有她的出站。"""
    events = load_events(paths)
    by_target = {}
    for e in events:
        by_target.setdefault(e["target"], []).append(e)

    picked = []
    for target, seq in by_target.items():
        if not seq or seq[0]["scene"] != "private":
            continue
        last_ts = 0.0
        for i, e in enumerate(seq):
            if e["dir"] != "in":
                continue
            nxt = next((x for x in seq[i + 1:] if x["dir"] == "out"), None)
            if not nxt or nxt["ts"] - e["ts"] > 180:
                continue
            if e["ts"] - last_ts < gap_s:      # 同一段对话只取一条
                continue
            hist = [x for x in seq[:i]]
            if len(hist) < 4:
                continue
            if e["text"].startswith("[图片]") or e["text"].startswith("[视频]"):
                continue
            last_ts = e["ts"]
            picked.append({
                "id": "g%02d" % (len(picked) + 1),
                "ts": e["ts"],
                "in_text": e["text"][:200],
                "actual": nxt["text"][:200],
                "gap_s": round(nxt["ts"] - e["ts"], 1),
                "transcript": build_transcript(hist),
            })
    picked.sort(key=lambda p: p["ts"])
    return picked[:limit]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["collect", "run"])
    ap.add_argument("--pairs", default="golden.json")
    ap.add_argument("--paths", nargs="*")
    ap.add_argument("--persona", default=os.path.join("plugins", "personality.txt"))
    ap.add_argument("--limit", type=int, default=40)
    a = ap.parse_args()

    if a.mode == "collect":
        import glob
        paths = a.paths or [p for g in DEFAULT_GLOBS for p in sorted(glob.glob(g))]
        picked = collect(paths, a.limit)
        persona = open(a.persona, encoding="utf-8").read()
        for p in picked:
            p["system"] = persona
        json.dump(picked, open(a.pairs, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        print("样本 %d 条" % len(picked))
        return 0

    key = os.environ.get("API_KEY") or os.environ.get("DEEPSEEK_API_KEY") or ""
    url = os.environ.get("AB_URL") or "https://opencode.ai/inference/go/openai/v1/chat/completions"
    model = os.environ.get("AB_MODEL") or "deepseek-v4.1-flash"
    if not key:
        print("没有 API key")
        return 2
    pairs = json.load(open(a.pairs, encoding="utf-8"))
    for p in pairs:
        msgs = [{"role": "system", "content": p["system"]},
                {"role": "user", "content":
                 "以下是最近的对话：\n%s\n\n最新一条：%s\n\n用她的口吻回一句"
                 "（不要解释、不要旁白，只说她会说的话）。" % (p["transcript"], p["in_text"])}]
        try:
            p["new"] = _post(url, key, model, msgs, 0.7, 500)
        except Exception as e:
            p["new"] = ""
            p["error"] = str(e)[:80]
        p["new_len"] = len(p["new"])
        p["actual_len"] = len(p["actual"])
        p["smell"] = [w for w in _AI_SMELL if w in p["new"]]
        print("%s 实际%2d字 / 新%2d字 %s" % (p["id"], p["actual_len"],
                                           p["new_len"], "⚠" if p["smell"] else ""))
        time.sleep(0.3)
    json.dump(pairs, open(a.pairs, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    la = [p["actual_len"] for p in pairs if p["new"]]
    ln = [p["new_len"] for p in pairs if p["new"]]
    print("\n实际中位 %.0f 字 / 新中位 %.0f 字" % (st.median(la), st.median(ln)))
    print("AI 腔命中：%d 条" % sum(1 for p in pairs if p.get("smell")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
