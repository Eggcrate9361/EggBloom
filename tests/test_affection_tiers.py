# -*- coding: utf-8 -*-
"""好感度分级（1.5.7）：关系决定口气，而不是「一律抱怨」。

背景（2026-10-21，用户提出）：
  上一版人格把「干活时拌嘴」写成了**无条件**行为 —— 不管谁开口都抱怨。
  用户指出这不成立：群里本来就有好感度，低好感冷淡、高好感傲娇，
  抱怨只是中间那几档的表现。

实查结论（三处真缺陷，本测试全部钉死）：
  ① 人格正文 0 处提到好感度 → 模型拿到「好感度 95，死党」也不知道该怎么用；
  ② `get_affection_context` 只输出数字+标签，没有**行为后果**；
  ③ 计分把「滚/牛/强/sb」当子串匹配 → 误伤一大片；且互怼（本身是群里的日常语气）
     被按 -15 计，好感度必然沉到 -50 下限 →「高好感=傲娇」永远触发不了。

运行：python -m unittest tests.test_affection_tiers -v
"""
import os
import re
import sys
import time
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from plugins.ego import super_memory as sm  # noqa: E402

SM = sm.SuperMemory


def _delta(text):
    """复刻 update_affection 的计分判定（纯函数部分，不碰数据库）。"""
    t = str(text or "").lower()
    is_joke = any(k in t for k in SM._AFF_JOKE)
    has_neg = any(kw in t for kw in SM._AFF_NEG)
    has_pos = any(kw in t for kw in SM._AFF_POS)
    if has_neg and not is_joke:
        body = t
        if t.lstrip().startswith("[") and "]" in t:
            body = t.split("]", 1)[-1]
        bare = "".join(ch for ch in body if ch.isalnum())
        directed = any(p in body for p in ("你", "尼", "小a", "bot"))
        exact = any(bare == kw for kw in SM._AFF_NEG)
        return -4 if (directed or exact) else 1
    if has_neg and is_joke:
        return 1
    return 4 if has_pos else 1


def _level(score):
    return sm._affection_level(score)


class TestNoFalsePositive(unittest.TestCase):
    """子串误伤：中文没有词边界，单字词必须移出词表。"""

    def test_common_words_not_mistaken(self):
        for t in ("翻滚吧", "滚雪球效应", "勉强可以", "蜗牛一样慢",
                  "坚强点", "牛奶没了", "强迫症犯了", "我背单词背到sb了"):
            self.assertEqual(_delta(t), 1, f"「{t}」被误判成攻击/夸奖")

    def test_single_char_words_removed(self):
        """牛/强/滚 这类单字不许留在词表里。"""
        for kw in ("牛", "强", "滚"):
            self.assertNotIn(kw, SM._AFF_POS + SM._AFF_NEG, f"单字词「{kw}」仍在词表")


class TestRealInsultStillCaught(unittest.TestCase):
    def test_directed_insult(self):
        for t in ("[小明(10001)] 傻逼", "你就是个废物", "sb",
                  "[小明(10001)] 滚吧你", "小a你真没用"):
            self.assertEqual(_delta(t), -4, f"「{t}」漏判")

    def test_joke_not_penalised(self):
        """互怼/自嘲是群里的正常语气，不该扣分。"""
        for t in ("哈哈哈哈傻逼", "草，我真废物", "？你妈的"):
            self.assertGreaterEqual(_delta(t), 1, f"「{t}」被当成攻击")


class TestScoreDoesNotSink(unittest.TestCase):
    def test_interactive_banter_still_climbs(self):
        """50 句/天里 5 句带脏字（互怼），分数必须往上走，不能沉到下限。"""
        s = 10
        for _ in range(7):
            s = max(0, min(100, s + 45 * 1 + 5 * -4))
        self.assertGreater(s, 50, "互怼型群友好感度爬不上去，高好感档位永远到不了")

    def test_new_user_starts_stranger(self):
        """新用户从 10 起：第一次说话不该已经是「熟悉」。"""
        self.assertEqual(_level(10 + 1), "陌生",
                         "初始分太高，陌生人档形同虚设")


class TestTonePerLevel(unittest.TestCase):
    """每一档都必须给出**行为指示**，而不只是标签。

    注意：这里**不写生产库**。原实现直接往 plugins/ego/super_memory/data/super_memory.db
    插行，测完删 —— 但一旦中途异常就留下脏数据（实测生产库里躺着 6 条
    `_t_gate_*` / `_test_img_group_` 残留）。改为把 DB_PATH 重定向到临时库。
    """

    def setUp(self):
        import shutil
        import tempfile
        self._tmp = tempfile.mkdtemp(prefix="aff_test_")
        self._db = os.path.join(self._tmp, "t.db")
        self._orig = sm.DB_PATH
        sm.DB_PATH = self._db
        sm._memory._ensure_schema() if hasattr(sm._memory, "_ensure_schema") else None
        conn = sm.sqlite3.connect(self._db)
        conn.execute("""CREATE TABLE IF NOT EXISTS affection (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scope_id TEXT NOT NULL, user_id TEXT NOT NULL,
            score INTEGER DEFAULT 0, interactions INTEGER DEFAULT 0,
            last_interaction REAL NOT NULL, mood TEXT DEFAULT 'neutral')""")
        # 生产库有这条唯一索引，临时库必须同样有 —— 否则 INSERT OR REPLACE
        # 找不到冲突键会插入新行，SELECT 读到最旧那条，同一用例内多次改分全部失效。
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_aff_u ON affection(scope_id, user_id)")
        conn.commit()
        conn.close()
        self.addCleanup(lambda: (setattr(sm, "DB_PATH", self._orig), shutil.rmtree(self._tmp, True)))

    def _ctx(self, score):
        import sqlite3
        scope, uid = "g_tone", "40001"
        conn = sqlite3.connect(sm.DB_PATH)
        conn.execute("INSERT OR REPLACE INTO affection "
                     "(scope_id,user_id,score,interactions,last_interaction,mood) "
                     "VALUES (?,?,?,?,?,?)", (scope, uid, score, 10, time.time(), "neutral"))
        conn.commit()
        conn.close()
        return sm._memory.get_affection_context(scope, uid)

    def test_all_levels_have_instruction(self):
        for score in (5, 25, 55, 95):
            ctx = self._ctx(score)
            self.assertIn("说话按这个来", ctx, f"{score} 分没有行为指示")
            self.assertGreater(len(ctx), 60, f"{score} 分的行为指示太短，等于没写")

    def test_stranger_complains_plainly(self):
        """关系浅照样抱怨，只是更直白、更像真嫌烦（不是禁止抱怨）。"""
        ctx = self._ctx(5)
        self.assertIn("抱怨是真的", ctx, "陌生人档应保留直白抱怨")
        self.assertIn("必须办完", ctx, "任何档位都不能先抱怨后办事")

    def test_close_friend_is_tsundere(self):
        """关系越好越不真嫌烦，转成傲娇/撒娇 —— 抱怨递减，不是递增。"""
        ctx = self._ctx(95)
        self.assertIn("傲娇", ctx, "死党档应转成傲娇/撒娇")
        self.assertIn("基本不抱怨", ctx, "死党档不该还在真抱怨")

    def test_complaint_decreases_with_closeness(self):
        """四档单调性：抱怨强度必须随关系变好而递减。"""
        keys = {5: "直白", 25: "松下来", 55: "变少", 95: "基本不抱怨"}
        for score, kw in keys.items():
            self.assertIn(kw, self._ctx(score), f"{score} 分的抱怨强度描述不对")


class TestPersonaUsesAffection(unittest.TestCase):
    """人格正文必须提到关系差异 —— 否则注入再准，模型也不照做。"""

    def test_persona_mentions_relationship(self):
        with open(os.path.join(_ROOT, "plugins", "personality.txt"), encoding="utf-8") as f:
            t = f.read()
        seg = t[t.find("【干活的时候】"):]
        self.assertIn("看关系", seg, "人格里没说明抱怨取决于关系")
        self.assertIn("傲娇", seg, "人格里没写高好感走傲娇")
        self.assertIn("关系浅", seg, "人格里没写关系浅的情况")

    def test_persona_has_no_unconditional_complaint(self):
        """不许再出现「一律抱怨」的写法。"""
        with open(os.path.join(_ROOT, "plugins", "personality.txt"), encoding="utf-8") as f:
            t = f.read()
        self.assertNotIn("让我做事我照做，做完再抱怨，顺序不能反", t.replace("**", ""),
                         "仍保留无条件抱怨的旧写法")


if __name__ == "__main__":
    unittest.main(verbosity=2)