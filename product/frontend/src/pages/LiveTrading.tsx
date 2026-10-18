import { useQuery } from '@tanstack/react-query'
import {
  Activity,
  Clock3,
  Radio,
  RefreshCw,
  ShieldAlert,
  Signal,
} from 'lucide-react'
import { EmptyState } from '@/components/EmptyState'
import { PageHeader } from '@/components/PageHeader'
import { api } from '@/lib/api'
import type { PolicyCandle, PolicyMonitorStock } from '@/lib/policy'
import { QK } from '@/lib/queryKeys'


function number(value: number | null | undefined, digits = 2): string {
  return value == null || !Number.isFinite(value) ? '—' : value.toFixed(digits)
}


function percent(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return '—'
  return `${value >= 0 ? '+' : ''}${value.toFixed(2)}%`
}


function stamp(value: string | null | undefined): string {
  if (!value) return '—'
  const parsed = new Date(value)
  if (Number.isNaN(parsed.valueOf())) return value
  return new Intl.DateTimeFormat('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
    timeZone: 'Asia/Shanghai',
  }).format(parsed)
}


function changeTone(value: number | null | undefined): string {
  if (value == null || value === 0) return 'text-foreground'
  return value > 0 ? 'text-bull' : 'text-bear'
}


function statusTone(state: string, irreversible = false): string {
  if (irreversible) return 'border-danger/30 text-danger'
  if (['triggered', 'sealed', 'at_limit'].includes(state)) return 'border-bull/30 text-bull'
  if (['stale', 'unavailable', 'no_quote'].includes(state)) return 'border-warning/30 text-warning'
  return 'border-border text-secondary'
}


function MiniTrend({ candles }: { candles?: PolicyCandle[] }) {
  const points = (candles ?? [])
    .map(item => item.close ?? item.price)
    .filter((value): value is number => value != null && Number.isFinite(value))
  if (points.length < 2) {
    return (
      <div className="grid h-20 place-items-center rounded-btn bg-elevated/50 text-[10px] text-muted">
        暂无分时
      </div>
    )
  }
  const width = 320
  const height = 80
  const min = Math.min(...points)
  const max = Math.max(...points)
  const span = Math.max(max - min, Math.abs(max) * 0.001, 0.01)
  const path = points.map((value, index) => {
    const x = points.length === 1 ? 0 : index / (points.length - 1) * width
    const y = height - ((value - min) / span * (height - 12) + 6)
    return `${index === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`
  }).join(' ')
  const rising = points[points.length - 1] >= points[0]

  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      className={`h-20 w-full rounded-btn bg-elevated/40 ${rising ? 'text-bull' : 'text-bear'}`}
      role="img"
      aria-label="盘中价格走势"
      preserveAspectRatio="none"
    >
      <path d={`M0 ${height / 2} H${width}`} stroke="currentColor" strokeOpacity="0.15" strokeDasharray="4 4" />
      <path d={path} fill="none" stroke="currentColor" strokeWidth="2" vectorEffect="non-scaling-stroke" />
    </svg>
  )
}


function QuoteCell({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-[9px] text-muted">{label}</div>
      <div className="mt-0.5 font-mono text-[11px] font-medium text-foreground">{value}</div>
    </div>
  )
}


function StockCard({ stock }: { stock: PolicyMonitorStock }) {
  const decision = stock.decision
  return (
    <article className="rounded-card border border-border bg-surface p-4 shadow-sm">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <h2 className="truncate text-[16px] font-semibold text-foreground">{stock.name}</h2>
            <span className="font-mono text-xs text-muted">{stock.symbol}</span>
          </div>
          <div className="mt-1 flex flex-wrap items-center gap-1.5 text-[10px] text-muted">
            {stock.industry && <span>{stock.industry}</span>}
            {stock.rank != null && <span>· 排名 {stock.rank}</span>}
            {stock.score != null && <span>· 评分 {number(stock.score, 1)}</span>}
          </div>
        </div>
        <div className="shrink-0 text-right">
          <div className="font-mono text-xl font-semibold text-foreground">{number(stock.price)}</div>
          <div className={`font-mono text-xs font-medium ${changeTone(stock.change_pct)}`}>
            {percent(stock.change_pct)}
          </div>
        </div>
      </div>

      <div className="mt-3">
        <MiniTrend candles={stock.candles} />
      </div>

      <div className="mt-3 grid grid-cols-4 gap-3">
        <QuoteCell label="开盘" value={number(stock.open)} />
        <QuoteCell label="最高" value={number(stock.high)} />
        <QuoteCell label="最低" value={number(stock.low)} />
        <QuoteCell label="昨收" value={number(stock.previous_close)} />
        <QuoteCell label="买一" value={number(stock.bid1)} />
        <QuoteCell label="卖一" value={number(stock.ask1)} />
        <QuoteCell label="分钟量比" value={number(stock.feature?.minute_volume_ratio)} />
        <QuoteCell
          label="板块上涨"
          value={stock.feature?.sector_rise_ratio == null
            ? '—'
            : `${number(stock.feature.sector_rise_ratio * 100, 1)}%`}
        />
      </div>

      <div className="mt-4 border-t border-border pt-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <span className={`rounded-full border px-2 py-1 text-[10px] font-medium ${statusTone(decision.state, decision.irreversible)}`}>
            {decision.label}
          </span>
          <span className="font-mono text-[9px] text-muted">行情 {stamp(stock.source_time)}</span>
        </div>
        <p className="mt-2 text-xs leading-5 text-secondary">{decision.reason}</p>
      </div>
    </article>
  )
}


function dataStatusTone(state: string): string {
  if (state === 'fresh') return 'border-accent/30 text-accent'
  if (state === 'historical') return 'border-border text-secondary'
  if (state === 'delayed') return 'border-warning/30 text-warning'
  return 'border-danger/30 text-danger'
}


export function LiveTrading() {
  const monitor = useQuery({
    queryKey: QK.policyMonitor(),
    queryFn: () => api.policyMonitor(),
    refetchInterval: query => query.state.data ? 1000 : 15_000,
    refetchIntervalInBackground: false,
    placeholderData: previous => previous,
    retry: false,
  })
  const data = monitor.data

  const right = (
    <button
      type="button"
      onClick={() => void monitor.refetch()}
      disabled={monitor.isFetching}
      className="inline-flex h-8 items-center gap-1.5 rounded-btn border border-border bg-surface px-2.5 text-xs text-secondary hover:text-accent disabled:opacity-50"
    >
      <RefreshCw className={`h-3.5 w-3.5 ${monitor.isFetching ? 'animate-spin' : ''}`} />
      同步
    </button>
  )

  return (
    <div className="flex min-h-full flex-col bg-base">
      <PageHeader title="当日实盘" subtitle="盘中条件监控与决策证据" right={right} />
      <main className="space-y-3 p-3 lg:p-4">
        <section className="flex flex-wrap items-center gap-2 rounded-card border border-border bg-surface px-3 py-2.5">
          <span className="inline-flex items-center gap-1.5 text-xs font-medium text-foreground">
            <Radio className="h-3.5 w-3.5 text-accent" />
            {data?.trade_date || '交易日待同步'}
          </span>
          <span className="h-4 w-px bg-border" />
          <span className="inline-flex items-center gap-1.5 text-xs text-secondary">
            <Clock3 className="h-3.5 w-3.5" />
            {data?.phase_label || '等待行情'}
          </span>
          <span className={`rounded-full border px-2 py-0.5 text-[10px] ${data ? dataStatusTone(data.data_status.state) : 'border-border text-secondary'}`}>
            {data
              ? data.data_status.state === 'fresh'
                ? '实时'
                : data.data_status.state === 'historical'
                  ? '历史复盘'
                  : data.data_status.state === 'delayed'
                    ? '行情延迟'
                    : '数据不可用'
              : monitor.isLoading
                ? '同步中'
                : '待同步'}
          </span>
          <span className="ml-auto font-mono text-[10px] text-muted">
            服务时间 {data ? stamp(data.server_time) : '—'}
          </span>
        </section>

        {monitor.isError && (
          <div className="flex items-center gap-2 rounded-btn border border-warning/30 bg-warning/5 px-3 py-2 text-xs text-warning">
            <Signal className="h-3.5 w-3.5" />
            {data ? '实时同步暂时中断，当前保留最后一次有效快照。' : '实盘数据暂未同步，页面会自动重试，也可手动同步。'}
          </div>
        )}

        {data && data.stocks.length > 0 ? (
          <section className="grid gap-3 xl:grid-cols-2 2xl:grid-cols-3">
            {data.stocks.map(stock => <StockCard key={stock.symbol} stock={stock} />)}
          </section>
        ) : (
          <div className="rounded-card border border-border bg-surface">
            <EmptyState
              icon={Activity}
              title={monitor.isLoading && !data ? '正在同步实盘状态' : '当前没有监控候选'}
              hint="次日计划产生候选后会自动进入监控列表。"
            />
          </div>
        )}

        <div className="flex items-start gap-2 px-1 text-[10px] leading-5 text-muted">
          <ShieldAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          <span>页面仅展示条件判断和行情证据，不连接券商、不自动下单；数据过期时停止给出有效确认。</span>
        </div>
      </main>
    </div>
  )
}
