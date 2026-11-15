# -*- coding: utf-8 -*-
"""tests/test_decider_retry.py —— 决策器「格式异常」不该白重试一次（11-05）

真机 16:08：决策放行 16:08:08 → 插件注入 16:08:41，中间 33 秒。
日志里 `reason: '[AI格式异常]（第2次）·交主模型自裁'` —— 两次调用都吐了
非 JSON。第一次超时窗口 30s、第二次 18s（DECISION_TIMEOUT × 0.6），
光第二次就白等十幾秒。

根因：格式异常是**确定性**失败 —— 同一 prompt、同一 MAX_TOKENS(800)，
长提示词下 12 字段链式 JSON 缺闭合 `}` 被 _parse_json_output 判死，
重试照样截断。超时/空/网络异常才是偶发，值得重试。
（独立 A/B：同网关 opencode_go / 同模型 deepseek-v4.1-flash，短 prompt
 三次都正常输出 JSON，4.2~5.8s、389~473 字 → 不是模型或网关不稳。）

修法：① 格式异常直接降级 fail-open ② MAX_TOKENS 800→1200
      ③ 把 len/brace/head 诊断塞进 _fail_kind（以前只 print 到 stdout，
         bot 的 stdout 只进 webui 协议日志，真机 qq_bot.log 里看不到）。
"""
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from _sandbox import sandbox_neurobase  # noqa: E402

sandbox_neurobase()

PASS, FAIL, FAILURES = 0, 0, []


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK]   {name}")
    else:
        FAIL += 1
        FAILURES.append(name + (f"  → {extra}" if extra else ""))
        print(f"  [FAIL] {name}  {extra}")


def main():
    import plugins.group_chat_decider as D

    print("[A] 重试分类")
    retry = D._should_retry_decision
    check("★ 超时（第1次/共2次）→ 重试", retry("[AI超时]（第1次 / 30s）", 0, 2))
    check("★ 返回空（第1次/共2次）→ 重试", retry("[AI返回空]（第1次）", 0, 2))
    check("★ 网络异常（第1次/共2次）→ 重试", retry("[AI异常]（第1次）超时", 0, 2))
    check("★ 格式异常（第1次/共2次）→ 不重试（省 16s）",
          not retry("[AI格式异常]（第1次·len=800 brace=True/False）", 0, 2))
    check("★ 已经是最后一次 → 任何类型都不重试",
          not retry("[AI超时]（第2次）", 1, 2))
    check("★ attempts=1 时不重试", not retry("[AI超时]（第1次）", 0, 1))
    check("★ 空 fail_kind 不当成格式异常（保守走重试）", retry("", 0, 2))

    print("\n[B] 配置与接线")
    cfg = io.open(os.path.join(ROOT, "plugins", "group_chat_decider", "config.py"),
                  encoding="utf-8", errors="replace").read()
    src = io.open(os.path.join(ROOT, "plugins", "group_chat_decider", "__init__.py"),
                  encoding="utf-8", errors="replace").read()
    check("★ MAX_TOKENS 提到 1200", "MAX_TOKENS = 1200" in cfg)
    check("★ 没靠改小重试次数掩盖（AI_RETRY_MAX 仍是 2）",
          "AI_RETRY_MAX = 2" in cfg)
    check("★ 主循环用了分类函数", "_should_retry_decision(_fail_kind" in src)
    check("★ 格式异常分支直接 break",
          "if not _should_retry_decision(_fail_kind, _attempt, _attempts):" in src
          and "break" in src)
    check("★ 诊断信息进 _fail_kind（len/brace/head）",
          "len={len(_raw)}" in src and "head=" in src and "_diag[:110]" in src)
    check("★ 降级 reason 仍带上 kind（诊断能进 qq_bot.log）",
          'reason = f"{kind}·交主模型自裁"' in src)

    print("\n" + "=" * 60)
    print(f"通过 {PASS} / 失败 {FAIL}")
    if FAILURES:
        print("失败项：")
        for x in FAILURES:
            print("  - " + x)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
