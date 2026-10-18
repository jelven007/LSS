// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'

import {
  APP_MENU_LABELS,
  DEFAULT_NAV_STATE,
  PRIMARY_NAV,
  nextNavState,
} from './Layout'
import { BUILTIN_PAGES } from '@/pages/settings/MenuSettings'


describe('integrated product navigation', () => {
  it('keeps the requested nine menu entries in order', () => {
    expect(APP_MENU_LABELS).toEqual([
      '首页',
      '自选',
      '策略',
      '次日计划',
      '当日实盘',
      '复盘',
      '回测',
      '个股分析',
      '设置',
    ])
    expect(PRIMARY_NAV.map(item => item.to)).toEqual([
      '/',
      '/watchlist',
      '/screener',
      '/next-plan',
      '/live-trading',
      '/review',
      '/backtest',
      '/stock-analysis',
    ])
    expect(BUILTIN_PAGES.map(item => item.label)).toEqual(APP_MENU_LABELS.slice(0, -1))
  })

  it('opens the desktop navigation by default and toggles only between full and rail modes', () => {
    expect(DEFAULT_NAV_STATE).toBe('expanded')
    expect(nextNavState('expanded')).toBe('rail')
    expect(nextNavState('rail')).toBe('expanded')
  })
})
