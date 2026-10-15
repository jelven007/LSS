from __future__ import annotations

import logging
import math
import threading
import time
from datetime import datetime
from typing import Any, Dict, Mapping, Optional, Sequence

from ..contracts.topics import topic_for_event
from ..intraday import (
    MarketLiveSource,
    normalize_quote,
    now_shanghai,
    plan_for,
)
from .persistence import snapshot_market_events
from .reliable_publish import ReliableEventPublisher

LOGGER = logging.getLogger(__name__)


class MarketCollector:
    """Collect market facts and publish them without making strategy decisions."""

    def __init__(
        self,
        *,
        candidates: Sequence[Mapping[str, Any]],
        publisher: ReliableEventPublisher,
        source: Optional[Any] = None,
        lease: Optional[Any] = None,
        clock: Optional[Any] = None,
        quote_interval_seconds: float = 1.0,
        idle_interval_seconds: float = 60.0,
        config_store=None,
        candidate_loader=None,
        session_checker=None,
    ):
        if not candidates and candidate_loader is None:
            raise ValueError("collector requires at least one watchlist candidate")
        if quote_interval_seconds <= 0 or idle_interval_seconds <= 0:
            raise ValueError("collector intervals must be positive")
        self.candidates = tuple(dict(item) for item in candidates)
        self.codes = tuple(dict.fromkeys(str(item["code"]) for item in candidates))
        self.publisher = publisher
        self.source = source or MarketLiveSource()
        self.lease = lease
        self.clock = clock or now_shanghai
        self.quote_interval_seconds = quote_interval_seconds
        self.idle_interval_seconds = idle_interval_seconds
        self.config_store = config_store
        self.candidate_loader = candidate_loader
        self.session_checker = session_checker
        self._verified_session_date = None
        self._calendar_date = None
        self._calendar_available = False
        self._trading_day = False
        self.stop_event = threading.Event()
        self._bar_event_ids = set()
        self._lock = threading.Lock()
        self._status: Dict[str, Any] = {
            "running": False,
            "leader": lease is None,
            "collections": 0,
            "published": 0,
            "failures": 0,
            "last_success_at": None,
            "last_error": None,
            "paused": False,
            "pause_reason": None,
            "session_date": None,
        }
        self._heartbeat = 0.0
        self._last_success_timestamp = 0.0
        self._candidate_count = len(self.codes)
        self._quote_coverage = 0.0
        self._source_times: tuple[float, ...] = ()
        self._errors: Dict[str, int] = {}
        self._messaging: Dict[str, Any] = {}
        self._stage = "source"

    @staticmethod
    def _continuous(now: datetime) -> bool:
        minute = now.hour * 60 + now.minute
        return now.weekday() < 5 and (
            9 * 60 + 30 <= minute < 11 * 60 + 30
            or 13 * 60 <= minute < 15 * 60
        )

    def _record_error(self, stage: str, error: str) -> None:
        with self._lock:
            self._errors[stage] = self._errors.get(stage, 0) + 1
            self._status["failures"] += 1
            self._status["last_error"] = error

    def heartbeat(self) -> None:
        with self._lock:
            self._heartbeat = self.clock().timestamp()

    def telemetry(self) -> Dict[str, Any]:
        """Return cached state only; a scrape must never call external services."""
        now = self.clock()
        with self._lock:
            calendar_current = self._calendar_date == now.date()
            market_open = (
                self._continuous(now) and calendar_current
                and self._calendar_available and self._trading_day
            )
            ages = sorted(max(0.0, now.timestamp() - stamp) for stamp in self._source_times)
            p95 = ages[max(0, math.ceil(len(ages) * 0.95) - 1)] if ages else 0.0
            interval = (
                self.quote_interval_seconds if self._active(now)
                else self.idle_interval_seconds
            )
            return {
                **self._status,
                "heartbeat_timestamp": self._heartbeat,
                "heartbeat_timeout_seconds": max(90.0, interval * 3),
                "last_success_timestamp": self._last_success_timestamp,
                "candidate_count": self._candidate_count,
                "clock_open": self._continuous(now),
                "calendar_available": calendar_current and self._calendar_available,
                "market_open": market_open,
                "quote_coverage_ratio": self._quote_coverage,
                "quote_age_p95_seconds": p95,
                "errors": dict(self._errors),
                "messaging": dict(self._messaging),
            }

    @staticmethod
    def _active(now: datetime) -> bool:
        if now.weekday() >= 5:
            return False
        minute = now.hour * 60 + now.minute
        return 9 * 60 + 15 <= minute < 11 * 60 + 30 or 13 * 60 <= minute < 15 * 60

    def reload_intervals(self):
        if self.config_store is not None:
            cfg = self.config_store.read()
            self.quote_interval_seconds = cfg.quote_interval_seconds
            self.idle_interval_seconds = cfg.idle_interval_seconds
            self.source.bar_interval = cfg.bar_interval_seconds

    def _collection_allowed(self, now: datetime) -> bool:
        session_date = now.date()
        reason = None
        error = None
        if now.weekday() >= 5:
            reason = "non_trading_day"
        elif self._verified_session_date == session_date:
            pass
        elif self.session_checker is not None:
            try:
                if self.session_checker(session_date):
                    self._verified_session_date = session_date
                else:
                    reason = "non_trading_day"
            except Exception as exc:
                reason = "trading_calendar_unavailable"
                error = str(exc)
        with self._lock:
            self._calendar_date = session_date
            self._calendar_available = error is None
            self._trading_day = reason is None
            self._status["paused"] = reason is not None
            self._status["pause_reason"] = reason
            self._status["session_date"] = session_date.isoformat()
            if error is not None:
                self._status["last_error"] = error
        if error is not None:
            self._record_error("calendar", error)
        return reason is None

    def collect_once(self) -> int:
        self.heartbeat()
        try:
            return self._collect_once()
        except Exception as exc:
            self._record_error(self._stage, str(exc))
            raise
        finally:
            self.heartbeat()
            try:
                if not self._status["paused"]:
                    messaging = self.publisher.status()
                    with self._lock:
                        self._messaging = dict(messaging)
            except Exception as exc:
                self._record_error("status", str(exc))
                LOGGER.exception("collector publisher status unavailable")

    def _collect_once(self) -> int:
        if not self._collection_allowed(self.clock()):
            with self._lock:
                self._status["leader"] = False
            return 0
        self._stage = "candidates"
        if self.candidate_loader is not None:
            candidates = self.candidate_loader()
            unique = {str(item["code"]): dict(item) for item in candidates}
            self.candidates = tuple(unique.values())
            self.codes = tuple(unique)
        with self._lock:
            self._candidate_count = len(self.codes)
        if not self.codes:
            with self._lock:
                self._status["leader"] = False
                self._quote_coverage = 0.0
                self._source_times = ()
            return 0
        self._stage = "lease"
        if self.lease is not None and not self.lease.try_acquire():
            with self._lock:
                self._status["leader"] = False
            return 0
        with self._lock:
            self._status["leader"] = True
        self._stage = "publish"
        self.publisher.replay()
        self._stage = "source"
        batch = self.source.fetch(self.codes)
        collected_at = self.clock()
        stocks = []
        source_times = []
        self._stage = "normalize"
        for candidate in self.candidates:
            code = str(candidate["code"])
            market = batch.get(code, {})
            if market.get("error") or not market.get("quote"):
                continue
            plan = plan_for(candidate)
            quote = normalize_quote(
                market["quote"],
                market.get("bars", []),
                collected_at,
                plan,
            )
            if quote.get("price") and quote.get("quote_time"):
                stamp = datetime.fromisoformat(quote["quote_time"]).timestamp()
                if stamp <= collected_at.timestamp() + 5:
                    source_times.append(stamp)
            stocks.append(
                {
                    "code": code,
                    "name": candidate.get("name"),
                    "industry": candidate.get("industry"),
                    "quote": quote,
                }
            )
        with self._lock:
            self._source_times = tuple(source_times)
            self._quote_coverage = len(source_times) / len(self.codes)
        if len(source_times) < len(self.codes):
            self._record_error("source", "missing or invalid candidate quotes")
        snapshot = {
            "collected_at": collected_at.isoformat(timespec="milliseconds"),
            "stocks": stocks,
            "source": getattr(getattr(self.source, "provider", None), "source_name", "rustdx"),
        }
        quotes, bars, _ = snapshot_market_events(snapshot)
        events = [*quotes]
        for event in bars:
            if event.event_id not in self._bar_event_ids:
                events.append(event)
                self._bar_event_ids.add(event.event_id)
        errors = []
        published = 0
        self._stage = "publish"
        for event in events:
            try:
                self.publisher.publish(
                    topic_for_event(event.event_type),
                    str(event.payload["symbol"]),
                    event,
                )
                published += 1
            except Exception as exc:
                errors.append(str(exc))
        with self._lock:
            self._status["collections"] += 1
            self._status["published"] += published
            if errors:
                self._status["last_error"] = errors[-1]
            elif source_times and quotes:
                self._status["last_error"] = None
                self._last_success_timestamp = collected_at.timestamp()
                self._status["last_success_at"] = collected_at.isoformat(
                    timespec="seconds"
                )
        if errors:
            raise RuntimeError("; ".join(errors))
        return published

    def run_forever(self) -> None:
        with self._lock:
            self._status["running"] = True
        try:
            while not self.stop_event.is_set():
                started = time.monotonic()
                self.heartbeat()
                try:
                    self._stage = "config"
                    self.reload_intervals()
                    self.collect_once()
                except Exception as exc:
                    if self._stage == "config":
                        self._record_error("config", str(exc))
                    LOGGER.exception("collector iteration failed")
                finally:
                    self.heartbeat()
                interval = (
                    self.quote_interval_seconds
                    if self._active(self.clock())
                    else self.idle_interval_seconds
                )
                self.stop_event.wait(max(0.05, interval - (time.monotonic() - started)))
        finally:
            with self._lock:
                self._status["running"] = False

    def status(self) -> Dict[str, Any]:
        with self._lock:
            result = dict(self._status)
        result["messaging"] = self.publisher.status()
        return result

    def stop(self) -> None:
        self.stop_event.set()

    def close(self) -> None:
        self.stop()
        for target in (self.source, self.publisher, self.lease):
            if target is None:
                continue
            close = getattr(target, "close", None)
            if close is not None:
                close()
