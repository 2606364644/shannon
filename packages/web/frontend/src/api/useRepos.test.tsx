// useRepos（SWR 迁移，2026-08-17 批次 Task 3）：仓库列表 hook 契约。
// 含 ReposTab 特有语义：enabled=false（auth user 未就绪）挂起不发请求。
import { describe, it, expect, beforeAll, afterAll, afterEach, vi } from "vitest";
import { renderHook, act, waitFor, cleanup } from "@testing-library/react";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { SWRConfig } from "swr";
import type { ReactNode } from "react";
import { useRepos } from "./useRepos";
import type { Repo } from "./types";

const wrapper = ({ children }: { children: ReactNode }) => (
  <SWRConfig value={{ provider: () => new Map() }}>{children}</SWRConfig>
);

function sharedCacheWrapper() {
  const cache = new Map();
  return { wrapper: ({ children }: { children: ReactNode }) => (
    <SWRConfig value={{ provider: () => cache }}>{children}</SWRConfig>
  ) };
}

const repo: Repo = {
  name: "app", state: "ready", path: "/repos/app", size: 12345,
} as unknown as Repo;

const server = setupServer(
  http.get("/api/workspaces/:ws/repos", () => HttpResponse.json([repo])),
);

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => { server.resetHandlers(); cleanup(); });
afterAll(() => server.close());

describe("useRepos", () => {
  it("enabled=true 初始 fetch：loading true → false，data 到位", async () => {
    const { result } = renderHook(() => useRepos("ws1", true), { wrapper });
    expect(result.current.loading).toBe(true);
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.repos).toHaveLength(1);
    expect(result.current.repos[0].name).toBe("app");
    expect(result.current.error).toBeNull();
  });

  it("enabled=false（user 未就绪）挂起：不发请求、不 loading", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    const { result } = renderHook(() => useRepos("ws1", false), { wrapper });
    expect(result.current.loading).toBe(false);
    expect(result.current.repos).toEqual([]);
    expect(fetchSpy.mock.calls.filter((c) => String(c[0]).includes("/repos")).length).toBe(0);
    fetchSpy.mockRestore();
  });

  it("refresh() 手动触发 revalidate（clone/pull 完成后调用）", async () => {
    const { result } = renderHook(() => useRepos("ws1", true), { wrapper });
    await waitFor(() => expect(result.current.loading).toBe(false));
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    const before = fetchSpy.mock.calls.length;
    await act(async () => { await result.current.refresh(); });
    expect(fetchSpy.mock.calls.length).toBeGreaterThan(before);
    fetchSpy.mockRestore();
  });

  it("同 ws 双实例（ReposTab + 扫描表单下拉）共享一份请求", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    const shared = sharedCacheWrapper();
    const { result: r1 } = renderHook(() => useRepos("ws1", true), { wrapper: shared.wrapper });
    const { result: r2 } = renderHook(() => useRepos("ws1", true), { wrapper: shared.wrapper });
    await waitFor(() => expect(r1.current.loading).toBe(false));
    await waitFor(() => expect(r2.current.loading).toBe(false));
    const gets = fetchSpy.mock.calls
      .filter((c) => String(c[0]).includes("/repos")).length;
    expect(gets).toBe(1);
    fetchSpy.mockRestore();
  });

  it("fetch 错误 → error 非 null，repos 空，loading false", async () => {
    server.use(http.get("/api/workspaces/:ws/repos",
      () => HttpResponse.json({ detail: "boom" }, { status: 500 })));
    const { result } = renderHook(() => useRepos("ws1", true), { wrapper });
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.error).not.toBeNull();
    expect(result.current.repos).toEqual([]);
  });
});
