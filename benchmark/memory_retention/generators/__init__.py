"""Synthetic conversation generators with known ground truth."""
from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any, Dict, List

# Deterministic seed for reproducibility
_SEED = 42


def _seed_rng(seed: int = _SEED) -> None:
    random.seed(seed)


class ConversationGenerator:
    """Generate synthetic conversations with complete ground truth."""

    def __init__(self, seed: int = _SEED):
        _seed_rng(seed)
        self.seed = seed

    def generate_short(self) -> Dict[str, Any]:
        """10 exchanges — basic fact/relationship/test coverage."""
        exchanges = [
            {"role": "system", "content": "You are a helpful AI assistant for project management."},
            {"role": "user", "content": "I'm starting a new project called Orion. It will be written in Python."},
            {"role": "assistant", "content": "Great! Orion sounds interesting. What's the goal?"},
            {"role": "user", "content": "Orion needs to work offline — no internet dependency."},
            {"role": "assistant", "content": "Understood. Offline-first Python project. I'll note that."},
            {"role": "user", "content": "Also, Alex will be the main developer on Orion."},
            {"role": "assistant", "content": "Noted: Alex is the lead developer for Orion."},
            {"role": "user", "content": "We decided to use SQLite for local storage. It's lightweight."},
            {"role": "assistant", "content": "SQLite it is. Orion → Alex → Python → Offline → SQLite."},
            {"role": "user", "content": "One more thing — the project should use MIT licensing."},
        ]
        ground_truth = {
            "facts": [
                {"id": "f1", "text": "Project name is Orion", "source": "msg_1"},
                {"id": "f2", "text": "Orion is written in Python", "source": "msg_1"},
                {"id": "f3", "text": "Orion must work offline", "source": "msg_4"},
                {"id": "f4", "text": "Alex is the lead developer", "source": "msg_6"},
                {"id": "f5", "text": "Orion uses SQLite", "source": "msg_8"},
                {"id": "f6", "text": "Orion uses MIT license", "source": "msg_10"},
            ],
            "relationships": [
                {"type": "owns", "subject": "Alex", "object": "Orion", "source": "msg_6"},
                {"type": "uses", "subject": "Orion", "object": "SQLite", "source": "msg_8"},
                {"type": "stores", "subject": "SQLite", "object": "local storage", "source": "msg_8"},
            ],
            "preferences": [
                {"id": "p1", "text": "Offline-first architecture", "source": "msg_4"},
                {"id": "p2", "text": "MIT licensing", "source": "msg_10"},
            ],
            "decisions": [
                {"id": "d1", "text": "Use SQLite for storage", "justification": "lightweight", "source": "msg_8"},
                {"id": "d2", "text": "MIT license", "justification": None, "source": "msg_10"},
            ],
            "constraints": [
                {"id": "c1", "text": "Must work offline", "source": "msg_4"},
            ],
            "temporal": [
                {"id": "t1", "event": "Project start", "message": "msg_1"},
                {"id": "t2", "event": "License decision", "message": "msg_10"},
            ],
        }
        return {"exchanges": exchanges, "ground_truth": ground_truth, "size": "short", "exchange_count": 10}

    def generate_medium(self) -> Dict[str, Any]:
        """20 exchanges — decisions, preferences, relationships."""
        exchanges = [
            {"role": "system", "content": "You are a helpful AI assistant for project management."},
            {"role": "user", "content": "I'm starting Project Orion. Python-based, offline-first."},
            {"role": "assistant", "content": "Got it: Orion = Python + offline."},
            {"role": "user", "content": "Alex is the lead developer."},
            {"role": "assistant", "content": "Noted."},
            {"role": "user", "content": "We'll use SQLite for the database."},
            {"role": "assistant", "content": "SQLite for Orion. Good choice for offline."},
            {"role": "user", "content": "MIT license for the project."},
            {"role": "assistant", "content": "MIT it is."},
            {"role": "user", "content": "The UI should be a terminal app, not web-based."},
            {"role": "assistant", "content": "Terminal UI — got it."},
            {"role": "user", "content": "We need to support Windows, macOS, and Linux."},
            {"role": "assistant", "content": "Cross-platform: Win/macOS/Linux."},
            {"role": "user", "content": "The project should have a plugin system."},
            {"role": "assistant", "content": "Plugin architecture noted."},
            {"role": "user", "content": "Initial deadline is 3 months from now."},
            {"role": "assistant", "content": "Deadline: 3 months."},
            {"role": "user", "content": "We should use pytest for testing."},
            {"role": "assistant", "content": "pytest it is."},
            {"role": "user", "content": "The data layer should be abstracted behind a repository pattern."},
            {"role": "assistant", "content": "Repository pattern for data layer. All noted."},
        ]
        ground_truth = {
            "facts": [
                {"id": "f1", "text": "Project name is Orion", "source": "msg_1"},
                {"id": "f2", "text": "Orion is written in Python", "source": "msg_1"},
                {"id": "f3", "text": "Orion is offline-first", "source": "msg_1"},
                {"id": "f4", "text": "Alex is lead developer", "source": "msg_3"},
                {"id": "f5", "text": "Orion uses SQLite", "source": "msg_6"},
                {"id": "f6", "text": "MIT license", "source": "msg_8"},
                {"id": "f7", "text": "Terminal UI (not web)", "source": "msg_10"},
                {"id": "f8", "text": "Cross-platform: Win/macOS/Linux", "source": "msg_12"},
                {"id": "f9", "text": "Plugin system required", "source": "msg_14"},
                {"id": "f10", "text": "3-month deadline", "source": "msg_16"},
                {"id": "f11", "text": "pytest for testing", "source": "msg_18"},
                {"id": "f12", "text": "Repository pattern for data layer", "source": "msg_20"},
            ],
            "relationships": [
                {"type": "owns", "subject": "Alex", "object": "Orion", "source": "msg_3"},
                {"type": "uses", "subject": "Orion", "object": "SQLite", "source": "msg_6"},
                {"type": "runs-on", "subject": "Orion", "object": "Windows", "source": "msg_12"},
                {"type": "runs-on", "subject": "Orion", "object": "macOS", "source": "msg_12"},
                {"type": "runs-on", "subject": "Orion", "object": "Linux", "source": "msg_12"},
            ],
            "preferences": [
                {"id": "p1", "text": "Offline-first", "source": "msg_1"},
                {"id": "p2", "text": "Terminal UI", "source": "msg_10"},
                {"id": "p3", "text": "Plugin architecture", "source": "msg_14"},
                {"id": "p4", "text": "pytest", "source": "msg_18"},
            ],
            "decisions": [
                {"id": "d1", "text": "Use SQLite", "justification": "lightweight, offline", "source": "msg_6"},
                {"id": "d2", "text": "MIT license", "justification": None, "source": "msg_8"},
                {"id": "d3", "text": "Terminal UI", "justification": "offline-first", "source": "msg_10"},
                {"id": "d4", "text": "Repository pattern", "justification": "abstraction", "source": "msg_20"},
            ],
            "constraints": [
                {"id": "c1", "text": "Offline operation", "source": "msg_1"},
                {"id": "c2", "text": "Cross-platform support", "source": "msg_12"},
                {"id": "c3", "text": "3-month deadline", "source": "msg_16"},
            ],
        }
        return {"exchanges": exchanges, "ground_truth": ground_truth, "size": "medium", "exchange_count": 20}

    def generate_long(self) -> Dict[str, Any]:
        """50 exchanges — long-range recall, contradictions, temporal changes."""
        exchanges = []
        # Phase 1: Initial setup (msgs 1-10)
        exchanges.extend([
            {"role": "system", "content": "You are a helpful AI assistant for project management."},
            {"role": "user", "content": "Starting Project Orion. Python, offline-first, cross-platform."},
            {"role": "assistant", "content": "Noted: Orion = Python + offline + cross-platform."},
            {"role": "user", "content": "Alex is the lead developer."},
            {"role": "assistant", "content": "Alex → Orion lead."},
            {"role": "user", "content": "We'll use SQLite. It's lightweight and portable."},
            {"role": "assistant", "content": "SQLite chosen for data layer."},
            {"role": "user", "content": "MIT license."},
            {"role": "assistant", "content": "MIT licensed."},
            {"role": "user", "content": "Terminal UI, no web framework."},
        ])
        # Phase 2: Evolution (msgs 11-25) — requirements change
        exchanges.extend([
            {"role": "assistant", "content": "Terminal UI noted."},
            {"role": "user", "content": "We need plugin support for extensibility."},
            {"role": "assistant", "content": "Plugin system planned."},
            {"role": "user", "content": "Deadline: 3 months from today."},
            {"role": "assistant", "content": "3-month deadline set."},
            {"role": "user", "content": "Use pytest for all tests."},
            {"role": "assistant", "content": "pytest configured."},
            {"role": "user", "content": "Repository pattern for data access abstraction."},
            {"role": "assistant", "content": "Repository pattern adopted."},
            {"role": "user", "content": "We should also support async operations."},
            {"role": "assistant", "content": "Async support noted."},
            {"role": "user", "content": "Actually, let's reconsider the UI. Maybe a web dashboard would be better for monitoring."},
            {"role": "assistant", "content": "UI requirement changed: web dashboard instead of terminal."},
            {"role": "user", "content": "Good point. Let's use a lightweight web framework then."},
            {"role": "assistant", "content": "Updated: web dashboard UI (was terminal)."},
            {"role": "user", "content": "The database choice is still SQLite, right?"},
        ])
        # Phase 3: Contradictions and uncertainties (msgs 26-40)
        exchanges.extend([
            {"role": "assistant", "content": "Yes, SQLite remains the choice."},
            {"role": "user", "content": "Wait, I just thought — maybe PostgreSQL would be better for a web app?"},
            {"role": "assistant", "content": "Contradiction noted: SQLite (earlier) vs PostgreSQL (new suggestion). Current: SQLite pending confirmation."},
            {"role": "user", "content": "Let's stick with SQLite for now. We can migrate later if needed."},
            {"role": "assistant", "content": "Confirmed: SQLite. PostgreSQL was a reconsideration but not adopted."},
            {"role": "user", "content": "The project should have CI/CD with GitHub Actions."},
            {"role": "assistant", "content": "CI/CD with GitHub Actions noted."},
            {"role": "user", "content": "Documentation should be in Markdown."},
            {"role": "assistant", "content": "Markdown docs planned."},
            {"role": "user", "content": "We need a configuration file in YAML format."},
            {"role": "assistant", "content": "YAML config file required."},
            {"role": "user", "content": "The API should follow REST conventions."},
            {"role": "assistant", "content": "REST API design noted."},
            {"role": "user", "content": "Actually, GraphQL might be more flexible for our clients."},
            {"role": "assistant", "content": "Contradiction: REST (earlier) vs GraphQL (new). Current: REST pending decision."},
            {"role": "user", "content": "Let's go with REST for simplicity. We can add GraphQL later."},
        ])
        # Phase 4: Final requirements (msgs 41-50)
        exchanges.extend([
            {"role": "assistant", "content": "Confirmed: REST API. GraphQL deferred."},
            {"role": "user", "content": "The project needs a proper error handling layer."},
            {"role": "assistant", "content": "Error handling layer planned."},
            {"role": "user", "content": "Logging should use the standard logging module with JSON format."},
            {"role": "assistant", "content": "JSON structured logging noted."},
            {"role": "user", "content": "We need environment-based configuration: dev, staging, prod."},
            {"role": "assistant", "content": "Multi-environment config planned."},
            {"role": "user", "content": "The project should be containerized with Docker."},
            {"role": "assistant", "content": "Docker containerization noted."},
            {"role": "user", "content": "Final deadline is actually 4 months, not 3. We need more time for testing."},
            {"role": "assistant", "content": "Deadline updated: 4 months (was 3)."},
            {"role": "user", "content": "That's all for now. Summary when ready."},
        ])
        ground_truth = {
            "facts": [
                {"id": "f1", "text": "Project name is Orion", "source": "msg_1"},
                {"id": "f2", "text": "Orion is written in Python", "source": "msg_1"},
                {"id": "f3", "text": "Orion is offline-first", "source": "msg_1"},
                {"id": "f4", "text": "Alex is lead developer", "source": "msg_4"},
                {"id": "f5", "text": "Orion uses SQLite", "source": "msg_6"},
                {"id": "f6", "text": "MIT license", "source": "msg_8"},
                {"id": "f7", "text": "Web dashboard UI (changed from terminal)", "source": "msg_12"},
                {"id": "f8", "text": "Plugin system required", "source": "msg_13"},
                {"id": "f9", "text": "4-month deadline (updated from 3)", "source": "msg_50"},
                {"id": "f10", "text": "pytest for testing", "source": "msg_17"},
                {"id": "f11", "text": "Repository pattern", "source": "msg_19"},
                {"id": "f12", "text": "REST API (changed from GraphQL)", "source": "msg_43"},
                {"id": "f13", "text": "CI/CD with GitHub Actions", "source": "msg_27"},
                {"id": "f14", "text": "Markdown documentation", "source": "msg_28"},
                {"id": "f15", "text": "YAML configuration", "source": "msg_29"},
                {"id": "f16", "text": "Docker containerization", "source": "msg_48"},
                {"id": "f17", "text": "JSON structured logging", "source": "msg_46"},
                {"id": "f18", "text": "Multi-environment config (dev/staging/prod)", "source": "msg_47"},
            ],
            "relationships": [
                {"type": "owns", "subject": "Alex", "object": "Orion", "source": "msg_4"},
                {"type": "uses", "subject": "Orion", "object": "SQLite", "source": "msg_6"},
                {"type": "deployed-on", "subject": "Orion", "object": "Docker", "source": "msg_48"},
            ],
            "preferences": [
                {"id": "p1", "text": "Offline-first", "source": "msg_1"},
                {"id": "p2", "text": "Web dashboard UI", "source": "msg_12"},
                {"id": "p3", "text": "Plugin architecture", "source": "msg_13"},
                {"id": "p4", "text": "pytest", "source": "msg_17"},
                {"id": "p5", "text": "REST API", "source": "msg_43"},
                {"id": "p6", "text": "JSON structured logging", "source": "msg_46"},
            ],
            "decisions": [
                {"id": "d1", "text": "Use SQLite", "justification": "lightweight, offline; PostgreSQL considered but rejected", "source": "msg_25"},
                {"id": "d2", "text": "MIT license", "justification": None, "source": "msg_8"},
                {"id": "d3", "text": "Web dashboard UI", "justification": "better for monitoring (changed from terminal)", "source": "msg_12"},
                {"id": "d4", "text": "REST API", "justification": "simplicity; GraphQL considered but deferred", "source": "msg_43"},
                {"id": "d5", "text": "4-month deadline", "justification": "more time needed for testing (updated from 3)", "source": "msg_50"},
            ],
            "contradictions": [
                {
                    "id": "contra_1",
                    "claim_a": {"text": "Terminal UI", "source": "msg_10", "timestamp": "early"},
                    "claim_b": {"text": "Web dashboard UI", "source": "msg_12", "timestamp": "later"},
                    "resolved": True,
                    "resolution": "Web dashboard accepted, terminal dropped",
                },
                {
                    "id": "contra_2",
                    "claim_a": {"text": "SQLite", "source": "msg_6", "timestamp": "early"},
                    "claim_b": {"text": "PostgreSQL", "source": "msg_26", "timestamp": "later"},
                    "resolved": True,
                    "resolution": "SQLite confirmed, PostgreSQL rejected",
                },
                {
                    "id": "contra_3",
                    "claim_a": {"text": "REST API", "source": "msg_33", "timestamp": "early"},
                    "claim_b": {"text": "GraphQL", "source": "msg_34", "timestamp": "later"},
                    "resolved": True,
                    "resolution": "REST confirmed, GraphQL deferred",
                },
                {
                    "id": "contra_4",
                    "claim_a": {"text": "3-month deadline", "source": "msg_16", "timestamp": "early"},
                    "claim_b": {"text": "4-month deadline", "source": "msg_50", "timestamp": "later"},
                    "resolved": True,
                    "resolution": "4 months accepted",
                },
            ],
            "temporal": [
                {"id": "t1", "event": "Project inception", "message": "msg_1", "state": "initial"},
                {"id": "t2", "event": "UI decision: terminal", "message": "msg_10", "state": " superseded"},
                {"id": "t3", "event": "UI decision: web dashboard", "message": "msg_12", "state": "current"},
                {"id": "t4", "event": "DB decision: SQLite", "message": "msg_6", "state": "current"},
                {"id": "t5", "event": "DB reconsideration: PostgreSQL", "message": "msg_26", "state": "superseded"},
                {"id": "t6", "event": "API decision: REST", "message": "msg_33", "state": "current"},
                {"id": "t7", "event": "API reconsideration: GraphQL", "message": "msg_34", "state": "superseded"},
                {"id": "t8", "event": "Deadline: 3 months", "message": "msg_16", "state": "superseded"},
                {"id": "t9", "event": "Deadline: 4 months", "message": "msg_50", "state": "current"},
            ],
        }
        return {"exchanges": exchanges, "ground_truth": ground_truth, "size": "long", "exchange_count": 50}

    def generate_very_long(self) -> Dict[str, Any]:
        """100 exchanges — very long-range recall test."""
        exchanges = []
        # Phase 1: Core facts (msgs 1-15) — will be tested for long-range recall
        exchanges.extend([
            {"role": "system", "content": "You are a helpful AI assistant for project management."},
            {"role": "user", "content": "Starting Project Nebula. It's a distributed computing framework written in Rust."},
            {"role": "assistant", "content": "Noted: Nebula = Rust + distributed computing."},
            {"role": "user", "content": "Jordan is the architect. Taylor is the lead developer."},
            {"role": "assistant", "content": "Jordan = architect, Taylor = lead dev."},
            {"role": "user", "content": "We need zero-downtime deployments."},
            {"role": "assistant", "content": "Zero-downtime deployment requirement noted."},
            {"role": "user", "content": "The system should use gRPC for inter-service communication."},
            {"role": "assistant", "content": "gRPC for service communication."},
            {"role": "user", "content": "PostgreSQL for the main database. We need ACID compliance."},
            {"role": "assistant", "content": "PostgreSQL with ACID compliance noted."},
            {"role": "user", "content": "Redis for caching and message queuing."},
            {"role": "assistant", "content": "Redis for cache + queue."},
            {"role": "user", "content": "Kubernetes for orchestration."},
            {"role": "assistant", "content": "K8s orchestration noted."},
            {"role": "user", "content": "Apache Kafka for event streaming."},
            {"role": "assistant", "content": "Kafka event streaming noted."},
        ])
        # Phase 2: Requirements evolution (msgs 16-40)
        exchanges.extend([
            {"role": "user", "content": f"Requirement {i}: We need horizontal scaling support."} if i % 2 == 0 else {"role": "assistant", "content": f"Noted: requirement {i} about scaling."}
            for i in range(16, 41)
        ])
        # Phase 3: Contradictions (msgs 41-55)
        exchanges.extend([
            {"role": "user", "content": "Actually, let's use REST instead of gRPC. Simpler for our clients."},
            {"role": "assistant", "content": "Contradiction: gRPC (earlier) vs REST (new). Current: gRPC pending review."},
            {"role": "user", "content": "After discussion, let's keep gRPC. REST was too limiting."},
            {"role": "assistant", "content": "Confirmed: gRPC. REST rejected."},
            {"role": "user", "content": "We should also consider GraphQL for the admin API."},
            {"role": "assistant", "content": "GraphQL considered for admin API (separate from gRPC core)."},
        ])
        # Phase 4: More requirements (msgs 56-80)
        exchanges.extend([
            {"role": "user", "content": f"Feature request {i}: Implement retry logic with exponential backoff."} if i % 2 == 0 else {"role": "assistant", "content": f"Noted: feature {i} about retry logic."}
            for i in range(56, 81)
        ])
        # Phase 5: Final decisions (msgs 81-100)
        exchanges.extend([
            {"role": "user", "content": "The project deadline is 6 months."},
            {"role": "assistant", "content": "Deadline: 6 months."},
            {"role": "user", "content": "Use MIT license, same as our other projects."},
            {"role": "assistant", "content": "MIT licensed."},
            {"role": "user", "content": "We need comprehensive logging with distributed tracing."},
            {"role": "assistant", "content": "Distributed tracing with comprehensive logging noted."},
            {"role": "user", "content": "The system should support multi-tenancy."},
            {"role": "assistant", "content": "Multi-tenancy support planned."},
            {"role": "user", "content": "Rate limiting per tenant is a must."},
            {"role": "assistant", "content": "Per-tenant rate limiting noted."},
            {"role": "user", "content": "Data isolation between tenants using schema-per-tenant approach."},
            {"role": "assistant", "content": "Schema-per-tenant data isolation planned."},
            {"role": "user", "content": "That's everything. Ready for summary."},
        ])
        ground_truth = {
            "facts": [
                {"id": "f1", "text": "Project name is Nebula", "source": "msg_1"},
                {"id": "f2", "text": "Nebula is written in Rust", "source": "msg_1"},
                {"id": "f3", "text": "Jordan is the architect", "source": "msg_4"},
                {"id": "f4", "text": "Taylor is the lead developer", "source": "msg_4"},
                {"id": "f5", "text": "Zero-downtime deployments required", "source": "msg_6"},
                {"id": "f6", "text": "gRPC for inter-service communication (confirmed)", "source": "msg_43"},
                {"id": "f7", "text": "PostgreSQL with ACID compliance", "source": "msg_8"},
                {"id": "f8", "text": "Redis for caching and message queuing", "source": "msg_10"},
                {"id": "f9", "text": "Kubernetes orchestration", "source": "msg_12"},
                {"id": "f10", "text": "Apache Kafka for event streaming", "source": "msg_14"},
                {"id": "f11", "text": "6-month deadline", "source": "msg_81"},
                {"id": "f12", "text": "MIT license", "source": "msg_83"},
                {"id": "f13", "text": "Distributed tracing with comprehensive logging", "source": "msg_85"},
                {"id": "f14", "text": "Multi-tenancy support", "source": "msg_87"},
                {"id": "f15", "text": "Per-tenant rate limiting", "source": "msg_89"},
                {"id": "f16", "text": "Schema-per-tenant data isolation", "source": "msg_91"},
            ],
            "relationships": [
                {"type": "architect", "subject": "Jordan", "object": "Nebula", "source": "msg_4"},
                {"type": "lead-dev", "subject": "Taylor", "object": "Nebula", "source": "msg_4"},
                {"type": "uses", "subject": "Nebula", "object": "PostgreSQL", "source": "msg_8"},
                {"type": "uses", "subject": "Nebula", "object": "Redis", "source": "msg_10"},
                {"type": "uses", "subject": "Nebula", "object": "Kubernetes", "source": "msg_12"},
                {"type": "uses", "subject": "Nebula", "object": "Kafka", "source": "msg_14"},
            ],
            "contradictions": [
                {
                    "id": "contra_1",
                    "claim_a": {"text": "gRPC", "source": "msg_8", "timestamp": "early"},
                    "claim_b": {"text": "REST", "source": "msg_41", "timestamp": "later"},
                    "resolved": True,
                    "resolution": "gRPC confirmed, REST rejected",
                },
            ],
            "temporal": [
                {"id": "t1", "event": "Project inception", "message": "msg_1", "state": "initial"},
                {"id": "t2", "event": "API protocol: gRPC", "message": "msg_8", "state": "current"},
                {"id": "t3", "event": "API reconsideration: REST", "message": "msg_41", "state": "superseded"},
                {"id": "t4", "event": "Deadline set", "message": "msg_81", "state": "current"},
            ],
        }
        return {"exchanges": exchanges, "ground_truth": ground_truth, "size": "very_long", "exchange_count": 100}

    def generate_multi_project(self) -> Dict[str, Any]:
        """Multi-project conversation with cross-project references."""
        exchanges = [
            {"role": "system", "content": "You are a helpful AI assistant for project management."},
            {"role": "user", "content": "I'm managing two projects: Orion (Python, offline) and Nebula (Rust, distributed)."},
            {"role": "assistant", "content": "Two projects: Orion and Nebula. Noted."},
            {"role": "user", "content": "Orion uses SQLite. Nebula uses PostgreSQL."},
            {"role": "assistant", "content": "Orion → SQLite, Nebula → PostgreSQL."},
            {"role": "user", "content": "Both projects share a common auth library called AuthKit."},
            {"role": "assistant", "content": "AuthKit is shared between Orion and Nebula."},
            {"role": "user", "content": "AuthKit is written in Go and uses gRPC."},
            {"role": "assistant", "content": "AuthKit: Go + gRPC, shared library."},
            {"role": "user", "content": "The CI/CD pipeline for both projects is on GitHub Actions."},
            {"role": "assistant", "content": "Shared CI/CD: GitHub Actions for both."},
            {"role": "user", "content": "Orion's deadline is 4 months. Nebula's is 6 months."},
            {"role": "assistant", "content": "Orion: 4 months, Nebula: 6 months."},
            {"role": "user", "content": "Alex works on Orion. Jordan architects Nebula."},
            {"role": "assistant", "content": "Alex → Orion, Jordan → Nebula architect."},
            {"role": "user", "content": "Both projects use MIT licensing."},
            {"role": "assistant", "content": "Both MIT licensed."},
            {"role": "user", "content": "The monitoring stack is Prometheus + Grafana for both."},
            {"role": "assistant", "content": "Shared monitoring: Prometheus + Grafana."},
        ]
        ground_truth = {
            "facts": [
                {"id": "f1", "text": "Project Orion exists (Python, offline)", "source": "msg_1"},
                {"id": "f2", "text": "Project Nebula exists (Rust, distributed)", "source": "msg_1"},
                {"id": "f3", "text": "Orion uses SQLite", "source": "msg_3"},
                {"id": "f4", "text": "Nebula uses PostgreSQL", "source": "msg_3"},
                {"id": "f5", "text": "AuthKit is a shared Go/gRPC library", "source": "msg_5"},
                {"id": "f6", "text": "Both use GitHub Actions CI/CD", "source": "msg_7"},
                {"id": "f7", "text": "Orion deadline: 4 months", "source": "msg_9"},
                {"id": "f8", "text": "Nebula deadline: 6 months", "source": "msg_9"},
                {"id": "f9", "text": "Alex works on Orion", "source": "msg_11"},
                {"id": "f10", "text": "Jordan architects Nebula", "source": "msg_11"},
                {"id": "f11", "text": "Both MIT licensed", "source": "msg_13"},
                {"id": "f12", "text": "Shared monitoring: Prometheus + Grafana", "source": "msg_15"},
            ],
            "relationships": [
                {"type": "uses", "subject": "Orion", "object": "SQLite", "source": "msg_3"},
                {"type": "uses", "subject": "Nebula", "object": "PostgreSQL", "source": "msg_3"},
                {"type": "shares", "subject": "Orion", "object": "AuthKit", "source": "msg_5"},
                {"type": "shares", "subject": "Nebula", "object": "AuthKit", "source": "msg_5"},
                {"type": "works-on", "subject": "Alex", "object": "Orion", "source": "msg_11"},
                {"type": "architect", "subject": "Jordan", "object": "Nebula", "source": "msg_11"},
            ],
        }
        return {"exchanges": exchanges, "ground_truth": ground_truth, "size": "multi_project", "exchange_count": 15}

    def generate_all(self) -> Dict[str, Any]:
        """Generate all benchmark conversations."""
        return {
            "short": self.generate_short(),
            "medium": self.generate_medium(),
            "long": self.generate_long(),
            "very_long": self.generate_very_long(),
            "multi_project": self.generate_multi_project(),
        }


def save_datasets(output_dir: Path) -> None:
    """Save all generated datasets to JSON."""
    output_dir.mkdir(parents=True, exist_ok=True)
    generator = ConversationGenerator()
    all_data = generator.generate_all()
    for size, data in all_data.items():
        (output_dir / f"{size}.json").write_text(json.dumps(data, indent=2, ensure_ascii=False))
    print(f"Saved datasets to {output_dir}")


if __name__ == "__main__":
    from pathlib import Path
    save_datasets(Path(__file__).parent.parent / "datasets")
