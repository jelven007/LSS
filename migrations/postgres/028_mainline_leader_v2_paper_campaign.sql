BEGIN;

-- Freeze the V2.1 operational config into a second, dedicated paper identity.
-- This runs after 027 has upgraded the operational row to leader_rule_version=3,
-- so the snapshot captures core execution (core-only, strength entry, causal
-- T+1 exit). The frozen V1 paper identity/campaign from 025 stay untouched; a
-- forward-only campaign never inherits any earlier archetype's win rate.
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
    '冻结主线龙头 V2.1 核心执行参数（仅前二持续主线常规龙头、评分≥60、10%/20% 仓位，开板后放量恢复高点买点，T+1 实际可成交退出），仅用于前向模拟成交和新增样本验证。',
    source.current_config,
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
  AND source.current_config->>'leader_rule_version' = '3'
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
