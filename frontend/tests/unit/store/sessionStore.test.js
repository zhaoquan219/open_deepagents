import { afterEach, describe, expect, it, vi } from "vitest";
import {
  createSessionStore,
  appendProcessEvent,
  finalizeAssistantMessage,
  mergeAssistantDelta,
  reconcileMessages,
  settleStreamingMessage,
} from "../../../src/store/sessionStore.js";
import { normalizeStreamEnvelope } from "../../../src/lib/sseContract.js";

describe("sessionStore transcript helpers", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("merges assistant deltas into a transient streaming message", () => {
    const initial = [];
    const firstPass = mergeAssistantDelta(initial, {
      runId: "run-1",
      delta: "Hello",
    });
    const secondPass = mergeAssistantDelta(firstPass, {
      runId: "run-1",
      delta: " world",
    });

    expect(secondPass).toHaveLength(1);
    expect(secondPass[0]).toMatchObject({
      id: "stream:run-1",
      content: "Hello world",
      streaming: true,
    });
  });

  it("replaces the transient message with the finalized assistant row", () => {
    const streaming = appendProcessEvent(
      mergeAssistantDelta([], { runId: "run-2", delta: "Partial" }),
      {
        runId: "run-2",
        event: {
          id: "tool-1",
          kind: "tool",
          title: "search",
          summary: "looked up docs",
          status: "completed",
        },
      },
    );
    const finalized = finalizeAssistantMessage(streaming, {
      runId: "run-2",
      message: {
        id: "msg-final",
        content: "Final answer",
      },
    });

    expect(finalized).toEqual([
      expect.objectContaining({
        id: "msg-final",
        content: "Final answer",
        streaming: false,
        processes: [
          expect.objectContaining({
            id: "tool-1",
            title: "search",
          }),
        ],
      }),
    ]);
  });

  it("settles a streaming assistant row when the run completes without another final message", () => {
    const streaming = mergeAssistantDelta([], {
      runId: "run-terminal",
      delta: "完成内容",
    });
    const settled = settleStreamingMessage(streaming, {
      runId: "run-terminal",
    });

    expect(settled).toEqual([
      expect.objectContaining({
        id: "stream:run-terminal",
        content: "完成内容",
        streaming: false,
      }),
    ]);
  });

  it("adds key process events to the assistant message instead of the side panel only", () => {
    const apiClient = {
      createSession: vi.fn(),
      deleteSession: vi.fn(),
      getSessionMessages: vi.fn(async () => []),
      listSessions: vi.fn(),
      uploadFiles: vi.fn(),
    };
    const store = createSessionStore(apiClient);

    store.consumeRunEvent({
      eventId: "evt-tool",
      type: "tool",
      runId: "run-tool",
      sessionId: "session-tool",
      timestamp: "2026-05-04T14:00:00.000Z",
      status: "completed",
      label: "tool.completed",
      detail: "search_docs",
      data: {
        tool_name: "search_docs",
        output: { text: "found 2 matches" },
      },
    });

    expect(store.state.messagesBySession["session-tool"]).toEqual([
      expect.objectContaining({
        id: "stream:run-tool",
        role: "assistant",
        processes: [
          expect.objectContaining({
            id: "evt-tool",
            kind: "tool",
            title: "search_docs",
            summary: expect.stringContaining("found 2 matches"),
            status: "completed",
          }),
        ],
      }),
    ]);
  });

  it("updates an existing assistant row when the same message id is replayed", () => {
    const finalized = finalizeAssistantMessage(
      [
        {
          id: "msg-final",
          role: "assistant",
          content: "旧内容",
          createdAt: "2026-04-13T10:00:00.000Z",
          attachments: [],
          streaming: false,
        },
      ],
      {
        runId: "run-2",
        message: {
          id: "msg-final",
          content: "新内容",
        },
      },
    );

    expect(finalized).toEqual([
      expect.objectContaining({
        id: "msg-final",
        runId: "run-2",
        content: "新内容",
        streaming: false,
      }),
    ]);
  });

  it("keeps later process events on the finalized assistant row for the same run", () => {
    const finalized = finalizeAssistantMessage(
      mergeAssistantDelta([], { runId: "run-log", delta: "完成" }),
      {
        runId: "run-log",
        message: {
          id: "msg-log",
          content: "完成",
        },
      },
    );

    const withLateProcess = appendProcessEvent(finalized, {
      runId: "run-log",
      event: {
        id: "tool-late",
        kind: "tool",
        title: "read_file",
        summary: "读取完成",
        status: "completed",
      },
    });

    expect(withLateProcess).toEqual([
      expect.objectContaining({
        id: "msg-log",
        processes: [
          expect.objectContaining({ id: "tool-late", title: "read_file" }),
        ],
      }),
    ]);
  });

  it("attaches live process events to the latest assistant row for that run", () => {
    const first = finalizeAssistantMessage(
      mergeAssistantDelta([], { runId: "run-order", delta: "先搜索" }),
      {
        runId: "run-order",
        message: {
          id: "msg-1",
          content: "先搜索",
        },
      },
    );
    const withFirstTools = appendProcessEvent(first, {
      runId: "run-order",
      event: {
        id: "tool-1",
        kind: "tool",
        title: "search",
        summary: "done",
        status: "completed",
      },
    });
    const second = finalizeAssistantMessage(withFirstTools, {
      runId: "run-order",
      message: {
        id: "msg-2",
        content: "再搜索",
      },
    });
    const withSecondTools = appendProcessEvent(second, {
      runId: "run-order",
      event: {
        id: "tool-2",
        kind: "tool",
        title: "read_file",
        summary: "done",
        status: "completed",
      },
    });

    expect(withSecondTools).toEqual([
      expect.objectContaining({
        id: "msg-1",
        processes: [expect.objectContaining({ id: "tool-1" })],
      }),
      expect.objectContaining({
        id: "msg-2",
        processes: [expect.objectContaining({ id: "tool-2" })],
      }),
    ]);
  });

  it("consumes finalized events even when the payload only contains a top-level message", () => {
    const apiClient = {
      createSession: vi.fn(),
      deleteSession: vi.fn(),
      getSessionMessages: vi.fn(async () => []),
      listSessions: vi.fn(),
      uploadFiles: vi.fn(),
    };
    const store = createSessionStore(apiClient);

    store.state.messagesBySession["session-1"] = mergeAssistantDelta([], {
      runId: "run-8",
      delta: "片段",
    });
    store.consumeRunEvent({
      type: "message.final",
      runId: "run-8",
      sessionId: "session-1",
      timestamp: "2026-04-13T10:00:00.000Z",
      message: {
        id: "msg-8",
        role: "assistant",
        content: "完整回复",
      },
      data: {},
    });

    expect(store.state.messagesBySession["session-1"]).toEqual([
      expect.objectContaining({
        id: "msg-8",
        content: "完整回复",
        streaming: false,
      }),
    ]);
  });

  it("covers the basic chat path from optimistic user message to final assistant reply", () => {
    const apiClient = {
      createSession: vi.fn(),
      deleteSession: vi.fn(),
      getSessionMessages: vi.fn(async () => []),
      listSessions: vi.fn(),
      uploadFiles: vi.fn(),
    };
    const store = createSessionStore(apiClient);

    store.addOptimisticUserMessage("session-1", "你好");
    store.consumeRunEvent({
      type: "message.delta",
      runId: "run-chat",
      sessionId: "session-1",
      delta: "你",
    });
    store.consumeRunEvent({
      type: "message.final",
      runId: "run-chat",
      sessionId: "session-1",
      timestamp: "2026-04-29T14:35:00.000Z",
      message: {
        id: "msg-chat",
        role: "assistant",
        content: "你好，我在。",
      },
      data: {},
    });

    expect(store.state.messagesBySession["session-1"]).toEqual([
      expect.objectContaining({ role: "user", content: "你好" }),
      expect.objectContaining({
        id: "msg-chat",
        role: "assistant",
        content: "你好，我在。",
      }),
    ]);
  });

  it("keeps multiple finalized assistant outputs for one run instead of overwriting them", () => {
    const apiClient = {
      createSession: vi.fn(),
      deleteSession: vi.fn(),
      getSessionMessages: vi.fn(async () => []),
      listSessions: vi.fn(),
      uploadFiles: vi.fn(),
    };
    const store = createSessionStore(apiClient);

    store.consumeRunEvent({
      eventId: "evt-final-1",
      type: "message.final",
      runId: "run-interleaved",
      sessionId: "session-1",
      timestamp: "2026-05-05T00:00:01.000Z",
      message: { role: "assistant", content: "准备调用工具" },
      data: {},
    });
    store.consumeRunEvent({
      eventId: "evt-tool",
      type: "sandbox",
      runId: "run-interleaved",
      sessionId: "session-1",
      timestamp: "2026-05-05T00:00:02.000Z",
      status: "completed",
      label: "sandbox.completed",
      detail: "execute",
      data: { input: { command: "echo ok" }, output: { stdout: "ok" } },
    });
    store.consumeRunEvent({
      eventId: "evt-final-2",
      type: "message.final",
      runId: "run-interleaved",
      sessionId: "session-1",
      timestamp: "2026-05-05T00:00:03.000Z",
      message: { role: "assistant", content: "工具完成" },
      data: {},
    });

    expect(store.state.messagesBySession["session-1"]).toEqual([
      expect.objectContaining({
        id: "final:run-interleaved:evt-final-1",
        content: "准备调用工具",
        processes: [expect.objectContaining({ title: "echo ok" })],
      }),
      expect.objectContaining({
        id: "final:run-interleaved:evt-final-2",
        content: "工具完成",
      }),
    ]);
  });

  it("restores the selected session after a page refresh", async () => {
    const storage = {
      getItem: vi.fn(() => "session-2"),
      setItem: vi.fn(),
      removeItem: vi.fn(),
    };
    vi.stubGlobal("window", {
      localStorage: storage,
    });
    const apiClient = {
      createSession: vi.fn(),
      deleteSession: vi.fn(),
      getSessionMessages: vi.fn(async () => []),
      listSessions: vi.fn(async () => [
        {
          id: "session-1",
          title: "最近会话",
          updatedAt: "2026-05-05T00:00:02Z",
        },
        {
          id: "session-2",
          title: "当前会话",
          updatedAt: "2026-05-05T00:00:01Z",
        },
      ]),
      uploadFiles: vi.fn(),
    };
    const store = createSessionStore(apiClient);

    await store.loadSessions();

    expect(store.state.currentSessionId).toBe("session-2");
    expect(storage.setItem).toHaveBeenCalledWith(
      "deepagents.currentSessionId",
      "session-2",
    );
  });

  it("ignores nested runtime completion noise and keeps the real assistant final reply", () => {
    const apiClient = {
      createSession: vi.fn(),
      deleteSession: vi.fn(),
      getSessionMessages: vi.fn(async () => []),
      listSessions: vi.fn(),
      uploadFiles: vi.fn(),
    };
    const store = createSessionStore(apiClient);
    store.addOptimisticUserMessage("session-1", "请直接回复已修复");

    const payloads = [
      {
        event_id: "evt-1",
        type: "status",
        run_id: "run-1",
        session_id: "session-1",
        timestamp: "2026-04-29T15:29:57Z",
        label: "run.completed",
        detail: "{'skills_metadata': []}",
        data: { status: "completed", node: "SkillsMiddleware.before_agent" },
      },
      {
        event_id: "evt-2",
        type: "message.delta",
        run_id: "run-1",
        session_id: "session-1",
        timestamp: "2026-04-29T15:29:58Z",
        label: "assistant.delta",
        detail: "已",
        data: { delta: "已" },
      },
      {
        event_id: "evt-3",
        type: "message.delta",
        run_id: "run-1",
        session_id: "session-1",
        timestamp: "2026-04-29T15:29:59Z",
        label: "assistant.delta",
        detail: "修复",
        data: { delta: "修复" },
      },
      {
        event_id: "evt-4",
        type: "message.final",
        run_id: "run-1",
        session_id: "session-1",
        timestamp: "2026-04-29T15:30:00Z",
        label: "assistant.message",
        detail: "已修复",
        data: {
          message: {
            id: "msg-final",
            role: "assistant",
            content: "已修复",
          },
        },
      },
    ];

    for (const payload of payloads) {
      const envelope = normalizeStreamEnvelope(payload);
      if (envelope) {
        store.consumeRunEvent(envelope);
      }
    }

    expect(store.state.messagesBySession["session-1"]).toEqual([
      expect.objectContaining({ role: "user", content: "请直接回复已修复" }),
      expect.objectContaining({
        id: "msg-final",
        role: "assistant",
        content: "已修复",
      }),
    ]);
  });

  it("preserves newer local messages when a session refresh returns a stale prefix", () => {
    const reconciled = reconcileMessages(
      [
        {
          id: "user-1",
          role: "user",
          content: "看下你本地有哪些文件？",
          attachments: [],
        },
        {
          id: "assistant-1",
          role: "assistant",
          content: "当前目录为空，没有文件。",
          attachments: [],
        },
      ],
      [
        {
          id: "server-user-1",
          role: "user",
          content: "看下你本地有哪些文件？",
          attachments: [],
        },
      ],
    );

    expect(reconciled).toEqual([
      expect.objectContaining({
        role: "user",
        content: "看下你本地有哪些文件？",
      }),
      expect.objectContaining({
        role: "assistant",
        content: "当前目录为空，没有文件。",
      }),
    ]);
  });

  it("merges a stale fetch without dropping local assistant output", async () => {
    const apiClient = {
      createSession: vi.fn(),
      deleteSession: vi.fn(async () => null),
      getSessionMessages: vi.fn(async () => [
        {
          id: "server-user-1",
          role: "user",
          content: "第一问",
          attachments: [],
        },
      ]),
      listSessions: vi.fn(),
      uploadFiles: vi.fn(),
    };
    const store = createSessionStore(apiClient);
    store.state.currentSessionId = "session-1";
    store.state.messagesBySession["session-1"] = [
      { id: "local-user-1", role: "user", content: "第一问", attachments: [] },
      {
        id: "local-assistant-1",
        role: "assistant",
        content: "第一答",
        attachments: [],
      },
    ];

    await store.selectSession("session-1");

    expect(store.state.messagesBySession["session-1"]).toEqual([
      expect.objectContaining({ role: "user", content: "第一问" }),
      expect.objectContaining({ role: "assistant", content: "第一答" }),
    ]);
  });

  it("deletes a session and clears its local transcript state", async () => {
    const apiClient = {
      createSession: vi.fn(),
      deleteSession: vi.fn(async () => null),
      getSessionMessages: vi.fn(async () => []),
      listSessions: vi.fn(),
      uploadFiles: vi.fn(),
    };
    const store = createSessionStore(apiClient);

    store.state.sessions = [
      {
        id: "session-1",
        title: "会话一",
        updatedAt: "2026-04-13T00:00:00Z",
        status: "idle",
      },
      {
        id: "session-2",
        title: "会话二",
        updatedAt: "2026-04-12T00:00:00Z",
        status: "idle",
      },
    ];
    store.state.currentSessionId = "session-1";
    store.state.messagesBySession["session-1"] = [
      { id: "msg-1", content: "hello" },
    ];

    await store.deleteSession("session-1");

    expect(apiClient.deleteSession).toHaveBeenCalledWith("session-1");
    expect(store.state.sessions.map((session) => session.id)).toEqual([
      "session-2",
    ]);
    expect(store.state.messagesBySession["session-1"]).toBeUndefined();
    expect(store.state.currentSessionId).toBe("session-2");
  });

  it("distills a placeholder session title once and preserves it on later prompts", () => {
    const apiClient = {
      createSession: vi.fn(),
      deleteSession: vi.fn(),
      getSessionMessages: vi.fn(async () => []),
      listSessions: vi.fn(),
      uploadFiles: vi.fn(),
    };
    const store = createSessionStore(apiClient);

    store.state.sessions = [
      {
        id: "session-1",
        title: "新会话",
        updatedAt: "2026-04-13T00:00:00Z",
        status: "idle",
      },
    ];

    store.addOptimisticUserMessage(
      "session-1",
      "第一行标题候选\n第二行不应进入标题",
    );
    expect(store.state.sessions[0].title).toBe("第一行标题候选");

    store.addOptimisticUserMessage("session-1", "第二条消息不能覆盖已有标题");
    expect(store.state.sessions[0].title).toBe("第一行标题候选");
  });

  it("also distills the first prompt for an English placeholder title", () => {
    const apiClient = {
      createSession: vi.fn(),
      deleteSession: vi.fn(),
      getSessionMessages: vi.fn(async () => []),
      listSessions: vi.fn(),
      uploadFiles: vi.fn(),
    };
    const store = createSessionStore(apiClient);

    store.state.sessions = [
      {
        id: "session-1",
        title: "New session",
        updatedAt: "2026-04-13T00:00:00Z",
        status: "idle",
      },
    ];

    store.addOptimisticUserMessage("session-1", "First prompt becomes title");

    expect(store.state.sessions[0].title).toBe("First prompt becomes title");
  });

  it("clears pending uploads after a successful submission without stripping the user message attachments", () => {
    const apiClient = {
      createSession: vi.fn(),
      deleteSession: vi.fn(),
      getSessionMessages: vi.fn(async () => []),
      listSessions: vi.fn(),
      uploadFiles: vi.fn(),
    };
    const store = createSessionStore(apiClient);

    store.state.pendingUploadsBySession["session-1"] = [
      { id: "upload-1", name: "notes.txt", size: 12, status: "uploaded" },
    ];

    store.addOptimisticUserMessage("session-1", "请看下这个文件里有什么");
    store.clearPendingUploads("session-1");

    expect(store.getPendingUploads("session-1")).toEqual([]);
    expect(store.state.messagesBySession["session-1"]).toEqual([
      expect.objectContaining({
        role: "user",
        attachments: [
          { id: "upload-1", name: "notes.txt", size: 12, status: "uploaded" },
        ],
      }),
    ]);
  });

  it("removes a pending upload from local state after the backend delete succeeds", async () => {
    const apiClient = {
      createSession: vi.fn(),
      deleteSession: vi.fn(),
      deleteUpload: vi.fn(async () => null),
      getSessionMessages: vi.fn(async () => []),
      listSessions: vi.fn(),
      uploadFiles: vi.fn(),
    };
    const store = createSessionStore(apiClient);

    store.state.pendingUploadsBySession["session-1"] = [
      { id: "upload-1", name: "notes.txt", size: 12, status: "uploaded" },
      { id: "upload-2", name: "spec.pdf", size: 24, status: "uploaded" },
    ];

    const result = await store.deletePendingUpload("session-1", "upload-1");

    expect(result).toEqual({ ok: true });
    expect(apiClient.deleteUpload).toHaveBeenCalledWith("upload-1");
    expect(store.getPendingUploads("session-1")).toEqual([
      { id: "upload-2", name: "spec.pdf", size: 24, status: "uploaded" },
    ]);
    expect(store.state.uploadError).toBe("");
  });

  it("clears stale upload errors when session context changes or uploads are consumed", async () => {
    const apiClient = {
      createSession: vi.fn(async () => ({
        id: "session-new",
        title: "新会话",
        updatedAt: "2026-04-13T00:00:00Z",
      })),
      deleteSession: vi.fn(),
      getSessionMessages: vi.fn(async () => []),
      listSessions: vi.fn(),
      uploadFiles: vi.fn(),
    };
    const store = createSessionStore(apiClient);

    store.state.uploadError = "File too large";
    await store.createSession();
    expect(store.state.uploadError).toBe("");

    store.state.uploadError = "File too large";
    await store.selectSession("session-new");
    expect(store.state.uploadError).toBe("");

    store.state.uploadError = "File too large";
    store.clearPendingUploads("session-new");
    expect(store.state.uploadError).toBe("");
  });

  it("searches sessions by keyword and exposes the visible list", async () => {
    const apiClient = {
      createSession: vi.fn(),
      deleteSession: vi.fn(),
      getSessionMessages: vi.fn(async () => []),
      listSessions: vi.fn(async () => [
        {
          id: "session-2",
          title: "预算复盘",
          updatedAt: "2026-05-05T00:00:01Z",
        },
      ]),
      uploadFiles: vi.fn(),
    };
    const store = createSessionStore(apiClient);
    store.state.sessions = [
      { id: "session-1", title: "会话一", updatedAt: "2026-05-05T00:00:02Z" },
      { id: "session-2", title: "预算复盘", updatedAt: "2026-05-05T00:00:01Z" },
    ];

    expect(store.getVisibleSessions().map((session) => session.id)).toEqual([
      "session-1",
      "session-2",
    ]);

    await store.searchSessions("预算");

    expect(apiClient.listSessions).toHaveBeenCalledWith({ query: "预算" });
    expect(store.state.searchQuery).toBe("预算");
    expect(store.getVisibleSessions().map((session) => session.id)).toEqual([
      "session-2",
    ]);

    await store.searchSessions("   ");

    expect(store.state.searchQuery).toBe("");
    expect(store.state.searchResults).toEqual([]);
    expect(store.getVisibleSessions().map((session) => session.id)).toEqual([
      "session-1",
      "session-2",
    ]);
    expect(apiClient.listSessions).toHaveBeenCalledTimes(1);
  });
});
