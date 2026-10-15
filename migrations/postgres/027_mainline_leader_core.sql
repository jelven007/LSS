BEGIN;

-- Only future operational plans use V2.1. Frozen paper campaigns, strategy
-- versions, watchlists and previously materialized plans retain their rules.
SET LOCAL banxia.allow_initial_strategy_upgrade = 'on';

UPDATE banxia.strategy_definition
SET current_config = current_config || jsonb_build_object(
        'leader_rule_version', 3,
        'leader_core_only', true,
        'leader_execution_version', 2,
        'minimum_minute_volume_ratio', 1.5,
        'next_day_exit_mode', 'protective_timed',
        'leader_maximum_theme_rank', 2,
        'leader_core_maximum_theme_rank', 2,
        'minimum_score', 60.0,
        'position_limit_pct', 10,
        'portfolio_risk_limit_pct', 20
    ),
    description = 'V2.1 核心实验：仅前二持续主线常规龙头，评分≥60，单票10%/组合20%；开板后放量恢复此前高点，后续分钟验证成交；T+1实际可成交退出。'
WHERE code = 'banxia-mainline-leader-fusion'
  AND catalog_role = 'operational'
  AND NOT archived
  AND current_config->>'strategy_archetype' = 'mainline_leader_fusion'
  AND current_config->>'leader_rule_version' = '2';

COMMIT;
