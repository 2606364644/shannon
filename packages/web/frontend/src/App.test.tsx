import { describe, it, expect, beforeAll, afterAll } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { router } from "./router";
import App from "./App";

// jsdom 默认 location.pathname = "/"，createBrowserRouter 读 History API → 落地根路由。
const server = setupServer(http.get("/api/workspaces", () => HttpResponse.json([])));
beforeAll(() => server.listen()); afterAll(() => server.close());

describe("App 集成冒烟", () => {
  it("根路由渲染 WorkspaceListPage（main 内；TopBar 的 Workspaces nav 在 header 被排除）", async () => {
    render(<App />);
    const main = screen.getByRole("main");
    await waitFor(() => {
      expect(within(main).getByText(/Workspaces/i)).toBeInTheDocument();
    });
  });
  it("RouterProvider 可用（导出 router）", () => {
    expect(router).toBeDefined();
  });
});
