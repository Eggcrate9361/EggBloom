# -*- coding: utf-8 -*-
"""人格只能有一个来源：plugins/personality.txt（2026-10-27）

## 事实

`load_personality()` 是「EGO 优先，文件兜底」。EGO 的 `PERSONALITY_PATH` 指向
`plugins/personality.txt`，配置页的人格编辑器、蒸馏人格卡也都写这一份 ——
所以**真正生效的从来只有它**。

问题在兜底链：它把**根目录** `personality.txt` 排在第一，而根目录那份是历史遗留，
内容停在 1.5.8 之前：

    根目录 1328B（440 字）  【你是谁】你是小a，18 岁的网瘾青年…
    plugins 3853B（1350 字）【我是谁】我叫小a，一个在群里混的网友…

两份是**不同代**的人格。后果：EGO 一旦不可用（异常 / 被摘掉 / 文件缺失），
人格会悄悄退回旧版，语气说变就变，日志里一个提示都没有。

## 契约

1. 兜底链第一项必须是 `plugins/personality.txt`（与 EGO 同一个文件）
2. 根目录那份只作历史兼容，排在后面
3. 版本库里**只能有一份**被跟踪的人格文件（否则迟早又分叉）

运行：python tests/test_persona_single_source.py
"""
import io
import os
import re
import subprocess
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


def main():
    src = io.open(os.path.join(ROOT, "bot_core.py"), encoding="utf-8").read()

    print("[1] 常量：规范路径指向 plugins/personality.txt")
    m = re.search(r'PERSONA_CANONICAL\s*=\s*os\.path\.join\(\s*SCRIPT_DIR,\s*"plugins",\s*"personality\.txt"\s*\)', src)
    check("PERSONA_CANONICAL 已定义且指向 plugins/", m is not None)

    print("\n[2] 兜底链顺序：plugins 那份必须排在根目录之前")
    seg_start = src.find("def load_personality")
    check("找到 load_personality", seg_start > 0)
    seg = src[seg_start:seg_start + 6000]
    i_canon = seg.find("PERSONA_CANONICAL,")
    i_root = seg.find("PERSONALITY_FILE,")
    check("search_paths 里用到了 PERSONA_CANONICAL", i_canon > 0, f"@ {i_canon}")
    check("search_paths 里还留着 PERSONALITY_FILE（历史兼容）", i_root > 0, f"@ {i_root}")
    check("★ 规范路径排在根目录之前", 0 < i_canon < i_root, f"canon@{i_canon} root@{i_root}")
    # 列表第一项必须就是它 —— 排在 PERSONALITY_FILE 前面还不够，得是首个候选
    check("★ 它是 search_paths 的第一项",
          re.search(r"search_paths\s*=\s*\[\s*\n?\s*PERSONA_CANONICAL,", seg) is not None)

    print("\n[3] 版本库里只有一份被跟踪的人格文件")
    try:
        out = subprocess.run(["git", "ls-files"], capture_output=True, text=True,
                             cwd=ROOT, timeout=20).stdout
        tracked = [x for x in out.splitlines()
                   if os.path.basename(x) in ("personality.txt", "personality.md")]
        check("★ 恰好一份", len(tracked) == 1, tracked)
        check("★ 是 plugins/personality.txt", tracked and tracked[0].replace("\\", "/") == "plugins/personality.txt",
              tracked)
    except Exception as e:
        check("能读到 git ls-files", False, str(e))

    print("\n[4] 运行时若同时存在两份，命中哪一份")
    # 用源码判据代替真跑（load_personality 依赖模块级常量，不好注入临时目录）
    check("两份同名文件不会再各读各的（顺序已固定）", 0 < i_canon < i_root)

    print("\n" + "=" * 56)
    if _FAIL:
        print(f"失败 {len(_FAIL)}：{_FAIL}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
