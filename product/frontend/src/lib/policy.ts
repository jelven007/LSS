export interface PolicyReportSummary {
  trade_date: string
  plan_date?: string | null
  generated_at?: string | null
  candidate_count: number
  market_regime?: string | null
  market_score?: number | null
  strategy_version?: string | null
}

export interface PolicyReportList {
  items: PolicyReportSummary[]
  next_cursor?: string | null
}

export interface PolicyPlanRule {
  open_min_pct?: number | null
  open_max_pct?: number | null
  reject_min_pct?: number | null
  reject_max_pct?: number | null
  entry_cutoff_time?: string | null
  hard_stop_pct?: number | null
  [key: string]: unknown
}

export interface PolicyCandidate {
  symbol: string
  name: string
  industry?: string | null
  rank?: number | null
  score?: number | null
  strategy?: string | null
  latest_price?: number | null
  position_limit_pct?: number | null
  entry_trigger?: string | null
  invalidation?: string | null
  exit_plan?: string | null
  reasons?: string[]
  plan?: PolicyPlanRule
  [key: string]: unknown
}

export interface PolicyReport {
  trade_date: string
  requested_date?: string
  resolved_from_non_trading_day?: boolean
  plan_date?: string | null
  generated_at?: string | null
  candidate_count: number
  market_regime?: string | null
  market_score?: number | null
  strategy_version?: string | null
  market?: Record<string, unknown>
  candidates: PolicyCandidate[]
  data_source?: string | null
  data_sessions?: string[]
  rejected_count?: number
  disclaimer?: string
}

export interface PolicyReportJob {
  job_id: string
  status: 'queued' | 'running' | 'succeeded' | 'failed'
  result?: Record<string, unknown>
  error_message?: string | null
}

export interface PolicyDecision {
  state: string
  label: string
  reason: string
  irreversible?: boolean
  updated_at?: string | null
}

export interface PolicyCandle {
  time?: string | null
  price?: number | null
  open?: number | null
  high?: number | null
  low?: number | null
  close?: number | null
  volume?: number | null
}

export interface PolicyMonitorStock {
  symbol: string
  name: string
  industry?: string | null
  rank?: number | null
  score?: number | null
  price?: number | null
  change_pct?: number | null
  open?: number | null
  high?: number | null
  low?: number | null
  previous_close?: number | null
  bid1?: number | null
  ask1?: number | null
  source_time?: string | null
  collected_at?: string | null
  feature?: {
    minute_volume_ratio?: number | null
    sector_rise_ratio?: number | null
    sector_sample_size?: number | null
    data_state?: string | null
    computed_at?: string | null
  }
  candles?: PolicyCandle[]
  plan?: PolicyPlanRule
  decision: PolicyDecision
}

export interface PolicyMonitor {
  trade_date: string
  requested_date?: string
  reference_date?: string | null
  plan_id?: string | null
  strategy_version?: string | null
  server_time: string
  phase: string
  phase_label: string
  data_status: {
    state: 'fresh' | 'delayed' | 'stale' | 'unavailable' | 'historical'
    max_quote_age_seconds?: number | null
    reason?: string | null
  }
  stocks: PolicyMonitorStock[]
}
