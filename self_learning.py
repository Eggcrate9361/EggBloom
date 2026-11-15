#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
QQ Bot 自主学习模块 v1
从 AstrBot 自主学习插件提取核心逻辑，轻量集成到现有 qq_bot.py

核心功能：
1. 消息收集与存储（SQLite）
2. 表达模式学习（场景→表达 映射）
3. 群组黑话挖掘
4. 好感度系统
5. 动态人格更新（自动备份 + 增量追加）
"""

import os, time, sqlite3, threading, sys
from datetime import datetime

# ── 配置 ──────────────────────────────────
if getattr(sys, 'frozen', False):
    _SCRIPT_DIR = os.path.dirname(sys.executable)
else:
    _SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_DIR = os.path.join(_SCRIPT_DIR, "self_learning_db")
DB_PATH = os.path.join(DB_DIR, "self_learning.db")
# ★ 2026-10-27 修正：原来指向**仓库根**的 personality.txt —— 那个文件不存在，
#   于是 backup() 直接 return False、apply_update() 抛 FileNotFoundError，
#   **人格自学习写回整条链是死的**（读侧早已统一到 plugins/personality.txt，
#   写侧漏了三处，核查报告第 2 项）。
#   权威路径就是 bot_core.PERSONA_CANONICAL，值相同：
#     <脚本目录>/plugins/personality.txt
PERSONALITY_FILE = os.path.join(_SCRIPT_DIR, "plugins", "personality.txt")
# 备份目录跟着人格文件走 —— 备份放 plugins/personality_versions/，
#   不再往仓库根 personality_versions/ 写（那是运行态，不该在仓库里）
PERSONALITY_BACKUP_DIR = os.path.join(_SCRIPT_DIR, "plugins", "personality_versions")

# 学习参数
MIN_MESSAGES_FOR_LEARNING = 10       # 最少消息数才触发学习
LEARNING_INTERVAL_SECONDS = 3600 * 2  # 自动学习间隔（2小时）
EXPRESSION_DECAY_DAYS = 7             # 表达模式衰减天数
MAX_EXPRESSIONS = 30                  # 最多保留的表达模式
JARGON_MIN_COUNT = 3                  # 黑话最小出现次数
AFFECTION_MAX_USER = 100             # 单用户好感度上限
AFFECTION_MAX_GROUP = 250            # 群组总好感度上限

os.makedirs(DB_DIR, exist_ok=True)
os.makedirs(PERSONALITY_BACKUP_DIR, exist_ok=True)

# ── 日志 ──────────────────────────────────
def sl_log(msg):
    try:
        import sys
        ts = datetime.now().strftime("%H:%M:%S")
        if getattr(sys, 'frozen', False):
            # PyInstaller/打包环境：写入数据目录日志
            try:
                from data_store import get_log_dir
                log_dir = get_log_dir()
            except Exception:
                log_dir = os.path.join(os.path.expanduser("~"), "AppData", "Local", "EggBloom", "logs")
                os.makedirs(log_dir, exist_ok=True)
            log_path = os.path.join(log_dir, "self_learn.log")
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"[{ts}] {msg}\n")
        else:
            print(f"[SelfLearn] [{ts}] {msg}")
    except Exception:
        pass


# ═══════════════════════════════════════════
#  数据库初始化
# ═══════════════════════════════════════════

def get_db():
    """获取数据库连接，自动初始化表"""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    _init_tables(conn)
    return conn


def _init_tables(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            text TEXT NOT NULL,
            timestamp REAL NOT NULL,
            learned INTEGER DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_messages_group ON messages(group_id, learned);
        CREATE INDEX IF NOT EXISTS idx_messages_user ON messages(group_id, user_id);
        
        CREATE TABLE IF NOT EXISTS expressions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id TEXT NOT NULL,
            scene TEXT NOT NULL,
            expression TEXT NOT NULL,
            quality REAL DEFAULT 0.5,
            count INTEGER DEFAULT 1,
            created_at REAL NOT NULL,
            last_seen REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_expr_group ON expressions(group_id);
        
        CREATE TABLE IF NOT EXISTS jargon (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id TEXT NOT NULL,
            word TEXT NOT NULL,
            meaning TEXT NOT NULL DEFAULT '',
            count INTEGER DEFAULT 1,
            last_seen REAL NOT NULL
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_jargon_word ON jargon(group_id, word);
        
        CREATE TABLE IF NOT EXISTS affection (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            score INTEGER DEFAULT 0,
            interactions INTEGER DEFAULT 0,
            last_interaction REAL NOT NULL,
            mood TEXT DEFAULT 'neutral'
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_affection_user ON affection(group_id, user_id);
        
        CREATE TABLE IF NOT EXISTS persona_updates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id TEXT NOT NULL,
            content TEXT NOT NULL,
            applied INTEGER DEFAULT 0,
            created_at REAL NOT NULL
        );
    """)
    conn.commit()


# ═══════════════════════════════════════════
#  消息收集器
# ═══════════════════════════════════════════

class MessageCollector:
    """收集并存储群聊消息"""
    
    def __init__(self):
        self._last_learn_time = {}  # {group_id: timestamp}
    
    def collect(self, group_id, user_id, text):
        """存储一条消息。group_id 为字符串，user_id 为字符串"""
        if not text or len(text) < 5:
            return
        conn = None
        try:
            conn = get_db()
            conn.execute(
                "INSERT INTO messages (group_id, user_id, text, timestamp) VALUES (?, ?, ?, ?)",
                (str(group_id), str(user_id), text, time.time())
            )
            conn.commit()
        except Exception as e:
            sl_log(f"消息存储失败: {e}")
        finally:
            if conn:
                try: conn.close()
                except Exception: pass
    
    def get_unlearned_count(self, group_id):
        """获取未学习的消息数量"""
        try:
            conn = get_db()
            cur = conn.execute(
                "SELECT COUNT(*) FROM messages WHERE group_id=? AND learned=0",
                (str(group_id),)
            )
            count = cur.fetchone()[0]
            conn.close()
            return count
        except Exception:
            return 0
    
    def get_unlearned_messages(self, group_id, limit=200):
        """获取未学习的消息"""
        try:
            conn = get_db()
            cur = conn.execute(
                "SELECT id, user_id, text FROM messages WHERE group_id=? AND learned=0 ORDER BY id LIMIT ?",
                (str(group_id), limit)
            )
            rows = cur.fetchall()
            conn.close()
            return rows
        except Exception:
            return []
    
    def mark_learned(self, msg_ids):
        """标记消息为已学习"""
        if not msg_ids:
            return
        try:
            conn = get_db()
            conn.executemany(
                "UPDATE messages SET learned=1 WHERE id=?",
                [(mid,) for mid in msg_ids]
            )
            conn.commit()
            conn.close()
        except Exception as e:
            sl_log(f"标记已学习失败: {e}")
    
    def should_learn(self, group_id):
        """判断是否应该触发学习"""
        count = self.get_unlearned_count(group_id)
        if count < MIN_MESSAGES_FOR_LEARNING:
            return False
        last = self._last_learn_time.get(str(group_id), 0)
        if time.time() - last < LEARNING_INTERVAL_SECONDS:
            return False
        return True


# ═══════════════════════════════════════════
#  表达模式学习器
# ═══════════════════════════════════════════

class ExpressionLearner:
    """学习群聊中的表达模式：场景→表达 映射"""
    
    # 常见场景关键词
    SCENE_PATTERNS = {
        "赞同": ["确实", "没错", "对的", "同意", "有道理", "真相", "正解", "支持"],
        "反对": ["不对", "不是", "错了", "不同意", "反对", "离谱", "胡扯"],
        "感谢": ["谢谢", "多谢", "感谢", "爱了", "好人", "辛苦了"],
        "拒绝": ["不了", "不用", "算了", "不要", "下次一定", "婉拒"],
        "惊讶": ["卧槽", "我去", "天哪", "吓人", "离谱", "震惊"],
        "开心": ["哈哈", "笑死", "乐", "好玩", "好笑", "开心"],
        "无语": ["。。。", "无语", "……", "nb", "逆天"],
        "问候": ["早", "晚上好", "来了", "在吗", "出来"],
        "告别": ["走了", "拜拜", "再见", "先撤", "溜了", "下了"],
        "安慰": ["没事", "别急", "加油", "会好的", "抱抱"],
    }
    
    def __init__(self):
        pass
    
    def extract_expressions(self, messages):
        """从消息列表中提取表达模式。messages: [(id, user_id, text), ...]

        ⚠ 2026-10-19 修：此前用**裸子串**判场景，等于给随机句子硬贴标签：
            「关不了」          命中 拒绝 的 `不了`   → 存成"拒绝时常用"
            「你不是要关机吗」  命中 反对 的 `不是`   → 存成"反对时常用"
            「这猫笑得比早上好还瘆人」命中 问候 的 `早` → 存成"问候时常用"
        结果 personality.txt 里 5 条"群表达风格"有 3 条标着"问候时该群常用"，
        全是风马牛不相及的句子。

        现在要求关键词出现在**句首或标点之后** —— 学的才是"这个群赞同/拒绝时
        怎么开口"，而不是"这句话里恰好有这个字"。

        另加三道过滤（都是实测见过的脏数据形态）：
          · 含 `[` `]` `【】` `@` → 是引用注记/转发块，不是人话；
          · 以问号结尾 → 那是提问，不是表达习惯；
          · 去掉首尾 @ 与空白后不足 3 字。
        """
        new_expressions = []
        seen = set()
        for msg_id, user_id, text in messages:
            if not text:
                continue
            text_clean = str(text).strip()
            if len(text_clean) < 3:
                continue
            # 只取较短的文本（长消息是叙述不是表达）
            if len(text_clean) > 30:
                continue
            # 引用注记/转发块：整段都是程序拼的，不是任何人的发言
            if any(ch in text_clean for ch in "[]【】"):
                continue
            if text_clean.lstrip("@").strip() != text_clean:
                continue
            # 提问不是表达习惯
            if text_clean.endswith(("？", "?")):
                continue
            # 匹配场景（关键词必须在句首或标点之后）
            for scene, keywords in self.SCENE_PATTERNS.items():
                for kw in keywords:
                    if self._starts_phrase(text_clean, kw):
                        expr = text_clean.strip()
                        if expr not in seen:
                            seen.add(expr)
                            new_expressions.append({
                                "scene": scene,
                                "expression": expr,
                                "quality": 0.6,  # 初始质量
                            })
                        break  # 一个表达只归入一个场景
        return new_expressions

    #: 断句符 —— 关键词出现在这些字符之后，才算"句子的开头"
    _BOUNDARY = set("，。！？；：、,.!?;: \t\n（(【[「『\"'“”‘’~～")

    @classmethod
    def _starts_phrase(cls, text: str, kw: str) -> bool:
        """`kw` 是否出现在句首或某个断句符之后（而不是嵌在词中间）。

        例（关键词 `不了`，场景"拒绝"）：
            「关不了」          → False（`不` 前面是"关"，嵌在词里）
            「不了，算了」      → True（句首）
            「行吧，不了」      → True（逗号之后）
        """
        if not kw:
            return False
        i = text.find(kw)
        while i >= 0:
            if i == 0 or text[i - 1] in cls._BOUNDARY:
                return True
            i = text.find(kw, i + 1)
        return False
    
    def save_expressions(self, group_id, expressions):
        """保存表达模式到数据库"""
        if not expressions:
            return 0
        try:
            conn = get_db()
            now = time.time()
            saved = 0
            for expr in expressions:
                # 检查是否已存在
                cur = conn.execute(
                    "SELECT id, count FROM expressions WHERE group_id=? AND expression=?",
                    (str(group_id), expr["expression"])
                )
                row = cur.fetchone()
                if row:
                    # 更新计数和最后出现时间
                    conn.execute(
                        "UPDATE expressions SET count=count+1, last_seen=?, quality=MIN(quality+0.02, 1.0) WHERE id=?",
                        (now, row[0])
                    )
                else:
                    conn.execute(
                        "INSERT INTO expressions (group_id, scene, expression, quality, count, created_at, last_seen) VALUES (?, ?, ?, ?, 1, ?, ?)",
                        (str(group_id), expr["scene"], expr["expression"], expr["quality"], now, now)
                    )
                    saved += 1
            # 清理超过上限的旧表达
            conn.execute("""
                DELETE FROM expressions WHERE group_id=? AND id NOT IN (
                    SELECT id FROM expressions WHERE group_id=? ORDER BY quality DESC, last_seen DESC LIMIT ?
                )
            """, (str(group_id), str(group_id), MAX_EXPRESSIONS))
            conn.commit()
            conn.close()
            return saved
        except Exception as e:
            sl_log(f"保存表达模式失败: {e}")
            return 0
    
    def get_context_for_llm(self, group_id):
        """获取当前表达模式上下文，注入 LLM prompt"""
        conn = None
        try:
            conn = get_db()
            now = time.time()
            decay_sec = EXPRESSION_DECAY_DAYS * 86400
            cur = conn.execute(
                """SELECT scene, expression, quality, last_seen 
                   FROM expressions 
                   WHERE group_id=? 
                   ORDER BY quality DESC, last_seen DESC 
                   LIMIT 10""",
                (str(group_id),)
            )
            rows = cur.fetchall()
            
            if not rows:
                return ""
            
            lines = []
            for scene, expr, quality, last_seen in rows:
                age_days = (now - last_seen) / 86400
                decayed = quality * max(0, (decay_sec - (now - last_seen)) / decay_sec)
                if decayed < 0.2:
                    continue
                lines.append(f"  [{scene}] → 「{expr}」")
            
            if not lines:
                return ""
            return "【群聊表达模式】本群常用的表达方式（可参考，别照抄）：\n" + "\n".join(lines)
        except Exception:
            return ""
        finally:
            if conn:
                try: conn.close()
                except Exception: pass


# ═══════════════════════════════════════════
#  黑话挖掘器
# ═══════════════════════════════════════════

class JargonMiner:
    """自动发现群组黑话/特定用语"""
    
    def __init__(self):
        pass
    
    def extract_candidates(self, messages):
        """从消息中提取候选黑话词"""
        # 统计词频
        word_count = {}
        for msg_id, user_id, text in messages:
            if not text:
                continue
            # 提取 2-4 字中文短词、特殊符号组合
            import re
            # 匹配中文短词组（2-4字）和非纯数字/英文的短词
            candidates = re.findall(r'[\u4e00-\u9fa5]{2,4}', text)
            for w in candidates:
                word_count[w] = word_count.get(w, 0) + 1
        
        # 过滤：出现 >= JARGON_MIN_COUNT 的词
        return [(w, c) for w, c in word_count.items() if c >= JARGON_MIN_COUNT]
    
    def save_jargon(self, group_id, candidates):
        """保存黑话候选词"""
        if not candidates:
            return 0
        try:
            conn = get_db()
            now = time.time()
            saved = 0
            for word, count in candidates:
                cur = conn.execute(
                    "SELECT id FROM jargon WHERE group_id=? AND word=?",
                    (str(group_id), word)
                )
                if cur.fetchone():
                    conn.execute(
                        "UPDATE jargon SET count=?, last_seen=? WHERE group_id=? AND word=?",
                        (count, now, str(group_id), word)
                    )
                else:
                    conn.execute(
                        "INSERT INTO jargon (group_id, word, meaning, count, last_seen) VALUES (?, ?, '', ?, ?)",
                        (str(group_id), word, count, now)
                    )
                    saved += 1
            conn.commit()
            conn.close()
            return saved
        except Exception as e:
            sl_log(f"保存黑话失败: {e}")
            return 0
    
    def get_context_for_llm(self, group_id):
        """获取黑话解释上下文"""
        try:
            conn = get_db()
            cur = conn.execute(
                "SELECT word, meaning, count FROM jargon WHERE group_id=? AND meaning != '' ORDER BY count DESC LIMIT 5",
                (str(group_id),)
            )
            rows = cur.fetchall()
            conn.close()
            if not rows:
                return ""
            lines = [f"  「{w}」: {m}" for w, m, c in rows]
            return "【群组黑话】已知的群内特定用语：\n" + "\n".join(lines)
        except Exception:
            return ""


# ═══════════════════════════════════════════
#  好感度管理器
# ═══════════════════════════════════════════

class AffectionManager:
    """追踪用户互动，管理好感度"""
    
    # 互动类型与分值
    INTERACTION_SCORES = {
        "praise": 8,      # 称赞/正面反馈
        "reply": 3,       # 普通回复
        "mention": 5,     # @ 或主动提到
        "help": 6,        # 求助
        "share": 4,       # 分享
        "insult": -15,    # 攻击/辱骂
        "ignore": -2,     # 被无视
    }
    
    # 正面关键词
    POSITIVE_KEYWORDS = ["哈哈", "笑死", "好棒", "厉害", "谢谢", "爱了", "好人", "优秀", "nb", "真棒", "牛", "强", "不错"]
    # 负面关键词
    NEGATIVE_KEYWORDS = ["垃圾", "废物", "傻逼", "sb", "脑残", "菜鸡", "没用", "恶心"]
    
    def __init__(self):
        pass
    
    def process_interaction(self, group_id, user_id, text):
        """处理一条互动消息，更新好感度"""
        if not text:
            return
        
        # 判断互动类型
        interaction_type = self._classify_interaction(text)
        score = self.INTERACTION_SCORES.get(interaction_type, 1)
        
        try:
            conn = get_db()
            now = time.time()
            cur = conn.execute(
                "SELECT score, interactions FROM affection WHERE group_id=? AND user_id=?",
                (str(group_id), str(user_id))
            )
            row = cur.fetchone()
            
            if row:
                new_score = max(-50, min(AFFECTION_MAX_USER, row[0] + score))
                conn.execute(
                    "UPDATE affection SET score=?, interactions=interactions+1, last_interaction=? WHERE group_id=? AND user_id=?",
                    (new_score, now, str(group_id), str(user_id))
                )
            else:
                new_score = max(0, min(AFFECTION_MAX_USER, 20 + score))  # 新用户初始20分
                conn.execute(
                    "INSERT INTO affection (group_id, user_id, score, interactions, last_interaction) VALUES (?, ?, ?, 1, ?)",
                    (str(group_id), str(user_id), new_score, now)
                )
            
            # 检查群组总分上限
            total = conn.execute(
                "SELECT SUM(score) FROM affection WHERE group_id=?",
                (str(group_id),)
            ).fetchone()[0] or 0
            
            if total > AFFECTION_MAX_GROUP:
                # 衰减所有用户的好感度（等比缩放）
                scale = AFFECTION_MAX_GROUP / total
                conn.execute(
                    "UPDATE affection SET score = CAST(score * ? AS INTEGER) WHERE group_id=?",
                    (scale, str(group_id))
                )
            
            conn.commit()
            conn.close()
        except Exception as e:
            sl_log(f"更新好感度失败: {e}")
    
    def _classify_interaction(self, text):
        """根据文本内容分类互动类型"""
        for kw in self.NEGATIVE_KEYWORDS:
            if kw in text:
                return "insult"
        for kw in self.POSITIVE_KEYWORDS:
            if kw in text:
                return "praise"
        return "reply"
    
    def get_affection(self, group_id, user_id):
        """获取指定用户好感度"""
        try:
            conn = get_db()
            cur = conn.execute(
                "SELECT score, interactions FROM affection WHERE group_id=? AND user_id=?",
                (str(group_id), str(user_id))
            )
            row = cur.fetchone()
            conn.close()
            return {"score": row[0], "interactions": row[1]} if row else {"score": 0, "interactions": 0}
        except Exception:
            return {"score": 0, "interactions": 0}
    
    def get_context_for_llm(self, group_id, user_id=None):
        """获取好感度上下文"""
        try:
            conn = get_db()
            if user_id:
                cur = conn.execute(
                    "SELECT user_id, score FROM affection WHERE group_id=? AND user_id=?",
                    (str(group_id), str(user_id))
                )
            else:
                cur = conn.execute(
                    "SELECT user_id, score FROM affection WHERE group_id=? ORDER BY score DESC LIMIT 5",
                    (str(group_id),)
                )
            rows = cur.fetchall()
            conn.close()
            if not rows:
                return ""
            lines = []
            for uid, score in rows:
                level = "友好" if score > 50 else ("普通" if score > 20 else "冷淡")
                try:
                    from bot_core import resolve_qq_nick
                    nick = resolve_qq_nick(str(uid))
                except Exception:
                    nick = ""
                name = nick if nick else f"用户{str(uid)[-4:]}"
                lines.append(f"  {name}: {level}({score}分)")
            return "【群友好感度】近期互动情况：\n" + "\n".join(lines)
        except Exception:
            return ""


# ═══════════════════════════════════════════
#  人格更新器
# ═══════════════════════════════════════════

class PersonaUpdater:
    """管理人格文件的备份和增量更新"""
    
    def __init__(self):
        self.pending_updates = []
    
    def backup(self):
        """备份当前人格文件"""
        if not os.path.exists(PERSONALITY_FILE):
            return False
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = os.path.join(PERSONALITY_BACKUP_DIR, f"personality_{ts}.txt")
        try:
            with open(PERSONALITY_FILE, "r", encoding="utf-8") as src:
                content = src.read()
            with open(backup_path, "w", encoding="utf-8") as dst:
                dst.write(content)
            sl_log(f"人格备份: {backup_path}")
            return True
        except Exception as e:
            sl_log(f"人格备份失败: {e}")
            return False
    
    def generate_update(self, group_id, expressions, jargon):
        """根据学习结果生成人格更新建议"""
        lines = []
        
        if expressions:
            top_expr = expressions[:5]
            lines.append(f"\n【群{group_id}表达风格 - {datetime.now().strftime('%m-%d %H:%M')}】")
            for scene, expr, quality in top_expr:
                lines.append(f"- {scene}时该群常用: 「{expr}」(可靠度{quality:.0%})")
        
        if jargon:
            top_jargon = jargon[:3]
            if lines:
                lines.append("")
            lines.append("【群内常用词】")
            for word, count in top_jargon:
                lines.append(f"- 「{word}」出现{count}次")
        
        if not lines:
            return ""
        
        lines.insert(0, "【自主学习成果】")
        return "\n".join(lines)
    
    def apply_update(self, update_content, group_id):
        """应用人格更新（追加到文件末尾，标注为群聊专属）"""
        if not update_content:
            return False
        
        # 先备份
        self.backup()
        
        try:
            with open(PERSONALITY_FILE, "r", encoding="utf-8") as f:
                current = f.read().strip()
            
            # 检查是否已有该群的更新，有则替换
            marker = "\n\n【自主学习成果】"
            if marker in current:
                # 只保留第一个「自主学习成果」之前的内容
                current = current.split(marker)[0].strip()
            
            new_content = current + "\n\n" + update_content
            
            with open(PERSONALITY_FILE, "w", encoding="utf-8") as f:
                f.write(new_content)
            
            sl_log("人格文件已更新")
            return True
        except Exception as e:
            sl_log(f"人格更新失败: {e}")
            return False
    
    def get_update_count(self):
        """获取待应用的更新数量"""
        return len(self.pending_updates)


# ═══════════════════════════════════════════
#  主控制器：编排所有子模块
# ═══════════════════════════════════════════

class SelfLearningEngine:
    """自主学习引擎，统一管理所有学习模块"""
    
    def __init__(self):
        self.collector = MessageCollector()
        self.expr_learner = ExpressionLearner()
        self.jargon_miner = JargonMiner()
        self.affection = AffectionManager()
        self.persona = PersonaUpdater()
        self._lock = threading.Lock()
        self._learning_in_progress = set()  # 防止并发学习
    
    def collect_message(self, group_id, user_id, text):
        """收集一条群聊消息"""
        try:
            self.collector.collect(group_id, user_id, text)
            self.affection.process_interaction(group_id, user_id, text)
        except Exception as e:
            sl_log(f"收集消息异常: {e}")
    
    def maybe_learn(self, group_id):
        """检查是否需要触发学习，如果是则执行"""
        if not self.collector.should_learn(group_id):
            return
        
        with self._lock:
            if group_id in self._learning_in_progress:
                return
            if not self.collector.should_learn(group_id):
                return
            self._learning_in_progress.add(str(group_id))
        
        try:
            self._do_learn(str(group_id))
        finally:
            with self._lock:
                self._learning_in_progress.discard(str(group_id))
    
    def _do_learn(self, group_id):
        """执行完整的学习周期"""
        sl_log(f"群[{group_id}] 开始自主学习...")
        start = time.time()
        
        # 1. 获取未学习消息
        messages = self.collector.get_unlearned_messages(group_id)
        if not messages:
            sl_log(f"群[{group_id}] 无消息可学习")
            return
        
        msg_ids = [m[0] for m in messages]
        sl_log(f"群[{group_id}] 学习 {len(messages)} 条消息")
        
        # 2. 表达模式学习
        expressions = self.expr_learner.extract_expressions(messages)
        saved_expr = self.expr_learner.save_expressions(group_id, expressions)
        sl_log(f"群[{group_id}] 新增 {saved_expr} 条表达模式")
        
        # 3. 黑话挖掘
        candidates = self.jargon_miner.extract_candidates(messages)
        saved_jargon = self.jargon_miner.save_jargon(group_id, candidates)
        sl_log(f"群[{group_id}] 发现 {saved_jargon} 个候选黑话")
        
        # 4. 标记已学习
        self.collector.mark_learned(msg_ids)
        self.collector._last_learn_time[str(group_id)] = time.time()
        
        # 5. 生成人格更新（仅在表达模式足够丰富时）
        #
        # ★ 2026-10-19「严格化」：写入人格前必须**重复出现过**。
        #   此前 SQL 只按 quality/count 排序、**没有 count 下限** → 一次性的
        #   发言也会被写进 personality.txt。实测被污染的 5 条"群表达风格"
        #   全是 count=1 的一次性句子（而且来自群里另一个 AI 机器人）。
        #   真实群习惯一定会重复；AI 的漂亮话常常只出现一次。
        #   门槛由 self_learn_min_count 控制（默认 2）。用户原话：
        #   "可是有的 ai 昵称就像真人啊，能不能自学习严格呢" —— 这就是那道闸。
        try:
            # self_learning 不持有 bot_core 的 _config，自己读一次（带异常兜底）
            from config import load_config as _load_cfg
            _cfg = _load_cfg() or {}
        except Exception:
            _cfg = {}
        try:
            _min_cnt = int(_cfg.get("self_learn_min_count", 2) or 2)
        except (TypeError, ValueError):
            _min_cnt = 2
        top_expr = self._select_promotable_expressions(group_id, _min_cnt)

        if len(top_expr) >= 3:
            update = self.persona.generate_update(group_id, top_expr, candidates)
            if update:
                self.persona.apply_update(update, group_id)
        else:
            sl_log(f"群[{group_id}] 表达样本不足（需≥3 条重复≥{_min_cnt} 次的表达，"
                   f"实际 {len(top_expr)} 条）→ 本次不写人格")
        
        elapsed = time.time() - start
        sl_log(f"群[{group_id}] 学习完成 ({elapsed:.1f}s)")

    @staticmethod
    def _select_promotable_expressions(group_id, min_count=2, limit=5):
        """挑出**够格写进人格**的表达：同一句至少被 `min_count` 次独立发言验证过。

        抽成静态方法是为了可测 —— `_do_learn` 整条链路要真实网络/LLM，测不动。

        ★ 2026-10-19 严格化的核心：旧 SQL 没有 count 下限，**一次性的句子也会
          被写进 personality.txt**。实测被污染的 5 条"群表达风格"全是 count=1，
          且全部来自群里另一个 AI 机器人（用户："有的 AI 昵称就像真人啊，
          能不能自学习严格呢"）。
          真实群习惯一定会重复；AI 的漂亮话常常只出现一次 —— 这就是那道闸。
        """
        try:
            m = int(min_count)
        except (TypeError, ValueError):
            m = 2
        rows = []
        try:
            conn = get_db()
            cur = conn.execute(
                "SELECT scene, expression, quality FROM expressions "
                "WHERE group_id=? AND count>=? "
                "ORDER BY quality DESC, count DESC LIMIT ?",
                (str(group_id), m, int(limit)),
            )
            rows = [(r[0], r[1], r[2]) for r in cur.fetchall()]
            conn.close()
        except Exception:
            rows = []
        return rows

    def get_context_for_llm(self, group_id, user_id=None):
        """获取所有学习上下文，注入 LLM prompt"""
        parts = []
        
        expr_ctx = self.expr_learner.get_context_for_llm(group_id)
        if expr_ctx:
            parts.append(expr_ctx)
        
        jargon_ctx = self.jargon_miner.get_context_for_llm(group_id)
        if jargon_ctx:
            parts.append(jargon_ctx)
        
        affection_ctx = self.affection.get_context_for_llm(group_id, user_id)
        if affection_ctx:
            parts.append(affection_ctx)
        
        return "\n\n".join(parts) if parts else ""
    
    def get_status(self, group_id=None):
        """获取学习状态"""
        status = {
            "total_messages": 0,
            "unlearned": 0,
            "expressions": 0,
            "jargon_words": 0,
            "active_users": 0,
        }
        try:
            conn = get_db()
            if group_id:
                status["unlearned"] = self.collector.get_unlearned_count(group_id)
                cur = conn.execute("SELECT COUNT(*) FROM expressions WHERE group_id=?", (str(group_id),))
                status["expressions"] = cur.fetchone()[0]
                cur = conn.execute("SELECT COUNT(*) FROM jargon WHERE group_id=?", (str(group_id),))
                status["jargon_words"] = cur.fetchone()[0]
                cur = conn.execute("SELECT COUNT(*) FROM affection WHERE group_id=?", (str(group_id),))
                status["active_users"] = cur.fetchone()[0]
            cur = conn.execute("SELECT COUNT(*) FROM messages")
            status["total_messages"] = cur.fetchone()[0]
            conn.close()
        except Exception:
            pass
        return status
    
    def get_group_profile(self, group_id, min_messages=5, limit=20):
        """获取群友画像统计（发言频次、风格等），返回结构供 AI 生成报告用"""
        group_id = str(group_id)
        conn = None
        try:
            conn = get_db()
            # 每人发言次数、总字数、最早/最晚时间
            cur = conn.execute("""
                SELECT user_id,
                       COUNT(*) AS msg_count,
                       SUM(LENGTH(text)) AS total_chars,
                       MIN(timestamp) AS first_ts,
                       MAX(timestamp) AS last_ts,
                       AVG(LENGTH(text)) AS avg_len
                FROM messages
                WHERE group_id=?
                GROUP BY user_id
                HAVING msg_count >= ?
                ORDER BY msg_count DESC
                LIMIT ?
            """, (group_id, min_messages, limit))
            users = []
            for row in cur.fetchall():
                user_id, msg_count, total_chars, first_ts, last_ts, avg_len = row
                # 活跃时段分布（按小时）
                cur2 = conn.execute("""
                    SELECT CAST(strftime('%H', datetime(timestamp,'unixepoch','localtime')) AS INTEGER) AS hour,
                           COUNT(*) AS cnt
                    FROM messages
                    WHERE group_id=? AND user_id=?
                    GROUP BY hour
                """, (group_id, user_id))
                hours = {h: c for h, c in cur2.fetchall()}
                peak_hour = max(hours, key=hours.get) if hours else -1
                
                # 常用词 Top 10（基于 jieba 分词）
                cur2 = conn.execute("""
                    SELECT text FROM messages
                    WHERE group_id=? AND user_id=?
                """, (group_id, user_id))
                all_text = " ".join([r[0] for r in cur2.fetchall()])
                top_words = _extract_top_words(all_text, top_n=10) if all_text.strip() else []
                
                # 好感度
                cur2 = conn.execute(
                    "SELECT score, mood FROM affection WHERE group_id=? AND user_id=?",
                    (group_id, user_id)
                )
                aff = cur2.fetchone()
                affection = {"score": aff[0], "mood": aff[1]} if aff else {"score": 0, "mood": "neutral"}
                
                users.append({
                    "user_id": user_id,
                    "msg_count": msg_count,
                    "total_chars": total_chars,
                    "avg_len": round(avg_len, 1),
                    "first_seen": first_ts,
                    "last_seen": last_ts,
                    "peak_hour": peak_hour,
                    "hour_dist": hours,
                    "top_words": top_words,
                    "affection": affection,
                })
            conn.close()
            return {"group_id": group_id, "users": users}
        except Exception as e:
            sl_log(f"画像统计失败: {e}")
            return {"group_id": group_id, "users": [], "error": str(e)}
        finally:
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass


def _extract_top_words(text, top_n=10):
    """从文本中提取高频词（无 jieba 时用简单分词）"""
    import re
    # 停用词
    stop_words = {
        "的","了","是","我","你","他","她","们","不","就","都","也","还","这","那","在",
        "和","说","有","要","会","吗","啊","吧","呢","哦","嗯","呀","哈","好","很","没",
        "一个","什么","怎么","为什","因为","如果","可以","没有","自己","知道","觉得",
        "这个","那个","今天","我们","他们","大家","不是","就是","但是","不过","所以",
        "然后","已经","可能","应该","真的","一点","一下","一直","一起","怎么","现在",
        "这样","那样","时候","还是","出来","进去","起来","过来","过去","下来","上来",
    }
    # 简单中文分词（2-4字词）
    words = re.findall(r'[\u4e00-\u9fff]{2,4}', text)
    freq = {}
    for w in words:
        if w in stop_words:
            continue
        freq[w] = freq.get(w, 0) + 1
    sorted_words = sorted(freq.items(), key=lambda x: x[1], reverse=True)
    return [{"word": w, "count": c} for w, c in sorted_words[:top_n]]


# ═══════════════════════════════════════════
#  导出接口
# ═══════════════════════════════════════════

# 全局引擎实例（单例）
_engine = None

def get_engine():
    global _engine
    if _engine is None:
        _engine = SelfLearningEngine()
    return _engine


if __name__ == "__main__":
    # 测试
    engine = get_engine()
    print("自主学习模块加载成功")
    print(f"数据库: {DB_PATH}")
    print(f"人格文件: {PERSONALITY_FILE}")
    status = engine.get_status()
    print(f"状态: {status}")