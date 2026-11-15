# -*- coding: utf-8 -*-
"""tests/test_personality_save.py —— 人格保存不能吞段落（2026-11-06）

旧版 save_personality 只写前端回传的 sections，没回传的段落会从文件里消失；
section_order 也是硬编码旧版，新段落只能排最后。这里锁三件事：
  · 没传的旧段落必须保留；
  · 传入的段落覆盖同名内容；
  · auto_backup / backup_count 生效，连续保存不互相覆盖备份。
"""
import importlib.util
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PATH = os.path.join(ROOT, "plugins", "ego", "personality_engine", "__init__.py")

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
        FAILURES.append(f"{name} :: {extra}")
        print(f"  [FAIL] {name} :: {extra}")


def load_pe():
    spec = importlib.util.spec_from_file_location("pe_save_test", PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main():
    td = tempfile.mkdtemp(prefix="eb-persona-")
    pe = load_pe()
    pe.DATA_DIR = td
    pe.PERSONALITY_PATH = os.path.join(td, "personality.txt")
    pe.CONFIG_PATH = os.path.join(td, "config.json")
    with open(pe.PERSONALITY_PATH, "w", encoding="utf-8") as f:
        f.write("【你是谁】\n旧人设\n【自主学习成果】\n旧成果\n")

    r1 = pe.save_personality({"【性格】": "新性格"},
                             {"auto_backup": True, "backup_count": 2})
    txt = open(pe.PERSONALITY_PATH, encoding="utf-8").read()
    check("第一次保存成功", r1.get("success"), r1)
    check("旧段落保留", "旧人设" in txt and "旧成果" in txt, txt)
    check("新段落写入", "新性格" in txt, txt)
    check("生成了备份", bool(r1.get("backup")) and os.path.exists(r1["backup"]), r1)

    r2 = pe.save_personality({"【口头禅】": "行"})
    txt2 = open(pe.PERSONALITY_PATH, encoding="utf-8").read()
    check("第二次保存成功", r2.get("success"), r2)
    check("上一次的段落还在", "新性格" in txt2 and "旧人设" in txt2, txt2)
    check("第二次段落写入", "【口头禅】" in txt2 and "行" in txt2, txt2)

    bdir = os.path.join(td, "backups")
    backups = [x for x in os.listdir(bdir) if x.startswith("personality-")]
    check("连续保存不覆盖备份", len(backups) == 2, backups)
    check("备份数量受 backup_count 约束", len(backups) <= 2, backups)

    print("=" * 58)
    if FAIL:
        print(f"失败 {FAIL} 条：")
        for f in FAILURES:
            print("  - " + f)
        return 1
    print(f"通过 {PASS} / 失败 0")
    return 0


if __name__ == "__main__":
    sys.exit(main())
