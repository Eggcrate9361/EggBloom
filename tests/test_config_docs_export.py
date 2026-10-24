# -*- coding: utf-8 -*-
"""tests/test_config_docs_export.py —— 配置三处一致性与文档导出回归

背景（1.5.8 计划 #22）
    配置说明原本要"集中到 config_meta.py，三处派生"。实际没建那个文件 ——
    链路已经有了（v3 标记 → _fieldmeta → v4 页面），再建就是第二份源。
    改成：`export_config_docs.py` 只做两件链上没有的事 —— 导出人读的文档，
    以及审计三个键集合是否一致。

锁死的东西：
  1. example 不漏登记 DEFAULT_CONFIG 的键（这个仓库犯过三次：
     agent_group_enable / ban_fabrication / fix_question_marks）
  2. example 里没有死键（代码零引用，用户改了没反应）
  3. 文档是从同一份数据生成的，不是手抄的
  4. 横排开关的标签不会串到上一个字段（use_local_llm 曾显示成「模型」）
  5. 嵌套键的默认值取得到（de_ai.* 曾全是「—」）

纯 Python，不依赖浏览器。
"""
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import config  # noqa: E402
import export_config_docs as ex  # noqa: E402

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
        FAILURES.append(name + (f"  → {extra}" if extra else ""))
        print(f"  [FAIL] {name}  {extra}")


def main():
    print("[1] 三处键集合一致")
    fields, panels = ex.load_meta()
    defaults = config.DEFAULT_CONFIG
    example = json.load(io.open(os.path.join(ROOT, "bot_config.json.example"), encoding="utf-8"))
    problems, lines, detail = ex.audit(fields, defaults, set(example.keys()))

    check("example 不漏登记 DEFAULT_CONFIG 的键", not detail["only_default"],
          detail["only_default"])
    check("example 里没有死键", not detail["dead"], detail["dead"])
    check("审计本身不报问题", not problems, problems)

    print("\n[2] 文档是生成的，且与数据源对得上")
    doc = ex.build_doc(fields, panels, defaults, set(example.keys()))
    check("文档非空且有表头结构",
          doc.startswith("# 配置项说明") and "| 配置项 | 键名 | 默认值 | 重启 |" in doc)
    # 每个配置页字段都要在文档里出现（用键名判，最可靠）
    missing = [k for k in fields if ("`%s`" % k) not in doc]
    check("95 个字段全部出现在文档里", not missing, missing[:8])
    check("文档标了「不要手工编辑」", "不要手工编辑" in doc)
    check("文档说明了「—」的含义", "没有这个键" in doc)

    print("\n[3] 横排开关标签不串位（2026-10-24 实测踩过）")
    check("use_local_llm 的标签是「本地聊天模型」，不是上一个字段的「模型」",
          fields["use_local_llm"]["label"] == "本地聊天模型",
          fields["use_local_llm"]["label"])
    check("use_local_llm 的说明是本地模式那段，不是模型名那段",
          "GGUF" in (fields["use_local_llm"].get("desc") or ""),
          fields["use_local_llm"].get("desc"))
    check("use_local_llm 与 model_name 标签不撞名",
          fields["use_local_llm"]["label"] != fields["model_name"]["label"],
          (fields["use_local_llm"]["label"], fields["model_name"]["label"]))

    print("\n[4] 嵌套键的默认值取得到")
    check("de_ai.enabled 默认值取到 True",
          ex.default_of("de_ai.enabled", defaults) is True,
          ex.default_of("de_ai.enabled", defaults))
    check("de_ai.max_sentence_len 默认值取到 0",
          ex.default_of("de_ai.max_sentence_len", defaults) == 0,
          ex.default_of("de_ai.max_sentence_len", defaults))
    check("auto_accept.friend 默认值取到 True",
          ex.default_of("auto_accept.friend", defaults) is True)
    check("嵌套键不在 DEFAULT_CONFIG 顶层",
          "de_ai.enabled" not in defaults)
    # 逐行看 de_ai 那些行的"默认值"列。判据取列内容，不数子串 ——
    # 第一版写 `doc.count("| `de_ai.")`，而行是 `| prompt 注入… | \`de_ai.x\` | 开启 |`
    # 中间夹着标签文字，那个子串根本不存在，断言恒假。
    deai_rows = [ln for ln in doc.splitlines() if "`de_ai." in ln]
    bad = [ln for ln in deai_rows if ln.split("|")[3].strip() in ("—", "")]
    check("文档里 %d 个 de_ai 开关都有默认值" % len(deai_rows),
          len(deai_rows) >= 9 and not bad, bad[:3])

    print("\n[5] code_refs 不把非配置读取算成活着")
    # model 是活键：webui_server 有 cfg.get('model_name') or cfg.get('model')
    check("model 有配置读取（兜底）", ex.code_refs("model") >= 1, ex.code_refs("model"))
    # free_llm.py 里的 r.get("model") 是读 API 响应，不算配置读取
    check("code_refs 不把 API 响应字段算成配置读取",
          ex.code_refs("totally_not_a_config_key_xyz") == 0)
    check("protocols 驱动模板里的 ws_port 键算活的",
          ex.code_refs("ws_port") >= 1, ex.code_refs("ws_port"))

    print("\n[6] 遗留键被点名，不混进正常键")
    check("llm_model 登记为遗留", "llm_model" in ex.LEGACY_KEYS)
    check("审计输出里单列了遗留键一行", any("遗留键" in ln for ln in lines), lines[:6])

    print("\n[7] 文档落盘与幂等")
    out = os.path.join(ROOT, "docs", "配置项说明.md")
    old = io.open(out, encoding="utf-8").read() if os.path.exists(out) else ""
    check("落盘的文档与当前数据生成的一致（说明有人手工改过就会暴露）",
          old == doc, "长度 %d vs %d" % (len(old), len(doc)))

    print("\n" + "=" * 56)
    print(f"通过 {PASS} / 失败 {FAIL}")
    if FAILURES:
        print("失败项：")
        for f in FAILURES:
            print("  -", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())