BEGIN;

-- Freeze the operational mainline-leader config into a dedicated paper identity.
-- The forward-only campaign accumulates genuine T+1-settled samples; it never
-- inherits the historical win rate of any earlier archetype.
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
    '1e6ea1fb-2260-51fc-80ac-8adb4c09dd6d'::uuid,
    'paper-mainline-leader-fusion-v1',
    '主线龙头统一策略模拟盘 V1',
    '冻结主线龙头统一策略运行参数，仅用于前向模拟成交和新增样本验证，遵守 A 股 T+1。',
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
    'cc074131-02c2-5ed1-8b5d-7ae842bc7f90'::uuid,
    'mainline-leader-fusion-v1',
    strategy_id,
    CURRENT_DATE,
    30,
    current_config || jsonb_build_object(
        'mainline_leader_fusion_v1',
        jsonb_build_object(
            'description',
            '主线龙头统一策略前向模拟盘：按运行目录识别完整题材池第一顺位并按生命周期入场；D2 按计划分钟窗口成交，遵守 T+1，D3 以 -4% 保护止损为先，未触发则 14:55 发出强制退出指令并以 14:56 分钟价代理。',
            'exit_session', 'D3',
            'exit_hard_stop_pct', 4.0,
            'exit_force_instruction', '14:55',
            'exit_force_proxy', '14:56',
            't1_compliant', true,
            'purpose', '30-new-sample-forward-validation'
        )
    )
FROM banxia.strategy_definition
WHERE code = 'paper-mainline-leader-fusion-v1'
ON CONFLICT (code) DO NOTHING;

COMMIT;
