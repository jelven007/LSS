// @vitest-environment jsdom
import { beforeEach, expect, it, vi } from 'vitest'
import type { AuthStatus } from './api'

vi.mock('@/components/Toast', () => ({ toast: vi.fn() }))

beforeEach(() => {
  vi.resetModules()
  vi.unstubAllGlobals()
  window.history.replaceState(null, '', '/login')
})

it('uses the account marker after loading administrator platform settings', async () => {
  const { confirmAccount } = await import('./accountSession')
  confirmAccount({
    authenticated: true, auth_required: true, multitenancy_enabled: true,
    user: { id: 'admin', role: 'admin', permissions: ['settings:manage'], auth_version: 1 },
  } as AuthStatus)
  const { api } = await import('./api')
  const fetchMock = vi.fn(async (path: string) => new Response(JSON.stringify(
    path === '/api/settings'
      ? { onboarding_completed: false, ai_model: 'configured-model' }
      : { onboarding_completed: true, ok: true },
  )))
  vi.stubGlobal('fetch', fetchMock)
  expect(await api.settings()).toMatchObject({
    onboarding_completed: true, ai_model: 'configured-model',
  })
  await api.completeOnboarding()
  expect(fetchMock).toHaveBeenLastCalledWith(
    '/api/business/onboarding/complete', expect.objectContaining({ method: 'POST' }),
  )
})

it('keeps standalone onboarding on the local settings endpoint', async () => {
  const { api } = await import('./api')
  const fetchMock = vi.fn(async () => new Response(JSON.stringify({ onboarding_completed: true })))
  vi.stubGlobal('fetch', fetchMock)
  await api.settings()
  await api.completeOnboarding()
  expect(fetchMock).toHaveBeenNthCalledWith(1, '/api/settings', expect.any(Object))
  expect(fetchMock).toHaveBeenNthCalledWith(
    2, '/api/settings/onboarding/complete', expect.objectContaining({ method: 'POST' }),
  )
})
