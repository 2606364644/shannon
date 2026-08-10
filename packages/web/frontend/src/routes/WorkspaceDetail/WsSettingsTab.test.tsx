import { describe, it, expect, beforeAll, afterAll, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, cleanup } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { vi } from "vitest";
import { AuthProvider } from "@/auth/AuthContext";
import WsSettingsTab from "./WsSettingsTab";

vi.mock("react-i18next", () => ({ useTranslation: () => ({ t: (k: string) => k }) }));

const ENV_TEXT = "SUPERNOVA_AI_PROVIDER=openai_compatible\nSUPERNOVA_OPENAI_API_KEY=••••\n";

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
  it("GET env_text → textarea 显示 env 文本", async () => {
    server.use(
      http.get("/api/auth/me", () => HttpResponse.json({ user: { id: 1, username: "admin", role: "admin" } })),
      http.get("/api/workspaces/:ws/config", () => HttpResponse.json({ env_text: ENV_TEXT })),
      http.get("/api/workspaces/:ws/members", () => HttpResponse.json({ members: [] })),
    );
    renderAt("ws-a");
    await waitFor(() => expect(screen.getByText("wsConfig.title")).toBeInTheDocument());
    expect(screen.getByLabelText("wsConfig.envText")).toHaveValue(ENV_TEXT);
  });

  it("编辑 + 保存 → PUT body {env_text}", async () => {
    let putBody: { env_text?: string } | null = null;
    server.use(
      http.get("/api/auth/me", () => HttpResponse.json({ user: { id: 1, username: "admin", role: "admin" } })),
      http.get("/api/workspaces/:ws/config", () => HttpResponse.json({ env_text: "" })),
      http.get("/api/workspaces/:ws/members", () => HttpResponse.json({ members: [] })),
      http.put("/api/workspaces/:ws/config", async ({ request }) => {
        putBody = await request.json() as { env_text?: string };
        return HttpResponse.json({ ok: true, warnings: { ineffective: [], unknown: [] } });
      }),
    );
    renderAt("ws-a");
    await waitFor(() => expect(screen.getByText("wsConfig.save")).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText("wsConfig.envText"),
      { target: { value: "SUPERNOVA_AI_PROVIDER=openai_compatible\n" } });
    fireEvent.click(screen.getByText("wsConfig.save"));
    await waitFor(() => expect(putBody).not.toBeNull());
    expect(putBody!.env_text).toBe("SUPERNOVA_AI_PROVIDER=openai_compatible\n");
  });

  it("保存返回 warnings → 展示 ineffective / unknown key", async () => {
    server.use(
      http.get("/api/auth/me", () => HttpResponse.json({ user: { id: 1, username: "admin", role: "admin" } })),
      http.get("/api/workspaces/:ws/config", () => HttpResponse.json({ env_text: "" })),
      http.get("/api/workspaces/:ws/members", () => HttpResponse.json({ members: [] })),
      http.put("/api/workspaces/:ws/config", async () => HttpResponse.json({
        ok: true,
        warnings: { ineffective: ["SUPERNOVA_MAX_CONCURRENT"], unknown: ["BOGUS_KEY"] },
      })),
    );
    renderAt("ws-a");
    await waitFor(() => expect(screen.getByText("wsConfig.save")).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText("wsConfig.envText"), { target: { value: "x=1\n" } });
    fireEvent.click(screen.getByText("wsConfig.save"));
    await waitFor(() => expect(screen.getByText(/SUPERNOVA_MAX_CONCURRENT/)).toBeInTheDocument());
    expect(screen.getByText(/BOGUS_KEY/)).toBeInTheDocument();
  });

  it("member（非 manager）→ 只读，无保存按钮", async () => {
    server.use(
      http.get("/api/auth/me", () => HttpResponse.json({ user: { id: 2, username: "bob", role: "user" } })),
      http.get("/api/workspaces/:ws/config", () => HttpResponse.json({ env_text: "" })),
      http.get("/api/workspaces/:ws/members", () => HttpResponse.json({ members: [{ user_id: 2, username: "bob", role: "member" }] })),
    );
    renderAt("ws-a");
    await waitFor(() => expect(screen.getByText("wsConfig.title")).toBeInTheDocument());
    expect(screen.queryByText("wsConfig.save")).toBeNull();
  });
});
