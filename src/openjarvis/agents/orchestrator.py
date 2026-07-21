"""OrchestratorAgent — multi-turn agent with tool-calling loop.

Supports two modes:

- **function_calling** (default): Uses OpenAI-format tool definitions and
  parses ``tool_calls`` from the engine response.
- **structured**: Uses a THOUGHT/TOOL/INPUT/FINAL_ANSWER text format
  (like ReAct) with a canonical system prompt from the orchestrator
  prompt registry.  This is the format used by the SFT/GRPO training
  pipelines, making the Orchestrator a distinctive trainable agent type.
"""

from __future__ import annotations

import concurrent.futures
import json
import re
from typing import Any, List, Optional

from openjarvis.agents._stubs import AgentContext, AgentResult, ToolUsingAgent
from openjarvis.core.events import EventBus
from openjarvis.core.registry import AgentRegistry
from openjarvis.core.types import Message, Role, ToolCall, ToolResult
from openjarvis.engine._stubs import InferenceEngine
from openjarvis.tools._stubs import BaseTool

# Common hallucinated tool names → registered OpenJarvis tools.
_TOOL_NAME_ALIASES: dict[str, str] = {
    "open_url": "browser_navigate",
    "open_website": "browser_navigate",
    "navigate": "browser_navigate",
    "browse": "browser_navigate",
    "browse_web": "browser_navigate",
    "visit_url": "browser_navigate",
    "open_reddit": "browser_navigate",
    "open_up_reddit": "browser_navigate",
    "search_web": "web_search",
    "web_search_tool": "web_search",
    "internet_search": "web_search",
    "google_search": "web_search",
}

_LOOSE_TOOL_NAME_RE = re.compile(
    r'["\']?(?:name|tool|tool_name)["\']?\s*:\s*["\']([^"\']+)["\']',
    re.IGNORECASE,
)
_LOOSE_URL_RE = re.compile(
    r'["\']url["\']\s*:\s*["\']([^"\']+)["\']',
    re.IGNORECASE,
)
_LOOSE_QUERY_RE = re.compile(
    r'["\']query["\']\s*:\s*["\']([^"\']+)["\']',
    re.IGNORECASE,
)


@AgentRegistry.register("orchestrator")
class OrchestratorAgent(ToolUsingAgent):
    """Multi-turn agent that routes between tools and the LLM.

    Implements a tool-calling loop:
    1. Send messages with tool definitions to the engine.
    2. If the response contains tool_calls, execute them and loop.
    3. If no tool_calls, return the final answer.
    4. Stop after ``max_turns`` iterations.

    In **structured** mode the agent instead uses a
    ``THOUGHT: / TOOL: / INPUT: / FINAL_ANSWER:`` text protocol
    identical to the format used by the orchestrator SFT/GRPO
    training pipelines.
    """

    agent_id = "orchestrator"
    _default_temperature = 0.7
    _default_max_tokens = 1024
    _default_max_turns = 10

    def __init__(
        self,
        engine: InferenceEngine,
        model: str,
        *,
        tools: Optional[List[BaseTool]] = None,
        bus: Optional[EventBus] = None,
        max_turns: Optional[int] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        mode: str = "function_calling",
        system_prompt: Optional[str] = None,
        parallel_tools: bool = True,
        interactive: bool = False,
        confirm_callback=None,
        prompt_builder: Optional[Any] = None,
    ) -> None:
        super().__init__(
            engine,
            model,
            tools=tools,
            bus=bus,
            max_turns=max_turns,
            temperature=temperature,
            max_tokens=max_tokens,
            interactive=interactive,
            confirm_callback=confirm_callback,
            prompt_builder=prompt_builder,
        )
        self._mode = mode
        self._system_prompt = system_prompt
        self._parallel_tools = parallel_tools

    def run(
        self,
        input: str,
        context: Optional[AgentContext] = None,
        **kwargs: Any,
    ) -> AgentResult:
        if self._mode == "structured":
            result = self._run_structured(input, context, **kwargs)
        else:
            result = self._run_function_calling(input, context, **kwargs)

        # Hermes-style learning loop: distill successful multi-step tasks
        # into reusable skills (fire-and-forget background reflection).
        try:
            from openjarvis.skills.loop import maybe_learn_skill

            maybe_learn_skill(self, input, result)
        except Exception:
            pass

        return result

    # ------------------------------------------------------------------
    # Structured mode (THOUGHT/TOOL/INPUT/FINAL_ANSWER)
    # ------------------------------------------------------------------

    def _run_structured(
        self,
        input: str,
        context: Optional[AgentContext] = None,
        **kwargs: Any,
    ) -> AgentResult:
        self._emit_turn_start(input)

        # Build system prompt
        if self._system_prompt:
            sys_prompt = self._system_prompt
        else:
            from openjarvis.learning.intelligence.orchestrator.prompt_registry import (
                build_system_prompt,
            )

            sys_prompt = build_system_prompt(tools=self._tools)

        messages = self._build_messages(input, context, system_prompt=sys_prompt)

        all_tool_results: list[ToolResult] = []
        turns = 0

        for _turn in range(self._max_turns):
            turns += 1

            if self._loop_guard:
                messages = self._loop_guard.compress_context(messages)

            result = self._generate(messages)
            content = result.get("content", "")

            parsed = self._parse_structured_response(content)

            # FINAL_ANSWER -> done
            if parsed["final_answer"]:
                self._emit_turn_end(turns=turns)
                return AgentResult(
                    content=parsed["final_answer"],
                    tool_results=all_tool_results,
                    turns=turns,
                )

            # TOOL -> execute
            if parsed["tool"]:
                messages.append(Message(role=Role.ASSISTANT, content=content))

                tool_call = ToolCall(
                    id=f"orch_{turns}",
                    name=parsed["tool"],
                    arguments=parsed["input"] or "{}",
                )
                tool_result = self._executor.execute(tool_call)
                all_tool_results.append(tool_result)

                observation = f"Observation: {tool_result.content}"
                messages.append(Message(role=Role.USER, content=observation))
                continue

            # Neither -> treat content as final answer
            self._emit_turn_end(turns=turns)
            return AgentResult(
                content=content,
                tool_results=all_tool_results,
                turns=turns,
            )

        # Max turns exceeded
        return self._max_turns_result(all_tool_results, turns)

    @staticmethod
    def _parse_structured_response(text: str) -> dict:
        """Parse THOUGHT/TOOL/INPUT/FINAL_ANSWER from model output."""
        result = {
            "thought": "",
            "tool": "",
            "input": "",
            "final_answer": "",
        }

        thought_match = re.search(
            r"THOUGHT:\s*(.+?)(?=\nTOOL:|\nFINAL[_ ]?ANSWER:|\Z)",
            text,
            re.DOTALL | re.IGNORECASE,
        )
        if thought_match:
            result["thought"] = thought_match.group(1).strip()

        final_match = re.search(
            r"FINAL[_ ]?ANSWER:\s*(.+)",
            text,
            re.DOTALL | re.IGNORECASE,
        )
        if final_match:
            result["final_answer"] = final_match.group(1).strip()
            return result

        tool_match = re.search(r"TOOL:\s*(.+)", text, re.IGNORECASE)
        if tool_match:
            result["tool"] = tool_match.group(1).strip()

        input_match = re.search(
            r"INPUT:\s*(.+?)(?=\nTHOUGHT:|\nTOOL:|\nFINAL|\Z)",
            text,
            re.DOTALL | re.IGNORECASE,
        )
        if input_match:
            result["input"] = input_match.group(1).strip()

        return result

    @staticmethod
    def _iter_json_objects(text: str):
        """Yield top-level JSON objects parsed from arbitrary text.

        Walks the string tracking brace depth (ignoring braces inside string
        literals) and attempts to ``json.loads`` each balanced ``{...}`` span.
        """
        depth = 0
        start = -1
        in_str = False
        esc = False
        for i, ch in enumerate(text):
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                if depth == 0:
                    start = i
                depth += 1
            elif ch == "}" and depth > 0:
                depth -= 1
                if depth == 0 and start >= 0:
                    try:
                        obj = json.loads(text[start : i + 1])
                    except Exception:
                        obj = None
                    if isinstance(obj, dict):
                        yield obj
                    start = -1

    @staticmethod
    def _resolve_tool_name(raw_name: str, known: set[str]) -> str | None:
        """Map a model-emitted tool name to a registered tool, if possible."""
        name = raw_name.strip()
        if name in known:
            return name
        alias = _TOOL_NAME_ALIASES.get(name.lower().replace("-", "_"))
        if alias and alias in known:
            return alias
        key = name.lower().replace("-", "_")
        if any(token in key for token in ("navigate", "open_url", "browse", "visit")):
            if "browser_navigate" in known:
                return "browser_navigate"
        if any(token in key for token in ("search", "lookup")):
            if "web_search" in known:
                return "web_search"
        return None

    @staticmethod
    def _default_args_for_tool(canonical: str, raw_name: str, args: dict) -> dict:
        """Fill missing required fields for alias-recovered tool calls."""
        merged = dict(args)
        raw_key = raw_name.lower().replace("-", "_")
        if canonical == "browser_navigate" and not merged.get("url"):
            if "reddit" in raw_key:
                merged["url"] = "https://www.reddit.com/"
        return merged

    def _coerce_recovered_tool(
        self, raw_name: str, args: Any, known: set[str]
    ) -> dict | None:
        canonical = self._resolve_tool_name(raw_name, known)
        if canonical is None:
            return None
        if isinstance(args, str):
            try:
                args_dict = json.loads(args) if args.strip() else {}
            except Exception:
                args_dict = {}
        elif isinstance(args, dict):
            args_dict = args
        else:
            args_dict = {}
        args_dict = self._default_args_for_tool(canonical, raw_name, args_dict)
        return {
            "name": canonical,
            "arguments": json.dumps(args_dict),
        }

    def _recover_loose_tool_calls(self, content: str, known: set[str]) -> list[dict]:
        """Recover tool calls from partial / malformed JSON text blobs."""
        recovered: list[dict] = []
        name_match = _LOOSE_TOOL_NAME_RE.search(content)
        if not name_match:
            return recovered
        raw_name = name_match.group(1)
        args: dict[str, str] = {}
        url_match = _LOOSE_URL_RE.search(content)
        if url_match:
            args["url"] = url_match.group(1)
        query_match = _LOOSE_QUERY_RE.search(content)
        if query_match:
            args["query"] = query_match.group(1)
        coerced = self._coerce_recovered_tool(raw_name, args, known)
        if coerced:
            recovered.append(coerced)
        return recovered

    def _recover_text_tool_calls(self, content: str) -> list[dict]:
        """Recover tool calls a model emitted as JSON text.

        Local models (e.g. ``qwen2.5-coder:7b``) often print a
        ```json {"name": ..., "arguments": {...}}``` blob in the content
        instead of returning OpenAI-style ``tool_calls``, or invent names
        like ``open_url`` instead of ``browser_navigate``. Known aliases
        are mapped to registered tools so the loop can execute them.
        """
        if not content or not self._tools:
            return []
        known = {t.spec.name for t in self._tools}
        recovered: list[dict] = []
        seen: set[tuple[str, str]] = set()

        def _add(raw_name: str, args: Any) -> None:
            coerced = self._coerce_recovered_tool(raw_name, args, known)
            if coerced is None:
                return
            key = (coerced["name"], coerced["arguments"])
            if key in seen:
                return
            seen.add(key)
            recovered.append(coerced)

        if "{" in content:
            for blob in self._iter_json_objects(content):
                raw_name = blob.get("name") or blob.get("tool") or blob.get("tool_name")
                if isinstance(raw_name, str):
                    _add(
                        raw_name,
                        blob.get(
                            "arguments",
                            blob.get("parameters", blob.get("input")),
                        ),
                    )

        for item in self._recover_loose_tool_calls(content, known):
            key = (item["name"], item["arguments"])
            if key not in seen:
                seen.add(key)
                recovered.append(item)

        return recovered

    def _infer_tool_from_input(self, user_input: str, known: set[str]) -> list[dict]:
        """Last-resort tool inference when the model fails to call a tool."""
        text = user_input.lower()
        if "browser_navigate" in known and "reddit" in text:
            if any(word in text for word in ("open", "browse", "visit", "launch", "go to")):
                return [
                    {
                        "name": "browser_navigate",
                        "arguments": json.dumps({"url": "https://www.reddit.com/"}),
                    }
                ]
        return []

    def _empty_after_tools_nudge(self, last_tr: ToolResult) -> str:
        """Prompt the model to continue when it returns empty text after tools."""
        if last_tr.tool_name == "COMPOSIO_SEARCH_TOOLS":
            return (
                "You called COMPOSIO_SEARCH_TOOLS but stopped without executing or "
                "answering. Call COMPOSIO_MULTI_EXECUTE_TOOL with the Reddit action "
                "from the plan (e.g. REDDIT_RETRIEVE_REDDIT_POST or "
                "REDDIT_SEARCH_ACROSS_SUBREDDITS), then reply with a plain-text "
                "summary for the user. Do not call COMPOSIO_SEARCH_TOOLS again."
            )
        return (
            "You ran tools but returned no final answer. Continue: call the next "
            "needed tool or summarize the tool results for the user in plain text."
        )

    def _synthesize_from_tool_results(self, tool_results: list[ToolResult]) -> str:
        """Build a user-visible summary when the model never answers after tools."""
        snippets = []
        for tr in tool_results[-4:]:
            body = (tr.content or "")[:800]
            snippets.append(
                f"**{tr.tool_name}** ({'ok' if tr.success else 'failed'}):\n{body}"
            )
        return (
            f"I ran {len(tool_results)} tool(s) but could not produce a final summary. "
            "Partial results:\n\n"
            + "\n\n".join(snippets)
            + "\n\nAsk me to continue or try a narrower question."
        )

    def _normalize_raw_tool_calls(
        self, raw_tool_calls: list[dict]
    ) -> list[dict]:
        """Normalize structured tool_calls (alias names, JSON args)."""
        if not raw_tool_calls or not self._tools:
            return raw_tool_calls
        known = {t.spec.name for t in self._tools}
        normalized: list[dict] = []
        for tc in raw_tool_calls:
            raw_name = tc.get("name", "")
            if not isinstance(raw_name, str):
                normalized.append(tc)
                continue
            coerced = self._coerce_recovered_tool(
                raw_name,
                tc.get("arguments", "{}"),
                known,
            )
            if coerced is None:
                normalized.append(tc)
            else:
                normalized.append({**tc, **coerced})
        return normalized

    # ------------------------------------------------------------------
    # Function-calling mode (original behaviour)
    # ------------------------------------------------------------------

    def _run_function_calling(
        self,
        input: str,
        context: Optional[AgentContext] = None,
        **kwargs: Any,
    ) -> AgentResult:
        self._emit_turn_start(input)

        # Build initial messages — pass the agent's specialized system_prompt
        # so managed ticks (consolidator, curator, …) are not replaced by the
        # main-brain default / prompt_builder template.
        messages = self._build_messages(
            input, context, system_prompt=self._system_prompt
        )

        # Get OpenAI-format tool definitions
        openai_tools = self._executor.get_openai_tools() if self._tools else []

        all_tool_results: list[ToolResult] = []
        turns = 0
        total_prompt_tokens = 0
        total_completion_tokens = 0
        empty_search_nudge_sent = False
        final_answer_nudge_sent = False

        for _turn in range(self._max_turns):
            turns += 1
            is_last_turn = turns >= self._max_turns

            if self._loop_guard:
                messages = self._loop_guard.compress_context(messages)

            # Last turn: withhold tools so local models cannot burn the budget
            # on endless knowledge_search / host_exec loops without answering.
            if (
                is_last_turn
                and all_tool_results
                and not final_answer_nudge_sent
            ):
                messages.append(
                    Message(
                        role=Role.USER,
                        content=(
                            "Turn budget is exhausted. Do NOT call any more tools. "
                            "Write your final status summary now in plain text."
                        ),
                    )
                )
                final_answer_nudge_sent = True

            gen_kwargs: dict[str, Any] = {}
            if openai_tools and not is_last_turn:
                gen_kwargs["tools"] = openai_tools

            result = self._generate(messages, **gen_kwargs)

            # Accumulate token usage
            usage = result.get("usage", {})
            total_prompt_tokens += usage.get("prompt_tokens", 0)
            total_completion_tokens += usage.get("completion_tokens", 0)

            content = result.get("content", "")
            raw_tool_calls = result.get("tool_calls", [])

            # On the last turn, discard tool calls — answer only.
            if is_last_turn:
                raw_tool_calls = []
            else:
                # Recover tool calls that the model emitted as a JSON blob in the
                # content instead of as structured tool_calls (common with small
                # local models). Without this the agent would "describe" browsing
                # or searching without ever executing the tool.
                if not raw_tool_calls and content:
                    recovered = self._recover_text_tool_calls(content)
                    if recovered:
                        raw_tool_calls = recovered
                elif raw_tool_calls:
                    raw_tool_calls = self._normalize_raw_tool_calls(raw_tool_calls)

                # No tool calls -> infer from user intent, then final answer
                if not raw_tool_calls:
                    known = {t.spec.name for t in self._tools} if self._tools else set()
                    inferred: list[dict] = []
                    # Only infer from the user message on the first turn — re-running
                    # inference every turn prevents the model from giving a final answer
                    # after tool results (burns max_turns on Composio / Reddit flows).
                    if turns == 1:
                        inferred = self._infer_tool_from_input(input, known)
                    if inferred:
                        raw_tool_calls = inferred

            if not raw_tool_calls:
                content = self._check_continuation(result, messages)
                content = self._strip_think_tags(content)
                # Qwen sometimes returns empty after a tool result (especially
                # large COMPOSIO_SEARCH_TOOLS payloads). Nudge once per stall.
                if (
                    not content.strip()
                    and all_tool_results
                    and not is_last_turn
                ):
                    messages.append(
                        Message(
                            role=Role.USER,
                            content=self._empty_after_tools_nudge(all_tool_results[-1]),
                        )
                    )
                    continue
                if not content.strip() and all_tool_results:
                    content = self._synthesize_from_tool_results(all_tool_results)
                elif not content.strip():
                    content = "Reached the turn budget without a final answer."
                self._emit_turn_end(turns=turns, content_length=len(content))
                return AgentResult(
                    content=content,
                    tool_results=all_tool_results,
                    turns=turns,
                    metadata={
                        "prompt_tokens": total_prompt_tokens,
                        "completion_tokens": total_completion_tokens,
                        "total_tokens": total_prompt_tokens + total_completion_tokens,
                        "forced_final_answer": is_last_turn and bool(all_tool_results),
                    },
                )

            # Build ToolCall objects from raw dicts
            tool_calls = [
                ToolCall(
                    id=tc.get("id", f"call_{i}"),
                    name=tc.get("name", ""),
                    arguments=tc.get("arguments", "{}"),
                )
                for i, tc in enumerate(raw_tool_calls)
            ]

            # Append assistant message with tool calls
            messages.append(
                Message(
                    role=Role.ASSISTANT,
                    content=content,
                    tool_calls=tool_calls,
                )
            )

            # Execute each tool (with loop guard check) and append results
            if self._parallel_tools and len(tool_calls) > 1:
                # Parallel execution
                def _exec_tool(tc: ToolCall) -> tuple:
                    if self._loop_guard:
                        verdict = self._loop_guard.check_call(
                            tc.name,
                            tc.arguments,
                        )
                        if verdict.blocked:
                            return tc, ToolResult(
                                tool_name=tc.name,
                                content=f"Loop guard: {verdict.reason}",
                                success=False,
                            )
                    return tc, self._executor.execute(tc)

                with concurrent.futures.ThreadPoolExecutor(
                    max_workers=len(tool_calls),
                ) as pool:
                    futures = {pool.submit(_exec_tool, tc): tc for tc in tool_calls}
                    results_map: dict[int, tuple] = {}
                    for future in concurrent.futures.as_completed(futures):
                        tc_orig = futures[future]
                        results_map[id(tc_orig)] = future.result()

                # Append results in original order
                for tc in tool_calls:
                    _, tool_result = results_map[id(tc)]
                    all_tool_results.append(tool_result)
                    messages.append(
                        Message(
                            role=Role.TOOL,
                            content=tool_result.content,
                            tool_call_id=tc.id,
                            name=tc.name,
                        )
                    )
            else:
                # Sequential execution
                for tc in tool_calls:
                    # Loop guard check before execution
                    if self._loop_guard:
                        verdict = self._loop_guard.check_call(
                            tc.name,
                            tc.arguments,
                        )
                        if verdict.blocked:
                            tool_result = ToolResult(
                                tool_name=tc.name,
                                content=f"Loop guard: {verdict.reason}",
                                success=False,
                            )
                            all_tool_results.append(tool_result)
                            messages.append(
                                Message(
                                    role=Role.TOOL,
                                    content=tool_result.content,
                                    tool_call_id=tc.id,
                                    name=tc.name,
                                )
                            )
                            continue

                    tool_result = self._executor.execute(tc)
                    all_tool_results.append(tool_result)

                    # Append tool response message
                    messages.append(
                        Message(
                            role=Role.TOOL,
                            content=tool_result.content,
                            tool_call_id=tc.id,
                            name=tc.name,
                        )
                    )

            # If every tool this turn was blocked as a repeat, stop digging
            # and force a final answer — otherwise max_turns burns on loops.
            turn_results = all_tool_results[-len(tool_calls) :]
            if turn_results and all(
                (tr.content or "").startswith("Loop guard:") for tr in turn_results
            ):
                messages.append(
                    Message(
                        role=Role.USER,
                        content=(
                            "Your last tool call(s) were blocked as identical repeats. "
                            "Do NOT call the same tool with the same arguments again. "
                            "Give your final status summary now."
                        ),
                    )
                )
            elif not empty_search_nudge_sent:
                # Different queries still burn the budget (Qwen + empty BM25).
                # After two empty searches in a row, demand a status write-up.
                streak = 0
                for tr in reversed(all_tool_results):
                    body = (tr.content or "").strip().lower()
                    if tr.tool_name in (
                        "knowledge_search",
                        "memory_search",
                    ) and (
                        body.startswith("no relevant results")
                        or body.startswith("loop guard:")
                    ):
                        streak += 1
                    else:
                        break
                if streak >= 2:
                    messages.append(
                        Message(
                            role=Role.USER,
                            content=(
                                "Recent searches returned no useful new results. "
                                "Stop searching. Write your final 5-line status now "
                                "(facts added / rules promoted / skipped reasons)."
                            ),
                        )
                    )
                    empty_search_nudge_sent = True


        # Max turns exceeded — return partial results if the model never answered
        final_content = self._strip_think_tags(content) if content else ""
        if not final_content.strip() and all_tool_results:
            snippets = []
            for tr in all_tool_results[-4:]:
                body = (tr.content or "")[:800]
                snippets.append(f"**{tr.tool_name}** ({'ok' if tr.success else 'failed'}):\n{body}")
            final_content = (
                "I hit the turn limit before finishing. Partial results from the "
                f"last {len(snippets)} tool call(s):\n\n"
                + "\n\n".join(snippets)
                + "\n\nAsk me to continue or try a narrower question."
            )
        self._emit_turn_end(turns=turns, max_turns_exceeded=True)
        return AgentResult(
            content=final_content or "Maximum turns reached without a final answer.",
            tool_results=all_tool_results,
            turns=turns,
            metadata={
                "max_turns_exceeded": True,
                "prompt_tokens": total_prompt_tokens,
                "completion_tokens": total_completion_tokens,
                "total_tokens": total_prompt_tokens + total_completion_tokens,
            },
        )


__all__ = ["OrchestratorAgent"]
