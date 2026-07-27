import { describe, it, expect, beforeAll, afterAll, afterEach, beforeEach } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import i18n from "@/i18n";
import { useSystemStatus } from "./systemStatus";
import { setUnauthorizedHandler, resetUnauthorizedHandler } from "./client";

const okBody = {
  ai_provider: "claude",
  browser_engine: "agent-browser",
  temporal: { enabled: true, host: "localhost:7233", last_status: "connected", last_error: null },
  git: { binary_available: true, credentials_configured: true },
  version: "supernova-web 0.1.0",
  brand_name: "Supernova",
};

const server = setupServer(
  http.get("/api/system-status", () => HttpResponse.json(okBody)),
);

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
beforeEach(() => i18n.changeLanguage("zh"));
afterEach(() => { server.resetHandlers(); i18n.changeLanguage("zh"); resetUnauthorizedHandler(); });
afterAll(() => server.close());

describe("useSystemStatus", () => {
  it("mount 拉一次 system-status shape", async () => {
    const { result } = renderHook(() => useSystemStatus());
    await waitFor(() => expect(result.current.data).not.toBeNull());
    expect(result.current.data?.ai_provider).toBe("claude");
    expect(result.current.data?.temporal.last_status).toBe("connected");
    expect(result.current.data?.version).toBe("supernova-web 0.1.0");
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

  it("fetch 失败 → error 文案经 i18n 本地化（中文）", async () => {
    server.use(http.get("/api/system-status", () => HttpResponse.json({}, { status: 500 })));
    const { result } = renderHook(() => useSystemStatus());
    await waitFor(() => expect(result.current.error).not.toBeNull());
    expect(result.current.error).toBe("加载失败（500）");
  });

  it("fetch 失败 → 切英文 error 文案为英文", async () => {
    i18n.changeLanguage("en");
    server.use(http.get("/api/system-status", () => HttpResponse.json({}, { status: 500 })));
    const { result } = renderHook(() => useSystemStatus());
    await waitFor(() => expect(result.current.error).not.toBeNull());
    expect(result.current.error).toBe("Failed to load (500)");
  });

  it("refresh 重新拉取", async () => {
    const { result } = renderHook(() => useSystemStatus());
    await waitFor(() => expect(result.current.data).not.toBeNull());
    let called = 0;
    server.use(http.get("/api/system-status", () => { called += 1; return HttpResponse.json(okBody); }));
    await result.current.refresh();
    expect(called).toBe(1);
  });

  it("401 不触发 onUnauthorized（brand 是装饰数据，未登录 /login 页 BrandProvider 也会发此请求，非 silent 会循环刷新）", async () => {
    // 根因：BrandProvider 在 App.tsx 最外层，无条件调 useSystemStatus -> GET /system-status
    // （需登录）。未登录访问 /login 页时该请求 401，若非 silent -> onUnauthorized
    // -> window.location.assign("/login?expired=1") -> 整页刷新 -> BrandProvider 重新
    // mount -> 又发 401 -> 又 assign -> 无限循环（login 页"一直在重复刷新"）。
    // brand 加载失败应静默回落默认，不该等同于 session 过期。
    const handler = vi.fn();
    setUnauthorizedHandler(handler);
    server.use(http.get("/api/system-status", () => HttpResponse.json({}, { status: 401 })));
    const { result } = renderHook(() => useSystemStatus());
    await waitFor(() => expect(result.current.error).not.toBeNull());
    expect(handler).not.toHaveBeenCalled();
  });
});
