from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
import tempfile
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

DATASETS = {
    "market_quote_snapshot": "trade_date, symbol, source_time, event_id",
    "market_bar_1m": "trade_date, symbol, bar_time",
    "market_feature_realtime": (
        "trade_date, symbol, feature_name, feature_version, window_end, event_id"
    ),
    "market_history_bar": "symbol, period, bar_time",
    "market_board_capital_history": "board_code, trade_date",
}
SHANGHAI = ZoneInfo("Asia/Shanghai")


def _json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":")).encode("utf-8")


class MarketArchive:
    """Archive collected ClickHouse facts; an empty day is never a complete archive."""

    def __init__(self, *, history_client: Any, object_store: Any,
                 database: str = "banxia"):
        if not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]*", database):
            raise ValueError("invalid ClickHouse database identifier")
        self.history = history_client
        self.objects = object_store
        self.database = database

    def _state(self, dataset: str, trade_date: date) -> tuple[int, int]:
        result = self.history.query(
            f"SELECT count(), groupBitXor(cityHash64(toString(tuple(*)))) "
            f"FROM {self.database}.{dataset} FINAL "
            "WHERE trade_date = {trade_date:Date}",
            parameters={"trade_date": trade_date},
        )
        count, fingerprint = result.result_rows[0]
        return int(count), int(fingerprint or 0)

    def _verify(self, key: str, expected_hash: str, expected_size: int) -> None:
        response = self.objects.client.get_object(self.objects.bucket, key)
        digest = hashlib.sha256()
        size = 0
        try:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
                size += len(chunk)
        finally:
            response.close()
            response.release_conn()
        if digest.hexdigest() != expected_hash or size != expected_size:
            raise RuntimeError(f"archive readback mismatch: {key}")

    def run(self, trade_date: date) -> dict[str, Any]:
        states = {name: self._state(name, trade_date) for name in DATASETS}
        if not any(count for count, _ in states.values()):
            raise RuntimeError("no collected market data for requested trade date")
        assets: dict[str, Any] = {}
        for dataset, order in DATASETS.items():
            count, fingerprint = states[dataset]
            if not count:
                assets[dataset] = {"row_count": 0, "state": "empty"}
                continue
            with tempfile.TemporaryFile() as output:
                raw_hash = hashlib.sha256()
                row_count = 0
                with gzip.GzipFile(fileobj=output, mode="wb", filename="", mtime=0) as compressed, \
                        self.history.raw_stream(
                        f"SELECT * FROM {self.database}.{dataset} FINAL "
                        f"WHERE trade_date = {{trade_date:Date}} ORDER BY {order}",
                        parameters={"trade_date": trade_date}, fmt="JSONEachRow",
                        ) as response:
                    while True:
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        row_count += chunk.count(b"\n")
                        raw_hash.update(chunk)
                        compressed.write(chunk)
                if row_count != count:
                    raise RuntimeError(f"archive row count mismatch: {dataset}")
                size = output.tell()
                output.seek(0)
                digest = hashlib.sha256()
                while True:
                    chunk = output.read(1024 * 1024)
                    if not chunk:
                        break
                    digest.update(chunk)
                content_hash = digest.hexdigest()
                key = (f"clickhouse/dataset={dataset}/"
                       f"trade_date={trade_date.isoformat()}/{content_hash}.json.gz")
                output.seek(0)
                self.objects.client.put_object(
                    self.objects.bucket, key, output, length=size,
                    content_type="application/gzip",
                    metadata={"sha256": content_hash, "dataset": dataset,
                              "schema-version": "1", "row-count": str(count)},
                )
                self._verify(key, content_hash, size)
                assets[dataset] = {
                    "object_key": key, "sha256": content_hash,
                    "raw_sha256": raw_hash.hexdigest(), "size_bytes": size,
                    "row_count": count, "source_fingerprint": str(fingerprint),
                    "state": "verified",
                }
        for dataset in DATASETS:
            if self._state(dataset, trade_date) != states[dataset]:
                raise RuntimeError(f"archive source changed during export: {dataset}")
        manifest = {
            "dataset": "clickhouse_market", "schema_version": 1,
            "trade_date": trade_date.isoformat(), "format": "JSONEachRow+gzip",
            "scope": "collected_data", "row_count": sum(c for c, _ in states.values()),
            "datasets": assets,
        }
        content = _json(manifest)
        digest = hashlib.sha256(content).hexdigest()
        key = (f"manifests/dataset=clickhouse_market/"
               f"trade_date={trade_date.isoformat()}/{digest}/manifest.json")
        self.objects.put(object_key=key, content=content,
                         content_type="application/json",
                         metadata={"dataset": "clickhouse_market", "format": "json"})
        self._verify(key, digest, len(content))
        return {**manifest, "manifest_key": key}


def main() -> None:
    from ..adapters.clickhouse import ClickHouseMarketHistoryStore
    from ..adapters.minio import MinioObjectAssetStore
    from ..storage_config import StorageSettings

    parser = argparse.ArgumentParser(description="Archive a closed trading day's collected market data")
    parser.add_argument("--date", type=date.fromisoformat)
    args = parser.parse_args()
    now = datetime.now(SHANGHAI)
    target = args.date or now.date() - timedelta(days=1)
    if target > now.date() or (target == now.date() and now.hour < 20):
        parser.error("only closed days can be archived; today's archive starts after 20:00 Shanghai time")
    settings = StorageSettings.from_env()
    if not settings.minio_access_key or not settings.minio_secret_key:
        parser.error("TOS credentials are not configured")
    history = ClickHouseMarketHistoryStore(
        host=settings.clickhouse_host, port=settings.clickhouse_port,
        database=settings.clickhouse_database, username=settings.clickhouse_user,
        password=settings.clickhouse_password, send_receive_timeout=300,
    )
    objects = MinioObjectAssetStore(
        settings.minio_endpoint, settings.minio_access_key, settings.minio_secret_key,
        bucket=settings.minio_market_bucket, secure=settings.minio_secure,
        region=settings.minio_region,
    )
    try:
        result = MarketArchive(history_client=history.client, object_store=objects,
                               database=settings.clickhouse_database).run(target)
        print(json.dumps(result, ensure_ascii=False))
    except Exception as exc:
        raise SystemExit(f"market archive failed: {type(exc).__name__}") from None
    finally:
        objects.close()
        history.close()


if __name__ == "__main__":
    main()
