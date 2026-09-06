# -*- coding: utf-8 -*-
"""神经算命师（真插件 ABI v1）：发「算命」或「!fortune」→ 一本正经胡说八道。

纯随机生成，无任何真实性，请勿当真（当真也没用）。
触发：消息以 `算命` / `!fortune` / `占卜` 开头（可带要算的内容）。
"""
import random

from plugins.abi import PluginContext

PLUGIN_SPEC = 1
PLUGIN_MANIFEST = {
    "name": "fortune_teller",
    "version": "1.0.0",
    "api_version": 1,
    "author": "EggBloom",
    "description": "神经算命师：发「算命 xxx」获得随机胡编的运势",
    "dependencies": [],
    "priority": 100,
    "default_enabled": True,
}

_PAST_LIFES = [
    ("东汉", "驿站马倌"), ("唐朝", "御膳房洗碗工"), ("宋朝", "科举落榜书童"),
    ("明朝", "织布机维修工"), ("清朝", "镖局账房先生"), ("民国", "黄包车夫"),
    ("商朝", "甲骨文刻错字的学徒"), ("周朝", "编钟校音师"), ("秦朝", "修长城摸鱼小兵"),
    ("三国", "给诸葛亮递扇子的"),
]

_DOMAINS = ["财运", "桃花", "事业", "学业", "健康", "社交", "手气", "睡眠", "胃口", "网速"]

_ADVICE = [
    "今天别立 flag，立了必倒。",
    "出门右转，好运在便利店第二排货架。",
    "建议今天多喝水，少说话，尤其是别发誓。",
    "今日宜摸鱼，忌写代码（反正你也在摸）。",
    "把手机倒过来看一眼，会有惊喜（也可能是惊吓）。",
    "今天适合表白，表白对象：你的枕头。",
    "走路别踩井盖，踩了就当无事发生。",
    "今天运气守恒：这里亏的，那里也会亏。",
    "建议现在立刻夸我一句，能消灾。",
    "今日幸运色：五彩斑斓的黑。",
    "今晚早点睡，梦里啥都有（包括考试卷子）。",
    "今天不适合抽卡，适合抽自己一嘴巴。",
]


def _fortune(subject: str, ctx: PluginContext) -> str:
    dynasty, job = random.choice(_PAST_LIFES)
    stars = random.randint(1, 6)
    domain = random.choice(_DOMAINS)
    luck_line = random.choice([
        f"今日{domain}运势：{'★' * stars}{'☆' * (6 - stars)}",
        f"今日{domain}：{'大吉' if stars >= 4 else ('中平' if stars >= 2 else '大凶（别怕，瞎说的）')}",
        f"今日{domain}评分：{random.randint(0, 100)}分（满分100，及格线60，自己品）",
    ])
    # 累计算命次数，越算越"折寿"
    times = int(ctx.get_state("times", 0) or 0) + 1
    ctx.set_state("times", times)
    warn = ""
    if times >= 5:
        warn = f"\n（你已经连算 {times} 次了，再算下去会折寿，建议到此为止）"
    subject_txt = f"（{subject}）" if subject else ""
    return (f"【前世】你上辈子是{dynasty}的{job}，所以这辈子命里缺觉。\n"
            f"【今日运势】{luck_line}\n"
            f"【建议】{random.choice(_ADVICE)}{warn}{subject_txt}")


def on_message(ctx: PluginContext, event: dict):
    text = (event.get("text") or "").strip()
    if not text:
        return None
    for prefix in ("算命", "占卜", "!fortune"):
        if text.startswith(prefix):
            subject = text[len(prefix):].strip() or ""
            return _fortune(subject, ctx)
    return None


PLUGIN_HOOKS = {"on_message": on_message}
