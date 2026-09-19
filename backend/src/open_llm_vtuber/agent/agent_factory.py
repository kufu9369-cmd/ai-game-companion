from typing import Type, Literal
from loguru import logger

from .agents.agent_interface import AgentInterface
from .agents.basic_memory_agent import BasicMemoryAgent
from .stateless_llm_factory import LLMFactory as StatelessLLMFactory
from .agents.hume_ai import HumeAIAgent
from .agents.letta_agent import LettaAgent

from ..mcpp.tool_manager import ToolManager
from ..mcpp.tool_executor import ToolExecutor
from typing import Optional


class AgentFactory:
    @staticmethod
    def create_agent(
        conversation_agent_choice: str,
        agent_settings: dict,
        llm_configs: dict,
        system_prompt: str,
        live2d_model=None,
        tts_preprocessor_config=None,
        **kwargs,
    ) -> Type[AgentInterface]:
        """Create an agent based on the configuration.

        Args:
            conversation_agent_choice: The type of agent to create
            agent_settings: Settings for different types of agents
            llm_configs: Pool of LLM configurations
            system_prompt: The system prompt to use
            live2d_model: Live2D model instance for expression extraction
            tts_preprocessor_config: Configuration for TTS preprocessing
            **kwargs: Additional arguments
        """
        logger.info(f"Initializing agent: {conversation_agent_choice}")

        if conversation_agent_choice == "basic_memory_agent":
            # Get the LLM provider choice from agent settings
            basic_memory_settings: dict = agent_settings.get("basic_memory_agent", {})
            llm_provider: str = basic_memory_settings.get("llm_provider")

            if not llm_provider:
                raise ValueError("LLM provider not specified for basic memory agent")

            # Get the LLM config for this provider
            llm_config: dict = llm_configs.get(llm_provider)
            interrupt_method: Literal["system", "user"] = llm_config.pop(
                "interrupt_method", "user"
            )

            if not llm_config:
                raise ValueError(
                    f"Configuration not found for LLM provider: {llm_provider}"
                )

            # Create the stateless LLM
            llm = StatelessLLMFactory.create_llm(
                llm_provider=llm_provider, system_prompt=system_prompt, **llm_config
            )

            tool_prompts = kwargs.get("system_config", {}).get("tool_prompts", {})

            # Extract MCP components/data needed by BasicMemoryAgent from kwargs
            tool_manager: Optional[ToolManager] = kwargs.get("tool_manager")
            tool_executor: Optional[ToolExecutor] = kwargs.get("tool_executor")
            mcp_prompt_string: str = kwargs.get("mcp_prompt_string", "")

            # Create the agent with the LLM and live2d_model
            return BasicMemoryAgent(
                llm=llm,
                system=system_prompt,
                live2d_model=live2d_model,
                tts_preprocessor_config=tts_preprocessor_config,
                faster_first_response=basic_memory_settings.get(
                    "faster_first_response", True
                ),
                segment_method=basic_memory_settings.get("segment_method", "pysbd"),
                use_mcpp=basic_memory_settings.get("use_mcpp", False),
                interrupt_method=interrupt_method,
                tool_prompts=tool_prompts,
                tool_manager=tool_manager,
                tool_executor=tool_executor,
                mcp_prompt_string=mcp_prompt_string,
            )

        elif conversation_agent_choice == "game_companion_agent":
            # AI游戏搭子：basic_memory_agent + 三层记忆 + RAG + 场景引擎
            from game_companion.memory.manager import MemoryManager
            from game_companion.memory.extractor import FactExtractor
            from game_companion.rag.knowledge import KnowledgeBase
            from game_companion.agent.game_companion_agent import (
                GameCompanionAgent,
            )

            gc_settings: dict = agent_settings.get("game_companion_agent", {})
            llm_provider = gc_settings.get("llm_provider", "deepseek_llm")
            llm_config = dict(llm_configs.get(llm_provider) or {})
            interrupt_method = llm_config.pop("interrupt_method", "user")
            if not llm_config:
                raise ValueError(
                    f"Configuration not found for LLM provider: {llm_provider}"
                )
            llm = StatelessLLMFactory.create_llm(
                llm_provider=llm_provider, system_prompt=system_prompt, **llm_config
            )

            memory_manager = MemoryManager(
                db_path=gc_settings.get("db_path", "game_companion/data/companion.db"),
                user_id=gc_settings.get("user_id", "default"),
                recall_probability=gc_settings.get("recall_probability", 0.3),
            )
            knowledge_base = KnowledgeBase(
                root=gc_settings.get("knowledge_root", "knowledge/games")
            )
            extractor = None
            ext_cfg = gc_settings.get("extract_llm") or {}
            if ext_cfg.get("api_key"):
                extractor = FactExtractor(
                    base_url=ext_cfg.get("base_url", "https://api.deepseek.com/v1"),
                    api_key=ext_cfg["api_key"],
                    model=ext_cfg.get("model", "deepseek-chat"),
                )

            # 记忆提纯器：事实攒到阈值后压缩（复用提炼 LLM，省一套配置）
            consolidator = None
            if extractor and gc_settings.get("consolidate_enabled", True):
                from game_companion.memory.consolidator import MemoryConsolidator

                consolidator = MemoryConsolidator(
                    base_url=ext_cfg.get("base_url", "https://api.deepseek.com/v1"),
                    api_key=ext_cfg["api_key"],
                    model=ext_cfg.get("model", "deepseek-chat"),
                    threshold=gc_settings.get("consolidate_threshold", 60),
                    max_items=gc_settings.get("consolidate_max_items", 30),
                )

            return GameCompanionAgent(
                llm=llm,
                system=system_prompt,
                live2d_model=live2d_model,
                tts_preprocessor_config=tts_preprocessor_config,
                faster_first_response=gc_settings.get("faster_first_response", True),
                segment_method=gc_settings.get("segment_method", "pysbd"),
                interrupt_method=interrupt_method,
                memory_manager=memory_manager,
                knowledge_base=knowledge_base,
                extractor=extractor,
                consolidator=consolidator,
                vision_enabled=gc_settings.get("vision_enabled", False),
                # MCP 工具调用（组件在 service_context 里按 basic_memory_agent.use_mcpp 初始化）
                use_mcpp=agent_settings.get("basic_memory_agent", {}).get(
                    "use_mcpp", False
                ),
                tool_manager=kwargs.get("tool_manager"),
                tool_executor=kwargs.get("tool_executor"),
                mcp_prompt_string=kwargs.get("mcp_prompt_string", ""),
            )

        elif conversation_agent_choice == "mem0_agent":
            from .agents.mem0_llm import LLM as Mem0LLM

            mem0_settings = agent_settings.get("mem0_agent", {})
            if not mem0_settings:
                raise ValueError("Mem0 agent settings not found")

            # Validate required settings
            required_fields = ["base_url", "model", "mem0_config"]
            for field in required_fields:
                if field not in mem0_settings:
                    raise ValueError(
                        f"Missing required field '{field}' in mem0_agent settings"
                    )

            return Mem0LLM(
                user_id=kwargs.get("user_id", "default"),
                system=system_prompt,
                live2d_model=live2d_model,
                **mem0_settings,
            )

        elif conversation_agent_choice == "hume_ai_agent":
            settings = agent_settings.get("hume_ai_agent", {})
            return HumeAIAgent(
                api_key=settings.get("api_key"),
                host=settings.get("host", "api.hume.ai"),
                config_id=settings.get("config_id"),
                idle_timeout=settings.get("idle_timeout", 15),
            )

        elif conversation_agent_choice == "letta_agent":
            settings = agent_settings.get("letta_agent", {})
            return LettaAgent(
                live2d_model=live2d_model,
                id=settings.get("id"),
                tts_preprocessor_config=tts_preprocessor_config,
                faster_first_response=settings.get("faster_first_response"),
                segment_method=settings.get("segment_method"),
                host=settings.get("host"),
                port=settings.get("port"),
            )

        else:
            raise ValueError(f"Unsupported agent type: {conversation_agent_choice}")
