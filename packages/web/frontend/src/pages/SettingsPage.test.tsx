import { describe, it, expect, beforeAll, afterAll, afterEach } from "vitest";
import { render, screen, waitFor, fireEvent, cleanup } from "@testing-library/react";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { SettingsPage } from "./SettingsPage";

const okBody = {
  ai_provider: "claude",
  browser_engine: "agent-browser",
  temporal: { enabled: true, host: "localhost:7233", last_status: "connected", last_error: null },
  git_available: true,
  version: "shannon-web 0.1.0",
};

const server = setupServer(
  http.get("/api/system-status", () => HttpResponse.json(okBody)),
);

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => { server.resetHandlers(); cleanup(); });
afterAll(() => server.close());

describe("SettingsPage", () => {
  it("渲染三张 Card(主题/系统状态/关于)", async () => {
    render(<SettingsPage />);
    // CardTitle 渲染为 div(非语义 heading),用文本匹配
    expect(await screen.findByText("主题")).toBeInTheDocument();
    expect(screen.getByText("系统状态")).toBeInTheDocument();
    expect(screen.getByText("关于")).toBeInTheDocument();
  });

  it("状态面板渲染各字段(ai_provider/temporal/version)", async () => {
    render(<SettingsPage />);
    await waitFor(() => expect(screen.getByText("claude")).toBeInTheDocument());
    expect(screen.getByText("agent-browser")).toBeInTheDocument();
    expect(screen.getByText("localhost:7233")).toBeInTheDocument();
    expect(screen.getByText("shannon-web 0.1.0")).toBeInTheDocument();
    expect(screen.getByText("可用")).toBeInTheDocument(); // git_available
  });

  it("主题 Switch 切到浅色 → <html> 加 light class + localStorage", async () => {
    render(<SettingsPage />);
    const sw = screen.getByRole("switch", { name: /切换深浅主题/ });
    fireEvent.click(sw);
    expect(document.documentElement.classList.contains("light")).toBe(true);
    expect(localStorage.getItem("shannon-theme")).toBe("light");
  });

  it("status fetch 失败 → 局部 ErrorState(role=alert)", async () => {
    server.use(http.get("/api/system-status", () => HttpResponse.json({}, { status: 500 })));
    render(<SettingsPage />);
    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
    // 主题 Card 仍在(不受 status 失败影响)
    expect(screen.getByText("主题")).toBeInTheDocument();
  });
});
