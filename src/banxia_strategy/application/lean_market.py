from __future__ import annotations

import hashlib
import threading
import time
from datetime import datetime
from typing import Any

from ..contracts.topics import (
    MARKET_BAR_1M,
    MARKET_FEATURE_REALTIME,
    MARKET_QUOTE_SNAPSHOT,
    REPORT_GENERATED,
    STRATEGY_AUDIT,
    STRATEGY_DECISION,
    STRATEGY_PLAN_CREATED,
)
from ..domain.events import EventEnvelope
from ..ports.messaging import ConsumedEvent
from .features import RealtimeFeatureProcessor
from .strategy_engine import StrategyEventProcessor


class LeanMarketPipeline:
    """Synchronous market pipeline for the production-scale watch universe.

    The collector WAL is acknowledged only after this pipeline returns, so raw
    market facts are persisted and projected before an event leaves the WAL.
    Strategy decisions remain transactional in PostgreSQL and reach Redis
    through the existing outbox relay.
    """

    def __init__(
        self,
        *,
        repository: Any,
        market_store: Any,
        cache: Any,
        feature_processor: RealtimeFeatureProcessor | None = None,
        plan_reload_seconds: float = 5.0,
        quote_ttl_seconds: int = 172800,
        decision_ttl_seconds: int = 604800,
        stream_ttl_seconds: int = 172800,
    ) -> None:
        if plan_reload_seconds <= 0:
            raise ValueError("plan_reload_seconds must be positive")
        self.repository = repository
        self.market_store = market_store
        self.cache = cache
        self.feature_processor = feature_processor or RealtimeFeatureProcessor()
        self.plan_reload_seconds = plan_reload_seconds
        self.quote_ttl_seconds = quote_ttl_seconds
        self.decision_ttl_seconds = decision_ttl_seconds
        self.stream_ttl_seconds = stream_ttl_seconds
        self.processors: dict[str, StrategyEventProcessor] = {}
        self.last_reload: float | None = None
        self._lock = threading.RLock()
        self._closed = False

    @staticmethod
    def _consumed(
        topic: str,
        key: str,
        event: EventEnvelope,
    ) -> ConsumedEvent:
        offset = int(
            hashlib.sha256(event.event_id.encode("utf-8")).hexdigest()[:15],
            16,
        )
        return ConsumedEvent(
            topic=topic,
            partition=0,
            offset=offset,
            key=key,
            event=event,
        )

    def _reload_plans(self, *, force: bool = False) -> None:
        if (
            not force
            and self.last_reload is not None
            and time.monotonic() - self.last_reload < self.plan_reload_seconds
        ):
            return
        active: dict[str, StrategyEventProcessor] = {}
        for plan in self.repository.list_monitor_plans():
            plan_id = str(plan["plan_id"])
            candidates = [
                {
                    **item,
                    "code": item["symbol"],
                    "plan_date": plan["trade_date"],
                    "reference_date": plan["reference_date"],
                }
                for item in plan["candidates"]
            ]
            processor = self.processors.get(plan_id)
            if processor is None:
                processor = StrategyEventProcessor(
                    repository=self.repository,
                    plan_id=plan_id,
                    strategy_version_id=str(plan["strategy_version_id"]),
                    strategy_version=str(plan["strategy_version"]),
                    candidates=candidates,
                )
            else:
                processor.replace_plan(
                    plan_id=plan_id,
                    strategy_version_id=str(plan["strategy_version_id"]),
                    strategy_version=str(plan["strategy_version"]),
                    candidates=candidates,
                )
            active[plan_id] = processor
        self.processors = active
        self.last_reload = time.monotonic()

    def _project(self, event: EventEnvelope) -> None:
        trade_date = str(
            event.payload.get("trade_date")
            or datetime.fromisoformat(event.occurred_at).date().isoformat()
        )
        if event.event_type == MARKET_QUOTE_SNAPSHOT:
            self.cache.set_latest_quote(event, self.quote_ttl_seconds)
        elif event.event_type == MARKET_BAR_1M:
            self.cache.append_minute_bar(event, self.quote_ttl_seconds)
        elif event.event_type == MARKET_FEATURE_REALTIME:
            self.cache.set_latest_feature(event, self.quote_ttl_seconds)
        elif event.event_type == STRATEGY_DECISION:
            self.cache.set_latest_decision(event, self.decision_ttl_seconds)
        else:
            return
        self.cache.append_monitor_event(
            trade_date,
            event,
            ttl_seconds=self.stream_ttl_seconds,
        )

    def _persist_market(self, event: EventEnvelope) -> None:
        if event.event_type == MARKET_QUOTE_SNAPSHOT:
            self.market_store.append_quote_snapshots([event])
        elif event.event_type == MARKET_BAR_1M:
            self.market_store.upsert_minute_bars([event])
        elif event.event_type == MARKET_FEATURE_REALTIME:
            self.market_store.append_features([event])

    def _process_strategy(
        self,
        topic: str,
        key: str,
        event: EventEnvelope,
    ) -> None:
        consumed = self._consumed(topic, key, event)
        for processor in self.processors.values():
            processor.process(consumed)

    def publish(self, topic: str, key: str, event: EventEnvelope) -> None:
        with self._lock:
            if self._closed:
                raise RuntimeError("lean market pipeline is closed")
            if event.event_type in {MARKET_QUOTE_SNAPSHOT, MARKET_BAR_1M}:
                self._reload_plans()
                self._persist_market(event)
                self._project(event)
                feature = self.feature_processor.process(event)
                if feature is not None:
                    self._persist_market(feature)
                    self._project(feature)
                    self._process_strategy(
                        MARKET_FEATURE_REALTIME,
                        key,
                        feature,
                    )
                self._process_strategy(topic, key, event)
                return
            if event.event_type == STRATEGY_PLAN_CREATED:
                self._reload_plans(force=True)
                return
            if event.event_type == STRATEGY_DECISION:
                self._project(event)
                return
            if event.event_type in {STRATEGY_AUDIT, REPORT_GENERATED}:
                return
            raise ValueError(f"unsupported lean pipeline event: {event.event_type}")

    def ready(self) -> bool:
        checks = (
            getattr(self.repository, "ready", None),
            getattr(self.market_store, "ready", None),
            getattr(self.cache, "ready", None),
        )
        return all(check is None or bool(check()) for check in checks)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        for target in (self.cache, self.market_store, self.repository):
            close = getattr(target, "close", None)
            if close is not None:
                close()
