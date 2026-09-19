from typing import Dict, List, Optional, Callable, TypedDict
from fastapi import WebSocket, WebSocketDisconnect
import asyncio
import io
import json
import os
import time as _time
from enum import Enum
import numpy as np
from loguru import logger

from .service_context import ServiceContext
from .chat_group import (
    ChatGroupManager,
    handle_group_operation,
    handle_client_disconnect,
    broadcast_to_group,
)
from .message_handler import message_handler
from .utils.stream_audio import prepare_audio_payload
from .chat_history_manager import (
    create_new_history,
    get_history,
    delete_history,
    get_history_list,
)
from .config_manager.utils import scan_config_alts_directory, scan_bg_directory
from .conversations.conversation_handler import (
    handle_conversation_trigger,
    handle_group_interrupt,
    handle_individual_interrupt,
)


class MessageType(Enum):
    """Enum for WebSocket message types"""

    GROUP = ["add-client-to-group", "remove-client-from-group"]
    HISTORY = [
        "fetch-history-list",
        "fetch-and-set-history",
        "create-new-history",
        "delete-history",
    ]
    CONVERSATION = ["mic-audio-end", "text-input", "ai-speak-signal"]
    CONFIG = ["fetch-configs", "switch-config"]
    CONTROL = ["interrupt-signal", "audio-play-start"]
    DATA = ["mic-audio-data"]


class WSMessage(TypedDict, total=False):
    """Type definition for WebSocket messages"""

    type: str
    action: Optional[str]
    text: Optional[str]
    audio: Optional[List[float]]
    images: Optional[List[str]]
    history_uid: Optional[str]
    file: Optional[str]
    display_text: Optional[dict]


class WebSocketHandler:
    """Handles WebSocket connections and message routing"""

    def __init__(self, default_context_cache: ServiceContext):
        """Initialize the WebSocket handler with default context"""
        self.client_connections: Dict[str, WebSocket] = {}
        self.client_contexts: Dict[str, ServiceContext] = {}
        self.chat_group_manager = ChatGroupManager()
        self.current_conversation_tasks: Dict[str, Optional[asyncio.Task]] = {}
        self.default_context_cache = default_context_cache
        self.received_data_buffers: Dict[str, np.ndarray] = {}
        self._last_client_activity: Dict[str, float] = {}
        self._proactive_tasks: Dict[str, asyncio.Task] = {}
        self._global_reminder_task: Optional[asyncio.Task] = None

        # Message handlers mapping
        self._message_handlers = self._init_message_handlers()

    def _init_message_handlers(self) -> Dict[str, Callable]:
        """Initialize message type to handler mapping"""
        return {
            "add-client-to-group": self._handle_group_operation,
            "remove-client-from-group": self._handle_group_operation,
            "request-group-info": self._handle_group_info,
            "fetch-history-list": self._handle_history_list_request,
            "fetch-and-set-history": self._handle_fetch_history,
            "create-new-history": self._handle_create_history,
            "delete-history": self._handle_delete_history,
            "interrupt-signal": self._handle_interrupt,
            "mic-audio-data": self._handle_audio_data,
            "mic-audio-end": self._handle_conversation_trigger,
            "raw-audio-data": self._handle_raw_audio_data,
            "text-input": self._handle_conversation_trigger,
            "ai-speak-signal": self._handle_conversation_trigger,
            "fetch-configs": self._handle_fetch_configs,
            "switch-config": self._handle_config_switch,
            "fetch-models": self._handle_fetch_models,
            "switch-model": self._handle_switch_model,
            "fetch-voices": self._handle_fetch_voices,
            "switch-voice": self._handle_switch_voice,
            "save-voice": self._handle_save_voice,
            "reminder-add": self._handle_reminder_add,
            "reminder-list": self._handle_reminder_list,
            "reminder-cancel": self._handle_reminder_cancel,
            "fetch-backgrounds": self._handle_fetch_backgrounds,
            "audio-play-start": self._handle_audio_play_start,
            "request-init-config": self._handle_init_config_request,
            "heartbeat": self._handle_heartbeat,
        }

    async def handle_new_connection(
        self, websocket: WebSocket, client_uid: str
    ) -> None:
        """
        Handle new WebSocket connection setup

        Args:
            websocket: The WebSocket connection
            client_uid: Unique identifier for the client

        Raises:
            Exception: If initialization fails
        """
        try:
            session_service_context = await self._init_service_context(
                websocket.send_text, client_uid
            )

            await self._store_client_data(
                websocket, client_uid, session_service_context
            )

            await self._send_initial_messages(
                websocket, client_uid, session_service_context
            )
            self._start_proactive_speaker(
                websocket, client_uid, session_service_context
            )
            self._ensure_reminder_scheduler()

            logger.info(f"Connection established for client {client_uid}")

        except Exception as e:
            logger.error(
                f"Failed to initialize connection for client {client_uid}: {e}"
            )
            await self._cleanup_failed_connection(client_uid)
            raise

    async def _store_client_data(
        self,
        websocket: WebSocket,
        client_uid: str,
        session_service_context: ServiceContext,
    ):
        """Store client data and initialize group status"""
        self.client_connections[client_uid] = websocket
        self.client_contexts[client_uid] = session_service_context
        self.received_data_buffers[client_uid] = np.array([])
        self._last_client_activity[client_uid] = asyncio.get_running_loop().time()

        self.chat_group_manager.client_group_map[client_uid] = ""
        await self.send_group_update(websocket, client_uid)

    async def _send_initial_messages(
        self,
        websocket: WebSocket,
        client_uid: str,
        session_service_context: ServiceContext,
    ):
        """Send initial connection messages to the client"""
        await websocket.send_text(
            json.dumps({"type": "full-text", "text": "Connection established"})
        )

        await websocket.send_text(
            json.dumps(
                {
                    "type": "set-model-and-conf",
                    "model_info": session_service_context.live2d_model.model_info,
                    "conf_name": session_service_context.character_config.conf_name,
                    "conf_uid": session_service_context.character_config.conf_uid,
                    "client_uid": client_uid,
                }
            )
        )

        # Send initial group status
        await self.send_group_update(websocket, client_uid)

        # Start microphone
        await websocket.send_text(json.dumps({"type": "control", "text": "start-mic"}))

    async def _init_service_context(
        self, send_text: Callable, client_uid: str
    ) -> ServiceContext:
        """Initialize service context for a new session by cloning the default context"""
        session_service_context = ServiceContext()
        await session_service_context.load_cache(
            config=self.default_context_cache.config.model_copy(deep=True),
            system_config=self.default_context_cache.system_config.model_copy(
                deep=True
            ),
            character_config=self.default_context_cache.character_config.model_copy(
                deep=True
            ),
            live2d_model=self.default_context_cache.live2d_model,
            asr_engine=self.default_context_cache.asr_engine,
            tts_engine=self.default_context_cache.tts_engine,
            vad_engine=self.default_context_cache.vad_engine,
            agent_engine=self.default_context_cache.agent_engine,
            translate_engine=self.default_context_cache.translate_engine,
            mcp_server_registery=self.default_context_cache.mcp_server_registery,
            tool_adapter=self.default_context_cache.tool_adapter,
            send_text=send_text,
            client_uid=client_uid,
        )
        return session_service_context

    async def handle_websocket_communication(
        self, websocket: WebSocket, client_uid: str
    ) -> None:
        """
        Handle ongoing WebSocket communication

        Args:
            websocket: The WebSocket connection
            client_uid: Unique identifier for the client
        """
        try:
            while True:
                try:
                    data = await websocket.receive_json()
                    self._mark_client_activity(client_uid, data)
                    message_handler.handle_message(client_uid, data)
                    await self._route_message(websocket, client_uid, data)
                except WebSocketDisconnect:
                    raise
                except json.JSONDecodeError:
                    logger.error("Invalid JSON received")
                    continue
                except Exception as e:
                    logger.error(f"Error processing message: {e}")
                    await websocket.send_text(
                        json.dumps({"type": "error", "message": str(e)})
                    )
                    continue

        except WebSocketDisconnect:
            logger.info(f"Client {client_uid} disconnected")
            raise
        except Exception as e:
            logger.error(f"Fatal error in WebSocket communication: {e}")
            raise

    async def _route_message(
        self, websocket: WebSocket, client_uid: str, data: WSMessage
    ) -> None:
        """
        Route incoming message to appropriate handler

        Args:
            websocket: The WebSocket connection
            client_uid: Client identifier
            data: Message data
        """
        msg_type = data.get("type")
        if not msg_type:
            logger.warning("Message received without type")
            return

        handler = self._message_handlers.get(msg_type)
        if handler:
            await handler(websocket, client_uid, data)
        else:
            if msg_type != "frontend-playback-complete":
                logger.warning(f"Unknown message type: {msg_type}")

    async def _handle_group_operation(
        self, websocket: WebSocket, client_uid: str, data: dict
    ) -> None:
        """Handle group-related operations"""
        operation = data.get("type")
        target_uid = data.get(
            "invitee_uid" if operation == "add-client-to-group" else "target_uid"
        )

        await handle_group_operation(
            operation=operation,
            client_uid=client_uid,
            target_uid=target_uid,
            chat_group_manager=self.chat_group_manager,
            client_connections=self.client_connections,
            send_group_update=self.send_group_update,
        )

    async def handle_disconnect(self, client_uid: str) -> None:
        """Handle client disconnection"""
        group = self.chat_group_manager.get_client_group(client_uid)
        if group:
            await handle_group_interrupt(
                group_id=group.group_id,
                heard_response="",
                current_conversation_tasks=self.current_conversation_tasks,
                chat_group_manager=self.chat_group_manager,
                client_contexts=self.client_contexts,
                broadcast_to_group=self.broadcast_to_group,
            )

        await handle_client_disconnect(
            client_uid=client_uid,
            chat_group_manager=self.chat_group_manager,
            client_connections=self.client_connections,
            send_group_update=self.send_group_update,
        )

        # Clean up other client data
        self.client_connections.pop(client_uid, None)
        self.client_contexts.pop(client_uid, None)
        self.received_data_buffers.pop(client_uid, None)
        self._last_client_activity.pop(client_uid, None)
        proactive_task = self._proactive_tasks.pop(client_uid, None)
        if proactive_task and not proactive_task.done():
            proactive_task.cancel()
        # 全局提醒调度器不随连接取消，继续服务其他/后续连接
        if client_uid in self.current_conversation_tasks:
            task = self.current_conversation_tasks[client_uid]
            if task and not task.done():
                task.cancel()
            self.current_conversation_tasks.pop(client_uid, None)

        # Call context close to clean up resources (e.g., MCPClient)
        context = self.client_contexts.get(client_uid)
        if context:
            await context.close()

        logger.info(f"Client {client_uid} disconnected")
        message_handler.cleanup_client(client_uid)

    async def _cleanup_failed_connection(self, client_uid: str) -> None:
        """Clean up failed connection data"""
        self.client_connections.pop(client_uid, None)
        self.client_contexts.pop(client_uid, None)
        self.received_data_buffers.pop(client_uid, None)
        self.chat_group_manager.client_group_map.pop(client_uid, None)
        self._last_client_activity.pop(client_uid, None)
        proactive_task = self._proactive_tasks.pop(client_uid, None)
        if proactive_task and not proactive_task.done():
            proactive_task.cancel()

        if client_uid in self.current_conversation_tasks:
            task = self.current_conversation_tasks[client_uid]
            if task and not task.done():
                task.cancel()
            self.current_conversation_tasks.pop(client_uid, None)

        message_handler.cleanup_client(client_uid)

    def _start_proactive_speaker(
        self,
        websocket: WebSocket,
        client_uid: str,
        context: ServiceContext,
    ) -> None:
        """Start the optional idle-time proactive conversation task."""
        settings = (
            context.character_config.agent_config.agent_settings.game_companion_agent
            or {}
        )
        if not settings.get("proactive_speak_enabled", True):
            return

        idle_seconds = max(30, int(settings.get("proactive_idle_seconds", 300)))
        cooldown_seconds = max(
            idle_seconds, int(settings.get("proactive_cooldown_seconds", 900))
        )
        self._proactive_tasks[client_uid] = asyncio.create_task(
            self._run_proactive_speaker(
                websocket, client_uid, idle_seconds, cooldown_seconds
            )
        )

    # ---------- 定时提醒调度（全局单例，优先级高于普通对话生命周期） ----------

    def _ensure_reminder_scheduler(self) -> None:
        """全局调度器只起一次；任意客户端在线即投递，新建对话/重连都不影响。"""
        if self._global_reminder_task is None or self._global_reminder_task.done():
            self._global_reminder_task = asyncio.create_task(
                self._run_reminder_scheduler()
            )

    async def _run_reminder_scheduler(self) -> None:
        try:
            while True:
                await asyncio.sleep(5)
                try:
                    if not self.client_connections:
                        # 没人在线：不消费，提醒留在库里，重连后补响
                        continue
                    from game_companion.memory import reminders as _rem

                    due = _rem.due_reminders()
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"提醒调度查询失败（已忽略）: {e}")
                    continue
                for r in due:
                    if not self.client_connections:
                        # 投递前全部断连：单次提醒重新入队，循环提醒已自动排下一次
                        if (r.get("repeat") or "none") == "none":
                            _rem.add_reminder(
                                r["text"],
                                fire_at=_time.time() + 5,
                                user_id=r["user_id"],
                            )
                        break
                    # 投递目标：最近活跃的客户端（优先级：最新的活跃连接）
                    target_uid = max(
                        self.client_connections,
                        key=lambda u: self._last_client_activity.get(u, 0),
                    )
                    target_ws = self.client_connections.get(target_uid)
                    active = self.current_conversation_tasks.get(target_uid)
                    if active and not active.done():
                        # 正在说话：推迟 6 秒再触发（保留循环信息）
                        _rem.snooze(r["id"], 6)
                        continue
                    logger.info(f"⏰ 触发定时提醒 #{r['id']}: {r['text']}")
                    try:
                        await self._handle_conversation_trigger(
                            target_ws,
                            target_uid,
                            {
                                "type": "ai-reminder-signal",
                                "text": r["text"],
                            },
                        )
                    except Exception as e:  # noqa: BLE001
                        logger.warning(f"提醒投递失败（10 秒后重试）: {e}")
                        # 循环提醒已自动排下一次；单次提醒重新入队
                        if (r.get("repeat") or "none") == "none":
                            _rem.add_reminder(
                                r["text"],
                                fire_at=_time.time() + 10,
                                user_id=r["user_id"],
                            )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.warning(f"全局提醒调度器停止: {error}")

    def _mark_client_activity(self, client_uid: str, data: WSMessage) -> None:
        """Ignore transport heartbeats so a connected but quiet user can be greeted."""
        if data.get("type") in {
            "text-input",
            "mic-audio-data",
            "raw-audio-data",
            "mic-audio-end",
            "interrupt-signal",
        }:
            self._last_client_activity[client_uid] = asyncio.get_running_loop().time()

    async def _run_proactive_speaker(
        self,
        websocket: WebSocket,
        client_uid: str,
        idle_seconds: int,
        cooldown_seconds: int,
    ) -> None:
        """Ask the current character to speak after a real user idle period."""
        try:
            while client_uid in self.client_connections:
                await asyncio.sleep(5)
                last_activity = self._last_client_activity.get(client_uid)
                if last_activity is None:
                    return
                if asyncio.get_running_loop().time() - last_activity < idle_seconds:
                    continue

                active_task = self.current_conversation_tasks.get(client_uid)
                if active_task and not active_task.done():
                    continue

                self._last_client_activity[client_uid] = (
                    asyncio.get_running_loop().time() + cooldown_seconds - idle_seconds
                )
                await self._handle_conversation_trigger(
                    websocket, client_uid, {"type": "ai-speak-signal"}
                )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.warning(f"Proactive speaker stopped for {client_uid}: {error}")

    async def broadcast_to_group(
        self, group_members: list[str], message: dict, exclude_uid: str = None
    ) -> None:
        """Broadcasts a message to group members"""
        await broadcast_to_group(
            group_members=group_members,
            message=message,
            client_connections=self.client_connections,
            exclude_uid=exclude_uid,
        )

    async def send_group_update(self, websocket: WebSocket, client_uid: str):
        """Sends group information to a client"""
        group = self.chat_group_manager.get_client_group(client_uid)
        if group:
            current_members = self.chat_group_manager.get_group_members(client_uid)
            await websocket.send_text(
                json.dumps(
                    {
                        "type": "group-update",
                        "members": current_members,
                        "is_owner": group.owner_uid == client_uid,
                    }
                )
            )
        else:
            await websocket.send_text(
                json.dumps(
                    {
                        "type": "group-update",
                        "members": [],
                        "is_owner": False,
                    }
                )
            )

    async def _handle_interrupt(
        self, websocket: WebSocket, client_uid: str, data: WSMessage
    ) -> None:
        """Handle conversation interruption"""
        heard_response = data.get("text", "")
        context = self.client_contexts[client_uid]
        group = self.chat_group_manager.get_client_group(client_uid)

        if group and len(group.members) > 1:
            await handle_group_interrupt(
                group_id=group.group_id,
                heard_response=heard_response,
                current_conversation_tasks=self.current_conversation_tasks,
                chat_group_manager=self.chat_group_manager,
                client_contexts=self.client_contexts,
                broadcast_to_group=self.broadcast_to_group,
            )
        else:
            await handle_individual_interrupt(
                client_uid=client_uid,
                current_conversation_tasks=self.current_conversation_tasks,
                context=context,
                heard_response=heard_response,
            )

    async def _handle_history_list_request(
        self, websocket: WebSocket, client_uid: str, data: WSMessage
    ) -> None:
        """Handle request for chat history list"""
        context = self.client_contexts[client_uid]
        histories = get_history_list(context.character_config.conf_uid)
        await websocket.send_text(
            json.dumps({"type": "history-list", "histories": histories})
        )

    async def _handle_fetch_history(
        self, websocket: WebSocket, client_uid: str, data: dict
    ):
        """Handle fetching and setting specific chat history"""
        history_uid = data.get("history_uid")
        if not history_uid:
            return

        context = self.client_contexts[client_uid]
        # Update history_uid in service context
        context.history_uid = history_uid
        context.agent_engine.set_memory_from_history(
            conf_uid=context.character_config.conf_uid,
            history_uid=history_uid,
        )

        messages = [
            msg
            for msg in get_history(
                context.character_config.conf_uid,
                history_uid,
            )
            if msg["role"] != "system"
        ]
        await websocket.send_text(
            json.dumps({"type": "history-data", "messages": messages})
        )

    async def _handle_create_history(
        self, websocket: WebSocket, client_uid: str, data: WSMessage
    ) -> None:
        """Handle creation of new chat history"""
        context = self.client_contexts[client_uid]
        history_uid = create_new_history(context.character_config.conf_uid)
        if history_uid:
            context.history_uid = history_uid
            context.agent_engine.set_memory_from_history(
                conf_uid=context.character_config.conf_uid,
                history_uid=history_uid,
            )
            await websocket.send_text(
                json.dumps(
                    {
                        "type": "new-history-created",
                        "history_uid": history_uid,
                    }
                )
            )

    async def _handle_delete_history(
        self, websocket: WebSocket, client_uid: str, data: dict
    ):
        """Handle deletion of chat history"""
        history_uid = data.get("history_uid")
        if not history_uid:
            return

        context = self.client_contexts[client_uid]
        success = delete_history(
            context.character_config.conf_uid,
            history_uid,
        )
        await websocket.send_text(
            json.dumps(
                {
                    "type": "history-deleted",
                    "success": success,
                    "history_uid": history_uid,
                }
            )
        )
        if history_uid == context.history_uid:
            context.history_uid = None

    async def _handle_audio_data(
        self, websocket: WebSocket, client_uid: str, data: WSMessage
    ) -> None:
        """Handle incoming audio data"""
        audio_data = data.get("audio", [])
        if audio_data:
            self.received_data_buffers[client_uid] = np.append(
                self.received_data_buffers[client_uid],
                np.array(audio_data, dtype=np.float32),
            )

    async def _handle_raw_audio_data(
        self, websocket: WebSocket, client_uid: str, data: WSMessage
    ) -> None:
        """Handle incoming raw audio data for VAD processing"""
        context = self.client_contexts[client_uid]
        chunk = data.get("audio", [])
        if chunk:
            for audio_bytes in context.vad_engine.detect_speech(chunk):
                if audio_bytes == b"<|PAUSE|>":
                    await websocket.send_text(
                        json.dumps({"type": "control", "text": "interrupt"})
                    )
                elif audio_bytes == b"<|RESUME|>":
                    pass
                elif len(audio_bytes) > 1024:
                    # Detected audio activity (voice)
                    self.received_data_buffers[client_uid] = np.append(
                        self.received_data_buffers[client_uid],
                        np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32),
                    )
                    await websocket.send_text(
                        json.dumps({"type": "control", "text": "mic-audio-end"})
                    )

    async def _handle_conversation_trigger(
        self, websocket: WebSocket, client_uid: str, data: WSMessage
    ) -> None:
        """Handle triggers that start a conversation"""
        await handle_conversation_trigger(
            msg_type=data.get("type", ""),
            data=data,
            client_uid=client_uid,
            context=self.client_contexts[client_uid],
            websocket=websocket,
            client_contexts=self.client_contexts,
            client_connections=self.client_connections,
            chat_group_manager=self.chat_group_manager,
            received_data_buffers=self.received_data_buffers,
            current_conversation_tasks=self.current_conversation_tasks,
            broadcast_to_group=self.broadcast_to_group,
        )

    async def _handle_fetch_configs(
        self, websocket: WebSocket, client_uid: str, data: WSMessage
    ) -> None:
        """Handle fetching available configurations

        每条配置附带 current 字段：按 conf_uid 与当前生效人设比对（无状态、
        不受缓存影响），前端据此高亮"当前人设"。
        """
        context = self.client_contexts[client_uid]
        config_files = scan_config_alts_directory(context.system_config.config_alts_dir)
        active_uid = getattr(context.character_config, "conf_uid", "") or ""
        current = ""
        for c in config_files:
            if not isinstance(c, dict):
                continue
            is_on = bool(active_uid) and c.get("uid") == active_uid
            # 同一 uid 可能对应多份配置（如 conf.yaml 与某角色卡内容相同），只标第一份
            c["current"] = bool(is_on) and not current
            if is_on and not current:
                current = c.get("filename") or ""
        await websocket.send_text(
            json.dumps(
                {
                    "type": "config-files",
                    "configs": config_files,
                    "current": current,
                },
                ensure_ascii=False,
            )
        )

    async def _handle_config_switch(
        self, websocket: WebSocket, client_uid: str, data: dict
    ):
        """Handle switching to a different configuration"""
        config_file_name = data.get("file")
        if config_file_name:
            context = self.client_contexts[client_uid]
            await context.handle_config_switch(websocket, config_file_name)
            # 切换成功后重新下发列表 → 面板"当前人设"高亮立即跟随
            try:
                await self._handle_fetch_configs(websocket, client_uid, {})
            except Exception as e:  # noqa: BLE001
                logger.warning(f"重新下发人设列表失败（已忽略）: {e}")

    async def _handle_fetch_models(
        self, websocket: WebSocket, client_uid: str, data: dict
    ) -> None:
        """列出可用 Live2D 形象（与人设解耦的形象库）

        附带 current = 当前正在用的形象名，前端据此高亮。
        """
        context = self.client_contexts[client_uid]
        try:
            path = context.live2d_model.model_dict_path
            content = context.live2d_model._load_file_content(path)
            model_dict = json.loads(content)
            try:
                current = (context.live2d_model.model_info or {}).get("name", "")
            except Exception:  # noqa: BLE001
                current = ""
            models = [
                {
                    "name": m.get("name", ""),
                    "url": m.get("url", ""),
                    "description": m.get("description", ""),
                    "current": m.get("name", "") == current,
                }
                for m in model_dict
                if m.get("name")
            ]
            await websocket.send_text(
                json.dumps(
                    {"type": "model-list", "models": models, "current": current},
                    ensure_ascii=False,
                )
            )
        except Exception as e:
            logger.error(f"Error fetching models: {e}")
            await websocket.send_text(
                json.dumps({"type": "error", "message": f"Error fetching models: {str(e)}"})
            )

    async def _handle_switch_model(
        self, websocket: WebSocket, client_uid: str, data: dict
    ) -> None:
        """只切换 Live2D 形象，保持当前人设/对话不变"""
        model_name = data.get("model")
        if not model_name:
            return
        context = self.client_contexts[client_uid]
        await context.handle_model_switch(websocket, model_name)

    # ---------- 声线（声音克隆）切换 ----------

    def _voice_engine(self, client_uid: str):
        """拿到当前客户端的 TTS 引擎（仅 zipvoice 引擎支持声线切换）"""
        context = self.client_contexts.get(client_uid)
        engine = getattr(context, "tts_engine", None) if context else None
        if engine is not None and hasattr(engine, "set_voice"):
            return engine
        return None

    async def _send_voice_list(self, websocket: WebSocket, client_uid: str) -> None:
        from .tts.zipvoice_tts import TTSEngine

        context = self.client_contexts[client_uid]
        engine = self._voice_engine(client_uid)
        voice_dir = getattr(engine, "voice_dir", None) or "./tts_voices"
        voices = TTSEngine.list_voices(voice_dir)
        current = getattr(engine, "current_voice", None)
        await websocket.send_text(
            json.dumps(
                {"type": "voice-list", "voices": voices, "current": current},
                ensure_ascii=False,
            )
        )

    async def _handle_fetch_voices(
        self, websocket: WebSocket, client_uid: str, data: dict
    ) -> None:
        """列出声线库"""
        try:
            await self._send_voice_list(websocket, client_uid)
        except Exception as e:
            logger.error(f"Error fetching voices: {e}")
            await websocket.send_text(
                json.dumps({"type": "error", "message": f"获取声线列表失败: {e}"})
            )

    # ---------- 定时提醒：面板增删查 ----------

    async def _send_reminder_list(self, websocket: WebSocket, client_uid: str) -> None:
        from game_companion.memory import reminders as _rem

        items = _rem.list_reminders()
        await websocket.send_text(
            json.dumps(
                {
                    "type": "reminder-list",
                    "reminders": [
                        {
                            "id": i["id"],
                            "text": i["text"],
                            "fire_at": _rem.fmt_ts(i["fire_at"]),
                            "desc": _rem.describe(i),
                        }
                        for i in items
                    ],
                },
                ensure_ascii=False,
            )
        )

    async def _handle_reminder_list(
        self, websocket: WebSocket, client_uid: str, data: dict
    ) -> None:
        try:
            await self._send_reminder_list(websocket, client_uid)
        except Exception as e:
            logger.error(f"Error listing reminders: {e}")

    async def _handle_reminder_add(
        self, websocket: WebSocket, client_uid: str, data: dict
    ) -> None:
        from game_companion.memory import reminders as _rem

        try:
            res = _rem.parse_and_create(
                text=(data.get("text") or "").strip(),
                delay_minutes=float(data.get("delay_minutes") or 0),
                at_time=data.get("at_time") or "",
                repeat=data.get("repeat") or "none",
                weekdays=data.get("weekdays") or "",
                at_day=data.get("at_day") or "",
            )
            if "错误" in res:
                raise ValueError(res["错误"])
            logger.info(f"⏰ 新提醒 #{res['id']}: {res['desc']}")
        except Exception as e:
            logger.warning(f"提醒创建失败: {e}")
        await self._send_reminder_list(websocket, client_uid)

    async def _handle_reminder_cancel(
        self, websocket: WebSocket, client_uid: str, data: dict
    ) -> None:
        from game_companion.memory import reminders as _rem

        try:
            _rem.cancel_reminder(int(data.get("id", 0)))
        except Exception as e:
            logger.warning(f"提醒取消失败: {e}")
        await self._send_reminder_list(websocket, client_uid)

    async def _handle_switch_voice(
        self, websocket: WebSocket, client_uid: str, data: dict
    ) -> None:
        """切换当前声线（运行时热切换，不中断对话）"""
        name = data.get("name")
        if not name:
            return
        engine = self._voice_engine(client_uid)
        if engine is None:
            await websocket.send_text(
                json.dumps(
                    {
                        "type": "error",
                        "message": "当前 TTS 引擎不支持声线切换（需要 zipvoice_tts）",
                    },
                    ensure_ascii=False,
                )
            )
            return
        try:
            engine.set_voice(name)
            await websocket.send_text(
                json.dumps(
                    {
                        "type": "voice-switched",
                        "message": f"声线已切换: {name}",
                        "current": engine.get_voice(),
                    },
                    ensure_ascii=False,
                )
            )
            await self._send_voice_list(websocket, client_uid)
        except Exception as e:
            logger.error(f"Error switching voice: {e}")
            await websocket.send_text(
                json.dumps(
                    {"type": "error", "message": f"切换声线失败: {e}"},
                    ensure_ascii=False,
                )
            )

    async def _handle_save_voice(
        self, websocket: WebSocket, client_uid: str, data: dict
    ) -> None:
        """保存一条新声线（前端录音或导入的音频，wav base64 + 参考文本）"""
        import base64
        import datetime

        import numpy as np
        import soundfile as sf

        name = (data.get("name") or "").strip()
        text = (data.get("text") or "").strip()
        audio_b64 = data.get("audio") or ""
        if not name or not text or not audio_b64:
            await websocket.send_text(
                json.dumps(
                    {"type": "error", "message": "保存声线需要：名字、参考文本、音频"},
                    ensure_ascii=False,
                )
            )
            return
        # 名字做安全清洗（当目录名用）
        for ch in '\\/:*?"<>|':
            name = name.replace(ch, "")
        if not name:
            name = "voice"
        engine = self._voice_engine(client_uid)
        voice_dir = getattr(engine, "voice_dir", None) or "./tts_voices"
        vdir = os.path.join(voice_dir, name)
        try:
            raw = base64.b64decode(audio_b64)
            samples, sr = sf.read(io.BytesIO(raw), dtype="float32", always_2d=False)
            if samples.ndim > 1:
                samples = samples.mean(axis=1)
            dur = len(samples) / sr
            if dur < 2.0:
                await websocket.send_text(
                    json.dumps(
                        {"type": "error", "message": f"参考音频太短（{dur:.1f}s），请至少 3 秒"},
                        ensure_ascii=False,
                    )
                )
                return
            if dur > 12.0:  # 截到 12 秒内，克隆更快更稳
                samples = samples[: int(12.0 * sr)]
            os.makedirs(vdir, exist_ok=True)
            # 统一转 24kHz 单声道（ZipVoice 原生采样率，省去运行时重采样）
            import scipy.signal as sps

            if sr != 24000:
                n_out = int(len(samples) * 24000 / sr)
                samples = sps.resample(samples, n_out).astype(np.float32)
                sr = 24000
            sf.write(os.path.join(vdir, "voice.wav"), samples, sr)
            meta = {
                "text": text,
                "speed": 1.0,
                "engine": "zipvoice",
                "created": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
            }
            with open(os.path.join(vdir, "voice.json"), "w", encoding="utf-8") as f:
                json.dump(meta, f, ensure_ascii=False, indent=1)
            # 使引擎缓存失效并切换到新声线
            if engine is not None and hasattr(engine, "_ref_cache"):
                engine._ref_cache.pop(name, None)
                try:
                    engine.set_voice(name)
                except Exception:
                    pass
            await websocket.send_text(
                json.dumps(
                    {
                        "type": "voice-saved",
                        "message": f"声线「{name}」已保存并启用",
                        "current": name,
                    },
                    ensure_ascii=False,
                )
            )
            await self._send_voice_list(websocket, client_uid)
        except Exception as e:
            logger.error(f"Error saving voice: {e}")
            await websocket.send_text(
                json.dumps(
                    {"type": "error", "message": f"保存声线失败: {e}"},
                    ensure_ascii=False,
                )
            )

    async def _handle_fetch_backgrounds(
        self, websocket: WebSocket, client_uid: str, data: WSMessage
    ) -> None:
        """Handle fetching available background images"""
        bg_files = scan_bg_directory()
        await websocket.send_text(
            json.dumps({"type": "background-files", "files": bg_files})
        )

    async def _handle_audio_play_start(
        self, websocket: WebSocket, client_uid: str, data: WSMessage
    ) -> None:
        """
        Handle audio playback start notification
        """
        group_members = self.chat_group_manager.get_group_members(client_uid)
        if len(group_members) > 1:
            display_text = data.get("display_text")
            if display_text:
                silent_payload = prepare_audio_payload(
                    audio_path=None,
                    display_text=display_text,
                    actions=None,
                    forwarded=True,
                )
                await self.broadcast_to_group(
                    group_members, silent_payload, exclude_uid=client_uid
                )

    async def _handle_group_info(
        self, websocket: WebSocket, client_uid: str, data: WSMessage
    ) -> None:
        """Handle group info request"""
        await self.send_group_update(websocket, client_uid)

    async def _handle_init_config_request(
        self, websocket: WebSocket, client_uid: str, data: WSMessage
    ) -> None:
        """Handle request for initialization configuration"""
        context = self.client_contexts.get(client_uid)
        if not context:
            context = self.default_context_cache

        await websocket.send_text(
            json.dumps(
                {
                    "type": "set-model-and-conf",
                    "model_info": context.live2d_model.model_info,
                    "conf_name": context.character_config.conf_name,
                    "conf_uid": context.character_config.conf_uid,
                    "client_uid": client_uid,
                }
            )
        )

    async def _handle_heartbeat(
        self, websocket: WebSocket, client_uid: str, data: WSMessage
    ) -> None:
        """Handle heartbeat messages from clients"""
        try:
            await websocket.send_json({"type": "heartbeat-ack"})
        except Exception as e:
            logger.error(f"Error sending heartbeat acknowledgment: {e}")
