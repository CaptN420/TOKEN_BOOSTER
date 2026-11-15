"""Token allocator: dynamic budget allocation across context categories."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, Optional

logger = logging.getLogger("boster.allocator")


@dataclass
class AllocationPlan:
    """Plan for token allocation across categories."""
    system: int = 0
    history: int = 0
    memory: int = 0
    tools: int = 0
    retrieved: int = 0
    output_reserve: int = 0
    total: int = 0

    def to_dict(self) -> Dict[str, int]:
        return {
            "system": self.system,
            "history": self.history,
            "memory": self.memory,
            "tools": self.tools,
            "retrieved": self.retrieved,
            "output_reserve": self.output_reserve,
            "total": self.total,
        }


class TokenAllocator:
    """Dynamically allocates token budget across context categories."""

    # Default priority weights (higher = more tokens allocated)
    DEFAULT_WEIGHTS = {
        "system": 1.0,
        "history": 0.8,
        "memory": 0.7,
        "tools": 0.5,
        "retrieved": 0.4,
    }

    # Minimum allocation per category (tokens)
    MIN_ALLOCATION = {
        "system": 100,
        "history": 200,
        "memory": 100,
        "tools": 50,
        "retrieved": 50,
    }

    def __init__(
        self,
        max_total_tokens: int = 30_000,
        output_reserve: int = 2_000,
        weights: Optional[Dict[str, float]] = None,
    ):
        self.max_total_tokens = max_total_tokens
        self.output_reserve = output_reserve
        self.weights = weights or self.DEFAULT_WEIGHTS.copy()

    def allocate(
        self,
        *,
        actual_system_tokens: int = 0,
        actual_history_tokens: int = 0,
        actual_memory_tokens: int = 0,
        actual_tool_tokens: int = 0,
        actual_retrieved_tokens: int = 0,
    ) -> AllocationPlan:
        """Allocate tokens dynamically based on actual usage and weights."""
        # Calculate total actual tokens
        totals = {
            "system": actual_system_tokens,
            "history": actual_history_tokens,
            "memory": actual_memory_tokens,
            "tools": actual_tool_tokens,
            "retrieved": actual_retrieved_tokens,
        }
        total_actual = sum(totals.values())

        # Budget available (total minus output reserve)
        budget = max(0, self.max_total_tokens - self.output_reserve)

        if total_actual == 0:
            # No content — return zero allocation
            return AllocationPlan(total=0)

        if total_actual <= budget:
            # Everything fits — return actual usage
            return AllocationPlan(
                system=actual_system_tokens,
                history=actual_history_tokens,
                memory=actual_memory_tokens,
                tools=actual_tool_tokens,
                retrieved=actual_retrieved_tokens,
                output_reserve=self.output_reserve,
                total=total_actual + self.output_reserve,
            )

        # Over budget — allocate proportionally by weight
        weighted_total = sum(
            totals.get(cat, 0) * self.weights.get(cat, 0.5)
            for cat in totals
        )

        plan = AllocationPlan(output_reserve=self.output_reserve)
        remaining = budget

        # Sort categories by weight (highest first)
        sorted_cats = sorted(
            self.weights.keys(),
            key=lambda c: self.weights[c],
            reverse=True,
        )

        for cat in sorted_cats:
            if total_actual == 0:
                break
            weight = self.weights.get(cat, 0.5)
            actual = totals.get(cat, 0)

            # Proportional allocation
            if weighted_total > 0:
                alloc = int(budget * (actual * weight / weighted_total))
            else:
                alloc = 0

            # Enforce minimum
            min_alloc = self.MIN_ALLOCATION.get(cat, 50)
            alloc = max(alloc, min_alloc) if actual > 0 else 0

            # Cap at actual
            alloc = min(alloc, actual)

            # Set on plan
            setattr(plan, cat, alloc)
            remaining -= alloc

        # If we still have budget, distribute proportionally
        if remaining > 0:
            for cat in sorted_cats:
                actual = totals.get(cat, 0)
                current = getattr(plan, cat)
                if current < actual:
                    add = min(remaining, actual - current)
                    setattr(plan, cat, current + add)
                    remaining -= add

        plan.total = sum(getattr(plan, c) for c in ["system", "history", "memory", "tools", "retrieved"]) + self.output_reserve
        return plan


__all__ = ["TokenAllocator", "AllocationPlan"]
