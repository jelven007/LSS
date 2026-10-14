from __future__ import annotations

import inspect
import unittest
from datetime import date

from banxia_strategy.data_provider import (
    DEFAULT_PROVIDER,
    PROVIDER_METHODS,
    DataProvider,
    make_provider,
    resolve_provider_name,
)
from banxia_strategy.mootdx_provider import MootdxProvider
from banxia_strategy.rustdx_provider import RustdxProvider, RustdxNotReady


class ProviderContractSurfaceTest(unittest.TestCase):
    """Both backends must expose the frozen surface with matching signatures."""

    def test_frozen_surface_has_17_methods(self):
        self.assertEqual(len(PROVIDER_METHODS), 16)
        # 16 methods + source_name attribute == the 17-member surface.
        self.assertIn("strategy_pool", PROVIDER_METHODS)

    def test_both_backends_implement_every_method(self):
        for backend in (MootdxProvider, RustdxProvider):
            self.assertTrue(
                hasattr(backend, "source_name"),
                f"{backend.__name__} missing source_name",
            )
            for method in PROVIDER_METHODS:
                self.assertTrue(
                    callable(getattr(backend, method, None)),
                    f"{backend.__name__} missing {method}",
                )

    def test_signatures_match_between_backends(self):
        for method in PROVIDER_METHODS:
            moot = inspect.signature(getattr(MootdxProvider, method))
            rust = inspect.signature(getattr(RustdxProvider, method))
            self.assertEqual(
                list(moot.parameters),
                list(rust.parameters),
                f"signature drift on {method}",
            )

    def test_backends_satisfy_runtime_protocol(self):
        # Structural check: instances are DataProvider without inheritance.
        self.assertIsInstance(RustdxProvider(), DataProvider)


class ProviderFactoryTest(unittest.TestCase):
    def test_default_is_mootdx(self):
        self.assertEqual(DEFAULT_PROVIDER, "mootdx")
        self.assertEqual(resolve_provider_name(None), "mootdx")
        self.assertEqual(resolve_provider_name(""), "mootdx")

    def test_resolve_is_case_insensitive_and_validated(self):
        self.assertEqual(resolve_provider_name("RustDX"), "rustdx")
        with self.assertRaises(ValueError):
            resolve_provider_name("tushare")

    def test_factory_builds_requested_backend(self):
        self.assertIsInstance(make_provider("rustdx"), RustdxProvider)
        self.assertEqual(make_provider("rustdx").source_name, "rustdx")
        # mootdx construction must not require a live network client.
        moot = make_provider("mootdx", client_factory=lambda _server: None)
        self.assertIsInstance(moot, MootdxProvider)
        self.assertEqual(moot.source_name, "mootdx")

    def test_factory_reads_environment(self):
        import os
        from unittest.mock import patch

        with patch.dict(os.environ, {"BANXIA_DATA_PROVIDER": "rustdx"}):
            self.assertIsInstance(make_provider(), RustdxProvider)


class RustdxSkeletonTest(unittest.TestCase):
    def setUp(self):
        self.provider = RustdxProvider()

    def test_symbol_validation_before_endpoint(self):
        # Validation errors must surface before the not-wired guard.
        with self.assertRaises(ValueError):
            self.provider.security("ABC")
        with self.assertRaises(ValueError):
            self.provider.history_bars("600000", "decade")

    def test_unwired_endpoints_raise_not_ready_not_silent(self):
        for call in (
            lambda: self.provider.trading_dates(),
            lambda: self.provider.securities(),
            lambda: self.provider.quote_snapshots(["600000"]),
            lambda: self.provider.historical_minutes("600000", date(2026, 10, 14)),
            lambda: self.provider.strategy_pool(date(2026, 10, 14), "mainline_leader_fusion"),
        ):
            with self.assertRaises(RustdxNotReady):
                call()

    def test_empty_quote_input_short_circuits(self):
        self.assertEqual(self.provider.quote_snapshots([]), {})

    def test_minute_normalization_matches_240_bar_contract(self):
        rows = [{"close": 10.0 + i * 0.01, "volume": 100 + i} for i in range(260)]
        bars = RustdxProvider._normalize_minutes(rows, date(2026, 10, 14))
        self.assertEqual(len(bars), 240)
        first = bars[0]
        self.assertEqual(
            set(first),
            {"time", "date", "open", "high", "low", "close", "volume", "amount", "raw"},
        )
        self.assertEqual(first["time"], "2026-10-14T09:31:00+08:00")
        self.assertEqual(first["open"], first["close"])
        self.assertEqual(bars[-1]["time"], "2026-10-14T15:00:00+08:00")

    def test_minute_normalization_drops_nonpositive_prices(self):
        rows = [{"close": 0.0}, {"close": 10.0}, {"close": -1.0}, {"close": 11.0}]
        bars = RustdxProvider._normalize_minutes(rows, date(2026, 10, 14))
        self.assertEqual([b["close"] for b in bars], [10.0, 11.0])

    def test_http_failure_is_not_ready(self):
        def failing_opener(_url, _timeout):
            raise OSError("connection refused")

        provider = RustdxProvider(opener=failing_opener)
        with self.assertRaises(RustdxNotReady):
            provider._get("/v1/anything")


if __name__ == "__main__":
    unittest.main()
