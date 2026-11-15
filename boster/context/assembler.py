"""Context assembler: combines system, history, memory, tools, retrieved into final context."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ..core.tokenizer import count_tokens

logger = logging.getLogger("boster.assembler")


@dataclass
class AssembledContext:
    """Final assembled context ready for LLM API call."""
    messages: List[Dict[str, Any]] = field(default_factory=list)
    system_prompt: str = ""
    tools: List[Dict[str, Any]] = field(default_factory=list)
    retrieved_context: str = ""
    memory_text: str = ""
    total_tokens: int = 0
    token_breakdown: Dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "system_prompt": self.system_prompt,
            "messages": self.messages,
            "tools": self.tools,
            "retrieved_context": self.retrieved_context,
            "memory_text": self.memory_text,
            "total_tokens": self.total_tokens,
            "token_breakdown": self.token_breakdown,
        }


class ContextAssembler:
    """Assembles final context from all sources within budget."""

    def __init__(self, max_total_tokens: int = 30_000, model: str = ""):
        self.max_total_tokens = max_total_tokens
        self.model = model

    def assemble(
        self,
        *,
        system_prompt: str = "",
        messages: Optional[List[Dict[str, Any]]] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        retrieved_context: str = "",
        memory_text: str = "",
    ) -> AssembledContext:
        """Assemble context from all sources.

        Order of priority (highest to lowest):
        1. System prompt (always first)
        2. Current user message (last user msg)
        3. Recent conversation history
        4. Relevant memories
        5. Tool schemas
        6. Retrieved context
        """
        msgs = list(messages) if messages else []
        tools_list = list(tools) if tools else []

        # Calculate token usage per category
        sys_tok = count_tokens(system_prompt, self.model)
        hist_tok = count_tokens("\n".join(m.get("content", "") for m in msgs), self.model)
        mem_tok = count_tokens(memory_text, self.model)
        tool_tok = count_tokens(
            __import__("json").dumps(tools_list, ensure_ascii=False, default=str),
            self.model,
        )
        ret_tok = count_tokens(retrieved_context, self.model)

        total = sys_tok + hist_tok + mem_tok + tool_tok + ret_tok

        # If over budget, truncate history (lowest priority after system)
        if total > self.max_total_tokens and len(msgs) > 1:
            # Keep first and last messages, drop middle
            keep_ratio = self.max_total_tokens / max(total, 1)
            keep_count = max(2, int(len(msgs) * keep_ratio))
            if keep_count < len(msgs):
                drop_count = len(msgs) - keep_count
                # Drop from the middle (keep head and tail)
                head = msgs[:keep_count // 2]
                tail = msgs[-(keep_count - keep_count // 2):]
                msgs = head + tail
                hist_tok = count_tokens(
                    "\n".join(m.get("content", "") for m in msgs), self.model
                )
                total = sys_tok + hist_tok + mem_tok + tool_tok + ret_tok

        # Build final messages list
        final_messages = []
        if system_prompt:
            final_messages.append({"role": "system", "content": system_prompt})
        final_messages.extend(msgs)

        token_breakdown = {
            "system": sys_tok,
            "history": hist_tok,
            "memory": mem_tok,
            "tools": tool_tok,
            "retrieved": ret_tok,
        }

        return AssembledContext(
            messages=final_messages,
            system_prompt=system_prompt,
            tools=tools_list,
            retrieved_context=retrieved_context,
            memory_text=memory_text,
            total_tokens=total,
            token_breakdown=token_breakdown,
        )


__all__ = ["ContextAssembler", "AssembledContext"]
