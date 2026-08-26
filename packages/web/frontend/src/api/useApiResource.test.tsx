// useApiText/useApiJson（SWR 迁移，2026-08-17 批次 Task 4）：按 path 拉取的通用资源
// hook（ReportTab 报告正文 / DeliverablesTab 产物 summary）。key 即 path 字符串——
// 同 path 消费方自动去重共享缓存；path=null 挂起。
import { describe, it, expect, beforeAll, afterAll, afterEach, vi } from "vitest";
import { renderHook, waitFor, cleanup } from "@testing-library/react";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { SWRConfig } from "swr";
import type { ReactNode } from "react";
import { useApiText, useApiJson, useReportData } from "./useApiResource";

const wrapper = ({ children }: { children: ReactNode }) => (
  <SWRConfig value={{ provider: () => new Map() }}>{children}</SWRConfig>
);

function sharedCacheWrapper() {
  const cache = new Map();
  return { wrapper: ({ children }: { children: ReactNode }) => (
    <SWRConfig value={{ provider: () => cache }}>{children}</SWRConfig>
  ) };
}

const server = setupServer(
  http.get("/api/workspaces/:ws/scans/:id/report", () => new HttpResponse("# 报告\n内容", {
    headers: { "Content-Type": "text/plain" },
  })),
  http.get("/api/workspaces/:ws/scans/:id/deliverables", () =>
    HttpResponse.json({ track: "whitebox", files: [], aggregated_vulnerabilities: [] })),
);

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => { server.resetHandlers(); cleanup(); });
afterAll(() => server.close());

describe("useApiText", () => {
  it("初始 fetch：loading true → false，text 到位", async () => {
    const { result } = renderHook(() => useApiText("/workspaces/ws/scans/s1/report"), { wrapper });
    expect(result.current.loading).toBe(true);
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.text).toContain("# 报告");
    expect(result.current.error).toBeNull();
  });

  it("path=null 挂起：不发请求", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    const { result } = renderHook(() => useApiText(null), { wrapper });
    expect(result.current.loading).toBe(false);
    expect(result.current.text).toBe("");
    expect(fetchSpy.mock.calls.length).toBe(0);
    fetchSpy.mockRestore();
  });

  it("同 path 双实例共享一份请求", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    const shared = sharedCacheWrapper();
    const { result: r1 } = renderHook(() => useApiText("/workspaces/ws/scans/s1/report"), { wrapper: shared.wrapper });
    const { result: r2 } = renderHook(() => useApiText("/workspaces/ws/scans/s1/report"), { wrapper: shared.wrapper });
    await waitFor(() => expect(r1.current.loading).toBe(false));
    await waitFor(() => expect(r2.current.loading).toBe(false));
    expect(fetchSpy.mock.calls.filter((c) => String(c[0]).includes("/report")).length).toBe(1);
    fetchSpy.mockRestore();
  });

  it("fetch 错误 → error 非 null", async () => {
    server.use(http.get("/api/workspaces/:ws/scans/:id/report",
      () => HttpResponse.json({ detail: "boom" }, { status: 500 })));
    const { result } = renderHook(() => useApiText("/workspaces/ws/scans/s1/report"), { wrapper });
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.error).not.toBeNull();
  });
});

describe("useApiJson", () => {
  it("初始 fetch + 类型化 data", async () => {
    const { result } = renderHook(
      () => useApiJson<{ track: string }>("/workspaces/ws/scans/s1/deliverables"), { wrapper });
    expect(result.current.loading).toBe(true);
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.data?.track).toBe("whitebox");
    expect(result.current.error).toBeNull();
  });

  it("fetch 错误 → error 非 null，data null", async () => {
    server.use(http.get("/api/workspaces/:ws/scans/:id/deliverables",
      () => HttpResponse.json({ detail: "boom" }, { status: 500 })));
    const { result } = renderHook(
      () => useApiJson("/workspaces/ws/scans/s1/deliverables"), { wrapper });
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.error).not.toBeNull();
    expect(result.current.data).toBeNull();
  });
});

describe("useReportData", () => {
  // T6（spec 2026-08-26 §7.1）：report_data.json 拉取。404 是「旧 scan 走 md 降级」
  // 的正常分流信号（notFound=true），非 404 错误交调用方显 ErrorState。
  it("200 -> data（schema_version 透传），error/notFound 空", async () => {
    server.use(http.get("/api/workspaces/:ws/scans/:id/report-data", () =>
      HttpResponse.json({ schema_version: 1, scan: { id: "s1", track: "whitebox" }, vulnerabilities: [] })));
    const { result } = renderHook(
      () => useReportData("/workspaces/ws/scans/s1/report-data?track=whitebox"), { wrapper });
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.data?.schema_version).toBe(1);
    expect(result.current.data?.scan.track).toBe("whitebox");
    expect(result.current.error).toBeNull();
    expect(result.current.notFound).toBe(false);
  });

  it("404 -> notFound=true（md 降级分流信号），error 为空", async () => {
    server.use(http.get("/api/workspaces/:ws/scans/:id/report-data", () =>
      new HttpResponse("", { status: 404 })));
    const { result } = renderHook(
      () => useReportData("/workspaces/ws/scans/s1/report-data?track=whitebox"), { wrapper });
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.notFound).toBe(true);
    expect(result.current.data).toBeNull();
    expect(result.current.error).toBeNull();
  });

  it("500 -> notFound=false + error 非 null（非降级信号）", async () => {
    server.use(http.get("/api/workspaces/:ws/scans/:id/report-data", () =>
      HttpResponse.json({ detail: "boom" }, { status: 500 })));
    const { result } = renderHook(
      () => useReportData("/workspaces/ws/scans/s1/report-data?track=whitebox"), { wrapper });
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.notFound).toBe(false);
    expect(result.current.error).not.toBeNull();
  });

  it("path=null 挂起：不发请求（组合 tab 无 run 时不探 scan 级 combined）", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    const { result } = renderHook(() => useReportData(null), { wrapper });
    expect(result.current.loading).toBe(false);
    expect(result.current.data).toBeNull();
    expect(fetchSpy.mock.calls.length).toBe(0);
    fetchSpy.mockRestore();
  });
});
