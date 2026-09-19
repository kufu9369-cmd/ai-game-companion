"""GameCompanionAgent - AI游戏搭子核心 Agent

继承基座 BasicMemoryAgent，在其对话流上叠加三层能力：
1. 长期记忆：启动时把用户资料/事实/事件注入 system prompt；
   对话中每 N 条用户消息触发一次后台事实提炼（异步，不阻塞语音）
2. RAG 知识库：用户提到游戏实体（英雄/武器/魂玉）时，
   检索命中的知识片段注入当轮上下文
3. 场景引擎钩子：情绪关键词检测（连败关怀等），
   以 system 提示形式实时调整 AI 的回应策略
"""

import asyncio
import re

from loguru import logger

from src.open_llm_vtuber.agent.agents.basic_memory_agent import BasicMemoryAgent
from src.open_llm_vtuber.agent.input_types import BatchInput

from ..memory.manager import MemoryManager
from ..memory.extractor import FactExtractor
from ..memory.consolidator import MemoryConsolidator
from ..rag.knowledge import KnowledgeBase

# 场景触发关键词（技术方案第 6 节 - 场景引擎 v1）
SCENE_RULES = [
    (r"连跪|连败|一直输|又输了|掉分", "on_user_lose",
     "玩家刚才表达了连败/失败的沮丧。按你的人格规则执行：调侃+鼓励，绝不简单安慰。"),
    (r"赢了|连胜|吃鸡|天选之人|carry", "on_user_win",
     "玩家刚才表达了胜利/carry。按你的人格规则回应（傲娇不服输或真心吹爆）。"),
    (r"好累|烦死了|不想玩了|心态崩", "on_emotion_low",
     "玩家情绪明显低落。收敛吐槽，收起嬉皮笑脸，用你自己的方式真诚陪伴。"),
    (r"下了|睡觉了|不玩了|溜了", "on_goodbye",
     "玩家要离开了。简短告别，可以提一句今天聊过的事或约下次。"),
]


class GameCompanionAgent(BasicMemoryAgent):
    def __init__(self, *args, memory_manager: MemoryManager,
                 knowledge_base: KnowledgeBase,
                 extractor: FactExtractor | None = None,
                 consolidator: MemoryConsolidator | None = None,
                 vision_enabled: bool = False,
                 use_mcpp: bool = False,
                 tool_manager=None,
                 tool_executor=None,
                 mcp_prompt_string: str = "",
                 **kwargs):
        # 记忆上下文注入 system prompt，再交给父类
        system = kwargs.get("system") or (args[1] if len(args) > 1 else "")
        memory_ctx = memory_manager.build_memory_context()
        if memory_ctx:
            system = f"{system}\n\n{memory_ctx}" if system else memory_ctx
            kwargs["system"] = system

        super().__init__(
            *args,
            use_mcpp=use_mcpp,
            tool_manager=tool_manager,
            tool_executor=tool_executor,
            mcp_prompt_string=mcp_prompt_string,
            **kwargs,
        )

        self.gc_memory = memory_manager
        self.gc_kb = knowledge_base
        self.gc_extractor = extractor
        self.gc_consolidator = consolidator
        self.gc_vision = vision_enabled
        self._extract_task: asyncio.Task | None = None
        self._consolidate_task: asyncio.Task | None = None
        logger.info(
            f"GameCompanionAgent ready | user={memory_manager.user_id}"
            f" | knowledge={knowledge_base.size} 条 | vision={vision_enabled}"
            f" | tools={'MCP' if use_mcpp else 'off'}"
            f" | consolidator={'on' if consolidator else 'off'}"
        )

    # ---------- 对话流注入 ----------

    def _to_messages(self, input_data: BatchInput):
        # 0) 视觉开关：开启时（多模态模型如 MiMo）放行用户共享的屏幕/摄像头画面；
        #    关闭时丢弃图片，避免纯文本 LLM 报错
        if input_data.images and not self.gc_vision:
            logger.info("检测到图片输入，视觉未启用已忽略")
            input_data.images = None
        elif input_data.images and self.gc_vision:
            logger.info(f"视觉输入: {len(input_data.images)} 张画面")
            self._add_message(
                "[场景提示] 玩家刚发来了他的屏幕/摄像头画面（可能是游戏画面）。"
                "如果他的问题和画面有关，结合画面回答；画面看不清就老实说。",
                "system", skip_memory=False,
            )

        # 1) 场景引擎：命中规则时注入临时 system 指令
        text = self._to_text_prompt(input_data)
        if text:
            self._apply_scene_rules(text)
            # 2) RAG：命中游戏知识时注入背景
            self._inject_knowledge(text)
            # 3) 相关回忆：跨会话检索旧事实/事件（"上个月五杀"走这里）
            self._inject_relevant_memory(text)

        messages = super()._to_messages(input_data)

        # 4) 会话追踪：到达阈值则后台提炼
        if text and self.gc_memory.on_user_message():
            self._schedule_extraction()
        return messages

    def _inject_relevant_memory(self, text: str):
        try:
            hits = self.gc_memory.relevant_recall(text)
            if hits:
                snippet = "【相关回忆】\n" + "\n".join(f"- {h}" for h in hits)
                self._add_message(snippet, "system", skip_memory=False)
                logger.info(f"相关回忆注入: {len(hits)} 条")
        except Exception as e:  # noqa: BLE001 - 召回失败绝不阻塞对话
            logger.warning(f"相关回忆检索失败（已忽略）: {e}")

    def _apply_scene_rules(self, text: str):
        for pattern, _name, instruction in SCENE_RULES:
            if re.search(pattern, text):
                self._add_message(f"[场景提示] {instruction}", "system",
                                  skip_memory=False)
                logger.info(f"场景触发: {_name}")
                break  # 一条消息最多触发一个场景

    def _inject_knowledge(self, text: str):
        results = self.gc_kb.search(text, top_k=2)
        if results:
            snippet = self.gc_kb.format_for_prompt(results)
            self._add_message(snippet, "system", skip_memory=False)
            logger.info(f"知识注入: {[r['title'] for r in results]}")

    # ---------- 后台事实提炼 ----------

    def _schedule_extraction(self):
        if self.gc_extractor is None:
            return
        if self._extract_task and not self._extract_task.done():
            return  # 上一轮还在跑，跳过
        snapshot = list(self._memory)
        self._extract_task = asyncio.create_task(self._run_extraction(snapshot))

    async def _run_extraction(self, messages: list[dict]):
        try:
            facts, events = await self.gc_extractor.extract(messages)
            if facts or events:
                n_f, n_e = self.gc_memory.save_extracted(facts, events)
                logger.info(f"记忆提炼: +{n_f} 事实, +{n_e} 事件")
            # 提纯检查：事实攒多了就压缩（异步、静默）
            self._schedule_consolidation()
        except Exception as e:  # noqa: BLE001 - 后台任务绝不冒泡
            logger.warning(f"记忆提炼异常（已忽略）: {e}")

    def _schedule_consolidation(self):
        if self.gc_consolidator is None or self._consolidate_task:
            if self._consolidate_task and not self._consolidate_task.done():
                return  # 上一轮还在跑
        try:
            count = self.gc_memory.db.count_facts(self.gc_memory.user_id)
            if not (self.gc_consolidator and self.gc_consolidator.should_run(count)):
                return
        except Exception as e:  # noqa: BLE001
            logger.warning(f"记忆计数失败（已忽略）: {e}")
            return
        snapshot = [
            f for f in self.gc_memory.db.get_facts(self.gc_memory.user_id, limit=500)
        ]
        self._consolidate_task = asyncio.create_task(
            self._run_consolidation(snapshot)
        )

    async def _run_consolidation(self, facts: list[dict]):
        try:
            items = await self.gc_consolidator.consolidate(facts)
            if items is None:
                return  # 静默失败，下轮再试
            # 写入提纯结果，然后保留「提纯条目 + 现存最新 20 条」，其余归档
            for content in items:
                self.gc_memory.db.add_fact(
                    self.gc_memory.user_id, category="consolidated",
                    content=content, confidence=0.9, source="consolidated",
                )
            recent = self.gc_memory.db.get_facts(self.gc_memory.user_id, limit=20)
            keep_ids = [f["id"] for f in recent]
            archived = self.gc_memory.db.archive_facts(
                self.gc_memory.user_id, keep_ids
            )
            logger.info(
                f"记忆提纯完成: {len(facts)} 条 → {len(items)} 条精炼"
                f"，保留 {len(keep_ids)} 条，归档 {archived} 条（数据仍在库可检索）"
            )
        except Exception as e:  # noqa: BLE001 - 后台任务绝不冒泡
            logger.warning(f"记忆提纯异常（已忽略）: {e}")

    # ---------- 会话收尾 ----------

    def set_memory_from_history(self, conf_uid: str, history_uid: str) -> None:
        """加载历史会话时，记忆上下文已在 __init__ 注入，无需额外处理。"""
        super().set_memory_from_history(conf_uid, history_uid)
