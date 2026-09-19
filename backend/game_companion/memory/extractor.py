"""记忆系统 - 后台事实提炼器

定期把最近对话发给 LLM，提炼出值得长期记住的事实与事件。
设计原则（技术方案 5/9 节）：
- 全异步，绝不阻塞语音链路
- 失败静默（免费/受限 API 限流是常态），重试 1 次后放弃本轮
"""

import asyncio
import json
import re

from loguru import logger

EXTRACT_PROMPT = """你是记忆提炼助手。阅读以下玩家与AI游戏伙伴的对话片段，提炼值得长期记住的信息。

只输出 JSON，格式：
{"facts": [{"category": "profile|hero|梗|habit|emotion|misc", "content": "一句话事实"}], "events": [{"event_type": "lose_streak|win|emotion_low|milestone", "detail": "简述", "sentiment": "positive|negative|neutral"}]}

规则：
- 只记有长期价值的（昵称、主玩英雄、连败、习惯、梗、情绪低谷），寒暄废话不记
- 没有值得记的就输出 {"facts": [], "events": []}
- 不要编造对话里没有的信息

对话片段：
{conversation}"""


class FactExtractor:
    def __init__(self, base_url: str, api_key: str, model: str,
                 max_retries: int = 1):
        self.base_url = base_url
        self.api_key = api_key
        self.model = model
        self.max_retries = max_retries

    async def extract(self, messages: list[dict]) -> tuple[list[dict], list[dict]]:
        """从对话消息提炼 (facts, events)。任何失败都返回空，不抛异常。"""
        conversation = self._format(messages)
        if not conversation:
            return [], []

        for attempt in range(self.max_retries + 1):
            try:
                raw = await self._call_llm(conversation)
                return self._parse(raw)
            except Exception as e:  # noqa: BLE001 - 后台任务必须静默容错
                logger.warning(f"事实提炼第 {attempt + 1} 次失败: {e}")
                if attempt < self.max_retries:
                    await asyncio.sleep(5)
        return [], []

    async def _call_llm(self, conversation: str) -> str:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(base_url=self.base_url, api_key=self.api_key,
                             timeout=60)
        resp = await client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "user",
                 "content": EXTRACT_PROMPT.format(conversation=conversation)},
            ],
            temperature=0.2,
            max_tokens=800,
        )
        return resp.choices[0].message.content or ""

    @staticmethod
    def _format(messages: list[dict]) -> str:
        lines = []
        for m in messages[-16:]:  # 最多取最近 16 条
            role = "玩家" if m.get("role") == "user" else "AI"
            content = m.get("content")
            if isinstance(content, str) and content.strip():
                lines.append(f"{role}：{content.strip()}")
        return "\n".join(lines)

    @staticmethod
    def _parse(raw: str) -> tuple[list[dict], list[dict]]:
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            return [], []
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            return [], []
        facts = data.get("facts") if isinstance(data.get("facts"), list) else []
        events = data.get("events") if isinstance(data.get("events"), list) else []
        return facts, events
