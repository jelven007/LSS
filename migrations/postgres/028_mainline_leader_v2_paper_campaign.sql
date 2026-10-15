BEGIN;

-- Freeze a V2.1 core-execution config into a dedicated paper identity WITHOUT
-- upgrading the operational strategy. Production keeps running V1; this paper
-- campaign is the only place V2.1 runs, accumulating forward T+1 samples.
--
-- The V2.1 overrides below are the combined 026 + 027 parameter sets applied
-- inline to the operational (V1) config. Equivalent to loading
-- config/strategy.json; a config-parity test guards against drift. The frozen
-- V1 paper identity/campaign from 025 stay untouched, and a forward-only
-- campaign never inherits any earlier archetype's win rate.
INSERT INTO banxia.strategy_definition (
    strategy_id,
    code,
    name,
    description,
    current_config,
    enabled,
    archived,
    parent_strategy_id,
    config_changes,
    catalog_role
)
SELECT
    '390db5b3-a74e-53ae-b8d2-c540db224277'::uuid,
    'paper-mainline-leader-fusion-v2',
    '主线龙头 V2.1 核心模拟盘',
    '冻结主线龙头 V2.1 核心执行参数（仅前二持续主线常规龙头、评分≥60、10%/20% 仓位，开板后放量恢复高点买点，T+1 实际可成交退出），仅用于前向模拟成交和新增样本验证；不改动线上运行策略。',
    source.current_config || jsonb_build_object(
        'leader_rule_version', 3,
        'leader_core_only', true,
        'leader_execution_version', 2,
        'minimum_minute_volume_ratio', 1.5,
        'next_day_exit_mode', 'protective_timed',
        'leader_minimum_market_score', 48.0,
        'leader_trial_minimum_market_score', 40.0,
        'leader_trial_maximum_break_rate_pct', 45.0,
        'leader_trial_minimum_market_height', 3,
        'leader_trial_maximum_boards', 3,
        'leader_defense_break_rate_pct', 55.0,
        'leader_emerging_minimum_limit_ups', 3,
        'leader_contraction_minimum_ratio', 0.5,
        'leader_trial_position_limit_pct', 5,
        'leader_maximum_theme_rank', 2,
        'leader_core_maximum_theme_rank', 2,
        'leader_start_maximum_seal_time', '14:00',
        'leader_start_minimum_turnover_pct', 2.0,
        'leader_start_maximum_turnover_pct', 25.0,
        'leader_start_first_minute_pct', 3.0,
        'leader_start_entry_cutoff', '10:00',
        'leader_start_open_min_pct', 0.0,
        'leader_start_open_max_pct', 5.0,
        'minimum_score', 60.0,
        'position_limit_pct', 10,
        'portfolio_risk_limit_pct', 20
    ),
    false,
    false,
    source.strategy_id,
    jsonb_build_object(
        'paper_campaign',
        jsonb_build_object(
            'source', source.code,
            'purpose', '30-new-sample-forward-validation'
        )
    ),
    'paper'
FROM banxia.strategy_definition AS source
WHERE source.code = 'banxia-mainline-leader-fusion'
  AND source.catalog_role = 'operational'
  AND NOT source.archived
ON CONFLICT (code) DO NOTHING;

INSERT INTO banxia.paper_campaign (
    campaign_id,
    code,
    strategy_id,
    started_on,
    target_sample_count,
    config_snapshot
)
SELECT
    'c0eb1367-6a69-52c0-adeb-c43b1f897127'::uuid,
    'mainline-leader-fusion-v2',
    strategy_id,
    CURRENT_DATE,
    30,
    current_config || jsonb_build_object(
        'mainline_leader_fusion_v2',
        jsonb_build_object(
            'description',
            '主线龙头 V2.1 核心前向模拟盘：仅执行常规核心龙头；D2 需触板开板后放量恢复此前已完成分钟高点并通过确认时点板块样本，确认之后的有量非涨停分钟才成交，遵守 T+1；D3 起 -4% 保护止损优先，未触发则 14:55 发出定时退出，按实际可成交分钟价成交，无法成交则保留重试不计入已结算样本。',
            'execution_version', 2,
            'core_only', true,
            'exit_session', 'D3',
            'exit_hard_stop_pct', 4.0,
            'exit_force_instruction', '14:55',
            't1_compliant', true,
            'purpose', '30-new-sample-forward-validation'
        )
    )
FROM banxia.strategy_definition
WHERE code = 'paper-mainline-leader-fusion-v2'
ON CONFLICT (code) DO NOTHING;

COMMIT;
