# -*- coding: utf-8 -*-
"""框架指令识别（2026-10-25 新增）。

线上事故（群 829854136，11:46）：

    碱式碳酸大脑 11:46:49  #sl
    SnowLuma     11:46:50  SnowLuma 状态 / 版本 1.14.10 / 平台 win32-x64 …

框架自己把 `#sl` 当命令处理并回了状态卡；Bot 完全不认识这个词，
却仍把它当聊天走了完整流程 —— 先花一次搜索查"#sl 是什么意思"，
再答「#sl是啥指令 / 我这边没这功能，你冲我喊也没用」。
群里对同一件事出现两条回复，后一条是错的。

判据不能写成"以 # 开头" —— `#每日一癫`、`#今日份快乐` 是真在跟人说话。
真正的形态是 **分隔符 + 纯 ASCII 单词 + 整条就这一个词**，
这与"话题标签"的区别是后者会带中文或空格。

运行：python tests/test_framework_command.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import bot_core  # noqa: E402

_fail = []


def check(name, ok, detail=""):
    if ok:
        print(f"  [OK]   {name}")
    else:
        print(f"  [FAIL] {name}  {detail}")
        _fail.append(name)


def main():
    P = bot_core._is_framework_command

    print("[1] 框架指令要认出来")
    for s in ("#sl", "/help", "!ping", "#status", "/st", "#SL", "#sl2",
              "  #sl  ", "/nsfw", "！help", "．help"):
        check(f"{s!r} 判为框架指令", P(s) is True, P(s))

    print("\n[2] 真聊天不能误伤（这是最主要的失败模式）")
    for s in ("#每日一癫", "#今日份快乐", "#sl 这是啥", "看这个 #sl",
              "/ω＼", "好", "在吗", "", "   ", "/", "#", "#!",
              "帮我看看 #sl 是什么意思"):
        check(f"{s!r} 不判为框架指令", P(s) is False, P(s))

    print("\n[3] 关键判据：中文一律不算")
    # 话题标签是中文用户的高频用法，AI 把它当命令拦掉 = 整段对话消失
    for s in ("#南京", "#考研", "#原神启动", "/今日", "!今天吃啥"):
        check(f"{s!r} 带中文 → 放行给 AI", P(s) is False, P(s))

    print("\n[4] 过长的不算（长度上限防误伤整句）")
    check("24 字以内的才能拦", P("/" + "a" * 20) is True, P("/" + "a" * 20))
    check("超长不放行拦截", P("/" + "a" * 30) is False, P("/" + "a" * 30))

    print("\n[5] 函数被真正接进主链（防止写了没人用）")
    src = open(os.path.join(ROOT, "bot_core.py"), encoding="utf-8").read()
    check("主链调用了 _is_framework_command",
          src.count("_is_framework_command(") >= 2, src.count("_is_framework_command("))
    check("调用点在群消息入口（同名文本日志之后）",
          src.find("_is_framework_command(text)") > src.find("has_xiao_a={has_xiao_a}"))
    # 拦截必须 continue 而不是 return：上下文已经记进 RECENT_MSGS/TIMELINE，
    # return 只是不回复，但 continue 能让后续消息的"近况"里仍有这条。
    seg = src[src.find("_is_framework_command(text)"):][:400]
    check("拦截后 continue（保留上下文，只不回复）", "continue" in seg, seg[:120])

    print("\n[6] 三个位置的前后次序（挪位就失效，比内容更容易被改坏）")
    _i_log = src.find("has_xiao_a={has_xiao_a}")          # 文本日志
    _i_ctx = src.find("GROUP_RECENT_MSGS[group_id].append")  # 上下文入库
    _i_cmd = src.find("_is_framework_command(text)")      # 拦截
    _i_buf = src.find("GROUP_MSG_BUFFER[group_id] = {", _i_cmd)  # 建缓冲区
    check("上下文入库早于拦截（近况里还能看到这条）",
          0 < _i_ctx < _i_cmd, f"ctx={_i_ctx} cmd={_i_cmd}")
    check("★ 拦截早于建缓冲区（否则 continue 白写，照样进流程）",
          0 < _i_cmd < _i_buf, f"cmd={_i_cmd} buf={_i_buf}")
    check("文本日志早于拦截（日志里留得下这条消息）",
          0 < _i_log < _i_cmd, f"log={_i_log} cmd={_i_cmd}")

    print("\n" + "=" * 56)
    if _fail:
        print(f"失败 {len(_fail)}：{_fail}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())