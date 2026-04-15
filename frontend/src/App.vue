<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { ElMessageBox } from 'element-plus'

import ChatWorkspace from './components/ChatWorkspace.vue'
import ProgressTimeline from './components/ProgressTimeline.vue'
import SessionSidebar from './components/SessionSidebar.vue'
import { createApiClient } from './api/client.js'
import { uiCopy } from './lib/copy.js'
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

const apiClient = createApiClient()
const sessionStore = createSessionStore(apiClient)
const runStore = createRunStore()
if (import.meta.env.DEV && typeof window !== 'undefined') {
  window.__deepagentsDebug = {
    sessionStore,
    runStore,
  }
}
const activeStream = ref(null)
const authUsername = ref('admin')
const authPassword = ref('')
const authError = ref('')
const authLoading = ref(false)
const isAuthenticated = ref(false)
const isWideLayout = ref(true)
const stoppingRunId = ref('')
const timelinePanelOpen = ref(true)
let viewportMediaQuery = null

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
const combinedError = computed(
  () => runStore.state.error || sessionStore.state.error || sessionStore.state.uploadError,
)
const activeRun = computed(() => runStore.state.activeRun)
const connectionState = computed(
  () => runStore.state.activeRun?.connectionState || runStore.state.connectionState,
)
const runtimeDiagnostics = computed(() => runStore.state.diagnostics)
const runError = computed(() => runStore.state.error)
const runStatus = computed(() => runStore.state.activeRun?.status || 'idle')
const canStopRun = computed(
  () => ['queued', 'running'].includes(runStatus.value) && Boolean(activeRun.value?.runId),
)
const stoppingRun = computed(
  () => Boolean(stoppingRunId.value) && stoppingRunId.value === String(activeRun.value?.runId || ''),
)
const sessionCount = computed(() => sessions.value.length)
const currentMessageCount = computed(() => currentMessages.value.length)
const pendingUploadCount = computed(() => pendingUploads.value.length)
const topbarStatusCopy = computed(() => {
  if (runStatus.value === 'running') {
    return uiCopy.app.topbarStatus.running
  }
  if (runStatus.value === 'completed') {
    return uiCopy.app.topbarStatus.completed
  }
  if (runStatus.value === 'cancelled') {
    return uiCopy.app.topbarStatus.cancelled
  }
  if (runStatus.value === 'failed') {
    return uiCopy.app.topbarStatus.failed
  }
  return uiCopy.app.topbarStatus.idle
})
const runStatusLabel = computed(() => {
  const status = runStatus.value
  if (status === 'idle') return uiCopy.common.idle
  if (status === 'queued') return uiCopy.common.queued
  if (status === 'running') return uiCopy.common.running
  if (status === 'completed') return uiCopy.common.completed
  if (status === 'cancelling') return uiCopy.common.cancelling
  if (status === 'cancelled') return uiCopy.common.cancelled
  if (status === 'failed') return uiCopy.common.failed
  return uiCopy.common.running
})
const showTimelinePanel = computed(() => isWideLayout.value || timelinePanelOpen.value)
const timelineToggleLabel = computed(() =>
  showTimelinePanel.value ? uiCopy.app.timelineToggle.close : uiCopy.app.timelineToggle.open,
)

function applyViewportLayout(matches) {
  isWideLayout.value = matches
  timelinePanelOpen.value = matches
}

function handleViewportChange(event) {
  applyViewportLayout(Boolean(event.matches))
}

function setupViewportTracking() {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') {
    applyViewportLayout(true)
    return
  }

  viewportMediaQuery = window.matchMedia('(min-width: 1320px)')
  applyViewportLayout(viewportMediaQuery.matches)

  if (typeof viewportMediaQuery.addEventListener === 'function') {
    viewportMediaQuery.addEventListener('change', handleViewportChange)
  } else {
    viewportMediaQuery.addListener(handleViewportChange)
  }
}

function teardownViewportTracking() {
  if (!viewportMediaQuery) {
    return
  }

  if (typeof viewportMediaQuery.removeEventListener === 'function') {
    viewportMediaQuery.removeEventListener('change', handleViewportChange)
  } else {
    viewportMediaQuery.removeListener(handleViewportChange)
  }

  viewportMediaQuery = null
}

function toggleTimelinePanel() {
  if (isWideLayout.value) {
    return
  }
  timelinePanelOpen.value = !timelinePanelOpen.value
}

function closeTimelinePanel() {
  if (isWideLayout.value) {
    return
  }
  timelinePanelOpen.value = false
}

function closeStream({ markDisconnected = false, detail = '' } = {}) {
  const runId = runStore.state.activeRun?.runId || ''
  if (activeStream.value) {
    activeStream.value.close()
    activeStream.value = null
  }
  if (markDisconnected && runId) {
    runStore.markDisconnected(runId, detail || uiCopy.app.stream.disconnected)
  }
}

function syncSessionTranscript(sessionId) {
  if (String(currentSessionId.value || '') !== String(sessionId || '')) {
    return
  }
  void sessionStore.selectSession(String(sessionId))
}

function isTerminalEnvelope(envelope) {
  return (
    envelope.type === 'error' ||
    (envelope.type === 'status' && ['completed', 'failed', 'cancelled'].includes(envelope.status))
  )
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
    authError.value = error instanceof Error ? error.message : uiCopy.app.auth.failure
  } finally {
    authLoading.value = false
  }
}

function connectRunStream(runId, sessionId) {
  closeStream()
  logRuntime('sse.connect', uiCopy.app.logs.sseConnect, { runId, sessionId })
  const terminalDetail = (envelope) =>
    envelope.type === 'error'
      ? uiCopy.app.stream.terminalError
      : envelope.status === 'cancelled'
        ? uiCopy.app.stream.terminalCancelled
      : uiCopy.app.stream.terminalCompleted
  activeStream.value = apiClient.openRunStream(runId, {
    lastEventId: runStore.getLastEventId(runId),
    onOpen() {
      logRuntime('sse.open', uiCopy.app.logs.sseOpen, { runId, sessionId })
      runStore.markConnected(runId)
    },
    onRetry() {
      logRuntime('sse.retry', uiCopy.app.logs.sseRetry, { runId, sessionId }, 'warn')
      runStore.markConnecting(runId, uiCopy.app.stream.retrying)
    },
    onEvent(payload) {
      const envelope = normalizeStreamEnvelope(payload)
      if (!envelope) {
        logRuntime('sse.drop', uiCopy.app.logs.sseDrop, payload, 'warn')
        return
      }

      logRuntime('sse.event', `${envelope.type}`, envelope)

      const accepted = runStore.consume(envelope)
      if (!accepted) {
        if (isTerminalEnvelope(envelope)) {
          syncSessionTranscript(sessionId)
          if (envelope.type === 'status' && envelope.status === 'completed') {
            runStore.markCompleted(runId, uiCopy.app.stream.replayCompleted)
          } else if (envelope.type === 'status' && envelope.status === 'cancelled') {
            runStore.markCancelled(runId, uiCopy.app.stream.replayCancelled)
          }
          closeStream({
            markDisconnected: true,
            detail: terminalDetail(envelope),
          })
        }
        return
      }

      if (envelope.sessionId && envelope.sessionId !== String(sessionId)) {
        return
      }

      sessionStore.consumeRunEvent(envelope)
      if (envelope.type === 'message.final' && !String(envelope.message?.content || '').trim()) {
        syncSessionTranscript(sessionId)
      }

      if (isTerminalEnvelope(envelope)) {
        if (envelope.type === 'status' && envelope.status === 'completed') {
          syncSessionTranscript(sessionId)
        }
        closeStream({
          markDisconnected: true,
          detail: terminalDetail(envelope),
        })
      }
    },
    onError(error) {
      if (['completed', 'cancelled'].includes(runStore.state.activeRun?.status || '')) {
        logRuntime('sse.closed', uiCopy.app.logs.sseClosed, { runId, sessionId })
        closeStream({
          markDisconnected: true,
          detail:
            runStore.state.activeRun?.status === 'cancelled'
              ? uiCopy.app.stream.cancelledClosed
              : uiCopy.app.stream.completedClosed,
        })
        return
      }
      logRuntime('sse.error', error.message, { runId, sessionId }, 'error')
      runStore.markErrored(runId, error.message)
      sessionStore.addSystemNotice(String(sessionId), uiCopy.app.stream.recoveryFailure(error.message))
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
  const title = session?.title || uiCopy.app.deleteSession.fallbackTitle

  try {
    await ElMessageBox.confirm(uiCopy.app.deleteSession.message(title), uiCopy.app.deleteSession.title, {
      confirmButtonText: uiCopy.app.deleteSession.confirm,
      cancelButtonText: uiCopy.app.deleteSession.cancel,
      type: 'warning',
    })
  } catch {
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
    const message = result?.error?.message || uiCopy.app.logs.uploadError
    logRuntime('upload.error', message, { sessionId: session.id, files }, 'error')
    runStore.recordClientIssue({
      sessionId: String(session.id),
      label: uiCopy.app.notices.uploadFailed,
      detail: message,
    })
    return
  }

  logRuntime('upload.success', uiCopy.app.logs.uploadSuccess, {
    sessionId: session.id,
    count: result.records.length,
  })
  runStore.recordClientNotice({
    sessionId: String(session.id),
    label: uiCopy.app.notices.uploadCompleted,
    detail: uiCopy.app.notices.uploadCompletedDetail(result.records.length),
    status: 'completed',
  })
}

async function handleSubmit({ prompt }) {
  if (['queued', 'running'].includes(runStatus.value)) {
    return
  }
  const text = String(prompt || '').trim()
  if (!text) {
    return
  }

  const session = await ensureSession()
  const sessionId = String(session.id)
  sessionStore.addOptimisticUserMessage(sessionId, text)
  sessionStore.setSubmitting(true)
  logRuntime('run.start', uiCopy.app.logs.runStart, {
    sessionId,
    attachmentCount: sessionStore.getPendingUploads(sessionId).length,
  })

  try {
    const run = await apiClient.startRun({
      sessionId,
      prompt: text,
      attachments: sessionStore.getPendingUploads(sessionId),
    })

    sessionStore.clearPendingUploads(sessionId)
    runStore.beginRun({ runId: run.runId, sessionId })
    runStore.recordClientNotice({
      sessionId,
      runId: run.runId,
      label: uiCopy.app.notices.runCreated,
      detail: uiCopy.app.notices.runCreatedDetail,
      status: 'completed',
    })
    connectRunStream(run.runId, sessionId)
  } catch (error) {
    const message = error instanceof Error ? error.message : uiCopy.app.logs.runStartError
    logRuntime('run.start.error', message, { sessionId }, 'error')
    runStore.markErrored('pending', message)
    runStore.recordClientIssue({
      sessionId,
      label: uiCopy.app.notices.runStartFailed,
      detail: message,
    })
    sessionStore.addSystemNotice(sessionId, message)
  } finally {
    sessionStore.setSubmitting(false)
  }
}

async function handleStopRun() {
  const runId = String(activeRun.value?.runId || '')
  const sessionId = String(activeRun.value?.sessionId || currentSessionId.value || '')
  if (!runId || !canStopRun.value || stoppingRunId.value) {
    return
  }

  stoppingRunId.value = runId
  try {
    const result = await apiClient.cancelRun(runId)
    if (result.status === 'completed') {
      runStore.markCompleted(runId, uiCopy.app.notices.stopAlreadyCompleted)
      syncSessionTranscript(sessionId)
    } else if (result.status === 'failed') {
      runStore.markErrored(runId, uiCopy.app.notices.stopAlreadyFailed)
    } else {
      runStore.markCancelled(runId, uiCopy.app.notices.stopped)
    }
    closeStream()
  } catch (error) {
    const message = error instanceof Error ? error.message : uiCopy.app.logs.stopRunError
    runStore.recordClientIssue({
      sessionId,
      label: uiCopy.app.notices.stopRunFailed,
      detail: message,
    })
  } finally {
    stoppingRunId.value = ''
  }
}

onMounted(async () => {
  setupViewportTracking()
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
  teardownViewportTracking()
})
</script>

<template>
  <div class="app-shell">
    <template v-if="isAuthenticated">
      <header class="app-toolbar">
        <div class="toolbar-brand">
          <p class="eyebrow">{{ uiCopy.app.brand }}</p>
          <h1>{{ uiCopy.app.title }}</h1>
          <p class="topbar-copy">{{ topbarStatusCopy }}</p>
        </div>

        <dl class="toolbar-metrics" :aria-label="uiCopy.app.overviewLabel">
          <div>
            <dt>{{ uiCopy.app.metrics.sessions }}</dt>
            <dd>{{ sessionCount }}</dd>
          </div>
          <div>
            <dt>{{ uiCopy.app.metrics.messages }}</dt>
            <dd>{{ currentMessageCount }}</dd>
          </div>
          <div>
            <dt>{{ uiCopy.app.metrics.attachments }}</dt>
            <dd>{{ pendingUploadCount }}</dd>
          </div>
        </dl>

        <div class="toolbar-actions">
          <el-tag
            size="small"
            :type="
              runStatus === 'failed'
                ? 'danger'
                : runStatus === 'completed'
                  ? 'success'
                  : runStatus === 'cancelled' || runStatus === 'cancelling'
                    ? 'warning'
                    : 'primary'
            "
            effect="light"
          >
            {{ runStatusLabel }}
          </el-tag>
          <el-button v-if="!isWideLayout" size="small" plain @click="toggleTimelinePanel">
            {{ timelineToggleLabel }}
          </el-button>
        </div>
      </header>

      <main class="layout-grid">
        <aside class="sidebar-shell">
          <SessionSidebar
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

        <section
          class="workspace-shell"
          :class="{ 'workspace-shell--wide': isWideLayout }"
          :aria-label="uiCopy.app.workspaceAriaLabel"
        >
          <ChatWorkspace
            :can-stop="canStopRun"
            :current-session="currentSession"
            :messages="currentMessages"
            :pending-uploads="pendingUploads"
            :loading="loadingMessages"
            :uploading="uploading"
            :submitting="submitting"
            :active-run="activeRun"
            :run-status="runStatus"
            :run-status-label="runStatusLabel"
            :stopping="stoppingRun"
            :error="combinedError"
            @submit="handleSubmit"
            @stop-run="handleStopRun"
            @upload="handleUpload"
          />

          <div
            v-if="!isWideLayout && showTimelinePanel"
            class="timeline-backdrop"
            @click="closeTimelinePanel"
          />

          <aside
            v-if="showTimelinePanel"
            class="timeline-shell"
            :class="{ 'timeline-shell--overlay': !isWideLayout }"
            :aria-label="uiCopy.app.timelineAriaLabel"
          >
            <ProgressTimeline
              :active-run="activeRun"
              :can-stop="canStopRun"
              :connection-state="connectionState"
              :diagnostics="runtimeDiagnostics"
              :runtime-error="runError"
              :dismissible="!isWideLayout"
              :stopping="stoppingRun"
              @close="closeTimelinePanel"
            />
          </aside>
        </section>
      </main>
    </template>

    <main v-else class="auth-layout">
      <el-card class="auth-panel card-shell" shadow="never">
        <div class="auth-card">
          <p class="eyebrow">{{ uiCopy.app.auth.eyebrow }}</p>
          <h2>{{ uiCopy.app.auth.title }}</h2>
          <p class="auth-copy">{{ uiCopy.app.auth.copy }}</p>

          <label class="composer-label" for="admin-username">{{ uiCopy.app.auth.username }}</label>
          <el-input
            id="admin-username"
            v-model="authUsername"
            autocomplete="username"
            :placeholder="uiCopy.app.auth.usernamePlaceholder"
          />

          <label class="composer-label" for="admin-password">{{ uiCopy.app.auth.password }}</label>
          <el-input
            id="admin-password"
            v-model="authPassword"
            type="password"
            show-password
            autocomplete="current-password"
            :placeholder="uiCopy.app.auth.passwordPlaceholder"
          />

          <el-alert v-if="authError" :closable="false" type="error" show-icon :title="authError" />

          <div class="composer-actions auth-actions">
            <span class="muted-copy">{{ uiCopy.app.auth.hint }}</span>
            <el-button type="primary" :loading="authLoading" @click="handleLogin">
              {{ authLoading ? uiCopy.app.auth.loading : uiCopy.app.auth.login }}
            </el-button>
          </div>
        </div>
      </el-card>
    </main>
  </div>
</template>
