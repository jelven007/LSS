import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  CalendarClock,
  CheckCircle2,
  Clock3,
  Loader2,
  RefreshCw,
  ShieldAlert,
  Target,
  WalletCards,
} from 'lucide-react'
import { DatePicker } from '@/components/DatePicker'
import { EmptyState } from '@/components/EmptyState'
import { PageHeader } from '@/components/PageHeader'
import { toast } from '@/components/Toast'
import { hasPermission } from '@/lib/accountSession'
import { api } from '@/lib/api'
import type { PolicyCandidate, PolicyReport } from '@/lib/policy'
import { QK } from '@/lib/queryKeys'


function number(value: number | null | undefined, digits = 2): string {
  return value == null || !Number.isFinite(value) ? '—' : value.toFixed(digits)
}


function time(value: string | null | undefined): string {
  if (!value) return '—'
  const parsed = new Date(value)
  if (Number.isNaN(parsed.valueOf())) return value
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
    timeZone: 'Asia/Shanghai',
  }).format(parsed)
}


function PlanCandidate({ candidate }: { candidate: PolicyCandidate }) {
  const plan = candidate.plan ?? {}
  const entryRange = plan.open_min_pct != null && plan.open_max_pct != null
    ? `${number(plan.open_min_pct)}% ～ ${number(plan.open_max_pct)}%`
    : '以计划条件为准'
  const abandonRange = plan.reject_min_pct != null && plan.reject_max_pct != null
    ? `< ${number(plan.reject_min_pct)}% / > ${number(plan.reject_max_pct)}%`
    : '以放弃条件为准'

  return (
    <article className="rounded-card border border-border bg-surface p-4 shadow-sm">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="font-mono text-[11px] text-muted">
              #{String(candidate.rank ?? '—').padStart(2, '0')}
            </span>
            <h2 className="truncate text-[16px] font-semibold text-foreground">
              {candidate.name}
            </h2>
            <span className="font-mono text-xs text-secondary">{candidate.symbol}</span>
          </div>
          <div className="mt-1 flex flex-wrap gap-1.5 text-[10px] text-muted">
            {candidate.industry && <span className="rounded-full border border-border px-2 py-0.5">{candidate.industry}</span>}
            {candidate.strategy && <span className="rounded-full border border-border px-2 py-0.5">{candidate.strategy}</span>}
          </div>
        </div>
        <div className="text-right">
          <div className="font-mono text-lg font-semibold text-foreground">
            {number(candidate.score, 1)}
          </div>
          <div className="text-[10px] text-muted">综合评分</div>
        </div>
      </div>

      <div className="mt-4 grid grid-cols-2 gap-px overflow-hidden rounded-btn border border-border bg-border sm:grid-cols-4">
        <Metric label="昨收参考" value={candidate.latest_price == null ? '—' : `¥ ${number(candidate.latest_price)}`} />
        <Metric label="竞价区间" value={entryRange} />
        <Metric label="放弃区间" value={abandonRange} />
        <Metric label="单票仓位" value={candidate.position_limit_pct == null ? '—' : `${candidate.position_limit_pct}%`} />
      </div>

      <div className="mt-4 grid gap-3 lg:grid-cols-3">
        <PlanText icon={Target} label="入场确认" value={candidate.entry_trigger} />
        <PlanText icon={ShieldAlert} label="放弃条件" value={candidate.invalidation} />
        <PlanText icon={Clock3} label="退出计划" value={candidate.exit_plan} />
      </div>

      {candidate.reasons && candidate.reasons.length > 0 && (
        <div className="mt-4 border-t border-border pt-3">
          <div className="text-[10px] font-medium text-muted">入选依据</div>
          <div className="mt-1.5 flex flex-wrap gap-1.5">
            {candidate.reasons.map(reason => (
              <span key={reason} className="rounded-full bg-elevated px-2 py-1 text-[10px] text-secondary">
                {reason}
              </span>
            ))}
          </div>
        </div>
      )}
    </article>
  )
}


function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="bg-surface px-3 py-2.5">
      <div className="text-[10px] text-muted">{label}</div>
      <div className="mt-1 font-mono text-xs font-medium text-foreground">{value}</div>
    </div>
  )
}


function PlanText({
  icon: Icon,
  label,
  value,
}: {
  icon: typeof Target
  label: string
  value?: string | null
}) {
  return (
    <div className="min-w-0">
      <div className="flex items-center gap-1.5 text-[11px] font-medium text-secondary">
        <Icon className="h-3.5 w-3.5 text-accent" />
        {label}
      </div>
      <p className="mt-1.5 text-xs leading-5 text-foreground/85">
        {value || '暂无结构化条件'}
      </p>
    </div>
  )
}


function ReportSummary({ report }: { report: PolicyReport }) {
  return (
    <section className="grid gap-px overflow-hidden rounded-card border border-border bg-border sm:grid-cols-2 xl:grid-cols-5">
      <Summary icon={CalendarClock} label="计划交易日" value={report.plan_date || '待确认'} />
      <Summary icon={Target} label="市场状态" value={report.market_regime || '未分类'} />
      <Summary icon={WalletCards} label="市场评分" value={number(report.market_score, 1)} />
      <Summary icon={CheckCircle2} label="观察候选" value={`${report.candidate_count} 只`} />
      <Summary icon={Clock3} label="生成时间" value={time(report.generated_at)} />
    </section>
  )
}


function DefaultReportSummary({
  selectedDate,
  loading,
}: {
  selectedDate: string
  loading: boolean
}) {
  return (
    <section className="grid gap-px overflow-hidden rounded-card border border-border bg-border sm:grid-cols-2 xl:grid-cols-5">
      <Summary icon={CalendarClock} label="计划交易日" value={selectedDate || '待选择'} />
      <Summary icon={Target} label="市场状态" value="待同步" />
      <Summary icon={WalletCards} label="市场评分" value="—" />
      <Summary icon={CheckCircle2} label="观察候选" value="0 只" />
      <Summary icon={Clock3} label="生成时间" value={loading ? '同步中' : '待生成'} />
    </section>
  )
}


function Summary({
  icon: Icon,
  label,
  value,
}: {
  icon: typeof CalendarClock
  label: string
  value: string
}) {
  return (
    <div className="flex items-center gap-3 bg-surface px-4 py-3">
      <Icon className="h-4 w-4 shrink-0 text-accent" />
      <div className="min-w-0">
        <div className="text-[10px] text-muted">{label}</div>
        <div className="mt-0.5 truncate text-sm font-medium text-foreground">{value}</div>
      </div>
    </div>
  )
}


export function NextPlan() {
  const queryClient = useQueryClient()
  const canRefresh = hasPermission('data:manage')
  const [selectedDate, setSelectedDate] = useState('')
  const [jobId, setJobId] = useState('')

  const reports = useQuery({
    queryKey: QK.policyReports,
    queryFn: () => api.policyReports(100),
    staleTime: 30_000,
    retry: false,
  })

  useEffect(() => {
    if (!selectedDate && reports.data?.items[0]?.trade_date) {
      setSelectedDate(reports.data.items[0].trade_date)
    }
  }, [reports.data?.items, selectedDate])

  const report = useQuery({
    queryKey: QK.policyReport(selectedDate),
    queryFn: () => api.policyReport(selectedDate),
    enabled: !!selectedDate,
    placeholderData: previous => previous,
  })

  const refresh = useMutation({
    mutationFn: () => api.policyRefreshReport(selectedDate),
    onSuccess: job => {
      setJobId(job.job_id)
      toast('报告刷新任务已提交', 'success')
    },
  })

  const job = useQuery({
    queryKey: QK.policyReportJob(jobId),
    queryFn: () => api.policyReportJob(jobId),
    enabled: !!jobId,
    refetchInterval: query => {
      const status = query.state.data?.status
      return status === 'succeeded' || status === 'failed' ? false : 1500
    },
  })

  useEffect(() => {
    if (job.data?.status === 'succeeded') {
      void queryClient.invalidateQueries({ queryKey: QK.policyReports })
      void queryClient.invalidateQueries({ queryKey: QK.policyReport(selectedDate) })
      toast('次日计划已刷新', 'success')
      setJobId('')
    } else if (job.data?.status === 'failed') {
      toast(job.data.error_message || '次日计划刷新失败', 'error')
      setJobId('')
    }
  }, [job.data, queryClient, selectedDate])

  const right = (
    <div className="flex min-w-0 items-center gap-2">
      <DatePicker
        value={selectedDate}
        onChange={setSelectedDate}
        placeholder="选择交易日"
        className="min-w-0"
        buttonClassName="h-8 bg-surface"
      />
      {canRefresh && (
        <button
          type="button"
          onClick={() => refresh.mutate()}
          disabled={!selectedDate || refresh.isPending || !!jobId}
          className="inline-flex h-8 shrink-0 items-center gap-1.5 rounded-btn border border-border bg-surface px-2.5 text-xs text-secondary hover:text-accent disabled:cursor-not-allowed disabled:opacity-50"
        >
          {refresh.isPending || jobId
            ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
            : <RefreshCw className="h-3.5 w-3.5" />}
          刷新
        </button>
      )}
    </div>
  )

  return (
    <div className="flex min-h-full flex-col bg-base">
      <PageHeader title="次日计划" subtitle="条件观察与交易准备" right={right} />
      <main className="space-y-3 p-3 lg:p-4">
        {(reports.isError || report.isError) && (
          <div className="flex flex-wrap items-center gap-2 rounded-btn border border-warning/30 bg-warning/5 px-3 py-2 text-xs text-warning">
            <ShieldAlert className="h-3.5 w-3.5 shrink-0" />
            <span className="min-w-0 flex-1">计划数据暂未同步，当前仍可选择日期或重新加载。</span>
            <button
              type="button"
              onClick={() => {
                if (selectedDate) void report.refetch()
                else void reports.refetch()
              }}
              disabled={reports.isFetching || report.isFetching}
              className="inline-flex items-center gap-1 rounded-btn border border-warning/30 px-2 py-1 text-[11px] transition-colors hover:bg-warning/10 disabled:opacity-50"
            >
              <RefreshCw className={`h-3 w-3 ${(reports.isFetching || report.isFetching) ? 'animate-spin' : ''}`} />
              重新同步
            </button>
          </div>
        )}
        {report.data && (
          <>
            <ReportSummary report={report.data} />
            {report.data.resolved_from_non_trading_day && (
              <div className="rounded-btn border border-warning/30 bg-warning/5 px-3 py-2 text-xs text-warning">
                所选日期不是交易日，当前展示最近一个有效交易日的计划。
              </div>
            )}
            {report.data.candidates.length > 0 ? (
              <section className="grid gap-3 2xl:grid-cols-2">
                {report.data.candidates.map(candidate => (
                  <PlanCandidate key={candidate.symbol} candidate={candidate} />
                ))}
              </section>
            ) : (
              <div className="rounded-card border border-border bg-surface">
                <EmptyState icon={Target} title="本期空仓观察" hint="当前策略没有产生合格候选。" />
              </div>
            )}
            {report.data.disclaimer && (
              <p className="px-1 text-[10px] leading-5 text-muted">{report.data.disclaimer}</p>
            )}
          </>
        )}
        {!report.data && (
          <>
            <DefaultReportSummary selectedDate={selectedDate} loading={reports.isLoading || report.isLoading} />
            <div className="rounded-card border border-border bg-surface">
              <EmptyState
                icon={CalendarClock}
                title={reports.isLoading || report.isLoading ? '正在同步次日计划' : '暂无次日计划'}
                hint={selectedDate ? '该交易日尚未生成计划。' : '选择交易日查看计划，收盘报告生成后会自动显示。'}
              />
            </div>
          </>
        )}
      </main>
    </div>
  )
}
