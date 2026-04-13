import { defineComponent } from 'vue'
import { formatDateTime } from '../lib/time.js'

export default defineComponent({
  name: 'ProgressTimeline',
  props: {
    activeRun: {
      type: Object,
      default: null,
    },
    connectionState: {
      type: String,
      default: 'idle',
    },
    diagnostics: {
      type: Array,
      default: () => [],
    },
    runtimeError: {
      type: String,
      default: '',
    },
  },
  methods: {
    formatDateTime,
    statusLabel(status) {
      if (status === 'info') {
        return '信息'
      }
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
    panelStatus(activeRun, runtimeError, diagnostics) {
      if (activeRun?.status) {
        return activeRun.status
      }
      if (runtimeError || diagnostics.some((entry) => entry.status === 'failed')) {
        return 'failed'
      }
      return 'idle'
    },
    connectionLabel(state) {
      if (state === 'open') {
        return '已连接'
      }
      if (state === 'connecting') {
        return '连接中'
      }
      if (state === 'closed') {
        return '已关闭'
      }
      if (state === 'error') {
        return '连接异常'
      }
      return '未连接'
    },
    entryLabel(entry) {
      const label = String(entry?.label || '')
      const labelMap = {
        '运行已启动': '运行已启动',
        '运行已创建': '运行已创建',
        '运行创建成功': '运行创建成功',
        '运行启动失败': '运行启动失败',
        '运行失败': '运行失败',
        '实时连接已建立': '实时连接已建立',
        '实时连接已关闭': '实时连接已关闭',
        '附件上传完成': '附件上传完成',
        '附件上传失败': '附件上传失败',
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
      if (entry?.kind === 'tool') {
        return '工具执行'
      }
      if (entry?.kind === 'skill') {
        return '技能执行'
      }
      if (entry?.kind === 'sandbox') {
        return '沙箱执行'
      }
      if (entry?.kind === 'connection') {
        return '连接状态变更'
      }
      if (entry?.kind === 'status') {
        return '状态更新'
      }
      if (entry?.kind === 'error') {
        return '处理异常'
      }
      return label || '进度更新'
    },
    mergedEntries(activeRun, diagnostics) {
      const runEntries = Array.isArray(activeRun?.timeline) ? activeRun.timeline : []
      return [...diagnostics, ...runEntries]
        .sort((left, right) => String(left.timestamp).localeCompare(String(right.timestamp)))
        .slice(-12)
    },
  },
  template: `
    <div class="runtime-card">
      <div class="runtime-header">
        <div>
          <p class="eyebrow">处理进度</p>
          <h3>运行状态</h3>
        </div>
        <span class="status-pill" :data-status="panelStatus(activeRun, runtimeError, diagnostics)">
          {{ statusLabel(panelStatus(activeRun, runtimeError, diagnostics)) }}
        </span>
      </div>

      <div v-if="!activeRun && diagnostics.length === 0" class="runtime-empty">
        <p>发起提问后，这里会显示本轮处理进度。</p>
      </div>

      <template v-else>
        <p class="runtime-copy">
          {{
            connectionState === 'open'
              ? '已连接实时通道，正在接收输出。'
              : connectionState === 'connecting'
                ? '正在建立实时连接...'
                : connectionState === 'error'
                  ? '实时连接出现异常，请查看下方日志。'
                  : '当前没有活跃的实时连接。'
          }}
        </p>

        <p v-if="runtimeError" class="runtime-error">{{ runtimeError }}</p>

        <dl class="runtime-meta-list">
          <div>
            <dt>运行编号</dt>
            <dd>{{ activeRun?.runId || '暂无' }}</dd>
          </div>
          <div>
            <dt>开始时间</dt>
            <dd>{{ activeRun?.startedAt ? formatDateTime(activeRun.startedAt) : '暂无' }}</dd>
          </div>
          <div>
            <dt>连接状态</dt>
            <dd>{{ connectionLabel(connectionState) }}</dd>
          </div>
        </dl>

        <ol class="runtime-list">
          <li
            v-for="entry in mergedEntries(activeRun, diagnostics)"
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
