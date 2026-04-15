import { describe, expect, it } from 'vitest'

import { uiCopy } from '../../../src/lib/copy.js'
import { distillSessionTitle, normalizeSessionTitle } from '../../../src/lib/sessionTitle.js'

describe('sessionTitle helpers', () => {
  it('normalizes placeholder titles through centralized copy', () => {
    expect(normalizeSessionTitle('New session')).toBe(uiCopy.sessionTitles.defaultTitle)
    expect(normalizeSessionTitle('未命名会话')).toBe(uiCopy.sessionTitles.defaultTitle)
  })

  it('uses the centralized default title when no title can be distilled', () => {
    expect(distillSessionTitle('')).toBe(uiCopy.sessionTitles.defaultTitle)
  })
})
