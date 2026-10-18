// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/components/Toast', () => ({
  toast: vi.fn(),
}))

import { toast } from '@/components/Toast'
import { api } from './api'


function ok(body: unknown): Response {
  return {
    ok: true,
    status: 200,
    statusText: 'OK',
    headers: new Headers(),
    json: async () => body,
    text: async () => JSON.stringify(body),
  } as Response
}


afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})


describe('Policy bridge API', () => {
  it('loads reports and the active live monitor through the same-origin bridge', async () => {
    const fetchMock = vi.fn(async () => ok({ items: [], stocks: [] }))
    vi.stubGlobal('fetch', fetchMock)

    await api.policyReports(25)
    await api.policyReport('2026-10-17')
    await api.policyMonitor()

    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      '/api/policy/reports?limit=25',
      expect.objectContaining({ headers: expect.any(Object) }),
    )
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      '/api/policy/reports/2026-10-17',
      expect.objectContaining({ headers: expect.any(Object) }),
    )
    expect(fetchMock).toHaveBeenNthCalledWith(
      3,
      '/api/policy/monitor',
      expect.objectContaining({ headers: expect.any(Object) }),
    )
  })

  it('submits report refresh as an authenticated same-origin write', async () => {
    const fetchMock = vi.fn(async () => ok({ job_id: 'job-1', status: 'queued' }))
    vi.stubGlobal('fetch', fetchMock)

    await api.policyRefreshReport('2026-10-17')

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/policy/reports/2026-10-17/refresh',
      expect.objectContaining({ method: 'POST' }),
    )
  })

  it('leaves read failures to the page instead of showing dependency error toasts', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({
      ok: false,
      status: 502,
      statusText: 'Bad Gateway',
      headers: new Headers(),
      text: async () => JSON.stringify({ detail: 'Policy 服务鉴权配置无效' }),
    } as Response)))

    await expect(api.policyReports()).rejects.toThrow('Policy 服务鉴权配置无效')
    await expect(api.policyReport('2026-10-17')).rejects.toThrow('Policy 服务鉴权配置无效')

    expect(toast).not.toHaveBeenCalled()
  })
})
