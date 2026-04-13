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
    statusLabel(status) {
      if (status === 'running' || status === 'in_progress') {
        return '处理中'
      }
      if (status === 'completed') {
        return '已完成'
      }
      if (status === 'failed') {
        return '失败'
      }
      if (status === 'queued') {
        return '排队中'
      }
      return '待命'
    },
    entryLabel(entry) {
      const label = String(entry?.label || '')
      const labelMap = {
        'Run started': '开始处理',
        'Run running': '正在处理',
        'Run completed': '处理完成',
        'Run failed': '处理失败',
        'Assistant message finalized': '最终回复已写入会话',
      }
      if (labelMap[label]) {
        return labelMap[label]
      }
      if (entry?.kind === 'message.delta') {
        return '正在生成回复'
      }
      if (entry?.kind === 'message.final') {
        return '回复生成完成'
      }
      if (entry?.kind === 'status') {
        return '状态更新'
      }
      if (entry?.kind === 'error') {
        return '处理异常'
      }
      return label || '进度更新'
    },
  },
  template: `
    <div class="runtime-card">
      <div class="runtime-header">
        <div>
          <p class="eyebrow">处理进度</p>
          <h3>运行状态</h3>
        </div>
        <span class="status-pill" :data-status="activeRun?.status || 'idle'">
          {{ statusLabel(activeRun?.status) }}
        </span>
      </div>

      <div v-if="!activeRun" class="runtime-empty">
        <p>发起提问后，这里会显示本轮处理进度。</p>
      </div>

      <template v-else>
        <p class="runtime-copy">
          {{ activeRun.connected ? '已连接实时通道，正在接收输出。' : '正在建立实时连接…' }}
        </p>

        <dl class="runtime-meta-list">
          <div>
            <dt>运行编号</dt>
            <dd>{{ activeRun.runId }}</dd>
          </div>
          <div>
            <dt>开始时间</dt>
            <dd>{{ formatDateTime(activeRun.startedAt) }}</dd>
          </div>
        </dl>

        <ol class="runtime-list">
          <li
            v-for="entry in activeRun.timeline.slice(-6)"
            :key="entry.id"
            class="runtime-entry"
            :data-status="entry.status"
          >
            <div class="runtime-entry-main">
              <p class="runtime-label">{{ entryLabel(entry) }}</p>
              <p v-if="entry.detail" class="runtime-detail">{{ entry.detail }}</p>
              <p class="timeline-meta">{{ formatDateTime(entry.timestamp) }}</p>
            </div>
            <span class="runtime-entry-status">{{ statusLabel(entry.status) }}</span>
          </li>
        </ol>
      </template>
    </div>
  `,
})
