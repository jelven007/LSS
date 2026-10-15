from __future__ import annotations

import inspect
import json
import unittest
from datetime import date
from unittest.mock import Mock, patch

from banxia_strategy.data_provider import (
    DEFAULT_PROVIDER, PROVIDER_METHODS, DataProvider, make_provider, resolve_provider_name,
)
from banxia_strategy.rustdx_client import (
    RUSTDX_SOURCE_REV, RustdxNotReady, RustdxQuoteClient, native_module, records,
)
from banxia_strategy.rustdx_provider import RustdxProvider


class ProviderContractTest(unittest.TestCase):
    def test_native_backend_implements_frozen_contract(self):
        self.assertEqual(len(PROVIDER_METHODS), 16)
        self.assertIsInstance(RustdxProvider(), DataProvider)
        for method in PROVIDER_METHODS:
            self.assertEqual(
                list(inspect.signature(getattr(DataProvider, method)).parameters),
                list(inspect.signature(getattr(RustdxProvider, method)).parameters),
                method,
            )

    def test_rustdx_is_default_and_legacy_config_is_normalized(self):
        self.assertEqual(DEFAULT_PROVIDER, "rustdx")
        for name in (None, "", " ", "RustDX", "mootdx", " MootDX "):
            self.assertEqual(resolve_provider_name(name), "rustdx")
            with patch.dict("os.environ", {}, clear=True):
                provider = make_provider(name, client_factory=lambda _: None)
            self.assertIsInstance(provider, RustdxProvider)
            self.assertEqual(provider.source_name, "rustdx")
        for unsupported in ("tushare", "tdxpy", "pytdx"):
            with self.assertRaises(ValueError):
                resolve_provider_name(unsupported)

    def test_factory_reads_environment_and_configured_nodes(self):
        with patch.dict("os.environ", {
            "BANXIA_DATA_PROVIDER": "mootdx",
            "BANXIA_RUSTDX_SERVERS": "127.0.0.1:7709,127.0.0.2:7710",
        }):
            provider = make_provider()
        self.assertEqual(provider.source_name, "rustdx")
        self.assertEqual(provider.servers, (("127.0.0.1", 7709), ("127.0.0.2", 7710)))

    def test_invalid_requests_never_open_a_connection(self):
        factory = Mock(side_effect=AssertionError("unexpected connection"))
        provider = RustdxProvider(client_factory=factory)
        with self.assertRaises(ValueError):
            provider.security("ABC")
        with self.assertRaises(ValueError):
            provider.history_bars("600000", "decade")
        self.assertEqual(provider.quote_snapshots([]), {})
        factory.assert_not_called()

    def test_transport_failure_is_explicit(self):
        provider = RustdxProvider(
            servers=[("offline", 7709)],
            client_factory=Mock(side_effect=RustdxNotReady("connection refused")),
        )
        with self.assertRaisesRegex(RuntimeError, "No usable rustdx.*connection refused"):
            provider.historical_minutes("600000", date(2026, 10, 14))

    def test_missing_minute_rows_retry_next_node(self):
        empty, good = Mock(), Mock()
        empty.minutes_json.return_value = "[]"
        good.minutes_json.return_value = '[{"price":10,"vol":5}]'
        clients = iter([empty, good])
        provider = RustdxProvider(
            servers=[("one", 7709), ("two", 7709)],
            client_factory=lambda server: RustdxQuoteClient(server, native=next(clients)),
        )
        result = provider.historical_minutes("600000", date(2026, 10, 14))
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["time"], "2026-10-14T09:31:00+08:00")
        empty.close.assert_called_once()
        good.close.assert_called_once()

    def test_minute_session_and_lunch_boundary_preserve_units(self):
        native = Mock()
        native.minutes_json.return_value = json.dumps([
            {"price": 10 + index * 0.01, "vol": 100 + index} for index in range(240)
        ])
        provider = RustdxProvider(
            servers=[("node", 7709)],
            client_factory=lambda server: RustdxQuoteClient(server, native=native),
        )
        bars = provider.historical_minutes("600000", date(2026, 10, 14))
        self.assertEqual(len(bars), 240)
        self.assertEqual(bars[119]["time"], "2026-10-14T11:30:00+08:00")
        self.assertEqual(bars[120]["time"], "2026-10-14T13:01:00+08:00")
        self.assertEqual(bars[-1]["time"], "2026-10-14T15:00:00+08:00")
        self.assertEqual(bars[-1]["volume"], 339.0)
        self.assertIsNone(bars[-1]["amount"])
        self.assertEqual(bars[-1]["raw"]["source"], "rustdx")


class NativeAdapterTest(unittest.TestCase):
    def setUp(self):
        self.native = Mock()
        self.client = RustdxQuoteClient(("node", 7709), native=self.native)

    def test_json_record_contract_rejects_invalid_responses(self):
        for payload in ('{"error":"offline"}', "null", "[null]", "[1]", "invalid"):
            with self.assertRaises(RustdxNotReady):
                records(payload)

    def test_quote_five_levels_are_mapped_without_volume_scaling(self):
        row = {"code": "600000", "price": 10, "vol": 500, "amount": 2.0 ** -127}
        for side in ("bid", "ask"):
            for level in range(1, 6):
                row[f"{side}{level}"] = 10.0
                row[f"{side}{level}_vol"] = level * 100
        self.native.quotes_json.return_value = json.dumps([row])
        quote = self.client.quotes(symbol=["600000"]).iloc[0].to_dict()
        self.assertEqual(quote["vol"], 500)
        self.assertEqual(quote["amount"], 0)
        self.assertEqual(quote["bid_vol1"], 100)
        self.assertEqual(quote["ask_vol5"], 500)
        self.assertNotIn("bid1_vol", quote)
        self.native.quotes_json.assert_called_once_with([(1, "600000")])

    def test_unrequested_quote_is_rejected(self):
        self.native.quotes_json.return_value = '[{"code":"000001","price":10}]'
        with self.assertRaises(RustdxNotReady):
            self.client.quotes(symbol="600000")

    def test_native_bar_dates_prices_and_volumes(self):
        self.native.bars_json.return_value = json.dumps([{
            "dt": {"year": 2026, "month": 10, "day": 14, "hour": 15, "minute": 0},
            "open": 10, "high": 11, "low": 9, "close": 10.5, "vol": 300, "amount": 315000,
        }])
        row = self.client.bars(symbol="000001").iloc[0]
        self.assertEqual(row["datetime"], "2026-10-14 15:00:00")
        self.assertEqual(row["vol"], 300)
        self.assertEqual(row["amount"], 315000)
        self.native.bars_json.assert_called_once_with(0, "000001", 9, 0, 800, False)

    def test_invalid_minutes_fail_without_compressing_or_filling_time(self):
        for rows in (
            [], [{"price": 0, "vol": 5}], [{"price": 10, "vol": -1}],
            [{"price": 10}], [{"price": 10, "vol": 1}] * 241,
        ):
            self.native.minutes_json.return_value = json.dumps(rows)
            with self.assertRaises(RustdxNotReady):
                self.client.minutes(symbol="000001", date="20261014")

    def test_catalog_fetch_is_cached_and_paged(self):
        rows = [{"code": f"{i:06d}", "name": str(i)} for i in range(1001)]
        self.native.stocks_json.return_value = json.dumps(rows)
        self.assertEqual(self.client.stock_count(0), 1001)
        self.assertEqual(len(self.client.get_security_list(0, 0)), 1000)
        self.assertEqual(self.client.get_security_list(0, 1000)[0]["code"], "001000")
        self.native.stocks_json.assert_called_once_with(0)

    def test_finance_f10_and_xdxr_compatibility(self):
        self.native.finance_json.return_value = '{"code":"000001","liutongguben":100000000}'
        self.assertEqual(self.client.finance(symbol="000001").iloc[0]["liutongguben"], 100000000)
        self.native.f10_json.return_value = '[[" 公司概况 ","内容"]]'
        self.assertEqual(self.client.F10(symbol="000001", name="公司概况"), "内容")
        self.assertEqual(self.client.F10C(symbol="000001"), [{"name": "公司概况", "length": 4}])
        self.native.xdxr_json.return_value = '[{"category":1,"fenhong":2}]'
        self.assertEqual(self.client.xdxr(symbol="000001").iloc[0]["fenhong"], 2)

    def test_historical_transactions_keep_minute_time_and_hands(self):
        self.native.transactions_json.return_value = '[{"time":"09:31","price":10,"vol":20,"buyorsell":0}]'
        row = self.client.transactions(symbol="000001", date="20261014", start=100, offset=20).iloc[0]
        self.assertEqual(row["time"], "09:31")
        self.assertEqual(row["vol"], 20)
        self.native.transactions_json.assert_called_once_with(0, "000001", 20261014, 100, 20)

    def test_missing_or_wrong_native_build_is_explicit(self):
        with patch("banxia_strategy.rustdx_client.importlib.import_module", side_effect=ImportError):
            with self.assertRaisesRegex(RustdxNotReady, "缺少"):
                native_module()
        with patch("banxia_strategy.rustdx_client.importlib.import_module", return_value=Mock(RUSTDX_SOURCE_REV="wrong")):
            with self.assertRaisesRegex(RustdxNotReady, "版本不匹配"):
                native_module()
        with patch("banxia_strategy.rustdx_client.importlib.import_module", return_value=Mock(RUSTDX_SOURCE_REV=RUSTDX_SOURCE_REV)):
            self.assertIsNotNone(native_module())


if __name__ == "__main__":
    unittest.main()
