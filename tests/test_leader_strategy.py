from dataclasses import asdict, replace
from datetime import date
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

from banxia_strategy.leader_strategy import leader_setups
from banxia_strategy.market_provider import _extract_pools, _strategy_rows_from_history
from banxia_strategy.strategy import StrategyEngine, _normalize, _render_markdown
from banxia_strategy.strategy_archetypes import LEADER_FUSION, initial_strategy_config
from banxia_strategy.strategy_config import ConfigError, StrategyConfig
from banxia_strategy.application.report_worker import ReportWorker
from banxia_strategy.application.persistence import PersistenceResult
from banxia_strategy.storage_config import StorageSettings
from test_strategy import FakeProvider
from banxia_strategy.api import _report_payload


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


class LeaderV2Test(LeaderStrategyTest):
    def setUp(self):
        super().setUp()
        self.v1 = self.config
        self.config = StrategyConfig.from_file(Path(__file__).parents[1] / "config/leader-v2.json")
        self.market.update(break_rate_pct=25, max_board=4)

    # V1 tests remain in the base class; the cases below exercise changed V2 rules.
    def test_start_requires_known_early_seal_and_no_higher_leader(self):
        pioneer = stock("600001", 1, 首次封板时间="13:30", 换手率=20)
        self.assertIn("600001", self.setups([pioneer]))
        self.assertNotIn("600001", self.setups([pioneer, stock("600002", 2)]))
        self.assertEqual(self.setups([stock("600001", 1, 首次封板时间=None)]), {})
        self.assertEqual(self.setups([stock("600001", 1, 首次封板时间="14:01")]), {})

    def test_missing_market_data_or_cold_environment_closes_gate(self):
        for changes in (
            {"score": 39.9}, {"score": 90, "broken_board_data_available": False},
            {"score": 90, "break_rate_pct": None}, {"score": 90, "break_rate_pct": 55},
            {"score": 45, "break_rate_pct": 45.1}, {"score": 45, "max_board": 2},
        ):
            diagnostics = {}
            self.assertEqual(leader_setups(
                [stock("600001")], [], self.stats, {**self.market, **changes},
                self.config, diagnostics), {})
            self.assertEqual(diagnostics["market_mode"], "closed")
            self.assertEqual(diagnostics["excluded"][0]["stage"], "market")

    def test_only_front_ranked_active_mainlines_are_allowed(self):
        stats = {theme: {**self.stats["机器人"], "today_count": count}
                 for theme, count in (("机器人", 5), ("芯片", 4), ("医药", 3), ("汽车", 2))}
        rows = [stock(code, 3, 所属行业=theme) for code, theme in
                (("600001", "机器人"), ("600002", "芯片"), ("600003", "医药"), ("600004", "汽车"))]
        self.assertEqual(set(leader_setups(rows, [], stats, self.market, self.config)),
                         {"600001", "600002", "600003"})

    def test_engine_emits_one_branch_plan_with_frozen_rules(self):
        engine = StrategyEngine(None, self.config)
        start = engine._build_candidates([stock("600001", 1)], self.stats, self.market)[0]
        self.assertEqual(start.plan["entry_cutoff_time"], "10:00")
        self.assertEqual(start.plan["minimum_first_minute_change_pct"], 3)
        self.assertEqual(start.plan["open_min_pct"], 0)
        self.assertEqual(start.plan["leader_rule_version"], 2)
        self.assertEqual(start.plan["position_limit_pct"], 5)
        self.assertEqual(start.plan["risk_reason"], "startup_trial")
        self.assertTrue(start.plan["t1_required"])

    def test_unversioned_frozen_config_remains_v1(self):
        raw = asdict(self.v1)
        raw.pop("leader_rule_version")
        frozen = StrategyConfig.from_mapping(raw)
        self.assertEqual(frozen.leader_rule_version, 1)
        weak_market = {**self.market, "score": 45, "break_rate_pct": 42}
        self.assertEqual(leader_setups([stock("600001", 2)], [], self.stats, weak_market, frozen), {})
        late = stock("600001", 1, 首次封板时间="13:00")
        self.assertEqual(leader_setups([late], [], self.stats, self.market, frozen), {})

    def test_trial_excludes_high_boards_and_repairs_and_freezes_risk(self):
        engine = StrategyEngine(None, self.config)
        weak = {**self.market, "score": 40, "break_rate_pct": 45, "max_board": 3}
        for boards in (1, 2, 3):
            candidate = engine._build_candidates([stock("600001", boards)], self.stats, weak)[0]
            self.assertEqual(candidate.position_limit_pct, 5)
            self.assertEqual(candidate.plan["position_limit_pct"], 5)
            self.assertEqual(candidate.plan["portfolio_risk_limit_pct"], 30)
            self.assertEqual(candidate.plan["risk_reason"], "market_trial")
            self.assertIn("单票不超过5%", candidate.exit_plan)
            self.assertTrue(candidate.plan["t1_required"])
        self.assertEqual(engine._build_candidates([stock("600001", 4)], self.stats, weak), [])
        prior = [(date(2026, 9, 21), [stock("600001", 3)])]
        repair = stock("600001", 0, _days_since_limit_up=1,
                       _recent_limit_date="2026-09-21", _is_bearish=True)
        self.assertNotIn("600001", leader_setups(
            [repair, stock("600002", 1)], prior, self.stats, weak, self.config))
        engine.config = replace(self.config, position_limit_pct=10)
        self.assertEqual(candidate.plan["position_limit_pct"], 5)

    def test_new_theme_and_moderate_contraction_use_small_position(self):
        engine = StrategyEngine(None, self.config)
        for stats, stage in (
            ({"today_count": 3, "active_days": 1, "momentum": 3, "max_board": 1}, "emerging"),
            ({"today_count": 2, "active_days": 3, "momentum": -2, "max_board": 2}, "divergence"),
        ):
            candidate = engine._build_candidates([stock("600001", stats["max_board"])],
                                                  {"机器人": stats}, self.market)[0]
            self.assertEqual(candidate.plan["leader_evidence"]["theme_stage"], stage)
            self.assertEqual(candidate.position_limit_pct, 5)
        for stats in (
            {"today_count": 2, "active_days": 1, "momentum": 2, "max_board": 1},
            {"today_count": 2, "active_days": 3, "momentum": -2.01, "max_board": 2},
            {"today_count": 2, "active_days": 3, "momentum": -1, "max_board": 1},
        ):
            self.assertEqual(engine._build_candidates([stock("600001", 1)],
                             {"机器人": stats}, self.market), [])

    def test_third_theme_is_observation_with_trial_risk(self):
        stats = {theme: {**self.stats["机器人"], "today_count": count}
                 for theme, count in (("机器人", 5), ("芯片", 4), ("医药", 3))}
        rows = [stock(code, 3, 所属行业=theme) for code, theme in
                (("600001", "机器人"), ("600002", "芯片"), ("600003", "医药"))]
        candidates = StrategyEngine(None, self.config)._build_candidates(
            rows, stats, self.market)
        self.assertEqual({item.code: item.position_limit_pct for item in candidates},
                         {"600001": 15, "600002": 15, "600003": 5})
        self.assertEqual(candidates[-1].plan["risk_reason"], "extended_theme_rank")

    def test_diagnostics_explain_quality_score_and_count_exclusions(self):
        engine = StrategyEngine(None, self.config)
        diag = {}
        rows = [stock("600001", 4, 成交额=100_000_000), stock("600002", 3)]
        self.assertEqual(engine._build_candidates(rows, self.stats, self.market, diagnostics=diag), [])
        self.assertEqual({row["reason"] for row in diag["excluded"]},
                         {"amount_outside_range", "not_theme_leader"})
        engine.config = replace(self.config, minimum_score=1000)
        candidates = engine._build_candidates([stock("600001", 3)], self.stats, self.market, diagnostics=diag)
        self.assertEqual(engine._select(candidates, diag), [])
        self.assertEqual(diag["stage_counts"], {"score": 1})

    def test_report_and_api_preserve_diagnostics(self):
        report = StrategyEngine(FakeProvider(), self.config).run(date(2026, 9, 23))
        self.assertTrue(report.selection_diagnostics)
        self.assertEqual(report.selection_diagnostics["selected_count"], len(report.candidates))
        self.assertEqual(sum(report.selection_diagnostics["stage_counts"].values()), report.rejected_count)
        self.assertIn("筛选诊断", _render_markdown(report))
        self.assertEqual(_report_payload(report.to_dict())["selection_diagnostics"],
                         report.selection_diagnostics)

    def test_config_rejects_unsafe_trial_limits(self):
        for changes in ({"leader_trial_position_limit_pct": 16},
                        {"leader_trial_minimum_market_score": 49},
                        {"leader_trial_maximum_break_rate_pct": 56}):
            with self.assertRaises(ConfigError):
                StrategyConfig.from_mapping({**asdict(self.config), **changes})


if __name__ == "__main__":
    unittest.main()
