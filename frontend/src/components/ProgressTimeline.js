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
    <div class="runtime-card">
      <div class="runtime-header">
        <div>
          <p class="eyebrow">状态</p>
          <h3>本轮处理</h3>
        </div>
        <span v-if="activeRun" class="status-pill" :data-status="activeRun.status">{{ activeRun.status }}</span>
      </div>

      <div v-if="!activeRun" class="runtime-empty">
        <p>提交问题后，这里会显示简要进度。</p>
      </div>

      <template v-else>
        <p class="runtime-copy">
          {{ activeRun.connected ? '已连接到实时输出通道。' : '正在建立实时连接…' }}
        </p>

        <ol class="runtime-list">
          <li
            v-for="entry in activeRun.timeline.slice(-3)"
            :key="entry.id"
            class="runtime-entry"
            :data-status="entry.status"
          >
            <div>
              <p class="runtime-label">{{ entry.label }}</p>
              <p v-if="entry.detail" class="runtime-detail">{{ entry.detail }}</p>
              <p class="timeline-meta">{{ formatDateTime(entry.timestamp) }}</p>
            </div>
          </li>
        </ol>
      </template>
    </div>
  `,
})
