import { describe, expect, it } from "vitest";
import {
  createInitialRun,
  createRunStore,
  reduceRunState,
} from "../../../src/store/runStore.js";

describe("runStore reducer", () => {
  it("tracks run status without maintaining a side-panel timeline", () => {
    const started = createInitialRun("run-1", "session-1");
    const running = reduceRunState(started, {
      eventId: "evt-1",
      type: "message.final",
      runId: "run-1",
      sessionId: "session-1",
      timestamp: "2026-04-12T14:00:10.000Z",
      status: "running",
    });
    const settled = reduceRunState(running, {
      eventId: "evt-2",
      type: "status",
      runId: "run-1",
      sessionId: "session-1",
      timestamp: "2026-04-12T14:00:11.000Z",
      status: "completed",
    });

    expect(settled).toMatchObject({
      status: "completed",
      connected: false,
      connectionState: "closed",
      lastEventId: "evt-2",
    });
    expect("timeline" in settled).toBe(false);
  });

  it("keeps duplicate stream events from mutating run state twice", () => {
    const store = createRunStore();
    store.beginRun({ runId: "run-2", sessionId: "session-2" });

    const envelope = {
      eventId: "evt-1",
      type: "status",
      runId: "run-2",
      sessionId: "session-2",
      timestamp: "2026-04-12T14:00:11.000Z",
      status: "completed",
    };

    expect(store.consume(envelope)).toBe(true);
    expect(store.consume(envelope)).toBe(false);
    expect(store.state.activeRun.status).toBe("completed");
  });

  it("tracks connection and stop states for composer locking", () => {
    const store = createRunStore();
    store.beginRun({ runId: "run-stop", sessionId: "session-stop" });
    store.markConnected("run-stop");
    store.markCancelling("run-stop");
    store.markCancelled("run-stop");

    expect(store.state.activeRun).toMatchObject({
      runId: "run-stop",
      status: "cancelled",
      connected: false,
      connectionState: "closed",
      lastError: "",
    });
  });

  it("keeps client failures as top-level errors instead of process noise", () => {
    const store = createRunStore();

    store.recordClientIssue({
      sessionId: "session-9",
      label: "附件上传失败",
      detail: "网络中断",
    });
    store.recordClientNotice({});

    expect(store.state.error).toBe("");
    expect(store.state.diagnostics).toEqual([
      expect.objectContaining({
        sessionId: "session-9",
        label: "附件上传失败",
        detail: "网络中断",
        status: "failed",
      }),
    ]);
  });
});
