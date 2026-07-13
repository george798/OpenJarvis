"""Bridge sync agent.run() + EventBus events to an async SSE generator.

Subscribes to EventBus callbacks that push events into an asyncio.Queue,
runs agent.run() in a background thread, and yields SSE-formatted strings
from the queue for consumption by FastAPI's StreamingResponse.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from typing import AsyncGenerator

from fastapi.responses import StreamingResponse

from openjarvis.agents._stubs import AgentContext, BaseAgent
from openjarvis.core.events import Event, EventBus, EventType
from openjarvis.server.models import (
    ChatCompletionChunk,
    ChatCompletionRequest,
    DeltaMessage,
    StreamChoice,
    UsageInfo,
)

# EventTypes we subscribe to and their corresponding SSE event names
_EVENT_MAP = {
    EventType.AGENT_TURN_START: "agent_turn_start",
    EventType.INFERENCE_START: "inference_start",
    EventType.INFERENCE_END: "inference_end",
    EventType.TOOL_CALL_START: "tool_call_start",
    EventType.TOOL_CALL_END: "tool_call_end",
}

# Sentinel signalling that the agent thread has finished
_DONE = object()


def _history_token_budget() -> int:
    """Token budget for replayed conversation history.

    Derived from OLLAMA_NUM_CTX minus a reservation for the system prompt
    (persona ~8K chars), tool schemas, injected memory and the model's
    response. Overridable via HISTORY_MAX_TOKENS.
    """
    override = os.environ.get("HISTORY_MAX_TOKENS")
    if override:
        try:
            return max(512, int(override))
        except ValueError:
            pass
    try:
        num_ctx = int(os.environ.get("OLLAMA_NUM_CTX", "8192"))
    except ValueError:
        num_ctx = 8192
    # Reserve ~40% of the window for system prompt + tools + memory + reply.
    return max(1024, int(num_ctx * 0.6))


def _window_history(messages: list, max_tokens: int) -> list:
    """Trim replayed history to a token budget without losing the task anchor.

    Keeps every leading system message and the first user turn (the anchor for
    what the conversation is about), then fills the remaining budget with the
    most recent messages. Older middle turns are dropped and replaced by a
    single marker so the model knows history was elided rather than silently
    truncated by Ollama (which drops from the front, i.e. the system prompt).
    """
    if not messages:
        return messages

    per_msg = [(m, _estimate_prompt_tokens([m])) for m in messages]
    total = sum(t for _, t in per_msg)
    if total <= max_tokens:
        return messages

    from openjarvis.core.types import Message, Role

    head: list = []
    head_idx = 0
    # Preserve leading system messages.
    while head_idx < len(per_msg) and getattr(
        per_msg[head_idx][0], "role", None
    ) in (Role.SYSTEM, "system"):
        head.append(per_msg[head_idx])
        head_idx += 1
    # Preserve the first user turn as the task anchor.
    if head_idx < len(per_msg):
        head.append(per_msg[head_idx])
        head_idx += 1

    head_tokens = sum(t for _, t in head)
    remaining = max_tokens - head_tokens

    tail: list = []
    tail_tokens = 0
    for m, t in reversed(per_msg[head_idx:]):
        if tail_tokens + t > remaining:
            break
        tail.append((m, t))
        tail_tokens += t
    tail.reverse()

    dropped = len(per_msg) - len(head) - len(tail)
    result = [m for m, _ in head]
    if dropped > 0:
        result.append(
            Message(
                role=Role.SYSTEM,
                content=f"[{dropped} earlier message(s) omitted to fit the "
                "context window. Ask the user if you need details from them.]",
            )
        )
    result.extend(m for m, _ in tail)
    return result


def _estimate_prompt_tokens(messages: list) -> int:
    """Estimate prompt tokens from request messages (incl. system, user, context).

    Uses ~4 chars per token heuristic. Ensures system and all context are counted.
    """
    total = 0
    for m in messages:
        text = getattr(m, "content", None) or ""
        if isinstance(text, str):
            total += max(1, len(text) // 4)
        # Tool call messages may have structured content; still count what we can
        if hasattr(m, "tool_calls") and m.tool_calls:
            for tc in m.tool_calls:
                fn = tc.get("function") if isinstance(tc, dict) else {}
                args = fn.get("arguments", "") if isinstance(fn, dict) else ""
                total += max(1, len(str(args)) // 4)
    return total


class AgentStreamBridge:
    """Bridge between a synchronous agent and an async SSE stream.

    Pattern:
    1. Subscribe EventBus callbacks that push events into an asyncio.Queue
       via ``loop.call_soon_threadsafe()``.
    2. Run ``agent.run()`` in a thread via ``asyncio.to_thread()``.
    3. Async generator reads from queue and yields SSE-formatted strings.
    4. Unsubscribe from EventBus in ``finally`` block.
    """

    def __init__(
        self,
        agent: BaseAgent,
        bus: EventBus,
        model: str,
        request: ChatCompletionRequest,
        *,
        agent_model: str = "",
        polish_model: str | None = None,
    ) -> None:
        self._agent = agent
        self._bus = bus
        self._model = model
        self._agent_model = agent_model or model
        self._polish_model = polish_model
        self._request = request
        self._chunk_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
        self._queue: asyncio.Queue = asyncio.Queue()
        self._callbacks: dict[EventType, object] = {}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _make_callback(self, event_type: EventType):
        """Create a callback that pushes the event onto the async queue."""
        loop = asyncio.get_event_loop()

        def _cb(event: Event) -> None:
            loop.call_soon_threadsafe(self._queue.put_nowait, event)

        self._callbacks[event_type] = _cb
        return _cb

    def _subscribe_all(self) -> None:
        """Subscribe to all relevant EventBus event types."""
        for et in _EVENT_MAP:
            self._bus.subscribe(et, self._make_callback(et))

    def _unsubscribe_all(self) -> None:
        """Remove all registered subscriptions."""
        for et, cb in self._callbacks.items():
            self._bus.unsubscribe(et, cb)
        self._callbacks.clear()

    def _format_named_event(self, name: str, data: dict) -> str:
        """Format an SSE event with an explicit ``event:`` field."""
        return f"event: {name}\ndata: {json.dumps(data)}\n\n"

    def _run_agent(self) -> object:
        """Execute the agent synchronously (called via asyncio.to_thread)."""
        ctx = AgentContext()
        if getattr(self._request, "skill", None):
            try:
                from openjarvis.core.config import load_config
                from openjarvis.skills.context import apply_active_skill_to_context

                cfg = load_config()
                apply_active_skill_to_context(ctx, self._request.skill, cfg)
            except Exception:
                pass
        # Build conversation context from prior messages. The web UI replays
        # the entire history verbatim every turn; window it to a token budget
        # so the oldest turns don't push the system prompt out of the model's
        # context (Ollama truncates from the front).
        if len(self._request.messages) > 1:
            from openjarvis.core.types import Message, Role

            prior = _window_history(
                list(self._request.messages[:-1]), _history_token_budget()
            )
            for m in prior:
                m_role = getattr(m, "role", None)
                role_val = m_role.value if hasattr(m_role, "value") else m_role
                role = (
                    Role(role_val)
                    if role_val in {r.value for r in Role}
                    else Role.USER
                )
                ctx.conversation.add(
                    Message(
                        role=role,
                        content=getattr(m, "content", "") or "",
                        name=getattr(m, "name", None),
                        tool_call_id=getattr(m, "tool_call_id", None),
                    )
                )

        input_text = (
            self._request.messages[-1].content if self._request.messages else ""
        )

        # Override agent model for this request if the caller specified one
        original_model = self._agent._model
        if self._agent_model:
            self._agent._model = self._agent_model
        try:
            return self._agent.run(input_text, context=ctx)
        finally:
            self._agent._model = original_model

    # ------------------------------------------------------------------
    # Public streaming interface
    # ------------------------------------------------------------------

    async def stream(self) -> AsyncGenerator[str, None]:
        """Async generator that yields SSE-formatted strings."""
        self._subscribe_all()

        # Kick off agent.run() in a background thread
        loop = asyncio.get_event_loop()
        agent_task = asyncio.ensure_future(asyncio.to_thread(self._run_agent))

        def _on_done(fut):
            loop.call_soon_threadsafe(self._queue.put_nowait, _DONE)

        agent_task.add_done_callback(_on_done)

        try:
            # Send initial role chunk (OpenAI-compatible)
            first_chunk = ChatCompletionChunk(
                id=self._chunk_id,
                model=self._model,
                choices=[
                    StreamChoice(
                        delta=DeltaMessage(role="assistant"),
                    )
                ],
            )
            yield f"data: {first_chunk.model_dump_json()}\n\n"

            # Drain queue until the agent finishes
            while True:
                item = await self._queue.get()

                if item is _DONE:
                    break

                if isinstance(item, Event):
                    sse_name = _EVENT_MAP.get(item.event_type)
                    if sse_name:
                        yield self._format_named_event(sse_name, item.data)

            # Agent is done -- retrieve result
            try:
                agent_result = agent_task.result()
            except Exception as exc:
                import logging

                logger = logging.getLogger("openjarvis.server")
                logger.error("Agent stream error: %s", exc, exc_info=True)

                error_str = str(exc)
                if "context length" in error_str.lower() or (
                    "400" in error_str and "too long" in error_str.lower()
                ):
                    error_content = (
                        "The input is too long for the model's context window. "
                        "Please try a shorter message."
                    )
                elif "400" in error_str:
                    error_content = f"The model returned an error: {error_str}"
                else:
                    error_content = f"Sorry, an error occurred: {error_str}"
                error_chunk = ChatCompletionChunk(
                    id=self._chunk_id,
                    model=self._model,
                    choices=[
                        StreamChoice(
                            delta=DeltaMessage(content=error_content),
                            finish_reason="stop",
                        )
                    ],
                )
                yield f"data: {error_chunk.model_dump_json()}\n\n"
                yield "data: [DONE]\n\n"
                return

            # Emit tool results metadata if any
            tool_results_data = []
            for tr in agent_result.tool_results:
                tool_results_data.append(
                    {
                        "tool_name": tr.tool_name,
                        "success": tr.success,
                        "output": tr.content,
                        "latency_ms": tr.latency_seconds * 1000,
                    }
                )

            if tool_results_data:
                yield self._format_named_event(
                    "tool_results",
                    {"results": tool_results_data},
                )

            # Deliver the agent's answer to the client. Optionally polish with a
            # fast proxy model after tool runs; otherwise word-replay content.
            content = agent_result.content or ""
            engine = getattr(self._agent, "_engine", None)
            used_real_streaming = False

            input_text = (
                self._request.messages[-1].content if self._request.messages else ""
            )

            if (
                self._polish_model
                and tool_results_data
                and engine is not None
                and hasattr(engine, "stream")
            ):
                from openjarvis.core.types import Message as MsgType
                from openjarvis.core.types import Role as RoleType

                tool_summary = "\n".join(
                    f"- {tr['tool_name']}: {str(tr['output'])[:2000]}"
                    for tr in tool_results_data
                )
                polish_messages = [
                    MsgType(
                        role=RoleType.SYSTEM,
                        content=(
                            "You are Jarvis, a concise personal assistant. "
                            "Summarize the tool results for the user in plain English. "
                            "Do not mention tools or JSON."
                        ),
                    ),
                    MsgType(
                        role=RoleType.USER,
                        content=(
                            f"User request: {input_text}\n\n"
                            f"Tool results:\n{tool_summary}\n\n"
                            f"Draft answer:\n{content or '(none)'}"
                        ),
                    ),
                ]
                try:
                    streamed = []
                    async for token in engine.stream(
                        polish_messages,
                        model=self._polish_model,
                        temperature=min(self._request.temperature, 0.5),
                        max_tokens=self._request.max_tokens,
                    ):
                        if token:
                            streamed.append(token)
                            chunk = ChatCompletionChunk(
                                id=self._chunk_id,
                                model=self._model,
                                choices=[
                                    StreamChoice(
                                        delta=DeltaMessage(content=token),
                                    )
                                ],
                            )
                            yield f"data: {chunk.model_dump_json()}\n\n"
                    if streamed:
                        content = "".join(streamed)
                        used_real_streaming = True
                except Exception as polish_exc:
                    import logging as _logging

                    _logging.getLogger("openjarvis.server").warning(
                        "Response polish with %s failed: %s",
                        self._polish_model,
                        polish_exc,
                    )

            def _request_messages():
                from openjarvis.core.types import Message as MsgType
                from openjarvis.core.types import Role as RoleType

                replay_messages = []
                for m in self._request.messages:
                    role = (
                        RoleType(m.role)
                        if m.role in {r.value for r in RoleType}
                        else RoleType.USER
                    )
                    replay_messages.append(
                        MsgType(
                            role=role,
                            content=m.content or "",
                            name=m.name,
                            tool_call_id=m.tool_call_id,
                        )
                    )
                return replay_messages

            # Agent finished with no visible answer (common when a proxy
            # returns an empty non-stream body for tool-heavy prompts).
            # Fall back to a plain engine.stream() so the client sees live
            # tokens or a surfaced proxy error instead of a blank bubble.
            if (
                not used_real_streaming
                and not content
                and not tool_results_data
                and engine is not None
                and hasattr(engine, "stream")
            ):
                try:
                    streamed = []
                    async for token in engine.stream(
                        _request_messages(),
                        model=self._model,
                        temperature=self._request.temperature,
                        max_tokens=self._request.max_tokens,
                    ):
                        if token:
                            streamed.append(token)
                            chunk = ChatCompletionChunk(
                                id=self._chunk_id,
                                model=self._model,
                                choices=[
                                    StreamChoice(
                                        delta=DeltaMessage(content=token),
                                    )
                                ],
                            )
                            yield f"data: {chunk.model_dump_json()}\n\n"
                    if streamed:
                        content = "".join(streamed)
                        used_real_streaming = True
                except Exception as stream_exc:
                    import logging as _logging

                    _logging.getLogger("openjarvis.server").warning(
                        "Direct stream fallback after empty agent result failed: %s",
                        stream_exc,
                    )

            # Last-resort diagnostic: the agent finished but nothing will reach
            # the client (no streamed tokens and no content to replay). Instead
            # of the frontend's opaque "No response was generated", tell the
            # user what actually happened so it is self-diagnosable.
            diagnostic_sent = False
            if not used_real_streaming and not content:
                if tool_results_data:
                    last = tool_results_data[-1]
                    summary = str(last.get("output", ""))[:500]
                    diagnostic = (
                        f"I ran {len(tool_results_data)} tool(s) but produced no "
                        f"final text. Last tool `{last.get('tool_name')}` "
                        f"({'ok' if last.get('success') else 'failed'}): {summary}"
                    )
                else:
                    diagnostic = (
                        "The model returned no text. This usually means the "
                        "context window overflowed (long chat) or the turn ended "
                        "on a tool call with no answer. Try a shorter message or "
                        "start a new chat."
                    )
                diag_chunk = ChatCompletionChunk(
                    id=self._chunk_id,
                    model=self._model,
                    choices=[
                        StreamChoice(delta=DeltaMessage(content=diagnostic))
                    ],
                )
                yield f"data: {diag_chunk.model_dump_json()}\n\n"
                content = diagnostic
                diagnostic_sent = True

            # Fallback: word-by-word replay if real streaming was not used
            if not used_real_streaming and content and not diagnostic_sent:
                words = content.split(" ")
                for i, word in enumerate(words):
                    token = word if i == 0 else " " + word
                    chunk = ChatCompletionChunk(
                        id=self._chunk_id,
                        model=self._model,
                        choices=[
                            StreamChoice(
                                delta=DeltaMessage(content=token),
                            )
                        ],
                    )
                    yield f"data: {chunk.model_dump_json()}\n\n"
                    await asyncio.sleep(0.012)

            # Final chunk: finish_reason + usage
            prompt_tokens = agent_result.metadata.get("prompt_tokens", 0)
            completion_tokens = agent_result.metadata.get(
                "completion_tokens",
                0,
            )
            total_tokens = agent_result.metadata.get("total_tokens", 0)
            if total_tokens == 0:
                # Fallback: estimate from request messages (incl. system) + content
                completion_tokens = max(len(content) // 4, 1)
                prompt_tokens = _estimate_prompt_tokens(self._request.messages)
                total_tokens = prompt_tokens + completion_tokens

            final_chunk = ChatCompletionChunk(
                id=self._chunk_id,
                model=self._model,
                choices=[
                    StreamChoice(
                        delta=DeltaMessage(),
                        finish_reason="stop",
                    )
                ],
            )
            final_data = json.loads(final_chunk.model_dump_json())
            final_data["usage"] = UsageInfo(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
            ).model_dump()
            yield f"data: {json.dumps(final_data)}\n\n"

            yield "data: [DONE]\n\n"

        except Exception:
            # On error, cancel the agent task if still running
            if not agent_task.done():
                agent_task.cancel()
            raise
        finally:
            self._unsubscribe_all()


async def create_agent_stream(
    agent: BaseAgent,
    bus: EventBus,
    model: str,
    request: ChatCompletionRequest,
    *,
    agent_model: str = "",
    polish_model: str | None = None,
) -> StreamingResponse:
    """Create an AgentStreamBridge and return a FastAPI StreamingResponse."""
    bridge = AgentStreamBridge(
        agent,
        bus,
        model,
        request,
        agent_model=agent_model,
        polish_model=polish_model,
    )
    return StreamingResponse(
        bridge.stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


__all__ = ["AgentStreamBridge", "create_agent_stream"]
