# -*- coding: utf-8 -*-
"""scripts/gen_agent_manual.py —— 从工具注册表生成 `docs/agent_manual.md`（2026-11-03）

执行总表 P1-4：「手册从工具注册表渲染，不许手写」。

用法：

    python scripts/gen_agent_manual.py            # 渲染并写进 docs/agent_manual.md
    python scripts/gen_agent_manual.py --check    # 只比对，不落盘（提交前 / CI 用）

`--check` 不一致时退出码 1 —— 谁加了工具忘了重新生成，提交门槛就能挡住。
"""
import argparse
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from harness import agent_manual as am  # noqa: E402

OUT = os.path.join(ROOT, "docs", "agent_manual.md")


def main() -> int:
    ap = argparse.ArgumentParser(description="从工具注册表渲染 docs/agent_manual.md")
    ap.add_argument("--check", action="store_true",
                    help="只比对不落盘；与仓库里的文件不一致则退出码 1")
    args = ap.parse_args()

    stats = am.collect()
    text = am.render()

    if args.check:
        old = ""
        if os.path.isfile(OUT):
            old = io.open(OUT, encoding="utf-8").read()
        if old == text:
            print(f"手册是最新的（{stats.get('total')} 个工具）")
            return 0
        print("手册已过期：请运行 python scripts/gen_agent_manual.py")
        # 指出第一处差异，省得人对着两份长文件找
        ol, nl = old.splitlines(), text.splitlines()
        for i in range(max(len(ol), len(nl))):
            a = ol[i] if i < len(ol) else "<文件结束>"
            b = nl[i] if i < len(nl) else "<文件结束>"
            if a != b:
                print(f"  第 {i + 1} 行不同：\n    有: {a[:120]}\n    应是: {b[:120]}")
                break
        return 1

    am.write(OUT)
    print(f"已写入 {OUT}（{stats.get('total')} 个工具 / "
          f"{len(stats.get('by_group') or {})} 个分组）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
