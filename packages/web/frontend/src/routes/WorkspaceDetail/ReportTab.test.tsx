import { describe, it, expect, beforeAll, afterAll, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { ReportTab } from "./ReportTab";

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: "bypass" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

const MD = `# 综合安全评估报告

## 执行摘要
1. SSRF-01 漏洞示例
`;

describe("ReportTab", () => {
  it("GET /report (text/plain) → 经 MarkdownView 渲染（标题 H1 出现）", async () => {
    server.use(
      http.get("/api/workspaces/:ws/report", () =>
        new HttpResponse(MD, { headers: { "content-type": "text/plain" } }),
      ),
    );
    render(
      <MemoryRouter initialEntries={["/p/ws/report"]}>
        <Routes>
          <Route path="/p/:workspace/report" element={<ReportTab />} />
        </Routes>
      </MemoryRouter>,
    );
    await waitFor(() => expect(screen.getByRole("heading", { level: 1, name: /综合安全评估报告/ })).toBeInTheDocument());
  });

  it("加载中显示 trace 占位", async () => {
    server.use(
      http.get("/api/workspaces/:ws/report", async () => {
        await new Promise((r) => setTimeout(r, 50));
        return new HttpResponse(MD, { headers: { "content-type": "text/plain" } });
      }),
    );
    render(
      <MemoryRouter initialEntries={["/p/ws/report"]}>
        <Routes>
          <Route path="/p/:workspace/report" element={<ReportTab />} />
        </Routes>
      </MemoryRouter>,
    );
    expect(screen.getByText(/加载报告/)).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("heading", { level: 1 })).toBeInTheDocument());
  });

  it("请求失败显示错误态", async () => {
    server.use(
      http.get("/api/workspaces/:ws/report", () => HttpResponse.text("not found", { status: 404 })),
    );
    render(
      <MemoryRouter initialEntries={["/p/ws/report"]}>
        <Routes>
          <Route path="/p/:workspace/report" element={<ReportTab />} />
        </Routes>
      </MemoryRouter>,
    );
    await waitFor(() => expect(screen.getByText(/报告加载失败/)).toBeInTheDocument());
  });
});
