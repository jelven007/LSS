from __future__ import annotations

import gzip
import hashlib
import io
import json
import unittest
from datetime import date

from banxia_strategy.application.market_archive import MarketArchive


class FakeHistory:
    def __init__(self, *, changed=False, truncated=False):
        self.changed = changed
        self.truncated = truncated
        self.states = 0

    def query(self, sql, parameters):
        self.states += 1
        rows = 2 if "market_quote_snapshot" in sql else 0
        if self.changed and self.states > 5:
            rows += 1
        return type("Result", (), {"result_rows": [(rows, rows)]})()

    def raw_stream(self, sql, parameters, fmt):
        assert "FINAL" in sql
        assert parameters == {"trade_date": date(2026, 10, 16)}
        assert fmt == "JSONEachRow"
        rows = b'{"price":"10.1234"}\n{"price":null}\n'
        return io.BytesIO(rows[:-1] if self.truncated else rows)


class Response(io.BytesIO):
    def release_conn(self):
        pass


class FakeObjects:
    bucket = "market-raw"

    def __init__(self, *, corrupt=False):
        self.client = self
        self.objects = {}
        self.corrupt = corrupt

    def put_object(self, bucket, key, data, length, **kwargs):
        content = data.read()
        assert length == len(content)
        self.objects[key] = content

    def get_object(self, bucket, key):
        content = self.objects[key]
        return Response(content + b"x" if self.corrupt else content)

    def put(self, *, object_key, content, **kwargs):
        self.objects[object_key] = content


class MarketArchiveTest(unittest.TestCase):
    def test_streamed_archive_round_trip_and_manifest_cover_all_datasets(self):
        objects = FakeObjects()
        archive = MarketArchive(history_client=FakeHistory(), object_store=objects)
        result = archive.run(date(2026, 10, 16))
        self.assertEqual(result["row_count"], 2)
        self.assertEqual(len(result["datasets"]), 5)
        dataset = result["datasets"]["market_quote_snapshot"]
        content = objects.objects[dataset["object_key"]]
        self.assertEqual(gzip.decompress(content),
                         b'{"price":"10.1234"}\n{"price":null}\n')
        self.assertEqual(hashlib.sha256(content).hexdigest(), dataset["sha256"])
        self.assertEqual(dataset["row_count"], 2)
        self.assertEqual(result["datasets"]["market_bar_1m"]["row_count"], 0)
        self.assertIn(result["manifest_key"], objects.objects)
        self.assertEqual(json.loads(objects.objects[result["manifest_key"]])["row_count"], 2)

    def test_corrupt_readback_never_publishes_manifest(self):
        objects = FakeObjects(corrupt=True)
        archive = MarketArchive(history_client=FakeHistory(), object_store=objects)
        with self.assertRaisesRegex(RuntimeError, "readback"):
            archive.run(date(2026, 10, 16))
        self.assertFalse(any(key.endswith("manifest.json") for key in objects.objects))

    def test_stable_retry_uses_identical_object_and_manifest_keys(self):
        objects = FakeObjects()
        archive = MarketArchive(history_client=FakeHistory(), object_store=objects)
        first = archive.run(date(2026, 10, 16))
        keys = set(objects.objects)
        second = archive.run(date(2026, 10, 16))
        self.assertEqual(first, second)
        self.assertEqual(set(objects.objects), keys)

    def test_changed_source_never_publishes_manifest(self):
        objects = FakeObjects()
        archive = MarketArchive(history_client=FakeHistory(changed=True), object_store=objects)
        with self.assertRaisesRegex(RuntimeError, "changed"):
            archive.run(date(2026, 10, 16))
        self.assertFalse(any(key.endswith("manifest.json") for key in objects.objects))

    def test_same_count_price_revision_prevents_manifest(self):
        history = FakeHistory()
        original = history.query

        def revised(sql, parameters):
            result = original(sql, parameters)
            if history.states > 5:
                count, fingerprint = result.result_rows[0]
                result.result_rows = [(count, fingerprint + 1)]
            return result

        history.query = revised
        objects = FakeObjects()
        archive = MarketArchive(history_client=history, object_store=objects)
        with self.assertRaisesRegex(RuntimeError, "changed"):
            archive.run(date(2026, 10, 16))
        self.assertFalse(any(key.endswith("manifest.json") for key in objects.objects))

    def test_truncated_export_never_publishes_manifest(self):
        objects = FakeObjects()
        archive = MarketArchive(history_client=FakeHistory(truncated=True), object_store=objects)
        with self.assertRaisesRegex(RuntimeError, "row count"):
            archive.run(date(2026, 10, 16))
        self.assertEqual(objects.objects, {})

    def test_empty_installation_is_not_reported_as_complete(self):
        history = FakeHistory()
        history.query = lambda *args, **kwargs: type("Result", (), {"result_rows": [(0, 0)]})()
        objects = FakeObjects()
        archive = MarketArchive(history_client=history, object_store=objects)
        with self.assertRaisesRegex(RuntimeError, "no collected market data"):
            archive.run(date(2026, 10, 16))
        self.assertEqual(objects.objects, {})

    def test_database_identifier_is_validated(self):
        with self.assertRaises(ValueError):
            MarketArchive(history_client=FakeHistory(), object_store=FakeObjects(),
                          database="banxia; DROP TABLE x")


if __name__ == "__main__":
    unittest.main()
