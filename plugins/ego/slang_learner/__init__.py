# -*- coding: utf-8 -*-
"""
slang_learner 插件：让 EggBloom 能手动学习最新网络黑话，候选池确认后再注入。
优化版：保持“手动触发 + 用户确认注入”模式，提升学习触发效率和注入流程。
扩展版：A) 扩圈层词表；B) 轻量 LLM 引导生成，每次 5-8 条，仍走候选池确认。
"""
import json
import os
import re
import sys
import time
import urllib.request
import urllib.error
from typing import Any, Dict, List, Optional

PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(PLUGIN_DIR, "data")
PENDING_FILE = os.path.join(DATA_DIR, "pending.json")
INJECTED_FILE = os.path.join(DATA_DIR, "injected.json")
HISTORY_FILE = os.path.join(DATA_DIR, "history.json")

PLUGIN_META = {
    "name": "slang_learner",
    "version": "0.3.0",
    "description": "网络黑话学习器：手动学习最新圈层黑话，候选池确认后注入 slang_injector。扩展版：扩圈层词表 + 轻量 LLM 引导生成。",
    "author": "EggBloom",
    "hooks": [],
    "priority": 50,
    "enabled": True,
    "tags": ["黑话学习"],
    "page": "plugins/slang_learner/ui.html",
    "config_schema": {
        "auto_learn": {"type": "bool", "default": False},
        "auto_learn_interval_hours": {"type": "int", "default": 12},
        "learn_interval_minutes": {"type": "int", "default": 120},
        "batch_size": {"type": "int", "default": 30},
        "auto_inject": {"type": "bool", "default": False},
        "trusted_sources": {"type": "list", "default": ["游戏圈", "直播圈", "抽象圈", "电竞圈", "弹幕圈", "二次元圈", "网梗圈", "职场圈", "饭圈", "科技圈", "鬼畜圈", "短视频圈"]},
        "min_quality": {"type": "float", "default": 0.3},
        "deduplicate_existing": {"type": "bool", "default": True},
        "llm_enrich": {"type": "bool", "default": False},
        "llm_batch_size": {"type": "int", "default": 6},
    },
}

os.makedirs(DATA_DIR, exist_ok=True)


def _load_json(path: str, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _save_json(path: str, data):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[SlangLearner] 保存失败 {path}: {e}")


CONFIG_PATH = os.path.join(DATA_DIR, "config.json")
_DEFAULT_CONFIG = {
    "auto_learn": False,
    "auto_learn_interval_hours": 12,
    "learn_interval_minutes": 120,
    "batch_size": 30,
    "auto_inject": False,
    "trusted_sources": [
        "游戏圈", "直播圈", "抽象圈", "电竞圈", "弹幕圈", "二次元圈",
        "网梗圈", "职场圈", "饭圈", "科技圈", "鬼畜圈", "短视频圈",
    ],
    "min_quality": 0.3,
    "deduplicate_existing": True,
    "llm_enrich": False,
    "llm_batch_size": 6,
}


def get_config() -> Dict[str, Any]:
    cfg = dict(_DEFAULT_CONFIG)
    cfg.update(_load_json(CONFIG_PATH, {}))
    return cfg


def set_config(params: Dict[str, Any]) -> Dict[str, Any]:
    cfg = get_config()
    if not isinstance(params, dict):
        return cfg
    for key in _DEFAULT_CONFIG:
        if key in params:
            cfg[key] = params[key]
    _save_json(CONFIG_PATH, cfg)
    return cfg


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _now_ts() -> float:
    return time.time()


class SlangLearner:
    def __init__(self):
        self.INJECTED_FILE = INJECTED_FILE
        self.PENDING_FILE = PENDING_FILE
        self.HISTORY_FILE = HISTORY_FILE
        self.pending: List[dict] = _load_json(PENDING_FILE, [])
        self.injected: List[dict] = _load_json(INJECTED_FILE, [])
        self.history: List[dict] = _load_json(HISTORY_FILE, [])

    def start_learning(self, topics: Optional[List[str]] = None) -> Dict[str, Any]:
        topics = topics or ["游戏圈", "直播圈", "抽象圈", "电竞圈", "弹幕圈"]
        candidates = []
        for topic in topics[:5]:
            candidates.extend(self._collect_from_topic(topic))

        # 真实数据源：实时热搜/热词（百度 + B站），作为"最新梗"候选
        hot_count = 0
        try:
            hot = self._fetch_hot_sources()
            hot_count = len(hot)
            candidates.extend(hot)
        except Exception as e:
            print(f"[SlangLearner] 实时热搜抓取失败: {e}")

        # B 部分：小体量 LLM 引导补充
        config = _load_json(os.path.join(PLUGIN_DIR, "data", "config.json"), {})
        if config.get("llm_enrich"):
            batch = int(config.get("llm_batch_size") or 6)
            for topic in topics[:5]:
                try:
                    candidates.extend(self._llm_enrich(topic, batch))
                except Exception as e:
                    print(f"[SlangLearner] llm_enrich 失败 {topic}: {e}")

        cleaned = self._clean(candidates)

        # 去重：排除已注入和已有候选
        existing_ids = {item["id"] for item in self.pending}
        injected_ids = {item["id"] for item in self.injected}
        existing_ids.update(injected_ids)

        # 读取已有 slang_dict 进一步去重
        slang_path = os.path.join(os.path.dirname(PLUGIN_DIR), "slang_dict.json")
        slang_data = _load_json(slang_path, {"entries": {}})
        existing_ids.update(slang_data.get("entries", {}).keys())

        added = []
        seen = set()
        for item in cleaned:
            key = item["id"]
            if key in existing_ids or key in seen:
                continue
            item.setdefault("status", "pending")
            item.setdefault("learned_at", _now_iso())
            item.setdefault("learned_ts", _now_ts())
            item.setdefault("quality", self._estimate_quality(item))
            item.setdefault("source_count", 1)
            self.pending.append(item)
            seen.add(key)
            existing_ids.add(key)
            added.append(item)

        self.history.append(
            {
                "at": _now_iso(),
                "topics": topics,
                "added": len(added),
                "items": [i["id"] for i in added],
            }
        )
        _save_json(PENDING_FILE, self.pending)
        _save_json(HISTORY_FILE, self.history)
        return {"ok": True, "added": len(added), "items": added}

    def get_pending(self, search: str = "", source: str = "", min_quality: float = 0.0) -> List[dict]:
        result = []
        for item in self.pending:
            if item.get("status") != "pending":
                continue
            if search and search not in item.get("id", "") and search not in item.get("meaning", ""):
                continue
            if source and item.get("source") != source:
                continue
            if item.get("quality", 0) < min_quality:
                continue
            result.append(item)
        return result

    def inject_selected(self, ids: List[str]) -> Dict[str, Any]:
        slang_path = os.path.join(os.path.dirname(PLUGIN_DIR), "slang_dict.json")
        slang_data = _load_json(slang_path, {"entries": {}})
        entries = slang_data.get("entries", {}) if isinstance(slang_data, dict) else {}
        updated = 0
        for item in self.pending:
            if item.get("id") in ids and item.get("status") == "pending":
                # 释义待确认的热搜词不允许直接注入（避免把占位文本写进词典）
                meaning = item.get("meaning", "")
                if not meaning or "待确认" in meaning or "待补充" in meaning:
                    continue
                entries[item["id"]] = meaning
                item["status"] = "injected"
                item["injected_at"] = _now_iso()
                item["injected_ts"] = _now_ts()
                self.injected.append(item)
                updated += 1
        slang_out = {"entries": entries}
        _save_json(slang_path, slang_out)
        _save_json(PENDING_FILE, self.pending)
        _save_json(INJECTED_FILE, self.injected)
        try:
            if "slang_injector" in sys.modules:
                import importlib
                importlib.reload(sys.modules["slang_injector"])
        except Exception as e:
            print(f"[SlangLearner] 热更新 slang_injector 失败: {e}")
        return {"ok": True, "injected": updated}

    def inject_all(self, min_quality: float = 0.0) -> Dict[str, Any]:
        ids = [
            item["id"]
            for item in self.pending
            if item.get("status") == "pending" and item.get("quality", 0) >= min_quality
        ]
        if not ids:
            return {"ok": True, "injected": 0}
        return self.inject_selected(ids)

    def reject_selected(self, ids: List[str]) -> Dict[str, Any]:
        count = 0
        for item in self.pending:
            if item.get("id") in ids and item.get("status") == "pending":
                item["status"] = "rejected"
                item["rejected_at"] = _now_iso()
                item["rejected_ts"] = _now_ts()
                count += 1
        _save_json(PENDING_FILE, self.pending)
        return {"ok": True, "rejected": count}

    def reject_all(self, min_quality: float = 0.0) -> Dict[str, Any]:
        ids = [
            item["id"]
            for item in self.pending
            if item.get("status") == "pending" and item.get("quality", 0) < min_quality
        ]
        if not ids:
            return {"ok": True, "rejected": 0}
        return self.reject_selected(ids)

    def get_injected(self, limit: int = 100) -> List[dict]:
        return [item for item in self.injected if item.get("status") == "injected"][-limit:]

    def get_history(self, limit: int = 50) -> List[dict]:
        return self.history[-limit:]

    def start_auto_learn_loop(self, interval_hours: float = 12.0):
        """启动后台定时自动学习线程（抓取最新热搜 → 进候选池，不自动注入）"""
        import threading
        interval = max(1.0, float(interval_hours))

        def _loop():
            while True:
                try:
                    result = self.start_learning()
                    print(f"[SlangLearner] 自动学习完成: 新增 {result.get('added', 0)} 条候选")
                except Exception as e:
                    print(f"[SlangLearner] 自动学习失败: {e}")
                time.sleep(interval * 3600)

        t = threading.Thread(target=_loop, daemon=True, name="slang-auto-learn")
        t.start()
        return t

    def get_stats(self) -> Dict[str, Any]:
        pending_count = sum(1 for item in self.pending if item.get("status") == "pending")
        injected_count = sum(1 for item in self.injected if item.get("status") == "injected")
        rejected_count = sum(1 for item in self.pending if item.get("status") == "rejected")
        sources = {}
        for item in self.pending:
            if item.get("status") == "pending":
                sources[item.get("source", "未知")] = sources.get(item.get("source", "未知"), 0) + 1
        return {
            "pending": pending_count,
            "injected": injected_count,
            "rejected": rejected_count,
            "total_learned": len(self.history),
            "sources": sources,
            "recent_topics": [h.get("topics", []) for h in self.history[-5:]],
        }

    def _collect_from_topic(self, topic: str) -> List[dict]:
        curated = self._scrape_sources(topic)
        results = []
        seen = set()
        for term, meaning in curated:
            term = term.strip()
            if not term or term in seen:
                continue
            seen.add(term)
            results.append(
                {
                    "id": term,
                    "meaning": meaning,
                    "source": topic,
                    "topic": topic,
                    "quality": 0.9,
                    "learned_at": _now_iso(),
                    "learned_ts": _now_ts(),
                }
            )
        return results

    def _llm_enrich(self, topic: str, batch_size: int = 6) -> List[dict]:
        """小体量 LLM 引导：给 topic 生成少量高传播度候选词条，保持同一格式。"""
        candidates: List[dict] = []

        # 读取项目级 provider 配置，复用现有 LLM 能力，不硬编码新依赖
        try:
            bot_config_path = os.path.join(os.path.dirname(PLUGIN_DIR), "..", "bot_config.json")
            bot_config = _load_json(bot_config_path, {})
            provider = (bot_config.get("api_provider") or bot_config.get("provider") or "deepseek")
            provider_cfg = next(
                (p for p in (bot_config.get("providers") or []) if p.get("id") == provider),
                {},
            )
            api_url = provider_cfg.get("base_url") or bot_config.get("api_url") or "https://api.deepseek.com/chat/completions"
            api_key = provider_cfg.get("api_key") or bot_config.get("api_key") or ""
            model = provider_cfg.get("model") or bot_config.get("model") or "deepseek-chat"
        except Exception:
            api_url = "https://api.deepseek.com/chat/completions"
            api_key = ""
            model = "deepseek-chat"

        if not api_key:
            return candidates

        prompt = (
            "你是网络黑话学习助手。请为圈层【{topic}】生成 {n} 个当前高传播度黑话/梗词。\n"
            "要求：每个词必须是圈层内真实在用的，不要编造过时的。\n"
            "输出严格 JSON 数组，每个元素两个字段：id（词本身）、meaning（说明，包含使用场景、文化影响、衍生梗）。\n"
            "不要输出多余内容。"
        ).format(topic=topic, n=batch_size)

        body = json.dumps({
            "model": model,
            "messages": [
                {"role": "system", "content": "只输出 JSON，不要解释。"},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.4,
            "max_tokens": 1200,
        }).encode("utf-8")

        req = urllib.request.Request(
            api_url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                raw = resp.read().decode("utf-8")
        except Exception as e:
            print(f"[SlangLearner] llm_enrich 请求失败: {e}")
            return candidates

        try:
            outer = json.loads(raw)
            content = ((outer.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        except Exception:
            content = raw

        # 容错解析：从返回文本里提取 JSON 数组
        m = re.search(r"(\[.*\])", content, re.S)
        if not m:
            return candidates
        try:
            items = json.loads(m.group(1))
        except Exception:
            return candidates

        for item in items[: batch_size * 2]:
            term = (item.get("id") or item.get("term") or "").strip()
            meaning = (item.get("meaning") or item.get("desc") or "").strip()
            if not term or not meaning:
                continue
            candidates.append(
                {
                    "id": term,
                    "meaning": meaning,
                    "source": topic,
                    "topic": topic,
                    "quality": 0.7,
                    "learned_at": _now_iso(),
                    "learned_ts": _now_ts(),
                }
            )
        return candidates

    def _fetch(self, url: str, timeout: int = 6) -> str:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            for enc in ["utf-8", "gbk", "gb2312", "gb18030"]:
                try:
                    return raw.decode(enc)
                except Exception:
                    continue
            return raw.decode("utf-8", errors="ignore")

    def _extract_terms(self, text: str) -> List[str]:
        text = re.sub(r"<[^>]+>", "", text)
        text = re.sub(r"\s+", "", text)
        matches = re.findall(r"[\u4e00-\u9fffA-Za-z0-9_·]+", text)
        terms = []
        seen = set()
        for m in matches:
            if m in seen:
                continue
            seen.add(m)
            terms.append(m)
        return terms

    def _score(self, text: str) -> float:
        chinese_count = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
        if chinese_count < 1:
            return 0.0
        score = 0.4
        if 2 <= len(text) <= 8:
            score += 0.2
        if not text.isdigit() and not text.isalpha():
            score += 0.1
        return min(1.0, max(0.0, score))

    def _scrape_sources(self, topic: str) -> List[str]:
        curated = {
            "游戏圈": [
                ("上分", "排位追求：玩家追求更高排位积分时使用，是MOBA类游戏最核心的目标词之一。\n文化影响：代练、上分车队、积分战神等概念都围绕它展开。"),
                ("坐牢", "对局体验：这把打得特别憋屈、毫无游戏体验，像在坐牢一样。\n衍生说法：坐牢局、坐牢时间。"),
                ("崩了", "局势突变：原本优势或平稳的局势突然变差，常用于团战失利或节奏断裂。\n文化影响：是MOBA、FPS游戏中描述局势逆转的高频词。"),
                ("开摆了", "心态变化：放弃挣扎、随便玩了，通常在对局明显劣势或队友不配合时出现。\n衍生梗：开摆、摆了、开摆哥。"),
                ("降维打击", "实力差距：高水平玩家或阵容对低水平形成碾压式优势。\n文化影响：源自科幻概念，现被广泛用于描述任何领域的实力不对称。"),
                ("拉扯", "战术动作：通过消耗、控制距离寻找输出空间，常见于poke阵容或对线期。\n衍生梗：拉扯大师、拉扯的艺术。"),
                ("开团", "团队配合：发起团战，通常由坦克或开团型技能先手。\n文化影响：衍生出开团了、团战开启、先开哪个等战术讨论。"),
                ("带飞", "carry表现：高手带领队友获胜，队友常用此表达感谢或崇拜。\n衍生梗：带飞吗、求带飞、带飞大哥。"),
                ("Carry", "核心输出：指队伍中的主要输出位或带飞全场的玩家。\n文化影响：从英文借词，已成为游戏圈通用术语，衍生出carry全场、carry不动等。"),
                ("MVP", "评价认可：最有价值玩家，通常由数据或团队投票产生。\n文化影响：体育和电竞通用术语，引申到日常表示某件事最出彩的人。"),
                ("小丑", "自我吐槽：自己打得菜还喷人，事后被事实打脸，像小丑一样。\n文化影响：抽象文化核心词汇，从游戏圈扩散到全网，衍生出小丑竟是我自己。"),
                ("脚本", "外挂质疑：操作过于精准离谱，被怀疑开了外挂脚本。\n衍生梗：这是脚本吧、锁头脚本、自瞄脚本。"),
                ("锁头", "外挂功能：外挂自动瞄准头部，导致被锁玩家毫无体验。\n衍生梗：被锁了、锁头哥、锁头挂。"),
                ("意识", "游戏判断：对局势、位置、timing的预判能力，是高手的重要特质。\n文化影响：从游戏圈扩散，现在泛指任何领域的判断力。"),
                ("摆烂", "消极比赛：故意不好好玩、送人头或消极应对，常见于晋级赛心态爆炸。\n文化影响：从游戏圈扩散到全网，成为描述破罐子破摔的通用词。衍生梗：开摆、摆了、摆烂哥。"),
                ("超鬼", "极端数据：死亡次数远超击杀，成为团队短板。\n衍生梗：超鬼了、超鬼数据、超鬼局。"),
                ("尽力了", "甩锅用语：输了但认为个人表现没问题，暗示队友太菜。\n文化影响：游戏圈甩锅经典台词，衍生出尽力局。"),
                ("下饭", "操作拉胯：操作极其失误，像把饭喂到观众嘴里一样离谱。\n衍生梗：下饭操作、太下饭了、饭都给你喂到嘴里了。"),
                ("脑溢血操作", "离谱操作：极其降智的失误，看得观众血压飙升。\n衍生梗：脑溢血、血压升高、辣眼睛。"),
            ],
            "直播圈": [
                ("kskbl", "提醒开播：粉丝用'kskbl'提醒他人康正在直播。\n调侃娱乐：不仅粉丝，连康粉、康黑和乐子人也会使用，带有玩梗和调侃性质，非纯黑。\n衍生梗：常见搭配包括'zdjd'（真的假的）、'wkzkbl'（我靠真开播了）等，用于丰富弹幕互动。\n文化影响：已成为康相关直播间的标志性暗号。"),
                ("榜一大哥", "粉丝经济：直播间刷最多礼物的粉丝，通常享有主播特殊关注或连麦机会。\n文化影响：直播圈核心概念，衍生出榜一、榜二、榜一大哥语录等。"),
                ("家人们", "主播话术：主播对观众的亲切称呼，营造家庭感和亲近感。\n文化影响：从直播圈扩散到短视频和日常口语，成为亲切称呼的代名词。"),
                ("666", "弹幕夸奖：厉害、牛，最常见的弹幕赞美词。\n文化影响：全网通用赞美词，从直播圈扩散到所有社交媒体。"),
                ("老铁", "粉丝称呼：兄弟、铁粉，东北方言演化来的亲切称呼。\n文化影响：快手等平台主播常用，已成为东北文化输出的代表词之一。"),
                ("双击", "互动引导：主播引导观众点赞关注，类似'点个赞'。\n衍生梗：双击666、双击评论。"),
                ("刷起来", "弹幕轰炸：多发弹幕、多送礼，营造热闹氛围。\n衍生梗：刷起来家人们、弹幕刷起来。"),
                ("整活", "节目效果：搞事情、制造笑点，让直播更有意思。\n文化影响：从直播圈扩散到全网，成为制造笑点的通用动词。"),
                ("家人们谁懂啊", "共鸣强调：强调某种情绪或经历只有家人们能理解。\n衍生梗：谁懂啊、真的谁懂啊。"),
                ("破防了", "情绪失控：被戳中痛处或感动到无法控制情绪。\n文化影响：全网通用情绪表达词，从直播圈扩散到所有社交媒体。"),
                ("笑不活了", "太好笑了：笑到无法承受，形容非常搞笑。\n文化影响：全网通用，已成为最火的搞笑表达之一。"),
                ("绷不住了", "忍俊不禁：忍不住了，可以是笑也可以是哭。\n文化影响：全网通用，抽象圈和直播圈共同推火的表情/文字表达。"),
                ("下次一定", "鸽王口头禅：这次不刷/不看/不买，下次再说，常用于白嫖。\n文化影响：直播圈白嫖文化标志性台词，衍生出下次一定看、下次一定支持等。"),
                ("三连", "支持UP主：点赞、投币、收藏三连，是B站用户对优质内容的默认支持。\n文化影响：B站核心文化，衍生出求三连、三连了吗等。"),
                ("火钳刘明", "提前占楼：预测帖子会火，提前留言占前排。\n文化影响：贴吧、B站经典占楼用语，历史悠久。"),
                ("名场面", "经典片段：视频或直播中的高光时刻，值得反复观看。\n文化影响：从直播圈扩散到影视、体育等领域，成为通用词。"),
            ],
            "抽象圈": [
                ("典中典", "经典评价：经典中的经典，用于高度认可某事反复出现或极具代表性。\n衍生梗：典中典中典、太典了、典。"),
                ("急急急", "情绪调侃：太急了，形容某人或某事非常着急，常用于看热闹不嫌事大。\n衍生梗：急典、急了。"),
                ("乐", "看乐子：好笑、有意思，抽象圈用户常用的单一字回复，表达愉悦。\n文化影响：抽象圈标志性回复方式，衍生出乐子人、乐子。"),
                ("绷不住了", "忍俊不禁：忍不住了，形容笑到停不下来或某事太离谱。\n文化影响：全网通用，抽象圈和直播圈共同推火。"),
                ("润", "跑路：离开、撤退，源自'润学'，泛指逃离当前环境。\n文化影响：抽象圈核心概念，衍生出润了、润学、润出去等，已成为社会现象级词汇。"),
                ("摆烂", "故意搞砸：事情已经无法挽回，干脆放弃抵抗、故意恶化。\n文化影响：从游戏圈扩散到全网，成为破罐子破摔的通用词。"),
                ("整活", "搞事情：制造节目效果、搞怪或整人，是抽象圈的核心精神。\n文化影响：全网通用，抽象圈最具代表性的动词。"),
                ("神中神", "至高评价：太神了，形容某事或某人极其优秀、无可挑剔。\n衍生梗：神中神中神、太神了。"),
                ("纯纯", "完全是：加强语气，表示完全是某种状态，如'纯纯的弱智'。\n衍生梗：纯纯的xxx。"),
                ("出生", "道德谴责：形容人坏透了、不是人，源自'初生'谐音。\n文化影响：抽象圈最具攻击性的词汇之一，使用广泛。"),
                ("孝", "反讽：讽刺对方盲目崇拜或维护某人，如'太孝了'。\n衍生梗：孝子、太孝了、尽孝。"),
                ("乐子人", "观众身份：以看热闹、看乐子为乐的人，不站队只图一乐。\n文化影响：抽象圈核心身份认同词，衍生出乐子人、看乐子。"),
                ("答辩", "极度贬低：把某作品或某人比作大便，表示极差。\n文化影响：抽象圈标志性贬义词，常用于评价影视、游戏、言论等。"),
                ("真的假的", "难以置信：对突发消息表示惊讶或质疑，源自kskbl。\n衍生梗：zdjd、真的假的啊。"),
                ("笑死", "太好笑了：形容极度好笑，常见于弹幕或评论区。\n文化影响：全网通用搞笑表达。"),
                ("好家伙", "惊讶感叹：对意外或夸张事情的感叹，表示'厉害啊'。\n文化影响：全网通用感叹词，历史悠久。"),
                ("确实", "认同附和：确实如此，表示同意对方的观点。\n文化影响：抽象圈标志性附和词，简洁有力。"),
                ("学到了", "涨知识：获得新知识或新见解，表示受益匪浅。\n文化影响：全网通用学习表达。"),
            ],
            "电竞圈": [
                ("团灭", "团队覆灭：全队被敌方消灭，局势急转直下。\n文化影响：MOBA、FPS游戏通用术语，衍生出团灭发动机。"),
                ("基地", "核心建筑：地图中心的核心建筑，摧毁即获胜。\n衍生梗：偷家、拆基地、基地爆炸。"),
                ("偷家", "战术奇袭：趁敌方不注意偷袭对方基地，常用于劣势翻盘。\n文化影响：电竞圈经典战术术语，衍生出偷塔、偷水晶。"),
                ("counter", "克制关系：英雄或阵容之间存在克制，选counter位压制对手。\n文化影响：电竞圈专业术语，现被广泛用于描述任何领域的克制关系。"),
                ("ban", "禁用环节：ban pick环节禁用对方核心英雄，限制其发挥。\n文化影响：电竞圈通用术语，衍生出ban人、ban位。"),
                ("pick", "选择环节：选择自己使用的英雄或阵容。\n文化影响：电竞圈通用术语，与ban组成ban/pick。"),
                ("运营", "战术执行：通过资源控制、视野布置、节奏推进积累优势。\n文化影响：电竞圈核心概念，现被广泛用于描述任何领域的策略执行。"),
                ("反杀", "逆转击杀：被追杀后利用地形或技能反杀敌人。\n文化影响：电竞圈经典操作术语，衍生出丝血反杀。"),
                ("五杀", "极限操作：连续击杀5名敌方玩家，是个人能力的极致体现。\n文化影响：电竞圈最高荣誉之一，衍生出五杀时刻、五杀记录。"),
                ("超神", "连续击杀：连续击杀多人不死，处于无敌状态。\n文化影响：电竞圈经典术语，与五杀并列。"),
                ("翻盘", "劣势逆转：从大劣势情况下扭转局势赢得胜利。\n文化影响：电竞圈最激动人心的场景之一，衍生出翻盘局、绝地翻盘。"),
                ("绝地反击", "最后逆转：在几乎不可能的情况下取得胜利。\n文化影响：体育和电竞通用术语，形容逆境中的胜利。"),
                ("MVP", "最有价值：本场表现最优秀的玩家。\n文化影响：体育和电竞通用术语，引申到日常表示某件事最出彩的人。"),
            ],
            "弹幕圈": [
                ("前方高能", "精彩预警：提示接下来有高能或名场面内容，提醒观众注意。\n文化影响：B站、直播平台通用预警语，衍生出高能预警、前方高能。"),
                ("名场面", "经典片段：视频或直播中的高光时刻，值得反复观看。\n文化影响：从直播圈扩散到影视、体育等领域，成为通用词。"),
                ("下次一定", "鸽王口头禅：这次不刷/不看/不买，下次再说，常用于白嫖。\n文化影响：直播圈白嫖文化标志性台词，全网通用。"),
                ("太真实了", "共鸣感叹：说到心坎里，形容内容过于真实引起共鸣。\n文化影响：全网通用共鸣表达。"),
                ("破防了", "情绪崩溃：被戳中痛处或感动到无法控制情绪。\n文化影响：全网通用情绪表达词。"),
                ("泪目", "感动到哭：很感动，眼眶湿润。\n文化影响：全网通用感动表达。"),
                ("爷青回", "童年回忆：童年记忆中的经典出现，让人怀念不已。\n文化影响：全网通用怀旧表达，衍生出爷童回、爷青结。"),
                ("火钳刘明", "提前占楼：预测帖子会火，提前留言占前排。\n文化影响：贴吧、B站经典占楼用语，历史悠久。"),
                ("全体起立", "全体致敬：对精彩内容的最高敬意，全体观众起立鼓掌。\n文化影响：B站、直播平台经典反应用语。"),
            ],
            "二次元圈": [
                ("破防了", "情绪崩溃：被戳中痛处或感动到无法控制情绪。\n文化影响：ACG作品弹幕/评论区高频词，后扩散到全网。"),
                ("厨力", "粉丝强度：对角色/CP 的喜爱与投入程度，厨力放出即大量产出。\n衍生梗：厨力放出、厨力PK。"),
                ("ky", "读空气失败：不会看气氛、不合时宜发言，源自日语‘空気読めない’。\n文化影响：二次元圈通用，后扩散到饭圈和泛网络。"),
                ("贴贴", "亲密互动：角色或粉丝之间亲密互动，表示关系好。\n文化影响：二次元圈通用，衍生出贴贴人、贴贴现场。"),
                ("哈人", "害怕：对恐怖、雷人或冲击性内容的反应。\n文化影响：抽象圈和二次元共用，表达‘太吓人了’。"),
                ("草", "笑死：看到离谱内容时的反应，相当于笑到说不出话。\n文化影响：二次元、弹幕圈通用，早期来自‘wwwww’。"),
                ("yyds", "永远的神：极高评价，表示永远的神级存在。\n文化影响：从电竞/饭圈扩散到二次元，现为全网通用。"),
                ("awsl", "啊我死了：被可爱/感动到不行，极度情绪化表达。\n文化影响：二次元圈最具代表性的情绪表达之一。"),
            ],
            "网梗圈": [
                ("我真的会谢", "无语吐槽：对无语事件表示无奈和感谢式吐槽。\n文化影响：全网通用吐槽句式，常用于阴阳怪气。"),
                ("尊嘟假嘟", "真假质疑：模仿软萌语气质疑真假，谐音‘真的假的’。\n文化影响：短视频平台传播极广，典型网梗句式。"),
                ("退退退", "拒绝态度：用重复‘退’字表达拒绝、退让、驱离。\n文化影响：源自短视频吵架名场面，现为全网通用拒绝语。"),
                ("命运的齿轮", "宿命感叹：用‘命运的齿轮开始转动’形容人生转折。\n文化影响：短视频、社交平台热门模板文案。"),
                ("搭子", "轻社交：饭搭子、旅游搭子、健身搭子，指特定场景的轻关系同伴。\n文化影响：2023-2024年最火的社交热词之一。"),
                ("确诊为", "网络诊断：用‘确诊为xx’来调侃某种状态或人格。\n文化影响：短视频平台流行句式，如‘确诊为恋爱脑’。"),
                ("哈基米", "可爱称呼：源自日语‘蜂蜜’的空耳，现多用于称呼可爱动物或人。\n文化影响：短视频萌宠圈流行词。"),
                ("挖呀挖", "魔性BGM：源自幼教儿歌，因魔性传播成为洗脑热梗。\n文化影响：短视频平台现象级传播热梗。"),
            ],
            "职场圈": [
                ("PUA", "精神操控：上级或同事通过否定、打压、画饼等方式控制他人。\n文化影响：职场圈核心负面词，后扩散到亲密关系和互联网。"),
                ("画饼", "空头承诺：上级用未来利益激励员工，但基本不会兑现。\n文化影响：职场圈经典吐槽词，衍生出‘大饼’‘饼王’。"),
                ("内卷", "无效竞争：内部过度竞争导致全员收益下降。\n文化影响：从职场扩散到全社会，成为描述过度竞争的通用词。"),
                ("躺平", "消极抵抗：拒绝加班、拒绝攀比、降低欲望。\n文化影响：从职场扩散到全社会，成为年轻人态度的标志词。"),
                ("摸鱼", "带薪休闲：工作时间做私事，偷偷划水。\n文化影响：职场圈核心词汇，现为全网通用。"),
                ("996", "加班文化：早9晚9、每周6天的工作制度。\n文化影响：职场圈标志性制度词，引发广泛社会讨论。"),
                ("35岁危机", "年龄焦虑：35岁以上员工面临的裁员、降薪、求职困难。\n文化影响：互联网职场圈核心焦虑词，后扩散到全社会。"),
                ("PPT造车", "概念先行：只靠PPT和概念忽悠投资，缺乏实际产品。\n文化影响：泛用于科技/创业圈，讽刺空谈。"),
            ],
            "饭圈": [
                ("塌房", "人设崩塌：偶像被曝负面新闻，粉丝心理防线崩溃。\n文化影响：饭圈核心词，后扩散到主播、UP主、品牌等所有公众人物。"),
                ("路人", "非粉立场：既不是粉丝也不是黑粉的中间观众。\n文化影响：饭圈用语，现为全网通用身份词。"),
                ("氪金", "付费投入：为偶像/游戏/内容大量花钱。\n文化影响：饭圈、游戏圈通用，现为全网通用消费词。"),
                ("应援", "粉丝支持：线下线上为偶像加油、买周边、打榜。\n文化影响：饭圈核心行为词，衍生出应援色、应援棒、应援口号。"),
                ("唯粉", "单一偶像：只粉一个偶像，不磕CP、不粉团。\n文化影响：饭圈身份词，衍生出唯粉、团粉、CP粉。"),
                ("拉踩", "捧一踩一：通过贬低B来抬高A，常见于粉丝互撕。\n文化影响：饭圈互撕高频词，现为全网通用。"),
                ("路人缘", "公众好感：偶像在大众中的整体印象和好感度。\n文化影响：饭圈评价体系词，现泛化使用。"),
                ("洗广场", "舆论控制：通过大量发帖/转评赞把负面内容刷下去。\n文化影响：饭圈控评术语，后扩散到公关和品牌圈。"),
            ],
            "科技圈": [
                ("抽象", "难以理解：代码或行为过于离谱、不符合常规逻辑。\n文化影响：技术圈吐槽词，后扩散到全网抽象文化。"),
                ("回滚", "版本撤回：新版本出问题，被迫恢复旧版本。\n文化影响：开发/运维通用词，现为全网通用。"),
                ("热更新", "不停机更新：无需重启服务即可更新代码/内容。\n文化影响：游戏圈/客户端圈通用词。"),
                ("封装", "屏蔽细节：把复杂实现藏起来，只暴露简单接口。\n文化影响：开发圈通用词，现泛化用于所有领域。"),
                ("玄学", "无法解释：现象不符合预期，又找不到原因，只能归为玄学。\n文化影响：技术圈经典吐槽，后扩散到全网。"),
                ("祖传代码", "历史包袱：多年遗留、没人敢动的老代码。\n文化影响：开发圈经典自嘲词。"),
                ("996", "加班文化：早9晚9、每周6天的工作制度。\n文化影响：从互联网科技圈扩散到全社会。"),
                ("35岁危机", "年龄焦虑：35岁以上程序员面临的裁员、降薪、求职困难。\n文化影响：科技圈核心焦虑词，后扩散到全社会。"),
            ],
            "鬼畜圈": [
                ("名场面", "经典片段：视频或直播中的高光时刻，值得反复观看。\n文化影响：从直播圈扩散到影视、体育等领域，成为通用词。"),
                ("鬼畜", "视频风格：将音视频素材进行极端重组、循环、变速，制造洗脑笑点。\n文化影响：B站独有文化，后扩散到全网短视频。"),
                ("全明星", "素材集合：将多位公众人物的名场面剪辑在一起。\n文化影响：鬼畜圈经典题材类型。"),
                ("空耳", "谐音梗：把外语/歌曲听成中文谐音，用于搞笑翻译。\n文化影响：鬼畜/二次元圈通用，衍生出空耳字幕、空耳梗。"),
                ("蕉个朋友", "谐音梗：把‘交个朋友’说成‘蕉个朋友’，常用于鬼畜恶搞。\n文化影响：鬼畜圈经典谐音梗。"),
                ("大力出奇迹", "土味口号：用简单粗暴方式强行解决问题，常用于鬼畜反差。\n文化影响：鬼畜圈和抽象圈共用。"),
                ("奥利给", "加油口号：来自冬泳怪鸽，意为‘给力噢’。\n文化影响：鬼畜圈、抽象圈、直播圈共同推火。"),
                ("影 流 之 主", "鬼畜顶流：源自网红‘影流之主’的魔性舞蹈，成为鬼畜区顶流素材。\n文化影响：鬼畜圈标志性IP。"),
            ],
            "短视频圈": [
                ("家人们谁懂啊", "共鸣强调：强调某种情绪或经历只有家人们能理解。\n衍生梗：谁懂啊、真的谁懂啊。"),
                ("命运的齿轮", "宿命感叹：用‘命运的齿轮开始转动’形容人生转折。\n文化影响：短视频、社交平台热门模板文案。"),
                ("搭子", "轻社交：饭搭子、旅游搭子、健身搭子，指特定场景的轻关系同伴。\n文化影响：2023-2024年最火的社交热词之一。"),
                ("尊嘟假嘟", "真假质疑：模仿软萌语气质疑真假，谐音‘真的假的’。\n文化影响：短视频平台传播极广，典型网梗句式。"),
                ("退退退", "拒绝态度：用重复‘退’字表达拒绝、退让、驱离。\n文化影响：源自短视频吵架名场面，现为全网通用拒绝语。"),
                ("挖呀挖", "魔性BGM：源自幼教儿歌，因魔性传播成为洗脑热梗。\n文化影响：短视频平台现象级传播热梗。"),
                ("哈基米", "可爱称呼：源自日语‘蜂蜜’的空耳，现多用于称呼可爱动物或人。\n文化影响：短视频萌宠圈流行词。"),
                ("确诊为", "网络诊断：用‘确诊为xx’来调侃某种状态或人格。\n文化影响：短视频平台流行句式，如‘确诊为恋爱脑’。"),
            ],
        }.get(topic, [])
        terms = []
        seen = set()
        for term, meaning in curated:
            term = term.strip()
            if not term or term in seen:
                continue
            seen.add(term)
            terms.append((term, meaning))
        return terms

    def _fetch_hot_sources(self) -> List[dict]:
        """从公开热搜接口抓取最新热词/梗词（百度热搜 + B站热搜 + B站搜索热词 + 抖音热榜）"""
        fetched = []
        fetched.extend(self._fetch_baidu_hot())
        fetched.extend(self._fetch_bili_trending())
        fetched.extend(self._fetch_bili_search_hot())
        fetched.extend(self._fetch_douyin_hot())
        return fetched

    def _fetch_douyin_hot(self) -> List[dict]:
        try:
            url = "https://www.iesdouyin.com/web/api/v2/hotsearch/billboard/word/"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            words = [(x.get("word") or "").strip() for x in (data.get("word_list") or [])]
            return self._hot_to_candidates(words, "热搜-抖音")
        except Exception as e:
            print(f"[SlangLearner] 抖音热榜抓取失败: {e}")
            return []

    def _fetch_baidu_hot(self) -> List[dict]:
        try:
            url = "https://top.baidu.com/api/board?platform=wise&tab=realtime"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            words = []
            for card in (data.get("data", {}).get("cards") or []):
                for c in (card.get("content") or []):
                    for item in (c.get("content") or []):
                        w = (item.get("word") or "").strip()
                        if w:
                            words.append(w)
            return self._hot_to_candidates(words, "热搜-百度")
        except Exception as e:
            print(f"[SlangLearner] 百度热搜抓取失败: {e}")
            return []

    def _fetch_bili_trending(self) -> List[dict]:
        try:
            url = "https://api.bilibili.com/x/web-interface/ranking/v2?rid=0"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            titles = [(x.get("title") or "").strip() for x in (data.get("data", {}).get("list") or [])]
            return self._hot_to_candidates(titles, "热搜-B站")
        except Exception as e:
            print(f"[SlangLearner] B站热榜抓取失败: {e}")
            return []

    def _fetch_bili_search_hot(self) -> List[dict]:
        try:
            url = "https://api.bilibili.com/x/web-interface/search/square?limit=20"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            words = [(x.get("keyword") or "").strip() for x in (data.get("data", {}).get("trending", {}).get("list") or [])]
            return self._hot_to_candidates(words, "热搜-B站", base_quality=0.75)
        except Exception as e:
            print(f"[SlangLearner] B站搜索热词抓取失败: {e}")
            return []

    def _hot_to_candidates(self, raw_words: List[str], source: str, base_quality: float = 0.6) -> List[dict]:
        """把热搜原文清洗为候选词条：保留短梗词 + 书名号内容，丢弃新闻长句"""
        candidates = []
        seen = set()

        def _is_meme_word(w: str) -> bool:
            if not w or len(w) < 2 or len(w) > 6:
                return False
            if w.isdigit() or w.isalpha() or not any("\u4e00" <= c <= "\u9fff" for c in w):
                # 允许中英/中数混合（yyds、2.5次元、kskbl）
                if not re.search(r"[\u4e00-\u9fff]", w):
                    return False
            # 丢弃明显新闻句式特征
            if re.search(r"(因为|所以|导致|发生|确认|公布|发布|宣布|称|回应|报道|事件|事故|灾区|遇难|涨幅|跌幅|突破|创下|开启|迎来|持续|上演)", w):
                return False
            return True

        def _push(w: str):
            w = re.sub(r"[\u2600-\u27ff\U0001f300-\U0001faff*✦⚡🔥💥]", "", w)
            w = w.strip(" \t《》「」『』\"'“”‘’()（）[]【】{}.。，、!！?？:：;；|/\\·…~—")
            if not w or len(w) < 2 or len(w) > 6:
                return
            if w in seen:
                return
            if not _is_meme_word(w):
                return
            seen.add(w)
            candidates.append({
                "id": w,
                "meaning": "（热搜候选，待确认释义）",
                "source": source,
                "topic": source,
                "quality": base_quality,
                "learned_at": _now_iso(),
                "learned_ts": _now_ts(),
            })

        for w in raw_words:
            w = (w or "").strip()
            if not w:
                continue
            # 长句（新闻/标题）→ 提取书名号内容，丢弃正文
            if len(w) > 6:
                inner = re.findall(r"[《「『]([\w·]+)[》」』]", w)
                for sub in inner:
                    _push(sub.replace(" ", ""))
                continue
            _push(w.replace(" ", ""))

        return candidates

    def _estimate_quality(self, item: dict) -> float:
        """简单质量评估：来源越多、长度适中、不是纯英文的分数更高"""
        score = 0.5
        text = item.get("id", "")
        meaning = item.get("meaning", "")

        # 中文占比高加分
        chinese_count = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
        if chinese_count >= 2:
            score += 0.2
        # 长度适中加分
        if 2 <= len(text) <= 6:
            score += 0.1
        # 有释义加分
        if meaning and "待确认" not in meaning:
            score += 0.1
        # 不是纯数字/纯英文
        if not text.isdigit() and not text.isalpha():
            score += 0.1

        return min(1.0, max(0.0, score))

    def _clean(self, candidates: List[dict]) -> List[dict]:
        seen = set()
        out = []
        for item in candidates:
            key = item.get("id")
            if not key or key in seen:
                continue
            if not item.get("meaning"):
                continue
            # 过滤过短或过长
            if len(key) < 2 or len(key) > 10:
                continue
            seen.add(key)
            out.append(item)
        # 按质量排序，优先保留高质量
        out.sort(key=lambda x: x.get("quality", 0.5), reverse=True)
        return out[:60]


_learner = SlangLearner()


def on_load():
    print("[SlangLearner] 已加载")


def on_unload():
    print("[SlangLearner] 已卸载")


def start_learning(topics=None):
    return _learner.start_learning(topics)


def get_pending(search="", source="", min_quality=0.0):
    return _learner.get_pending(search, source, min_quality)


def inject_selected(ids):
    return _learner.inject_selected(ids)


def inject_all(min_quality=0.0):
    return _learner.inject_all(min_quality)


def reject_selected(ids):
    return _learner.reject_selected(ids)


def reject_all(min_quality=0.0):
    return _learner.reject_all(min_quality)


def get_injected(limit=100):
    return _learner.get_injected(limit)


def get_history(limit=50):
    return _learner.get_history(limit)


def get_stats():
    return _learner.get_stats()


def start_auto_learn_loop(interval_hours: float = 12.0):
    """启动后台定时自动学习线程"""
    return _learner.start_auto_learn_loop(interval_hours)


def add_manual(term: str, meaning: str, source: str = "手动输入", quality: float = 0.9) -> Dict[str, Any]:
    """手动输入新梗/模板：直接写入候选池，等机主确认注入。"""
    if not term or not meaning:
        return {"ok": False, "error": "词条与释义不能为空"}
    term = str(term).strip()
    existing_ids = {x["id"] for x in _learner.pending}
    existing_ids.update(x["id"] for x in _learner.injected)
    try:
        slang_path = os.path.join(os.path.dirname(PLUGIN_DIR), "slang_dict.json")
        existing_ids.update(_load_json(slang_path, {"entries": {}}).get("entries", {}).keys())
    except Exception:
        pass
    if term in existing_ids:
        return {"ok": False, "error": f"「{term}」已存在，无需重复添加"}
    item = {
        "id": term,
        "meaning": str(meaning).strip(),
        "source": source,
        "topic": source,
        "quality": max(0.0, min(1.0, float(quality))),
        "learned_at": _now_iso(),
        "learned_ts": _now_ts(),
        "status": "pending",
        "source_count": 1,
    }
    _learner.pending.append(item)
    _save_json(PENDING_FILE, _learner.pending)
    return {"ok": True, "added": 1, "item": item}


def web_status() -> Dict[str, Any]:
    """联网查梗环境与 DeepSeek 登录态，供 UI 显示操作提示。"""
    try:
        from .web_meme_finder import status as _web_status

        return _web_status()
    except Exception as e:
        return {"error": f"环境检测失败: {e}", "hints": [f"环境检测失败: {e}"]}


def web_enrich(games=None, batch=5, fallback=True, template=""):
    """用 DeepSeek 网页版强制联网查指定游戏「热且出圈」的梗，写入 pending 池。

    games: 游戏名列表，默认 ['明日方舟']；batch: 每游戏条数。
    fallback: 联网失败时回退离线 _llm_enrich。
    template: 自定义提问模板，占位符 {game}/{batch}/{want}/{banned}；留空用默认模板。
    返回 {"ok", "added", "items", "errors"}。
    """
    import time as _time

    games = games or ["明日方舟"]
    added = 0
    items_all = []
    errors = []
    for game in games[:5]:
        candidates = []
        try:
            from .web_meme_finder import enrich as _web_enrich

            raw = _web_enrich(game, batch, [], template)
            for it in raw:
                key = (it.get("id") or "").strip()
                if key and key not in [c["id"] for c in candidates]:
                    candidates.append(
                        {
                            "id": key,
                            "meaning": it.get("meaning", "").strip(),
                            "source": game,
                            "topic": game,
                        }
                    )
        except Exception as e:
            if not fallback:
                errors.append(f"{game}:{e}")
                continue
            try:
                candidates = _learner._llm_enrich(game, batch)
            except Exception as e2:
                errors.append(f"{game}:{e} / {e2}")
                continue

        cleaned = _learner._clean(candidates)
        existing_ids = {x["id"] for x in _learner.pending}
        existing_ids.update(x["id"] for x in _learner.injected)
        try:
            slang_path = os.path.join(os.path.dirname(PLUGIN_DIR), "slang_dict.json")
            existing_ids.update(_load_json(slang_path, {"entries": {}}).get("entries", {}).keys())
        except Exception:
            pass
        for item in cleaned:
            key = item.get("id")
            if not key or key in existing_ids:
                continue
            item.setdefault("status", "pending")
            item.setdefault("learned_at", _now_iso())
            item.setdefault("learned_ts", _now_ts())
            item.setdefault("quality", _learner._estimate_quality(item))
            item.setdefault("source_count", 1)
            _learner.pending.append(item)
            existing_ids.add(key)
            added += 1
            items_all.append(item)
        if added:
            _save_json(PENDING_FILE, _learner.pending)
    if added:
        _learner.history.append({
            "at": _time.strftime("%Y-%m-%dT%H:%M:%S"),
            "topics": games,
            "added": added,
            "items": [i["id"] for i in items_all],
        })
        _save_json(HISTORY_FILE, _learner.history)
    return {"ok": True, "added": added, "items": items_all, "errors": errors}


# ---------------------------------------------------------------- 回复模板
_REPLIES_PATH = os.path.join(os.path.dirname(PLUGIN_DIR), "slang_replies.json")


def get_reply_templates(term: str = "") -> Dict[str, Any]:
    """读取回复模板（词 → 回复列表）。term 为空返回全部。"""
    data = _load_json(_REPLIES_PATH, {"entries": {}})
    entries = data.get("entries", {}) if isinstance(data, dict) else {}
    if term:
        return {"term": term, "replies": entries.get(term, [])}
    return {"entries": entries}


def set_reply_template(term: str, replies) -> Dict[str, Any]:
    """设置某个词的回复模板。replies 为字符串列表（逗号/换行/竖线分隔自动拆分）。"""
    if not term:
        return {"ok": False, "error": "词条不能为空"}
    term = str(term).strip()
    if isinstance(replies, str):
        parts = [p.strip() for p in re.split(r"[,，\n/|]", replies) if p.strip()]
    elif isinstance(replies, (list, tuple)):
        parts = [str(p).strip() for p in replies if str(p).strip()]
    else:
        parts = []
    data = _load_json(_REPLIES_PATH, {"entries": {}})
    entries = data.get("entries", {}) if isinstance(data, dict) else {}
    if parts:
        entries[term] = parts
    else:
        entries.pop(term, None)
    _save_json(_REPLIES_PATH, {"entries": entries})
    return {"ok": True, "term": term, "replies": parts}


def delete_reply_template(term: str) -> Dict[str, Any]:
    return set_reply_template(term, [])


def import_reply_templates(mapping) -> Dict[str, Any]:
    """批量导入回复模板 {词: 回复列表}。"""
    if not isinstance(mapping, dict):
        return {"ok": False, "error": "格式错误"}
    data = _load_json(_REPLIES_PATH, {"entries": {}})
    entries = data.get("entries", {}) if isinstance(data, dict) else {}
    count = 0
    for term, replies in mapping.items():
        term = str(term or "").strip()
        if not term:
            continue
        if isinstance(replies, str):
            parts = [p.strip() for p in re.split(r"[,，\n/|]", replies) if p.strip()]
        else:
            parts = [str(p).strip() for p in (replies or []) if str(p).strip()]
        entries[term] = parts
        count += 1
    _save_json(_REPLIES_PATH, {"entries": entries})
    return {"ok": True, "imported": count}


# ---------------------------------------------------------------- 提问模板库
_PROMPT_TEMPLATES_FILE = os.path.join(DATA_DIR, "prompt_templates.json")

_DEFAULT_PROMPT_TEMPLATES = {
    "default": {
        "name": "默认模板",
        "template": (
            "今天2026年8月。请开启联网搜索，查询游戏【{game}】里最热且最出圈的梗/黑话/名场面 "
            "{want} 个。\n"
            "标准：必须是这个游戏里真实存在、够火、而且出圈的（圈外普通网友也常拿来开玩笑/刷弹幕/当表情包的），"
            "不要求最新，够热够出圈就行；不要编造，不要那种只有老玩家才知道的小众梗；避开以下已收录词：{banned}\n"
            "直接输出严格 JSON 数组，不要任何其他文字。每个元素："
            '{"id":"梗词","meaning":"意思。含使用场景、来源平台、在对局/社区里的用法、文化影响。"}\n'
            "请务必只输出一个纯 JSON 数组，不要 markdown 代码块，不要在 JSON 内添加任何编号、引用标记、换行符。"
        ),
    },
    "hardcore": {
        "name": "硬核玩家向 · 只收录圈内黑话",
        "template": (
            "请开启联网搜索，只查询【{game}】这款游戏的核心玩家圈子真正高频使用的黑话/缩写/名场面共 "
            "{want} 个。\n"
            "不要求出圈，但必须是圈内人天天在用的专业黑话（如技能缩写、装备黑话、指挥术语）；"
            "不要泛泛的流行梗。避开已收录词：{banned}\n"
            "输出严格 JSON 数组：{\"id\":\"词\",\"meaning\":\"圈内具体用法\"}，只要 JSON。"
        ),
    },
    "memes_fresh": {
        "name": "最新热梗 · 出圈传播向",
        "template": (
            "请开启联网搜索，找出最近 1-2 个月【{game}】玩家圈最容易出圈的梗/表情包/语录共 "
            "{want} 个。\n"
            "标准：时间新、传播快、圈外也能看懂；给出梗的由来与社区用法。避开已收录词：{banned}\n"
            "输出严格 JSON 数组：{\"id\":\"梗词\",\"meaning\":\"来源+用法+传播\"}，只要 JSON。"
        ),
    },
}


def get_prompt_templates() -> Dict[str, Any]:
    data = _load_json(_PROMPT_TEMPLATES_FILE, {})
    merged = dict(_DEFAULT_PROMPT_TEMPLATES)
    merged.update(data)
    return {"templates": merged}


def set_prompt_template(key: str, name: str, template: str) -> Dict[str, Any]:
    if not key or not template:
        return {"ok": False, "error": "模板 key 与内容不能为空"}
    key = str(key).strip()
    data = _load_json(_PROMPT_TEMPLATES_FILE, {})
    data[key] = {"name": (name or key).strip(), "template": template}
    _save_json(_PROMPT_TEMPLATES_FILE, data)
    return {"ok": True, "key": key}


def delete_prompt_template(key: str) -> Dict[str, Any]:
    if key in _DEFAULT_PROMPT_TEMPLATES:
        return {"ok": False, "error": f"内置模板「{key}」不可删除"}
    data = _load_json(_PROMPT_TEMPLATES_FILE, {})
    data.pop(key, None)
    _save_json(_PROMPT_TEMPLATES_FILE, data)
    return {"ok": True, "deleted": key}
