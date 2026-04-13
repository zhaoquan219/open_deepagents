import { computed, defineComponent, ref } from 'vue'
import MessageThread from './MessageThread.js'

export default defineComponent({
  name: 'ChatWorkspace',
  components: {
    MessageThread,
  },
  props: {
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
    activeRun: {
      type: Object,
      default: null,
    },
  },
  emits: ['submit', 'upload'],
  setup(props, { emit }) {
    const draft = ref('')
    const fileInput = ref(null)
    const hasMessages = computed(() => props.messages.length > 0)
    const runtimeCopy = computed(() => {
      if (props.runStatus === 'running') {
        return '正在持续生成回复，新的内容会自动追加到消息区。'
      }
      if (props.runStatus === 'completed') {
        return '本轮处理已完成，可以继续追问。'
      }
      if (props.runStatus === 'failed') {
        return '本轮处理失败，请检查提示信息后重试。'
      }
      return '准备就绪，输入问题即可开始。'
    })
    const uploadStatusLabel = (status) => {
      if (status === 'submitted') {
        return '已提交'
      }
      if (status === 'uploaded') {
        return '已上传'
      }
      return '待发送'
    }

    function submitDraft() {
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
      if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') {
        submitDraft()
      }
    }

    return {
      draft,
      fileInput,
      handleComposerKeydown,
      handleFileSelection,
      hasMessages,
      runtimeCopy,
      submitDraft,
      triggerFilePicker,
      uploadStatusLabel,
    }
  },
  template: `
    <div class="workspace">
      <div class="workspace-header">
        <div>
          <p class="eyebrow">当前会话</p>
          <h2>{{ currentSession?.title || '新会话' }}</h2>
        </div>
        <span class="status-pill" :data-status="runStatus">{{ runStatusLabel }}</span>
      </div>

      <div class="workspace-main">
        <p v-if="error" class="inline-error workspace-error">{{ error }}</p>

        <section v-if="loading" class="empty-state">
          <p>正在加载会话内容…</p>
        </section>

        <section v-else-if="!hasMessages" class="empty-state">
          <h3>开始新的对话</h3>
          <p>在下方输入问题，系统会按消息流的方式持续返回结果。</p>
        </section>

        <message-thread v-else :messages="messages" />

        <div class="workspace-runtime" v-if="activeRun || runStatus !== 'idle'">
          <p class="workspace-runtime-copy">{{ runtimeCopy }}</p>
        </div>
      </div>

      <div class="composer-panel">
        <div class="upload-row">
          <button class="secondary-button" type="button" @click="triggerFilePicker">上传附件</button>
          <input ref="fileInput" class="hidden-input" type="file" multiple @change="handleFileSelection" />
          <span class="muted-copy">{{ uploading ? '附件上传中…' : '附件会自动附加到下一次发送。' }}</span>
        </div>

        <ul v-if="pendingUploads.length" class="upload-list">
          <li v-for="file in pendingUploads" :key="file.id">
            <span>{{ file.name }}</span>
            <span class="upload-status">{{ uploadStatusLabel(file.status) }}</span>
          </li>
        </ul>

        <label class="composer-label" for="prompt">输入内容</label>
        <textarea
          id="prompt"
          v-model="draft"
          class="composer-textarea"
          rows="4"
          placeholder="请输入你的问题，或结合附件说明要处理的任务。"
          @keydown="handleComposerKeydown"
        ></textarea>

        <div class="composer-actions">
          <span class="muted-copy">按 Ctrl 或 Command + Enter 可快速发送</span>
          <button class="primary-button" type="button" :disabled="submitting || !draft.trim()" @click="submitDraft">
            {{ submitting ? '发送中…' : '发送' }}
          </button>
        </div>
      </div>
    </div>
  `,
})
