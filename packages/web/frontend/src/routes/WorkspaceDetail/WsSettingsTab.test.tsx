import { describe, it, expect, beforeAll, afterAll, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, cleanup } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { vi } from "vitest";
import { AuthProvider } from "@/auth/AuthContext";
import WsSettingsTab from "./WsSettingsTab";

vi.mock("react-i18next", () => ({ useTranslation: () => ({ t: (k: string) => k }) }));

const EMPTY_PROVIDER = {
  api_key: null, ai_provider: null, base_url: null, model: null,
  small_model: null, medium_model: null, large_model: null,
  max_turns: null, adaptive_thinking: null,
};

const server = setupServer();

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => { server.resetHandlers(); cleanup(); });
afterAll(() => server.close());

function renderAt(ws: string) {
  return render(
    <AuthProvider>
      <MemoryRouter initialEntries={[`/p/${ws}/settings`]}>
        <Routes>
          <Route path="/p/:workspace/settings" element={<WsSettingsTab />} />
        </Routes>
      </MemoryRouter>
    </AuthProvider>
  );
}

describe("WsSettingsTab", () => {
  it("admin + 已配置 api_key → 显脱敏占位 + 保存按钮", async () => {
    server.use(
      http.get("/api/auth/me", () => HttpResponse.json({ user: { id: 1, username: "admin", role: "admin" } })),
      http.get("/api/workspaces/:ws/config", () => HttpResponse.json({ provider: { ...EMPTY_PROVIDER, api_key: "••••", ai_provider: "openai_compatible" } })),
      http.get("/api/workspaces/:ws/members", () => HttpResponse.json({ members: [] })),
    );
    renderAt("ws-a");
    await waitFor(() => expect(screen.getByText("wsConfig.title")).toBeInTheDocument());
    // api_key 已配置 → placeholder 显「已配置」
    expect(screen.getByPlaceholderText("wsConfig.apiKey.configured")).toBeInTheDocument();
    // admin 可编辑 → 保存按钮显示
    expect(screen.getByText("wsConfig.save")).toBeInTheDocument();
  });

  it("填 api_key + 保存 → PUT body 含字段", async () => {
    let putBody: { provider?: { api_key?: string } } | null = null;
    server.use(
      http.get("/api/auth/me", () => HttpResponse.json({ user: { id: 1, username: "admin", role: "admin" } })),
      http.get("/api/workspaces/:ws/config", () => HttpResponse.json({ provider: EMPTY_PROVIDER })),
      http.get("/api/workspaces/:ws/members", () => HttpResponse.json({ members: [] })),
      http.put("/api/workspaces/:ws/config", async ({ request }) => {
        putBody = await request.json() as { provider?: { api_key?: string } };
        return HttpResponse.json({ ok: true });
      }),
    );
    renderAt("ws-a");
    await waitFor(() => expect(screen.getByText("wsConfig.save")).toBeInTheDocument());
    fireEvent.change(screen.getByPlaceholderText("wsConfig.apiKey.notConfigured"), { target: { value: "sk-new" } });
    fireEvent.click(screen.getByText("wsConfig.save"));
    await waitFor(() => expect(putBody).not.toBeNull());
    expect(putBody!.provider!.api_key).toBe("sk-new");
  });

  it("member（非 manager）→ 只读，无保存按钮", async () => {
    server.use(
      http.get("/api/auth/me", () => HttpResponse.json({ user: { id: 2, username: "bob", role: "user" } })),
      http.get("/api/workspaces/:ws/config", () => HttpResponse.json({ provider: { ...EMPTY_PROVIDER, api_key: "••••" } })),
      http.get("/api/workspaces/:ws/members", () => HttpResponse.json({ members: [{ user_id: 2, username: "bob", role: "member" }] })),
    );
    renderAt("ws-a");
    await waitFor(() => expect(screen.getByText("wsConfig.title")).toBeInTheDocument());
    expect(screen.queryByText("wsConfig.save")).toBeNull();
  });

  it("已配置 gitlab_token → git 段显脱敏占位", async () => {
    server.use(
      http.get("/api/auth/me", () => HttpResponse.json({ user: { id: 1, username: "admin", role: "admin" } })),
      http.get("/api/workspaces/:ws/config", () => HttpResponse.json({
        provider: EMPTY_PROVIDER,
        git: { gitlab_user: "bot", gitlab_token: "••••" },
      })),
      http.get("/api/workspaces/:ws/members", () => HttpResponse.json({ members: [] })),
    );
    renderAt("ws-a");
    await waitFor(() => expect(screen.getByText("wsConfig.title")).toBeInTheDocument());
    expect(screen.getByPlaceholderText("wsConfig.gitToken.configured")).toBeInTheDocument();
  });

  it("填 gitlab_user + 保存 → PUT body 含 git.gitlab_user", async () => {
    let putBody: { git?: { gitlab_user?: string | null } } | null = null;
    server.use(
      http.get("/api/auth/me", () => HttpResponse.json({ user: { id: 1, username: "admin", role: "admin" } })),
      http.get("/api/workspaces/:ws/config", () => HttpResponse.json({
        provider: EMPTY_PROVIDER,
        git: { gitlab_user: null, gitlab_token: null },
      })),
      http.get("/api/workspaces/:ws/members", () => HttpResponse.json({ members: [] })),
      http.put("/api/workspaces/:ws/config", async ({ request }) => {
        putBody = await request.json() as { git?: { gitlab_user?: string | null } };
        return HttpResponse.json({ ok: true });
      }),
    );
    renderAt("ws-a");
    await waitFor(() => expect(screen.getByText("wsConfig.save")).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText("wsConfig.fields.gitlabUser"), { target: { value: "bot-a" } });
    fireEvent.click(screen.getByText("wsConfig.save"));
    await waitFor(() => expect(putBody).not.toBeNull());
    expect(putBody!.git!.gitlab_user).toBe("bot-a");
  });
});
