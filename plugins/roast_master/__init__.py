# -*- coding: utf-8 -*-
"""毒舌吐槽大师（真插件 ABI v1）：发「吐槽 xxx」随机毒舌，发「吐槽我」自嘲。

触发：`吐槽 <内容/人名>` / `!roast <内容>`；`吐槽我` → 对自己下手。
语气：阴阳怪气为主，攻击性为 0（对事不对人，纯属整活）。
"""
import random

from plugins.abi import PluginContext

PLUGIN_SPEC = 1
PLUGIN_MANIFEST = {
    "name": "roast_master",
    "version": "1.0.0",
    "api_version": 1,
    "author": "EggBloom",
    "description": "毒舌吐槽大师：发「吐槽 xxx」获得随机阴阳怪气评论",
    "dependencies": [],
    "priority": 100,
    "default_enabled": True,
}

_ROASTS = [
    "你的存在感，像群公告：明明发了，但没人看。",
    "你说话的样子像在跟路由器聊天，对方也懒得回你。",
    "你的计划清单比我的身材还丰满，完成的没一个。",
    "你搁这努力的样子，让我想起了考试前夜的复习资料——全新未拆封。",
    "建议你别熬夜了，反正你梦里也实现不了，不如早点睡省点电。",
    "你这个人吧，优点是想得开，缺点是只想得开。",
    "你的幽默感像你家 WiFi：时有时无，还经常连不上。",
    "别人是三天打鱼两天晒网，你是三天晒网，两天躺着看别人打鱼。",
    "你打字速度挺快，就是脑子跟不上，手都替你着急。",
    "你这水平去参加比赛，评委都得给你颁个「重在参与」奖，然后提前散场。",
    "你的人生像一张优惠券：看起来能用，扫一下发现过期了。",
    "别灰心，你虽然一事无成，但你一事无成得很稳定。",
    "你朋友圈发得比我的代码注释还勤，内容比注释还难懂。",
    "说你懒吧你还不服，你连不服都是躺着说的。",
]

_SELF_ROASTS = [
    "吐槽我？我连自己都吐槽：本 bot 的代码 5 万行，有一半是调试日志。",
    "我？我是由 bug 组成的，运行全靠玄学。",
    "我说话有时候也像个复读机，毕竟我是个机器人，别跟我一般见识。",
    "我连自己的插件都理不清，还有脸吐槽别人？（笑）",
    "我 0.65 的温度，烧不熟任何东西，只能输出阴阳怪气。",
]


def _roast(target: str, ctx: PluginContext) -> str:
    if target in ("我", "自己", "本机器人", "bot", "你"):
        return random.choice(_SELF_ROASTS)
    total = int(ctx.get_state("total_roasts", 0) or 0) + 1
    ctx.set_state("total_roasts", total)
    roast = random.choice(_ROASTS)
    header = f"（对 {target} 的观察报告）" if target else ""
    footer = f"\n（本bot已毒舌 {total} 次，再吐槽要收费了）" if total % 5 == 0 else ""
    return f"{header}{roast}{footer}"


def on_message(ctx: PluginContext, event: dict):
    text = (event.get("text") or "").strip()
    if not text:
        return None
    for prefix in ("吐槽", "!roast"):
        if text.startswith(prefix):
            target = text[len(prefix):].strip() or ""
            return _roast(target, ctx)
    return None


PLUGIN_HOOKS = {"on_message": on_message}
