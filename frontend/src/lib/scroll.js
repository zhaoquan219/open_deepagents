export const BOTTOM_SCROLL_THRESHOLD = 24

export function scrollMetrics(element, scrollTopOverride) {
  return {
    scrollTop: scrollTopOverride ?? Number(element?.scrollTop || 0),
    clientHeight: Number(element?.clientHeight || 0),
    scrollHeight: Number(element?.scrollHeight || 0),
  }
}

export function isNearBottom(metrics, threshold = BOTTOM_SCROLL_THRESHOLD) {
  const scrollTop = Number(metrics?.scrollTop || 0)
  const clientHeight = Number(metrics?.clientHeight || 0)
  const scrollHeight = Number(metrics?.scrollHeight || 0)

  if (scrollHeight <= 0) {
    return true
  }

  return scrollHeight - (scrollTop + clientHeight) <= threshold
}

export function shouldForceFollowLatest(previousMessages, nextMessages, options = {}) {
  if (options.suppressUserAppend) {
    return false
  }

  const previous = Array.isArray(previousMessages) ? previousMessages : []
  const next = Array.isArray(nextMessages) ? nextMessages : []
  if (next.length <= previous.length) {
    return false
  }

  const latest = next.at(-1)
  return latest?.role === 'user'
}
