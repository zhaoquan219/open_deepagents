import { computed, defineComponent, onBeforeUnmount, onMounted, ref } from 'vue'
import ChatWorkspace from './components/ChatWorkspace.js'
import ProgressTimeline from './components/ProgressTimeline.js'
import SessionSidebar from './components/SessionSidebar.js'
import { createApiClient } from './api/client.js'
import { normalizeStreamEnvelope } from './lib/sseContract.js'
import { createRunStore } from './store/runStore.js'
import { createSessionStore } from './store/sessionStore.js'

function logRuntime(scope, detail, payload, level = 'debug') {
  if (level === 'debug' && !import.meta.env.DEV) {
    return
  }

  const logger = console[level] || console.log
  logger(`[deepagents-ui] ${scope}: ${detail}`, payload ?? '')
}

export default defineComponent({
  name: 'AppShell',
  components: {
    ChatWorkspace,
    ProgressTimeline,
    SessionSidebar,
  },
  template: `
    <div class="app-shell">
      <header class="topbar">
        <div class="brand-block">
          <p class="eyebrow">智能工作台</p>
          <h1>企业级智能助理</h1>
          <p class="topbar-copy">面向用户的对话式工作界面，专注提问、回答与历史沉淀。</p>
        </div>
        <div class="topbar-meta">
          <span class="status-pill" :data-status="runStatus">{{ runStatusLabel }}</span>
        </div>
      </header>

      <main v-if="isAuthenticated" class="layout-grid">
        <aside class="sidebar-shell">
          <session-sidebar
            :sessions="sessions"
            :current-session-id="currentSessionId"
            :loading="loadingSessions"
            :error="sessionError"
            :deleting-session-id="deletingSessionId"
            @new-session="handleCreateSession"
            @refresh="handleRefreshSessions"
            @select-session="handleSelectSession"
            @delete-session="handleDeleteSession"
          />
        </aside>

        <section class="workspace-shell">
          <chat-workspace
            :current-session="currentSession"
            :messages="currentMessages"
            :pending-uploads="pendingUploads"
            :loading="loadingMessages"
            :uploading="uploading"
            :submitting="submitting"
            :active-run="activeRun"
            :run-status="runStatus"
            :run-status-label="runStatusLabel"
            :error="combinedError"
            @submit="handleSubmit"
            @upload="handleUpload"
          />

          <aside class="timeline-shell">
            <progress-timeline
              :active-run="activeRun"
              :connection-state="connectionState"
              :diagnostics="runtimeDiagnostics"
              :runtime-error="runError"
            />
          </aside>
        </section>
      </main>

      <main v-else class="auth-layout">
        <section class="auth-panel">
          <div class="auth-card">
            <p class="eyebrow">安全登录</p>
            <h2>欢迎进入智能助理工作台</h2>
            <p class="auth-copy">请输入管理员账号信息，进入会话与问答界面。</p>

            <label class="composer-label" for="admin-username">用户名</label>
            <input id="admin-username" v-model="authUsername" class="auth-input" type="text" autocomplete="username" />

            <label class="composer-label" for="admin-password">密码</label>
            <input id="admin-password" v-model="authPassword" class="auth-input" type="password" autocomplete="current-password" />

            <p v-if="authError" class="inline-error">{{ authError }}</p>

            <div class="composer-actions">
              <span class="muted-copy">登录后会自动建立安全会话。</span>
              <button class="primary-button" type="button" :disabled="authLoading" @click="handleLogin">
                {{ authLoading ? '登录中…' : '进入工作台' }}
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
    const deletingSessionId = computed(() => sessionStore.state.deletingSessionId)
    const sessionError = computed(() => sessionStore.state.error)
    const combinedError = computed(() => runStore.state.error || sessionStore.state.error || sessionStore.state.uploadError)
    const activeRun = computed(() => runStore.state.activeRun)
    const connectionState = computed(() => runStore.state.activeRun?.connectionState || runStore.state.connectionState)
    const runtimeDiagnostics = computed(() => runStore.state.diagnostics)
    const runError = computed(() => runStore.state.error)
    const runStatus = computed(() => runStore.state.activeRun?.status || 'idle')
    const runStatusLabel = computed(() => {
      const status = runStatus.value
      if (status === 'idle') {
        return '待命'
      }
      if (status === 'queued') {
        return '排队中'
      }
      if (status === 'running') {
        return '处理中'
      }
      if (status === 'completed') {
        return '已完成'
      }
      if (status === 'failed') {
        return '失败'
      }
      return '处理中'
    })

    function closeStream({ markDisconnected = false, detail = '' } = {}) {
      const runId = runStore.state.activeRun?.runId || ''
      if (activeStream.value) {
        activeStream.value.close()
        activeStream.value = null
      }
      if (markDisconnected && runId) {
        runStore.markDisconnected(runId, detail || '实时连接已关闭。')
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
        authError.value = error instanceof Error ? error.message : '登录失败，请检查账号信息。'
      } finally {
        authLoading.value = false
      }
    }

    function connectRunStream(runId, sessionId) {
      closeStream()
      logRuntime('sse.connect', '正在连接实时事件流', { runId, sessionId })
      activeStream.value = apiClient.openRunStream(runId, {
        lastEventId: runStore.getLastEventId(runId),
        onOpen() {
          logRuntime('sse.open', '实时事件流已连接', { runId, sessionId })
          runStore.markConnected(runId)
        },
        onEvent(payload) {
          const envelope = normalizeStreamEnvelope(payload)
          if (!envelope) {
            logRuntime('sse.drop', '收到无法识别的 SSE 事件', payload, 'warn')
            return
          }

          logRuntime('sse.event', `${envelope.type}`, envelope)

          const accepted = runStore.consume(envelope)
          if (!accepted) {
            return
          }

          if (envelope.sessionId && envelope.sessionId !== String(sessionId)) {
            return
          }

          sessionStore.consumeRunEvent(envelope)

          if (
            envelope.type === 'message.final' ||
            envelope.type === 'error' ||
            (envelope.type === 'status' && ['completed', 'failed'].includes(envelope.status))
          ) {
            closeStream({
              markDisconnected: true,
              detail:
                envelope.type === 'error'
                  ? '运行异常，实时连接已终止。'
                  : '本轮输出已结束，实时连接已关闭。',
            })
          }
        },
        onError(error) {
          if (runStore.state.activeRun?.status === 'completed') {
            logRuntime('sse.closed', '实时事件流在完成后关闭', { runId, sessionId })
            closeStream({
              markDisconnected: true,
              detail: '最终回复已写入，会话流已正常结束。',
            })
            return
          }
          logRuntime('sse.error', error.message, { runId, sessionId }, 'error')
          runStore.markErrored(runId, error.message)
          sessionStore.addSystemNotice(String(sessionId), `实时连接已中断：${error.message}`)
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

    async function handleDeleteSession(sessionId) {
      const session = sessions.value.find((item) => item.id === sessionId)
      const title = session?.title || '当前会话'
      const confirmed = window.confirm(`确定要删除“${title}”吗？此操作无法撤销。`)
      if (!confirmed) {
        return
      }

      if (currentSessionId.value === sessionId) {
        closeStream()
        runStore.clear()
      }
      await sessionStore.deleteSession(sessionId)
    }

    async function handleUpload(files) {
      const session = await ensureSession()
      const result = await sessionStore.uploadFiles(String(session.id), files)
      if (!result?.ok) {
        const message = result?.error?.message || '上传附件失败。'
        logRuntime('upload.error', message, { sessionId: session.id, files }, 'error')
        runStore.recordClientIssue({
          sessionId: String(session.id),
          label: '附件上传失败',
          detail: message,
        })
        return
      }

      logRuntime('upload.success', '附件上传完成', {
        sessionId: session.id,
        count: result.records.length,
      })
      runStore.recordClientNotice({
        sessionId: String(session.id),
        label: '附件上传完成',
        detail: `已上传 ${result.records.length} 个附件，将附加到下一次发送。`,
        status: 'completed',
      })
    }

    async function handleSubmit({ prompt }) {
      const text = String(prompt || '').trim()
      if (!text) {
        return
      }

      const session = await ensureSession()
      const sessionId = String(session.id)
      sessionStore.addOptimisticUserMessage(sessionId, text)
      sessionStore.setSubmitting(true)
      logRuntime('run.start', '正在创建运行', {
        sessionId,
        attachmentCount: sessionStore.getPendingUploads(sessionId).length,
      })

      try {
        const run = await apiClient.startRun({
          sessionId,
          prompt: text,
          attachments: sessionStore.getPendingUploads(sessionId),
        })

        sessionStore.markUploadsSubmitted(sessionId)
        runStore.beginRun({ runId: run.runId, sessionId })
        runStore.recordClientNotice({
          sessionId,
          runId: run.runId,
          label: '运行创建成功',
          detail: '后端已接受请求，正在建立实时连接。',
          status: 'completed',
        })
        connectRunStream(run.runId, sessionId)
      } catch (error) {
        const message = error instanceof Error ? error.message : '发送失败，请稍后再试。'
        logRuntime('run.start.error', message, { sessionId }, 'error')
        runStore.markErrored('pending', message)
        runStore.recordClientIssue({
          sessionId,
          label: '运行启动失败',
          detail: message,
        })
        sessionStore.addSystemNotice(sessionId, message)
      } finally {
        sessionStore.setSubmitting(false)
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
      connectionState,
      currentMessages,
      currentSession,
      currentSessionId,
      deletingSessionId,
      handleCreateSession,
      handleDeleteSession,
      handleLogin,
      handleRefreshSessions,
      handleSelectSession,
      handleSubmit,
      handleUpload,
      isAuthenticated,
      loadingMessages,
      loadingSessions,
      pendingUploads,
      runError,
      runtimeDiagnostics,
      runStatus,
      runStatusLabel,
      sessionError,
      sessions,
      submitting,
      uploading,
    }
  },
})
