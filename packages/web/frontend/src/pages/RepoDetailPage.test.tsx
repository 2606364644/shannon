import { describe, it, expect, beforeAll, afterAll, afterEach, beforeEach } from "vitest";
import { render, screen, waitFor, cleanup, act } from "@testing-library/react";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import i18n from "@/i18n";
import { RepoDetailPage } from "./RepoDetailPage";
import { Toaster } from "@/components/ui/sonner";

const repoBody = {
  name: "foo",
  state: "ready",
  source: { kind: "git", url: "https://x/foo.git", branch: "main", commit: "abc1234567" },
  cloned_at: "2026-07-01T00:00:00Z",
  last_pull_at: "2026-07-02T00:00:00Z",
};

const server = setupServer(
  http.get("/api/repos/:name", () => HttpResponse.json(repoBody)),
);

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
// jsdom navigator.language 默认 en，LanguageDetector 会把 i18n 切到 en；现有断言依赖中文渲染，逐测试钉回 zh。
beforeEach(() => i18n.changeLanguage("zh"));
afterEach(() => { server.resetHandlers(); cleanup(); });
afterAll(() => server.close());

function renderPage(name = "foo") {
  render(
    <MemoryRouter initialEntries={[`/repos/${name}`]}>
      <Routes>
        <Route path="/repos/*" element={<RepoDetailPage />} />
      </Routes>
      <Toaster />
    </MemoryRouter>,
  );
}

describe("RepoDetailPage", () => {
  it("渲染仓库元信息(来源/分支/clone 于/最后更新)", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText("foo")).toBeInTheDocument());
    expect(screen.getByText(/来源：/)).toBeInTheDocument();
    expect(screen.getByText(/分支：/)).toBeInTheDocument();
    expect(screen.getByText(/clone 于：/)).toBeInTheDocument();
    expect(screen.getByText(/最后更新：/)).toBeInTheDocument();
  });

  it("操作按钮(发起扫描/更新 pull/checkout)", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText("foo")).toBeInTheDocument());
    expect(screen.getByRole("button", { name: "发起扫描" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "更新 pull" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "checkout" })).toBeInTheDocument();
  });

  it("加载失败 -> 显示错误态 + 返回链接", async () => {
    server.use(http.get("/api/repos/:name", () => HttpResponse.json({}, { status: 404 })));
    renderPage();
    await waitFor(() => expect(screen.getByText(/仓库加载失败/)).toBeInTheDocument());
    expect(screen.getByText(/返回仓库列表/)).toBeInTheDocument();
  });

  it("stale 状态 -> 显示未完成提示", async () => {
    server.use(http.get("/api/repos/:name", () => HttpResponse.json({ ...repoBody, state: "stale" })));
    renderPage();
    await waitFor(() => expect(screen.getByText(/上次 clone 未完成/)).toBeInTheDocument());
  });
});

describe("RepoDetailPage i18n", () => {
  afterEach(() => i18n.changeLanguage("zh"));

  it("中文渲染元信息标签 + 操作按钮", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText("foo")).toBeInTheDocument());
    expect(screen.getByText(/来源：/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "发起扫描" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "更新 pull" })).toBeInTheDocument();
  });

  it("切英文后元信息标签 + 按钮变英文", async () => {
    renderPage();
    await screen.findByText("foo");
    await act(async () => {
      await i18n.changeLanguage("en");
    });
    expect(await screen.findByText(/Source:/)).toBeInTheDocument();
    expect(screen.getByText(/Branch:/)).toBeInTheDocument();
    expect(screen.getByText(/Cloned at:/)).toBeInTheDocument();
    expect(screen.getByText(/Last update:/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Start scan" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Pull updates" })).toBeInTheDocument();
  });

  it("切英文后加载失败态变英文", async () => {
    server.use(http.get("/api/repos/:name", () => HttpResponse.json({}, { status: 404 })));
    renderPage();
    await act(async () => {
      await i18n.changeLanguage("en");
    });
    expect(await screen.findByText(/Repository failed to load/i)).toBeInTheDocument();
    expect(screen.getByText(/Back to repositories/i)).toBeInTheDocument();
  });
});

describe("RepoDetailPage repo.state 映射", () => {
  afterEach(() => i18n.changeLanguage("zh"));

  it("已知状态 ready -> 中文映射 ✓ 就绪", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText("✓ 就绪")).toBeInTheDocument());
  });

  it("切英文后 ready -> ✓ Ready", async () => {
    renderPage();
    await screen.findByText("foo");
    await act(async () => {
      await i18n.changeLanguage("en");
    });
    expect(await screen.findByText("✓ Ready")).toBeInTheDocument();
  });

  it("未知状态 fallback 原值(zh)", async () => {
    server.use(http.get("/api/repos/:name", () => HttpResponse.json({ ...repoBody, state: "some-new-state" })));
    renderPage();
    await waitFor(() => expect(screen.getByText("some-new-state")).toBeInTheDocument());
  });

  it("未知状态 fallback 原值(en)", async () => {
    server.use(http.get("/api/repos/:name", () => HttpResponse.json({ ...repoBody, state: "some-new-state" })));
    renderPage();
    await screen.findByText("some-new-state");
    await act(async () => {
      await i18n.changeLanguage("en");
    });
    expect(screen.getByText("some-new-state")).toBeInTheDocument();
  });
});
