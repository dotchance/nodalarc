# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Windowed Node Agent reachability health.

Unreachability is an AGENT property, not a ground-station property, and its
operational meaning depends on the pattern, not a lifetime count:

- a burst of failures while an agent restarts during a rollout is expected
  and self-heals in seconds;
- one missed call every few hours across an eight-day session is noise and
  must never accumulate toward a cutoff;
- an agent that fails most of its recent calls is DOWN and the operator
  must be told - loudly, once, with recovery announced when it returns.

So health is judged over a sliding window of the last N proof/dispatch
attempts per agent: degraded when failures within the window cross the
threshold, recovered on the first successful answer afterwards. Old
outcomes age out of the window naturally; there is no monotonic counter
and no permanent state.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class AgentHealthTransition(StrEnum):
    DEGRADED = "degraded"
    RECOVERED = "recovered"


@dataclass(frozen=True)
class AgentHealthPolicy:
    """How much recent failure means an agent is down.

    With the defaults, an agent must fail 15 of its last 20 calls to be
    declared degraded - a restart burst (a handful of failures, then
    successes) never crosses it, and isolated misses age out long before
    they can combine.
    """

    window_size: int = 20
    failure_threshold: int = 15


@dataclass
class _AgentWindow:
    outcomes: deque[bool] = field(default_factory=deque)
    degraded: bool = False
    last_failure_reason: str | None = None
    last_change: datetime | None = None


class AgentHealthTracker:
    """Per-agent sliding-window reachability state for one Scheduler."""

    def __init__(self, policy: AgentHealthPolicy | None = None) -> None:
        self._policy = policy or AgentHealthPolicy()
        self._agents: dict[str, _AgentWindow] = {}

    def record(
        self,
        agent_addr: str,
        *,
        ok: bool,
        reason: str | None = None,
        now: datetime | None = None,
    ) -> AgentHealthTransition | None:
        """Record one call outcome; return a transition when state changes."""
        window = self._agents.setdefault(agent_addr, _AgentWindow())
        window.outcomes.append(ok)
        while len(window.outcomes) > self._policy.window_size:
            window.outcomes.popleft()
        if not ok:
            window.last_failure_reason = reason

        failures = sum(1 for outcome in window.outcomes if not outcome)
        if not window.degraded and failures >= self._policy.failure_threshold:
            window.degraded = True
            window.last_change = now
            return AgentHealthTransition.DEGRADED
        if window.degraded and ok:
            window.degraded = False
            window.last_change = now
            return AgentHealthTransition.RECOVERED
        return None

    def is_degraded(self, agent_addr: str) -> bool:
        window = self._agents.get(agent_addr)
        return bool(window and window.degraded)

    def any_degraded(self) -> bool:
        return any(window.degraded for window in self._agents.values())

    def failure_summary(self, agent_addr: str) -> str:
        window = self._agents.get(agent_addr)
        if window is None:
            return "no recorded calls"
        failures = sum(1 for outcome in window.outcomes if not outcome)
        return f"{failures} of last {len(window.outcomes)} calls failed" + (
            f" (last reason: {window.last_failure_reason})" if window.last_failure_reason else ""
        )
