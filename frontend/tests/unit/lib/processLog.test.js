import { afterEach, describe, expect, it, vi } from "vitest";
import {
  groupProcessLogs,
  logEntryFromEnvelope,
  thinkingEntriesFromContent,
  visibleAssistantContent,
} from "../../../src/lib/processLog.js";

describe("processLog", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("extracts thinking into process logs and keeps the visible answer clean", () => {
    const content = "<think>先分析问题</think>\n\n最终答案";

    expect(visibleAssistantContent(content)).toBe("最终答案");
    expect(
      thinkingEntriesFromContent(content, "2026-05-05T00:00:00.000Z"),
    ).toEqual([
      expect.objectContaining({
        kind: "thinking",
        title: "思考过程",
        summary: "先分析问题",
      }),
    ]);
  });

  it("hides skill-loading events by default", () => {
    const entry = logEntryFromEnvelope({
      eventId: "evt-skill",
      type: "skill",
      label: "skill.completed",
      detail: "1 skills ready",
      status: "completed",
      timestamp: "2026-05-05T00:00:01.000Z",
      data: {
        skills: [
          {
            name: "web-research",
            description: "Search and synthesize web references",
          },
        ],
      },
    });

    expect(entry).toBeNull();
  });

  it("summarizes skill metadata when internal logs are enabled", () => {
    vi.stubEnv("VITE_DEEPAGENTS_SHOW_INTERNAL_EVENTS", "true");

    const entry = logEntryFromEnvelope({
      eventId: "evt-skill",
      type: "skill",
      label: "skill.completed",
      detail: "1 skills ready",
      status: "completed",
      timestamp: "2026-05-05T00:00:01.000Z",
      data: {
        skills: [
          {
            name: "web-research",
            description: "Search and synthesize web references",
          },
        ],
      },
    });

    expect(entry).toMatchObject({
      kind: "step",
      title: "已加载 1 个技能",
      summary: "web-research: Search and synthesize web references",
    });
  });

  it("keeps sandbox command input and output together", () => {
    const entry = logEntryFromEnvelope({
      eventId: "evt-sandbox",
      type: "sandbox",
      label: "sandbox.completed",
      detail: "execute",
      status: "completed",
      timestamp: "2026-05-05T00:00:02.000Z",
      data: {
        input: { command: "python script.py" },
        output: { stdout: "ok", stderr: "" },
      },
    });

    expect(entry.title).toBe("python script.py");
    expect(entry.summary).toContain("输入: python script.py");
    expect(entry.summary).toContain("输出:");
    expect(entry.summary).toContain("ok");
  });

  it("hides tool start events by default to avoid duplicate process rows", () => {
    const entry = logEntryFromEnvelope({
      eventId: "evt-tool-start",
      type: "tool",
      label: "tool.started",
      detail: "read_file",
      status: "in_progress",
      timestamp: "2026-05-05T00:00:02.000Z",
      data: { input: { path: "/workspace/main/file.txt" } },
    });

    expect(entry).toBeNull();
  });

  it("shows tool parameters together with completed output", () => {
    const entry = logEntryFromEnvelope({
      eventId: "evt-tool-completed",
      type: "tool",
      label: "tool.completed",
      detail: "read_file",
      status: "completed",
      timestamp: "2026-05-05T00:00:02.000Z",
      data: {
        input: { path: "/workspace/main/file.txt" },
        output: { text: "file contents" },
      },
    });

    expect(entry.summary).toContain("输入:");
    expect(entry.summary).toContain("/workspace/main/file.txt");
    expect(entry.summary).toContain("输出: file contents");
  });

  it("keeps subagent and error events visible while internal logs are hidden", () => {
    const subagent = logEntryFromEnvelope({
      eventId: "evt-subagent",
      type: "subagent",
      label: "subagent.completed",
      detail: "task",
      status: "completed",
      timestamp: "2026-05-05T00:00:03.000Z",
      data: { input: { subagent_type: "code-reviewer" }, output: "approved" },
    });
    const error = logEntryFromEnvelope({
      eventId: "evt-error",
      type: "error",
      label: "run.failed",
      detail: "boom",
      status: "failed",
      timestamp: "2026-05-05T00:00:04.000Z",
      data: { error: "boom" },
    });

    expect(subagent).toMatchObject({
      kind: "subagent",
      title: "code-reviewer",
    });
    expect(error).toMatchObject({ kind: "error", title: "运行异常" });
  });

  it("folds adjacent intermediate events into one chronological process group", () => {
    const groups = groupProcessLogs([
      {
        id: "1",
        kind: "thinking",
        title: "思考过程",
        summary: "A",
        timestamp: "2026-05-05T00:00:00Z",
      },
      {
        id: "2",
        kind: "thinking",
        title: "思考过程",
        summary: "B",
        timestamp: "2026-05-05T00:00:01Z",
      },
      {
        id: "3",
        kind: "sandbox",
        title: "python script.py",
        summary: "ok",
        timestamp: "2026-05-05T00:00:02Z",
      },
    ]);

    expect(groups).toHaveLength(1);
    expect(groups[0]).toMatchObject({
      kind: "process",
      title: "中间过程",
      items: expect.arrayContaining([
        expect.objectContaining({ summary: "A" }),
      ]),
    });
    expect(groups[0].items).toHaveLength(3);
  });

  it("drops empty thinking blocks before rendering process logs", () => {
    expect(
      thinkingEntriesFromContent(
        "<think>  </think>\n\n完成",
        "2026-05-05T00:00:00Z",
      ),
    ).toEqual([]);
  });

  it("keeps adjacent mixed tool history under one intermediate process group after refresh", () => {
    const groups = groupProcessLogs([
      {
        id: "1",
        kind: "tool",
        title: "read_file",
        summary: "input",
        timestamp: "2026-05-05T00:00:00Z",
      },
      {
        id: "2",
        kind: "tool",
        title: "read_file",
        summary: "output",
        timestamp: "2026-05-05T00:00:01Z",
      },
      {
        id: "3",
        kind: "tool",
        title: "grep",
        summary: "matches",
        timestamp: "2026-05-05T00:00:02Z",
      },
    ]);

    expect(groups).toHaveLength(1);
    expect(groups[0]).toMatchObject({ kind: "process", title: "中间过程" });
    expect(groups[0].items.map((item) => item.title)).toEqual([
      "read_file",
      "read_file",
      "grep",
    ]);
  });

  it("preserves caller event order instead of sorting by timestamp", () => {
    const groups = groupProcessLogs([
      {
        id: "later",
        kind: "tool",
        title: "first emitted",
        summary: "A",
        timestamp: "2026-05-05T00:00:02Z",
      },
      {
        id: "earlier",
        kind: "tool",
        title: "second emitted",
        summary: "B",
        timestamp: "2026-05-05T00:00:01Z",
      },
    ]);

    expect(groups[0].items.map((item) => item.title)).toEqual([
      "first emitted",
      "second emitted",
    ]);
  });
});
