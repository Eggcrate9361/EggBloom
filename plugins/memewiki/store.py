# -*- coding: utf-8 -*-
"""Local meme knowledge store and matcher.

The plugin keeps a small SQLite database so matching stays local and fast. Network
lookup remains a fallback for words that are not in the local corpus.
"""
from __future__ import annotations

import json
import os
import random
import re
import sqlite3
import threading
import time
import unicodedata
from datetime import date, datetime
from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional, Tuple

from plugins.memewiki.seeds import SEEDS

PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DB = os.environ.get("EGGBLOOM_MEMEWIKI_DB") or os.path.join(
    PLUGIN_DIR, "data", "memewiki.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meme (
  word TEXT PRIMARY KEY,
  aliases TEXT NOT NULL DEFAULT '[]',
  meaning TEXT NOT NULL,
  origin TEXT NOT NULL DEFAULT '',
  tone TEXT NOT NULL DEFAULT 'neutral',
  circle TEXT NOT NULL DEFAULT '通用',
  risk INTEGER NOT NULL DEFAULT 0,
  freshness INTEGER NOT NULL DEFAULT 0,
  used INTEGER NOT NULL DEFAULT 0,
  good INTEGER NOT NULL DEFAULT 0,
  source TEXT NOT NULL DEFAULT 'manual'
);
CREATE TABLE IF NOT EXISTS meme_group (
  word TEXT NOT NULL,
  scope_id TEXT NOT NULL,
  used INTEGER NOT NULL DEFAULT 0,
  good INTEGER NOT NULL DEFAULT 0,
  last_ts REAL NOT NULL DEFAULT 0,
  PRIMARY KEY(word, scope_id)
);
CREATE TABLE IF NOT EXISTS meme_learn (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  word TEXT NOT NULL,
  meaning TEXT NOT NULL,
  evidence TEXT NOT NULL DEFAULT '',
  scope_id TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'pending',
  created REAL NOT NULL,
  reviewed REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_learn_status
  ON meme_learn(status, created);
"""

_SPACE_RE = re.compile(r"[\s\u3000]+")
_PUNCT_RE = re.compile(r"[，。！？!?；;：:、,.()（）\[\]【】\"'“”‘’]+")
_STOPWORDS = {
    "这个", "那个", "什么", "怎么", "意思", "梗", "词语", "说法",
    "真的假的", "笑死", "确实", "细说", "前排", "就这",
}
_AMBIGUOUS = {
    "典", "寄", "孝", "唐", "急了", "确实", "细说", "前排", "就这", "笑死",
    "拿捏", "退退退", "emo",
}


def normalize(text: Any) -> str:
    value = unicodedata.normalize("NFKC", str(text or "")).lower()
    value = value.replace("\u200b", "").replace("\ufeff", "")
    return _SPACE_RE.sub(" ", value).strip()


def compact(text: Any) -> str:
    return _PUNCT_RE.sub("", normalize(text).replace(" ", ""))


@dataclass(frozen=True)
class MemeMatch:
    word: str
    surface: str
    score: float
    mode: str
    entry: Dict[str, Any]
    usage: int = 0
    feedback: int = 0

    def as_dict(self) -> dict:
        return asdict(self)


class MemeStore:
    def __init__(self, path: str = "", *, seed: bool = True):
        self.path = str(path or DEFAULT_DB)
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()
            if seed:
                self.seed()
        self._entries: List[Dict[str, Any]] = []
        self._index: Dict[str, Dict[str, Any]] = {}
        self._terms: List[Tuple[str, str, str, bool, Dict[str, Any]]] = []
        self.reload()

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass

    def seed(self) -> None:
        now_day = int(time.strftime("%Y%m%d"))
        with self._lock:
            for row in SEEDS:
                aliases = [normalize(x) for x in (row.get("aliases") or []) if normalize(x)]
                aliases = list(dict.fromkeys(aliases))
                self._conn.execute(
                    "INSERT INTO meme(word,aliases,meaning,origin,tone,circle,risk,"
                    "freshness,used,good,source) VALUES(?,?,?,?,?,?,?,?,0,0,'seed') "
                    "ON CONFLICT(word) DO UPDATE SET "
                    "aliases=excluded.aliases, meaning=excluded.meaning, origin=excluded.origin, "
                    "tone=excluded.tone, circle=excluded.circle, risk=excluded.risk, "
                    "freshness=excluded.freshness "
                    "WHERE meme.source='seed'",
                    (str(row["word"]), json.dumps(aliases, ensure_ascii=False),
                     str(row["meaning"]), str(row.get("origin") or ""),
                     str(row.get("tone") or "neutral"), str(row.get("circle") or "通用"),
                     max(0, min(2, int(row.get("risk") or 0))),
                     int(row.get("freshness") or now_day)))
            self._conn.commit()

    def reload(self) -> int:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM meme ORDER BY word").fetchall()
        entries, index, terms = [], {}, []
        for row in rows:
            item = dict(row)
            try:
                aliases = json.loads(item.get("aliases") or "[]")
            except Exception:
                aliases = []
            if isinstance(aliases, str):
                aliases = [aliases]
            aliases = [normalize(x) for x in aliases if normalize(x)]
            item["aliases"] = aliases
            item["used"] = int(item.get("used") or 0)
            item["good"] = int(item.get("good") or 0)
            item["risk"] = int(item.get("risk") or 0)
            entries.append(item)
            word = normalize(item.get("word"))
            index[word] = item
            terms.append((word, word, item["word"], word in _AMBIGUOUS, item))
            for alias in aliases:
                terms.append((alias, alias, item["word"], alias in _AMBIGUOUS, item))
        terms.sort(key=lambda x: (-len(x[0]), x[2]))
        with self._lock:
            self._entries, self._index, self._terms = entries, index, terms
        return len(entries)

    def get(self, word: str) -> Optional[dict]:
        key = normalize(word)
        with self._lock:
            item = self._index.get(key)
            if item:
                return dict(item)
            for row in self._entries:
                if key in (row.get("aliases") or []):
                    return dict(row)
        return None

    def match(self, text: str, *, question: bool = False,
              limit: int = 2, scope_id: str = "") -> List[MemeMatch]:
        raw = normalize(text)
        comp = compact(text)
        if not raw or not comp:
            return []
        with self._lock:
            terms = list(self._terms)
            group_stats = self._group_stats(scope_id) if scope_id else {}
        today = date.today().toordinal()
        hits: Dict[str, MemeMatch] = {}
        for surface, surface_compact, word, ambiguous, entry in terms:
            if not surface_compact:
                continue
            exact = comp == surface_compact
            if surface in _STOPWORDS and not (question or exact):
                continue
            contained = (
                (len(surface_compact) >= 2 and surface_compact in comp)
                or (question and len(surface_compact) == 1
                    and surface_compact in comp[:6]))
            reverse = (
                question and len(comp) >= 3 and len(surface_compact) >= 2
                and comp in surface_compact)
            if not (exact or contained or reverse):
                continue
            if ambiguous and not question and not exact:
                continue
            score = 100.0 + len(surface_compact)
            if exact:
                score += 40.0
            if question:
                score += 15.0
            stats = group_stats.get(str(entry["word"])) or {}
            usage = max(0, int(stats.get("used") or 0))
            feedback = int(stats.get("good") or 0)
            score += min(usage, 8) * 0.25
            score += max(-6.0, min(6.0, feedback * 0.8))
            score += _freshness_bonus(entry.get("freshness"), today)
            mode = "direct" if exact else "usage"
            old = hits.get(str(entry["word"]))
            match = MemeMatch(str(entry["word"]), surface,
                              score, mode, entry, usage, feedback)
            if old is None or match.score > old.score:
                hits[str(entry["word"])] = match
        return sorted(hits.values(), key=lambda x: (-x.score, x.word))[:max(1, limit)]

    def random(self) -> Optional[dict]:
        """Return a random local entry without touching the network."""
        with self._lock:
            entries = list(self._entries)
        return dict(random.choice(entries)) if entries else None

    def _group_stats(self, scope_id: str) -> Dict[str, dict]:
        if not scope_id:
            return {}
        rows = self._conn.execute(
            "SELECT word,used,good FROM meme_group WHERE scope_id=?",
            (str(scope_id),)).fetchall()
        return {str(row["word"]): dict(row) for row in rows}

    def record_use(self, word: str, scope_id: str, *, good_delta: int = 0) -> None:
        item = self.get(word)
        if not item:
            return
        key = str(item.get("word"))
        scope_id = str(scope_id or "")
        with self._lock:
            self._conn.execute(
                "UPDATE meme SET used=used+1, good=good+? WHERE word=?",
                (int(good_delta), key))
            if scope_id:
                row = self._conn.execute(
                    "SELECT used, good FROM meme_group WHERE word=? AND scope_id=?",
                    (key, scope_id)).fetchone()
                if row:
                    self._conn.execute(
                        "UPDATE meme_group SET used=used+1, good=good+?, last_ts=? "
                        "WHERE word=? AND scope_id=?",
                        (int(good_delta), time.time(), key, scope_id))
                else:
                    self._conn.execute(
                        "INSERT INTO meme_group(word,scope_id,used,good,last_ts) "
                        "VALUES(?,?,1,?,?)",
                        (key, scope_id, int(good_delta), time.time()))
            self._conn.commit()
        self.reload()

    def group_stats(self, word: str, scope_id: str) -> dict:
        with self._lock:
            row = self._conn.execute(
                "SELECT used,good,last_ts FROM meme_group WHERE word=? AND scope_id=?",
                (str(word), str(scope_id or ""))).fetchone()
        return dict(row) if row else {"used": 0, "good": 0, "last_ts": 0.0}

    def update_feedback(self, word: str, scope_id: str, delta: int) -> None:
        """Update reaction quality without counting another use."""
        delta = int(delta or 0)
        if not delta:
            return
        with self._lock:
            self._conn.execute(
                "UPDATE meme SET good=good+? WHERE word=?",
                (delta, str(word)))
            if scope_id:
                self._conn.execute(
                    "UPDATE meme_group SET good=good+? WHERE word=? AND scope_id=?",
                    (delta, str(word), str(scope_id)))
            self._conn.commit()
        self.reload()

    def add_learn(self, word: str, meaning: str, evidence: str,
                  scope_id: str = "") -> Optional[int]:
        word = normalize(word)
        meaning = str(meaning or "").strip()[:120]
        evidence = str(evidence or "").strip()[:500]
        if len(word) < 2 or not meaning or not evidence:
            return None
        if self.get(word):
            return None
        with self._lock:
            row = self._conn.execute(
                "SELECT id FROM meme_learn WHERE word=? AND status='pending' "
                "AND scope_id=? ORDER BY id DESC LIMIT 1",
                (word, str(scope_id or ""))).fetchone()
            if row:
                return int(row[0])
            cur = self._conn.execute(
                "INSERT INTO meme_learn(word,meaning,evidence,scope_id,status,created) "
                "VALUES(?,?,?,?, 'pending', ?)",
                (word, meaning, evidence, str(scope_id or ""), time.time()))
            self._conn.commit()
            return int(cur.lastrowid)

    def list_learn(self, status: str = "pending", limit: int = 100) -> List[dict]:
        sql = "SELECT * FROM meme_learn"
        args: tuple = ()
        if status:
            sql += " WHERE status=?"
            args = (str(status),)
        sql += " ORDER BY id DESC LIMIT ?"
        args += (max(1, int(limit)),)
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, args).fetchall()]

    def review_learn(self, item_id: int, action: str, *,
                     word: str = "", meaning: str = "") -> bool:
        action = str(action or "").strip().lower()
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM meme_learn WHERE id=?", (int(item_id),)).fetchone()
            if not row or row["status"] != "pending":
                return False
            if action == "reject":
                self._conn.execute(
                    "UPDATE meme_learn SET status='rejected', reviewed=? WHERE id=?",
                    (time.time(), int(item_id)))
                self._conn.commit()
                return True
            if action != "accept":
                return False
            final_word = normalize(word or row["word"])
            final_meaning = str(meaning or row["meaning"]).strip()[:120]
            if not final_word or not final_meaning:
                return False
            self._conn.execute(
                "INSERT INTO meme(word,aliases,meaning,origin,tone,circle,risk,"
                "freshness,used,good,source) VALUES(?,?,?,?,?,?,?,?,0,0,'learn') "
                "ON CONFLICT(word) DO UPDATE SET meaning=excluded.meaning, "
                "source='learn'",
                (final_word, "[]", final_meaning, "群聊学习", "neutral", "群内",
                 1, int(time.strftime("%Y%m%d"))))
            self._conn.execute(
                "UPDATE meme_learn SET word=?, meaning=?, status='accepted', reviewed=? "
                "WHERE id=?",
                (final_word, final_meaning, time.time(), int(item_id)))
            self._conn.commit()
        self.reload()
        return True


_STORE: Optional[MemeStore] = None
_STORE_LOCK = threading.Lock()


def default() -> MemeStore:
    global _STORE
    with _STORE_LOCK:
        if _STORE is None:
            _STORE = MemeStore()
        return _STORE


def reset_default() -> None:
    global _STORE
    with _STORE_LOCK:
        if _STORE is not None:
            _STORE.close()
        _STORE = None


__all__ = ["MemeStore", "MemeMatch", "normalize", "compact", "default", "reset_default"]


def _freshness_bonus(value: Any, today: int) -> float:
    """Small recency boost; malformed or old values stay neutral."""
    text = str(value or "")
    try:
        stamp = datetime.strptime(text, "%Y%m%d").date().toordinal()
    except ValueError:
        return 0.0
    age = max(0, today - stamp)
    return max(0.0, 2.0 - age / 15.0) if age <= 30 else 0.0
