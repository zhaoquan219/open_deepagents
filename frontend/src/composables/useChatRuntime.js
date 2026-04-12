import { createApiClient } from '../api/client.js'
import { createRunStore } from '../store/runStore.js'
import { createSessionStore } from '../store/sessionStore.js'

export function createChatRuntime() {
  const apiClient = createApiClient()
  return {
    apiClient,
    runStore: createRunStore(),
    sessionStore: createSessionStore(apiClient),
  }
}
