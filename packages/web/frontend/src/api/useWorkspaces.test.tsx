import { describe, it, expect, beforeAll, afterAll, afterEach, beforeEach, vi } from "vitest";
import { renderHook, act, cleanup, waitFor } from "@testing-library/react";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { SWRConfig } from "swr";
import type { ReactNode } from "react";
import i18n from "@/i18n";
import { useWorkspaces } from "./useWorkspaces";

// 独立 cache：SWR 全局缓存会把前一个测试的成功数据带进后一个测试（错误用例被去重跳过请求）。
const wrapper = ({ children }: { children: ReactNode }) => (
  <SWRConfig value={{ provider: () => new Map() }}>{children}</SWRConfig>
);

const server = setupServer(
  http.get("/api/workspaces", () => HttpResponse.json([
    { name: "ws-a", scan_type: "whitebox", status: "completed", created_at: 0 },
  ])),
);

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
beforeEach(() => i18n.changeLanguage("zh"));
afterEach(() => { server.resetHandlers(); cleanup(); i18n.changeLanguage("zh"); if (vi.isFakeTimers()) vi.useRealTimers(); });
afterAll(() => server.close());

describe("useWorkspaces", () => {
  it("初始 fetch + loading 转 false + data", async () => {
    const { result } = renderHook(() => useWorkspaces(), { wrapper });
    expect(result.current.loading).toBe(true);
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.data).toHaveLength(1);
  });

  it("5s 轮询触发新 fetch", async () => {
    vi.useFakeTimers();
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    renderHook(() => useWorkspaces(), { wrapper });
    await act(async () => { await vi.advanceTimersByTimeAsync(1); });
    const afterMount = fetchSpy.mock.calls.length;
    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
    expect(fetchSpy.mock.calls.length).toBeGreaterThan(afterMount);
    fetchSpy.mockRestore();
  });

  it("refresh() 手动触发", async () => {
    const { result } = renderHook(() => useWorkspaces(), { wrapper });
    await waitFor(() => expect(result.current.loading).toBe(false));
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    const before = fetchSpy.mock.calls.length;
    await act(async () => { await result.current.refresh(); });
    expect(fetchSpy.mock.calls.length).toBeGreaterThan(before);
    fetchSpy.mockRestore();
  });

  it("fetch 错误 → error 非 null，loading false", async () => {
    server.use(http.get("/api/workspaces", () => HttpResponse.json({ detail: "boom" }, { status: 500 })));
    const { result } = renderHook(() => useWorkspaces(), { wrapper });
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.error).not.toBeNull();
  });

  it("fetch 错误 → error 文案经 i18n 本地化（中文含 status）", async () => {
    server.use(http.get("/api/workspaces", () => HttpResponse.json({ detail: "boom" }, { status: 500 })));
    const { result } = renderHook(() => useWorkspaces(), { wrapper });
    await waitFor(() => expect(result.current.error).not.toBeNull());
    expect(result.current.error).toBe("加载失败（500）");
  });

  it("fetch 错误 → 切英文 error 文案为英文", async () => {
    i18n.changeLanguage("en");
    server.use(http.get("/api/workspaces", () => HttpResponse.json({ detail: "boom" }, { status: 500 })));
    const { result } = renderHook(() => useWorkspaces(), { wrapper });
    await waitFor(() => expect(result.current.error).not.toBeNull());
    expect(result.current.error).toBe("Failed to load (500)");
  });
});
