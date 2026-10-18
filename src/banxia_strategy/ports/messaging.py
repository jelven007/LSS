from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable, Optional, Protocol

from ..domain.events import EventEnvelope


@dataclass(frozen=True)
class PendingEvent:
    sequence: int
    topic: str
    key: str
    event: EventEnvelope
    attempts: int
    created_at: datetime
    last_error: Optional[str] = None


@dataclass(frozen=True)
class ConsumedEvent:
    topic: str
    partition: int
    offset: int
    key: str
    event: EventEnvelope
    raw: Any = None


class EventWAL(Protocol):
    def append(self, topic: str, key: str, event: EventEnvelope) -> bool:
        """Persist an event before attempting pipeline processing."""

    def pending(self, limit: int = 100) -> Iterable[PendingEvent]:
        """Return unpublished events in insertion order."""

    def mark_published(self, event_id: str) -> None:
        """Remove an event only after successful pipeline processing."""

    def mark_failed(self, event_id: str, error: str) -> None:
        """Record a failed publication attempt without dropping the event."""

    def count(self) -> int:
        """Return the number of events awaiting publication."""


class Closable(Protocol):
    def close(self) -> None:
        """Release external resources."""
