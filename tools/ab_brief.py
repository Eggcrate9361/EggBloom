# -*- coding: utf-8 -*-
"""tools/ab_brief.py —— 场景简报的 A/B 回放（配对实验）

设计见 `D:\\EggBloom水星\\拟人改造-AB回放方案-20261124.md`。要点：

- 同一轮对话、同一模型，**只改一处**：A 组末尾追加三行「她现在」，B 组不加；
- A/B 顺序随机；每对只跑一次（不做多次采样挑最好）；
- 判据先用硬指标（长度、简报泄漏），再交给另一个模型盲评（甲/乙/平）。

三种运行方式：
    python tools/ab_brief.py collect --out pairs.json     # 只挑样本、造 prompt，不花钱
    python tools/ab_brief.py run --pairs pairs.json       # 真调模型，产出 A/B 回复
    python tools/ab_brief.py judge --pairs pairs.json     # 盲评（随机甲乙）

API key 从环境变量 `API_KEY` / `DEEPSEEK_API_KEY` 读（服务器上是 .env）。
本工具**不写生产目录**，所有产物都在 --out 指定的文件里。
"""
import argparse
import json
import os
import random
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# `autonomy.ledger` 只有 collect 需要；run/judge 在服务器上跑，那里没有这个包，
# 所以延迟导入，避免为了调模型还要拷整个仓库。
L = None


def _ledger():
    global L
    if L is None:
        from autonomy import ledger as _L
        L = _L
    return L

DEFAULT_GLOBS = [r"D:\EggBloom水星\server-logs-*\bot-journal.log",
                 r"D:\EggBloom水星\triage-*\bot-journal.log"]

_TS = re.compile(r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]")
_TS_SYSLOG = re.compile(r"^(\d{1,2})月\s+(\d{1,2})\s+(\d{2}):(\d{2}):(\d{2})")
_CHAT = re.compile(r"\[CHAT\](\{.*\})\s*$")

#: 简报泄漏检测：这些词只可能来自那三行，正常聊天不该出现
_BRIEF_WORDS = ("刚被", "气氛", "隔了", "答应过", "还没人接", "脾气还没")

#: 三类样本的目标配额
QUOTA = {"情绪": 12, "久别": 10, "话头未接": 8}


def _parse_ts(line, default_year=2026):
    import datetime
    m = _TS.search(line)
    if m:
        try:
            return datetime.datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").timestamp()
        except ValueError:
            return None
    m = _TS_SYSLOG.match(line)
    if m:
        mo, d, hh, mm, ss = (int(x) for x in m.groups())
        try:
            return datetime.datetime(default_year, mo, d, hh, mm, ss).timestamp()
        except ValueError:
            return None
    return None


def load_events(paths):
    rows = []
    for p in paths:
        for line in open(p, encoding="utf-8", errors="replace"):
            m = _CHAT.search(line.strip())
            if not m:
                continue
            ts = _parse_ts(line)
            if not ts:
                continue
            try:
                d = json.loads(m.group(1))
            except Exception:
                continue
            text = str(d.get("text") or "").strip()
            if not text:
                continue
            scene = "group" if str(d.get("type") or "") == "group" else "private"
            rows.append({"ts": ts, "dir": str(d.get("direction") or ""), "scene": scene,
                         "target": str(d.get("target") or ""), "text": text,
                         "sender": str(d.get("sender") or "")})
    rows.sort(key=lambda r: r["ts"])
    return rows


def classify(brief: str) -> str:
    if "答应过" in brief:
        return "话头未接"
    if "隔了" in brief or "好几天" in brief:
        return "久别"
    if "上一条" in brief or "刚说过" in brief:
        return "话头未接"
    if any(k in brief for k in ("怼", "呛", "顶", "捧", "夸", "气氛", "带刺")):
        return "情绪"
    return "其它"


def build_transcript(hist, limit=12):
    lines = []
    for h in hist[-limit:]:
        who = "她" if h["dir"] == "out" else ("群友" if h["scene"] == "group" else "对方")
        lines.append("%s：%s" % (who, h["text"][:120]))
    return "\n".join(lines)


def collect(paths, persona_path):
    events = load_events(paths)
    LG = _ledger()
    persona = open(persona_path, encoding="utf-8").read()
    states, hist = {}, {}
    picked, seen_key = [], {}
    for i, ev in enumerate(events):
        key = LG.key_of(ev["scene"], ev["target"])
        hist.setdefault(key, []).append(ev)
        if ev["dir"] == "in":
            e = {"sender": ev["sender"] or ev["target"], "text": ev["text"], "ts": ev["ts"]}
            if ev["scene"] == "group":
                e["group_id"] = ev["target"]
            st = LG.observe_user_message(states.get(key), e, ev["ts"])
            states[key] = st
            brief = LG.render_state_brief(st, ev["ts"] + 60)
            kind = classify(brief) if brief else ""
            if not brief or kind not in QUOTA:
                continue
            # 同一会话 10 分钟内只取一条，避免同一段情绪被反复计分
            if ev["ts"] - seen_key.get(key, 0) < 300:
                continue
            if len([p for p in picked if p["type"] == kind]) >= QUOTA[kind]:
                continue
            text = ev["text"]
            if text.startswith("[图片]") or text.startswith("[视频]"):
                continue
            seen_key[key] = ev["ts"]
            picked.append({
                "id": "s%02d" % (len(picked) + 1),
                "type": kind,
                "scene": ev["scene"],
                "ts": ev["ts"],
                "in_text": text[:200],
                "brief": brief,
                "transcript": build_transcript(hist[key][:-1]),
                "system": persona,
            })
        else:
            e = {"scene": ev["scene"], "text": ev["text"], "ts": ev["ts"],
                 "trace_id": "ab%d" % i}
            if ev["scene"] == "group":
                e["group_id"] = ev["target"]
            else:
                e["user_id"] = ev["target"]
            states[key] = LG.observe_bot_reply(states.get(key), e, ev["ts"])
    return picked


def _post(url, key, model, messages, temperature, max_tokens=500):
    import requests
    # 网关（OpenCode Go）要 User-Agent + x-opencode-session，缺了直接 400/403。
    # 与主链 `_llm_request_headers` 保持同一套要求。
    headers = {
        "Authorization": "Bearer " + key,
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "EggBloom-AB/1.0 (offline replay)",
    }
    if "opencode" in str(url).lower():
        headers["x-opencode-session"] = "eggbloom-ab"
    r = requests.post(url, headers=headers,
                      json={"model": model, "messages": messages,
                            "temperature": temperature, "max_tokens": max_tokens},
                      timeout=120)
    r.raise_for_status()
    return (r.json()["choices"][0]["message"]["content"] or "").strip()


def _user_msg(p, with_brief):
    body = "以下是最近的对话：\n%s\n\n最新一条：%s" % (p["transcript"], p["in_text"])
    if with_brief:
        body += "\n\n她现在：%s" % p["brief"]
    body += "\n\n用她的口吻回一句（不要解释、不要旁白，只说她会说的话）。"
    return body


def run(pairs, url, key, model, temperature=0.7):
    out = []
    for p in pairs:
        order = [("A", True), ("B", False)]
        random.shuffle(order)
        got = {}
        for tag, with_brief in order:
            msgs = [{"role": "system", "content": p["system"]},
                    {"role": "user", "content": _user_msg(p, with_brief)}]
            got[tag] = _post(url, key, model, msgs, temperature)
            time.sleep(0.3)
        leaked = [w for w in _BRIEF_WORDS if w in got["A"]]
        p2 = dict(p, A=got["A"], B=got["B"], A_leak=leaked,
                  A_len=len(got["A"]), B_len=len(got["B"]))
        out.append(p2)
        print("%s %s  A=%d字 B=%d字  leak=%s" % (p["id"], p["type"], len(got["A"]),
                                                 len(got["B"]), leaked or "-"))
    return out


def judge(pairs, url, key, model):
    rubric = ("你是评审。下面同一轮对话有两个回复，甲和乙，来源不同但都合理。\n"
              "按三条各打 1-5 分：\n"
              "1) 接住当下：有没有回应这一句真正在说的事；\n"
              "2) 不像客服：有没有模板味、解释腔、报幕腔；\n"
              "3) 不重复自己：有没有把过去的情绪硬搬过来。\n"
              "注意：更长不是优点。只输出 JSON：{\"甲\":x,\"乙\":y,\"更好\":\"甲|乙|平\",\"why\":\"一句话\"}")
    out = []
    for p in pairs:
        swap = random.random() < 0.5
        jia, yi = (p["B"], p["A"]) if swap else (p["A"], p["B"])
        user = ("对话：\n%s\n\n最新一条：%s\n\n甲：%s\n\n乙：%s"
                % (p["transcript"], p["in_text"], jia, yi))
        try:
            raw = _post(url, key, model,
                        [{"role": "system", "content": rubric},
                         {"role": "user", "content": user}], 0.0, 700)
            m = re.search(r"\{.*\}", raw, re.S)
            j = json.loads(m.group(0)) if m else {}
            j.setdefault("raw", raw[:200])
        except Exception as e:
            j = {"error": str(e)[:80]}
        win = str(j.get("更好") or "")
        # 还原成 A/B
        winner = {"甲": "B" if swap else "A", "乙": "A" if swap else "B"}.get(win, "平")
        out.append(dict(p, judge=j, winner=winner))
        print("%s %s  → %s" % (p["id"], p["type"], winner))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["collect", "run", "judge"])
    ap.add_argument("--pairs", default="pairs.json")
    ap.add_argument("--paths", nargs="*")
    ap.add_argument("--persona", default=os.path.join("plugins", "personality.txt"))
    a = ap.parse_args()

    url = os.environ.get("AB_URL") or "https://opencode.ai/inference/go/openai/v1/chat/completions"
    key = os.environ.get("API_KEY") or os.environ.get("DEEPSEEK_API_KEY") or ""
    model = os.environ.get("AB_MODEL") or "deepseek-v4.1-flash"

    if a.mode == "collect":
        paths = a.paths or [p for g in DEFAULT_GLOBS for p in sorted(__import__("glob").glob(g))]
        picked = collect(paths, a.persona)
        json.dump(picked, open(a.pairs, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        from collections import Counter
        print("样本 %d 条：%s" % (len(picked), dict(Counter(p["type"] for p in picked))))
        return 0

    pairs = json.load(open(a.pairs, encoding="utf-8"))
    if a.mode == "run":
        if not key:
            print("没有 API key（API_KEY / DEEPSEEK_API_KEY）")
            return 2
        res = run(pairs, url, key, model)
        json.dump(res, open(a.pairs, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        leak = sum(1 for p in res if p.get("A_leak"))
        print("完成 %d 对；A 组泄漏 %d 条" % (len(res), leak))
        return 0

    if a.mode == "judge":
        if not key:
            print("没有 API key（API_KEY / DEEPSEEK_API_KEY）")
            return 2
        res = judge(pairs, url, key, model)
        json.dump(res, open(a.pairs, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        from collections import Counter
        print("胜负：", dict(Counter(p["winner"] for p in res)))
        return 0


if __name__ == "__main__":
    sys.exit(main())
