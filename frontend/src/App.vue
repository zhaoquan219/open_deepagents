<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { ElMessageBox } from "element-plus";

import ChatWorkspace from "./components/ChatWorkspace.vue";
import SessionSidebar from "./components/SessionSidebar.vue";
import { createApiClient } from "./api/client.js";
import { localeState, setLocale, uiCopy } from "./lib/copy.js";
import {
  initializeAuthenticatedApp,
  isForeignSessionStreamEnvelope,
} from "./lib/appBootstrap.js";
import { normalizeStreamEnvelope } from "./lib/sseContract.js";
import { createRunStore } from "./store/runStore.js";
import { createSessionStore } from "./store/sessionStore.js";

function logRuntime(scope, detail, payload, level = "debug") {
  if (
    level === "debug" &&
    import.meta.env.VITE_DEEPAGENTS_VERBOSE_STREAM !== "true"
  ) {
    return;
  }

  const logger = console[level] || console.log;
  logger(`[deepagents-ui] ${scope}: ${detail}`, payload ?? "");
}

const apiClient = createApiClient();
const sessionStore = createSessionStore(apiClient);
const runStore = createRunStore();
if (import.meta.env.DEV && typeof window !== "undefined") {
  window.__deepagentsDebug = {
    sessionStore,
    runStore,
  };
}
const activeStream = ref(null);
const pendingSessionDeltas = new Map();
let pendingSessionDeltaFlush = 0;
const authUsername = ref("admin");
const authPassword = ref("");
const authError = ref("");
const authLoading = ref(false);
const authChecked = ref(false);
const isAuthenticated = ref(false);
const stoppingRunId = ref("");
const messageSendScrollKey = ref(0);
const runtimeOptions = ref({ models: [], defaultModelId: "" });
const runtimeOptionsError = ref("");
const selectedModelId = ref("");

function scheduleSessionDeltaFlush() {
  if (pendingSessionDeltaFlush) {
    return;
  }
  const scheduler =
    globalThis.requestAnimationFrame ||
    ((callback) => globalThis.setTimeout(callback, 16));
  pendingSessionDeltaFlush = scheduler(() => {
    pendingSessionDeltaFlush = 0;
    flushSessionDeltas();
  });
}

function flushSessionDeltas(key = "") {
  const entries = key
    ? [[key, pendingSessionDeltas.get(key)]]
    : [...pendingSessionDeltas.entries()];
  for (const [entryKey, envelope] of entries) {
    if (!envelope) {
      continue;
    }
    pendingSessionDeltas.delete(entryKey);
    sessionStore.consumeRunEvent(envelope);
  }
}

function consumeSessionRunEvent(envelope) {
  const key = `${envelope.sessionId}:${envelope.runId}`;
  if (envelope.type !== "message.delta" || !envelope.delta) {
    flushSessionDeltas(key);
    sessionStore.consumeRunEvent(envelope);
    return;
  }
  const previous = pendingSessionDeltas.get(key);
  pendingSessionDeltas.set(key, {
    ...envelope,
    delta: `${previous?.delta || ""}${envelope.delta}`,
  });
  scheduleSessionDeltaFlush();
}

const displaySessions = computed(() =>
  sessionStore.state.searchQuery
    ? sessionStore.state.searchResults
    : sessionStore.state.sessions,
);
const searchingSessions = computed(() => sessionStore.state.searching);
const searchQuery = computed(() => sessionStore.state.searchQuery);
const currentSessionId = computed(() => sessionStore.state.currentSessionId);
const currentSession = computed(() => sessionStore.getCurrentSession());
const currentMessages = computed(() => sessionStore.getCurrentMessages());
const pendingUploads = computed(() => sessionStore.getPendingUploads());
const deletingUploads = computed(() => sessionStore.state.deletingUploadIds);
const loadingSessions = computed(() => sessionStore.state.loadingSessions);
const loadingMessages = computed(() => sessionStore.state.loadingMessages);
const uploading = computed(() => sessionStore.state.uploading);
const submitting = computed(() => sessionStore.state.submitting);
const deletingSessionId = computed(() => sessionStore.state.deletingSessionId);
const sessionError = computed(() => sessionStore.state.error);
const combinedError = computed(
  () =>
    runtimeOptionsError.value ||
    runStore.state.error ||
    sessionStore.state.error ||
    sessionStore.state.uploadError,
);
const activeRun = computed(() => runStore.state.activeRun);
const runStatus = computed(() => runStore.state.activeRun?.status || "idle");
const currentLocale = computed(() => localeState.current);
const canStopRun = computed(
  () =>
    ["queued", "running"].includes(runStatus.value) &&
    Boolean(activeRun.value?.runId),
);
const stoppingRun = computed(
  () =>
    Boolean(stoppingRunId.value) &&
    stoppingRunId.value === String(activeRun.value?.runId || ""),
);
const topbarStatusCopy = computed(() => {
  if (runStatus.value === "running") {
    return uiCopy.app.topbarStatus.running;
  }
  if (runStatus.value === "completed") {
    return uiCopy.app.topbarStatus.completed;
  }
  if (runStatus.value === "cancelled") {
    return uiCopy.app.topbarStatus.cancelled;
  }
  if (runStatus.value === "failed") {
    return uiCopy.app.topbarStatus.failed;
  }
  return uiCopy.app.topbarStatus.idle;
});
const runStatusLabel = computed(() => {
  const status = runStatus.value;
  if (status === "idle") return uiCopy.common.idle;
  if (status === "queued") return uiCopy.common.queued;
  if (status === "running") return uiCopy.common.running;
  if (status === "completed") return uiCopy.common.completed;
  if (status === "cancelling") return uiCopy.common.cancelling;
  if (status === "cancelled") return uiCopy.common.cancelled;
  if (status === "failed") return uiCopy.common.failed;
  return uiCopy.common.running;
});

function handleLocaleChange(locale) {
  setLocale(locale);
}

async function loadRuntimeOptions() {
  try {
    const options = await apiClient.getRuntimeOptions();
    runtimeOptionsError.value = "";
    runtimeOptions.value = options;
    selectedModelId.value = options.models.some(
      (model) => model.id === selectedModelId.value,
    )
      ? selectedModelId.value
      : options.defaultModelId;
  } catch (error) {
    const message =
      error instanceof Error ? error.message : "Runtime options failed";
    runtimeOptionsError.value = message;
    logRuntime("runtime.options.error", message, {}, "warn");
  }
}

function closeStream({ markDisconnected = false } = {}) {
  flushSessionDeltas();
  const runId = runStore.state.activeRun?.runId || "";
  if (activeStream.value) {
    activeStream.value.close();
    activeStream.value = null;
  }
  if (markDisconnected && runId) {
    runStore.markDisconnected(runId);
  }
}

function syncSessionTranscript(sessionId) {
  if (String(currentSessionId.value || "") !== String(sessionId || "")) {
    return;
  }
  void sessionStore.selectSession(String(sessionId));
}

function isTerminalEnvelope(envelope) {
  return (
    envelope.type === "error" ||
    (envelope.type === "status" && envelope.terminal === true)
  );
}

async function handleLogin() {
  authLoading.value = true;
  authError.value = "";
  try {
    await apiClient.login({
      username: authUsername.value.trim(),
      password: authPassword.value,
    });
    await apiClient.getAdminProfile();
    isAuthenticated.value = true;
    await loadRuntimeOptions();
    await sessionStore.loadSessions();
    if (sessionStore.state.currentSessionId) {
      await sessionStore.selectSession(sessionStore.state.currentSessionId);
    }
  } catch (error) {
    apiClient.logout();
    isAuthenticated.value = false;
    authError.value =
      error instanceof Error ? error.message : uiCopy.app.auth.failure;
  } finally {
    authLoading.value = false;
  }
}

async function ensureSession() {
  if (sessionStore.state.currentSessionId) {
    return sessionStore.getCurrentSession();
  }

  return handleCreateSession();
}

let searchDebounceTimer = 0;
function handleSearchSessions(query) {
  if (searchDebounceTimer) {
    globalThis.clearTimeout(searchDebounceTimer);
  }
  searchDebounceTimer = globalThis.setTimeout(() => {
    searchDebounceTimer = 0;
    void sessionStore.searchSessions(query);
  }, 250);
}

async function handleRefreshSessions() {
  await sessionStore.loadSessions({ preserveSelection: true });
  if (sessionStore.state.searchQuery) {
    await sessionStore.searchSessions(sessionStore.state.searchQuery);
  }
}

async function handleCreateSession() {
  const session = await sessionStore.createSession();
  await sessionStore.selectSession(session.id);
  return session;
}

async function handleSelectSession(sessionId) {
  await sessionStore.selectSession(sessionId);
}

async function handleDeleteSession(sessionId) {
  const session = displaySessions.value.find((item) => item.id === sessionId);
  const title = session?.title || uiCopy.app.deleteSession.fallbackTitle;

  try {
    await ElMessageBox.confirm(
      uiCopy.app.deleteSession.message(title),
      uiCopy.app.deleteSession.title,
      {
        confirmButtonText: uiCopy.app.deleteSession.confirm,
        cancelButtonText: uiCopy.app.deleteSession.cancel,
        type: "warning",
      },
    );
  } catch {
    return;
  }

  if (currentSessionId.value === sessionId) {
    closeStream();
    runStore.clear();
  }
  await sessionStore.deleteSession(sessionId);
}

async function handleUpload(files) {
  const session = await ensureSession();
  const result = await sessionStore.uploadFiles(String(session.id), files);
  if (!result?.ok) {
    const message = result?.error?.message || uiCopy.app.logs.uploadError;
    logRuntime(
      "upload.error",
      message,
      { sessionId: session.id, files },
      "error",
    );
    runStore.recordClientIssue({
      sessionId: String(session.id),
      label: uiCopy.app.notices.uploadFailed,
      detail: message,
    });
    return;
  }

  logRuntime("upload.success", uiCopy.app.logs.uploadSuccess, {
    sessionId: session.id,
    count: result.records.length,
  });
  runStore.recordClientNotice({
    sessionId: String(session.id),
    label: uiCopy.app.notices.uploadCompleted,
    detail: uiCopy.app.notices.uploadCompletedDetail(result.records.length),
    status: "completed",
  });
}

async function handleDeletePendingUpload(upload) {
  const sessionId = String(currentSessionId.value || "");
  const uploadId = String(upload?.id || "");
  const uploadName = String(upload?.name || uiCopy.api.unnamedAttachment);
  if (!sessionId || !uploadId) {
    return;
  }

  const result = await sessionStore.deletePendingUpload(sessionId, uploadId);
  if (!result?.ok) {
    const message =
      result?.error?.message ||
      uiCopy.api.deleteUploadFailedForFile(uploadName);
    logRuntime(
      "upload.delete.error",
      message,
      { sessionId, uploadId },
      "error",
    );
    runStore.recordClientIssue({
      sessionId,
      label: uiCopy.app.notices.deleteUploadFailed,
      detail: message,
    });
  }
}

async function handleSubmit({ prompt }) {
  if (["queued", "running"].includes(runStatus.value)) {
    return;
  }
  const text = String(prompt || "").trim();
  if (!text) {
    return;
  }

  const session = await ensureSession();
  const sessionId = String(session.id);
  sessionStore.setSubmitting(true);

  try {
    sessionStore.addOptimisticUserMessage(sessionId, text);
    logRuntime("run.start", uiCopy.app.logs.runStart, {
      sessionId,
      attachmentCount: sessionStore.getPendingUploads(sessionId).length,
    });
    const run = await apiClient.startRun({
      sessionId,
      prompt: text,
      attachments: sessionStore.getPendingUploads(sessionId),
      modelId: selectedModelId.value,
      onOpen() {
        logRuntime("fetch-stream.open", uiCopy.app.logs.sseOpen, { sessionId });
      },
      onEvent(payload) {
        const envelope = normalizeStreamEnvelope(payload);
        if (!envelope) {
          logRuntime(
            "fetch-stream.drop",
            uiCopy.app.logs.sseDrop,
            payload,
            "warn",
          );
          return;
        }
        if (isForeignSessionStreamEnvelope(envelope, sessionId)) {
          logRuntime(
            "fetch-stream.drop",
            uiCopy.app.logs.sseDrop,
            {
              expectedSessionId: sessionId,
              eventId: envelope.eventId,
              sessionId: envelope.sessionId,
            },
            "warn",
          );
          return;
        }
        if (
          !runStore.state.activeRun ||
          runStore.state.activeRun.runId !== envelope.runId
        ) {
          runStore.beginRun({ runId: envelope.runId, sessionId });
        }
        const accepted = runStore.consume(envelope);
        if (!accepted) {
          if (isTerminalEnvelope(envelope)) {
            closeStream({
              markDisconnected: true,
              detail:
                envelope.type === "error"
                  ? uiCopy.app.stream.terminalError
                  : envelope.status === "cancelled"
                    ? uiCopy.app.stream.terminalCancelled
                    : uiCopy.app.stream.terminalCompleted,
            });
          }
          return;
        }
        consumeSessionRunEvent(envelope);
        if (isTerminalEnvelope(envelope)) {
          closeStream({
            markDisconnected: true,
            detail:
              envelope.type === "error"
                ? uiCopy.app.stream.terminalError
                : envelope.status === "cancelled"
                  ? uiCopy.app.stream.terminalCancelled
                  : uiCopy.app.stream.terminalCompleted,
          });
        }
      },
      onError(error) {
        const message =
          error instanceof Error ? error.message : uiCopy.app.stream.retrying;
        logRuntime("fetch-stream.error", message, { sessionId }, "error");
        runStore.markErrored(
          runStore.state.activeRun?.runId || "pending",
          message,
        );
        sessionStore.addSystemNotice(
          sessionId,
          uiCopy.app.stream.recoveryFailure(message),
        );
      },
    });

    sessionStore.clearPendingUploads(sessionId);
    messageSendScrollKey.value += 1;
    if (
      !runStore.state.activeRun ||
      runStore.state.activeRun.runId !== run.runId
    ) {
      runStore.beginRun({ runId: run.runId, sessionId });
    }
    activeStream.value = run;
    runStore.recordClientNotice({
      sessionId,
      runId: run.runId,
      label: uiCopy.app.notices.runCreated,
      detail: uiCopy.app.notices.runCreatedDetail,
      status: "completed",
    });
  } catch (error) {
    const message =
      error instanceof Error ? error.message : uiCopy.app.logs.runStartError;
    logRuntime("run.start.error", message, { sessionId }, "error");
    runStore.markErrored("pending", message);
    runStore.recordClientIssue({
      sessionId,
      label: uiCopy.app.notices.runStartFailed,
      detail: message,
    });
    sessionStore.addSystemNotice(sessionId, message);
  } finally {
    sessionStore.setSubmitting(false);
  }
}

async function handleStopRun() {
  const runId = String(activeRun.value?.runId || "");
  const sessionId = String(
    activeRun.value?.sessionId || currentSessionId.value || "",
  );
  if (!runId || !canStopRun.value || stoppingRunId.value) {
    return;
  }

  stoppingRunId.value = runId;
  runStore.markCancelling(runId, uiCopy.common.cancelling);
  try {
    const result = await apiClient.cancelRun(runId);
    activeStream.value?.close?.();
    if (result.status === "cancelled") {
      runStore.markCancelled(runId, uiCopy.app.notices.stopped);
      sessionStore.discardStreamingMessage(sessionId, runId);
      closeStream({
        markDisconnected: true,
        detail: uiCopy.app.stream.terminalCancelled,
      });
    } else if (result.status === "completed") {
      runStore.recordClientNotice({
        sessionId,
        runId,
        label: uiCopy.app.notices.stopRunFailed,
        detail: uiCopy.app.notices.stopAlreadyCompleted,
        status: "warning",
        clearError: false,
      });
      syncSessionTranscript(sessionId);
      closeStream({
        markDisconnected: true,
        detail: uiCopy.app.stream.completedClosed,
      });
    } else if (result.status === "failed") {
      runStore.recordClientNotice({
        sessionId,
        runId,
        label: uiCopy.app.notices.stopRunFailed,
        detail: uiCopy.app.notices.stopAlreadyFailed,
        status: "warning",
        clearError: false,
      });
      closeStream({
        markDisconnected: true,
        detail: uiCopy.app.stream.terminalError,
      });
    }
  } catch (error) {
    const message =
      error instanceof Error ? error.message : uiCopy.app.logs.stopRunError;
    runStore.recordClientNotice({
      sessionId,
      runId,
      label: uiCopy.app.notices.stopRunFailed,
      detail: message,
      status: "warning",
      clearError: false,
    });
  } finally {
    stoppingRunId.value = "";
  }
}

onMounted(async () => {
  await initializeAuthenticatedApp({
    apiClient,
    loadRuntimeOptions,
    sessionStore,
    markAuthenticated: () => {
      isAuthenticated.value = true;
      authChecked.value = true;
    },
    markUnauthenticated: () => {
      isAuthenticated.value = false;
      authChecked.value = true;
    },
  });
  if (!authChecked.value) {
    authChecked.value = true;
  }
});

onBeforeUnmount(() => {
  if (searchDebounceTimer) {
    globalThis.clearTimeout(searchDebounceTimer);
  }
  closeStream();
});
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

        <div class="toolbar-actions">
          <el-button-group :aria-label="uiCopy.app.locale.label">
            <el-button
              size="small"
              :type="currentLocale === 'zh' ? 'primary' : 'default'"
              @click="handleLocaleChange('zh')"
            >
              {{ uiCopy.app.locale.zh }}
            </el-button>
            <el-button
              size="small"
              :type="currentLocale === 'en' ? 'primary' : 'default'"
              @click="handleLocaleChange('en')"
            >
              {{ uiCopy.app.locale.en }}
            </el-button>
          </el-button-group>
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
        </div>
      </header>

      <main class="layout-grid">
        <aside class="sidebar-shell">
          <SessionSidebar
            :sessions="displaySessions"
            :current-session-id="currentSessionId"
            :loading="loadingSessions"
            :searching="searchingSessions"
            :search-query="searchQuery"
            :error="sessionError"
            :deleting-session-id="deletingSessionId"
            @new-session="handleCreateSession"
            @refresh="handleRefreshSessions"
            @search="handleSearchSessions"
            @select-session="handleSelectSession"
            @delete-session="handleDeleteSession"
          />
        </aside>

        <section
          class="workspace-shell"
          :aria-label="uiCopy.app.workspaceAriaLabel"
        >
          <ChatWorkspace
            :can-stop="canStopRun"
            :current-session="currentSession"
            :messages="currentMessages"
            :message-send-scroll-key="messageSendScrollKey"
            :pending-uploads="pendingUploads"
            :deleting-uploads="deletingUploads"
            :loading="loadingMessages"
            :uploading="uploading"
            :submitting="submitting"
            :active-run="activeRun"
            :run-status="runStatus"
            :run-status-label="runStatusLabel"
            :runtime-options="runtimeOptions"
            :selected-model-id="selectedModelId"
            :stopping="stoppingRun"
            :error="combinedError"
            @submit="handleSubmit"
            @stop-run="handleStopRun"
            @update:selected-model-id="selectedModelId = $event"
            @upload="handleUpload"
            @delete-upload="handleDeletePendingUpload"
          />
        </section>
      </main>
    </template>

    <main v-else-if="authChecked" class="auth-layout">
      <el-card class="auth-panel card-shell" shadow="never">
        <div class="auth-card">
          <div class="composer-actions auth-actions">
            <span class="muted-copy">{{ uiCopy.app.locale.label }}</span>
            <el-button-group :aria-label="uiCopy.app.locale.label">
              <el-button
                size="small"
                :type="currentLocale === 'zh' ? 'primary' : 'default'"
                @click="handleLocaleChange('zh')"
              >
                {{ uiCopy.app.locale.zh }}
              </el-button>
              <el-button
                size="small"
                :type="currentLocale === 'en' ? 'primary' : 'default'"
                @click="handleLocaleChange('en')"
              >
                {{ uiCopy.app.locale.en }}
              </el-button>
            </el-button-group>
          </div>
          <p class="eyebrow">{{ uiCopy.app.auth.eyebrow }}</p>
          <h2>{{ uiCopy.app.auth.title }}</h2>
          <p class="auth-copy">{{ uiCopy.app.auth.copy }}</p>

          <label class="composer-label" for="admin-username">{{
            uiCopy.app.auth.username
          }}</label>
          <el-input
            id="admin-username"
            v-model="authUsername"
            autocomplete="username"
            :placeholder="uiCopy.app.auth.usernamePlaceholder"
          />

          <label class="composer-label" for="admin-password">{{
            uiCopy.app.auth.password
          }}</label>
          <el-input
            id="admin-password"
            v-model="authPassword"
            type="password"
            show-password
            autocomplete="current-password"
            :placeholder="uiCopy.app.auth.passwordPlaceholder"
          />

          <el-alert
            v-if="authError"
            :closable="false"
            type="error"
            show-icon
            :title="authError"
          />

          <div class="composer-actions auth-actions">
            <span class="muted-copy">{{ uiCopy.app.auth.hint }}</span>
            <el-button
              type="primary"
              :loading="authLoading"
              @click="handleLogin"
            >
              {{
                authLoading ? uiCopy.app.auth.loading : uiCopy.app.auth.login
              }}
            </el-button>
          </div>
        </div>
      </el-card>
    </main>
  </div>
</template>
