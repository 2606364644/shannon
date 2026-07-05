import { describe, it, expect, beforeAll, afterAll, afterEach } from "vitest";
import { render, screen, waitFor, cleanup } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import WorkspaceDetail from "./index";

const server = setupServer();

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => { server.resetHandlers(); cleanup(); });
afterAll(() => server.close());

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/p/:workspace" element={<WorkspaceDetail />}>
          <Route path="report" element={<div>report-tab</div>} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );
}

describe("WorkspaceDetail header", () => {
  it("显返回链接 + workspace 名 + 元信息（status/scan_type/repo_path）", async () => {
    server.use(
      http.get("/api/workspaces/ws1", () =>
        HttpResponse.json({ status: "completed", scan_type: "whitebox", repo_path: "/root/nodegoat" }),
      ),
    );
    renderAt("/p/ws1/report");
    expect(screen.getByText("返回列表")).toBeInTheDocument();
    expect(screen.getByText("ws1")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText("whitebox")).toBeInTheDocument());
    expect(screen.getByText("/root/nodegoat")).toBeInTheDocument();
    // StatusBadge 兜底显 completed
    expect(screen.getByText("completed")).toBeInTheDocument();
  });

  it("fetch 失败不阻塞 tab（降级显 workspace 名 + 默认 running）", async () => {
    server.use(http.get("/api/workspaces/ws1", () => new HttpResponse(null, { status: 500 })));
    renderAt("/p/ws1/report");
    await waitFor(() => expect(screen.getByText("ws1")).toBeInTheDocument());
    expect(screen.getByText("返回列表")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "报告" })).toBeInTheDocument();
  });
});
