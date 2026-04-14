export const BOTTOM_SCROLL_THRESHOLD = 24

export function isNearBottom(metrics, threshold = BOTTOM_SCROLL_THRESHOLD) {
  const scrollTop = Number(metrics?.scrollTop || 0)
  const clientHeight = Number(metrics?.clientHeight || 0)
  const scrollHeight = Number(metrics?.scrollHeight || 0)

  if (scrollHeight <= 0) {
    return true
  }

  return scrollHeight - (scrollTop + clientHeight) <= threshold
}
