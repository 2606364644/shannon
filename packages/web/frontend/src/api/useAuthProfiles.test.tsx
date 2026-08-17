// useAuthProfiles（SWR 迁移，2026-08-17 批次 Task 1）：认证档案列表 hook 契约。
// Harness 镜像 useWorkspaces.test.tsx（msw + 独立 cache wrapper）。
// 核心断言：①初始 fetch ②error 语义 ③refresh 触发 revalidate
// ④同 key 双实例单请求（页面与下拉共享缓存的基石）⑤workspace 未定时挂起不发请求。
import { describe, it, expect, beforeAll, afterAll, afterEach, beforeEach, vi } from "vitest";
import { renderHook, act, waitFor, cleanup } from "@testing-library/react";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { SWRConfig } from "swr";
import type { ReactNode } from "react";
import { useAuthProfiles } from "./useAuthProfiles";
import type { AuthProfile } from "./types";

// 默认 wrapper：每 renderHook 独立 cache（测试间隔离）。
const wrapper = ({ children }: { children: ReactNode }) => (
  <SWRConfig value={{ provider: () => new Map() }}>{children}</SWRConfig>
);

// 共享 cache wrapper 工厂：同一次 renderHook 调用与跨 hook 共用同一 Map——
// 模拟真实应用树（全局单一 SWR cache），“同 key 去重”只在此语义下成立。
function sharedCacheWrapper() {
  const cache = new Map();
  return { wrapper: ({ children }: { children: ReactNode }) => (
    <SWRConfig value={{ provider: () => cache }}>{children}</SWRConfig>
  ) };
}

const profile: AuthProfile = {
  id: "prof_1",
  name: "NG",
  login_url: "http://t/",
  login_type: "form",
  credentials: [],
};

const server = setupServer(
  http.get("/api/workspaces/:ws/auth-profiles", () => HttpResponse.json([profile])),
);

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => { server.resetHandlers(); cleanup(); });
afterAll(() => server.close());

describe("useAuthProfiles", () => {
  beforeEach(() => { vi.clearAllMocks(); });

  it("初始 fetch：loading true → false，data 到位", async () => {
    const { result } = renderHook(() => useAuthProfiles("ws1"), { wrapper });
    expect(result.current.loading).toBe(true);
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.profiles).toHaveLength(1);
    expect(result.current.profiles[0].name).toBe("NG");
    expect(result.current.error).toBeNull();
  });

  it("fetch 错误 → error 非 null，profiles 空，loading false", async () => {
    server.use(http.get("/api/workspaces/:ws/auth-profiles",
      () => HttpResponse.json({ detail: "boom" }, { status: 500 })));
    const { result } = renderHook(() => useAuthProfiles("ws1"), { wrapper });
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.error).not.toBeNull();
    expect(result.current.profiles).toEqual([]);
  });

  it("refresh() 手动触发 revalidate", async () => {
    const { result } = renderHook(() => useAuthProfiles("ws1"), { wrapper });
    await waitFor(() => expect(result.current.loading).toBe(false));
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    const before = fetchSpy.mock.calls.length;
    await act(async () => { await result.current.refresh(); });
    expect(fetchSpy.mock.calls.length).toBeGreaterThan(before);
    fetchSpy.mockRestore();
  });

  it("同 ws 双 hook 实例（页面 + 表单下拉）共享一份请求", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    // 同一应用树（共享 cache）：key 相同 → SWR 去重为单请求。
    const shared = sharedCacheWrapper();
    const { result: r1 } = renderHook(() => useAuthProfiles("ws1"), { wrapper: shared.wrapper });
    const { result: r2 } = renderHook(() => useAuthProfiles("ws1"), { wrapper: shared.wrapper });
    await waitFor(() => expect(r1.current.loading).toBe(false));
    await waitFor(() => expect(r2.current.loading).toBe(false));
    const authGets = fetchSpy.mock.calls
      .filter((c) => String(c[0]).includes("/auth-profiles")).length;
    expect(authGets).toBe(1);
    fetchSpy.mockRestore();
  });

  it("workspace undefined → 挂起：不发请求、不 loading", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    const { result } = renderHook(() => useAuthProfiles(undefined), { wrapper });
    expect(result.current.loading).toBe(false);
    expect(result.current.profiles).toEqual([]);
    expect(fetchSpy.mock.calls.filter((c) => String(c[0]).includes("/auth-profiles")).length
    ).toBe(0);
    fetchSpy.mockRestore();
  });
});
