import { describe, it, expect, vi, beforeEach } from "vitest";
import { apiGet, apiPost, apiDelete, ApiError, setUnauthorizedHandler } from "./client";

// 构造符合 fetch Response 真实契约的 mock：text() 与 json() 都在。
function res({ ok, status, body }: { ok: boolean; status: number; body: unknown }) {
  const text = typeof body === "string" ? body : JSON.stringify(body);
  return {
    ok, status,
    text: async () => text,
    json: async () => (typeof body === "string" ? JSON.parse(body) : body),
  };
}

beforeEach(() => {
  globalThis.fetch = vi.fn() as unknown as typeof fetch;
});

describe("api client", () => {
  it("apiGet 解析 JSON 成功", async () => {
    (globalThis.fetch as any).mockResolvedValue(res({ ok: true, status: 200, body: { name: "ws" } }));
    const r = await apiGet<{ name: string }>("/workspaces/ws");
    expect(r.name).toBe("ws");
  });

  it("apiPost 成功返回 body", async () => {
    (globalThis.fetch as any).mockResolvedValue(res({ ok: true, status: 202, body: { workspace: "ws" } }));
    const r = await apiPost<{ workspace: string }>("/scan", { type: "whitebox" });
    expect(r.workspace).toBe("ws");
  });

  it("422 抛 ApiError 带 body", async () => {
    (globalThis.fetch as any).mockResolvedValue(
      res({ ok: false, status: 422, body: { detail: [{ loc: ["repos"], msg: "bad" }] } }),
    );
    await expect(apiPost("/scan", {})).rejects.toMatchObject({ status: 422 });
    try { await apiPost("/scan", {}); } catch (e) {
      expect(e).toBeInstanceOf(ApiError);
      expect((e as ApiError).body).toMatchObject({ detail: [{ loc: ["repos"] }] });
    }
  });

  it("apiDelete 成功（204 无 body）", async () => {
    (globalThis.fetch as any).mockResolvedValue({ ok: true, status: 204, text: async () => "" });
    const r = await apiDelete<{ ok: true }>("/workspaces/ws");
    expect(r).toEqual({});
  });

  it("请求前缀 /api 且 POST 带 JSON body + Content-Type", async () => {
    let captured: { url?: string; init?: RequestInit } = {};
    (globalThis.fetch as any).mockImplementation((url: string, init: RequestInit) => {
      captured = { url, init };
      return Promise.resolve(res({ ok: true, status: 202, body: { workspace: "ws" } }));
    });
    await apiPost("/scan", { type: "blackbox" });
    expect(captured.url).toBe("/api/scan");
    expect(captured.init?.method).toBe("POST");
    expect((captured.init?.headers as Record<string, string>)["Content-Type"]).toBe("application/json");
    expect(captured.init?.body).toBe(JSON.stringify({ type: "blackbox" }));
  });
});

describe("client auth", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    document.cookie = "sn-csrf=token123; path=/";
  });

  it("sends credentials: include", async () => {
    const fm = vi.spyOn(window, "fetch").mockResolvedValue(new Response("{}", { status: 200 }));
    await apiGet("/foo");
    expect(fm.mock.calls[0][1]).toMatchObject({ credentials: "include" });
  });

  it("adds X-CSRF-Token on POST", async () => {
    const fm = vi.spyOn(window, "fetch").mockResolvedValue(new Response("{}", { status: 200 }));
    await apiPost("/foo", { a: 1 });
    const init = fm.mock.calls[0][1] as RequestInit;
    expect((init.headers as Record<string, string>)["X-CSRF-Token"]).toBe("token123");
  });

  it("401 silent does not fire handler", async () => {
    vi.spyOn(window, "fetch").mockResolvedValue(new Response("{}", { status: 401 }));
    const h = vi.fn();
    setUnauthorizedHandler(h);
    await expect(apiGet("/me", { silent: true })).rejects.toThrow();
    expect(h).not.toHaveBeenCalled();
  });

  it("401 non-silent fires handler", async () => {
    vi.spyOn(window, "fetch").mockResolvedValue(new Response("{}", { status: 401 }));
    const h = vi.fn();
    setUnauthorizedHandler(h);
    await expect(apiGet("/workspaces")).rejects.toThrow();
    expect(h).toHaveBeenCalled();
  });
});
