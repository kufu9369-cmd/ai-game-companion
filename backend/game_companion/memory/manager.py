"""记忆系统 - 记忆管理器

职责：
1. 组装注入 system prompt 的"记忆上下文"（用户资料 + 长期事实 + 近期事件）
2. 维护会话缓冲，决定何时触发后台事实提炼
3. 记忆召回（按角色配置的 recall 概率主动提起往事）
"""

import random
import time

from .db import MemoryDB

# 触发一次后台提炼所需的用户消息条数
EXTRACT_EVERY_N_USER_MSGS = 6


class MemoryManager:
    def __init__(self, db_path: str, user_id: str = "default",
                 recall_probability: float = 0.3):
        self.db = MemoryDB(db_path)
        self.user_id = user_id
        self.recall_probability = recall_probability
        self.user = self.db.get_or_create_user(user_id)
        self._user_msg_count = 0
        self.session_started_at = time.time()

    # ---------- 注入上下文 ----------

    def build_memory_context(self, max_facts: int = 12) -> str:
        """生成追加到人格 prompt 之后的记忆上下文文本。"""
        parts: list[str] = []

        u = self.user
        profile_bits = []
        if u.get("nickname"):
            profile_bits.append(f"昵称：{u['nickname']}")
        if u.get("main_game"):
            profile_bits.append(f"主玩游戏：{u['main_game']}")
        if u.get("fav_heroes"):
            profile_bits.append(f"常用英雄：{u['fav_heroes']}")
        if profile_bits:
            parts.append("【玩家资料】\n" + "；".join(profile_bits))

        facts = self.db.get_facts(self.user_id, limit=max_facts)
        if facts:
            self.db.touch_facts([f["id"] for f in facts])
            lines = "\n".join(f"- {f['content']}" for f in facts)
            parts.append("【你记得的关于他的事】\n" + lines)

        events = self.db.get_recent_events(self.user_id, limit=3)
        if events:
            lines = "\n".join(
                f"- [{e['event_type']}] {e['detail']}" for e in reversed(events)
            )
            parts.append("【最近发生的事】\n" + lines)

        return "\n\n".join(parts)

    def maybe_recall_memory(self) -> str | None:
        """按概率挑一条旧事让 AI 主动提起（用于冷场/开场）。没命中返回 None。"""
        if random.random() > self.recall_probability:
            return None
        facts = self.db.get_facts(self.user_id, limit=20)
        if not facts:
            return None
        fact = random.choice(facts)
        self.db.touch_facts([fact["id"]])
        return fact["content"]

    def relevant_recall(self, text: str, limit: int = 4) -> list[dict]:
        """按用户消息检索相关旧记忆（跨会话召回）。命中才返回，绝不注入噪音。"""
        hits = self.db.search_memory(self.user_id, self._keywords(text), limit=limit)
        out = []
        for h in hits:
            when = time.strftime("%Y-%m-%d", time.localtime(h.get("created_at") or 0))
            if h.get("kind") == "event":
                detail = h.get("detail") or h["content"]
                out.append(f"[{when}][{h.get('category', 'event')}] {detail}")
            else:
                out.append(f"[{when}][{h.get('category', 'misc')}] {h['content']}")
        return out

    @staticmethod
    def _keywords(text: str) -> str:
        """中文关键词提取：jieba 搜索引擎模式分词，过滤虚词/单字。"""
        text = (text or "").strip()
        if not text:
            return ""
        try:
            import jieba

            words = jieba.cut_for_search(text)
        except Exception:  # noqa: BLE001 - jieba 不可用时退化为整句
            return text
        stop = {
            "是不是", "有没有", "一次", "一下", "什么", "怎么", "这个", "那个",
            "还记得", "可以", "你们", "我们", "他们", "自己", "已经", "还是",
            "就是", "不是", "没有", "时候", "现在", "上个月", "这个月",
        }
        kws = []
        for w in words:
            w = w.strip()
            if len(w) >= 2 and w not in stop and w not in kws:
                kws.append(w)
        return " ".join(kws[:8]) or text

    # ---------- 会话追踪 ----------

    def on_user_message(self) -> bool:
        """每收到一条用户消息调用。返回 True 表示应触发后台事实提炼。"""
        self._user_msg_count += 1
        return self._user_msg_count % EXTRACT_EVERY_N_USER_MSGS == 0

    # ---------- 写入（供提炼器回调） ----------

    def save_extracted(self, facts: list[dict], events: list[dict]) -> tuple[int, int]:
        """保存提炼结果，返回 (新增事实数, 新增事件数)。"""
        new_facts = 0
        for f in facts:
            content = (f.get("content") or "").strip()
            if not content:
                continue
            if self.db.add_fact(
                self.user_id,
                category=f.get("category", "misc"),
                content=content,
                confidence=float(f.get("confidence", 0.8)),
            ):
                new_facts += 1
                # 关键资料同步回 users 表
                if f.get("category") == "profile" and "昵称" in content:
                    nickname = content.split("：")[-1].strip()[:20]
                    self.db.update_user(self.user_id, nickname=nickname)
                    self.user = self.db.get_or_create_user(self.user_id)
        for e in events:
            if e.get("event_type"):
                self.db.add_event(
                    self.user_id,
                    event_type=e["event_type"],
                    detail=e.get("detail", ""),
                    sentiment=e.get("sentiment", "neutral"),
                )
        return new_facts, len(events)
