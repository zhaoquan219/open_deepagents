<script setup>
import { UploadFilled } from '@element-plus/icons-vue'
import { computed, ref } from 'vue'

import MessageThread from './MessageThread.vue'

const props = defineProps({
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
    default: '待命',
  },
  submitting: {
    type: Boolean,
    default: false,
  },
  uploading: {
    type: Boolean,
    default: false,
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
const canSubmitDraft = computed(() => Boolean(draft.value.trim()))
const primaryActionLabel = computed(() => {
  if (props.stopping) {
    return '停止中…'
  }
  if (isRunLocked.value) {
    return '停止'
  }
  if (props.submitting) {
    return '发送中…'
  }
  return '发送'
})
const primaryActionType = computed(() => (isRunLocked.value ? 'danger' : 'primary'))
const composerStatusCopy = computed(() => {
  if (props.stopping) {
    return '正在请求停止当前运行，界面会在服务端确认后恢复可发送状态。'
  }
  if (isRunLocked.value) {
    return '主按钮已切换为停止操作；发送下一轮前需要先结束当前运行。'
  }
  if (props.uploading) {
    return '附件上传中…'
  }
  return '附件会自动附加到下一次发送。'
})
const runtimeCopy = computed(() => {
  if (props.runStatus === 'running') {
    return '当前任务仍在处理中，底部主按钮已经切换为停止操作，新的内容会自动追加到消息流。'
  }
  if (props.runStatus === 'completed') {
    return '本轮处理已经完成，可以立刻继续追问。'
  }
  if (props.runStatus === 'cancelled') {
    return '本轮处理已手动停止，可以调整上下文后重新发起。'
  }
  if (props.runStatus === 'failed') {
    return '本轮处理失败，先查看运行面板再决定是否重试。'
  }
  return '对话区已准备就绪，把目标和上下文一次说清就可以开始。'
})

function statusTagType(status) {
  if (status === 'failed') return 'danger'
  if (status === 'completed') return 'success'
  if (status === 'cancelled' || status === 'cancelling') return 'warning'
  return 'primary'
}

function uploadStatusLabel(status) {
  if (status === 'submitted') return '已提交'
  if (status === 'uploaded') return '已上传'
  return '待发送'
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

function handlePrimaryAction() {
  if (isRunLocked.value) {
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
              <p class="eyebrow">当前会话</p>
              <h2>{{ props.currentSession?.title || '新会话' }}</h2>
            </div>
            <p class="workspace-runtime-copy">{{ runtimeCopy }}</p>
          </div>

          <dl class="workspace-summary-list">
            <div>
              <dt>消息</dt>
              <dd>{{ messageCount }}</dd>
            </div>
            <div>
              <dt>助手回复</dt>
              <dd>{{ assistantMessageCount }}</dd>
            </div>
            <div>
              <dt>待发附件</dt>
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
          description="正在加载会话内容…"
        />

        <section v-else-if="!hasMessages" class="empty-state">
          <p class="empty-state-kicker">准备开始新的任务</p>
          <h3>把目标、背景和限制条件一次交代清楚</h3>
          <p>系统会把回复、工具动作和最终结果都收敛到这一条会话里，便于连续协作。</p>
        </section>

        <MessageThread v-else :messages="props.messages" />
      </div>
    </div>

    <div class="composer-panel">
      <div class="upload-row">
        <div class="upload-row-main">
          <p class="composer-label">输入内容</p>
          <span class="muted-copy">{{ composerStatusCopy }}</span>
        </div>
        <el-button
          size="small"
          plain
          :icon="UploadFilled"
          :disabled="props.uploading || isRunLocked"
          @click="triggerFilePicker"
        >
          上传附件
        </el-button>
        <input
          ref="fileInput"
          class="hidden-input"
          type="file"
          multiple
          @change="handleFileSelection"
        />
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
        placeholder="请输入你的问题，或结合附件说明要处理的任务。"
        @keydown="handleComposerKeydown"
      />

      <div class="composer-actions">
        <span class="muted-copy">
          {{ isRunLocked ? '当前运行中，主按钮已切换为停止。' : '按 Ctrl 或 Command + Enter 可快速发送' }}
        </span>
        <el-button
          class="composer-primary-button"
          size="small"
          :type="primaryActionType"
          :loading="props.submitting || props.stopping"
          :disabled="(!isRunLocked && !canSubmitDraft) || (isRunLocked && props.stopping)"
          @click="handlePrimaryAction"
        >
          {{ primaryActionLabel }}
        </el-button>
      </div>
    </div>
  </div>
</template>
