from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock

from prometheus_client import CollectorRegistry, generate_latest

from banxia_strategy.adapters.wal import SQLiteEventWAL
from banxia_strategy.application.collector import MarketCollector
from banxia_strategy.application.reliable_publish import ReliableEventPublisher
from banxia_strategy.observability import CollectorMetrics


class CollectorObservabilityTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.now = datetime.fromisoformat("2026-10-15T09:45:00+08:00")
        self.broker = Mock()
        self.publisher = ReliableEventPublisher(
            wal=SQLiteEventWAL(Path(self.directory.name) / "wal.sqlite3"),
            publisher=self.broker,
        )
        self.addCleanup(self.publisher.close)
        self.source = Mock()
        self.source.fetch.side_effect = lambda codes: {
            code: {"quote": {
                "price": 10, "servertime": "09:45:00",
                "last_close": 9, "vol": 100,
            }, "bars": []} for code in codes
        }

    def collector(self, **kwargs):
        return MarketCollector(
            candidates=[{"code": "002635"}, {"code": "600000"}],
            publisher=self.publisher, source=self.source,
            clock=lambda: self.now, **kwargs,
        )

    def test_success_and_age_grow_without_scrape_io(self):
        collector = self.collector(session_checker=lambda _: True)
        self.assertEqual(collector.collect_once(), 2)
        registry = CollectorRegistry()
        registry.register(CollectorMetrics(collector, environment="production"))
        self.source.fetch.reset_mock()
        self.publisher.status = Mock(side_effect=AssertionError("scrape must not query WAL"))
        self.now += timedelta(seconds=25)
        output = generate_latest(registry).decode()
        self.assertIn('banxia_collector_quote_age_p95_seconds{environment="production",shard="0"} 25.0', output)
        self.assertIn('banxia_collector_candidate_count{environment="production",shard="0"} 2.0', output)
        self.source.fetch.assert_not_called()
        self.assertEqual(collector.telemetry()["last_success_timestamp"], self.now.timestamp() - 25)

    def test_empty_batch_never_records_success(self):
        self.source.fetch.side_effect = lambda _: {}
        collector = self.collector()
        self.assertEqual(collector.collect_once(), 0)
        state = collector.telemetry()
        self.assertEqual(state["last_success_timestamp"], 0)
        self.assertEqual(state["quote_coverage_ratio"], 0)
        self.assertEqual(state["errors"]["source"], 1)

    def test_partial_and_invalid_future_source_times_reduce_coverage(self):
        self.source.fetch.side_effect = lambda _: {
            "002635": {"quote": {"price": 10, "servertime": "09:45:00"}},
            "600000": {"quote": {"price": 10, "servertime": "10:45:00"}},
        }
        collector = self.collector()
        collector.collect_once()
        self.assertEqual(collector.telemetry()["quote_coverage_ratio"], 0.5)

    def test_missing_source_time_is_not_success(self):
        self.source.fetch.side_effect = lambda codes: {
            code: {"quote": {"price": 10}} for code in codes
        }
        collector = self.collector()
        self.assertEqual(collector.collect_once(), 0)
        self.assertEqual(collector.telemetry()["last_success_timestamp"], 0)

    def test_empty_plan_and_standby_are_distinct(self):
        lease = Mock()
        collector = self.collector(candidate_loader=lambda: [], lease=lease)
        collector.collect_once()
        self.assertEqual(collector.telemetry()["candidate_count"], 0)
        self.assertFalse(collector.telemetry()["leader"])
        lease.try_acquire.assert_not_called()
        self.source.fetch.assert_not_called()
        lease.try_acquire.return_value = False
        collector.candidate_loader = lambda: [{"code": "600000"}]
        collector.collect_once()
        self.assertEqual(collector.telemetry()["candidate_count"], 1)
        self.assertFalse(collector.telemetry()["leader"])
        self.source.fetch.assert_not_called()

    def test_lunch_holiday_and_next_date_clear_market_gate(self):
        checker = Mock(return_value=True)
        collector = self.collector(session_checker=checker)
        collector.collect_once()
        self.assertTrue(collector.telemetry()["market_open"])
        self.now = self.now.replace(hour=12)
        self.assertFalse(collector.telemetry()["market_open"])
        self.now += timedelta(days=1)
        self.now = self.now.replace(hour=9)
        self.assertFalse(collector.telemetry()["calendar_available"])
        checker.return_value = False
        collector.collect_once()
        self.assertTrue(collector.telemetry()["calendar_available"])
        self.assertFalse(collector.telemetry()["market_open"])

    def test_calendar_unavailable_is_distinct_from_holiday(self):
        collector = self.collector(session_checker=Mock(side_effect=RuntimeError("offline")))
        collector.collect_once()
        state = collector.telemetry()
        self.assertTrue(state["clock_open"])
        self.assertFalse(state["calendar_available"])
        self.assertFalse(state["market_open"])
        self.assertEqual(state["errors"]["calendar"], 1)
        self.source.fetch.assert_not_called()

    def test_loader_and_source_errors_are_counted_once(self):
        loader = Mock(side_effect=RuntimeError("plans offline"))
        collector = self.collector(candidate_loader=loader)
        with self.assertRaisesRegex(RuntimeError, "plans offline"):
            collector.collect_once()
        self.assertEqual(collector.telemetry()["errors"]["candidates"], 1)
        loader.side_effect = None
        loader.return_value = [{"code": "600000"}]
        self.source.fetch.side_effect = RuntimeError("quotes offline")
        with self.assertRaisesRegex(RuntimeError, "quotes offline"):
            collector.collect_once()
        self.assertEqual(collector.telemetry()["errors"]["source"], 1)

    def test_kafka_failures_leave_cached_wal_pending(self):
        self.broker.publish.side_effect = RuntimeError("broker offline")
        collector = self.collector()
        with self.assertRaisesRegex(RuntimeError, "broker offline"):
            collector.collect_once()
        state = collector.telemetry()
        self.assertEqual(state["messaging"]["wal_pending"], 2)
        self.assertEqual(state["messaging"]["failed"], 2)
        self.assertEqual(state["last_success_timestamp"], 0)

    def test_run_loop_logs_errors_and_stops_cleanly(self):
        collector = self.collector()
        def fail(_):
            collector.stop()
            raise RuntimeError("source offline")
        self.source.fetch.side_effect = fail
        with self.assertLogs("banxia_strategy.application.collector", level="ERROR"):
            collector.run_forever()
        self.assertEqual(collector.telemetry()["errors"]["source"], 1)
        self.assertFalse(collector.telemetry()["running"])
        self.assertEqual(collector.telemetry()["heartbeat_timestamp"], self.now.timestamp())
