import { describe, it, expect, beforeAll, afterAll, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, cleanup } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { vi } from "vitest";
import { AuthProvider } from "@/auth/AuthContext";
import type { WsProviderFields } from "@/api/wsConfig";
import WsSettingsTab from "./WsSettingsTab";

vi.mock("react-i18next", () => ({ useTranslation: () => ({ t: (k: string) => k }) }));

const EMPTY_PROVIDER: WsProviderFields = {
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

// admin + 空成员的常用 handler 组合
function adminHandlers(provider: WsProviderFields = EMPTY_PROVIDER, git: { gitlab_user: string | null; gitlab_token: string | null } = { gitlab_user: null, gitlab_token: null }) {
  return [
    http.get("/api/auth/me", () => HttpResponse.json({ user: { id: 1, username: "admin", role: "admin" } })),
    http.get("/api/workspaces/:ws/config", () => HttpResponse.json({ provider, git })),
    http.get("/api/workspaces/:ws/members", () => HttpResponse.json({ members: [] })),
  ];
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
    // 只读横幅提示
    expect(screen.getByText("wsConfig.readonlyBanner")).toBeInTheDocument();
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

  // ---- 以下为重构后新增覆盖 ----

  it("覆盖字段显「已覆盖」徽标，继承字段显「继承全局」", async () => {
    server.use(...adminHandlers({ ...EMPTY_PROVIDER, ai_provider: "openai_compatible", model: "glm-4.6" }));
    renderAt("ws-a");
    await waitFor(() => expect(screen.getByText("wsConfig.title")).toBeInTheDocument());
    // ai_provider + model 已覆盖 → ≥2 个 override 徽标
    expect(screen.getAllByText("wsConfig.provenance.override").length).toBeGreaterThanOrEqual(2);
    // base_url 等未设 → 继承
    expect(screen.getAllByText("wsConfig.provenance.inherit").length).toBeGreaterThanOrEqual(1);
    // 覆盖计数汇总
    expect(screen.getByText("wsConfig.summary.overridden")).toBeInTheDocument();
  });

  it("无任何覆盖 → 汇总显「全部继承」", async () => {
    server.use(...adminHandlers(EMPTY_PROVIDER));
    renderAt("ws-a");
    await waitFor(() => expect(screen.getByText("wsConfig.title")).toBeInTheDocument());
    expect(screen.getByText("wsConfig.summary.none")).toBeInTheDocument();
  });

  it("未改动时 save 禁用；改动后显未保存指示并启用", async () => {
    server.use(...adminHandlers(EMPTY_PROVIDER));
    renderAt("ws-a");
    await waitFor(() => expect(screen.getByText("wsConfig.title")).toBeInTheDocument());
    expect(screen.getByRole("button", { name: "wsConfig.save" })).toBeDisabled();
    fireEvent.change(screen.getByPlaceholderText("wsConfig.apiKey.notConfigured"), { target: { value: "sk" } });
    expect(screen.getByText("wsConfig.dirtyHint")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "wsConfig.save" })).not.toBeDisabled();
  });

  it("放弃更改恢复初始值", async () => {
    server.use(...adminHandlers(EMPTY_PROVIDER));
    renderAt("ws-a");
    await waitFor(() => expect(screen.getByText("wsConfig.title")).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText("wsConfig.fields.gitlabUser"), { target: { value: "bot" } });
    expect(screen.getByRole("button", { name: "wsConfig.discard" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "wsConfig.discard" }));
    expect(screen.getByLabelText("wsConfig.fields.gitlabUser")).toHaveValue("");
  });

  it("reset 链接把覆盖字段恢复为继承", async () => {
    server.use(...adminHandlers({ ...EMPTY_PROVIDER, model: "glm-4.6" }));
    renderAt("ws-a");
    await waitFor(() => expect(screen.getByText("wsConfig.title")).toBeInTheDocument());
    // 仅 model 覆盖 → 唯一一条 reset 链接（文本含 ↺ 前缀，用 role+正则匹配）
    expect(screen.getByRole("button", { name: /wsConfig\.reset/ })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /wsConfig\.reset/ }));
    // 重置后无 override 徽标
    await waitFor(() => expect(screen.queryByText("wsConfig.provenance.override")).toBeNull());
  });

  it("4 个 section 标题 + 模型档位提示渲染", async () => {
    server.use(...adminHandlers(EMPTY_PROVIDER));
    renderAt("ws-a");
    await waitFor(() => expect(screen.getByText("wsConfig.title")).toBeInTheDocument());
    for (const k of ["engine", "models", "runtime", "git"]) {
      expect(screen.getByText(`wsConfig.sections.${k}.title`)).toBeInTheDocument();
    }
    for (const k of ["small", "medium", "large", "fallback"]) {
      expect(screen.getByText(`wsConfig.tiers.${k}`)).toBeInTheDocument();
    }
  });

  it("加载中显骨架、无标题；resolve 后显标题", async () => {
    // 预创建 deferred promise：handler 返回它，测试侧控制何时 resolve
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    let resolveCfg!: (v: any) => void;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const cfgDeferred: Promise<any> = new Promise((r) => { resolveCfg = r; });
    server.use(
      http.get("/api/auth/me", () => HttpResponse.json({ user: { id: 1, username: "admin", role: "admin" } })),
      http.get("/api/workspaces/:ws/config", () => cfgDeferred),
      http.get("/api/workspaces/:ws/members", () => HttpResponse.json({ members: [] })),
    );
    renderAt("ws-a");
    // 未 resolve：无标题（骨架态）
    expect(screen.queryByText("wsConfig.title")).toBeNull();
    resolveCfg(HttpResponse.json({ provider: EMPTY_PROVIDER }));
    await waitFor(() => expect(screen.getByText("wsConfig.title")).toBeInTheDocument());
  });

  it("加载失败显失败提示 + 重试入口", async () => {
    server.use(
      http.get("/api/auth/me", () => HttpResponse.json({ user: { id: 1, username: "admin", role: "admin" } })),
      http.get("/api/workspaces/:ws/config", () => HttpResponse.json({ detail: "boom" }, { status: 500 })),
      http.get("/api/workspaces/:ws/members", () => HttpResponse.json({ members: [] })),
    );
    renderAt("ws-a");
    await waitFor(() => expect(screen.getByText("wsConfig.loadFailed")).toBeInTheDocument());
    expect(screen.getByText("wsConfig.retry")).toBeInTheDocument();
  });
});
