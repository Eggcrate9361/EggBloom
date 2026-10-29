# -*- coding: utf-8 -*-
"""harness/persona_cards 单测（批次 I1）。

覆盖：首次播种（小a + 大肥鱼）、目录已存在不复活、同名覆盖更新、
删除、apply 走注入的 save_fn（唯一写回口）、应用前自动备份、失败传播。
不测 HTTP 路由（bot_core 薄路由只做转发，与 /api/agent 同惯例）。
"""
import os
import sys
import json
import shutil
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from harness import persona_cards as pc  # noqa: E402

PASS = []
FAIL = []


def check(name, cond):
    (PASS if cond else FAIL).append(name)


# ── 隔离卡目录 ──────────────────────────────────────────────────────
_tmp = tempfile.mkdtemp(prefix="pcards_")
pc._CARDS_DIR = os.path.join(_tmp, "persona_cards")
pc._BACKUP_DIR = os.path.join(pc._CARDS_DIR, "_backups")


def _fake_sections():
    return {"【我是谁】": "测试人格 A。", "【怎么说话】": "短句。"}


_tmp2 = None

try:
    # ── 1. 首次播种：注入 seed_fn → 小a + 大肥鱼 ──────────────────
    born = pc.seed_if_empty(_fake_sections)
    check("播种含小a", "小a" in born)
    check("播种含大肥鱼", "大肥鱼" in born)
    lst = pc.list_cards()
    check("列表两条", len(lst) == 2)
    names = {c["name"] for c in lst}
    check("列表名正确", names == {"小a", "大肥鱼"})
    orig = [c for c in lst if c["name"] == "小a"][0]
    got = pc.get_card(orig["id"])
    check("小a 内容=注入内容", got["sections"].get("【我是谁】") == "测试人格 A。")
    fish = [c for c in lst if c["name"] == "大肥鱼"][0]
    fishc = pc.get_card(fish["id"])
    check("大肥鱼 ≥6 段", len(fishc["sections"]) >= 6)
    check("大肥鱼含【爱吃】", "【爱吃】" in fishc["sections"])
    check("摘要不含 sections JSON", '"sections":' not in json.dumps(lst))

    # ── 2. 目录已存在 → 不再播种（删光也不复活）─────────────────────
    born2 = pc.seed_if_empty(_fake_sections)
    check("第二次播种为空", born2 == [])
    pc.delete_card(orig["id"])
    check("删除生效", pc.get_card(orig["id"]) is None)
    born3 = pc.seed_if_empty(_fake_sections)
    check("删光后不复活小a", "小a" not in born3)

    # ── 2b. 旧名字迁移：「原小a」→「小a」（2026-10-29 定名）────────
    _legacy = pc.save_card("原小a", "旧名卡", {"【旧】": "x"})
    check("造出旧名卡", pc.get_card(_legacy["id"])["name"] == "原小a")
    _mig = pc.migrate_legacy_names()
    check("迁移返回 True", _mig is True)
    check("旧名已改为小a", pc.get_card(_legacy["id"])["name"] == "小a")
    check("再迁移一次无效（幂等）", pc.migrate_legacy_names() is False)
    pc.delete_card(_legacy["id"])       # 清掉，避免干扰后面的同名统计

    # ── 3. 同名覆盖更新：存两张同名 → 仍一张且内容更新 ───────────────
    pc.save_card("测试B", "第一条", {"【x】": "v1"})
    rec = pc.save_card("测试B", "第二条", {"【x】": "v2"})
    lst2 = [c for c in pc.list_cards() if c["name"] == "测试B"]
    check("同名只有一张", len(lst2) == 1)
    got2 = pc.get_card(rec["id"])
    check("同名更新到 v2", got2["sections"] == {"【x】": "v2"} and got2.get("note") == "第二条")
    # created 保留、updated 更新
    check("created 保留", got2.get("created", 0) > 0)

    # ── 4. apply：唯一写回口 = 注入的 save_fn ────────────────────────
    calls = []

    def _ok_save(sections):
        calls.append(dict(sections))
        return {"success": True}

    def _raw_fn():
        return "【旧】\n旧人格原文"

    r = pc.apply_card(rec["id"], _ok_save, _raw_fn)
    check("apply ok", r.get("ok") is True)
    check("apply 版本传的是 sections", calls and calls[0] == {"【x】": "v2"})
    # 自动备份：写入了 _backups 且含原文
    bks = [f for f in os.listdir(pc._BACKUP_DIR) if f.endswith(".json")] \
        if os.path.isdir(pc._BACKUP_DIR) else []
    check("应用前自动备份存在", len(bks) >= 1)
    if bks:
        bk = json.load(open(os.path.join(pc._BACKUP_DIR, bks[0]), encoding="utf-8"))
        check("备份含当前原文", "旧人格原文" in (bk.get("raw") or ""))
        check("备份记了目标卡", bk.get("before_card") == rec["id"])

    # ── 5. apply 失败传播 + 未知卡 + 免注入 ─────────────────────────
    def _bad_save(sections):
        return {"success": False, "error": "磁盘炸了"}

    r2 = pc.apply_card(rec["id"], _bad_save, _raw_fn)
    check("失败如实传播", r2.get("ok") is False and "磁盘" in (r2["result"].get("error") or ""))
    r3 = pc.apply_card("card-nope", _ok_save, _raw_fn)
    check("未知卡 → 拒绝", r3.get("ok") is False)
    r4 = pc.apply_card(rec["id"], None, None)
    check("未注入写入口 → 拒绝（不旁写）", r4.get("ok") is False
          and "未注入" in (r4["result"].get("error") or ""))

    # ── 6. save_card 防御：非 dict sections 不炸 ────────────────────
    r5 = pc.save_card("防御卡", "", None)
    check("None sections 存成空卡", pc.get_card(r5["id"])["sections"] == {})

    # ── 7. seed_fn 抛异常 → 只有大肥鱼 ───────────────────────────────
    _tmp2 = tempfile.mkdtemp(prefix="pcards2_")
    pc._CARDS_DIR = os.path.join(_tmp2, "persona_cards")
    pc._BACKUP_DIR = os.path.join(pc._CARDS_DIR, "_backups")

    def _boom():
        raise RuntimeError("读不到人格")

    born4 = pc.seed_if_empty(_boom)
    check("seed_fn 失败只播大肥鱼", born4 == ["大肥鱼"])

finally:
    shutil.rmtree(_tmp, ignore_errors=True)
    if _tmp2:
        shutil.rmtree(_tmp2, ignore_errors=True)

print(f"通过 {len(PASS)} / 失败 {len(FAIL)}")
for n in FAIL:
    print(f"  FAIL  {n}")
sys.exit(1 if FAIL else 0)
