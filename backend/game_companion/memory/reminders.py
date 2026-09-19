# -*- coding: utf-8 -*-
"""定时提醒 - 数据与调度辅助

表：reminders（存 companion.db，与记忆库同文件，WAL 并发安全）
- 用户经两种途径创建：① 对 AI 说"每天8点提醒我…"（AI 调 MCP 工具）② 界面 ⏰ 面板
- 支持循环：每天 / 每周指定星期几，到点触发后自动排下一次，取消才会停
- 到点后由后端每个连接上的调度任务触发「主动说话」
"""

import sqlite3
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

_DB_LOCK = threading.Lock()
_DB_PATH = Path(__file__).resolve().parents[1] / "data" / "companion.db"

WEEKDAY_NAMES = {1: "一", 2: "二", 3: "三", 4: "四", 5: "五", 6: "六", 7: "日"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS reminders (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    TEXT DEFAULT 'default',
    text       TEXT NOT NULL,
    fire_at    REAL NOT NULL,
    status     TEXT DEFAULT 'pending',   -- pending / fired / cancelled
    created_at REAL,
    repeat     TEXT DEFAULT 'none',      -- none / daily / weekly
    weekdays   TEXT DEFAULT '',          -- weekly 用：'1,3,5'（1=周一…7=周日）
    hh         INTEGER,                  -- 循环提醒的锚点时刻
    mm         INTEGER
);
CREATE INDEX IF NOT EXISTS idx_reminders_due ON reminders(status, fire_at);
"""


def _conn():
    conn = sqlite3.connect(_DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _init():
    with _DB_LOCK:
        conn = _conn()
        try:
            conn.executescript(SCHEMA)
            # 旧库迁移：补新列
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(reminders)")}
            for col, ddl in [
                ("repeat", "TEXT DEFAULT 'none'"),
                ("weekdays", "TEXT DEFAULT ''"),
                ("hh", "INTEGER"),
                ("mm", "INTEGER"),
            ]:
                if col not in cols:
                    conn.execute(f"ALTER TABLE reminders ADD COLUMN {col} {ddl}")
            conn.commit()
        finally:
            conn.close()


_init()


def add_reminder(
    text: str,
    fire_at: float,
    user_id: str = "default",
    repeat: str = "none",
    weekdays: str = "",
    hh: int | None = None,
    mm: int | None = None,
) -> int:
    with _DB_LOCK:
        conn = _conn()
        try:
            cur = conn.execute(
                "INSERT INTO reminders"
                " (user_id, text, fire_at, created_at, repeat, weekdays, hh, mm)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (user_id, text, fire_at, time.time(), repeat, weekdays, hh, mm),
            )
            conn.commit()
            return cur.lastrowid
        finally:
            conn.close()


def _next_occurrence(hh: int, mm: int, weekdays: str | None) -> float:
    """循环提醒的下一次触发时间（严格晚于现在）。weekdays=None 表示每天。"""
    now = datetime.now()
    cand = now.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
    if not weekdays:  # daily
        if cand <= now:
            cand += timedelta(days=1)
        return cand.timestamp()
    days = sorted({int(d) for d in weekdays.split(",") if d.strip().isdigit()
                   and 1 <= int(d) <= 7})
    if not days:
        # 兜底：无效 weekdays 退化为每天
        if cand <= now:
            cand += timedelta(days=1)
        return cand.timestamp()
    for offset in range(0, 8):
        day = (now + timedelta(days=offset)).date()
        if day.isoweekday() in days:
            t = datetime(day.year, day.month, day.day, int(hh), int(mm))
            if t > now:
                return t.timestamp()
    # 理论到不了这里
    return (now + timedelta(days=1)).timestamp()


def due_reminders(user_id: str | None = None) -> list[dict]:
    """取到点未触发的提醒。

    单次：标记 fired 并返回；循环：自动把 fire_at 推进到下一次（保持 pending）再返回。
    取走即触发，避免重复。
    """
    with _DB_LOCK:
        conn = _conn()
        try:
            sql = "SELECT * FROM reminders WHERE status='pending' AND fire_at <= ?"
            params: list = [time.time()]
            if user_id:
                sql += " AND user_id = ?"
                params.append(user_id)
            rows = conn.execute(sql, params).fetchall()
            out = []
            for r in rows:
                if (r["repeat"] or "none") == "none":
                    conn.execute(
                        "UPDATE reminders SET status='fired' WHERE id=?", (r["id"],)
                    )
                    out.append(dict(r))
                else:
                    nxt = _next_occurrence(r["hh"], r["mm"], r["weekdays"])
                    conn.execute(
                        "UPDATE reminders SET fire_at=? WHERE id=?", (nxt, r["id"])
                    )
                    d = dict(r)
                    d["fire_at"] = nxt  # 调度器日志用旧值也无妨
                    out.append(d)
            conn.commit()
            return out
        finally:
            conn.close()


def snooze(reminder_id: int, seconds: float) -> None:
    """正在说话时把提醒往后推，保留循环信息（代替旧的删除重加）。"""
    with _DB_LOCK:
        conn = _conn()
        try:
            conn.execute(
                "UPDATE reminders SET fire_at = fire_at + ? WHERE id = ?",
                (seconds, reminder_id),
            )
            conn.commit()
        finally:
            conn.close()


def list_reminders(user_id: str = "default") -> list[dict]:
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT * FROM reminders WHERE user_id = ? AND status='pending'"
            " ORDER BY fire_at LIMIT 30",
            (user_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def cancel_reminder(reminder_id: int, user_id: str = "default") -> bool:
    with _DB_LOCK:
        conn = _conn()
        try:
            cur = conn.execute(
                "UPDATE reminders SET status='cancelled'"
                " WHERE id = ? AND user_id = ? AND status='pending'",
                (reminder_id, user_id),
            )
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()


def describe(r: dict) -> str:
    """给人看的触发描述：'08:00' / '每天 08:00' / '每周一、三、五 21:30'。"""
    hh, mm = r.get("hh"), r.get("mm")
    anchor = f"{int(hh):02d}:{int(mm):02d}" if hh is not None and mm is not None else fmt_ts(r["fire_at"])
    repeat = r.get("repeat") or "none"
    if repeat == "daily":
        return f"每天 {anchor}"
    if repeat == "weekly":
        days = sorted({int(d) for d in (r.get("weekdays") or "").split(",") if d.strip().isdigit()})
        names = "、".join(WEEKDAY_NAMES.get(d, str(d)) for d in days) or "每天"
        return f"每周{names} {anchor}"
    return fmt_ts(r["fire_at"])


def parse_and_create(
    text: str,
    delay_minutes: float = 0,
    at_time: str = "",
    repeat: str = "none",
    weekdays: str = "",
    at_day: str = "",
    user_id: str = "default",
) -> dict:
    """统一的创建入口（MCP 工具与 WS 面板共用）。返回 {id, fire_at, desc} 或 {错误}。"""
    text = (text or "").strip()
    if not text:
        return {"错误": "提醒内容为空"}
    repeat = (repeat or "none").lower()
    if repeat not in ("none", "daily", "weekly"):
        repeat = "none"
    if repeat in ("daily", "weekly"):
        if not at_time or ":" not in at_time:
            return {"错误": "循环提醒需要 at_time（HH:MM）"}
        try:
            hh, mm = int(at_time.split(":")[0]), int(at_time.split(":")[1])
            if not (0 <= hh <= 23 and 0 <= mm <= 59):
                raise ValueError
        except Exception:
            return {"错误": f"at_time 格式不对: {at_time}"}
        if repeat == "weekly":
            days = sorted({int(d) for d in (weekdays or "").split(",") if d.strip().isdigit() and 1 <= int(d) <= 7})
            if not days:
                return {"错误": "weekly 需要 weekdays（1=周一…7=周日，如 '1,3,5'）"}
            weekdays = ",".join(str(d) for d in days)
        fire_at = _next_occurrence(hh, mm, weekdays if repeat == "weekly" else None)
        rid = add_reminder(text, fire_at, user_id, repeat, weekdays, hh, mm)
        desc = describe(
            {"hh": hh, "mm": mm, "repeat": repeat, "weekdays": weekdays,
             "fire_at": fire_at}
        )
        return {"id": rid, "fire_at": fire_at, "desc": desc}
    # 单次
    now = datetime.now()
    if at_time and ":" in at_time:
        try:
            hh, mm = int(at_time.split(":")[0]), int(at_time.split(":")[1])
        except Exception:
            return {"错误": f"at_time 格式不对: {at_time}"}
        target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if (at_day or "").lower() in ("tomorrow", "明天") or target <= now:
            if target <= now or (at_day or "").lower() in ("tomorrow", "明天"):
                target += timedelta(days=1)
        fire_at = target.timestamp()
    elif delay_minutes and float(delay_minutes) > 0:
        fire_at = now.timestamp() + float(delay_minutes) * 60
    else:
        return {"错误": "需要 delay_minutes 或 at_time"}
    rid = add_reminder(text, fire_at, user_id)
    return {"id": rid, "fire_at": fire_at, "desc": fmt_ts(fire_at)}


def fmt_ts(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%m-%d %H:%M")
