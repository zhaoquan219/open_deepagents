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
          <p class="eyebrow">Current session</p>
          <h2>{{ currentSession?.title || 'New conversation' }}</h2>
        </div>
        <span class="status-pill" :data-status="runStatus">{{ runStatus }}</span>
      </div>

      <p v-if="error" class="inline-error">{{ error }}</p>

      <section v-if="loading" class="empty-state">
        <p>Loading transcript…</p>
      </section>

      <section v-else-if="!hasMessages" class="empty-state">
        <h3>Ready for the next run</h3>
        <p>Start a session, attach context files, and stream agent progress into this workspace.</p>
      </section>

      <message-thread v-else :messages="messages" />

      <div class="composer-panel">
        <div class="upload-row">
          <button class="secondary-button" type="button" @click="triggerFilePicker">Attach files</button>
          <input ref="fileInput" class="hidden-input" type="file" multiple @change="handleFileSelection" />
          <span class="muted-copy">{{ uploading ? 'Uploading…' : 'Uploads stay attached to the next run.' }}</span>
        </div>

        <ul v-if="pendingUploads.length" class="upload-list">
          <li v-for="file in pendingUploads" :key="file.id">
            <span>{{ file.name }}</span>
            <span class="upload-status">{{ file.status }}</span>
          </li>
        </ul>

        <label class="composer-label" for="prompt">Prompt</label>
        <textarea
          id="prompt"
          v-model="draft"
          class="composer-textarea"
          rows="5"
          placeholder="Ask the agent to reason, use tools, or summarize uploads…"
          @keydown="handleComposerKeydown"
        ></textarea>

        <div class="composer-actions">
          <span class="muted-copy">⌘/Ctrl + Enter to send</span>
          <button class="primary-button" type="button" :disabled="submitting || !draft.trim()" @click="submitDraft">
            {{ submitting ? 'Starting…' : 'Start run' }}
          </button>
        </div>
      </div>
    </div>
  `,
})
