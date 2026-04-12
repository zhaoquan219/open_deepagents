import { computed, defineComponent, onBeforeUnmount, onMounted, ref } from 'vue'
import ChatWorkspace from './components/ChatWorkspace.js'
import ProgressTimeline from './components/ProgressTimeline.js'
import SessionSidebar from './components/SessionSidebar.js'
import { createApiClient } from './api/client.js'
import { STREAM_SCHEMA_VERSION, normalizeStreamEnvelope } from './lib/sseContract.js'
import { createRunStore } from './store/runStore.js'
import { createSessionStore } from './store/sessionStore.js'

export default defineComponent({
  name: 'AppShell',
  components: {
    ChatWorkspace,
    ProgressTimeline,
    SessionSidebar,
  },
  template: `
    <div class="app-shell">
      <header class="topbar panel">
        <div>
          <p class="eyebrow">DeepAgents agent platform</p>
          <h1>Operator frontend shell</h1>
        </div>
        <div class="topbar-meta">
          <span class="status-pill" :data-status="runStatus">{{ runStatusLabel }}</span>
          <span class="contract-pill">{{ contractVersion }}</span>
        </div>
      </header>

      <main v-if="isAuthenticated" class="layout-grid">
        <section class="panel sidebar-panel">
          <session-sidebar
            :sessions="sessions"
            :current-session-id="currentSessionId"
            :loading="loadingSessions"
            :error="sessionError"
            @new-session="handleCreateSession"
            @refresh="handleRefreshSessions"
            @select-session="handleSelectSession"
          />
        </section>

        <section class="panel workspace-panel">
          <chat-workspace
            :current-session="currentSession"
            :messages="currentMessages"
            :pending-uploads="pendingUploads"
            :loading="loadingMessages"
            :uploading="uploading"
            :submitting="submitting"
            :run-status="runStatus"
            :error="combinedError"
            @submit="handleSubmit"
            @upload="handleUpload"
          />
        </section>

        <aside class="panel timeline-panel">
          <progress-timeline :active-run="activeRun" />
        </aside>
      </main>

      <main v-else class="auth-layout">
        <section class="panel auth-panel">
          <div class="auth-card">
            <p class="eyebrow">Administrator access</p>
            <h2>Sign in to manage agent sessions</h2>
            <p class="auth-copy">Use the credentials configured in <code>backend/.env</code>.</p>

            <label class="composer-label" for="admin-username">Username</label>
            <input id="admin-username" v-model="authUsername" class="auth-input" type="text" autocomplete="username" />

            <label class="composer-label" for="admin-password">Password</label>
            <input id="admin-password" v-model="authPassword" class="auth-input" type="password" autocomplete="current-password" />

            <p v-if="authError" class="inline-error">{{ authError }}</p>

            <div class="composer-actions">
              <span class="muted-copy">The scaffold stores a bearer token in local storage.</span>
              <button class="primary-button" type="button" :disabled="authLoading" @click="handleLogin">
                {{ authLoading ? 'Signing in…' : 'Sign in' }}
              </button>
            </div>
          </div>
        </section>
      </main>
    </div>
  `,
  setup() {
    const apiClient = createApiClient()
    const sessionStore = createSessionStore(apiClient)
    const runStore = createRunStore()
    const activeStream = ref(null)
    const authUsername = ref('admin')
    const authPassword = ref('')
    const authError = ref('')
    const authLoading = ref(false)
    const isAuthenticated = ref(false)

    const sessions = computed(() => sessionStore.state.sessions)
    const currentSessionId = computed(() => sessionStore.state.currentSessionId)
    const currentSession = computed(() => sessionStore.getCurrentSession())
    const currentMessages = computed(() => sessionStore.getCurrentMessages())
    const pendingUploads = computed(() => sessionStore.getPendingUploads())
    const loadingSessions = computed(() => sessionStore.state.loadingSessions)
    const loadingMessages = computed(() => sessionStore.state.loadingMessages)
    const uploading = computed(() => sessionStore.state.uploading)
    const submitting = computed(() => sessionStore.state.submitting)
    const sessionError = computed(() => sessionStore.state.error)
    const combinedError = computed(() => runStore.state.error || sessionStore.state.error || sessionStore.state.uploadError)
    const activeRun = computed(() => runStore.state.activeRun)
    const runStatus = computed(() => runStore.state.activeRun?.status || 'idle')
    const runStatusLabel = computed(() => {
      const status = runStatus.value
      return status === 'idle' ? 'Idle' : status.replace(/-/g, ' ')
    })

    function closeStream() {
      if (activeStream.value) {
        activeStream.value.close()
        activeStream.value = null
      }
    }

    async function handleLogin() {
      authLoading.value = true
      authError.value = ''
      try {
        await apiClient.login({
          username: authUsername.value.trim(),
          password: authPassword.value,
        })
        await apiClient.getAdminProfile()
        isAuthenticated.value = true
        await sessionStore.loadSessions()
        if (sessionStore.state.currentSessionId) {
          await sessionStore.selectSession(sessionStore.state.currentSessionId)
        }
      } catch (error) {
        apiClient.logout()
        isAuthenticated.value = false
        authError.value = error instanceof Error ? error.message : 'Unable to sign in.'
      } finally {
        authLoading.value = false
      }
    }

    function connectRunStream(runId, sessionId) {
      closeStream()
      activeStream.value = apiClient.openRunStream(runId, {
        lastEventId: runStore.getLastEventId(runId),
        onOpen() {
          runStore.markConnected(runId)
        },
        onEvent(payload) {
          const envelope = normalizeStreamEnvelope(payload)
          if (!envelope) {
            return
          }

          const accepted = runStore.consume(envelope)
          if (!accepted) {
            return
          }

          if (envelope.sessionId && envelope.sessionId !== String(sessionId)) {
            return
          }

          sessionStore.consumeRunEvent(envelope)

          if (
            envelope.type === 'error' ||
            (envelope.type === 'status' && ['completed', 'failed'].includes(envelope.status))
          ) {
            closeStream()
          }
        },
        onError(error) {
          if (runStore.state.activeRun?.status === 'completed') {
            closeStream()
            return
          }
          runStore.markErrored(runId, error.message)
          sessionStore.addSystemNotice(String(sessionId), `Stream disconnected: ${error.message}`)
        },
      })
    }

    async function ensureSession() {
      if (sessionStore.state.currentSessionId) {
        return sessionStore.getCurrentSession()
      }

      return handleCreateSession()
    }

    async function handleRefreshSessions() {
      await sessionStore.loadSessions({ preserveSelection: true })
    }

    async function handleCreateSession() {
      closeStream()
      runStore.clear()
      const session = await sessionStore.createSession()
      await sessionStore.selectSession(session.id)
      return session
    }

    async function handleSelectSession(sessionId) {
      closeStream()
      runStore.clear()
      await sessionStore.selectSession(sessionId)
    }

    async function handleUpload(files) {
      const session = await ensureSession()
      await sessionStore.uploadFiles(String(session.id), files)
    }

    async function handleSubmit({ prompt }) {
      const text = String(prompt || '').trim()
      if (!text) {
        return
      }

      const session = await ensureSession()
      const sessionId = String(session.id)
      sessionStore.addOptimisticUserMessage(sessionId, text)

      try {
        const run = await apiClient.startRun({
          sessionId,
          prompt: text,
          attachments: sessionStore.getPendingUploads(sessionId),
        })

        sessionStore.markUploadsSubmitted(sessionId)
        runStore.beginRun({ runId: run.runId, sessionId })
        connectRunStream(run.runId, sessionId)
      } catch (error) {
        const message = error instanceof Error ? error.message : 'Unable to start run.'
        runStore.markErrored('pending', message)
        sessionStore.addSystemNotice(sessionId, message)
      }
    }

    onMounted(async () => {
      try {
        await apiClient.getAdminProfile()
        isAuthenticated.value = true
        await sessionStore.loadSessions()
        if (sessionStore.state.currentSessionId) {
          await sessionStore.selectSession(sessionStore.state.currentSessionId)
        }
      } catch {
        apiClient.logout()
        isAuthenticated.value = false
      }
    })

    onBeforeUnmount(() => {
      closeStream()
    })

    return {
      activeRun,
      authError,
      authLoading,
      authPassword,
      authUsername,
      combinedError,
      contractVersion: STREAM_SCHEMA_VERSION,
      currentMessages,
      currentSession,
      currentSessionId,
      handleCreateSession,
      handleLogin,
      handleRefreshSessions,
      handleSelectSession,
      handleSubmit,
      handleUpload,
      isAuthenticated,
      loadingMessages,
      loadingSessions,
      pendingUploads,
      runStatus,
      runStatusLabel,
      sessionError,
      sessions,
      submitting,
      uploading,
    }
  },
})
