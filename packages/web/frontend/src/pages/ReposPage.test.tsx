import { describe, it, expect, beforeAll, afterAll, afterEach, beforeEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor, cleanup, act } from "@testing-library/react";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { MemoryRouter } from "react-router-dom";
import i18n from "@/i18n";
import { ReposPage } from "./ReposPage";
import { Toaster } from "@/components/ui/sonner";

const server = setupServer(
  http.get("/api/repos", () => HttpResponse.json([
    { name: "foo", state: "ready", source: { kind: "git", url: "https://x/foo.git", branch: "main" } },
    { name: "bar", state: "failed", source: { kind: "git" } },
  ])),
  http.delete("/api/repos/:name", ({ params }) => HttpResponse.json({ deleted: params.name })),
);

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
// jsdom navigator.language 默认 en，LanguageDetector 会把 i18n 切到 en；现有断言依赖中文渲染，逐测试钉回 zh。
beforeEach(() => i18n.changeLanguage("zh"));
afterEach(() => { server.resetHandlers(); cleanup(); });
afterAll(() => server.close());

function renderPage() {
  render(<MemoryRouter><ReposPage /><Toaster /></MemoryRouter>);
}

describe("ReposPage", () => {
  it("列出仓库 + 状态", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText("foo")).toBeInTheDocument());
    expect(screen.getByText("✗ 失败")).toBeInTheDocument();
  });

  it("删除确认 Dialog", async () => {
    const spy = vi.spyOn(console, "log").mockImplementation(() => {});
    renderPage();
    await waitFor(() => expect(screen.getByText("foo")).toBeInTheDocument());
    fireEvent.click(screen.getAllByText("删除")[0]);
    // DialogTitle(<h2>) 和 DialogDescription(<p>"删除仓库 foo？…") 都含「删除仓库」，
    // 用 exact 精确命中 title（description 含「删除仓库 foo…」不等于「删除仓库」）。
    expect(await screen.findByText("删除仓库", { exact: true })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "确认" }));
    await waitFor(() => expect(screen.queryByText("删除仓库", { exact: true })).toBeNull());
    expect(spy).not.toHaveBeenCalled();
    spy.mockRestore();
  });

  it("cloning / stale 行显对应指示（CloneProgress / 未完成）", async () => {
    server.use(
      http.get("/api/repos", () => HttpResponse.json([
        { name: "wip", state: "cloning", source: { kind: "git" } },
        { name: "old", state: "stale", source: { kind: "git" } },
      ])),
      // CloneProgress 走 SSE；空流不影响「clone 中」渲染
      http.get("/api/repos/wip/events", () => new HttpResponse("", { headers: { "Content-Type": "text/event-stream" } })),
    );
    renderPage();
    await waitFor(() => expect(screen.getByText("wip")).toBeInTheDocument());
    expect(screen.getByText(/clone 中/)).toBeInTheDocument();
    expect(screen.getByText(/未完成/)).toBeInTheDocument();
  });

  it("按分组折叠渲染（group/repo 跨组同名不冲突）", async () => {
    server.use(
      http.get("/api/repos", () => HttpResponse.json([
        { name: "frontend/honor", group: "frontend", state: "ready", source: { kind: "git", url: "https://x/hon-fe.git" } },
        { name: "backend/honor", group: "backend", state: "ready", source: { kind: "git", url: "https://x/hon-be.git" } },
      ])),
    );
    renderPage();
    // 两个分组 section 都渲染（section 标题 button 含分组名 + 计数）
    expect(await screen.findByRole("button", { name: /frontend \(1\)/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /backend \(1\)/ })).toBeInTheDocument();
    // 同名 honor 跨组共存：两个链接都在
    const honorLinks = screen.getAllByRole("link", { name: /honor/ });
    expect(honorLinks).toHaveLength(2);
  });
});

describe("ReposPage i18n", () => {
  afterEach(() => i18n.changeLanguage("zh"));

  it("中文显示标题与空状态", async () => {
    server.use(http.get("/api/repos", () => HttpResponse.json([])));
    renderPage();
    expect(await screen.findByText("仓库")).toBeInTheDocument();
    expect(screen.getByText(/暂无仓库/)).toBeInTheDocument();
  });

  it("切英文后标题变 Repositories", async () => {
    server.use(http.get("/api/repos", () => HttpResponse.json([])));
    renderPage();
    await screen.findByText("仓库");
    await act(async () => {
      await i18n.changeLanguage("en");
    });
    expect(await screen.findByText("Repositories")).toBeInTheDocument();
  });
});
