// @vitest-environment jsdom
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { LiveTrading } from './LiveTrading'
import { NextPlan } from './NextPlan'

const calls = vi.hoisted(() => ({
  policyReports: vi.fn(),
  policyReport: vi.fn(),
  policyMonitor: vi.fn(),
}))

vi.mock('@/lib/api', () => ({
  api: {
    policyReports: calls.policyReports,
    policyReport: calls.policyReport,
    policyMonitor: calls.policyMonitor,
    policyRefreshReport: vi.fn(),
    policyReportJob: vi.fn(),
  },
}))

vi.mock('@/lib/accountSession', () => ({
  hasPermission: () => true,
}))

let host: HTMLDivElement
let root: Root
let client: QueryClient

beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true })
  calls.policyReports.mockReset()
  calls.policyReport.mockReset()
  calls.policyMonitor.mockReset()
  host = document.createElement('div')
  document.body.append(host)
  root = createRoot(host)
  client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: Infinity },
      mutations: { retry: false },
    },
  })
})

afterEach(async () => {
  await act(async () => root.unmount())
  client.clear()
  host.remove()
})

async function render(page: React.ReactNode) {
  await act(async () => root.render(
    <QueryClientProvider client={client}>{page}</QueryClientProvider>,
  ))
}

async function settle() {
  for (let i = 0; i < 4; i++) {
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)) })
  }
}

describe('Policy pages default views', () => {
  it('shows the next-plan controls before the report dependency responds', async () => {
    calls.policyReports.mockImplementation(() => new Promise(() => {}))
    await render(<NextPlan />)

    expect(host.textContent).toContain('次日计划')
    expect(host.textContent).toContain('计划交易日')
    expect(host.textContent).toContain('正在同步次日计划')
  })

  it('keeps the next-plan view usable when report synchronization fails', async () => {
    calls.policyReports.mockRejectedValue(new Error('upstream unavailable'))
    await render(<NextPlan />)
    await settle()

    expect(host.textContent).toContain('次日计划')
    expect(host.textContent).toContain('计划数据暂未同步')
    expect(host.textContent).toContain('暂无次日计划')
    expect(host.textContent).not.toContain('数据加载失败')
  })

  it('shows the live-trading controls before the monitor dependency responds', async () => {
    calls.policyMonitor.mockImplementation(() => new Promise(() => {}))
    await render(<LiveTrading />)

    expect(host.textContent).toContain('当日实盘')
    expect(host.textContent).toContain('交易日待同步')
    expect(host.textContent).toContain('正在同步实盘状态')
  })

  it('keeps the live-trading view usable when monitor synchronization fails', async () => {
    calls.policyMonitor.mockRejectedValue(new Error('upstream unavailable'))
    await render(<LiveTrading />)
    await settle()

    expect(host.textContent).toContain('当日实盘')
    expect(host.textContent).toContain('实盘数据暂未同步')
    expect(host.textContent).toContain('当前没有监控候选')
    expect(host.textContent).not.toContain('数据加载失败')
  })
})
