import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { SsoWhitelistPanel } from "./SsoWhitelistPanel";

vi.mock("react-i18next", () => ({ useTranslation: () => ({ t: (k: string) => k }) }));

function mockFetchByRoute(map: Record<string, unknown>) {
  vi.spyOn(window, "fetch").mockImplementation((input: RequestInfo | URL) => {
    const url = typeof input === "string" ? input : (input as Request).url;
    for (const [k, v] of Object.entries(map)) {
      if (url.includes(k)) {
        return Promise.resolve(new Response(JSON.stringify(v), { status: 200 }));
      }
    }
    return Promise.resolve(new Response("{}", { status: 404 }));
  });
}

const ROWS = { whitelist: [
  { nick: "niu", added_by: "admin", created_at: "2026-08-25T00:00:00Z" },
  { nick: "mate", added_by: "admin", created_at: "2026-08-25T01:00:00Z" },
] };

describe("SsoWhitelistPanel", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("enabled 时加载并渲染白名单 chip", async () => {
    mockFetchByRoute({ "/auth/sso/config": { enabled: true }, "/auth/sso/whitelist": ROWS });
    render(<SsoWhitelistPanel />);
    await waitFor(() => expect(screen.getByTestId("sso-whitelist-item-niu")).toBeInTheDocument());
    expect(screen.getByTestId("sso-whitelist-item-mate")).toBeInTheDocument();
  });

  it("添加：POST 后刷新列表", async () => {
    const calls: string[] = [];
    vi.spyOn(window, "fetch").mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : (input as Request).url;
      if (url.includes("/auth/sso/config")) {
        return Promise.resolve(new Response(JSON.stringify({ enabled: true }), { status: 200 }));
      }
      if (url.includes("/auth/sso/whitelist") && init?.method === "POST") {
        calls.push(`POST ${url} ${init.body}`);
        return Promise.resolve(new Response(JSON.stringify({ ok: true }), { status: 200 }));
      }
      return Promise.resolve(new Response(JSON.stringify(ROWS), { status: 200 }));
    });
    render(<SsoWhitelistPanel />);
    await waitFor(() => expect(screen.getByTestId("sso-whitelist-input")).toBeInTheDocument());
    fireEvent.change(screen.getByTestId("sso-whitelist-input"), { target: { value: "newbie" } });
    fireEvent.click(screen.getByRole("button", { name: "users.ssoWhitelist.add" }));
    await waitFor(() => expect(calls.some((c) => c.includes('"nick":"newbie"'))).toBe(true));
  });

  it("移除：DELETE 后 chip 消失", async () => {
    const deleted: string[] = [];
    vi.spyOn(window, "fetch").mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : (input as Request).url;
      if (url.includes("/auth/sso/config")) {
        return Promise.resolve(new Response(JSON.stringify({ enabled: true }), { status: 200 }));
      }
      if (init?.method === "DELETE") {
        deleted.push(url);
        return Promise.resolve(new Response(JSON.stringify({ ok: true }), { status: 200 }));
      }
      const body = deleted.length
        ? { whitelist: ROWS.whitelist.filter((r) => !deleted[0].endsWith(r.nick)) }
        : ROWS;
      return Promise.resolve(new Response(JSON.stringify(body), { status: 200 }));
    });
    render(<SsoWhitelistPanel />);
    await waitFor(() => expect(screen.getByTestId("sso-whitelist-item-niu")).toBeInTheDocument());
    fireEvent.click(screen.getByLabelText("remove-niu"));
    await waitFor(() => expect(screen.queryByTestId("sso-whitelist-item-niu")).toBeNull());
  });

  it("disabled 时显示未启用提示、无输入框", async () => {
    mockFetchByRoute({ "/auth/sso/config": { enabled: false } });
    render(<SsoWhitelistPanel />);
    await waitFor(() => expect(screen.getByText("users.ssoWhitelist.disabledHint")).toBeInTheDocument());
    expect(screen.queryByTestId("sso-whitelist-input")).toBeNull();
  });
});
