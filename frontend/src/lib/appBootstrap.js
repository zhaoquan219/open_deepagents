export async function initializeAuthenticatedApp({
  apiClient,
  loadRuntimeOptions,
  sessionStore,
  markAuthenticated,
  markUnauthenticated,
}) {
  try {
    await apiClient.getAdminProfile();
    markAuthenticated();
    await loadRuntimeOptions();
    await sessionStore.loadSessions();
    if (sessionStore.state.currentSessionId) {
      void sessionStore.selectSession(sessionStore.state.currentSessionId);
    }
    return true;
  } catch {
    apiClient.logout();
    markUnauthenticated();
    return false;
  }
}

export function isForeignSessionStreamEnvelope(envelope, sessionId) {
  const expectedSessionId = String(sessionId || "");
  const actualSessionId = String(envelope?.sessionId || "");
  return Boolean(expectedSessionId && actualSessionId && actualSessionId !== expectedSessionId);
}
