from __future__ import annotations

import unittest
from datetime import date
from pathlib import Path

from banxia_strategy.application.leader_paper import (
    DEFAULT_HARD_STOP_PCT,
    LEADER_CAMPAIGN_CODE,
    MainlineLeaderPaperTradingWorker,
    simulate_t1_protective_exit,
)
from banxia_strategy.storage_config import StorageSettings


def minute_bars(prices, volumes):
    return [{"close": price, "volume": volume} for price, volume in zip(prices, volumes)]


def leader_candidate():
    return {
        "code": "600001",
        "name": "龙头股份",
        "industry": "主线题材",
        "latest_price": 10.0,
        "plan": {
            "entry_mode": "board_reseal",
            "open_min_pct": -1.0,
            "open_max_pct": 4.0,
            "minimum_first_minute_change_pct": -20.0,
            "entry_cutoff_time": "10:00",
            "manual_max_intraday_breaks": 1,
            "reject_below_previous_close": True,
            "setup": "leader_relay",
        },
    }


class Provider:
    def __init__(self):
        self.sessions = [
            date(2026, 10, 8),
            date(2026, 10, 9),
            date(2026, 10, 12),
        ]
        self.minutes = {}
        self.daily = {}

    def trading_dates(self):
        return self.sessions

    def historical_minutes(self, symbol, session):
        return self.minutes.get((symbol, session), [])

    def history_bars(self, symbol, _period, *, end_date, max_bars):
        del max_bars
        return [
            {"date": session.isoformat(), **bar}
            for (stored_symbol, session), bar in self.daily.items()
            if stored_symbol == symbol and session <= end_date
        ]


class Repository:
    def __init__(self, config_snapshot=None):
        self.campaign = {
            "campaign_id": "leader-campaign",
            "code": LEADER_CAMPAIGN_CODE,
            "strategy_id": "paper-leader",
            "started_on": "2026-10-03",
            "target_sample_count": 30,
            "status": "running",
            "config_snapshot": config_snapshot or {},
        }
        self.entry_candidates = []
        self.trade = None

    def ensure_paper_campaign(self, _code, **_kwargs):
        return self.campaign

    def get_paper_campaign(self, _code=LEADER_CAMPAIGN_CODE):
        return self.campaign

    def save_trading_sessions(self, _sessions):
        return None

    def get_strategy_day(self, _strategy_id, _trade_date):
        return {"next_plan": {"already_generated": True}}

    def list_paper_entry_candidates(self, _campaign_id, _through_date):
        return list(self.entry_candidates) if self.trade is None else []

    def save_paper_entry(self, _campaign_id, record):
        self.trade = {"paper_trade_id": "trade", **record}

    def list_paper_pending_exits(self, _campaign_id, through_date):
        if (
            self.trade
            and self.trade["status"] in {"open", "exit_data_missing"}
            and date.fromisoformat(self.trade["exit_date"]) <= through_date
        ):
            return [self.trade]
        return []

    def save_paper_exit(self, _paper_trade_id, record):
        self.trade.update(record)

    def refresh_paper_campaign(self, _campaign_id):
        completed = int(
            bool(self.trade and self.trade["status"] in {"closed", "exit_unfilled"})
        )
        positive = int(bool(completed and self.trade.get("positive")))
        return {
            "status": "running",
            "target_sample_count": 30,
            "completed_sample_count": completed,
            "remaining_sample_count": 30 - completed,
            "positive_count": positive,
            "positive_rate_pct": 100.0 if positive else None,
        }


class ProtectiveExitSimulationTest(unittest.TestCase):
    def test_stop_loss_triggers_on_d3_at_threshold(self):
        prices = [10.0, 9.5] + [9.5] * 238
        price, reason, index = simulate_t1_protective_exit(
            10.0, prices, hard_stop_pct=4.0
        )
        self.assertEqual(reason, "t1_d3_stop_loss")
        self.assertAlmostEqual(price, 9.6, places=4)
        self.assertEqual(index, 1)

    def test_opening_gap_below_stop_uses_observed_price(self):
        prices = [9.4] + [9.4] * 239
        price, reason, index = simulate_t1_protective_exit(
            10.0, prices, hard_stop_pct=4.0
        )
        self.assertEqual(reason, "t1_d3_stop_loss")
        self.assertAlmostEqual(price, 9.4, places=4)
        self.assertEqual(index, 0)

    def test_no_stop_forces_exit_at_1456_proxy(self):
        prices = [10.5] * 240
        price, reason, index = simulate_t1_protective_exit(
            10.0, prices, hard_stop_pct=4.0
        )
        self.assertEqual(reason, "t1_d3_force_exit")
        self.assertAlmostEqual(price, 10.5, places=4)
        self.assertEqual(index, 235)

    def test_rejects_invalid_inputs(self):
        with self.assertRaises(ValueError):
            simulate_t1_protective_exit(0.0, [10.0] * 240, hard_stop_pct=4.0)
        with self.assertRaises(ValueError):
            simulate_t1_protective_exit(10.0, [10.0] * 240, hard_stop_pct=0.0)
        with self.assertRaises(ValueError):
            simulate_t1_protective_exit(10.0, [10.0] * 100, hard_stop_pct=4.0)
        with self.assertRaises(ValueError):
            simulate_t1_protective_exit(10.0, [10.0] * 239 + [-1.0], hard_stop_pct=4.0)


class MainlineLeaderPaperWorkerTest(unittest.TestCase):
    def setUp(self):
        self.repository = Repository(
            config_snapshot={"hard_stop_pct": DEFAULT_HARD_STOP_PCT}
        )
        self.repository.entry_candidates = [
            {
                "plan_id": "plan",
                "reference_date": "2026-10-08",
                "entry_date": "2026-10-09",
                "candidate": leader_candidate(),
            }
        ]
        self.provider = Provider()
        # D2 touches the 10% limit (11.0) then opens the board and reseals above
        # the reference close so the board_reseal entry window is proven.
        d2_prices = [10.2, 11.0, 10.8, 10.9] + [10.9] * 236
        volumes = [1_000.0] * 240
        self.provider.minutes[("600001", date(2026, 10, 9))] = minute_bars(
            d2_prices, volumes
        )
        self.provider.daily[("600001", date(2026, 10, 9))] = {
            "open": 10.1,
            "high": 11.0,
            "low": 10.1,
            "close": 10.9,
            "volume": 240_000,
        }
        self.worker = MainlineLeaderPaperTradingWorker(
            repository=self.repository,
            provider=self.provider,
            storage_settings=StorageSettings(),
            strategy_config_path=Path("config/strategy.json"),
            output_dir=Path("reports"),
        )

    def test_defaults_to_leader_campaign(self):
        self.assertEqual(self.worker.campaign_code, LEADER_CAMPAIGN_CODE)

    def test_entry_then_protected_exit_forms_one_sample(self):
        entry = self.worker.run(date(2026, 10, 9))
        self.assertEqual(entry.entries_processed, 1)
        self.assertEqual(self.repository.trade["status"], "open")
        self.assertEqual(self.repository.trade["exit_date"], "2026-10-12")

        # D3 stays above the -4% stop, so the 14:55 instruction force-exits at
        # the 14:56 proxy; the net return is positive after costs.
        d3_prices = [11.5] * 240
        volumes = [1_000.0] * 240
        self.provider.minutes[("600001", date(2026, 10, 12))] = minute_bars(
            d3_prices, volumes
        )
        self.provider.daily[("600001", date(2026, 10, 12))] = {
            "open": 11.5,
            "high": 11.5,
            "low": 11.5,
            "close": 11.5,
            "volume": 240_000,
        }
        exit_result = self.worker.run(date(2026, 10, 12))
        self.assertEqual(exit_result.exits_processed, 1)
        self.assertEqual(self.repository.trade["status"], "closed")
        self.assertEqual(self.repository.trade["exit_reason"], "t1_d3_force_exit")
        self.assertTrue(self.repository.trade["positive"])
        self.assertTrue(self.repository.trade["exit_evidence"]["t1_compliant"])
        self.assertEqual(exit_result.summary["completed_sample_count"], 1)

    def test_stop_loss_day_forms_one_negative_sample(self):
        self.worker.run(date(2026, 10, 9))
        buy_price = float(self.repository.trade["entry_price"])
        # D3 opens below the -4% stop, exiting at the observed first-minute price.
        stop_open = buy_price * 0.95
        d3_prices = [stop_open] * 240
        volumes = [1_000.0] * 240
        self.provider.minutes[("600001", date(2026, 10, 12))] = minute_bars(
            d3_prices, volumes
        )
        self.provider.daily[("600001", date(2026, 10, 12))] = {
            "open": stop_open,
            "high": stop_open,
            "low": stop_open,
            "close": stop_open,
            "volume": 240_000,
        }
        exit_result = self.worker.run(date(2026, 10, 12))
        self.assertEqual(self.repository.trade["status"], "closed")
        self.assertEqual(self.repository.trade["exit_reason"], "t1_d3_stop_loss")
        self.assertFalse(self.repository.trade["positive"])
        self.assertEqual(exit_result.summary["completed_sample_count"], 1)

    def test_missing_d3_minutes_stay_retryable(self):
        self.worker.run(date(2026, 10, 9))
        self.provider.minutes[("600001", date(2026, 10, 12))] = []
        exit_result = self.worker.run(date(2026, 10, 12))
        self.assertEqual(self.repository.trade["status"], "exit_data_missing")
        self.assertEqual(exit_result.summary["completed_sample_count"], 0)


if __name__ == "__main__":
    unittest.main()
