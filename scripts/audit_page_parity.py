# -*- coding: utf-8 -*-
"""全页面功能对等审计：把 v3 每个页面的功能点逐条核对 v4 有没有。

为什么需要它
------------
"页面看着对"不等于"功能对"。v3→v4 的退化几乎都是**静默**的：
按钮没了、端点没接、控件退化（划线列表变 JSON 框），页面照样渲染得很好看。
所以这个脚本不看外观，只比对三样可验证的东西：
  ① 端点：v3 该页调用过、v4 同页没调用的（= 功能缺失的最硬证据）
  ② 交互函数：v3 有、整个 v4 找不到的（mountXxx / startStream / replaySession…）
  ③ 控件标识：v3 页里的按钮/表单 id，v4 同页没有的

用法：python scripts/audit_page_parity.py [--md 输出.md]
"""
import io
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
V3 = os.path.join(ROOT, "webui", "webui_artistic_v3.html")
V4 = os.path.join(ROOT, "webui", "webui_artistic_v4.html")

# v3 的页面挂载函数 → v4 的页面函数（页面名两边一致）
PAGE_MAP = {
    "dashboard": (["mountDashboard", "loadDashStatus", "loadDashLog"],
                  ["pageDashboard"]),
    "protocol": (["mountProtocol"], ["pageProtocol"]),
    "groupmanage": (["mountGroupManager", "openMembers", "loadMembers",
                     "loadFriends", "openAddModal"],
                    ["pageGroups"]),
    "memes": (["mountMemes", "updateMemeCount", "openLightbox"],
              ["pageMemes", "memeLightbox", "memeUpload"]),
    "imagegen": (["mountImagegen"], ["pageImagegen"]),
    "behavior": (["renderTriggerPanel", "renderExitPanel", "renderLengthPanel"],
                 ["pageConfig", "cfRenderArea"]),
    "settings": (["renderLLMPanel", "collectSettings", "bindSettingsEvents",
                  "validateLLMForm", "openProviderModal", "initSettingsSearch"],
                 ["pageConfig", "cfRenderArea", "cfBindArea", "protoWizard"]),
    "bot": (["mountBot", "pushBot", "pushSys", "pushUser"],
            ["pageBot"]),
    "chatlogs": (["loadSessions", "switchSession", "newSession", "replaySession",
                  "rerunFrom", "getSelectedDashLogText", "scrollBottom"],
                 ["pageChatlogs"]),
    "console": (["mountConsole", "startStream", "stopStream", "renderTrace"],
                ["pageConsole"]),
    "memory": (["loadMemory", "refreshHourglass"], ["pageMemory"]),
    "monitor": (["loadNotifySettings", "bindNotifyActions", "scanAll"],
                ["focusedForm"]),
    "plugins": (["mountPlugins", "bindCardActions", "bindGridActions"],
                ["pagePlugins"]),
}

# v3 的动作按钮 → 中文含义（用于报告里说清"缺了什么"）
LABELS = {
    "startStream": "控制台流式执行", "stopStream": "停止流式执行",
    "replaySession": "会话重放", "rerunFrom": "从某条重跑",
    "getSelectedDashLogText": "日志选中片段复制", "loadSessions": "会话列表",
    "switchSession": "切换会话", "newSession": "新建会话",
    "loadMemory": "记忆检索", "refreshHourglass": "沙漏刷新",
    "loadNotifySettings": "通知设置", "scanAll": "全量扫描",
    "loadFriends": "好友列表", "openMembers": "成员抽屉", "openAddModal": "添加弹窗",
    "loadMemeCount": "表情包计数", "openLightbox": "表情包灯箱",
    "validateLLMForm": "LLM 表单校验", "openProviderModal": "提供商弹窗",
    "initSettingsSearch": "设置搜索", "mountEditableList": "可编辑划线列表",
    "openSetupWizard": "配置向导", "runDetect": "自动探测",
    "openProtoFileBrowser": "文件浏览器", "showDriverCfg": "驱动配置弹窗",
    "renderProtoCheck": "启动前自检", "renderProtoRiskPanel": "风险面板",
    "mountConsole": "底层控制台", "mountGroupManager": "群管理",
}


def body(src, name):
    i = src.find("function " + name)
    if i < 0:
        return ""
    j = src.index("{", src.index(")", i))
    depth, k = 0, j
    while k < len(src):
        if src[k] == "{":
            depth += 1
        elif src[k] == "}":
            depth -= 1
            if depth == 0:
                break
        k += 1
    return src[j:k]


def main():
    s3 = io.open(V3, encoding="utf-8").read()
    s4 = io.open(V4, encoding="utf-8").read()
    api3 = re.compile(r"API\(\s*['\"`]([^'\"`]+)")
    id3 = re.compile('id="([A-Za-z][A-Za-z0-9_]{2,30})"')

    rows, gaps = [], []
    for page, (f3, f4) in PAGE_MAP.items():
        b3 = "".join(body(s3, f) for f in f3 if body(s3, f))
        b4 = "".join(body(s4, f) for f in f4 if body(s4, f))
        if not b3:
            continue
        eps3 = {e.lstrip("/") for e in api3.findall(b3)}
        eps4 = {e.lstrip("/") for e in api3.findall(b4)}
        miss_ep = sorted(eps3 - eps4)
        fn_miss = [f for f in f3 if f not in s4]
        ids3 = set(id3.findall(b3))
        ids4 = set(id3.findall(b4))
        # 只关心"动作类" id（btn/xxxBtn/act 等），字段 id 由 FIELD_META 另行核对
        act3 = {i for i in ids3 if re.search(r"(Btn|btn|act|Act|Toggle|toggle|Go)$", i)}
        miss_id = sorted(act3 - ids4)
        rows.append((page, len(eps3), len(eps4), len(miss_ep), len(fn_miss), len(miss_id)))
        gaps.append({
            "page": page, "missing_endpoints": miss_ep,
            "missing_functions": fn_miss, "missing_actions": miss_id,
        })

    print("=" * 78)
    print("v4 全页面功能对等审计")
    print("=" * 78)
    print("%-12s %8s %8s %10s %10s" % ("页面", "v3端点", "v4端点", "缺端点", "缺交互函数"))
    for page, n3, n4, me, mf, mi in rows:
        flag = "  ← 有缺口" if (me or mf or mi) else "  ✓"
        print("%-12s %8d %8d %10d %10d%s" % (page, n3, n4, me, mf, flag))

    print("\n" + "-" * 78)
    print("逐项缺口")
    print("-" * 78)
    for g in gaps:
        if not (g["missing_endpoints"] or g["missing_functions"] or g["missing_actions"]):
            continue
        print("\n【%s】" % g["page"])
        if g["missing_endpoints"]:
            print("  端点：" + "、".join(g["missing_endpoints"]))
        if g["missing_functions"]:
            print("  交互：" + "、".join(
                "%s(%s)" % (f, LABELS.get(f, "?")) for f in g["missing_functions"]))
        if g["missing_actions"]:
            print("  按钮：" + "、".join(g["missing_actions"]))

    out = os.path.join(ROOT, "_parity_report.json")
    io.open(out, "w", encoding="utf-8").write(json.dumps(gaps, ensure_ascii=False, indent=1))
    print("\n写出：" + out)
    return 0


if __name__ == "__main__":
    sys.exit(main())