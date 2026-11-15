# -*- coding: utf-8 -*-
"""group_send_max_parts：一个键只能有一个值。

2026-10-24 修之前，这个键有三处不一致：

    bot_config.json.example   5
    bot_core.py 兜底          3
    config.DEFAULT_CONFIG     （没有这个键）

用户复制 example 得 5，不复制得 3，而配置页上根本没有这一项 ——
他怎么调都不知道。同一件事三个数，是这仓库最高频的 bug 形态
（骂人尺度 3 处、批次关闭判据 2 处、延迟计算 2 处，都是它）。

另外它此前不在 _RELOADABLE_GLOBALS 里，而同功能域的 msg_split_len
却能热重载。改完配置不生效，等于没有入口。

这个文件锁四件事：

  [1] 三处（DEFAULT_CONFIG / example / 代码兜底）必须是同一个值
  [2] 热重载真的刷新它
  [3] v3 三处一致：标记 / 填充 / 收集（否则保存会把字段重置）
  [4] 超上限时的合并行为仍按这个值走
"""
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import config  # noqa: E402

_fail = []


def check(name, ok, detail=""):
    if ok:
        print(f"  [OK]   {name}")
    else:
        print(f"  [FAIL] {name}  {detail}")
        _fail.append(name)


def main():
    KEY = "group_send_max_parts"

    print("[1] 三处同一个值")
    dft = config.DEFAULT_CONFIG.get(KEY)
    check("DEFAULT_CONFIG 有这个键", dft is not None, dft)
    ex = io.open(os.path.join(ROOT, "bot_config.json.example"),
                 encoding="utf-8-sig").read()
    m = re.search(r'"%s"\s*:\s*(\d+)' % KEY, ex)
    check("example 有这个键", m is not None)
    ex_v = int(m.group(1)) if m else None
    check(f"example 与 DEFAULT_CONFIG 相同（{dft}）", ex_v == dft, (ex_v, dft))
    bc = io.open(os.path.join(ROOT, "bot_core.py"), encoding="utf-8").read()
    m2 = re.search(r'GROUP_SEND_MAX_PARTS\s*=\s*_config\.get\("%s",\s*(\d+)\)' % KEY, bc)
    check("bot_core 兜底有这个键", m2 is not None)
    code_v = int(m2.group(1)) if m2 else None
    check(f"bot_core 兜底与 DEFAULT_CONFIG 相同（{dft}）", code_v == dft, (code_v, dft))
    # 5 是 1.5.7 报告里写的设计意图，不能悄悄改回去
    check("值为 5（1.5.7 设计意图）", dft == 5, dft)

    print("\n[2] 热重载真的刷新它")
    check("GROUP_SEND_MAX_PARTS 在 _RELOADABLE_GLOBALS 里",
          re.search(r'_RELOADABLE_GLOBALS\s*=\s*\[.*?"GROUP_SEND_MAX_PARTS".*?\]',
                    bc, re.S) is not None)
    check("reload_config 的映射表里有它",
          '"GROUP_SEND_MAX_PARTS": "%s"' % KEY in bc)
    # 同功能域的另一个键也该在，否则又是"一个能改一个不能改"
    check("GROUP_SEND_BLOCK_THRESH 也在热重载列表里",
          re.search(r'_RELOADABLE_GLOBALS\s*=\s*\[.*?"GROUP_SEND_BLOCK_THRESHOLD".*?\]',
                    bc, re.S) is not None)
    check("block_threshold 兜底与 DEFAULT_CONFIG 相同",
          config.DEFAULT_CONFIG.get("group_send_block_threshold") ==
          int(re.search(r'GROUP_SEND_BLOCK_THRESHOLD\s*=\s*_config\.get\("group_send_block_threshold",\s*(\d+)\)',
                        bc).group(1)))

    print("\n[3] v3 三处一致")
    v3 = io.open(os.path.join(ROOT, "webui", "webui_artistic_v3.html"),
                 encoding="utf-8").read()
    for fid, key in (("sGroupSendMaxParts", KEY),
                     ("sGroupSendBlockThreshold", "group_send_block_threshold")):
        n_mark = v3.count('id="%s"' % fid)
        n_fill = v3.count("s('%s'" % fid)
        n_coll = v3.count("%s:" % key)
        check(f"{fid} 标记/填充/收集各一处",
              n_mark == 1 and n_fill == 1 and n_coll >= 1,
              (n_mark, n_fill, n_coll))

    print("\n[4] 超上限时的合并行为")
    # 逻辑：超过上限就把尾部并成一条，总数正好等于上限
    check("合并后总数等于上限（不是上限-1）",
          "GROUP_SEND_MAX_PARTS - 1" in bc and "_keep_l + [_rest_l]" in bc)
    check("合并发生在发送之前（上限对硬切碎片也生效）",
          "拆分之后、发送之前" in bc)

    print("\n" + "=" * 56)
    if _fail:
        print(f"失败 {len(_fail)}：{_fail}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())