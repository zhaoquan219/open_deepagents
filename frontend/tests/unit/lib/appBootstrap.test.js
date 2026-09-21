import { describe, expect, it, vi } from "vitest";

import {
  initializeAuthenticatedApp,
  isForeignSessionStreamEnvelope,
} from "../../../src/lib/appBootstrap.js";

describe("initializeAuthenticatedApp", () => {
  it("does not block first paint on initial session message loading", async () => {
    const calls = [];
    const apiClient = {
      getAdminProfile: vi.fn(async () => {
        calls.push(["profile", false]);
      }),
      logout: vi.fn(),
    };
    let isVisible = false;
    const sessionStore = {
      state: { currentSessionId: "session-blocked" },
      loadSessions: vi.fn(async () => {
        calls.push(["loadSessions", isVisible]);
      }),
      selectSession: vi.fn(() => {
        calls.push(["selectSession", isVisible]);
        return new Promise(() => {});
      }),
    };
    const result = await Promise.race([
      initializeAuthenticatedApp({
        apiClient,
        loadRuntimeOptions: vi.fn(async () => {
          calls.push(["runtimeOptions", isVisible]);
        }),
        sessionStore,
        markAuthenticated: () => {
          isVisible = true;
          calls.push(["authenticated", isVisible]);
        },
        markUnauthenticated: () => {
          isVisible = false;
        },
      }),
      new Promise((resolve) => {
        globalThis.setTimeout(() => resolve("blocked"), 20);
      }),
    ]);

    expect(result).toBe(true);
    expect(calls).toEqual([
      ["profile", false],
      ["authenticated", true],
      ["runtimeOptions", true],
      ["loadSessions", true],
      ["selectSession", true],
    ]);
  });

  it("logs out and marks unauthenticated when profile loading fails", async () => {
    const apiClient = {
      getAdminProfile: vi.fn(async () => {
        throw new Error("unauthorized");
      }),
      logout: vi.fn(),
    };
    const markUnauthenticated = vi.fn();

    await expect(
      initializeAuthenticatedApp({
        apiClient,
        loadRuntimeOptions: vi.fn(),
        sessionStore: {
          state: {},
          loadSessions: vi.fn(),
          selectSession: vi.fn(),
        },
        markAuthenticated: vi.fn(),
        markUnauthenticated,
      }),
    ).resolves.toBe(false);

    expect(apiClient.logout).toHaveBeenCalledTimes(1);
    expect(markUnauthenticated).toHaveBeenCalledTimes(1);
  });

  it("accepts only stream events that belong to the requested session", () => {
    expect(
      isForeignSessionStreamEnvelope({ sessionId: "session-1" }, "session-1"),
    ).toBe(false);
    expect(
      isForeignSessionStreamEnvelope({ sessionId: "" }, "session-1"),
    ).toBe(false);
    expect(
      isForeignSessionStreamEnvelope({ sessionId: "session-2" }, "session-1"),
    ).toBe(true);
  });
});
