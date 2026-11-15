# -*- coding: utf-8 -*-
"""人格文件路径：读侧统一了，写侧漏了三处（2026-10-27 核查报告第 2 项）

## 症状

- `self_learning.backup()` 返回 False、`apply_update()` 抛 FileNotFoundError
  → **人格自学习写回整条链是死的**
- `bot_identity.bot_names()` 返回空集 → 认不出「这条是不是在叫机器人」
- 蒸馏发布读不到手写人设 → "要保留的硬规矩"那几段会丢

## 根因

读侧早已统一到 `plugins/personality.txt`（`bot_core.PERSONA_CANONICAL`），
但**写侧三处**仍指向仓库根 `personality.txt` —— 那个文件不存在：

| 位置 | 原来指向 |
|---|---|
| `self_learning.py:25` | `<app>/personality.txt` |
| `plugins/这样脆弱的东西/publisher.py:45` | `<app>/personality.txt` |
| `plugins/这样脆弱的东西/bot_identity.py:37` | `<app>/personality.txt` |

第二层：`bot_identity._NAME_RE` 只认「你是X」，
而 personality.txt 现在写的是「**我叫**小a，……」→ 正则永远匹配不到。

## 契约

1. 三处写侧都指向 `plugins/personality.txt`
2. 该文件**真实存在**（不是路径对但文件缺）
3. `bot_names()` 能从 personality.txt 读出机器人自称
4. 人格文件里的名字句式（你是X / 我叫X）都能解析

运行：python tests/test_persona_paths.py
"""
import io
import os
import re
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


CANON = os.path.join(ROOT, "plugins", "personality.txt")


def test_canonical_exists():
    """权威人格文件必须真实存在。"""
    check("plugins/personality.txt 存在", os.path.isfile(CANON), CANON)
    if os.path.isfile(CANON):
        body = io.open(CANON, encoding="utf-8").read()
        check("内容非空", len(body.strip()) > 50, len(body))
        check("有【我是谁】段", "【我是谁】" in body, body[:60])


def test_self_learning_paths():
    """写回侧：备份与更新都指向权威文件。"""
    import self_learning as sl
    check("PERSONALITY_FILE 指向 plugins/personality.txt",
          os.path.normpath(sl.PERSONALITY_FILE) == os.path.normpath(CANON),
          sl.PERSONALITY_FILE)
    check("该文件存在（backup 才有意义）", os.path.isfile(sl.PERSONALITY_FILE),
          sl.PERSONALITY_FILE)
    # 备份目录不能落在仓库根（那是运行态目录，不该进仓库）
    check("备份目录不在仓库根",
          "personality_versions" not in os.path.dirname(sl.PERSONALITY_FILE).split(ROOT)[-1]
          or os.path.join(ROOT, "personality_versions") != sl.PERSONALITY_BACKUP_DIR,
          sl.PERSONALITY_BACKUP_DIR)


def test_backup_actually_works():
    """★ 真跑一次 backup —— 原来它恒返回 False。"""
    import self_learning as sl
    pl = sl.PersonaLearner() if hasattr(sl, "PersonaLearner") else None
    if pl is None:
        # 找到持有 backup() 的那个类
        for _n in dir(sl):
            _o = getattr(sl, _n)
            if isinstance(_o, type) and hasattr(_o, "backup"):
                pl = _o(); break
    if pl is None:
        check("找到带 backup() 的类", False, dir(sl)[:20])
        return
    ok = pl.backup()
    check("backup() 返回 True", ok is True, ok)
    if ok:
        # 备份目录里应该有刚写的文件
        try:
            files = os.listdir(sl.PERSONALITY_BACKUP_DIR)
        except OSError:
            files = []
        check("备份文件落盘了", bool(files), sl.PERSONALITY_BACKUP_DIR)


def test_publisher_path():
    """蒸馏发布侧也要指向权威文件。"""
    src = io.open(os.path.join(ROOT, "plugins", "这样脆弱的东西", "publisher.py"),
                  encoding="utf-8").read()
    check("publisher 用 plugins/personality.txt",
          '"plugins", "personality.txt"' in src
          or "'plugins', 'personality.txt'" in src,
          [ln for ln in src.split("\n") if "personality.txt" in ln][:3])


def test_bot_identity_path():
    """bot_identity 侧：路径 + 句式。"""
    p = os.path.join(ROOT, "plugins", "这样脆弱的东西", "bot_identity.py")
    src = io.open(p, encoding="utf-8").read()
    check("bot_identity 用 plugins/personality.txt",
          '"plugins", "personality.txt"' in src,
          [ln for ln in src.split("\n") if "personality.txt" in ln][:3])

    # 直接测正则：人设文件现在写的是「我叫X」
    import importlib.util
    spec = importlib.util.spec_from_file_location("bi_mod", p)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception as e:
        check("bot_identity 能导入", False, e)
        return
    head = io.open(CANON, encoding="utf-8").read()[:400] if os.path.isfile(CANON) else ""
    m = mod._NAME_RE.search(head)
    check("能从人设里读出名字", bool(m), head[:80])
    if m:
        check("读到的是「小a」", m.group(1) == "小a", m.group(1))
    check("「你是X」句式也认",
          bool(mod._NAME_RE.search("你是小a，一个程序")), "不认")


def test_no_stale_root_reference():
    """所有"权威路径"常量必须解析到同一个文件。

    ★ 不能靠字符串判断 —— 同一份文件可以写成
      os.path.join(dirname(dirname(X)), "personality.txt")
      os.path.join(X, "..", "personality.txt")
      "plugins/personality.txt"
      三者拼出来是同一个路径。判据只能是**解析后的绝对路径**。
    """
    import self_learning as sl
    import bot_core as bc

    canon = os.path.normpath(bc.PERSONA_CANONICAL)
    check("bot_core.PERSONA_CANONICAL == plugins/personality.txt",
          canon == os.path.normpath(CANON), canon)
    check("self_learning.PERSONALITY_FILE == 权威路径",
          os.path.normpath(sl.PERSONALITY_FILE) == canon,
          sl.PERSONALITY_FILE)
    # 旧包兜底必须**显式**标成 legacy，不能再叫 PERSONALITY_FILE
    # （那个名字看着像权威路径，写侧照着它写就全废了）
    check("旧包兜底常量叫 PERSONALITY_LEGACY_ROOT",
          hasattr(bc, "PERSONALITY_LEGACY_ROOT")
          and not hasattr(bc, "PERSONALITY_FILE"),
          [a for a in dir(bc) if "PERSONALITY" in a])
    # 兜底链不许再翻程序目录的父目录去找人格文件
    #   （<app>/../personality.txt 那种 —— 既没意义也不安全）
    #   注：EGO 的 mtime 探测里有一处 "..", ".."，它拼出的是
    #   <app>/plugins/personality.txt（ego/ 往上两层正是 plugins/），是对的。
    #   判据直接看 search_paths 列表本身，不去数全文里的 personality.txt
    #   —— 注释里提到它是正常的，列表项数才是契约。
    src = io.open(os.path.join(ROOT, "bot_core.py"), encoding="utf-8").read()
    _i = src.find("search_paths = [")
    _j = src.find("]", _i) if _i >= 0 else -1
    _lst = src[_i:_j] if (_i >= 0 and _j > _i) else ""
    check("兜底链只有两项（权威路径 + 旧包根目录）",
          _lst.count(",") == 2 and ".." not in _lst,
          repr(_lst))


def main():
    print("=" * 58)
    print("人格文件路径")
    print("=" * 58)
    for fn in (test_canonical_exists,
               test_self_learning_paths,
               test_backup_actually_works,
               test_publisher_path,
               test_bot_identity_path,
               test_no_stale_root_reference):
        print(f"\n[{fn.__name__}]")
        fn()
    print("\n" + "=" * 58)
    if _FAIL:
        print(f"失败 {len(_FAIL)}：{_FAIL}")
        sys.exit(1)
    print("全部通过")


if __name__ == "__main__":
    main()