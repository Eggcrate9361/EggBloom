# -*- coding: utf-8 -*-
"""身份档案层单测（people.py，2026-10-30）。

钉死：
  1. QQ 号是主键，名字只是属性（改名不换人）
  2. 别名 > 群名片 > 全局昵称 > 裸号
  3. 同一个人多个号只能由本人明说才关联，且可解绑
  4. scope=private 的事实只在私聊注入
  5. 同群重名能被列出来（模型得靠号区分）

运行：python tests/test_people.py
"""
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

TMP = tempfile.mkdtemp(prefix="people_test_")
import people as pp  # noqa: E402

pp.DATA_DIR = TMP
pp.PEOPLE_FILE = os.path.join(TMP, "people.json")
pp.EXTRA_FILE = os.path.join(TMP, "people_extra.json")

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name if not extra else f"{name}  {extra}")


try:
    # 旧格式底账（sandglass 维护的那份）
    with open(pp.PEOPLE_FILE, "w", encoding="utf-8") as f:
        json.dump({
            "2011257385": {"name": "Texas", "last_seen": 1, "groups": ["829854136"]},
            "3150007406": {"name": "(/ω＼)", "last_seen": 2, "groups": ["829854136"]},
            "9999999999": {"name": "Texas", "last_seen": 3, "groups": ["829854136"]},
        }, f, ensure_ascii=False)
    pp.reload()

    # ── 1. 读 ────────────────────────────────────────────────
    p = pp.get("2011257385")
    check("读到旧账的名字", p["name"] == "Texas", p)
    check("读到群归属", p["groups"] == ["829854136"], p)
    check("未知号返回空档案", pp.get("123")["name"] == "" and pp.get("")["uid"] == "")

    # ── 2. 名字是属性 ────────────────────────────────────────
    pp.note_name("2011257385", "Texas", "829854136")     # 本群名片
    pp.note_name("2011257385", "德州佬")                  # 全局改名
    p = pp.get("2011257385")
    check("全局改名不丢群名片", (p["names"] or {}).get("群:829854136") == "Texas", p["names"])
    check("全局名更新", p["name"] == "德州佬", p["name"])
    check("显示优先群名片", pp.display("2011257385", "829854136") == "Texas",
          pp.display("2011257385", "829854136"))

    pp.set_alias("2011257385", "老铁")
    check("别名压过群名片", pp.display("2011257385", "829854136") == "老铁",
          pp.display("2011257385", "829854136"))
    check("改别名不改 uid", pp.get("2011257385")["uid"] == "2011257385")

    # ── 3. 事实与作用域 ──────────────────────────────────────
    pp.add_fact("2011257385", "城市", "上海")
    pp.add_fact("2011257385", "小秘密", "怕黑", scope="private")
    pp.add_fact("2011257385", "城市", "杭州")            # 同 key 覆盖
    c_priv = pp.card("2011257385", "829854136", private=True)
    c_grp = pp.card("2011257385", "829854136", private=False)
    check("私聊档案含私密事实", "怕黑" in c_priv, c_priv)
    check("群聊档案不含私密事实", "怕黑" not in c_grp, c_grp)
    check("同 key 覆盖不重复", c_grp.count("城市") == 1 and "杭州" in c_grp, c_grp)
    check("档案带 QQ 号（区分同名）", "2011257385" in c_grp, c_grp)
    check("档案带称呼", "老铁" in c_grp, c_grp)

    # ── 4. 多号关联 ─────────────────────────────────────────
    check("关联前不是同一人", not pp.same_person("2011257385", "1112223333"))
    pp.link_account("2011257385", "1112223333")
    check("关联后视为同一人", pp.same_person("2011257385", "1112223333"))
    check("反向也成立", pp.same_person("1112223333", "2011257385"))
    check("归一到主号", pp.canonical("1112223333") == "2011257385",
          pp.canonical("1112223333"))
    check("档案里列出小号", "1112223333" in pp.card("2011257385", private=True),
          pp.card("2011257385", private=True))
    pp.unlink_account("2011257385", "1112223333")
    check("解绑后不再是同一人", not pp.same_person("2011257385", "1112223333"))

    # ── 5. 同群重名 ─────────────────────────────────────────
    hits = pp.same_name_uids("829854136", "Texas")
    check("同群重名能列出来", set(hits) >= {"2011257385", "9999999999"}, hits)
    check("不同名不误报", pp.same_name_uids("829854136", "不存在") == [])

    # ── 6. 空档案不占 token ─────────────────────────────────
    check("陌生号档案为空", pp.card("7777777", "1", private=True) == "",
          pp.card("7777777", "1", private=True))

    # ── 7. 落盘与重载 ───────────────────────────────────────
    pp._EXTRA = None
    pp._BASE = None
    check("重启后别名还在", pp.get("2011257385")["alias"] == "老铁")
    check("重启后事实还在", any(f.get("v") == "杭州" for f in pp.get("2011257385")["facts"]))
finally:
    shutil.rmtree(TMP, ignore_errors=True)

print(f"通过 {len(PASS)} / 失败 {len(FAIL)}")
for n in FAIL:
    print(f"  FAIL  {n}")
sys.exit(1 if FAIL else 0)
