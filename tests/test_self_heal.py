# -*- coding: utf-8 -*-
"""tests/test_self_heal.py —— 自愈 R1/R2（FIXPLAN 1.5.11 §4.5 M2）

验收口径（FIXPLAN）：「人为改坏一个开关 → 它发现并改回，且跑测试」。
这个文件就是那句话的机器版：

  A. 体检：类型不对/超范围要报；缺键不报（DEFAULT_CONFIG 兜底）；白名单外的键不看
  B. 快照：坏配置不存；好配置存；JSON 断了不炸（R2 重建）
  C. 自愈：坏值 → 用快照改回；没快照 → 用默认值；apply=False 不写盘
  D. 安全边界：api_key / target_qqs 这类键**永远不在白名单**
  E. 每次自动修复都落 repairs.jsonl（可事后核对）

全程用假 config 模块 + 临时文件，不动真实 bot_config.json。
"""
import io
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from harness import self_heal as H  # noqa: E402
from harness import trace_tools as TT  # noqa: E402

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


class FakeCfg:
    """假 config 模块：load_config / save_config / DEFAULT_CONFIG。"""

    def __init__(self, cfg):
        self.cfg = dict(cfg)
        self.saved = []
        self.DEFAULT_CONFIG = {
            "eval_enabled": True, "eval_max_chars": 16, "eval_max_parts": 3,
            "eval_max_total": 80, "memory_scope_share": True,
            "private_reply_max_length": 500, "max_tool_rounds": 3,
        }

    def load_config(self):
        return dict(self.cfg)

    def save_config(self, cfg, save_api_key=False):
        self.cfg = dict(cfg)
        self.saved.append(dict(cfg))
        return True


def main():
    tmp = tempfile.mkdtemp(prefix="eb_heal_")
    H.SNAPSHOT_PATH = os.path.join(tmp, "config_snapshot.json")
    TT.REPAIRS_PATH = os.path.join(tmp, "repairs.jsonl")

    print("[A] 体检")
    good = {"eval_enabled": True, "eval_max_chars": 16, "eval_max_parts": 3,
            "eval_max_total": 80, "memory_scope_share": True,
            "private_reply_max_length": 500, "max_tool_rounds": 3}
    check("好配置 → 0 问题", H.validate(good) == [], H.validate(good))
    bad = dict(good, eval_enabled="yes", eval_max_chars=999, max_tool_rounds=0)
    probs = H.validate(bad)
    keys = {p["key"] for p in probs}
    check("抓出类型错", "eval_enabled" in keys, probs)
    check("抓出超上限", "eval_max_chars" in keys, probs)
    check("抓出低于下限", "max_tool_rounds" in keys, probs)
    check("缺键不算坏", H.validate({}) == [], H.validate({}))
    check("白名单外的键不看", H.validate({"api_key": 123, "target_qqs": "x"}) == [])

    print("\n[B] 快照")
    check("坏配置不存快照", H.save_snapshot(bad) is False)
    check("好配置存得下", H.save_snapshot(good) is True)
    check("读得回来", H.load_snapshot() == good, H.load_snapshot())
    io.open(H.SNAPSHOT_PATH, "w", encoding="utf-8").write("{坏掉的 json")
    check("JSON 断了 → None（不炸）", H.load_snapshot() is None)

    print("\n[C] R2：快照重建")
    res = H.heal_snapshot(good)
    check("缺失/损坏 → 重建", res["rebuilt"] is True, res)
    check("重建后读得到", H.load_snapshot() == good)

    print("\n[D] R1：坏值自愈")
    fake = FakeCfg(dict(good, eval_enabled="yes", eval_max_chars=999))
    H._cfg_module = lambda: fake
    H.SNAPSHOT_PATH = os.path.join(tmp, "snap2.json")
    H.save_snapshot(good)
    dry = H.heal(apply=False)
    check("apply=False 只报告", dry["applied"] is False and len(dry["fixed"]) == 2, dry["fixed"])
    check("apply=False 没写盘", fake.saved == [], fake.saved)
    res = H.heal(apply=True)
    check("两项都修了", len(res["fixed"]) == 2 and res["applied"] is True, res)
    check("从快照取的值是对的",
          fake.cfg["eval_enabled"] is True and fake.cfg["eval_max_chars"] == 16, fake.cfg)
    check("写盘一次", len(fake.saved) == 1, fake.saved)

    print("\n[E] 没快照 → 用默认值")
    H.SNAPSHOT_PATH = os.path.join(tmp, "no_such_snap.json")
    fake2 = FakeCfg(dict(good, eval_max_total=9999))
    H._cfg_module = lambda: fake2
    res = H.heal(apply=True)
    check("用 DEFAULT_CONFIG 修好", fake2.cfg["eval_max_total"] == 80, fake2.cfg)
    check("记了 fixed", res["fixed"][0]["key"] == "eval_max_total", res["fixed"])

    print("\n[F] repairs 台账")
    rows = TT.list_repairs(hours=24)
    check("自动修复写了台账", len(rows) >= 2, rows)
    check("kind 是 config", any(r["kind"] == "config" for r in rows), rows[:2])
    check("台账里能看到改了哪个键",
          any("eval_max_chars" in str(r.get("detail", "")) for r in rows), rows[:2])

    print("\n[G] 安全边界")
    for danger in ("api_key", "target_qqs", "group_ids", "admin_qqs"):
        check(f"{danger} 不在白名单", danger not in H.WATCHED)

    print("\n[H] startup 一条龙")
    H.SNAPSHOT_PATH = os.path.join(tmp, "snap3.json")
    fake3 = FakeCfg(dict(good, memory_scope_share="false"))
    H._cfg_module = lambda: fake3
    out = H.startup()
    check("startup 修了坏键", fake3.cfg["memory_scope_share"] is True, fake3.cfg)
    check("startup 建了快照", H.load_snapshot() is not None)

    print("\n" + "=" * 56)
    print(f"通过 {PASS} / 失败 {FAIL}")
    if FAILURES:
        print("失败项：")
        for f in FAILURES:
            print("  -", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
