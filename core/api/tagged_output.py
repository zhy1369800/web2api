"""
Tagged tool protocol: prompt construction and non-stream parsing.

Model output contract:

    <think>...</think>
    <tool_calls>[{"name":"Read","arguments":{"path":"..."}}]</tool_calls>

or:

    <think>...</think>
    <final_answer>...</final_answer>
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from core.api.function_call import format_tools_for_prompt

TAGGED_TOOL_PROMPT_PARALLEL = """You are a tool-capable assistant.

You must respond using only the following XML-like tags:
- <think>...</think>
- <tool_calls>[{"name":"ToolName","arguments":{...}}]</tool_calls>
- <final_answer>...</final_answer>

Rules:
- You may output one or more <think> blocks.
- You must then output exactly one terminal block: either <tool_calls> or <final_answer>.
- Do not output any text outside these tags.
- In <tool_calls>, the content must be a valid JSON array. Each item must be an object with keys "name" and "arguments".
- If you need only one tool, still use <tool_calls> with an array of length 1.
- In string values inside <tool_calls>, you must escape quotes, backslashes, and newlines exactly as JSON requires.
- After </tool_calls> or </final_answer>, stop immediately.
- Never generate Observation, tool results, or a second terminal block in the same response.
- Never output <observation>; the system will provide tool results in the next turn.
"""

TAGGED_TOOL_PROMPT_SINGLE = """You are a tool-capable assistant.

You must respond using only the following XML-like tags:
- <think>...</think>
- <tool_call>{"name":"ToolName","arguments":{...}}</tool_call>
- <final_answer>...</final_answer>

Rules:
- You may output one or more <think> blocks.
- You must then output exactly one terminal block: either <tool_call> or <final_answer>.
- Do not output any text outside these tags.
- In <tool_call>, the content must be a valid JSON object with keys "name" and "arguments".
- In string values inside <tool_call>, you must escape quotes, backslashes, and newlines exactly as JSON requires.
- After </tool_call> or </final_answer>, stop immediately.
- Never generate Observation, tool results, or a second terminal block in the same response.
- Never output <observation>; the system will provide tool results in the next turn.
"""


class TaggedOutputError(ValueError):
    """Raised when model output violates the tagged tool protocol."""


@dataclass(slots=True)
class TaggedToolCall:
    name: str
    arguments: dict[str, Any]
    raw_json: str


@dataclass(slots=True)
class TaggedOutput:
    thinking: str | None = None
    tool_calls: list[TaggedToolCall] = field(default_factory=list)
    final_answer: str | None = None

    @property
    def is_tool_call(self) -> bool:
        return bool(self.tool_calls)

    @property
    def tool_call(self) -> TaggedToolCall | None:
        return self.tool_calls[0] if self.tool_calls else None

    @property
    def is_final_answer(self) -> bool:
        return self.final_answer is not None


def format_tagged_prompt(
    tools: list[dict[str, Any]],
    tools_text: str | None = None,
    *,
    allow_parallel_tool_calls: bool = True,
) -> str:
    """Build the system prefix for the tagged tool protocol."""
    if tools_text is None:
        tools_text = format_tools_for_prompt(tools)
    prompt_fixed = (
        TAGGED_TOOL_PROMPT_PARALLEL
        if allow_parallel_tool_calls
        else TAGGED_TOOL_PROMPT_SINGLE
    )
    if tools_text:
        return (
            prompt_fixed
            + "\n\n---\n\n## Available tools\n\n"
            + tools_text
            + "\n"
        )
    return prompt_fixed


def _parse_tool_call_item(payload: Any) -> TaggedToolCall:
    if not isinstance(payload, dict):
        raise TaggedOutputError("tool call payload must be an object")

    name = payload.get("name")
    arguments = payload.get("arguments")
    if not isinstance(name, str) or not name.strip():
        raise TaggedOutputError("tool_call.name must be a non-empty string")
    if not isinstance(arguments, dict):
        raise TaggedOutputError("tool_call.arguments must be an object")

    raw_json = json.dumps(payload, ensure_ascii=False)
    return TaggedToolCall(
        name=name.strip(),
        arguments=arguments,
        raw_json=raw_json,
    )


def _parse_tool_call_block(raw_json: str) -> list[TaggedToolCall]:
    try:
        payload = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        raise TaggedOutputError(f"invalid tool_call json: {exc}") from exc
    return [_parse_tool_call_item(payload)]


def _parse_tool_calls_block(raw_json: str) -> list[TaggedToolCall]:
    try:
        payload = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        raise TaggedOutputError(f"invalid tool_calls json: {exc}") from exc
    if not isinstance(payload, list):
        raise TaggedOutputError("tool_calls payload must be an array")
    if not payload:
        raise TaggedOutputError("tool_calls payload must not be empty")
    return [_parse_tool_call_item(item) for item in payload]


def parse_tagged_output(text: str) -> TaggedOutput:
    """Parse strict tagged output produced by the upstream site model."""
    if not text or not text.strip():
        raise TaggedOutputError("empty tagged output")

    content = text.strip()
    n = len(content)

    def skip_ws(pos: int) -> int:
        while pos < n and content[pos].isspace():
            pos += 1
        return pos

    def read_block(pos: int, tag: str) -> tuple[str, int]:
        open_tag = f"<{tag}>"
        close_tag = f"</{tag}>"
        if not content.startswith(open_tag, pos):
            raise TaggedOutputError(f"expected {open_tag}")
        start = pos + len(open_tag)
        end = content.find(close_tag, start)
        if end < 0:
            raise TaggedOutputError(f"missing {close_tag}")
        return content[start:end], end + len(close_tag)

    pos = skip_ws(0)
    thinking_blocks: list[str] = []
    tool_calls: list[TaggedToolCall] = []
    final_answer: str | None = None

    while pos < n:
        if content.startswith("<think>", pos):
            raw_thinking, pos = read_block(pos, "think")
            thinking = raw_thinking.strip()
            if thinking:
                thinking_blocks.append(thinking)
            pos = skip_ws(pos)
            continue

        if content.startswith("<tool_calls>", pos):
            raw_tool_json, pos = read_block(pos, "tool_calls")
            tool_calls = _parse_tool_calls_block(raw_tool_json.strip())
            break

        if content.startswith("<tool_call>", pos):
            raw_tool_json, pos = read_block(pos, "tool_call")
            tool_calls = _parse_tool_call_block(raw_tool_json.strip())
            break

        if content.startswith("<final_answer>", pos):
            raw_answer, pos = read_block(pos, "final_answer")
            final_answer = raw_answer.strip()
            break

        if content[pos].isspace():
            pos += 1
            continue

        raise TaggedOutputError("text outside tags is not allowed")

    if not tool_calls and final_answer is None:
        raise TaggedOutputError("expected <tool_calls>, <tool_call>, or <final_answer>")

    return TaggedOutput(
        thinking="\n\n".join(thinking_blocks) or None,
        tool_calls=tool_calls,
        final_answer=final_answer,
    )


def format_openai_tagged_answer(parsed: TaggedOutput) -> str:
    """Render a tagged final answer for OpenAI-compatible text content."""
    if not parsed.is_final_answer:
        raise TaggedOutputError("tagged output is not a final answer")
    parts: list[str] = []
    if parsed.thinking:
        parts.append(f"<think>{parsed.thinking}</think>")
    parts.append(parsed.final_answer or "")
    return "\n\n".join(part for part in parts if part)
