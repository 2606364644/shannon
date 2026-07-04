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

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/p/:workspace/report" element={<ReportTab />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("ReportTab", () => {
  it("GET /report (text/plain) → 经 MarkdownView 渲染（标题 H1 出现）", async () => {
    server.use(
      http.get("/api/workspaces/:ws/report", () =>
        new HttpResponse(MD, { headers: { "content-type": "text/plain" } }),
      ),
    );
    renderAt("/p/ws/report");
    await waitFor(() =>
      expect(screen.getByRole("heading", { level: 1, name: /综合安全评估报告/ })).toBeInTheDocument(),
    );
  });

  it("加载中渲染 Skeleton（animate-pulse）", async () => {
    server.use(
      http.get("/api/workspaces/:ws/report", async () => {
        await new Promise((r) => setTimeout(r, 50));
        return new HttpResponse(MD, { headers: { "content-type": "text/plain" } });
      }),
    );
    renderAt("/p/ws/report");
    expect(document.querySelector(".animate-pulse")).toBeInTheDocument();
    // 等到加载完成确保不泄漏 act warning
    await waitFor(() =>
      expect(screen.getByRole("heading", { level: 1 })).toBeInTheDocument(),
    );
  });

  it("请求失败渲染 ErrorState（role=alert）不永久 loading", async () => {
    server.use(
      http.get("/api/workspaces/:ws/report", () =>
        HttpResponse.text("not found", { status: 404 }),
      ),
    );
    renderAt("/p/ws/report");
    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
    // 守卫：不渲染加载占位 / 空态
    expect(screen.queryByText(/报告尚未生成/)).not.toBeInTheDocument();
  });

  it("空报告（apiGetText 返 \"\"）渲染 Empty 而非加载态", async () => {
    server.use(
      http.get("/api/workspaces/:ws/report", () =>
        new HttpResponse("", { headers: { "content-type": "text/plain" } }),
      ),
    );
    renderAt("/p/ws/report");
    await waitFor(() => expect(screen.getByText(/报告尚未生成/)).toBeInTheDocument());
    // 守卫：不渲染 Skeleton 加载态
    expect(document.querySelector(".animate-pulse")).not.toBeInTheDocument();
  });
});
