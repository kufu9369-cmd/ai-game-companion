"""记忆系统 - SQLite 数据层

表设计（对应技术方案第 5 节）：
- users:        用户资料（昵称、主玩游戏、人格偏好、付费状态）
- memory_facts: AI 提炼的长期事实（昵称、主玩英雄、连败记录、梗……）
- events:       带情绪标记的事件（连败、首次使用、情绪低谷……）
- usage_log:    用量统计（token / TTS 字符），商业化计费基础
"""

import sqlite3
import threading
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id      TEXT PRIMARY KEY,
    nickname     TEXT DEFAULT '',
    age          INTEGER,
    main_game    TEXT DEFAULT '永劫无间手游',
    fav_heroes   TEXT DEFAULT '',
    chat_style   TEXT DEFAULT '',
    persona_pref TEXT DEFAULT '星野露露',
    pay_status   TEXT DEFAULT 'free',
    created_at   REAL
);

CREATE TABLE IF NOT EXISTS memory_facts (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id          TEXT NOT NULL,
    category         TEXT NOT NULL,          -- profile / hero /梗 / habit / emotion / misc
    content          TEXT NOT NULL,
    confidence       REAL DEFAULT 0.8,
    source           TEXT DEFAULT 'chat',    -- chat / manual
    created_at       REAL,
    last_recalled_at REAL,
    UNIQUE(user_id, content)
);

CREATE TABLE IF NOT EXISTS events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    TEXT NOT NULL,
    event_type TEXT NOT NULL,                -- lose_streak / win / first_use / emotion_low / milestone
    detail     TEXT DEFAULT '',
    sentiment  TEXT DEFAULT 'neutral',       -- positive / negative / neutral
    created_at REAL
);

CREATE TABLE IF NOT EXISTS usage_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    TEXT NOT NULL,
    tokens_in  INTEGER DEFAULT 0,
    tokens_out INTEGER DEFAULT 0,
    tts_chars  INTEGER DEFAULT 0,
    created_at REAL
);

CREATE INDEX IF NOT EXISTS idx_facts_user   ON memory_facts(user_id);
CREATE INDEX IF NOT EXISTS idx_events_user  ON events(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_usage_user   ON usage_log(user_id, created_at);
"""


class MemoryDB:
    """线程安全的 SQLite 封装（WAL 模式，语音主线程与后台写入共用）。"""

    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    def close(self):
        with self._lock:
            self._conn.close()

    # ---------- users ----------

    def get_or_create_user(self, user_id: str) -> dict:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM users WHERE user_id = ?", (user_id,)
            ).fetchone()
            if row is None:
                self._conn.execute(
                    "INSERT INTO users (user_id, created_at) VALUES (?, ?)",
                    (user_id, time.time()),
                )
                self._conn.commit()
                row = self._conn.execute(
                    "SELECT * FROM users WHERE user_id = ?", (user_id,)
                ).fetchone()
            return dict(row)

    def update_user(self, user_id: str, **fields):
        allowed = {
            "nickname", "age", "main_game", "fav_heroes",
            "chat_style", "persona_pref", "pay_status",
        }
        sets = {k: v for k, v in fields.items() if k in allowed}
        if not sets:
            return
        sql = "UPDATE users SET {} WHERE user_id = ?".format(
            ", ".join(f"{k} = ?" for k in sets)
        )
        with self._lock:
            self._conn.execute(sql, (*sets.values(), user_id))
            self._conn.commit()

    # ---------- memory_facts ----------

    def add_fact(self, user_id: str, category: str, content: str,
                 confidence: float = 0.8, source: str = "chat") -> bool:
        """插入事实，重复内容忽略。返回是否为新插入。"""
        try:
            with self._lock:
                cur = self._conn.execute(
                    "INSERT OR IGNORE INTO memory_facts"
                    " (user_id, category, content, confidence, source, created_at)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (user_id, category, content, confidence, source, time.time()),
                )
                self._conn.commit()
                return cur.rowcount > 0
        except sqlite3.Error:
            return False

    def get_facts(self, user_id: str, limit: int = 20,
                  category: str | None = None) -> list[dict]:
        sql = "SELECT * FROM memory_facts WHERE user_id = ? AND source != 'archived'"
        params: list = [user_id]
        if category:
            sql += " AND category = ?"
            params.append(category)
        sql += " ORDER BY confidence DESC, COALESCE(last_recalled_at, 0) DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
            return [dict(r) for r in rows]

    def count_facts(self, user_id: str, include_archived: bool = False) -> int:
        sql = "SELECT COUNT(*) c FROM memory_facts WHERE user_id = ?"
        if not include_archived:
            sql += " AND source != 'archived'"
        with self._lock:
            return self._conn.execute(sql, (user_id,)).fetchone()["c"]

    def search_memory(self, user_id: str, query: str, limit: int = 8) -> list[dict]:
        """按关键词检索事实+事件（跨会话召回，"上个月五杀"这类问题走这里）。"""
        words = [w for w in (query or "").replace("，", " ").replace(",", " ").split() if w]
        if not words:
            return []
        hits: list[dict] = []
        with self._lock:
            # 取 8 个词：jieba 切出的实词（如"值日"）常排在第 5、6 位，截 4 个会漏
            for w in words[:8]:
                like = f"%{w}%"
                for r in self._conn.execute(
                    "SELECT content, category, created_at,"
                    " 'fact' AS kind, confidence FROM memory_facts"
                    " WHERE user_id = ? AND content LIKE ?"
                    " ORDER BY confidence DESC LIMIT 6",
                    (user_id, like),
                ):
                    hits.append(dict(r))
                for r in self._conn.execute(
                    "SELECT event_type AS content, event_type AS category,"
                    " created_at, sentiment AS confidence,"
                    " detail, 'event' AS kind FROM events"
                    " WHERE user_id = ? AND (detail LIKE ? OR event_type LIKE ?)"
                    " ORDER BY created_at DESC LIMIT 6",
                    (user_id, like, like),
                ):
                    hits.append(dict(r))
        # 去重 + 相关度（命中词越多越靠前）+ 新鲜度加成
        seen, scored = set(), []
        now = time.time()
        for h in hits:
            key = (h["kind"], h["content"], h.get("detail", ""))
            if key in seen:
                continue
            seen.add(key)
            age_days = max(0.0, (now - (h["created_at"] or 0)) / 86400)
            score = 1.0 / (1 + age_days / 90.0)  # 90 天半衰期的平滑衰减
            scored.append((score, h))
        scored.sort(key=lambda x: -x[0])
        return [h for _, h in scored[:limit]]

    def archive_facts(self, user_id: str, keep_ids: list[int]) -> int:
        """把未列入 keep_ids 的事实标记为 archived（数据保留，不再注入 prompt）。"""
        if not keep_ids:
            with self._lock:
                cur = self._conn.execute(
                    "UPDATE memory_facts SET source='archived'"
                    " WHERE user_id = ? AND source != 'archived'",
                    (user_id,),
                )
                self._conn.commit()
                return cur.rowcount
        marks = ",".join("?" for _ in keep_ids)
        with self._lock:
            cur = self._conn.execute(
                f"UPDATE memory_facts SET source='archived'"
                f" WHERE user_id = ? AND source != 'archived' AND id NOT IN ({marks})",
                (user_id, *keep_ids),
            )
            self._conn.commit()
            return cur.rowcount

    def touch_facts(self, fact_ids: list[int]):
        if not fact_ids:
            return
        with self._lock:
            self._conn.executemany(
                "UPDATE memory_facts SET last_recalled_at = ? WHERE id = ?",
                [(time.time(), fid) for fid in fact_ids],
            )
            self._conn.commit()

    # ---------- events ----------

    def add_event(self, user_id: str, event_type: str,
                  detail: str = "", sentiment: str = "neutral"):
        with self._lock:
            self._conn.execute(
                "INSERT INTO events (user_id, event_type, detail, sentiment, created_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (user_id, event_type, detail, sentiment, time.time()),
            )
            self._conn.commit()

    def get_recent_events(self, user_id: str, limit: int = 10) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM events WHERE user_id = ?"
                " ORDER BY created_at DESC LIMIT ?",
                (user_id, limit),
            ).fetchall()
            return [dict(r) for r in rows]

    # ---------- usage_log ----------

    def log_usage(self, user_id: str, tokens_in: int = 0,
                  tokens_out: int = 0, tts_chars: int = 0):
        with self._lock:
            self._conn.execute(
                "INSERT INTO usage_log (user_id, tokens_in, tokens_out, tts_chars, created_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (user_id, tokens_in, tokens_out, tts_chars, time.time()),
            )
            self._conn.commit()

    def get_usage_today(self, user_id: str) -> dict:
        day_start = time.time() - (time.time() % 86400) - time.timezone
        with self._lock:
            row = self._conn.execute(
                "SELECT COALESCE(SUM(tokens_in),0) ti, COALESCE(SUM(tokens_out),0) to_,"
                " COALESCE(SUM(tts_chars),0) tc FROM usage_log"
                " WHERE user_id = ? AND created_at >= ?",
                (user_id, day_start),
            ).fetchone()
            return {"tokens_in": row["ti"], "tokens_out": row["to_"], "tts_chars": row["tc"]}
