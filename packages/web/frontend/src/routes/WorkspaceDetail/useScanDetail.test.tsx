// useScanDetail（SWR 迁移，2026-08-17 批次 Task 2）：scan 详情 hook 契约。
// 核心断言：①初始 fetch ②同 key 多消费方（ScanDetail/OverviewTab/ReportTab）单请求
// ③refresh() revalidate 且不翻 loading（silent 语义——保 ScanProgressOverview 不卸载）
// ④错误语义 ⑤参数未定挂起。
import { describe, it, expect, beforeAll, afterAll, afterEach, vi } from "vitest";
import { renderHook, act, waitFor, cleanup } from "@testing-library/react";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { SWRConfig } from "swr";
import type { ReactNode } from "react";
import { useScanDetail } from "./useScanDetail";
import type { SessionData } from "@/api/types";

const wrapper = ({ children }: { children: ReactNode }) => (
  <SWRConfig value={{ provider: () => new Map() }}>{children}</SWRConfig>
);

function sharedCacheWrapper() {
  const cache = new Map();
  return { wrapper: ({ children }: { children: ReactNode }) => (
    <SWRConfig value={{ provider: () => cache }}>{children}</SWRConfig>
  ) };
}

const detail: SessionData = {
  scan_id: "s1",
  workflow_id: "ws1-s1",
  scan_type: "whitebox",
  status: "completed",
  combined: true,
} as unknown as SessionData;

let payload: SessionData | { status: string } = detail;

const server = setupServer(
  http.get("/api/workspaces/:ws/scans/:scanId", () => HttpResponse.json(payload)),
);

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
beforeEach(() => { payload = detail; });
afterEach(() => { server.resetHandlers(); cleanup(); });
afterAll(() => server.close());

describe("useScanDetail", () => {
  it("初始 fetch：loading true → false，data 到位", async () => {
    const { result } = renderHook(() => useScanDetail("ws1", "s1"), { wrapper });
    expect(result.current.loading).toBe(true);
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.data?.workflow_id).toBe("ws1-s1");
    expect(result.current.error).toBeNull();
  });

  it("同 scan 三个消费方（Detail/Overview/Report 探测）共享一份请求", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    const shared = sharedCacheWrapper();
    const hooks = [0, 1, 2].map(() =>
      renderHook(() => useScanDetail("ws1", "s1"), { wrapper: shared.wrapper }));
    await waitFor(() => expect(hooks[0].result.current.loading).toBe(false));
    await waitFor(() => expect(hooks[2].result.current.loading).toBe(false));
    const gets = fetchSpy.mock.calls
      .filter((c) => String(c[0]).includes("/scans/s1")).length;
    expect(gets).toBe(1);
    fetchSpy.mockRestore();
  });

  it("refresh() 拿到新数据且不翻 loading（silent 语义）", async () => {
    const { result } = renderHook(() => useScanDetail("ws1", "s1"), { wrapper });
    await waitFor(() => expect(result.current.loading).toBe(false));
    payload = { ...detail, status: "failed" };
    await act(async () => { await result.current.refresh(); });
    expect(result.current.data?.status).toBe("failed");
    // silent：已有数据时 refresh 不回 loading（子树不卸载）。
    expect(result.current.loading).toBe(false);
  });

  it("fetch 错误 → error 非 null，data null，loading false", async () => {
    server.use(http.get("/api/workspaces/:ws/scans/:scanId",
      () => HttpResponse.json({ detail: "boom" }, { status: 500 })));
    const { result } = renderHook(() => useScanDetail("ws1", "s1"), { wrapper });
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.error).not.toBeNull();
    expect(result.current.data).toBeNull();
  });

  it("ws/scanId undefined → 挂起：不发请求", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    const { result } = renderHook(() => useScanDetail(undefined, "s1"), { wrapper });
    expect(result.current.loading).toBe(false);
    expect(result.current.data).toBeNull();
    expect(fetchSpy.mock.calls.filter((c) => String(c[0]).includes("/scans/")).length).toBe(0);
    fetchSpy.mockRestore();
  });
});
