from dataclasses import asdict
from datetime import date
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

from banxia_strategy.leader_strategy import leader_setups
from banxia_strategy.mootdx_provider import _extract_pools, _strategy_rows_from_history
from banxia_strategy.strategy import StrategyEngine, _normalize
from banxia_strategy.strategy_archetypes import LEADER_FUSION, initial_strategy_config
from banxia_strategy.strategy_config import StrategyConfig
from banxia_strategy.application.report_worker import ReportWorker
from banxia_strategy.application.persistence import PersistenceResult
from banxia_strategy.storage_config import StorageSettings
from test_strategy import FakeProvider


def stock(code, boards=3, **changes):
    return _normalize({
        "代码": code, "名称": code, "所属行业": "机器人", "连板数": boards,
        "最新价": 10, "成交额": 800_000_000, "流通市值": 5_000_000_000,
        "换手率": 10, "首次封板时间": "09:40", "最后封板时间": "09:40",
        "封板资金": 100_000_000, **changes,
    })


class LeaderStrategyTest(unittest.TestCase):
    def setUp(self):
        self.config = StrategyConfig.from_mapping(
            initial_strategy_config(LEADER_FUSION, asdict(StrategyConfig()))
        )
        self.market = {"score": 70, "broken_board_data_available": True}
        self.stats = {"机器人": {"today_count": 3, "active_days": 3,
                                 "momentum": 1, "max_board": 3}}

    def setups(self, rows, pools=()):
        return leader_setups(rows, pools, self.stats, self.market, self.config)

    def test_rejected_real_leader_does_not_promote_a_follower(self):
        leader = stock("600001", 4, 成交额=20_000_000_000)
        follower = stock("600002", 3)
        engine = StrategyEngine(None, self.config)
        self.assertEqual(
            engine._build_candidates([leader, follower], self.stats, self.market),
            [],
        )

    def test_start_requires_known_early_seal_and_no_higher_leader(self):
        pioneer = stock("600001", 1)
        self.assertEqual(self.setups([pioneer])["600001"]["setup"], "leader_start")
        self.assertNotIn("600001", self.setups([pioneer, stock("600002", 2)]))
        self.assertEqual(self.setups([stock("600001", 1, 首次封板时间=None)]), {})
        self.assertEqual(self.setups([stock("600001", 1, 换手率=20)]), {})

    def test_repair_requires_exact_prior_leader_and_no_replacement(self):
        prior = [(date(2026, 9, 21), [stock("600001", 3), stock("600002", 2)])]
        repair = stock("600001", 0, _days_since_limit_up=2,
                       _recent_limit_date="2026-09-21", _reversal_confirmed=True)
        peer = stock("600003", 1)
        setup = self.setups([repair, peer], prior)["600001"]
        self.assertEqual(setup["setup"], "leader_reversal")
        self.assertEqual(setup["leader_evidence"]["prior_leader_height"], 3)
        follower = stock("600002", 0, _days_since_limit_up=2,
                         _recent_limit_date="2026-09-21", _reversal_confirmed=True)
        self.assertNotIn("600002", self.setups([follower, peer], prior))
        self.assertNotIn("600001", self.setups([repair, stock("600003", 3)], prior))
        self.assertNotIn("600001", self.setups([repair, peer]))

    def test_missing_market_data_or_cold_environment_closes_gate(self):
        for market in (
            {"score": 47, "broken_board_data_available": True},
            {"score": 90, "broken_board_data_available": False},
        ):
            self.assertEqual(leader_setups(
                [stock("600001")], [], self.stats, market, self.config,
            ), {})

    def test_only_front_ranked_active_mainlines_are_allowed(self):
        stats = {theme: {**self.stats["机器人"], "today_count": count}
                 for theme, count in (("机器人", 5), ("芯片", 4), ("医药", 3))}
        rows = [stock(code, 3, 所属行业=theme)
                for code, theme in (("600001", "机器人"), ("600002", "芯片"), ("600003", "医药"))]
        self.assertEqual(set(leader_setups(rows, [], stats, self.market, self.config)),
                         {"600001", "600002"})
        stats["机器人"]["active_days"] = 1
        self.assertNotIn("600001", leader_setups(rows, [], stats, self.market, self.config))

    def test_engine_emits_one_branch_plan_with_frozen_rules(self):
        engine = StrategyEngine(None, self.config)
        start = engine._build_candidates([stock("600001", 1)], self.stats, self.market)[0]
        self.assertEqual(start.plan["entry_cutoff_time"], "09:43")
        self.assertEqual(start.plan["minimum_first_minute_change_pct"], 6.5)
        self.assertEqual(start.plan["open_min_pct"], 0.5)
        self.assertEqual(start.plan["setup"], "leader_start")
        prior = [(date(2026, 9, 21), [stock("600002", 3)])]
        repair = stock("600002", 0, _days_since_limit_up=1,
                       _recent_limit_date="2026-09-21", _is_bearish=True)
        plans = engine._build_candidates([repair, stock("600003", 1)],
                                         self.stats, self.market, prior)
        candidate = next(item for item in plans if item.code == "600002")
        self.assertEqual(candidate.plan["entry_mode"], "reclaim_open")
        self.assertFalse(candidate.plan["reject_below_previous_close"])
        self.assertTrue(candidate.plan["t1_required"])
        self.assertIn("A股T+1", candidate.exit_plan)
        self.assertEqual(candidate.position_limit_pct, 15)

    def test_provider_keeps_recent_limit_date_on_second_and_third_repair_days(self):
        histories = {"600001": [
            {"datetime": f"2026-09-{day:02d}", "close": close, "open": close - .1,
             "low": close - .2, "high": close + .1, "amount": 800_000_000}
            for day, close in [(18, 10), (21, 11), (22, 10.8), (23, 10.9), (24, 11.1)]
        ]}
        names = {"600001": "样本"}
        pools, _ = _extract_pools(histories, names)
        for day, offset in [(23, 2), (24, 3)]:
            rows = _strategy_rows_from_history(
                histories, names, pools, {}, {"600001": "机器人"},
                date(2026, 9, day), LEADER_FUSION.key,
            )
            self.assertEqual(rows[0]["_recent_limit_date"], "2026-09-21")
            self.assertEqual(rows[0]["_days_since_limit_up"], offset)
            self.assertEqual(rows[0]["_previous_board_count"], 0)
            self.assertEqual(rows[0]["最新价"], 10.9 if day == 23 else 11.1)

    def test_local_report_keeps_new_identity_separate_from_original_history(self):
        with tempfile.TemporaryDirectory() as tmp, patch(
            "banxia_strategy.application.report_worker.persist_report_copy",
            return_value=PersistenceResult(False),
        ), patch("banxia_strategy.application.report_worker.write_completion_marker"):
            config_path = Path(tmp) / "strategy.json"
            config_path.write_text(json.dumps(asdict(self.config)))
            report, _, _ = ReportWorker(
                strategy_config_path=config_path, output_dir=Path(tmp),
                storage_settings=StorageSettings(), provider=FakeProvider(),
            ).run(date(2026, 9, 23))
            self.assertEqual(report.strategy_code, LEADER_FUSION.code)
            self.assertEqual(report.strategy_name, LEADER_FUSION.name)


if __name__ == "__main__":
    unittest.main()
