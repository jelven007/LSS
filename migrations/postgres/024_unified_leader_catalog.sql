BEGIN;

ALTER TABLE banxia.strategy_definition
    ADD COLUMN IF NOT EXISTS catalog_role TEXT NOT NULL DEFAULT 'reference'
        CHECK (catalog_role IN ('operational', 'reference', 'retired', 'paper'));
ALTER TABLE banxia.strategy_definition ALTER COLUMN enabled SET DEFAULT false;

-- Preserve definitions, plans, versions and results; only consolidate operation.
UPDATE banxia.strategy_definition
SET enabled=false,
    catalog_role=CASE
        WHEN code LIKE 'paper-%' THEN 'paper'
        WHEN COALESCE(current_config->>'strategy_archetype', 'first_board_second_board')
             IN ('low_position_first_board','theme_catchup','capacity_trend')
          THEN 'retired'
        ELSE 'reference'
    END
WHERE code <> 'banxia-mainline-leader-fusion';

INSERT INTO banxia.strategy_definition
    (strategy_id, code, name, description, current_config, enabled, catalog_role)
VALUES (
    '6e2b64ea-2c2f-554e-b12f-1dc327946af5',
    'banxia-mainline-leader-fusion',
    '主线龙头统一策略',
    '先确认活跃主线与题材第一顺位，再按启动、接力、首阴修复或断板反包生成唯一入场计划。',
    '{"strategy_archetype":"mainline_leader_fusion","minimum_score":50,
      "max_candidates":3,"max_per_industry":1,
      "minimum_industry_limit_up_count":2,"maximum_break_count":3,
      "maximum_amount_cny":10000000000,
      "entry_open_min_pct":-1,"entry_open_max_pct":4,
      "reject_open_min_pct":-2,"reject_open_max_pct":6,
      "position_limit_pct":15,"portfolio_risk_limit_pct":30}'::jsonb,
    true, 'operational'
) ON CONFLICT (code) DO NOTHING;

CREATE UNIQUE INDEX IF NOT EXISTS strategy_one_operational
    ON banxia.strategy_definition ((catalog_role))
    WHERE catalog_role='operational' AND NOT archived;

ALTER TABLE banxia.strategy_definition
    DROP CONSTRAINT IF EXISTS strategy_operational_identity,
    DROP CONSTRAINT IF EXISTS strategy_enabled_role;
ALTER TABLE banxia.strategy_definition
    ADD CONSTRAINT strategy_operational_identity CHECK (
        catalog_role <> 'operational'
        OR code='banxia-mainline-leader-fusion'
    ),
    ADD CONSTRAINT strategy_enabled_role CHECK (
        NOT enabled OR (catalog_role='operational' AND NOT archived)
    );

-- The old L7 pipeline does not prove leader identity and failed the T+1 replay.
-- Reconcile existing positions, but do not generate or enter new samples.
UPDATE banxia.paper_campaign
SET status='cancelled', updated_at=now()
WHERE code='fusion-l7-v1' AND status='running';

COMMIT;
