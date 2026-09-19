# -*- coding: utf-8 -*-
"""陪伴工具 MCP 服务器（stdio）

给游戏搭子 Agent 提供的本地工具集，全部只读/低频，绝不干扰游戏：
- get_game_context : 前台窗口 + 已知游戏进程识别（AI 知道你在玩什么、打到哪一步的入口）
- get_running_apps : 当前在跑的用户软件列表
- get_current_time : 本地日期时间（支持"上个月"这类时间推理）
- search_memory    : 检索长期记忆库（事实 + 事件，跨会话）
- remember_fact    : 主动记住一条事实

按需调用：只有 AI 决定调用时才执行一次轻量查询，无后台轮询。
"""

import json
import os
import sys

# 让服务器能 import 本项目与依赖（stdio 模式下走的是干净环境）
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, os.path.join(PROJECT_ROOT, "py_modules"))

from mcp.server.fastmcp import FastMCP  # noqa: E402

import psutil  # noqa: E402

mcp = FastMCP("companion-tools")

DB_PATH = os.path.join(PROJECT_ROOT, "game_companion", "data", "companion.db")

# 常见游戏进程 -> 游戏名（小写子串匹配）
KNOWN_GAMES = {
    "naraka": "永劫无间", "nbforever": "永劫无间",
    "yuanshen": "原神", "genshinimpact": "原神",
    "starrail": "崩坏：星穹铁道",
    "zenlesszone": "绝区零",
    "leagueclient": "英雄联盟", "league of legends": "英雄联盟", "lolclient": "英雄联盟",
    "cs2": "CS2", "csgo": "CS2",
    "valorant": "无畏契约", "valorant-win64": "无畏契约",
    "deltaforce": "三角洲行动",
    "tslgame": "绝地求生", "pubg": "绝地求生",
    "r5apex": "Apex英雄",
    "javaw": "Minecraft", "minecraft": "Minecraft",
    "dnf": "地下城与勇士",
    "crossfire": "穿越火线",
    "marvelfirst": "漫威争锋", "marvel rivals": "漫威争锋",
    "wuthering waves": "鸣潮", "client-win64-shipping": "鸣潮",
    "faction": "暗区突围",
    "hg_canary": "永劫无间手游", "hhh": "永劫无间手游",
}

# 系统噪音进程（get_running_apps 时过滤）
SYSTEM_NOISE = {
    "svchost", "csrss", "wininit", "services", "lsass", "smss", "winlogon",
    "dwm", "explorer", "system", "registry", "memory compression", "idle",
    "conhost", "runtimebroker", "searchhost", "startmenuexperiencehost",
    "textinputhost", "shellexperiencehost", "applicationframehost",
    "securityhealthservice", "securityhealthsystray", "msmpeng", "msoia",
    "widgetservice", "widgets", "phoneexpertservice", "dragdrophost",
    "spoolsv", "taskhostw", "sihost", "ctfmon", "audiodg", "wudfhost",
    "fontdrvhost", "dashost", "smartscreen", "searchindexer", "wmiavhost",
    "desktopwindowmanager", "lockapp", "gamebar", "gamebarftserver",
    "usysdiag", "qqpcrtp", "360tray", "360safe", "zhuandong", "hednsapi",
    "pythonw", "python", "ollama", "ollama_llama_server", "cmd", "conhost",
}

# 搭子自己 / 后端 / 通讯工具之外的白名单不算"软件"
SELF_NAMES = {"run_server", "open_llm_vtuber", "codebuddy", "workbuddy"}


def _foreground_window() -> dict:
    """取前台窗口标题与进程名（轻量 Win32 调用，不注入不挂钩）。"""
    try:
        import ctypes
        import ctypes.wintypes as wt

        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return {}
        length = user32.GetWindowTextLengthW(hwnd) + 1
        buf = ctypes.create_unicode_buffer(length)
        user32.GetWindowTextW(hwnd, buf, length)
        pid = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        proc_name = ""
        try:
            proc_name = psutil.Process(pid.value).name()
        except Exception:
            pass
        return {"title": buf.value, "process": proc_name}
    except Exception:
        return {}


def _match_game(name: str):
    n = (name or "").lower()
    for key, game in KNOWN_GAMES.items():
        if key in n:
            return game
    return None


@mcp.tool()
def get_game_context() -> str:
    """获取玩家当前的游戏上下文：前台窗口、正在运行的游戏。
    当玩家提到"我这局/这个游戏/我玩的"或你想了解他在玩什么时调用。"""
    fg = _foreground_window()
    games = []
    seen = set()
    try:
        for p in psutil.process_iter(["name"]):
            try:
                nm = (p.info["name"] or "")
            except Exception:
                continue
            g = _match_game(nm)
            if g and g not in seen:
                seen.add(g)
                games.append(g)
    except Exception:
        pass

    ctx = {"前台窗口": fg.get("title", "未知"), "前台程序": fg.get("process", "")}
    if games:
        ctx["正在运行的游戏"] = games
        ctx["最可能的游戏"] = games[0]
    else:
        fg_name = fg.get("process", "")
        g = _match_game(fg_name)
        if g:
            ctx["最可能的游戏"] = g
        elif fg.get("title"):
            ctx["提示"] = "未识别到已知游戏，可根据前台窗口标题推断"
    return json.dumps(ctx, ensure_ascii=False)


@mcp.tool()
def get_running_apps() -> str:
    """列出玩家电脑上正在运行的用户软件（已过滤系统进程）。
    当你想了解玩家还开着什么软件（直播、音乐、通讯等）时调用。"""
    apps = {}
    try:
        for p in psutil.process_iter(["name"]):
            try:
                nm = (p.info["name"] or "").strip()
            except Exception:
                continue
            base = nm.rsplit(".", 1)[0].lower() if nm else ""
            if not base or base in SYSTEM_NOISE or base in SELF_NAMES:
                continue
            apps.setdefault(base, 0)
            apps[base] += 1
    except Exception:
        pass
    names = sorted(apps.keys())[:40]
    return json.dumps({"正在运行的软件": names}, ensure_ascii=False)


@mcp.tool()
def get_current_time() -> str:
    """获取当前本地日期、时间、星期。涉及"今天/昨天/上个月"等时间推理时调用。"""
    import datetime

    now = datetime.datetime.now()
    weekdays = "一二三四五六日"
    return json.dumps(
        {
            "日期": now.strftime("%Y-%m-%d"),
            "时间": now.strftime("%H:%M"),
            "星期": "星期" + weekdays[now.weekday()],
        },
        ensure_ascii=False,
    )


def _fmt_ts(ts):
    import datetime

    try:
        return datetime.datetime.fromtimestamp(float(ts)).strftime("%Y-%m-%d")
    except Exception:
        return "未知时间"


@mcp.tool()
def search_memory(query: str) -> str:
    """检索与玩家的长期记忆（他告诉过你的事、发生过的重大事件）。
    当玩家问"还记得…吗 / 我上次/上个月…过吗"，或你需要回忆他的历史时调用。
    query 用空格分隔 1~4 个关键词，如：五杀 五杀MOBA。"""
    import sqlite3

    if not os.path.exists(DB_PATH):
        return json.dumps({"结果": "记忆库为空"}, ensure_ascii=False)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    hits = []
    try:
        words = [w for w in (query or "").replace("，", " ").split() if w]
        if not words:
            return json.dumps({"结果": "query 为空"}, ensure_ascii=False)
        for w in words:
            like = f"%{w}%"
            for r in conn.execute(
                "SELECT content, category, created_at FROM memory_facts"
                " WHERE content LIKE ? LIMIT 8",
                (like,),
            ):
                hits.append(
                    f"[事实·{r['category']}·{ _fmt_ts(r['created_at']) }] {r['content']}"
                )
            for r in conn.execute(
                "SELECT event_type, detail, sentiment, created_at FROM events"
                " WHERE detail LIKE ? OR event_type LIKE ? LIMIT 8",
                (like, like),
            ):
                hits.append(
                    f"[事件·{r['event_type']}·{r['sentiment']}·{_fmt_ts(r['created_at'])}] {r['detail']}"
                )
    finally:
        conn.close()
    # 去重并限量
    seen, out = set(), []
    for h in hits:
        if h not in seen:
            seen.add(h)
            out.append(h)
    if not out:
        return json.dumps({"结果": "没有找到相关记忆"}, ensure_ascii=False)
    return json.dumps({"相关记忆": out[:12]}, ensure_ascii=False)


@mcp.tool()
def remember_fact(content: str, category: str = "misc") -> str:
    """把一条值得长期记住的事实写进记忆库（玩家主动告诉你"记住这个"时调用）。
    category 可选：profile / hero / 梗 / habit / emotion / misc。"""
    import sqlite3
    import time as _time

    content = (content or "").strip()
    if not content:
        return json.dumps({"结果": "内容为空，未保存"}, ensure_ascii=False)
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(
            "INSERT OR IGNORE INTO memory_facts"
            " (user_id, category, content, confidence, source, created_at)"
            " VALUES ('default', ?, ?, 0.95, 'manual', ?)",
            (category or "misc", content, _time.time()),
        )
        conn.commit()
        return json.dumps({"结果": f"已记住：{content}"}, ensure_ascii=False)
    finally:
        conn.close()


# ---------------- 定时提醒 ----------------

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "game_companion", "memory"))
import reminders as _rem  # noqa: E402


@mcp.tool()
def set_reminder(
    text: str,
    delay_minutes: float = 0,
    at_time: str = "",
    repeat: str = "none",
    weekdays: str = "",
    at_day: str = "",
) -> str:
    """创建定时提醒，到点后你会主动开口提醒玩家。支持单次和循环。
    参数映射（照玩家原话选一种）：
    - "N分钟后提醒我X" → delay_minutes=N
    - "今天/明天X点提醒我X" → at_time="HH:MM"，明天再加 at_day="tomorrow"
    - "每天X点提醒我X" → repeat="daily", at_time="HH:MM"
    - "每周一三五X点提醒我X" → repeat="weekly", weekdays="1,3,5", at_time="HH:MM"
      （weekdays：1=周一 2=周二 3=周三 4=周四 5=周五 6=周六 7=周日，多个用逗号）
    循环提醒到点会自动排下一次，除非玩家取消。当玩家要定时/循环提醒时调用。"""
    try:
        res = _rem.parse_and_create(
            text=text,
            delay_minutes=delay_minutes,
            at_time=at_time,
            repeat=repeat,
            weekdays=weekdays,
            at_day=at_day,
        )
        if "错误" in res:
            return json.dumps({"结果": res["错误"]}, ensure_ascii=False)
        loop = "（循环）" if (repeat or "none") != "none" else ""
        return json.dumps(
            {
                "结果": f"已设定提醒{loop}（#{res['id']}）：{res['desc']} 提醒「{(text or '').strip()}」"
            },
            ensure_ascii=False,
        )
    except Exception as e:
        return json.dumps({"结果": f"创建失败: {e}"}, ensure_ascii=False)


@mcp.tool()
def list_reminders() -> str:
    """列出玩家还没触发的定时提醒（含每天/每周循环的）。玩家问"我有哪些提醒/闹钟"时调用。"""
    items = _rem.list_reminders()
    if not items:
        return json.dumps({"提醒": "（暂无待触发的提醒）"}, ensure_ascii=False)
    return json.dumps(
        {
            "提醒": [
                f"#{i['id']} {_rem.describe(i)} — {i['text']}" for i in items
            ]
        },
        ensure_ascii=False,
    )


@mcp.tool()
def cancel_reminder(reminder_id: int) -> str:
    """取消一个还没触发的定时提醒（玩家说"取消提醒"时调用）。"""
    ok = _rem.cancel_reminder(int(reminder_id))
    return json.dumps(
        {"结果": f"已取消 #{reminder_id}" if ok else f"#{reminder_id} 不存在或已触发"},
        ensure_ascii=False,
    )


if __name__ == "__main__":
    mcp.run()
