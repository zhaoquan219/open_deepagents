import { defineComponent } from 'vue'
import { formatDateTime } from '../lib/time.js'

export default defineComponent({
  name: 'ProgressTimeline',
  props: {
    activeRun: {
      type: Object,
      default: null,
    },
  },
  methods: {
    formatDateTime,
  },
  template: `
    <div class="timeline">
      <div class="timeline-header">
        <div>
          <p class="eyebrow">Execution visibility</p>
          <h2>Progress timeline</h2>
        </div>
        <span v-if="activeRun" class="status-pill" :data-status="activeRun.status">{{ activeRun.status }}</span>
      </div>

      <div v-if="!activeRun" class="empty-state compact">
        <p>No active run yet. Stream events will appear here once a run starts.</p>
      </div>

      <template v-else>
        <dl class="run-summary">
          <div>
            <dt>Run ID</dt>
            <dd>{{ activeRun.runId }}</dd>
          </div>
          <div>
            <dt>Connection</dt>
            <dd>{{ activeRun.connected ? 'Connected' : 'Waiting for stream' }}</dd>
          </div>
        </dl>

        <ol class="timeline-list">
          <li v-for="entry in activeRun.timeline" :key="entry.id" class="timeline-entry" :data-status="entry.status">
            <div class="timeline-dot"></div>
            <div>
              <p class="timeline-label">{{ entry.label }}</p>
              <p v-if="entry.detail" class="timeline-detail">{{ entry.detail }}</p>
              <p class="timeline-meta">{{ entry.kind }} · {{ formatDateTime(entry.timestamp) }}</p>
            </div>
          </li>
        </ol>
      </template>
    </div>
  `,
})
