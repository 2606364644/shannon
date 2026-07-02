import { describe, it, expect, vi, beforeEach } from "vitest";
import { apiGet, apiPost, apiDelete, ApiError } from "./client";

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
