"""RAG 知识库 - 永劫无间手游（v1：关键词检索）

v1 实现说明：按 markdown 标题切分条目，用关键词命中打分检索。
零额外依赖、毫秒级延迟，满足"提到英雄/武器/魂玉时注入背景知识"的需求。
v2 替换为 ChromaDB 向量检索（接口保持不变，仅替换 search 实现）。
"""

import re
from pathlib import Path


class KnowledgeBase:
    def __init__(self, root: str | Path, max_chars: int = 600):
        self.root = Path(root)
        self.max_chars = max_chars
        self.entries: list[dict] = []  # {title, keywords, text}
        if self.root.exists():
            self._load()

    def _load(self):
        for md in self.root.rglob("*.md"):
            try:
                content = md.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            self._split(md.stem, content)

    def _split(self, source: str, content: str):
        """按 ## 标题切条；无标题则整体为一条。"""
        chunks = re.split(r"\n(?=#{1,3}\s)", content)
        for chunk in chunks:
            chunk = chunk.strip()
            if not chunk:
                continue
            title_match = re.match(r"#{1,3}\s+(.+)", chunk)
            title = title_match.group(1).strip() if title_match else source
            # 关键词 = 标题分词 + 别名（标题里"／/（）"分隔的部分都算）
            keywords = set(re.split(r"[／/（）()\s、]+", title)) - {""}
            self.entries.append({"title": title, "keywords": keywords,
                                 "text": chunk[: self.max_chars]})

    def search(self, query: str, top_k: int = 3) -> list[dict]:
        """关键词命中打分，返回 top_k 条。无命中返回空列表。"""
        if not query:
            return []
        scored = []
        for e in self.entries:
            score = sum(2 if kw in query else 0 for kw in e["keywords"] if len(kw) >= 2)
            # 标题整体命中加权
            if e["title"] in query:
                score += 3
            if score > 0:
                scored.append((score, e))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [e for _, e in scored[:top_k]]

    def format_for_prompt(self, results: list[dict]) -> str:
        if not results:
            return ""
        body = "\n\n".join(r["text"] for r in results)
        return f"[背景知识 - 仅供参考，自然地融入回答，不要照念]\n{body}"

    @property
    def size(self) -> int:
        return len(self.entries)
