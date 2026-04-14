import { describe, expect, it } from 'vitest'

import { BOTTOM_SCROLL_THRESHOLD, isNearBottom } from '../../../src/lib/scroll.js'

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
})
