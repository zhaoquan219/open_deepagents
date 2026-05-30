import { afterEach, describe, expect, it, vi } from "vitest";

import { copyBlob, copyText } from "../../../src/lib/clipboard.js";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("clipboard helpers", () => {
  it("copies text with the Clipboard API when available", async () => {
    const writeText = vi.fn(async () => {});
    vi.stubGlobal("navigator", { clipboard: { writeText } });

    await expect(copyText("hello")).resolves.toBe("clipboard");

    expect(writeText).toHaveBeenCalledWith("hello");
  });

  it("falls back to a textarea when writeText is unavailable", async () => {
    const execCommand = vi.fn(() => true);
    const textarea = {
      setAttribute: vi.fn(),
      select: vi.fn(),
      style: {},
      value: "",
    };
    const body = {
      appendChild: vi.fn(),
      removeChild: vi.fn(),
    };
    vi.stubGlobal("navigator", {});
    vi.stubGlobal("document", {
      body,
      createElement: vi.fn(() => textarea),
      execCommand,
    });

    await expect(copyText("fallback")).resolves.toBe("fallback");

    expect(execCommand).toHaveBeenCalledWith("copy");
    expect(textarea.value).toBe("fallback");
    expect(body.appendChild).toHaveBeenCalledWith(textarea);
    expect(body.removeChild).toHaveBeenCalledWith(textarea);
  });

  it("falls back to execCommand for PNG blobs when image clipboard is unavailable", async () => {
    const execCommand = vi.fn(() => true);
    const selection = {
      addRange: vi.fn(),
      removeAllRanges: vi.fn(),
    };
    const range = {
      selectNode: vi.fn(),
    };
    const body = {
      appendChild: vi.fn(),
      removeChild: vi.fn(),
    };
    class FakeFileReader {
      readAsDataURL() {
        this.result = "data:image/png;base64,eA==";
        this.onload();
      }
    }
    vi.stubGlobal("navigator", { clipboard: {} });
    vi.stubGlobal("ClipboardItem", undefined);
    vi.stubGlobal("FileReader", FakeFileReader);
    vi.stubGlobal("getSelection", () => selection);
    vi.stubGlobal("document", {
      body,
      createElement: vi.fn((tag) => ({
        appendChild: vi.fn(),
        contentEditable: "",
        setAttribute: vi.fn(),
        src: "",
        style: {},
        tag,
      })),
      createRange: vi.fn(() => range),
      execCommand,
    });

    await expect(
      copyBlob(new globalThis.Blob(["x"]), "image/png"),
    ).resolves.toBe("fallback");

    expect(execCommand).toHaveBeenCalledWith("copy");
    expect(range.selectNode).toHaveBeenCalled();
    expect(body.appendChild).toHaveBeenCalled();
    expect(body.removeChild).toHaveBeenCalled();
  });

  it("copies PNG blobs with the native Clipboard API when supported", async () => {
    const write = vi.fn(async () => {});
    class FakeClipboardItem {
      constructor(items) {
        this.items = items;
      }
    }
    class FakeFileReader {
      readAsDataURL() {
        this.result = "data:image/png;base64,eA==";
        this.onload();
      }
    }
    vi.stubGlobal("navigator", { clipboard: { write }, platform: "Win32" });
    vi.stubGlobal("ClipboardItem", FakeClipboardItem);
    vi.stubGlobal("FileReader", FakeFileReader);

    await expect(
      copyBlob(new globalThis.Blob(["x"]), "image/png"),
    ).resolves.toBe("clipboard");

    expect(write).toHaveBeenCalledWith([expect.any(FakeClipboardItem)]);
    expect(write.mock.calls[0][0][0].items["image/png"]).toBeInstanceOf(
      globalThis.Blob,
    );
    expect(write.mock.calls[0][0][0].items["text/html"]).toBeInstanceOf(
      globalThis.Blob,
    );
  });

  it("falls back to execCommand for PNG blobs on Windows when ClipboardItem is unavailable", async () => {
    const execCommand = vi.fn(() => true);
    const selection = {
      addRange: vi.fn(),
      removeAllRanges: vi.fn(),
    };
    const range = {
      selectNode: vi.fn(),
    };
    const body = {
      appendChild: vi.fn(),
      removeChild: vi.fn(),
    };
    class FakeFileReader {
      readAsDataURL() {
        this.result = "data:image/png;base64,eA==";
        this.onload();
      }
    }
    vi.stubGlobal("navigator", { clipboard: {}, platform: "Win32" });
    vi.stubGlobal("ClipboardItem", undefined);
    vi.stubGlobal("FileReader", FakeFileReader);
    vi.stubGlobal("getSelection", () => selection);
    vi.stubGlobal("document", {
      body,
      createElement: vi.fn((tag) => ({
        appendChild: vi.fn(),
        contentEditable: "",
        setAttribute: vi.fn(),
        src: "",
        style: {},
        tag,
      })),
      createRange: vi.fn(() => range),
      execCommand,
    });

    await expect(
      copyBlob(new globalThis.Blob(["x"]), "image/png"),
    ).resolves.toBe("fallback");
  });

  it("uses an HTML image clipboard fallback when native PNG clipboard is unsupported", async () => {
    const write = vi.fn(async () => {});
    class FakeClipboardItem {
      static supports(type) {
        return type === "text/html";
      }

      constructor(items) {
        this.items = items;
      }
    }
    class FakeFileReader {
      readAsDataURL() {
        this.result = "data:image/png;base64,eA==";
        this.onload();
      }
    }
    vi.stubGlobal("navigator", { clipboard: { write }, platform: "Win32" });
    vi.stubGlobal("ClipboardItem", FakeClipboardItem);
    vi.stubGlobal("FileReader", FakeFileReader);

    await expect(
      copyBlob(new globalThis.Blob(["x"]), "image/png"),
    ).resolves.toBe("clipboard");

    expect(write).toHaveBeenCalledWith([expect.any(FakeClipboardItem)]);
    expect(write.mock.calls[0][0][0].items["text/html"]).toBeInstanceOf(
      globalThis.Blob,
    );
  });
});
