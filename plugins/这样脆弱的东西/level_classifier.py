# -*- coding: utf-8 -*-
"""数据量分级判定：按消息条数判定 L0-L4 等级，并给出该等级的产出规格。

等级不再只是"预算数字"，它决定三件事：
  1. 跑哪些 section（见 sections.LEVEL_SECTIONS）
  2. 每节期望产出多少条目（expected，用于脆弱度与覆盖度评分）
  3. 这个等级相对于现有数据量是否吃得消（feasibility）
"""
from . import sections

LEVELS = {
    "L0": {"name": "种子", "min": 0, "max": 99, "budget": 200_000,
           "desc": "身份锚点，能说出这人大概是什么样", "expected": 6},
    "L1": {"name": "核心", "min": 100, "max": 999, "budget": 500_000,
           "desc": "语言指纹+行为，日常对话风格可匹配", "expected": 18},
    "L2": {"name": "深度", "min": 1000, "max": 9999, "budget": 2_000_000,
           "desc": "认知+关系，跨话题人格稳定", "expected": 34},
    "L3": {"name": "完整", "min": 10000, "max": 49999, "budget": 5_000_000,
           "desc": "时间+环境，人格模型可对话", "expected": 52},
    "L4": {"name": "完美", "min": 50000, "max": None, "budget": 20_000_000,
           "desc": "完整重建，图灵测试级还原", "expected": 80},
}

LEVEL_ORDER = ["L0", "L1", "L2", "L3", "L4"]


def classify_level(data_count):
    """按数据量判定等级。"""
    for level in LEVEL_ORDER:
        info = LEVELS[level]
        if data_count < (info["max"] if info["max"] is not None else float("inf")):
            return level
    return "L4"


def level_info(level):
    info = dict(LEVELS.get(level, LEVELS["L0"]))
    # section 清单以 sections.py 为唯一真相，避免两边各写一份
    info["sections"] = sections.for_level(level)
    info["llm_sections"] = sections.llm_sections_for_level(level)
    info["section_count"] = len(info["sections"])
    return info


def level_budget(level):
    return LEVELS.get(level, LEVELS["L0"])["budget"]


def level_sections(level):
    """兼容旧接口：返回 "S0-S7" 这样的区间字符串。"""
    ids = sections.for_level(level)
    return f"{ids[0]}-{ids[-1]}" if ids else "S0"


def sections_for_level(level):
    return sections.for_level(level)


def expected_entries(level):
    return LEVELS.get(level, LEVELS["L0"])["expected"]


def feasibility(data_count, level):
    """该等级相对现有数据量是否吃得消。

    返回 {"ok", "ratio", "grade", "hint"}：
      ratio = 数据量 / 该等级门槛。L0 无门槛，恒为 1。
    """
    info = LEVELS.get(level, LEVELS["L0"])
    floor = info["min"]
    if floor <= 0:
        return {"ok": True, "ratio": 1.0, "grade": "合适",
                "hint": "L0 对数据量没有门槛"}
    ratio = round(data_count / floor, 3)
    if ratio >= 1.0:
        grade, ok, hint = "合适", True, "数据量满足该等级门槛"
    elif ratio >= 0.3:
        grade, ok, hint = "勉强", True, (
            f"数据量约为 {level} 门槛的 {ratio:.0%}，"
            "深层 section 的证据会偏薄，产出置信度会自动下调"
        )
    else:
        grade, ok, hint = "超纲", True, (
            f"数据量仅为 {level} 门槛的 {ratio:.0%}。"
            f"建议降到 {classify_level(data_count)}，"
            "或把它们当作「薄证据下的最大努力」看待"
        )
    return {"ok": ok, "ratio": ratio, "grade": grade, "hint": hint}
