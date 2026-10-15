BEGIN;

-- Explicit operational upgrade. Historical strategy versions, plans and the
-- paper-mainline-leader-fusion-v1 config/campaign snapshots remain untouched.
SET LOCAL banxia.allow_initial_strategy_upgrade = 'on';

UPDATE banxia.strategy_definition
SET current_config = current_config || jsonb_build_object(
        'leader_rule_version', 2,
        'leader_minimum_market_score', 48.0,
        'leader_trial_minimum_market_score', 40.0,
        'leader_trial_maximum_break_rate_pct', 45.0,
        'leader_trial_minimum_market_height', 3,
        'leader_trial_maximum_boards', 3,
        'leader_defense_break_rate_pct', 55.0,
        'leader_emerging_minimum_limit_ups', 3,
        'leader_contraction_minimum_ratio', 0.5,
        'leader_trial_position_limit_pct', 5,
        'leader_maximum_theme_rank', 3,
        'leader_core_maximum_theme_rank', 2,
        'leader_start_maximum_seal_time', '14:00',
        'leader_start_minimum_turnover_pct', 2.0,
        'leader_start_maximum_turnover_pct', 25.0,
        'leader_start_first_minute_pct', 3.0,
        'leader_start_entry_cutoff', '10:00',
        'leader_start_open_min_pct', 0.0,
        'leader_start_open_max_pct', 5.0,
        'minimum_score', 45.0
    ),
    description = 'V2：完整题材池确认第一顺位，区分持续主线、首日启动与有限分歧；弱市仅观察低板龙头，试错单票上限5%，严重退潮关闭。'
WHERE code = 'banxia-mainline-leader-fusion'
  AND catalog_role = 'operational'
  AND NOT archived
  AND current_config->>'strategy_archetype' = 'mainline_leader_fusion'
  AND COALESCE(current_config->>'leader_rule_version', '1') = '1';

COMMIT;
