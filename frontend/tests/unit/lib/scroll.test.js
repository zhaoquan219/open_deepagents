import { describe, expect, it } from 'vitest'

import {
  BOTTOM_SCROLL_THRESHOLD,
  isNearBottom,
  scrollMetrics,
  shouldForceFollowLatest,
} from '../../../src/lib/scroll.js'

describe('scroll helpers', () => {
  it('treats positions within the follow threshold as pinned to the bottom', () => {
    expect(
      isNearBottom({
        scrollTop: 176,
        clientHeight: 200,
        scrollHeight: 400,
      }),
    ).toBe(true)
  })

  it('stops auto-follow when the user scrolls clearly away from the latest content', () => {
    expect(
      isNearBottom(
        {
          scrollTop: 120,
          clientHeight: 200,
          scrollHeight: 400,
        },
        BOTTOM_SCROLL_THRESHOLD,
      ),
    ).toBe(false)
  })

  it('can read element scroll metrics with an explicit scroll top override', () => {
    expect(
      scrollMetrics(
        {
          scrollTop: 20,
          clientHeight: 100,
          scrollHeight: 500,
        },
        300,
      ),
    ).toEqual({
      scrollTop: 300,
      clientHeight: 100,
      scrollHeight: 500,
    })
  })

  it('forces follow mode when a new user message is appended', () => {
    expect(
      shouldForceFollowLatest(
        [{ id: 'assistant-1', role: 'assistant', content: 'older' }],
        [
          { id: 'assistant-1', role: 'assistant', content: 'older' },
          { id: 'user-2', role: 'user', content: 'new prompt' },
        ],
      ),
    ).toBe(true)
  })

  it('does not force follow mode while a history load is being reconciled', () => {
    expect(
      shouldForceFollowLatest(
        [],
        [{ id: 'user-1', role: 'user', content: 'historical prompt' }],
        { suppressUserAppend: true },
      ),
    ).toBe(false)
  })

  it('does not force follow mode for assistant streaming updates', () => {
    expect(
      shouldForceFollowLatest(
        [{ id: 'stream:run-1', role: 'assistant', content: 'hello', streaming: true }],
        [{ id: 'stream:run-1', role: 'assistant', content: 'hello world', streaming: true }],
      ),
    ).toBe(false)
  })
})
