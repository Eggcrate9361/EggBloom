# -*- coding: utf-8 -*-
"""H3：生成 docs/separation-keys.md（分离与真实 §5 白名单 × 现仓库读取点）。

只读审计：键名 → 读取点（文件:行） → 分组 → 分离后可否旁路。
分组的依据是"这个键在拦什么"：
  闸     —— 决定"回不回"（发言闸/决策器/话头翻旧）
  量     —— 决定"回多长/几条/多久一次"
  规则   —— 决定"怎么说话"（de_ai / persona_*）
  形式   —— 决定"什么时候说/怎么呈现"（延迟/静默/退场）
"""
import io
import os
import re

ROOT = r"D:\git\EggBloom"

KEYS = {
    "闸": ["reply_question_guard", "reply_stale_turns", "reply_stale_drop_sec",
           "speak_gate_tier", "speak_probability", "speak_rate_max_per_100",
           "speak_rate_window_min", "consecutive_reply_limit", "decider_min_score"],
    "量": ["reply_max_chars", "reply_max_parts", "reply_max_total", "reply_long_ratio",
           "private_reply_interval", "group_session_max_replies",
           "group_msg_buffer_window", "group_msg_buffer_window_session",
           "group_msg_buffer_window_trigger"],
    "规则": ["persona_sharpness", "persona_curiosity", "persona_warmth",
             "persona_talkativeness", "de_ai.enabled", "de_ai.prompt_inject",
             "de_ai.post_process", "de_ai.ban_words", "de_ai.ban_dashes",
             "de_ai.ban_rule_of_three", "de_ai.ban_filler", "de_ai.ban_sycophancy",
             "de_ai.max_sentence_len"],
    "形式": ["quiet_hours", "group_idle_exit_enable", "group_idle_exit_probability",
             "group_idle_exit_min_silence", "group_last_exit_cooldown",   # 设计稿写作 group_idle_exit_cooldown；代码里是这个名字
             "group_last_exit_cooldown", "reply_reaction_delay",
             "private_reply_reaction_delay"],
}

SKIP_DIRS = (".git", "data", "__pycache__", "_fontwalk", "node_modules")
SKIP_FILES = {"config.py"}


def _read_points(key: str):
    """返回 [(相对路径, 行号, 行内容)]，只认真的读取形态。"""
    base = key.split(".")[-1]
    pats = [f'get("{base}"', f"get('{base}'", f'["{base}"]', f"['{base}']",
            f'("{base}"', f"('{base}'"]
    hits = []
    for root, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for f in files:
            if not f.endswith(".py") or f in SKIP_FILES:
                continue
            p = os.path.join(root, f)
            try:
                lines = io.open(p, encoding="utf-8", errors="replace").read().splitlines()
            except OSError:
                continue
            for i, line in enumerate(lines, 1):
                if any(pt in line for pt in pats):
                    hits.append((os.path.relpath(p, ROOT).replace("\\", "/"), i, line.strip()))
    return hits


def main():
    rows = []
    missing = []
    for group, keys in KEYS.items():
        for key in keys:
            hits = _read_points(key)
            if not hits:
                missing.append(key)
                rows.append((group, key, "—", "**无读取点（待定稿）**", "可旁路"))
                continue
            first = hits[0]
            extra = f"（共 {len(hits)} 处）" if len(hits) > 1 else ""
            rows.append((group, key, f"`{first[0]}:{first[1]}`{extra}", "可旁路", "可旁路"))

    out = ["# 分离键清单（H3）", "",
           "> 来源：`分离与真实-设计.md` §5 白名单 × 2026-10-29 现仓库静态核对。",
           "> 用途：1.6.0 分离启用脚本收尾 grep 闸的核对数据；**只读审计，不改行为**。",
           "> 「分离后可否旁路」= 这些键在 separation 态下可以由 Null* 替身接管/删除，",
           "> 因此启用前必须能被枚举——否则脚本会漏删，留下“人形”残影。", "",
           "| 分组 | 键 | 读取点（第一个） | 分离后可否旁路 | 备注 |",
           "|---|---|---|---|---|"]
    for g, k, pt, bypass, note in rows:
        out.append(f"| {g} | `{k}` | {pt} | {bypass} | {note} |")
    out += ["", f"合计 {len(rows)} 个键；无读取点 {len(missing)} 个"
            + (f"：{', '.join(missing)}" if missing else "。"), ""]

    p = os.path.join(ROOT, "docs", "separation-keys.md")
    io.open(p, "w", encoding="utf-8").write("\n".join(out))
    print(f"wrote {p}: {len(rows)} keys, missing={missing}")


if __name__ == "__main__":
    main()
