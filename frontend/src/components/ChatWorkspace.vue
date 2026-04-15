<script setup>
import { UploadFilled } from '@element-plus/icons-vue'
import { computed, ref } from 'vue'

import { uiCopy } from '../lib/copy.js'
import MessageThread from './MessageThread.vue'

const props = defineProps({
  canStop: {
    type: Boolean,
    default: false,
  },
  currentSession: {
    type: Object,
    default: null,
  },
  error: {
    type: String,
    default: '',
  },
  loading: {
    type: Boolean,
    default: false,
  },
  messages: {
    type: Array,
    default: () => [],
  },
  pendingUploads: {
    type: Array,
    default: () => [],
  },
  runStatus: {
    type: String,
    default: 'idle',
  },
  runStatusLabel: {
    type: String,
    default: uiCopy.common.idle,
  },
  submitting: {
    type: Boolean,
    default: false,
  },
  uploading: {
    type: Boolean,
    default: false,
  },
  activeRun: {
    type: Object,
    default: null,
  },
  stopping: {
    type: Boolean,
    default: false,
  },
})

const emit = defineEmits(['submit', 'upload', 'stop-run'])

const draft = ref('')
const fileInput = ref(null)

const hasMessages = computed(() => props.messages.length > 0)
const messageCount = computed(() => props.messages.length)
const assistantMessageCount = computed(
  () => props.messages.filter((message) => message?.role === 'assistant').length,
)
const isRunLocked = computed(() => ['queued', 'running'].includes(props.runStatus))
const usesStopAction = computed(() => props.canStop && Boolean(props.activeRun))
const runtimeCopy = computed(() => {
  if (props.runStatus === 'running') {
    return uiCopy.workspace.runtimeCopy.running
  }
  if (props.runStatus === 'completed') {
    return uiCopy.workspace.runtimeCopy.completed
  }
  if (props.runStatus === 'cancelled') {
    return uiCopy.workspace.runtimeCopy.cancelled
  }
  if (props.runStatus === 'failed') {
    return uiCopy.workspace.runtimeCopy.failed
  }
  return uiCopy.workspace.runtimeCopy.idle
})

function statusTagType(status) {
  if (status === 'failed') return 'danger'
  if (status === 'completed') return 'success'
  if (status === 'cancelled' || status === 'cancelling') return 'warning'
  return 'primary'
}

function uploadStatusLabel(status) {
  if (status === 'submitted') return uiCopy.workspace.uploadStatus.submitted
  if (status === 'uploaded') return uiCopy.workspace.uploadStatus.uploaded
  return uiCopy.workspace.uploadStatus.pending
}

function submitDraft() {
  if (isRunLocked.value) {
    return
  }
  const prompt = draft.value.trim()
  if (!prompt) {
    return
  }

  emit('submit', { prompt })
  draft.value = ''
}

function triggerFilePicker() {
  fileInput.value?.click()
}

function handleFileSelection(event) {
  const files = [...(event.target.files || [])]
  if (files.length > 0) {
    emit('upload', files)
  }
  event.target.value = ''
}

function handleComposerKeydown(event) {
  if (!isRunLocked.value && (event.metaKey || event.ctrlKey) && event.key === 'Enter') {
    submitDraft()
  }
}

const primaryActionLabel = computed(() => {
  if (usesStopAction.value) {
    return props.stopping ? uiCopy.workspace.actions.stopping : uiCopy.workspace.actions.stop
  }
  return props.submitting ? uiCopy.workspace.actions.sending : uiCopy.workspace.actions.send
})

const primaryActionType = computed(() => (usesStopAction.value ? 'danger' : 'primary'))
const primaryActionDisabled = computed(() => {
  if (usesStopAction.value) {
    return props.stopping
  }
  return props.submitting || isRunLocked.value || !draft.value.trim()
})
const composerHint = computed(() =>
  usesStopAction.value ? uiCopy.workspace.composerHint.stop : uiCopy.workspace.composerHint.send,
)

function handlePrimaryAction() {
  if (usesStopAction.value) {
    emit('stop-run')
    return
  }
  submitDraft()
}
</script>

<template>
  <div class="workspace">
    <div class="workspace-header">
      <div class="workspace-header-main">
        <div class="workspace-header-top">
          <div class="workspace-heading-block">
            <div class="workspace-heading">
              <p class="eyebrow">{{ uiCopy.workspace.sessionEyebrow }}</p>
              <h2>{{ props.currentSession?.title || uiCopy.workspace.newSessionTitle }}</h2>
            </div>
            <p class="workspace-runtime-copy">{{ runtimeCopy }}</p>
          </div>

          <dl class="workspace-summary-list">
            <div>
              <dt>{{ uiCopy.workspace.metrics.messages }}</dt>
              <dd>{{ messageCount }}</dd>
            </div>
            <div>
              <dt>{{ uiCopy.workspace.metrics.assistantMessages }}</dt>
              <dd>{{ assistantMessageCount }}</dd>
            </div>
            <div>
              <dt>{{ uiCopy.workspace.metrics.pendingAttachments }}</dt>
              <dd>{{ props.pendingUploads.length }}</dd>
            </div>
          </dl>
        </div>
      </div>
      <div class="workspace-header-actions">
        <el-tag size="small" :type="statusTagType(props.runStatus)" effect="light">
          {{ props.runStatusLabel }}
        </el-tag>
      </div>
    </div>

    <div class="workspace-main">
      <el-alert
        v-if="props.error"
        class="workspace-error-alert"
        :closable="false"
        type="error"
        show-icon
        :title="props.error"
      />

      <div class="workspace-thread-shell">
        <el-empty
          v-if="props.loading"
          class="empty-state"
          :image-size="72"
          :description="uiCopy.workspace.loadingDescription"
        />

        <section v-else-if="!hasMessages" class="empty-state">
          <p class="empty-state-kicker">{{ uiCopy.workspace.emptyKicker }}</p>
          <h3>{{ uiCopy.workspace.emptyTitle }}</h3>
          <p>{{ uiCopy.workspace.emptyCopy }}</p>
        </section>

        <MessageThread v-else :messages="props.messages" />
      </div>
    </div>

    <div class="composer-panel">
      <div class="upload-row">
        <div class="upload-row-main">
          <p class="composer-label">{{ uiCopy.workspace.composerLabel }}</p>
          <span class="muted-copy">
            {{
              isRunLocked
                ? uiCopy.workspace.uploadHint.locked
                : props.uploading
                  ? uiCopy.workspace.uploadHint.uploading
                  : uiCopy.workspace.uploadHint.idle
            }}
          </span>
        </div>
        <el-button
          size="small"
          plain
          :icon="UploadFilled"
          :disabled="props.uploading || isRunLocked"
          @click="triggerFilePicker"
        >
          {{ uiCopy.workspace.uploadButton }}
        </el-button>
        <input ref="fileInput" class="hidden-input" type="file" multiple @change="handleFileSelection" />
      </div>

      <ul v-if="props.pendingUploads.length" class="upload-list">
        <li v-for="file in props.pendingUploads" :key="file.id">
          <span>{{ file.name }}</span>
          <el-tag size="small" effect="plain">{{ uploadStatusLabel(file.status) }}</el-tag>
        </li>
      </ul>

      <el-input
        v-model="draft"
        class="composer-textarea"
        type="textarea"
        :autosize="{ minRows: 3, maxRows: 6 }"
        resize="none"
        :disabled="isRunLocked"
        :placeholder="uiCopy.workspace.placeholder"
        @keydown="handleComposerKeydown"
      />

      <div class="composer-actions">
        <span class="muted-copy">{{ composerHint }}</span>
        <el-button
          class="composer-submit-button"
          size="small"
          :type="primaryActionType"
          :loading="usesStopAction ? props.stopping : props.submitting"
          :disabled="primaryActionDisabled"
          @click="handlePrimaryAction"
        >
          {{ primaryActionLabel }}
        </el-button>
      </div>
    </div>
  </div>
</template>
