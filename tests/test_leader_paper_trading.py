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
        self.pools = {}

    def trading_dates(self):
        return self.sessions

    def historical_minutes(self, symbol, session):
        return self.minutes.get((symbol, session), [])

    def limit_up_pool(self, session):
        return list(self.pools.get(session, []))

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


def v21_candidate():
    candidate = leader_candidate()
    candidate["plan"].update(
        leader_execution_version=2,
        minimum_minute_volume_ratio=1.5,
        next_day_exit_mode="protective_timed",
        minimum_sector_sample_size=2,
        minimum_sector_rise_ratio=0.5,
    )
    return candidate


def daily_from_minutes(prices, volumes):
    return {
        "open": prices[0], "high": max(prices), "low": min(prices),
        "close": prices[-1], "volume": sum(volumes),
    }


class MainlineLeaderV2PaperWorkerTest(unittest.TestCase):
    def setUp(self):
        self.repository = Repository(
            config_snapshot={"leader_execution_version": 2, "hard_stop_pct": 4.0,
                             "minimum_minute_volume_ratio": 1.5,
                             "minimum_sector_sample_size": 2, "minimum_sector_rise_ratio": 0.5}
        )
        self.repository.entry_candidates = [{
            "plan_id": "plan", "reference_date": "2026-10-08", "entry_date": "2026-10-09",
            "candidate": v21_candidate(),
        }]
        self.provider = Provider()
        # D2: touch limit (11.0) at idx6, open board, then recover above the
        # prior post-break high with a volume spike at idx12 WITHOUT resealing,
        # leaving idx13 as a usable sub-limit fill minute with ample capacity.
        d2_prices = [10.1, 10.3, 10.5, 10.7, 10.9, 10.95, 11.0, 10.8, 10.82,
                     10.85, 10.88, 10.9, 10.95, 10.93] + [10.9] * 226
        d2_vol = [2_000.0, 2_000, 2_000, 2_000, 2_000, 2_000, 6_000, 2_400, 2_200,
                  2_200, 2_200, 2_400, 12_000, 3_000] + [3_000.0] * 226
        self.provider.minutes[("600001", date(2026, 10, 9))] = minute_bars(d2_prices, d2_vol)
        self.provider.daily[("600001", date(2026, 10, 9))] = daily_from_minutes(d2_prices, d2_vol)
        # A same-theme D1 limit-up peer that is up at the confirmation minute.
        peer_prices = [10.5] * 240
        peer_vol = [200.0] * 240
        self.provider.minutes[("600002", date(2026, 10, 9))] = minute_bars(peer_prices, peer_vol)
        self.provider.daily[("600002", date(2026, 10, 9))] = daily_from_minutes(peer_prices, peer_vol)
        self.provider.pools[date(2026, 10, 8)] = [
            {"代码": "600001", "名称": "龙头股份", "所属行业": "主线题材",
             "最新价": 10.0, "连板数": 2},
            {"代码": "600002", "名称": "同题材二", "所属行业": "主线题材",
             "最新价": 10.0, "连板数": 1},
        ]
        self.worker = MainlineLeaderPaperTradingWorker(
            repository=self.repository, provider=self.provider,
            storage_settings=StorageSettings(),
            strategy_config_path=Path("config/strategy.json"),
            output_dir=Path("reports"), campaign_code="mainline-leader-fusion-v2",
        )

    def test_strength_entry_with_sector_gate_opens_position(self):
        entry = self.worker.run(date(2026, 10, 9))
        self.assertEqual(entry.entries_processed, 1)
        trade = self.repository.trade
        self.assertEqual(trade["status"], "open")
        self.assertEqual(trade["exit_date"], "2026-10-12")
        self.assertEqual(trade["entry_evidence"]["reason_code"], "buyable_but_failed")
        self.assertEqual(trade["entry_evidence"]["leader_execution_version"], 2)
        self.assertGreaterEqual(trade["entry_evidence"]["sector_confirmation"]["sample_size"], 2)
        self.assertEqual(str(trade["entry_time"])[:5], "09:44")

    def test_sector_gate_rejects_when_theme_is_weak(self):
        # Two same-theme peers trade below their reference, so the rise ratio
        # (only the candidate itself is up) falls below the 0.5 gate.
        for code in ("600002", "600003"):
            weak = [9.5] * 240
            self.provider.minutes[(code, date(2026, 10, 9))] = minute_bars(weak, [2_000.0] * 240)
            self.provider.daily[(code, date(2026, 10, 9))] = daily_from_minutes(weak, [2_000.0] * 240)
        self.provider.pools[date(2026, 10, 8)] = [
            {"代码": "600001", "名称": "龙头股份", "所属行业": "主线题材", "最新价": 10.0, "连板数": 2},
            {"代码": "600002", "名称": "同题材二", "所属行业": "主线题材", "最新价": 10.0, "连板数": 1},
            {"代码": "600003", "名称": "同题材三", "所属行业": "主线题材", "最新价": 10.0, "连板数": 1},
        ]
        self.worker.run(date(2026, 10, 9))
        self.assertEqual(self.repository.trade["status"], "rejected")
        self.assertEqual(self.repository.trade["entry_evidence"]["reason_code"],
                         "sector_evidence_missing_or_weak")

    def test_causal_exit_settles_open_position_on_d3(self):
        self.worker.run(date(2026, 10, 9))
        buy_price = float(self.repository.trade["entry_price"])
        # D3 holds above the -4% stop, so the 14:55 timed instruction fills.
        d3_prices = [buy_price * 1.03] * 240
        d3_vol = [1_000.0] * 240
        self.provider.minutes[("600001", date(2026, 10, 12))] = minute_bars(d3_prices, d3_vol)
        self.provider.daily[("600001", date(2026, 10, 12))] = daily_from_minutes(d3_prices, d3_vol)
        exit_result = self.worker.run(date(2026, 10, 12))
        self.assertEqual(exit_result.exits_processed, 1)
        self.assertEqual(self.repository.trade["status"], "closed")
        self.assertEqual(self.repository.trade["exit_reason"], "timed_exit")
        self.assertTrue(self.repository.trade["exit_evidence"]["t1_compliant"])
        self.assertEqual(exit_result.summary["completed_sample_count"], 1)


if __name__ == "__main__":
    unittest.main()
