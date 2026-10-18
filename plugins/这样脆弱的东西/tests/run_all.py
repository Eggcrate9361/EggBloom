# -*- coding: utf-8 -*-
"""回归测试入口：跑全部自测套件，任一失败则退出码非零。

用法（在插件目录）：
    python tests/run_all.py
或通过 CLI：
    python cli.py test

设计说明：每个套件在**独立子进程**里跑 —— 它们各自注入 sys.modules 的
persona_distill 包，互相不能污染；子进程隔离也让崩溃不至于拖垮整批。
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable

SUITES = (
    "ledger_selftest.py",   # 账本状态机：沉默≠否定 / 反证才推翻 / 编号终身稳定
    "mode_test.py",         # 模式判定：增量/全量/跳过
    "wiring_test.py",       # 接线：蒸馏→审核→落账→渲染 全链路（隔离 target）
)


def main():
    failures = []
    for name in SUITES:
        path = os.path.join(HERE, name)
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
        print(f"—— {name} " + "—" * max(1, 50 - len(name)))
        proc = subprocess.run([PY, path], env=env, cwd=HERE,
                              capture_output=True, text=True,
                              encoding="utf-8", errors="replace")
        tail = (proc.stdout or "").strip().splitlines()[-2:]
        for line in tail:
            print("   " + line)
        if proc.returncode != 0:
            failures.append(name)
            err = (proc.stderr or "").strip().splitlines()[-6:]
            print("   ✗ 失败，stderr 末尾：")
            for line in err:
                print("     " + line)
    print()
    if failures:
        print(f"结果：{len(SUITES) - len(failures)}/{len(SUITES)} 通过，"
              f"失败：{failures}")
        return 1
    print(f"结果：全部 {len(SUITES)} 个套件通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
