from __future__ import annotations

import unittest
import tempfile
from contextlib import ExitStack
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch

from banxia_strategy.adapters.wal import SQLiteEventWAL
from banxia_strategy.application.lean_market import LeanMarketPipeline
from banxia_strategy.application.reliable_publish import ReliableEventPublisher
from banxia_strategy.config import RuntimeSettings
from banxia_strategy.domain.events import EventEnvelope
from banxia_strategy.service import run_market_worker

STAMP = datetime.fromisoformat("2026-09-24T09:45:00+08:00")


def event(event_type, payload):
    return EventEnvelope.create(
        event_type=event_type,
        producer="test",
        occurred_at=STAMP,
        published_at=STAMP,
        identity={
            "symbol": payload.get("symbol", "system"),
            "time": payload.get("source_time") or STAMP,
        },
        payload=payload,
        trace_id="trace",
    )


class Repository:
    def __init__(self):
        self.current = None
        self.calls = []
        self.plan_reads = 0

    def list_monitor_plans(self):
        self.plan_reads += 1
        return [
            {
                "plan_id": "plan",
                "strategy_version_id": "version-id",
                "strategy_version": "v1",
                "trade_date": "2026-09-24",
                "reference_date": "2026-09-23",
                "candidates": [
                    {
                        "symbol": "002635",
                        "name": "安洁科技",
                        "latest_price": 10.0,
                        "entry_trigger": "开盘涨幅位于1%～5%",
                        "invalidation": "低于0%或高于6%",
                        "eligible": True,
                        "eligibility_reason": "test",
                    }
                ],
            }
        ]

    def get(self, _plan_id, _symbol):
        return self.current

    def apply(self, **kwargs):
        self.calls.append(kwargs)
        self.current = kwargs["decision"]
        return True

    def ready(self):
        return True

    def close(self):
        pass


class MarketStore:
    def __init__(self):
        self.quotes = []
        self.bars = []
        self.features = []

    def append_quote_snapshots(self, events):
        self.quotes.extend(events)

    def upsert_minute_bars(self, events):
        self.bars.extend(events)

    def append_features(self, events):
        self.features.extend(events)

    def ready(self):
        return True

    def close(self):
        pass


class Cache:
    def __init__(self):
        self.quotes = []
        self.bars = []
        self.features = []
        self.decisions = []
        self.stream = []

    def set_latest_quote(self, value, ttl):
        self.quotes.append((value, ttl))

    def append_minute_bar(self, value, ttl):
        self.bars.append((value, ttl))

    def set_latest_feature(self, value, ttl):
        self.features.append((value, ttl))

    def set_latest_decision(self, value, ttl):
        self.decisions.append((value, ttl))

    def append_monitor_event(self, trade_date, value, **kwargs):
        self.stream.append((trade_date, value, kwargs))

    def ready(self):
        return True

    def close(self):
        pass


class LeanMarketPipelineTest(unittest.TestCase):
    def test_worker_starts_without_local_reports_and_loads_database_plan(self):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            settings = RuntimeSettings(
                report_dirs=(Path(directory) / "missing-reports",),
                wal_path=Path(directory) / "wal.sqlite3",
            )
            repository = Mock()
            repository.list_monitor_plans.return_value = Repository().list_monitor_plans()
            stack.enter_context(patch("banxia_strategy.service._postgres", return_value=repository))
            stack.enter_context(patch("banxia_strategy.service._clickhouse", return_value=MarketStore()))
            stack.enter_context(patch("banxia_strategy.service._redis", return_value=Cache()))
            stack.enter_context(patch("banxia_strategy.service.PostgresAdvisoryLease"))
            stack.enter_context(patch("banxia_strategy.service.OutboxRelay"))
            stack.enter_context(patch("banxia_strategy.service.signal.signal"))
            stack.enter_context(patch("banxia_strategy.service.register_collector_metrics"))
            collector = stack.enter_context(patch("banxia_strategy.service.MarketCollector"))
            run_market_worker(settings, Mock())
            options = collector.call_args.kwargs
            self.assertEqual(options["candidates"], ())
            candidates = options["candidate_loader"]()
            self.assertEqual(candidates[0]["code"], "002635")
            self.assertEqual(candidates[0]["plan_date"], "2026-09-24")
            repository.persist_report.assert_not_called()
            collector.return_value.run_forever.assert_called_once()
            # The mocked collector does not own/close its real publisher.
            options["publisher"].close()

    def test_failed_minute_projection_stays_in_wal_until_recovery(self):
        market, cache = MarketStore(), Cache()
        pipeline = LeanMarketPipeline(repository=Repository(), market_store=market, cache=cache)
        bar = event("market.bar.1m.v1", {
            "trade_date": "2026-09-24", "symbol": "002635",
            "bar_time": STAMP.isoformat(), "volume": 100, "close": 10.2,
        })
        with tempfile.TemporaryDirectory() as directory:
            wal = SQLiteEventWAL(Path(directory) / "wal.sqlite3")
            publisher = ReliableEventPublisher(wal=wal, publisher=pipeline)
            with patch.object(cache, "append_minute_bar", side_effect=RuntimeError("redis down")):
                with self.assertRaisesRegex(RuntimeError, "redis down"):
                    publisher.publish(bar.event_type, "002635", bar)
            self.assertEqual(wal.count(), 1)
            self.assertFalse(market.features)
            self.assertEqual(publisher.replay(), 1)
            self.assertEqual(wal.count(), 0)
            self.assertEqual(cache.bars[0][0], bar)
            self.assertEqual(cache.stream[0][0], "2026-09-24")
            publisher.close()

    def test_quote_runs_the_complete_lean_pipeline(self):
        repository = Repository()
        market = MarketStore()
        cache = Cache()
        pipeline = LeanMarketPipeline(
            repository=repository,
            market_store=market,
            cache=cache,
        )
        quote = event(
            "market.quote.snapshot.v1",
            {
                "trade_date": "2026-09-24",
                "symbol": "002635",
                "industry": "电子",
                "source_time": STAMP,
                "collected_at": STAMP,
                "price": 10.6,
                "open": 10.2,
                "high": 10.7,
                "low": 10.1,
                "previous_close": 10.0,
                "cumulative_volume": 1000,
                "cumulative_amount_cny": 300000000,
                "bid1": 10.59,
                "ask1": 10.6,
                "bid1_volume": 100,
                "ask1_volume": 50,
            },
        )

        pipeline.publish(quote.event_type, "002635", quote)

        self.assertEqual(market.quotes, [quote])
        self.assertEqual(len(market.features), 1)
        self.assertEqual(cache.quotes[0][0], quote)
        self.assertEqual(cache.features[0][0], market.features[0])
        self.assertEqual(len(repository.calls), 1)
        self.assertEqual(repository.calls[0]["topic"], quote.event_type)
        self.assertEqual(repository.calls[0]["partition"], 0)
        self.assertEqual(repository.plan_reads, 1)

    def test_outbox_decision_updates_only_the_rebuildable_projection(self):
        repository = Repository()
        market = MarketStore()
        cache = Cache()
        pipeline = LeanMarketPipeline(
            repository=repository,
            market_store=market,
            cache=cache,
        )
        decision = event(
            "strategy.decision.v1",
            {
                "trade_date": "2026-09-24",
                "symbol": "002635",
                "state": "watch",
            },
        )

        pipeline.publish(decision.event_type, "002635", decision)

        self.assertEqual(cache.decisions[0][0], decision)
        self.assertFalse(market.quotes)
        self.assertFalse(market.features)
        self.assertFalse(repository.calls)


if __name__ == "__main__":
    unittest.main()
