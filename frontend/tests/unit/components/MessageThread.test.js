import { renderToString } from "@vue/server-renderer";
import { createSSRApp } from "vue";
import { describe, expect, it, vi } from "vitest";

import MessageThread from "../../../src/components/MessageThread.vue";

vi.mock("../../../src/components/MarkdownContent.vue", () => ({
  default: {
    name: "MarkdownContent",
    props: {
      content: {
        type: String,
        default: "",
      },
    },
    emits: ["content-rendered"],
    template: '<div class="markdown-content">{{ content }}</div>',
  },
}));

const ElScrollbar = {
  name: "ElScrollbar",
  template: '<div class="stub-scrollbar"><slot /></div>',
};
const ElButton = {
  name: "ElButton",
  template: '<button type="button"><slot /></button>',
};

function renderThread(messages) {
  const app = createSSRApp(MessageThread, {
    messages,
    loading: false,
    sessionId: "session-1",
    activeRunSessionId: "",
    runStatus: "idle",
  });
  app.component("ElScrollbar", ElScrollbar);
  app.component("ElButton", ElButton);
  return renderToString(app);
}

describe("MessageThread", () => {
  it("does not render empty assistant rows without content or process logs", async () => {
    const html = await renderThread([
      {
        id: "user-1",
        role: "user",
        content: "这个是什么文件？",
        createdAt: "2026-05-05T00:00:00.000Z",
        attachments: [],
        streaming: false,
      },
      {
        id: "assistant-empty",
        role: "assistant",
        content: "",
        createdAt: "2026-05-05T00:00:01.000Z",
        attachments: [],
        processes: [],
        streaming: false,
      },
    ]);

    expect(html).toContain("这个是什么文件？");
    expect(html).not.toContain("assistant-empty");
    expect(html).not.toContain("（空消息）");
  });

  it("still renders assistant rows that only contain process logs", async () => {
    const html = await renderThread([
      {
        id: "assistant-process",
        role: "assistant",
        content: "",
        createdAt: "2026-05-05T00:00:01.000Z",
        attachments: [],
        processes: [
          {
            id: "tool-1",
            kind: "tool",
            title: "read_file",
            summary: "读取完成",
            status: "completed",
            timestamp: "2026-05-05T00:00:01.000Z",
          },
        ],
        streaming: false,
      },
    ]);

    expect(html).toContain("read_file");
    expect(html).toContain("读取完成");
  });

  it("renders assistant content and process groups in timestamp order", async () => {
    const html = await renderThread([
      {
        id: "assistant-ordered",
        role: "assistant",
        content: "先输出这一段",
        createdAt: "2026-05-05T00:00:01.000Z",
        attachments: [],
        processes: [
          {
            id: "tool-late",
            kind: "tool",
            title: "read_file",
            summary: "读取完成",
            status: "completed",
            timestamp: "2026-05-05T00:00:02.000Z",
          },
        ],
        streaming: false,
      },
    ]);

    expect(html.indexOf("先输出这一段")).toBeLessThan(
      html.indexOf("read_file"),
    );
  });

  it("collapses adjacent mixed process entries into one outer block with nested items", async () => {
    const html = await renderThread([
      {
        id: "assistant-processes",
        role: "assistant",
        content: "",
        createdAt: "2026-05-05T00:00:01.000Z",
        attachments: [],
        processes: [
          {
            id: "tool-1",
            kind: "tool",
            title: "ls",
            summary: "列出目录",
            status: "completed",
            timestamp: "2026-05-05T00:00:01.000Z",
          },
          {
            id: "tool-2",
            kind: "tool",
            title: "read_file",
            summary: "读取文件",
            status: "completed",
            timestamp: "2026-05-05T00:00:02.000Z",
          },
        ],
        streaming: false,
      },
    ]);

    expect(html.match(/class="process-block"/g) || []).toHaveLength(1);
    expect(html.match(/class="process-item"/g) || []).toHaveLength(2);
    expect(html).toContain("中间过程");
    expect(html).toContain("ls");
    expect(html).toContain("read_file");
  });
});
