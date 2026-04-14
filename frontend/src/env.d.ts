declare module '*.css'
declare module '*.vue'

interface Window {
  __deepagentsDebug?: {
    sessionStore: unknown
    runStore: unknown
  }
}
