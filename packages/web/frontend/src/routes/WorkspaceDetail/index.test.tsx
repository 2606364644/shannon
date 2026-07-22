import { describe, it, expect, beforeAll, afterAll, afterEach, beforeEach } from "vitest";
import { render, screen, fireEvent, cleanup, waitFor } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import i18n from "@/i18n";
import WorkspaceDetail from "./index";

const server = setupServer(
  http.get("/api/workspaces/:ws", () => HttpResponse.json({ status: "running" })),
);

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
beforeEach(() => i18n.changeLanguage("zh"));
afterEach(() => {
  server.resetHandlers();
  cleanup();
  i18n.changeLanguage("zh");
});
afterAll(() => server.close());

function renderAt(initialPath: string) {
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <Routes>
        <Route path="/p/:workspace" element={<WorkspaceDetail />}>
          <Route path="overview" element={<div>ov-content</div>} />
          <Route path="report" element={<div>rp-content</div>} />
          <Route path="deliverables" element={<div>dl-content</div>} />
          <Route path="logs" element={<div>lg-content</div>} />
          <Route path="live" element={<div>lv-content</div>} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );
}

describe("WorkspaceDetail shell", () => {
  it("渲染 tablist 与 5 个 tab role", () => {
    renderAt("/p/ws/overview");
    expect(screen.getByRole("tablist")).toBeInTheDocument();
    expect(screen.getAllByRole("tab")).toHaveLength(5);
  });
  it("当前 tab 由路由段决定（aria-selected）", () => {
    renderAt("/p/ws/logs");
    expect(screen.getByRole("tab", { name: "日志" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tab", { name: "概览" })).toHaveAttribute("aria-selected", "false");
  });

  it("mousedown 一个 tab 触发导航", () => {
    renderAt("/p/ws/overview");
    fireEvent.mouseDown(screen.getByRole("tab", { name: "实时" }));
    expect(screen.getByText("lv-content")).toBeInTheDocument();
  });

  it("返回列表链接渲染（中文）", () => {
    renderAt("/p/ws/overview");
    expect(screen.getByText(/返回列表/)).toBeInTheDocument();
  });

  it("Tabs 外层容器 sticky 吸顶（top-12 z-30，紧贴 TopBar 下沿）", () => {
    renderAt("/p/ws/overview");
    const sticky = screen.getByTestId("wd-tabs-sticky");
    expect(sticky.className).toContain("sticky");
    expect(sticky.className).toContain("top-12");
    expect(sticky.className).toContain("z-30");
    expect(sticky.className).toContain("print:static");
  });
});

describe("WorkspaceDetail shell i18n", () => {
  it("切英文 tab 标签 + 返回链接为英文", () => {
    i18n.changeLanguage("en");
    renderAt("/p/ws/overview");
    expect(screen.getByRole("tab", { name: "Overview" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Report" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Deliverables" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Logs" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Live" })).toBeInTheDocument();
    expect(screen.getByText(/Back to list/)).toBeInTheDocument();
  });
});

describe("WorkspaceDetail shell notFound", () => {
  it("404 → 显 notFound 消息（中文）", async () => {
    server.use(http.get("/api/workspaces/:ws", () => HttpResponse.json({ detail: "nope" }, { status: 404 })));
    renderAt("/p/ghost/overview");
    await waitFor(() => expect(screen.getByText(/工作区不存在或已被删除/)).toBeInTheDocument());
    expect(screen.getByText(/返回列表/)).toBeInTheDocument();
  });

  it("404 + 切英文 → notFound 消息英文", async () => {
    i18n.changeLanguage("en");
    server.use(http.get("/api/workspaces/:ws", () => HttpResponse.json({ detail: "nope" }, { status: 404 })));
    renderAt("/p/ghost/overview");
    await waitFor(() => expect(screen.getByText(/does not exist or has been deleted/i)).toBeInTheDocument());
    expect(screen.getByText(/Back to list/)).toBeInTheDocument();
  });
});
