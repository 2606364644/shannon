import { describe, it, expect, beforeAll, afterAll, afterEach, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import i18n from "@/i18n";
import { OverviewTab } from "./OverviewTab";

const session = {
  web_url: "", repo_path: "/x", scan_type: "whitebox", status: "running",
  session: { status: "completed" },  // 矛盾
  metrics: {
    total_duration_ms: 5892153, total_cost_usd: 16.29,
    phases: { "pre-recon": { duration_ms: 805974, duration_percentage: 13.68, cost_usd: 3.75, agent_count: 1 } },
    agents: { "injection-vuln": { duration_ms: 434233, cost_usd: 1.15, success: true, attempt_number: 2, model: "GLM-5.2[1m]", error: "api_error_status=429" } },
  },
};

const server = setupServer();

beforeAll(() => server.listen({ onUnhandledRequest: "bypass" }));
// jsdom navigator.language 默认 en，LanguageDetector 会把 i18n 切到 en；现有断言依赖中文渲染，逐测试钉回 zh。
beforeEach(() => i18n.changeLanguage("zh"));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/p/:workspace/overview" element={<OverviewTab />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("OverviewTab", () => {
  it("阶段瀑布渲染 + 大数字（结构性：删除阶段名/大数字则失败）", async () => {
    server.use(http.get("/api/workspaces/:ws", () => HttpResponse.json(session)));
    renderAt("/p/ws/overview");
    await waitFor(() => expect(screen.getByText(/pre-recon/)).toBeInTheDocument());
    expect(screen.getByText(/\$16\.29/)).toBeInTheDocument();
    expect(screen.getByText(/13\.68/)).toBeInTheDocument();
  });

  it("status 矛盾标黄（保留兜底，后端已 flag）", async () => {
    server.use(http.get("/api/workspaces/:ws", () => HttpResponse.json(session)));
    renderAt("/p/ws/overview");
    // 走 Badge 文本断言，不再依赖 ev-warn 事件类
    await waitFor(() => expect(screen.getByText(/顶层 running vs session.completed/)).toBeInTheDocument());
  });

  it("重试 agent 行 attempt_number>1 + error 用黄色 cell 标注（结构性）", async () => {
    server.use(http.get("/api/workspaces/:ws", () => HttpResponse.json(session)));
    const { container } = renderAt("/p/ws/overview");
    // agent 名出现后，attempt_number=2 行存在
    await waitFor(() => expect(screen.getByText(/injection-vuln/)).toBeInTheDocument());
    // 表格行存在；警示信息（attempt+error）渲染到表格
    expect(container.querySelector("table")).toBeInTheDocument();
    expect(container.textContent).toContain("⚠");
  });

  it("fetch 失败渲染 ErrorState（role=alert）不永久 loading", async () => {
    server.use(
      http.get("/api/workspaces/:ws", () => HttpResponse.json({ detail: "boom" }, { status: 500 })),
    );
    renderAt("/p/ws/overview");
    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
    // 守卫：不渲染空态『等待扫描』
    expect(screen.queryByText(/等待扫描/)).not.toBeInTheDocument();
  });

  it("加载中渲染 Skeleton（animate-pulse）", async () => {
    server.use(
      http.get("/api/workspaces/:ws", async () => {
        await new Promise((r) => setTimeout(r, 50));
        return HttpResponse.json(session);
      }),
    );
    renderAt("/p/ws/overview");
    expect(document.querySelector(".animate-pulse")).toBeInTheDocument();
    // 等到加载完成确保不泄漏 act warning
    await waitFor(() => expect(screen.getByText(/pre-recon/)).toBeInTheDocument());
  });

  it("无 metrics 渲染空态（pre-recon 阶段后才有）", async () => {
    server.use(
      http.get("/api/workspaces/:ws", () =>
        HttpResponse.json({ ...session, metrics: undefined }),
      ),
    );
    renderAt("/p/ws/overview");
    await waitFor(() => expect(screen.getByText(/等待扫描/)).toBeInTheDocument());
  });
});

describe("OverviewTab i18n", () => {
  afterEach(() => i18n.changeLanguage("zh"));

  it("切英文后空态标题/hint 变英文", async () => {
    server.use(
      http.get("/api/workspaces/:ws", () =>
        HttpResponse.json({ ...session, metrics: undefined }),
      ),
    );
    renderAt("/p/ws/overview");
    await screen.findByText(/等待扫描/);
    await i18n.changeLanguage("en");
    expect(await screen.findByText("Waiting for scan")).toBeInTheDocument();
    expect(screen.getByText(/will appear after the pre-recon phase/)).toBeInTheDocument();
  });

  it("切英文后阶段瀑布/agent 账本标题变英文", async () => {
    server.use(http.get("/api/workspaces/:ws", () => HttpResponse.json(session)));
    renderAt("/p/ws/overview");
    await waitFor(() => expect(screen.getByText(/pre-recon/)).toBeInTheDocument());
    await i18n.changeLanguage("en");
    expect(await screen.findByText("Phase waterfall")).toBeInTheDocument();
    expect(screen.getByText("Agent ledger")).toBeInTheDocument();
  });
});
