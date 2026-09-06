# -*- coding: utf-8 -*-
"""猫语翻译官（真插件 ABI v1）：人话 ↔ 猫话互译。

触发：
- `猫语 <内容>`  → 把内容翻译成猫话
- `人话 <猫话>`  → 把猫话翻译回人话（随机）*
- 消息里带「喵」 → 随机回应一句猫言猫语

*人话翻译是胡编的，猫听不懂，你也听不懂，纯属气氛组。
"""
import random

from plugins.abi import PluginContext

PLUGIN_SPEC = 1
PLUGIN_MANIFEST = {
    "name": "cat_translator",
    "version": "1.0.0",
    "api_version": 1,
    "author": "EggBloom",
    "description": "猫语翻译官：发「猫语 xxx」翻译成猫话，或带「喵」触发回应",
    "dependencies": [],
    "priority": 100,
    "default_enabled": True,
}

# 词替换表（人话 → 猫话）
_CAT_WORDS = {
    "吃": "恰", "饭": "罐罐", "你": "本喵", "我": "朕", "好": "妙", "累": "困",
    "睡": "眯", "来": "驾到", "去": "遁", "看": "盯", "叫": "喵叫", "走": "溜",
    "快乐": "愉悦", "今天": "今日", "心情": "毛色", "工作": "磨爪子",
}

_CAT_REPLIES = [
    "喵喵喵？朕刚睡醒，不懂人话。",
    "（甩了甩尾巴）你刚才说啥？本喵没听清，再说一遍要加罐罐。",
    "喵~ 朕觉得你该喂猫了，这是朕的直觉。",
    "（歪头）人话？听不懂，但朕原谅你。",
    "喵。这一声是朕今天的第 {n} 声喵，再叫要收费了。",
    "（伸懒腰）听懂了吗？没听懂就对了，朕也不懂。",
]


def _catify(text: str) -> str:
    out = []
    for ch in text:
        if ch in _CAT_WORDS:
            out.append(_CAT_WORDS[ch])
        elif random.random() < 0.04:
            out.append(random.choice(["喵", "咪", "咕噜", "喵~"]))
        else:
            out.append(ch)
    return "".join(out) + random.choice([" 喵~", "（喵）", "喵喵喵！", "（咕噜咕噜）", " 喵。"]) + \
        random.choice(["", "", "（朕说的）", "（翻译完了，你爱信不信）"])


def _humanify(text: str) -> str:
    """假装翻译回人话：纯气氛组，随机重排成一句"人话"。"""
    n = random.randint(3, 6)
    parts = ["好的", "明白了", "原来如此", "收到", "知道了", "没问题", "真的假的", "行吧", "懂了", "离谱", "真巧", "难绷", "焯", "好家伙"]
    return "（猫话翻译）" + "，".join(random.sample(parts, min(n, len(parts)))) + "。翻译完毕，猫嘴硬，别较真。" + \
        ("（其实朕没听懂）" if random.random() < 0.5 else "")


def on_message(ctx: PluginContext, event: dict):
    text = (event.get("text") or "").strip()
    if not text:
        return None
    if text.startswith("猫语") or text.startswith("猫话"):
        content = text[len("猫语" if text.startswith("猫语") else "猫话"):].strip() or "喵"
        return _catify(content)
    if text.startswith("人话"):
        content = text[2:].strip() or "喵"
        return _humanify(content)
    if "喵" in text:
        n = int(ctx.get_state("meows", 0) or 0) + 1
        ctx.set_state("meows", n)
        r = random.choice(_CAT_REPLIES)
        return r.format(n=n)
    return None


PLUGIN_HOOKS = {"on_message": on_message}
