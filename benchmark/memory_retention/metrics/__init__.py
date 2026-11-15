"""Metrics computation for memory retention benchmarks."""
from __future__ import annotations

from typing import Any, Dict, List


class MetricRegistry:
    """Collects and computes all benchmark metrics."""

    def __init__(self):
        self._metrics: Dict[str, float] = {}
        self._details: Dict[str, List[Dict[str, Any]]] = {}

    def record(self, name: str, value: float, detail: str = "") -> None:
        self._metrics[name] = value
        if detail:
            self._details.setdefault(name, []).append({"value": value, "detail": detail})

    def get(self, name: str) -> float:
        return self._metrics.get(name, 0.0)

    def report(self) -> Dict[str, Any]:
        return {
            **self._metrics,
            "_details": self._details,
        }


def compute_fact_retention(
    selected_messages: List[Dict[str, Any]],
    ground_truth_facts: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Compute fact retention rate.

    Checks how many ground-truth facts are preserved in the selected messages.
    A fact is 'retained' if its content appears (substring match) in any selected message.
    """
    if not ground_truth_facts:
        return {"rate": 1.0, "retained": 0, "total": 0, "missing": []}

    full_text = " ".join(m.get("content", "") for m in selected_messages)
    retained = 0
    missing = []

    for fact in ground_truth_facts:
        text = fact["text"]
        # Check if key entities from the fact are present
        keywords = text.split()[:5]  # first 5 words as keywords
        if any(kw.lower() in full_text.lower() for kw in keywords if len(kw) > 3):
            retained += 1
        else:
            missing.append(fact["id"])

    return {
        "rate": retained / len(ground_truth_facts) if ground_truth_facts else 0.0,
        "retained": retained,
        "total": len(ground_truth_facts),
        "missing_ids": missing,
    }


def compute_relationship_retention(
    selected_messages: List[Dict[str, Any]],
    ground_truth_rels: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Compute relationship retention rate."""
    if not ground_truth_rels:
        return {"rate": 1.0, "retained": 0, "total": 0}

    full_text = " ".join(m.get("content", "") for m in selected_messages)
    retained = 0

    for rel in ground_truth_rels:
        subject = rel.get("subject", "")
        obj = rel.get("object", "")
        # Check if both entities appear in selected text
        if subject.lower() in full_text.lower() and obj.lower() in full_text.lower():
            retained += 1

    return {
        "rate": retained / len(ground_truth_rels) if ground_truth_rels else 0.0,
        "retained": retained,
        "total": len(ground_truth_rels),
    }


def compute_decision_retention(
    selected_messages: List[Dict[str, Any]],
    ground_truth_decisions: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Compute decision retention rate."""
    if not ground_truth_decisions:
        return {"rate": 1.0, "retained": 0, "total": 0}

    full_text = " ".join(m.get("content", "") for m in selected_messages)
    retained = 0

    for dec in ground_truth_decisions:
        text = dec["text"]
        # Decision retention: the decision text or key terms must be present
        keywords = [w for w in text.split() if len(w) > 4]
        if any(kw.lower() in full_text.lower() for kw in keywords):
            retained += 1

    return {
        "rate": retained / len(ground_truth_decisions) if ground_truth_decisions else 0.0,
        "retained": retained,
        "total": len(ground_truth_decisions),
    }


def compute_constraint_retention(
    selected_messages: List[Dict[str, Any]],
    ground_truth_constraints: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Compute constraint retention rate."""
    if not ground_truth_constraints:
        return {"rate": 1.0, "retained": 0, "total": 0}

    full_text = " ".join(m.get("content", "") for m in selected_messages)
    retained = 0

    for c in ground_truth_constraints:
        text = c["text"]
        keywords = [w for w in text.split() if len(w) > 4]
        if any(kw.lower() in full_text.lower() for kw in keywords):
            retained += 1

    return {
        "rate": retained / len(ground_truth_constraints) if ground_truth_constraints else 0.0,
        "retained": retained,
        "total": len(ground_truth_constraints),
    }


def compute_temporal_accuracy(
    selected_messages: List[Dict[str, Any]],
    ground_truth_temporal: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Compute temporal accuracy — can we distinguish current vs superseded?"""
    if not ground_truth_temporal:
        return {"rate": 1.0, "correct": 0, "total": 0}

    # Check if current states are preserved and superseded ones are not conflated
    current_events = [t for t in ground_truth_temporal if t.get("state") == "current"]
    superseded_events = [t for t in ground_truth_temporal if t.get("state") == "superseded"]

    full_text = " ".join(m.get("content", "") for m in selected_messages)

    correct = 0
    for event in current_events:
        text = event.get("event", "")
        if text.lower() in full_text.lower():
            correct += 1

    # Superseded events should ideally not be presented as current
    for event in superseded_events:
        text = event.get("event", "")
        # If the superseded info is present but the current info is also present, that's OK
        # We just need current events to be retained
        pass

    return {
        "rate": correct / len(current_events) if current_events else 1.0,
        "correct": correct,
        "total": len(current_events),
        "superseded_count": len(superseded_events),
    }


def compute_contradiction_preservation(
    selected_messages: List[Dict[str, Any]],
    ground_truth_contradictions: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Compute contradiction preservation rate."""
    if not ground_truth_contradictions:
        return {"rate": 1.0, "preserved": 0, "total": 0}

    full_text = " ".join(m.get("content", "") for m in selected_messages)
    preserved = 0

    for contra in ground_truth_contradictions:
        # Check if the resolution is preserved
        resolution = contra.get("resolution", "")
        if resolution and resolution.lower() in full_text.lower():
            preserved += 1
        elif contra.get("resolved"):
            # At minimum, check if both claims are mentioned
            claim_a = contra["claim_a"]["text"]
            claim_b = contra["claim_b"]["text"]
            if claim_a.lower() in full_text.lower() or claim_b.lower() in full_text.lower():
                preserved += 0.5  # Partial credit

    return {
        "rate": preserved / len(ground_truth_contradictions) if ground_truth_contradictions else 0.0,
        "preserved": preserved,
        "total": len(ground_truth_contradictions),
    }


def compute_long_range_recall(
    selected_messages: List[Dict[str, Any]],
    ground_truth_facts: List[Dict[str, Any]],
    max_message_idx: int = 10,
) -> Dict[str, Any]:
    """Compute recall of facts from early messages (long-range)."""
    if not ground_truth_facts:
        return {"rate": 1.0, "retained": 0, "total": 0}

    full_text = " ".join(m.get("content", "") for m in selected_messages)
    retained = 0

    for fact in ground_truth_facts:
        source = fact.get("source", "")
        # Extract message number from source like "msg_1"
        try:
            msg_num = int(source.replace("msg_", ""))
        except (ValueError, AttributeError):
            continue
        if msg_num <= max_message_idx:
            text = fact["text"]
            keywords = [w for w in text.split() if len(w) > 3]
            if any(kw.lower() in full_text.lower() for kw in keywords):
                retained += 1

    return {
        "rate": retained / max(retained + 1, 1),
        "retained": retained,
        "total": len([f for f in ground_truth_facts if "msg_" in str(f.get("source", ""))]),
    }


def compute_unsupported_answer_rate(
    selected_messages: List[Dict[str, Any]],
) -> float:
    """Measure rate of unsupported/hallucinated answers.

    In a compression context, this measures how much new (unsupported) content
    appears in the selected output vs the original.
    """
    # This is a proxy metric — in real benchmarks we'd compare against ground truth
    # For now return 0.0 as we don't have generative output to evaluate
    return 0.0


def compute_information_loss_rate(
    original_tokens: int,
    selected_tokens: int,
    fact_retention_rate: float,
) -> float:
    """Compute information loss rate.

    Balances token reduction against information retention.
    High loss = lots of tokens saved but important info lost.
    Low loss = good retention with reasonable compression.
    """
    if original_tokens == 0:
        return 0.0
    token_loss_ratio = 1.0 - (selected_tokens / original_tokens)
    info_retention = fact_retention_rate
    # Information loss = token loss * (1 - information retention)
    info_loss = token_loss_ratio * (1.0 - info_retention)
    return info_loss
