import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { setLocale, statusText, uiCopy } from '../../../src/lib/copy.js'

afterEach(() => {
  setLocale('zh')
  vi.unstubAllGlobals()
})

beforeEach(() => {
  setLocale('zh')
})

describe('copy locale switching', () => {
  it('switches the shared copy tree between chinese and english', () => {
    expect(uiCopy.common.completed).toBe('已完成')

    setLocale('en')
    expect(uiCopy.common.completed).toBe('Completed')
    expect(uiCopy.sidebar.title).toBe('History')
    expect(statusText('queued')).toBe('Queued')

    setLocale('zh')
    expect(uiCopy.common.completed).toBe('已完成')
    expect(uiCopy.sidebar.title).toBe('历史会话')
  })

  it('persists the selected locale to localStorage', () => {
    const storage = {
      setItem: vi.fn(),
    }

    vi.stubGlobal('window', {
      localStorage: storage,
    })

    setLocale('en')

    expect(storage.setItem).toHaveBeenCalledWith('deepagents.ui.locale', 'en')
  })
})
