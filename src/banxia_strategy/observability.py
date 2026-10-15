from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any, Dict


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for name in ("service", "event_id", "trace_id", "symbol", "request_id"):
            value = getattr(record, name, None)
            if value is not None:
                payload[name] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        )


def configure_logging(service_name: str, level: str = "INFO") -> logging.Logger:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
    logger = logging.getLogger(service_name)
    return logging.LoggerAdapter(logger, {"service": service_name})


def start_metrics_server(port: int) -> None:
    try:
        from prometheus_client import start_http_server
    except ImportError as exc:
        raise RuntimeError(
            "metrics require `pip install -e '.[production]'`"
        ) from exc
    start_http_server(port)


class CollectorMetrics:
    """Expose a collector's cached snapshot with bounded environment/shard labels."""

    def __init__(self, collector: Any, *, environment: str, shard: str = "0"):
        self.collector = collector
        self.label_values = (environment, shard)

    def collect(self):
        from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily

        state = self.collector.telemetry()
        gauges = {
            "running": ("Collector polling loop is running.", state["running"]),
            "leader": ("Collector currently owns the shard lease.", state["leader"]),
            "heartbeat_timestamp_seconds": ("Last polling loop heartbeat.", state["heartbeat_timestamp"]),
            "heartbeat_timeout_seconds": ("Allowed heartbeat gap for the current polling interval.", state["heartbeat_timeout_seconds"]),
            "last_success_timestamp_seconds": ("Last valid quote batch published successfully.", state["last_success_timestamp"]),
            "candidate_count": ("Unique symbols in the current monitor plans.", state["candidate_count"]),
            "clock_open": ("Shanghai weekday continuous trading clock window.", state["clock_open"]),
            "calendar_available": ("Trading calendar verified for the current Shanghai date.", state["calendar_available"]),
            "market_open": ("Verified trading day in a continuous trading window.", state["market_open"]),
            "quote_coverage_ratio": ("Fraction of candidates with a valid price and source time.", state["quote_coverage_ratio"]),
            "quote_age_p95_seconds": ("P95 age of the last valid quote batch, increasing between fetches.", state["quote_age_p95_seconds"]),
            "wal_pending": ("Cached number of events awaiting Kafka acknowledgement.", state["messaging"].get("wal_pending", 0)),
        }
        for suffix, (help_text, value) in gauges.items():
            metric = GaugeMetricFamily(
                f"banxia_collector_{suffix}", help_text, labels=["environment", "shard"]
            )
            metric.add_metric(self.label_values, float(value))
            yield metric
        for suffix, value in (
            ("collections", state["collections"]),
            ("published_events", state["messaging"].get("published", state["published"])),
            ("publish_failures", state["messaging"].get("failed", 0)),
        ):
            metric = CounterMetricFamily(
                f"banxia_collector_{suffix}", f"Collector {suffix}.",
                labels=["environment", "shard"],
            )
            metric.add_metric(self.label_values, float(value))
            yield metric
        errors = CounterMetricFamily(
            "banxia_collector_errors", "Collector failures by operation.",
            labels=["environment", "shard", "stage"],
        )
        for stage in ("calendar", "candidates", "lease", "source", "normalize", "publish", "status", "config"):
            errors.add_metric((*self.label_values, stage), state["errors"].get(stage, 0))
        yield errors


def register_collector_metrics(collector: Any, *, environment: str) -> CollectorMetrics:
    from prometheus_client import REGISTRY

    metrics = CollectorMetrics(collector, environment=environment)
    REGISTRY.register(metrics)
    return metrics
