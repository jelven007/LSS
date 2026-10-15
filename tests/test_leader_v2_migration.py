"""Operational V2 upgrades must not rewrite the frozen V1 experiment/history."""
import json
import os
import unittest
from contextlib import contextmanager
from datetime import date
from pathlib import Path

from banxia_strategy.strategy_config import StrategyConfig


ROOT = Path(__file__).parents[1]


@unittest.skipUnless(os.environ.get("BANXIA_TEST_POSTGRES_DSN"), "requires isolated PostgreSQL")
class LeaderV2MigrationTest(unittest.TestCase):
    def setUp(self):
        import psycopg
        self.connection = psycopg.connect(os.environ["BANXIA_TEST_POSTGRES_DSN"])
        self.addCleanup(self.connection.close)
        self.addCleanup(self.connection.rollback)
        self.migration = (ROOT / "migrations/postgres/026_mainline_leader_v2.sql").read_text()
        # Run within the fixture's transaction, so every mutation is rolled back.
        self.migration = self.migration.removeprefix("BEGIN;").removesuffix("COMMIT;\n")

    def snapshot(self):
        result = {}
        for table, columns, order in (
            ("strategy_definition", "code,current_config,enabled,catalog_role", "code"),
            ("paper_campaign", "code,config_snapshot,status", "code"),
            ("strategy_day", "strategy_id,trade_date,next_plan,execution_plan", "strategy_id,trade_date"),
            ("strategy_version", "*", "strategy_version_id"),
            ("strategy_plan", "*", "plan_id"),
            ("watchlist_item", "*", "watchlist_item_id"),
        ):
            result[table] = self.connection.execute(
                f"SELECT {columns} FROM banxia.{table} ORDER BY {order}"
            ).fetchall()
        return result

    def test_upgrade_preserves_v1_snapshots_and_old_plans(self):
        self.connection.execute("SET LOCAL banxia.allow_initial_strategy_upgrade = 'on'")
        v1 = json.loads((ROOT / "config/leader-v1.json").read_text())
        self.connection.execute(
            """UPDATE banxia.strategy_definition SET current_config=%s::jsonb
            WHERE code='banxia-mainline-leader-fusion'""", (json.dumps(v1),))
        self.connection.execute(
            """INSERT INTO banxia.strategy_day(strategy_id,trade_date,next_plan,execution_plan)
            SELECT strategy_id,'2026-10-14','{"version":"frozen-v1"}','{"version":"frozen-v1"}'
            FROM banxia.strategy_definition WHERE code='banxia-mainline-leader-fusion'
            ON CONFLICT (strategy_id,trade_date) DO NOTHING""")
        from banxia_strategy.adapters.postgres import PostgresStorage
        from banxia_strategy.strategy import StrategyEngine
        from test_strategy import FakeProvider
        @contextmanager
        def factory():
            yield self.connection
        repository = PostgresStorage(connection_factory=factory)
        report = StrategyEngine(FakeProvider(), StrategyConfig.from_mapping(v1)).run(
            date(2026, 9, 23)).to_dict()
        report["next_session"] = "2026-09-24"
        source = repository.ensure_leader_strategy()
        report.update(strategy_id=source["strategy_id"], strategy_code=source["code"],
                      strategy_name=source["name"])
        repository.persist_report(report, strategy_version="migration-frozen-v1",
                                  strategy_config=v1, code_commit="test", enqueue_events=False)
        before = self.snapshot()
        self.connection.execute(self.migration)
        after = self.snapshot()
        self.assertTrue(before["strategy_version"])
        self.assertTrue(before["strategy_plan"])
        for table in ("paper_campaign", "strategy_day", "strategy_version", "strategy_plan", "watchlist_item"):
            self.assertEqual(before[table], after[table], table)
        self.assertEqual(
            [row for row in before["strategy_definition"] if row[0] != "banxia-mainline-leader-fusion"],
            [row for row in after["strategy_definition"] if row[0] != "banxia-mainline-leader-fusion"],
        )
        active = next(row[1] for row in after["strategy_definition"] if row[0] == "banxia-mainline-leader-fusion")
        self.assertEqual(StrategyConfig.from_mapping(active),
                         StrategyConfig.from_file(ROOT / "config/strategy.json"))
        paper = next(row[1] for row in after["strategy_definition"] if row[0] == "paper-mainline-leader-fusion-v1")
        self.assertEqual(StrategyConfig.from_mapping(paper).leader_rule_version, 1)
        self.connection.execute(self.migration)
        self.assertEqual(after, self.snapshot())

    def test_repeat_migration_preserves_later_v2_adjustment(self):
        self.connection.execute("SET LOCAL banxia.allow_initial_strategy_upgrade = 'on'")
        self.connection.execute(
            """UPDATE banxia.strategy_definition
            SET current_config=jsonb_set(current_config,'{minimum_score}','47')
            WHERE code='banxia-mainline-leader-fusion'""")
        before = self.snapshot()
        self.connection.execute(self.migration)
        self.assertEqual(before, self.snapshot())


if __name__ == "__main__":
    unittest.main()
