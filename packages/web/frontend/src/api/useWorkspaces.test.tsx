import { describe, it, expect, beforeAll, afterAll, afterEach, vi } from "vitest";
import { renderHook, act, cleanup, waitFor } from "@testing-library/react";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { useWorkspaces } from "./useWorkspaces";

const server = setupServer(
  http.get("/api/workspaces", () => HttpResponse.json([
    { name: "ws-a", scan_type: "whitebox", status: "completed", created_at: 0 },
  ])),
);

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => { server.resetHandlers(); cleanup(); if (vi.isFakeTimers()) vi.useRealTimers(); });
afterAll(() => server.close());

describe("useWorkspaces", () => {
  it("初始 fetch + loading 转 false + data", async () => {
    const { result } = renderHook(() => useWorkspaces());
    expect(result.current.loading).toBe(true);
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.data).toHaveLength(1);
    expect(result.current.lastUpdated).not.toBeNull();
  });

  it("5s 轮询触发新 fetch", async () => {
    vi.useFakeTimers();
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    renderHook(() => useWorkspaces());
    await act(async () => { await vi.advanceTimersByTimeAsync(1); });
    const afterMount = fetchSpy.mock.calls.length;
    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
    expect(fetchSpy.mock.calls.length).toBeGreaterThan(afterMount);
    fetchSpy.mockRestore();
  });

  it("refresh() 手动触发", async () => {
    const { result } = renderHook(() => useWorkspaces());
    await waitFor(() => expect(result.current.loading).toBe(false));
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    const before = fetchSpy.mock.calls.length;
    await act(async () => { await result.current.refresh(); });
    expect(fetchSpy.mock.calls.length).toBeGreaterThan(before);
    fetchSpy.mockRestore();
  });

  it("fetch 错误 → error 非 null，loading false", async () => {
    server.use(http.get("/api/workspaces", () => HttpResponse.json({ detail: "boom" }, { status: 500 })));
    const { result } = renderHook(() => useWorkspaces());
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.error).not.toBeNull();
  });
});
