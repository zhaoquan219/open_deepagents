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
      submitDraft,
      triggerFilePicker,
    }
  },
  template: `
    <div class="workspace">
      <div class="workspace-header">
        <div>
          <p class="eyebrow">当前会话</p>
          <h2>{{ currentSession?.title || '新的对话' }}</h2>
        </div>
        <span class="status-pill" :data-status="runStatus">{{ runStatus === 'idle' ? '待命' : runStatus }}</span>
      </div>

      <p v-if="error" class="inline-error">{{ error }}</p>

      <section v-if="loading" class="empty-state">
        <p>正在加载对话记录…</p>
      </section>

      <section v-else-if="!hasMessages" class="empty-state">
        <h3>开始一段新的智能对话</h3>
        <p>在这里输入你的问题，系统会以聊天气泡的形式持续返回结果。</p>
      </section>

      <message-thread v-else :messages="messages" />

      <div class="workspace-runtime" v-if="activeRun || runStatus !== 'idle'">
        <p class="workspace-runtime-copy">
          {{ runStatus === 'running' ? '正在处理你的问题，请稍候…' : '本轮对话已完成。' }}
        </p>
      </div>

      <div class="composer-panel">
        <div class="upload-row">
          <button class="secondary-button" type="button" @click="triggerFilePicker">上传附件</button>
          <input ref="fileInput" class="hidden-input" type="file" multiple @change="handleFileSelection" />
          <span class="muted-copy">{{ uploading ? '附件上传中…' : '附件会自动附加到下一次提问。' }}</span>
        </div>

        <ul v-if="pendingUploads.length" class="upload-list">
          <li v-for="file in pendingUploads" :key="file.id">
            <span>{{ file.name }}</span>
            <span class="upload-status">{{ file.status === 'submitted' ? '已提交' : '已上传' }}</span>
          </li>
        </ul>

        <label class="composer-label" for="prompt">输入内容</label>
        <textarea
          id="prompt"
          v-model="draft"
          class="composer-textarea"
          rows="5"
          placeholder="请输入你的问题，或让智能助手结合附件帮你完成任务…"
          @keydown="handleComposerKeydown"
        ></textarea>

        <div class="composer-actions">
          <span class="muted-copy">按 Ctrl/Command + Enter 快速发送</span>
          <button class="primary-button" type="button" :disabled="submitting || !draft.trim()" @click="submitDraft">
            {{ submitting ? '发送中…' : '发送' }}
          </button>
        </div>
      </div>
    </div>
  `,
})
