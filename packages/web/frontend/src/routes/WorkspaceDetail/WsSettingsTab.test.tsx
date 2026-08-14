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
  it("is_default=true → 预填完整推荐模板（默认值 + 凭据注释行）", async () => {
    server.use(
      http.get("/api/auth/me", () => HttpResponse.json({ user: { id: 1, username: "admin", role: "admin" } })),
      http.get("/api/workspaces/:ws/config", () =>
        HttpResponse.json({ env_text: "SUPERNOVA_AI_PROVIDER=openai_compatible\n", is_default: true })),
      http.get("/api/workspaces/:ws/members", () => HttpResponse.json({ members: [] })),
    );
    renderAt("ws-a");
    await waitFor(() => expect(screen.getByText("wsConfig.title")).toBeInTheDocument());
    const ta = screen.getByLabelText("wsConfig.envText") as HTMLTextAreaElement;
    // 非凭据默认值已预填（保存即生效）
    expect(ta.value).toContain("SUPERNOVA_AI_PROVIDER=openai_compatible");
    expect(ta.value).toContain("SUPERNOVA_MAX_TURNS=10000");
    expect(ta.value).toContain("SUPERNOVA_ADAPTIVE_THINKING=true");
    expect(ta.value).toContain("SUPERNOVA_LLM_TRACK_ENABLED=0");
    expect(ta.value).toContain("SUPERNOVA_GITNEXUS_LLM_ENABLED=1");
    expect(ta.value).toContain("SUPERNOVA_BROWSER_ENGINE=agent-browser");
    expect(ta.value).toContain("SUPERNOVA_AGENT_NARRATION_LANG=zh");
    // 凭据行以 # 注释出现（不落盘空串、删 # 填值才生效）
    expect(ta.value).toContain("#SUPERNOVA_OPENAI_API_KEY=");
    // git 段默认不进入预填模板（prefill=false）；需要时在右侧词典点击注入
    expect(ta.value).not.toContain("GITLAB_TOKEN");
    expect(ta.value).not.toContain("GITLAB_USER");
  });

  it("is_default=false → 显示后端 env_text，不预填模板", async () => {
    server.use(
      http.get("/api/auth/me", () => HttpResponse.json({ user: { id: 1, username: "admin", role: "admin" } })),
      http.get("/api/workspaces/:ws/config", () =>
        HttpResponse.json({ env_text: "SUPERNOVA_AI_PROVIDER=anthropic_api\n", is_default: false })),
      http.get("/api/workspaces/:ws/members", () => HttpResponse.json({ members: [] })),
    );
    renderAt("ws-a");
    await waitFor(() => expect(screen.getByText("wsConfig.title")).toBeInTheDocument());
    const ta = screen.getByLabelText("wsConfig.envText") as HTMLTextAreaElement;
    expect(ta.value).toBe("SUPERNOVA_AI_PROVIDER=anthropic_api\n");
    expect(ta.value).not.toContain("SUPERNOVA_MAX_TURNS");
  });

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

  it("渲染可用配置项词典（生效 + 进程级）", async () => {
    server.use(
      http.get("/api/auth/me", () => HttpResponse.json({ user: { id: 1, username: "admin", role: "admin" } })),
      http.get("/api/workspaces/:ws/config", () => HttpResponse.json({ env_text: "" })),
      http.get("/api/workspaces/:ws/members", () => HttpResponse.json({ members: [] })),
    );
    renderAt("ws-a");
    await waitFor(() => expect(screen.getByText("wsConfig.title")).toBeInTheDocument());
    // 生效类（ws 覆盖生效）
    expect(screen.getByText("SUPERNOVA_AI_PROVIDER")).toBeInTheDocument();
    expect(screen.getByText("SUPERNOVA_OPENAI_API_KEY")).toBeInTheDocument();
    expect(screen.getByText("SUPERNOVA_MAX_TURNS")).toBeInTheDocument();
    expect(screen.getByText("SUPERNOVA_ADAPTIVE_THINKING")).toBeInTheDocument();
    expect(screen.getByText("GITLAB_TOKEN")).toBeInTheDocument();
    // 进程级（仅全局生效）
    expect(screen.getByText("SUPERNOVA_MAX_CONCURRENT")).toBeInTheDocument();
    expect(screen.getByText("CLAUDE_CODE_MAX_OUTPUT_TOKENS")).toBeInTheDocument();
  });

  it("点击「填入模板」→ textarea 注入注释模板", async () => {
    server.use(
      http.get("/api/auth/me", () => HttpResponse.json({ user: { id: 1, username: "admin", role: "admin" } })),
      http.get("/api/workspaces/:ws/config", () => HttpResponse.json({ env_text: "" })),
      http.get("/api/workspaces/:ws/members", () => HttpResponse.json({ members: [] })),
    );
    renderAt("ws-a");
    await waitFor(() => expect(screen.getByText("wsConfig.keys.insertTemplate")).toBeInTheDocument());
    fireEvent.click(screen.getByText("wsConfig.keys.insertTemplate"));
    const ta = screen.getByLabelText("wsConfig.envText") as HTMLTextAreaElement;
    await waitFor(() => expect(ta.value).toContain("wsConfig.keys.template"));
  });

  it("点击生效 key → 注入 KEY=默认值 到 textarea", async () => {
    server.use(
      http.get("/api/auth/me", () => HttpResponse.json({ user: { id: 1, username: "admin", role: "admin" } })),
      http.get("/api/workspaces/:ws/config", () => HttpResponse.json({ env_text: "" })),
      http.get("/api/workspaces/:ws/members", () => HttpResponse.json({ members: [] })),
    );
    renderAt("ws-a");
    await waitFor(() => expect(screen.getByText("wsConfig.title")).toBeInTheDocument());
    fireEvent.click(screen.getByText("SUPERNOVA_MAX_TURNS"));
    const ta = screen.getByLabelText("wsConfig.envText") as HTMLTextAreaElement;
    await waitFor(() => expect(ta.value).toContain("SUPERNOVA_MAX_TURNS=10000"));
  });

  it("点击凭据 key → 注入空值(等用户填)", async () => {
    server.use(
      http.get("/api/auth/me", () => HttpResponse.json({ user: { id: 1, username: "admin", role: "admin" } })),
      http.get("/api/workspaces/:ws/config", () => HttpResponse.json({ env_text: "" })),
      http.get("/api/workspaces/:ws/members", () => HttpResponse.json({ members: [] })),
    );
    renderAt("ws-a");
    await waitFor(() => expect(screen.getByText("wsConfig.title")).toBeInTheDocument());
    fireEvent.click(screen.getByText("SUPERNOVA_OPENAI_API_KEY"));
    const ta = screen.getByLabelText("wsConfig.envText") as HTMLTextAreaElement;
    await waitFor(() => expect(ta.value).toContain("SUPERNOVA_OPENAI_API_KEY="));
    // 不应被填入任何默认值
    expect(ta.value).not.toMatch(/SUPERNOVA_OPENAI_API_KEY=\S/);
  });

  it("已存在的 key 再点击 → 不重复注入", async () => {
    server.use(
      http.get("/api/auth/me", () => HttpResponse.json({ user: { id: 1, username: "admin", role: "admin" } })),
      http.get("/api/workspaces/:ws/config", () => HttpResponse.json({ env_text: "SUPERNOVA_MAX_TURNS=50\n" })),
      http.get("/api/workspaces/:ws/members", () => HttpResponse.json({ members: [] })),
    );
    renderAt("ws-a");
    await waitFor(() => expect(screen.getByText("wsConfig.title")).toBeInTheDocument());
    fireEvent.click(screen.getByText("SUPERNOVA_MAX_TURNS"));
    const ta = screen.getByLabelText("wsConfig.envText") as HTMLTextAreaElement;
    // 仍是原值，未追加第二行
    expect(ta.value).toBe("SUPERNOVA_MAX_TURNS=50\n");
    expect((ta.value.match(/SUPERNOVA_MAX_TURNS=/g) || []).length).toBe(1);
  });
});
