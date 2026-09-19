# -*- coding: utf-8 -*-
"""记忆提纯器（Memory Consolidator）

目标：事实条目会随时间无限增长，但注入 prompt 的空间有限。
提纯 = 把积累的旧事实交给 LLM 压缩成更少的紧凑条目，**保留全部关键信息**：
- 合并同义/过时条目（"在玩永劫" + "主玩永劫无间手游" → 一条）
- 保留可考证的具体事件（五杀、连败记录、约定），一条都不许丢
- 压缩结果以 source='consolidated' 写回，旧条目转为 archived（数据仍在库里可检索，
  只是不再占用注入额度——"不忘记，但瘦身"）

触发：后台提炼之后检查条目数，超过阈值才跑，异步、静默失败。
"""

import asyncio

from loguru import logger

CONSOLIDATE_PROMPT = """你是记忆整理助手。下面是关于一位玩家的事实条目（可能有很多重复、过时、琐碎的）。
请把它们**提纯**成一份紧凑清单：

规则：
- 合并同义/重复/可互相推导的条目，过时的用最新信息覆盖
- 具体事件（比赛结果、五杀、连败、约定、承诺）一条都不许丢，可缩写但不能丢关键信息
- 输出最多 {max_items} 条，每条一句话
- 只输出 JSON：{{"facts": ["条目1", "条目2", ...]}}

现有条目：
{facts}"""


class MemoryConsolidator:
    def __init__(self, base_url: str, api_key: str, model: str,
                 threshold: int = 60, max_items: int = 30):
        self.base_url = base_url
        self.api_key = api_key
        self.model = model
        self.threshold = threshold
        self.max_items = max_items

    def should_run(self, fact_count: int) -> bool:
        return fact_count >= self.threshold

    async def consolidate(self, facts: list[dict]) -> list[str] | None:
        """把事实列表压缩成紧凑清单。失败返回 None（静默）。"""
        if not facts:
            return None
        lines = "\n".join(
            f"- [{f.get('category', 'misc')}] {f['content']}" for f in facts
        )
        prompt = CONSOLIDATE_PROMPT.format(max_items=self.max_items, facts=lines)
        for attempt in range(2):
            try:
                raw = await self._call_llm(prompt)
                items = self._parse(raw)
                if items:
                    return items
                return []
            except Exception as e:  # noqa: BLE001
                logger.warning(f"记忆提纯第 {attempt + 1} 次失败: {e}")
                await asyncio.sleep(5)
        return None

    async def _call_llm(self, prompt: str) -> str:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(base_url=self.base_url, api_key=self.api_key, timeout=120)
        resp = await client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=1500,
        )
        return resp.choices[0].message.content or ""

    @staticmethod
    def _parse(raw: str) -> list[str]:
        import json
        import re

        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            return []
        try:
            data = json.loads(match.group(0))
            items = data.get("facts")
            return [str(x).strip() for x in items if str(x).strip()] if isinstance(items, list) else []
        except json.JSONDecodeError:
            return []
