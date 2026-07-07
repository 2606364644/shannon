import { describe, it, expect, beforeAll, afterAll, afterEach } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { useSystemStatus } from "./systemStatus";

const okBody = {
  ai_provider: "claude",
  browser_engine: "agent-browser",
  temporal: { enabled: true, host: "localhost:7233", last_status: "connected", last_error: null },
  git: { binary_available: true, credentials_configured: true },
  version: "shannon-web 0.1.0",
};

const server = setupServer(
  http.get("/api/system-status", () => HttpResponse.json(okBody)),
);

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

describe("useSystemStatus", () => {
  it("mount 拉一次 system-status shape", async () => {
    const { result } = renderHook(() => useSystemStatus());
    await waitFor(() => expect(result.current.data).not.toBeNull());
    expect(result.current.data?.ai_provider).toBe("claude");
    expect(result.current.data?.temporal.last_status).toBe("connected");
    expect(result.current.data?.version).toBe("shannon-web 0.1.0");
    expect(result.current.loading).toBe(false);
    expect(result.current.error).toBeNull();
  });

  it("fetch 失败 → error 设置,data 保持 null", async () => {
    server.use(http.get("/api/system-status", () => HttpResponse.json({}, { status: 500 })));
    const { result } = renderHook(() => useSystemStatus());
    await waitFor(() => expect(result.current.error).not.toBeNull());
    expect(result.current.data).toBeNull();
    expect(result.current.loading).toBe(false);
  });

  it("refresh 重新拉取", async () => {
    const { result } = renderHook(() => useSystemStatus());
    await waitFor(() => expect(result.current.data).not.toBeNull());
    let called = 0;
    server.use(http.get("/api/system-status", () => { called += 1; return HttpResponse.json(okBody); }));
    await result.current.refresh();
    expect(called).toBe(1);
  });
});
