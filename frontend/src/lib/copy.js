import { reactive } from 'vue'

const STORAGE_KEY = 'deepagents.ui.locale'
const SUPPORTED_LOCALES = ['zh', 'en']
const DEFAULT_LOCALE = normalizeLocale(import.meta.env.VITE_DEFAULT_LOCALE || 'zh')

export const messages = {
  zh: {
    common: {
      emptyRecent: '暂无',
      info: '信息',
      idle: '待命',
      queued: '排队中',
      running: '处理中',
      completed: '已完成',
      cancelling: '停止中',
      cancelled: '已停止',
      failed: '失败',
      thinking: '思考过程',
      reasoning: '推理过程',
    },
    api: {
      unnamedAttachment: '未命名附件',
      requestFailedStatus: (status) => `请求失败（状态码 ${status}）`,
      uploadFailedForFile: (filename) => `上传附件失败：${filename}`,
      deleteUploadFailedForFile: (filename) => `删除附件失败：${filename}`,
      streamRecoveryFailed: '实时连接恢复失败，请稍后重试。',
      invalidStreamEvent: '实时事件格式无效。',
    },
    app: {
      brand: 'DeepAgents',
      title: '对话工作台',
      workspaceAriaLabel: '聊天工作区',
      timelineAriaLabel: '运行状态',
      locale: {
        label: '语言',
        zh: '中文',
        en: 'EN',
      },
      timelineToggle: {
        close: '收起运行面板',
        open: '查看运行面板',
      },
      topbarStatus: {
        running: '当前任务正在处理，回复、工具动作和连接状态会在侧边运行面板里持续更新。',
        completed: '本轮任务已经完成，当前会话里可以直接继续追问或回溯上下文。',
        cancelled: '上一轮运行已手动停止，可以调整提示或附件后重新发起。',
        failed: '上一轮处理被中断，先查看运行面板里的错误，再决定是否重试。',
        idle: '工作台已就绪，左侧管理会话，中间专注对话，运行细节按需查看。',
      },
      auth: {
        eyebrow: '安全登录',
        title: '进入智能工作台',
        copy: '使用管理员账号建立受保护连接，进入稳定的会话与运行工作区。',
        username: '用户名',
        usernamePlaceholder: '请输入管理员用户名',
        password: '密码',
        passwordPlaceholder: '请输入密码',
        hint: '登录后会自动恢复最近会话。',
        login: '进入工作台',
        loading: '登录中…',
        failure: '登录失败，请检查账号信息。',
      },
      deleteSession: {
        fallbackTitle: '当前会话',
        title: '删除会话',
        confirm: '删除',
        cancel: '取消',
        message: (title) => `确定要删除“${title}”吗？此操作无法撤销。`,
      },
      logs: {
        sseConnect: '正在连接实时事件流',
        sseOpen: '实时事件流已连接',
        sseRetry: '实时事件流正在自动恢复',
        sseDrop: '收到无法识别的 SSE 事件',
        sseClosed: '实时事件流在完成后关闭',
        uploadError: '上传附件失败。',
        uploadSuccess: '附件上传完成',
        runStart: '正在创建运行',
        runStartError: '发送失败，请稍后再试。',
        stopRunError: '停止运行失败，请稍后再试。',
      },
      stream: {
        disconnected: '实时连接已关闭。',
        terminalError: '运行异常，实时连接已终止。',
        terminalCancelled: '当前运行已手动停止，实时连接已关闭。',
        terminalCompleted: '本轮输出已结束，实时连接已关闭。',
        retrying: '实时连接短暂中断，正在自动恢复。',
        replayCompleted: '检测到终态事件重放，已同步最终回复。',
        replayCancelled: '运行已被手动停止。',
        cancelledClosed: '运行已手动停止，会话流已正常结束。',
        completedClosed: '最终回复已写入，会话流已正常结束。',
        recoveryFailure: (message) => `实时连接恢复失败：${message}`,
      },
      notices: {
        uploadFailed: '附件上传失败',
        uploadCompleted: '附件上传完成',
        uploadCompletedDetail: (count) => `已上传 ${count} 个附件，将附加到下一次发送。`,
        deleteUploadFailed: '附件删除失败',
        runCreated: '运行创建成功',
        runCreatedDetail: '后端已接受请求，正在建立实时连接。',
        runStartFailed: '运行启动失败',
        stopRunFailed: '停止运行失败',
        stopAlreadyCompleted: '停止请求到达前，本轮处理已经完成。',
        stopAlreadyFailed: '停止请求到达前，本轮处理已经失败。',
        stopped: '已手动停止当前运行。',
      },
    },
    sessionTitles: {
      defaultTitle: '新会话',
      placeholderAliases: [
        'new session',
        'new chat',
        'untitled',
        'untitled session',
        '新会话',
        '新聊天',
        '未命名会话',
      ],
    },
    workspace: {
      sessionEyebrow: '当前会话',
      newSessionTitle: '新会话',
      metrics: {
        messages: '消息',
        assistantMessages: '助手回复',
        pendingAttachments: '待发附件',
      },
      runtimeCopy: {
        running: '当前任务仍在处理中，新的内容会自动追加到下方消息流，完成前不能继续发送下一轮。',
        completed: '本轮处理已经完成，可以立刻继续追问。',
        cancelled: '本轮处理已手动停止，可以调整上下文后重新发起。',
        failed: '本轮处理失败，先查看运行面板再决定是否重试。',
        idle: '对话区已准备就绪，把目标和上下文一次说清就可以开始。',
      },
      loadingDescription: '正在加载会话内容…',
      emptyKicker: '准备开始新的任务',
      emptyTitle: '把目标、背景和限制条件一次交代清楚',
      emptyCopy: '系统会把回复、工具动作和最终结果都收敛到这一条会话里，便于连续协作。',
      composerLabel: '输入内容',
      uploadHint: {
        locked: '当前任务处理中，完成后才能继续发送或追加附件。',
        uploading: '附件上传中…',
        idle: '附件会自动附加到下一次发送。',
      },
      uploadButton: '上传附件',
      removeUpload: '删除附件',
      placeholder: '请输入你的问题，或结合附件说明要处理的任务。',
      composerHint: {
        stop: '当前任务处理中，可直接使用同一按钮停止。',
        send: '按 Ctrl 或 Command + Enter 可快速发送',
      },
      actions: {
        stop: '停止',
        stopping: '停止中…',
        send: '发送',
        sending: '发送中…',
      },
      uploadStatus: {
        submitted: '已提交',
        uploaded: '已上传',
        pending: '待发送',
      },
    },
    sidebar: {
      eyebrow: '会话',
      title: '历史会话',
      currentHint: '切换会话后可继续当前上下文。',
      emptyHint: '新建会话后即可开始对话。',
      newSession: '新建',
      refresh: '刷新',
      loading: '正在加载会话…',
      empty: '还没有历史会话，开始一段新对话吧。',
      unnamed: '未命名会话',
      currentSession: '当前会话',
      continueSession: '点击继续对话',
      deleting: '删除中',
      remove: '移除',
    },
    timeline: {
      eyebrow: '运行面板',
      title: '执行脉络',
      close: '收起',
      empty: '发起提问后，这里会显示本轮处理进度。',
      summary: {
        connection: '连接',
        run: '运行',
        latest: '最近活动',
      },
      connection: {
        open: '已连接',
        connecting: '连接中',
        closed: '已关闭',
        error: '连接异常',
        idle: '未连接',
      },
      copy: {
        open: '已连接实时通道，工具动作和回复会持续滚动进来。',
        connecting: '正在恢复实时连接，界面会在恢复后继续接收进度。',
        error: '实时连接恢复失败，请查看错误后重试。',
        idle: '当前没有活跃运行，新的提问会在这里展示执行过程。',
      },
      labels: {
        runStarted: '运行已启动',
        runCreated: '运行已创建',
        runCreateSuccess: '运行创建成功',
        runStartFailed: '运行启动失败',
        runFailed: '运行失败',
        queued: '排队中',
        running: '处理中',
        processCompleted: '处理完成',
        processFailed: '处理失败',
        finalSaved: '最终回复已写入会话',
        connectionOpened: '实时连接已建立',
        connectionClosed: '实时连接已关闭',
        uploadCompleted: '附件上传完成',
        uploadFailed: '附件上传失败',
        upstreamRunStarted: '开始处理',
        upstreamRunRunning: '正在处理',
        upstreamRunCompleted: '处理完成',
        upstreamRunCancelled: '已手动停止',
        upstreamRunFailed: '处理失败',
        messageDelta: '正在生成回复',
        messageFinalCompleted: '回复生成完成',
        messageFinalUpdated: '回复已更新',
        tool: '工具执行',
        skill: '技能执行',
        sandbox: '沙箱执行',
        connection: '连接状态变更',
        status: '状态更新',
        error: '处理异常',
        progress: '进度更新',
      },
      sourceLabels: {
        '运行已启动': '运行已启动',
        '运行已创建': '运行已创建',
        '运行创建成功': '运行创建成功',
        '运行启动失败': '运行启动失败',
        '运行失败': '运行失败',
        排队中: '排队中',
        处理中: '处理中',
        处理完成: '处理完成',
        处理失败: '处理失败',
        '最终回复已写入会话': '最终回复已写入会话',
        '实时连接已建立': '实时连接已建立',
        '实时连接已关闭': '实时连接已关闭',
        '附件上传完成': '附件上传完成',
        '附件上传失败': '附件上传失败',
        'Run started': '开始处理',
        'Run running': '正在处理',
        'Run completed': '处理完成',
        'Run cancelled': '已手动停止',
        'Run failed': '处理失败',
        'Assistant message finalized': '最终回复已写入会话',
      },
      detail: {
        deltaAggregate: (count) => `已连续接收 ${count} 段回复内容。`,
        deltaStreaming: '正在持续生成回复。',
      },
    },
    messageThread: {
      roles: {
        user: '发起人',
        assistant: '智能助理',
        system: '系统',
      },
      streaming: '正在生成回复…',
      empty: '（空消息）',
    },
    store: {
      session: {
        newTitle: '新会话',
        loadSessionsFailed: '加载会话失败。',
        deleteSessionFailed: '删除会话失败。',
        loadMessagesFailed: '加载消息失败。',
        uploadFailed: '上传附件失败。',
        deleteUploadFailed: '删除附件失败。',
        runFailed: '本轮处理失败。',
      },
      run: {
        statusUpdate: '状态更新',
        connectionUpdate: '连接状态更新',
        processFailed: '处理失败',
        finalSaved: '最终回复已写入会话',
        runStarted: '运行已启动',
        runStartedDetail: '已提交请求，正在建立实时连接。',
        connectionOpened: '实时连接已建立',
        connectionOpenedDetail: '已连接到实时事件流，正在接收运行状态。',
        reconnecting: '实时连接恢复中',
        reconnectingDetail: '实时连接恢复中。',
        disconnected: '实时连接已关闭',
        disconnectedDetail: '实时连接已关闭。',
        completed: '处理完成',
        completedDetail: '本轮处理已完成。',
        cancelled: '运行已手动停止',
        cancelledDetail: '当前运行已手动停止。',
        runFailed: '运行失败',
        deltaAggregate: (count) => `已连续接收 ${count} 段回复内容。`,
      },
    },
  },
  en: {
    common: {
      emptyRecent: 'None',
      info: 'Info',
      idle: 'Idle',
      queued: 'Queued',
      running: 'Running',
      completed: 'Completed',
      cancelling: 'Stopping',
      cancelled: 'Stopped',
      failed: 'Failed',
      thinking: 'Thinking',
      reasoning: 'Reasoning',
    },
    api: {
      unnamedAttachment: 'Unnamed attachment',
      requestFailedStatus: (status) => `Request failed (status ${status})`,
      uploadFailedForFile: (filename) => `Failed to upload attachment: ${filename}`,
      deleteUploadFailedForFile: (filename) => `Failed to delete attachment: ${filename}`,
      streamRecoveryFailed: 'Live connection recovery failed. Please try again.',
      invalidStreamEvent: 'Invalid live event payload.',
    },
    app: {
      brand: 'DeepAgents',
      title: 'Conversation Workspace',
      workspaceAriaLabel: 'Chat workspace',
      timelineAriaLabel: 'Run status',
      locale: {
        label: 'Language',
        zh: '中文',
        en: 'EN',
      },
      timelineToggle: {
        close: 'Hide run panel',
        open: 'Show run panel',
      },
      topbarStatus: {
        running: 'The current task is still running. Replies, tool activity, and connection state continue in the run panel.',
        completed: 'This run is complete. You can keep asking follow-up questions in the same session.',
        cancelled: 'The last run was stopped manually. Adjust the prompt or attachments and start again.',
        failed: 'The last run was interrupted. Review the error in the run panel before retrying.',
        idle: 'The workspace is ready. Manage sessions on the left, focus on chat in the center, and open run details when needed.',
      },
      auth: {
        eyebrow: 'Secure Sign-In',
        title: 'Enter the intelligent workspace',
        copy: 'Sign in with the administrator account to open the protected session and run workspace.',
        username: 'Username',
        usernamePlaceholder: 'Enter the administrator username',
        password: 'Password',
        passwordPlaceholder: 'Enter the password',
        hint: 'Your recent session will be restored after sign-in.',
        login: 'Enter workspace',
        loading: 'Signing in…',
        failure: 'Sign-in failed. Check the account details and try again.',
      },
      deleteSession: {
        fallbackTitle: 'Current session',
        title: 'Delete session',
        confirm: 'Delete',
        cancel: 'Cancel',
        message: (title) => `Delete "${title}"? This action cannot be undone.`,
      },
      logs: {
        sseConnect: 'Connecting to the live event stream',
        sseOpen: 'Live event stream connected',
        sseRetry: 'Live event stream is recovering automatically',
        sseDrop: 'Received an unrecognized SSE payload',
        sseClosed: 'Live event stream closed after completion',
        uploadError: 'Attachment upload failed.',
        uploadSuccess: 'Attachment upload completed',
        runStart: 'Creating run',
        runStartError: 'Send failed. Please try again.',
        stopRunError: 'Failed to stop the run. Please try again.',
      },
      stream: {
        disconnected: 'Live connection closed.',
        terminalError: 'The run failed and the live connection was closed.',
        terminalCancelled: 'The run was stopped and the live connection was closed.',
        terminalCompleted: 'The run completed and the live connection was closed.',
        retrying: 'The live connection was interrupted briefly and is reconnecting.',
        replayCompleted: 'A terminal replay event was detected and the final reply was synchronized.',
        replayCancelled: 'The run was stopped manually.',
        cancelledClosed: 'The run was stopped and the stream closed normally.',
        completedClosed: 'The final reply was saved and the stream closed normally.',
        recoveryFailure: (message) => `Live connection recovery failed: ${message}`,
      },
      notices: {
        uploadFailed: 'Attachment upload failed',
        uploadCompleted: 'Attachment upload completed',
        uploadCompletedDetail: (count) => `${count} attachment(s) uploaded and queued for the next message.`,
        deleteUploadFailed: 'Attachment deletion failed',
        runCreated: 'Run created',
        runCreatedDetail: 'The backend accepted the request and is opening the live connection.',
        runStartFailed: 'Failed to start the run',
        stopRunFailed: 'Failed to stop the run',
        stopAlreadyCompleted: 'The run had already completed before the stop request arrived.',
        stopAlreadyFailed: 'The run had already failed before the stop request arrived.',
        stopped: 'The current run was stopped.',
      },
    },
    sessionTitles: {
      defaultTitle: 'New Session',
      placeholderAliases: [
        'new session',
        'new chat',
        'untitled',
        'untitled session',
        '新会话',
        '新聊天',
        '未命名会话',
      ],
    },
    workspace: {
      sessionEyebrow: 'Current Session',
      newSessionTitle: 'New Session',
      metrics: {
        messages: 'Messages',
        assistantMessages: 'Assistant Replies',
        pendingAttachments: 'Pending Attachments',
      },
      runtimeCopy: {
        running: 'The current task is still running. New content will keep streaming below, and the next turn stays locked until it finishes.',
        completed: 'This run is complete. You can continue immediately.',
        cancelled: 'This run was stopped manually. Adjust the context and start again.',
        failed: 'This run failed. Check the run panel before retrying.',
        idle: 'The chat area is ready. State the goal and context clearly to begin.',
      },
      loadingDescription: 'Loading session content…',
      emptyKicker: 'Ready for a new task',
      emptyTitle: 'Describe the goal, background, and constraints in one pass',
      emptyCopy: 'Replies, tool activity, and final results stay inside this session so collaboration remains continuous.',
      composerLabel: 'Input',
      uploadHint: {
        locked: 'A task is still running. Wait for it to finish before sending more text or attachments.',
        uploading: 'Uploading attachments…',
        idle: 'Attachments are added automatically to the next message.',
      },
      uploadButton: 'Upload attachment',
      removeUpload: 'Remove attachment',
      placeholder: 'Describe the question or task, and reference the attachments when needed.',
      composerHint: {
        stop: 'The task is still running. Use the same button to stop it.',
        send: 'Press Ctrl or Command + Enter to send quickly',
      },
      actions: {
        stop: 'Stop',
        stopping: 'Stopping…',
        send: 'Send',
        sending: 'Sending…',
      },
      uploadStatus: {
        submitted: 'Submitted',
        uploaded: 'Uploaded',
        pending: 'Pending',
      },
    },
    sidebar: {
      eyebrow: 'Sessions',
      title: 'History',
      currentHint: 'Switch sessions to keep working in the current context.',
      emptyHint: 'Create a session to begin the conversation.',
      newSession: 'New',
      refresh: 'Refresh',
      loading: 'Loading sessions…',
      empty: 'There are no saved sessions yet. Start a new conversation.',
      unnamed: 'Untitled session',
      currentSession: 'Current session',
      continueSession: 'Click to continue',
      deleting: 'Deleting',
      remove: 'Remove',
    },
    timeline: {
      eyebrow: 'Run Panel',
      title: 'Execution Trace',
      close: 'Close',
      empty: 'Progress for the current run appears here after you send a prompt.',
      summary: {
        connection: 'Connection',
        run: 'Run',
        latest: 'Latest Activity',
      },
      connection: {
        open: 'Open',
        connecting: 'Connecting',
        closed: 'Closed',
        error: 'Error',
        idle: 'Idle',
      },
      copy: {
        open: 'The live channel is open. Tool activity and replies will keep streaming here.',
        connecting: 'The live connection is recovering and will resume once it reconnects.',
        error: 'Live connection recovery failed. Review the error and retry.',
        idle: 'There is no active run yet. New prompts will show execution details here.',
      },
      labels: {
        runStarted: 'Run started',
        runCreated: 'Run created',
        runCreateSuccess: 'Run created',
        runStartFailed: 'Run start failed',
        runFailed: 'Run failed',
        queued: 'Queued',
        running: 'Running',
        processCompleted: 'Completed',
        processFailed: 'Failed',
        finalSaved: 'Final reply saved',
        connectionOpened: 'Live connection opened',
        connectionClosed: 'Live connection closed',
        uploadCompleted: 'Attachment uploaded',
        uploadFailed: 'Attachment upload failed',
        upstreamRunStarted: 'Processing started',
        upstreamRunRunning: 'Processing',
        upstreamRunCompleted: 'Completed',
        upstreamRunCancelled: 'Stopped',
        upstreamRunFailed: 'Failed',
        messageDelta: 'Generating reply',
        messageFinalCompleted: 'Reply complete',
        messageFinalUpdated: 'Reply updated',
        tool: 'Tool call',
        skill: 'Skill call',
        sandbox: 'Sandbox step',
        connection: 'Connection change',
        status: 'Status update',
        error: 'Processing error',
        progress: 'Progress update',
      },
      sourceLabels: {
        '运行已启动': 'Run started',
        '运行已创建': 'Run created',
        '运行创建成功': 'Run created',
        '运行启动失败': 'Run start failed',
        '运行失败': 'Run failed',
        排队中: 'Queued',
        处理中: 'Running',
        处理完成: 'Completed',
        处理失败: 'Failed',
        '最终回复已写入会话': 'Final reply saved',
        '实时连接已建立': 'Live connection opened',
        '实时连接已关闭': 'Live connection closed',
        '附件上传完成': 'Attachment uploaded',
        '附件上传失败': 'Attachment upload failed',
        'Run started': 'Run started',
        'Run running': 'Running',
        'Run completed': 'Completed',
        'Run cancelled': 'Stopped',
        'Run failed': 'Failed',
        'Assistant message finalized': 'Final reply saved',
      },
      detail: {
        deltaAggregate: (count) => `${count} reply chunks have been received.`,
        deltaStreaming: 'The reply is still streaming.',
      },
    },
    messageThread: {
      roles: {
        user: 'User',
        assistant: 'Assistant',
        system: 'System',
      },
      streaming: 'Generating reply…',
      empty: '(Empty message)',
    },
    store: {
      session: {
        newTitle: 'New Session',
        loadSessionsFailed: 'Failed to load sessions.',
        deleteSessionFailed: 'Failed to delete the session.',
        loadMessagesFailed: 'Failed to load messages.',
        uploadFailed: 'Failed to upload attachments.',
        deleteUploadFailed: 'Failed to delete the attachment.',
        runFailed: 'The run failed.',
      },
      run: {
        statusUpdate: 'Status update',
        connectionUpdate: 'Connection update',
        processFailed: 'Processing failed',
        finalSaved: 'Final reply saved',
        runStarted: 'Run started',
        runStartedDetail: 'The request was submitted and the live connection is opening.',
        connectionOpened: 'Live connection opened',
        connectionOpenedDetail: 'Connected to the live event stream and receiving run updates.',
        reconnecting: 'Reconnecting live stream',
        reconnectingDetail: 'The live connection is reconnecting.',
        disconnected: 'Live connection closed',
        disconnectedDetail: 'The live connection was closed.',
        completed: 'Completed',
        completedDetail: 'The run completed.',
        cancelled: 'Run stopped',
        cancelledDetail: 'The current run was stopped.',
        runFailed: 'Run failed',
        deltaAggregate: (count) => `${count} reply chunks have been received.`,
      },
    },
  },
}

export const localeState = reactive({
  current: resolveInitialLocale(),
})

export const uiCopy = reactive(cloneValue(messages[localeState.current]))

export function setLocale(nextLocale) {
  const normalized = normalizeLocale(nextLocale)
  if (localeState.current === normalized) {
    return
  }
  localeState.current = normalized
  applyLocale(normalized)
  persistLocale(normalized)
}

export function statusText(status) {
  return uiCopy.common[status] || uiCopy.common.idle
}

export function normalizeLocale(value) {
  const locale = String(value || '').toLowerCase()
  return SUPPORTED_LOCALES.includes(locale) ? locale : DEFAULT_LOCALE
}

export function getSupportedLocales() {
  return [...SUPPORTED_LOCALES]
}

function resolveInitialLocale() {
  const persisted = readPersistedLocale()
  if (persisted) {
    return persisted
  }
  return DEFAULT_LOCALE
}

function applyLocale(locale) {
  replaceObject(uiCopy, messages[locale])
}

function persistLocale(locale) {
  if (typeof window === 'undefined' || !window.localStorage) {
    return
  }
  window.localStorage.setItem(STORAGE_KEY, locale)
}

function readPersistedLocale() {
  if (typeof window === 'undefined' || !window.localStorage) {
    return ''
  }
  const value = String(window.localStorage.getItem(STORAGE_KEY) || '').toLowerCase()
  return SUPPORTED_LOCALES.includes(value) ? value : ''
}

function cloneValue(value) {
  if (Array.isArray(value)) {
    return value.map((item) => cloneValue(item))
  }
  if (value && typeof value === 'object') {
    return Object.fromEntries(
      Object.entries(value).map(([key, item]) => [key, cloneValue(item)]),
    )
  }
  return value
}

function replaceObject(target, source) {
  for (const key of Object.keys(target)) {
    if (!(key in source)) {
      delete target[key]
    }
  }

  for (const [key, value] of Object.entries(source)) {
    if (Array.isArray(value)) {
      target[key] = [...value]
      continue
    }

    if (value && typeof value === 'object') {
      const current = target[key]
      if (!current || typeof current !== 'object' || Array.isArray(current)) {
        target[key] = cloneValue(value)
        continue
      }
      replaceObject(current, value)
      continue
    }

    target[key] = value
  }
}

applyLocale(localeState.current)
