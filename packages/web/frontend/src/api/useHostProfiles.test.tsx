// useHostProfiles（SWR 迁移，2026-08-17 批次 Task 1）：HOST 档案列表 hook 契约。
// Harness 镜像 useAuthProfiles.test.tsx（msw + 独立 cache wrapper）。
import { describe, it, expect, beforeAll, afterAll, afterEach, vi } from "vitest";
import { renderHook, act, waitFor, cleanup } from "@testing-library/react";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { SWRConfig } from "swr";
import type { ReactNode } from "react";
import { useHostProfiles } from "./useHostProfiles";
import type { HostProfile } from "./types";

const wrapper = ({ children }: { children: ReactNode }) => (
  <SWRConfig value={{ provider: () => new Map() }}>{children}</SWRConfig>
);

// 共享 cache wrapper：去重断言须在同一应用树（单一 SWR cache）语义下验证。
function sharedCacheWrapper() {
  const cache = new Map();
  return { wrapper: ({ children }: { children: ReactNode }) => (
    <SWRConfig value={{ provider: () => cache }}>{children}</SWRConfig>
  ) };
}

const profile: HostProfile = {
  id: "host_1",
  name: "4000",
  source_url: "https://t/hosts",
  mappings: [{ ip: "10.0.0.1", host: "a.example.com" }],
  scope: "workspace",
  created_at: "2026-08-17T00:00:00Z",
  updated_at: "2026-08-17T00:00:00Z",
};

const server = setupServer(
  http.get("/api/workspaces/:ws/host-profiles", () => HttpResponse.json([profile])),
);

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => { server.resetHandlers(); cleanup(); });
afterAll(() => server.close());

describe("useHostProfiles", () => {
  it("初始 fetch：loading true → false，data 到位", async () => {
    const { result } = renderHook(() => useHostProfiles("ws1"), { wrapper });
    expect(result.current.loading).toBe(true);
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.profiles).toHaveLength(1);
    expect(result.current.profiles[0].mappings).toHaveLength(1);
    expect(result.current.error).toBeNull();
  });

  it("fetch 错误 → error 非 null，profiles 空，loading false", async () => {
    server.use(http.get("/api/workspaces/:ws/host-profiles",
      () => HttpResponse.json({ detail: "boom" }, { status: 500 })));
    const { result } = renderHook(() => useHostProfiles("ws1"), { wrapper });
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.error).not.toBeNull();
    expect(result.current.profiles).toEqual([]);
  });

  it("refresh() 手动触发 revalidate", async () => {
    const { result } = renderHook(() => useHostProfiles("ws1"), { wrapper });
    await waitFor(() => expect(result.current.loading).toBe(false));
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    const before = fetchSpy.mock.calls.length;
    await act(async () => { await result.current.refresh(); });
    expect(fetchSpy.mock.calls.length).toBeGreaterThan(before);
    fetchSpy.mockRestore();
  });

  it("同 ws 双 hook 实例（页面 + 表单下拉）共享一份请求", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    const shared = sharedCacheWrapper();
    const { result: r1 } = renderHook(() => useHostProfiles("ws1"), { wrapper: shared.wrapper });
    const { result: r2 } = renderHook(() => useHostProfiles("ws1"), { wrapper: shared.wrapper });
    await waitFor(() => expect(r1.current.loading).toBe(false));
    await waitFor(() => expect(r2.current.loading).toBe(false));
    const hostGets = fetchSpy.mock.calls
      .filter((c) => String(c[0]).includes("/host-profiles")).length;
    expect(hostGets).toBe(1);
    fetchSpy.mockRestore();
  });

  it("workspace undefined → 挂起：不发请求", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    const { result } = renderHook(() => useHostProfiles(undefined), { wrapper });
    expect(result.current.loading).toBe(false);
    expect(result.current.profiles).toEqual([]);
    expect(fetchSpy.mock.calls.filter((c) => String(c[0]).includes("/host-profiles")).length
    ).toBe(0);
    fetchSpy.mockRestore();
  });
});
