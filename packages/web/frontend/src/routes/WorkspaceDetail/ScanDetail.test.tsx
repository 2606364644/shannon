import { describe, it, expect, beforeAll, afterAll, afterEach, beforeEach } from "vitest";
import { render, screen, fireEvent, cleanup, waitFor } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import i18n from "@/i18n";
import ScanDetail from "./ScanDetail";

const server = setupServer(
  http.get("/api/workspaces/:ws/scans/:scanId", () =>
    HttpResponse.json({ status: "running", scan_type: "whitebox", repo_path: "/root/code" }),
  ),
);

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
beforeEach(() => i18n.changeLanguage("zh"));
afterEach(() => { server.resetHandlers(); cleanup(); i18n.changeLanguage("zh"); });
afterAll(() => server.close());

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/p/:workspace/scans/:scanId" element={<ScanDetail />}>
          <Route index element={<div>default-content</div>} />
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

describe("ScanDetail per-scan 视图", () => {
  it("渲染 scan_id + 5 scan tabs + 返回 ws 链接", () => {
    renderAt("/p/ws/scans/s1/live");
    expect(screen.getByText("s1")).toBeInTheDocument();
    expect(screen.getByRole("tablist")).toBeInTheDocument();
    expect(screen.getAllByRole("tab")).toHaveLength(5);
    // 返回 ws 概览链接（/p/ws）
    expect(screen.getByRole("link", { name: /返回工作区/ }).getAttribute("href")).toBe("/p/ws");
  });

  it("当前 tab aria-selected（live）", () => {
    renderAt("/p/ws/scans/s1/live");
    expect(screen.getByRole("tab", { name: "实时" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tab", { name: "概览" })).toHaveAttribute("aria-selected", "false");
  });

  it("点 tab 触发导航", () => {
    renderAt("/p/ws/scans/s1/live");
    fireEvent.mouseDown(screen.getByRole("tab", { name: "报告" }));
    expect(screen.getByText("rp-content")).toBeInTheDocument();
  });

  it("scan header 显 status/scan_type/repo_path", async () => {
    const { container } = renderAt("/p/ws/scans/s1/live");
    await waitFor(() => expect(screen.getByText("whitebox")).toBeInTheDocument());
    expect(screen.getByText("/root/code")).toBeInTheDocument();
    expect(container.querySelector("[title='running']")).toBeInTheDocument();
  });

  it("i18n 英文 tab 标签 + 返回 ws 链接", () => {
    i18n.changeLanguage("en");
    renderAt("/p/ws/scans/s1/live");
    expect(screen.getByRole("tab", { name: "Overview" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Report" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Live" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Back to workspace/ })).toBeInTheDocument();
  });
});
